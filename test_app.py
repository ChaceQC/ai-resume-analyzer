"""运行：python -m unittest -v。所有测试均不调用外部大模型。"""

import copy
import codecs
import io
import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import httpx
from docx import Document
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from analyzer import completion_url, extract_skills
from rubric import criteria, VERSION
from scoring import evidence_catalog
from test_support import model_assessment
from app import ROOT, app, extract_text


def provider_events(report, complete=True):
    content = json.dumps(report, ensure_ascii=True)
    chunks = [{"choices": [{"delta": {"reasoning_content": "内部推理不应展示"}}]}]
    chunks.extend({"choices": [{"delta": {"content": content[i:i + 17]}}]} for i in range(0, len(content), 17))
    frames = [": keep-alive\n\n"] + ["data: " + json.dumps(chunk) + "\n\n" for chunk in chunks]
    if complete:
        frames.extend(["data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]}) + "\n\n", "data: [DONE]\n\n"])
    return "".join(frames)


ORIGINAL_ASYNC_CLIENT = httpx.AsyncClient


@contextmanager
def mock_provider(body=None, error=None, status_code=200, content_type="text/event-stream", responses=None):
    requests = []
    client_class = ORIGINAL_ASYNC_CLIENT

    def handle(request):
        requests.append(json.loads(request.content))
        if error:
            raise error
        if responses is not None:
            return responses[len(requests) - 1]
        result = body
        if result is None:
            payload = json.loads(requests[-1]["messages"][1]["content"])
            result = provider_events(model_assessment(payload["resume"], payload["job_description"], payload["market_criteria"]))
        return httpx.Response(status_code, content=result.encode(), headers={"content-type": content_type})

    config = {"AI_API_KEY": "test-secret", "AI_BASE_URL": "https://example.com/v1", "AI_MODEL": "test-model", "AI_PROXY": ""}
    with patch.dict(os.environ, config), patch("analyzer.httpx.AsyncClient", side_effect=lambda **kwargs: client_class(transport=httpx.MockTransport(handle))):
        yield requests


def parse_events(response):
    return [json.loads(frame[6:]) for frame in response.text.strip().split("\n\n") if frame.startswith("data: ")]


class ResumeAnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        cache = patch.dict(os.environ, {"SCORE_CACHE_PATH": str(Path(temporary.name) / "scores.sqlite3")})
        cache.start()
        cls.addClassCleanup(cache.stop)
        cls.client = TestClient(app)
        cls.resume = (ROOT / "samples/resume.txt").read_text(encoding="utf-8")
        cls.job = (ROOT / "samples/job.txt").read_text(encoding="utf-8")


    def test_skill_boundaries(self):
        skills = extract_skills("熟悉JavaScript、FastAPI、C++，使用 GitHub，掌握Python。")
        self.assertIn("JavaScript", skills)
        self.assertIn("C++", skills)
        self.assertIn("Python", skills)
        self.assertNotIn("Java", skills)
        self.assertNotIn("Git", skills)


    def test_provider_base_url(self):
        self.assertEqual(completion_url("https://example.com/"), "https://example.com/v1/chat/completions")
        self.assertEqual(completion_url("https://example.com/v1/"), "https://example.com/v1/chat/completions")
        self.assertEqual(completion_url("https://example.com/custom/v2"), "https://example.com/custom/v2/chat/completions")
        self.assertEqual(completion_url(" https://example.com/v1/chat/completions/ "), "https://example.com/v1/chat/completions")
        self.assertEqual(completion_url("https://example.com/custom?api-version=2026"), "https://example.com/custom/chat/completions?api-version=2026")
        for invalid in ("example.com/v1", "ftp://example.com", ""):
            with self.assertRaises(ValueError):
                completion_url(invalid)


    def test_text_file_upload(self):
        response = self.client.post("/api/analyze", files={"file": ("简历.TXT", self.resume.encode(), "text/plain")}, data={"job_description": self.job})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["source"], "ai")
        self.assertEqual(response.json()["profile"]["email"], "chen@example.com")


    def test_docx_includes_table_text(self):
        document = Document()
        document.add_paragraph("教育背景：软件工程 本科")
        document.add_table(rows=1, cols=1).cell(0, 0).text = "项目经历：使用 FastAPI 和 Redis 开发接口"
        stream = io.BytesIO()
        document.save(stream)
        text = extract_text("简历.docx", stream.getvalue())
        self.assertIn("FastAPI", text)
        self.assertIn("本科", text)


    def test_gb18030_text(self):
        text = "教育背景：软件工程；专业技能：Python、MySQL。"
        self.assertEqual(extract_text("resume.txt", text.encode("gb18030")), text)


    def test_windows_and_unicode_text_encodings(self):
        for encoding in ("utf-8-sig", "utf-16", "utf-16-be", "utf-16-le", "utf-32"):
            with self.subTest(encoding=encoding):
                self.assertEqual(extract_text("简历.TXT", self.resume.encode(encoding)), self.resume)
        for bom, encoding in ((codecs.BOM_UTF16_BE, "utf-16-be"), (codecs.BOM_UTF32_BE, "utf-32-be")):
            self.assertEqual(extract_text("resume.txt", bom + self.resume.encode(encoding)), self.resume)
        with self.assertRaisesRegex(ValueError, "UTF-8"):
            extract_text("resume.txt", b"\xff\xfe\x00")


    def test_docx_preserves_body_order_headers_and_nested_tables(self):
        document = Document()
        document.sections[0].header.paragraphs[0].text = "Alex Chen alex@example.com"
        document.add_paragraph("教育背景")
        table = document.add_table(rows=1, cols=1)
        table.cell(0, 0).text = "示例大学 本科"
        document.add_paragraph("项目经历")
        nested = document.add_table(rows=1, cols=1).cell(0, 0).add_table(rows=1, cols=1)
        nested.cell(0, 0).text = "使用 Redis 优化接口"
        document.sections[0].footer.paragraphs[0].text = "电话：13800138000"
        stream = io.BytesIO()
        document.save(stream)
        text = extract_text("resume.docx", stream.getvalue())
        self.assertLess(text.index("Alex Chen"), text.index("教育背景"))
        self.assertLess(text.index("示例大学"), text.index("项目经历"))
        self.assertIn("使用 Redis 优化接口", text)
        self.assertIn("13800138000", text)


    def test_docx_textboxes_and_merged_cells(self):
        document = Document()
        document.add_paragraph("项目经历")
        paragraph = document.add_paragraph()
        paragraph._p.append(parse_xml('<w:r ' + nsdecls('w') + '><w:pict><w:txbxContent><w:p><w:r>'
                                     '<w:t>姓名：陈同学 邮箱：chen@example.com</w:t>'
                                     '</w:r></w:p></w:txbxContent></w:pict></w:r>'))
        table = document.add_table(rows=2, cols=1)
        table.cell(0, 0).merge(table.cell(1, 0)).text = "合并单元格内容"
        stream = io.BytesIO()
        document.save(stream)
        text = extract_text("resume.docx", stream.getvalue())
        self.assertIn("chen@example.com", text)
        self.assertEqual(text.count("合并单元格内容"), 1)


    def test_empty_or_too_short_resume_rejected(self):
        for value in ("", "  ", "简历"):
            response = self.client.post("/api/analyze", data={"resume_text": value})
            self.assertEqual(response.status_code, 400)


    def test_content_limits(self):
        self.assertEqual(self.client.post("/api/analyze", data={"resume_text": "字" * 40001}).status_code, 400)
        self.assertEqual(self.client.post("/api/analyze", data={"resume_text": self.resume, "job_description": "字" * 12001}).status_code, 400)
        response = self.client.post("/api/analyze", files={"file": ("resume.txt", b"x" * (9 * 1024 * 1024))})
        self.assertEqual(response.status_code, 413)


    def test_unsupported_and_corrupt_files_rejected(self):
        for filename, content in [("resume.exe", b"invalid"), ("resume.docx", b"invalid")]:
            response = self.client.post("/api/analyze", files={"file": (filename, content)})
            self.assertEqual(response.status_code, 400)


    def test_empty_pdf_gives_ocr_guidance(self):
        writer = PdfWriter()
        writer.add_blank_page(width=595, height=842)
        stream = io.BytesIO()
        writer.write(stream)
        response = self.client.post("/api/analyze", files={"file": ("scan.pdf", stream.getvalue())})
        self.assertEqual(response.status_code, 400)
        self.assertIn("OCR", response.json()["detail"])


    def test_status_never_returns_key_and_docs_available(self):
        with patch.dict(os.environ, {"AI_API_KEY": "do-not-return-this-secret", "AI_BASE_URL": "https://example.com", "AI_MODEL": "demo"}):
            response = self.client.get("/api/status")
        self.assertTrue(response.json()["ai_available"])
        self.assertNotIn("do-not-return-this-secret", response.text)
        for path in ("/", "/static/app.js", "/static/style.css", "/docs", "/openapi.json", "/api/sample"):
            self.assertEqual(self.client.get(path).status_code, 200, path)


    def setUp(self):
        provider = mock_provider()
        self.requests = provider.__enter__()
        self.addCleanup(provider.__exit__, None, None, None)

    def test_ai_is_default_and_receives_unscored_criteria(self):
        response = self.client.post("/api/analyze", data={"resume_text": self.resume})
        self.assertEqual(response.status_code, 200)
        report = response.json()
        self.assertEqual(report["source"], "ai")
        self.assertEqual(report["meta"]["scoring_source"], "ai")
        self.assertEqual(report["overall_score"], sum(item["points"] for item in report["score_items"]))
        payload = json.loads(self.requests[0]["messages"][1]["content"])
        self.assertTrue(all("points" not in item and "evidence" not in item for item in payload["criteria"]))
        self.assertIn("由你阅读完整简历", self.requests[0]["messages"][0]["content"])
        self.assertEqual(self.requests[0]["temperature"], 0)

    def test_no_job_does_not_invent_match_score(self):
        response = self.client.post("/api/analyze", data={"resume_text": self.resume})
        self.assertIsNone(response.json()["job_match_score"])
        self.assertFalse(response.json()["meta"]["job_provided"])

    def test_disabled_ai_does_not_return_rule_scores(self):
        response = self.client.post("/api/analyze", data={"resume_text": self.resume, "use_ai": "false"})
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("overall_score", response.json())
        self.assertEqual(self.requests, [])

    def test_unconfigured_ai_is_explicit_error(self):
        with patch.dict(os.environ, {"AI_API_KEY": "", "AI_BASE_URL": "", "AI_MODEL": ""}):
            response = self.client.post("/api/analyze", data={"resume_text": self.resume})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("overall_score", response.json())

    def test_ai_points_change_the_score_for_the_same_resume(self):
        data = model_assessment(self.resume)
        results = []
        for points in (1, 5):
            data["score_items"][8]["points"] = 1
            data["score_items"][9]["points"] = points
            with mock_provider(provider_events(data)):
                results.append(self.client.post("/api/analyze", data={"resume_text": self.resume}).json())
        self.assertEqual(results[1]["overall_score"] - results[0]["overall_score"], 4)
        self.assertNotEqual(results[0]["meta"]["score_snapshot_id"], results[1]["meta"]["score_snapshot_id"])

    def test_direct_ai_totals_do_not_override_item_sum(self):
        data = model_assessment(self.resume)
        data.update(overall_score=99, dimensions=[{"score": 100, "label": "随意更改"}])
        with mock_provider(provider_events(data)):
            report = self.client.post("/api/analyze", data={"resume_text": self.resume}).json()
        self.assertEqual(report["overall_score"], sum(item["points"] for item in data["score_items"]))
        self.assertNotEqual(report["overall_score"], 99)
        self.assertEqual(report["dimensions"][0]["label"], "信息完整度")

    def test_invalid_model_output_does_not_return_scores(self):
        data = model_assessment(self.resume)
        data["summary"] = 150
        with mock_provider(provider_events(data)):
            response = self.client.post("/api/analyze", data={"resume_text": self.resume})
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("overall_score", response.json())

    def test_uploaded_docx_bad_quote_is_corrected_once_using_original_ids(self):
        text = self.resume.replace("数据库维护", "数据\n库维护")
        document = Document()
        document.add_paragraph(text)
        file = io.BytesIO()
        document.save(file)
        extracted = extract_text("resume.docx", file.getvalue()).strip()
        bad = model_assessment(extracted)
        bad["score_items"][0]["evidence"] = ["PRIVATE fabricated quote never in the uploaded file"]
        corrected = model_assessment(extracted)
        ref = evidence_catalog(extracted)[0]
        for item in corrected["score_items"]:
            item["evidence"] = [ref["id"]] if item["points"] else []
        responses = [httpx.Response(200, content=provider_events(value), headers={"content-type": "text/event-stream"})
                     for value in (bad, corrected)]
        with mock_provider(responses=responses) as requests, self.assertLogs("resume_analyzer", level="WARNING") as logs:
            response = self.client.post("/api/analyze", files={"file": ("resume.docx", file.getvalue())})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(requests), 2)
        self.assertIn("score_items.info.email.evidence", requests[1]["messages"][-1]["content"])
        self.assertNotIn("PRIVATE", "\n".join(logs.output))
        report = response.json()
        self.assertEqual(report["source"], "ai")
        self.assertEqual(report["overall_score"], sum(item["points"] for item in corrected["score_items"]))
        self.assertEqual(report["score_items"][0]["evidence"][0]["quote"], ref["quote"])

    def test_failed_correction_is_bounded_and_never_stores_fallback_scores(self):
        bad = model_assessment(self.resume)
        bad["score_items"][0]["evidence"] = ["missing original quote"]
        with mock_provider(provider_events(bad)) as requests, patch("app.remember") as remember:
            response = self.client.post("/api/analyze", data={"resume_text": self.resume})
        self.assertEqual(len(requests), 2)
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("overall_score", response.json())
        remember.assert_not_called()

    def test_ai_timeout_is_actionable_without_fallback(self):
        with mock_provider(error=httpx.ReadTimeout("timeout")):
            response = self.client.post("/api/analyze", data={"resume_text": self.resume})
        self.assertEqual(response.status_code, 502)
        self.assertIn("超时", response.json()["detail"])
        self.assertNotIn("overall_score", response.json())

    def test_stream_progress_and_complete_ai_report(self):
        data = model_assessment(self.resume, self.job)
        with mock_provider(provider_events(data)):
            response = self.client.post("/api/analyze", data={"resume_text": self.resume, "job_description": self.job, "stream": "true"})
        self.assertEqual(response.headers["x-accel-buffering"], "no")
        events = parse_events(response)
        summaries = [event["text"] for event in events if event["type"] == "summary"]
        self.assertGreater(len(summaries), 2)
        self.assertEqual(summaries[-1], data["summary"])
        self.assertEqual(events[-1]["type"], "complete")
        self.assertEqual(events[-1]["report"]["source"], "ai")
        self.assertNotIn("内部推理", json.dumps(events, ensure_ascii=False))

    def test_interrupted_stream_ends_with_error_and_no_score(self):
        with mock_provider(provider_events(model_assessment(self.resume), complete=False)):
            response = self.client.post("/api/analyze", data={"resume_text": self.resume, "stream": "true"})
        events = parse_events(response)
        self.assertEqual(events[-1]["type"], "error")
        self.assertIn("中断", events[-1]["message"])
        self.assertFalse(any(event["type"] == "complete" for event in events))

    def test_non_stream_provider_and_text_blocks_are_supported(self):
        data = model_assessment(self.resume)
        body = json.dumps({"choices": [{"message": {"content": [{"type": "text", "text": "```json\n" + json.dumps(data) + "\n```"}]}, "finish_reason": "stop"}]})
        with mock_provider(body, content_type="application/json"):
            response = self.client.post("/api/analyze", data={"resume_text": self.resume})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["source"], "ai")

    def test_sse_crlf_content_blocks_and_stop_without_done(self):
        data = model_assessment(self.resume)
        content = json.dumps(data)
        body = "data: " + json.dumps({"choices": [{"delta": {"content": [{"type": "text", "text": content}]}, "finish_reason": "stop"}]}) + "\r\n\r\n"
        with mock_provider(body):
            response = self.client.post("/api/analyze", data={"resume_text": self.resume})
        self.assertEqual(response.status_code, 200)

    def test_only_explicitly_unsupported_parameters_are_retried(self):
        responses = [httpx.Response(400, json={"error": "Unsupported max_tokens; use max_completion_tokens"}),
                     httpx.Response(400, json={"error": "temperature is not supported; only the default value is allowed"}),
                     httpx.Response(422, json={"error": "stream is not supported"}),
                     httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(model_assessment(self.resume))}, "finish_reason": "stop"}]})]
        with mock_provider(responses=responses) as requests:
            response = self.client.post("/api/analyze", data={"resume_text": self.resume})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(requests), 4)
        self.assertEqual(requests[1]["max_completion_tokens"], 12000)
        self.assertNotIn("temperature", requests[2])
        self.assertFalse(requests[3]["stream"])
        for status in (401, 429, 500):
            with self.subTest(status=status), mock_provider("provider error", status_code=status) as requests:
                response = self.client.post("/api/analyze", data={"resume_text": self.resume})
            self.assertEqual(response.status_code, 502)
            self.assertEqual(len(requests), 1)

    def test_truncated_reports_rejected(self):
        for reason in ("length", "content_filter"):
            body = json.dumps({"choices": [{"message": {"content": json.dumps(model_assessment(self.resume))}, "finish_reason": reason}]})
            with self.subTest(reason=reason), mock_provider(body, content_type="application/json"):
                response = self.client.post("/api/analyze", data={"resume_text": self.resume})
            self.assertEqual(response.status_code, 502)

    def test_unverified_optional_rewrite_is_omitted(self):
        data = model_assessment(self.resume)
        data["rewrites"] = [{"before": "简历中不存在的句子", "after": "这条改写不应展示"}]
        with mock_provider(provider_events(data)):
            response = self.client.post("/api/analyze", data={"resume_text": self.resume})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["rewrites"], [])

    def test_rubric_exposes_ai_policy_without_scoring(self):
        rubric = self.client.get("/api/rubric").json()
        self.assertEqual(rubric["scorer"], "ai")
        self.assertEqual(rubric["version"], VERSION)
        self.assertEqual(sum(item["max_points"] for item in rubric["items"]), 100)
        self.assertTrue(all("points" not in item for item in rubric["items"]))


if __name__ == "__main__":
    unittest.main()
