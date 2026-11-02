"""启动：python app.py；接口文档：http://127.0.0.1:5000/docs。"""

import io
import json
import os
import zipfile
from pathlib import Path
from typing import Annotated

import uvicorn
from docx import Document
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pypdf import PdfReader
from starlette.concurrency import run_in_threadpool

from analyzer import AIError, ai_analyze, ai_analyze_stream, ai_configured, local_analyze


ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_RESUME_CHARS = 40_000
MAX_JOB_CHARS = 12_000

app = FastAPI(title="简历透镜 · AI 简历分析", version="1.0.1",
              description="上传简历或粘贴文本，进行本地关键词分析或大模型深度分析。")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.middleware("http")
async def limit_request_size(request, call_next):
    length = request.headers.get("content-length")
    if length and (not length.isdecimal() or int(length) > MAX_FILE_BYTES + 128 * 1024):
        return JSONResponse(status_code=413, content={"detail": "请求过大，简历文件请控制在 8 MB 以内。"})
    return await call_next(request)


def extract_text(filename: str, data: bytes) -> str:
    extension = Path(filename).suffix.lower()
    if extension not in {".txt", ".pdf", ".docx"}:
        raise ValueError("仅支持 PDF、DOCX、TXT 文件；旧版 DOC 请先另存为 DOCX。")
    if not data:
        raise ValueError("文件为空，请重新选择。")
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("文件不能超过 8 MB。")
    try:
        if extension == ".txt":
            try:
                return data.decode("utf-8-sig")
            except UnicodeDecodeError:
                return data.decode("gb18030")
        if extension == ".pdf":
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted and not reader.decrypt(""):
                raise ValueError("PDF 已加密，请先移除密码。")
            if len(reader.pages) > 30:
                raise ValueError("PDF 请控制在 30 页以内。")
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
            if not text.strip():
                raise ValueError("PDF 未提取到文字；扫描件请先 OCR，或直接粘贴简历文本。")
            return text
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if len(archive.infolist()) > 2000 or sum(info.file_size for info in archive.infolist()) > 30 * 1024 * 1024:
                raise ValueError("DOCX 解压后内容过大，请精简文件。")
        document = Document(io.BytesIO(data))
        parts = [paragraph.text for paragraph in document.paragraphs]
        parts.extend(" | ".join(cell.text for cell in row.cells)
                     for table in document.tables for row in table.rows)
        return "\n".join(parts)
    except ValueError:
        raise
    except Exception:
        raise ValueError("文件解析失败，请确认文件未损坏且格式正确，或改用粘贴文本。") from None


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(ROOT / "static" / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/api/status", summary="查看服务状态和 AI 配置状态")
def status():
    available = ai_configured()
    return {"status": "ok", "version": app.version, "ai_available": available,
            "model": os.getenv("AI_MODEL") if available else None}


@app.get("/api/sample", summary="读取示例简历与岗位描述")
def sample():
    return {"resume_text": (ROOT / "samples/resume.txt").read_text(encoding="utf-8"),
            "job_description": (ROOT / "samples/job.txt").read_text(encoding="utf-8")}


@app.post("/api/analyze", summary="分析简历，stream=true 时实时返回 SSE 事件",
          responses={200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}})
async def analyze(
    file: Annotated[UploadFile | None, File(description="PDF / DOCX / TXT，最大 8 MB")] = None,
    resume_text: Annotated[str, Form(description="简历正文，60—40000 字符")] = "",
    job_description: Annotated[str, Form(description="岗位描述，最多 12000 字符")] = "",
    use_ai: Annotated[bool, Form(description="是否使用已配置的大模型服务")] = False,
    stream: Annotated[bool, Form(description="是否通过 SSE 实时返回分析进度和结果")] = False,
):
    if file and file.filename:
        try:
            data = await file.read(MAX_FILE_BYTES + 1)
            resume_text = await run_in_threadpool(extract_text, file.filename, data)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        finally:
            await file.close()
    text = resume_text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "").strip()
    job = job_description.strip()
    if len(text) < 60:
        raise HTTPException(400, "简历内容不足 60 字符，请补充教育、技能或项目经历后重试。")
    if len(text) > MAX_RESUME_CHARS:
        raise HTTPException(400, "简历内容最多支持 40000 字符，请精简后重试。")
    if len(job) > MAX_JOB_CHARS:
        raise HTTPException(400, "岗位描述最多支持 12000 字符。")
    report = local_analyze(text, job)
    if use_ai and not ai_configured():
        raise HTTPException(400, "深度分析暂不可用，请先使用基础分析。")
    if stream:
        async def events():
            def encode(event):
                return "data: " + json.dumps(event, ensure_ascii=True) + "\n\n"
            try:
                if use_ai:
                    async for event in ai_analyze_stream(text, job, report):
                        yield encode(event)
                else:
                    yield encode({"type": "complete", "report": report})
            except AIError as exc:
                yield encode({"type": "error", "message": str(exc)})
        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})
    if use_ai:
        try:
            report = await ai_analyze(text, job, report)
        except AIError as exc:
            raise HTTPException(502, str(exc)) from None
    return report


if __name__ == "__main__":
    uvicorn.run(app, host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "5000")))
