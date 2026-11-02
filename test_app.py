"""运行：python -m unittest -v。所有测试均不调用外部大模型。"""

import copy
import io
import json
import os
import unittest
from contextlib import contextmanager
from unittest.mock import patch

import httpx
from docx import Document
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from analyzer import completion_url, extract_skills, local_analyze
from app import ROOT, app, extract_text


def provider_events(report, complete=True):
    content = json.dumps(report, ensure_ascii=True)
    chunks = [{"choices": [{"delta": {"reasoning_content": "内部推理不应展示"}}]}]
    chunks.extend({"choices": [{"delta": {"content": content[i:i + 17]}}]} for i in range(0, len(content), 17))
    frames = [": keep-alive\n\n"] + ["data: " + json.dumps(chunk) + "\n\n" for chunk in chunks]
    if complete:
        frames.extend(["data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]}) + "\n\n", "data: [DONE]\n\n"])
    return "".join(frames)


@contextmanager
def mock_provider(body="", error=None, status_code=200):
    requests = []
    client_class = httpx.AsyncClient

    def handle(request):
        requests.append(json.loads(request.content))
        if error:
            raise error
        return httpx.Response(status_code, content=body.encode(), headers={"content-type": "text/event-stream"})

    config = {"AI_API_KEY": "test-secret", "AI_BASE_URL": "https://example.com/v1", "AI_MODEL": "test-model", "AI_PROXY": ""}
    with patch.dict(os.environ, config), patch("analyzer.httpx.AsyncClient", side_effect=lambda **kwargs: client_class(transport=httpx.MockTransport(handle))):
        yield requests


def parse_events(response):
    return [json.loads(frame[6:]) for frame in response.text.strip().split("\n\n") if frame.startswith("data: ")]


class ResumeAnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
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

    def test_local_scores_and_keyword_coverage(self):
        report = local_analyze(self.resume, "Python Django Kubernetes")
        self.assertEqual(report["job_match_score"], 33)
        self.assertEqual(report["matched_skills"], ["Python"])
        self.assertEqual(report["missing_skills"], ["Django", "Kubernetes"])
        self.assertTrue(0 <= report["overall_score"] <= 100)
        self.assertEqual(report["source"], "local")
        self.assertTrue(all(item["before"] in self.resume for item in report["rewrites"]))

    def test_no_job_does_not_invent_match_score(self):
        response = self.client.post("/api/analyze", data={"resume_text": self.resume})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["job_match_score"])
        self.assertFalse(response.json()["meta"]["job_provided"])

    def test_text_file_upload(self):
        response = self.client.post("/api/analyze", files={"file": ("简历.TXT", self.resume.encode(), "text/plain")}, data={"job_description": self.job})
        self.assertEqual(response.status_code, 200)
        self.assertIn("FastAPI", response.json()["matched_skills"])
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

    def test_unconfigured_ai_is_explicit_error(self):
        with patch.dict(os.environ, {"AI_API_KEY": "", "AI_BASE_URL": "", "AI_MODEL": ""}):
            response = self.client.post("/api/analyze", data={"resume_text": self.resume, "use_ai": "true"})
        self.assertEqual(response.status_code, 400)

    def test_ai_report_and_invalid_model_output(self):
        report = local_analyze(self.resume, self.job)
        with mock_provider(provider_events(report)) as requests:
            response = self.client.post("/api/analyze", data={"resume_text": self.resume, "job_description": self.job, "use_ai": "true"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["source"], "ai")
            self.assertEqual(response.json()["meta"]["model"], "test-model")
            self.assertTrue(requests[0]["stream"])
        broken = copy.deepcopy(report)
        broken["overall_score"] = 150
        with mock_provider(provider_events(broken)):
            self.assertEqual(self.client.post("/api/analyze", data={"resume_text": self.resume, "job_description": self.job, "use_ai": "true"}).status_code, 502)

    def test_ai_timeout_is_actionable(self):
        with mock_provider(error=httpx.ReadTimeout("timeout")):
            response = self.client.post("/api/analyze", data={"resume_text": self.resume, "use_ai": "true"})
        self.assertEqual(response.status_code, 502)
        self.assertIn("超时", response.json()["detail"])

    def test_stream_progress_chinese_and_final_report(self):
        report = local_analyze(self.resume, self.job)
        with mock_provider(provider_events(report)) as requests:
            response = self.client.post("/api/analyze", data={"resume_text": self.resume, "job_description": self.job, "use_ai": "true", "stream": "true"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/event-stream", response.headers["content-type"])
        self.assertEqual(response.headers["x-accel-buffering"], "no")
        events = parse_events(response)
        self.assertEqual(events[0]["type"], "status")
        summaries = [event["text"] for event in events if event["type"] == "summary"]
        self.assertGreater(len(summaries), 2)
        self.assertLess(len(summaries[0]), len(summaries[-1]))
        self.assertEqual(summaries[-1], report["summary"])
        self.assertEqual(events[-1]["type"], "complete")
        self.assertEqual(events[-1]["report"]["source"], "ai")
        self.assertNotIn("内部推理", json.dumps(events, ensure_ascii=False))
        self.assertTrue(requests[0]["stream"])

    def test_stream_disconnect_never_marks_report_complete(self):
        report = local_analyze(self.resume, self.job)
        with mock_provider(provider_events(report, complete=False)):
            response = self.client.post("/api/analyze", data={"resume_text": self.resume, "job_description": self.job, "use_ai": "true", "stream": "true"})
        events = parse_events(response)
        self.assertEqual(events[-1]["type"], "error")
        self.assertIn("中断", events[-1]["message"])
        self.assertNotIn("complete", [event["type"] for event in events])

    def test_extra_ai_items_do_not_reject_valid_report(self):
        report = local_analyze(self.resume, self.job)
        report["rewrites"] = report["rewrites"] * 6
        report["interview_questions"] = report["interview_questions"] * 4
        with mock_provider(provider_events(report)):
            response = self.client.post("/api/analyze", data={"resume_text": self.resume, "job_description": self.job, "use_ai": "true", "stream": "true"})
        final = parse_events(response)[-1]
        self.assertEqual(final["type"], "complete")
        self.assertEqual(len(final["report"]["rewrites"]), 5)
        self.assertEqual(len(final["report"]["interview_questions"]), 8)

    def test_unverified_rewrite_does_not_reject_entire_report(self):
        report = local_analyze(self.resume, self.job)
        report["rewrites"] = [{"before": "这是一条简历中并不存在的项目经历", "after": "这条改写应被跳过"}]
        with mock_provider(provider_events(report)):
            response = self.client.post("/api/analyze", data={"resume_text": self.resume, "job_description": self.job, "use_ai": "true", "stream": "true"})
        final = parse_events(response)[-1]
        self.assertEqual(final["type"], "complete")
        self.assertEqual(final["report"]["rewrites"], [])
        self.assertEqual(final["report"]["overall_score"], report["overall_score"])

    def test_wrapped_pdf_quote_preserves_actual_original_text(self):
        sentence = "使用 Python、Flask、MySQL 和 Redis 实现用户登录、商品管理与订单模块。"
        wrapped = "使用 Python、Flask、MySQL 和 Redis 实现\n用户登录、商品管理与订单模块。"
        resume = self.resume.replace(sentence, wrapped)
        report = local_analyze(resume, self.job)
        report["rewrites"] = [{"before": sentence, "after": "说明具体负责的模块，并补充实际测试结果。"}]
        with mock_provider(provider_events(report)):
            response = self.client.post("/api/analyze", data={"resume_text": resume, "job_description": self.job, "use_ai": "true", "stream": "true"})
        final = parse_events(response)[-1]
        self.assertEqual(final["type"], "complete")
        self.assertEqual(final["report"]["rewrites"][0]["before"], wrapped)

    def test_numeric_ai_scores_are_normalized(self):
        report = local_analyze(self.resume, self.job)
        report["overall_score"] = "85"
        report["job_match_score"] = 78.6
        report["dimensions"][0]["score"] = "92.0"
        with mock_provider(provider_events(report)):
            response = self.client.post("/api/analyze", data={"resume_text": self.resume, "job_description": self.job, "use_ai": "true", "stream": "true"})
        final = parse_events(response)[-1]
        self.assertEqual(final["type"], "complete")
        self.assertEqual(final["report"]["overall_score"], 85)
        self.assertEqual(final["report"]["job_match_score"], 79)
        self.assertEqual(final["report"]["dimensions"][0]["score"], 92)

    def test_optional_ai_match_and_rewrites_can_be_absent(self):
        report = local_analyze(self.resume, self.job)
        del report["job_match_score"]
        report["rewrites"] = None
        with mock_provider(provider_events(report)):
            response = self.client.post("/api/analyze", data={"resume_text": self.resume, "job_description": self.job, "use_ai": "true", "stream": "true"})
        final = parse_events(response)[-1]
        self.assertEqual(final["type"], "complete")
        self.assertIsNone(final["report"]["job_match_score"])
        self.assertEqual(final["report"]["rewrites"], [])

    def test_stream_upstream_error_is_visible(self):
        with mock_provider(status_code=401):
            response = self.client.post("/api/analyze", data={"resume_text": self.resume, "use_ai": "true", "stream": "true"})
        self.assertEqual(parse_events(response)[-1]["type"], "error")

    def test_local_stream_returns_same_report(self):
        response = self.client.post("/api/analyze", data={"resume_text": self.resume, "stream": "true"})
        events = parse_events(response)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "complete")
        self.assertEqual(events[0]["report"]["source"], "local")

    def test_status_never_returns_key_and_docs_available(self):
        with patch.dict(os.environ, {"AI_API_KEY": "do-not-return-this-secret", "AI_BASE_URL": "https://example.com", "AI_MODEL": "demo"}):
            response = self.client.get("/api/status")
        self.assertTrue(response.json()["ai_available"])
        self.assertNotIn("do-not-return-this-secret", response.text)
        for path in ("/", "/static/app.js", "/static/style.css", "/docs", "/openapi.json", "/api/sample"):
            self.assertEqual(self.client.get(path).status_code, 200, path)


if __name__ == "__main__":
    unittest.main()
