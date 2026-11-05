"""启动：python app.py；接口文档：http://127.0.0.1:5000/docs。"""

import codecs
import io
import json
import os
import zipfile
from pathlib import Path
from typing import Annotated

import uvicorn
from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pypdf import PdfReader
from starlette.concurrency import run_in_threadpool

from analyzer import AIError, ai_analyze, ai_analyze_stream, ai_configured, prepare_analysis
import market
from score_store import remember
from scoring import INDUSTRIES, LEVELS, ROLES, VERSION
from rubric import POLICY, criteria


ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_RESUME_CHARS = 40_000
MAX_JOB_CHARS = 12_000

app = FastAPI(title="简历透镜 · AI 简历分析", version="4.0.0",
              description="上传简历或粘贴文本，由AI按严格细则评分并提供可追溯依据。")
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
            for bom, encoding in ((codecs.BOM_UTF32_LE, "utf-32"), (codecs.BOM_UTF32_BE, "utf-32"),
                                  (codecs.BOM_UTF16_LE, "utf-16"), (codecs.BOM_UTF16_BE, "utf-16")):
                if data.startswith(bom):
                    return data.decode(encoding)
            # Windows 导出的无 BOM UTF-16 通常在 ASCII 一侧有连续的空字节。
            if len(data) % 2 == 0 and b"\x00" in data:
                for offset, encoding in ((1, "utf-16-le"), (0, "utf-16-be")):
                    sample = data[offset:1024:2]
                    if sample and sample.count(0) / len(sample) > .3:
                        return data.decode(encoding)
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

        def block_texts(container):
            for block in container.iter_inner_content():
                if isinstance(block, Table):
                    seen_cells = set()
                    for row in block.rows:
                        cells = []
                        for cell in row.cells:
                            if cell._tc not in seen_cells:
                                seen_cells.add(cell._tc)
                                cells.append("\n".join(block_texts(cell)))
                        yield " | ".join(cells)
                else:
                    yield block.text

        parts, seen_headers = [], set()
        for section in document.sections:
            for header in (section.header, section.first_page_header, section.even_page_header):
                if header._element not in seen_headers:
                    seen_headers.add(header._element)
                    parts.extend(block_texts(header))
        parts.extend(block_texts(document))
        for section in document.sections:
            for footer in (section.footer, section.first_page_footer, section.even_page_footer):
                if footer._element not in seen_headers:
                    seen_headers.add(footer._element)
                    parts.extend(block_texts(footer))
        # 侧栏模板常将联系方式放进文本框，python-docx 的 paragraph.text 不含这部分。
        for textbox in document.element.iter(qn("w:txbxContent")):
            parts.extend("".join(node.text or "" for node in paragraph.iter(qn("w:t")))
                         for paragraph in textbox.iter(qn("w:p")))
        return "\n".join(parts)
    except UnicodeError:
        raise ValueError("TXT 编码无法识别，请另存为 UTF-8 后上传，或直接粘贴文本。") from None
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


@app.get("/api/market", summary="查看行情快照、覆盖范围与可选评估背景")
def market_status(industry: str = "", role: str = "", level: str = "", city: str = ""):
    for value, options, label in ((industry, INDUSTRIES, "行业"), (role, ROLES, "岗位族"), (level, LEVELS, "职级")):
        if value and value not in options:
            raise HTTPException(400, f"无效的{label}选项。")
    if len(city.strip()) > 80:
        raise HTTPException(400, "目标地区不能超过 80 字符。")
    return market.status({"industry": industry, "role": role, "level": level, "city": city.strip()})


@app.get("/api/market/snapshot", summary="下载公开招聘样本、原文依据和来源链接")
def market_snapshot():
    snapshot = market.load_snapshot()
    if not snapshot:
        raise HTTPException(404, "暂无有效行情快照。")
    return JSONResponse(snapshot, headers={"Content-Disposition": 'attachment; filename="market-snapshot.json"'})


@app.get("/api/rubric", summary="查看AI评分细则与版本")
def rubric(level: str = ""):
    if level and level not in LEVELS:
        raise HTTPException(400, "职级选项无效。")
    return {"version": VERSION, "max_points": 100,
            "scorer": "ai", "calibration": POLICY,
            "items": [{key: item[key] for key in ("id", "dimension", "title", "max_points", "rule")} for item in criteria()]}


@app.post("/api/analyze", summary="分析简历，stream=true 时实时返回 SSE 事件",
          responses={200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}})
async def analyze(
    file: Annotated[UploadFile | None, File(description="PDF / DOCX / TXT，最大 8 MB")] = None,
    resume_text: Annotated[str, Form(description="简历正文，60—40000 字符")] = "",
    job_description: Annotated[str, Form(description="岗位描述，最多 12000 字符")] = "",
    use_ai: Annotated[bool, Form(description="须启用AI评分；不再提供固定规则分数")] = True,
    stream: Annotated[bool, Form(description="是否通过 SSE 实时返回分析进度和结果")] = False,
    industry: Annotated[str, Form(description="目标行业，可选值见 /api/market")] = "",
    role: Annotated[str, Form(description="岗位族，可选值见 /api/market")] = "",
    level: Annotated[str, Form(description="职业阶段，可选值见 /api/market")] = "",
    city: Annotated[str, Form(description="目标地区；留空使用快照中不限地区的样本")] = "",
    use_market: Annotated[bool, Form(description="是否结合有效的招聘行情快照")] = True,
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
    for value, options, label in ((industry, INDUSTRIES, "行业"), (role, ROLES, "岗位族"), (level, LEVELS, "职级")):
        if value and value not in options:
            raise HTTPException(400, f"{label}选项无效，请刷新页面后重新选择。")
    if len(city.strip()) > 80:
        raise HTTPException(400, "地区最多支持80字符。")
    context = {"industry": industry, "role": role, "level": level, "city": city.strip()}
    if not use_ai:
        raise HTTPException(400, "当前版本由AI按细则评分，请启用AI评分。")
    if not ai_configured():
        raise HTTPException(503, "AI评分服务尚未配置，请配置模型后重试。")
    report = await run_in_threadpool(prepare_analysis, text, job, context)
    report["source_text"] = {"resume": text, "job": job}
    await run_in_threadpool(market.attach, report, text, job, None if use_market else {})
    if not use_market:
        report["market"].update(status="disabled", message="本次未启用行情评估。")

    def finalize(assessed):
        market.combine(assessed)
        remember(assessed, text, job)
        return assessed

    if stream:
        async def events():
            def encode(event):
                return "data: " + json.dumps(event, ensure_ascii=True) + "\n\n"
            try:
                async for event in ai_analyze_stream(text, job, report):
                    if event["type"] == "complete":
                        await run_in_threadpool(finalize, event["report"])
                    yield encode(event)
            except AIError as exc:
                yield encode({"type": "error", "message": str(exc)})
        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})
    try:
        assessed = await ai_analyze(text, job, report)
    except AIError as exc:
        raise HTTPException(502, str(exc)) from None
    return await run_in_threadpool(finalize, assessed)


if __name__ == "__main__":
    uvicorn.run(app, host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "5000")))
