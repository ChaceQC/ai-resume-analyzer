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
def mock_provider(body="", error=None, status_code=200, content_type="text/event-stream", responses=None):
    requests = []
    client_class = httpx.AsyncClient

    def handle(request):
        requests.append(json.loads(request.content))
        if error:
            raise error
        if responses is not None:
            return responses[len(requests) - 1]
        return httpx.Response(status_code, content=body.encode(), headers={"content-type": content_type})

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

    def test_keyword_and_number_stuffing_does_not_get_high_scores(self):
        weak = """王同学
电话：13800138000 邮箱：test@example.com
教育背景
示例大学 本科
专业技能
Python Java JavaScript TypeScript Go Rust PHP Flask FastAPI Django Vue React SQL MySQL Redis Docker Linux Git
项目经历
参加 3 个项目。
参加 5 次活动。
拥有 10 项兴趣。
自我评价
""" + "本人认真负责，积极进取，沟通能力良好，热爱学习。" * 15
        weak_report = local_analyze(weak, "")
        sample_report = local_analyze(self.resume, "")
        self.assertLessEqual(weak_report["overall_score"], 45)
        self.assertGreaterEqual(sample_report["overall_score"], 75)
        self.assertLess(sample_report["overall_score"], 90)
        self.assertGreater(sample_report["overall_score"] - weak_report["overall_score"], 30)

    def test_action_method_and_result_improve_scores(self):
        basic = """陈同学
chen@example.com 13800138000
教育背景
2021—2025 示例大学 本科
专业技能
Python MySQL Redis Docker
项目经历
2024—2025 业务系统
负责业务系统的开发和维护工作。
"""
        concrete = basic + """主导接口优化，通过 Redis 缓存将响应耗时从 320ms 降低至 110ms。
独立设计数据库，使用 MySQL 复合索引将查询耗时从 8 秒缩短至 2 秒。
基于 Python 实现数据校验，将人工处理时间减少 60%。
使用 Docker 部署业务系统，通过自动化发布将交付时间缩短 50%。
"""
        weak = local_analyze(basic, "")
        strong = local_analyze(concrete, "")
        self.assertGreater(strong["overall_score"], weak["overall_score"] + 25)
        self.assertGreaterEqual(strong["overall_score"], 85)
        self.assertGreater(strong["dimensions"][2]["score"], weak["dimensions"][2]["score"])
        self.assertGreater(strong["dimensions"][3]["score"], weak["dimensions"][3]["score"])

    def test_dates_versions_and_duplicate_results_do_not_inflate_scores(self):
        text = "教育背景\n2021—2025 示例大学 本科\n专业技能\nPython 3.12 MySQL 8.0\n项目经历\n2024—2025 示例项目\n电话 13800138000\n参加 5 次培训，使用版本 3.12。"
        self.assertEqual(local_analyze(text, "")["dimensions"][3]["score"], 0)
        repeated = self.resume + "\n" + "通过 Redis 缓存热门商品，将接口平均响应时间从 320ms 降低到 110ms。\n" * 10
        self.assertLessEqual(local_analyze(repeated, "")["overall_score"], local_analyze(self.resume, "")["overall_score"])
        numbered = self.resume.replace("自我评价", "\n".join(f"{i}. 通过 Redis 缓存热门商品，将接口平均响应时间从 320ms 降低到 110ms。" for i in range(1, 6)) + "\n自我评价")
        self.assertEqual(local_analyze(numbered, "")["dimensions"][3]["score"], local_analyze(self.resume, "")["dimensions"][3]["score"])
        counts = "教育背景\n示例大学 本科\n专业技能\nPython\n项目经历\n开发接口服务，共支持 2025个用户。"
        self.assertEqual(next(item for item in local_analyze(counts, "")["score_items"] if item["id"] == "structure.timeline")["points"], 0)
        empty_sections = local_analyze("教育背景\n专业技能\n项目经历", "")
        self.assertEqual(empty_sections["dimensions"][1]["score"], 0)

    def test_english_and_full_width_resume(self):
        english = """Alex Chen
alex@example.com | +1 (415) 555-2671
Education
2021—2025 Example University, Bachelor of Science
Skills
Ｐｙｔｈｏｎ, Ｃ＋＋, Ｃ＃, Node JS, Redis
Work Experience
2024—2025 Software Engineer
Developed Python services using Redis and reduced response time by 45%.
Led deployment using Docker and improved delivery time by 30%.
"""
        report = local_analyze(english, "Python Redis Docker")
        self.assertEqual(report["profile"]["name"], "Alex Chen")
        self.assertEqual(report["profile"]["education"], "本科")
        self.assertEqual(report["profile"]["phone"], "+1 (415) 555-2671")
        self.assertTrue({"Python", "C++", "C#", "Node.js"}.issubset(report["skills"]))
        self.assertEqual(report["job_match_score"], 100)
        self.assertGreater(report["dimensions"][3]["score"], 30)
        self.assertEqual(local_analyze(english.replace("Education", "【教育背景】").replace("Skills", "二、专业技能"), "")["dimensions"][1]["score"], 100)

    def test_job_keywords_do_not_change_resume_quality_score(self):
        self.assertEqual(local_analyze(self.resume, "")["overall_score"], local_analyze(self.resume, "Python Django Kubernetes")["overall_score"])

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
        broken["summary"] = 150
        with mock_provider(provider_events(broken)):
            response = self.client.post("/api/analyze", data={"resume_text": self.resume, "job_description": self.job, "use_ai": "true"})
            self.assertEqual(response.json()["advice_status"], "failed")
            self.assertEqual(response.json()["overall_score"], report["overall_score"])

    def test_ai_timeout_is_actionable(self):
        with mock_provider(error=httpx.ReadTimeout("timeout")):
            response = self.client.post("/api/analyze", data={"resume_text": self.resume, "use_ai": "true"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["advice_status"], "failed")
        self.assertIn("超时", response.json()["advice_error"])

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

    def test_interrupted_ai_advice_retains_only_verified_rule_scores(self):
        report = local_analyze(self.resume, self.job)
        with mock_provider(provider_events(report, complete=False)):
            response = self.client.post("/api/analyze", data={"resume_text": self.resume, "job_description": self.job, "use_ai": "true", "stream": "true"})
        events = parse_events(response)
        self.assertEqual(events[-1]["type"], "complete")
        final = events[-1]["report"]
        self.assertEqual(final["source"], "local")
        self.assertEqual(final["advice_status"], "failed")
        self.assertIn("中断", final["advice_error"])
        self.assertEqual(final["overall_score"], report["overall_score"])

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

    def test_numeric_ai_scores_cannot_override_rule_scores(self):
        report = local_analyze(self.resume, self.job)
        report["overall_score"] = "85"
        report["job_match_score"] = 78.6
        for dimension, score in zip(report["dimensions"], ["92.0", "80分", "70/100", "60%", 80]):
            dimension["score"] = score
        with mock_provider(provider_events(report)):
            response = self.client.post("/api/analyze", data={"resume_text": self.resume, "job_description": self.job, "use_ai": "true", "stream": "true"})
        final = parse_events(response)[-1]
        self.assertEqual(final["type"], "complete")
        baseline = local_analyze(self.resume, self.job)
        self.assertEqual(final["report"]["overall_score"], baseline["overall_score"])
        self.assertEqual(final["report"]["job_match_score"], baseline["job_match_score"])
        self.assertEqual(final["report"]["dimensions"], baseline["dimensions"])

    def test_arbitrary_ai_totals_and_dimensions_are_ignored(self):
        report = local_analyze(self.resume, self.job)
        report["overall_score"] = 99
        for dimension in report["dimensions"]:
            dimension["score"] = 50
        with mock_provider(provider_events(report)):
            response = self.client.post("/api/analyze", data={"resume_text": self.resume, "use_ai": "true"})
        self.assertEqual(response.json()["overall_score"], local_analyze(self.resume, "")["overall_score"])
        for invalid in (True, "NaN", "Infinity", -1, 101):
            with self.subTest(score=invalid):
                report["dimensions"][0]["score"] = invalid
                with mock_provider(provider_events(report)):
                    response = self.client.post("/api/analyze", data={"resume_text": self.resume, "use_ai": "true"})
                self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["overall_score"], local_analyze(self.resume, "")["overall_score"])

    def test_non_stream_provider_and_text_blocks_are_supported(self):
        report = local_analyze(self.resume, self.job)
        del report["overall_score"]
        report["strengths"] = []
        content = "以下为报告：\n```JSON\n" + json.dumps(report, ensure_ascii=False) + "\n```"
        body = json.dumps({"choices": [{"message": {"content": [
            {"type": "reasoning", "text": "内部推理不应展示"},
            {"type": "text", "text": content},
        ]}, "finish_reason": "stop"}]})
        with mock_provider(body, content_type="application/json; charset=utf-8"):
            response = self.client.post("/api/analyze", data={"resume_text": self.resume, "use_ai": "true", "stream": "true"})
        final = parse_events(response)[-1]
        self.assertEqual(final["type"], "complete")
        self.assertEqual(final["report"]["source"], "ai")
        self.assertEqual(final["report"]["strengths"], [])
        self.assertNotIn("内部推理", response.text)

    def test_sse_crlf_content_blocks_and_stop_without_done(self):
        report = local_analyze(self.resume, self.job)
        body = ": heartbeat\r\n\r\ndata: " + json.dumps({"choices": [{
            "delta": {"content": [{"type": "text", "text": json.dumps(report)}]},
            "finish_reason": "stop",
        }]})
        with mock_provider(body):
            response = self.client.post("/api/analyze", data={"resume_text": self.resume, "use_ai": "true"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["source"], "ai")

    def test_only_explicitly_unsupported_model_parameters_are_retried(self):
        report = local_analyze(self.resume, self.job)
        responses = [
            httpx.Response(400, json={"error": "Unsupported parameter max_tokens; use max_completion_tokens"}),
            httpx.Response(400, json={"error": "temperature is not supported; only the default value is allowed"}),
            httpx.Response(422, json={"error": "stream is not supported"}),
            httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(report)}, "finish_reason": "stop"}]}),
        ]
        with mock_provider(responses=responses) as requests:
            response = self.client.post("/api/analyze", data={"resume_text": self.resume, "use_ai": "true"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(requests), 4)
        self.assertNotIn("max_tokens", requests[1])
        self.assertEqual(requests[1]["max_completion_tokens"], 8000)
        self.assertNotIn("temperature", requests[2])
        self.assertFalse(requests[3]["stream"])
        for status, body in ((401, "Unauthorized"), (400, "Invalid API key"), (429, "Rate limit"), (500, "Server error")):
            with self.subTest(status=status), mock_provider(body, status_code=status) as requests:
                response = self.client.post("/api/analyze", data={"resume_text": self.resume, "use_ai": "true"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["advice_status"], "failed")
            self.assertEqual(len(requests), 1)

    def test_truncated_reports_rejected_and_ai_dimensions_ignored(self):
        report = local_analyze(self.resume, self.job)
        for reason in ("length", "content_filter"):
            body = json.dumps({"choices": [{"message": {"content": json.dumps(report)}, "finish_reason": reason}]})
            with self.subTest(reason=reason), mock_provider(body, content_type="application/json"):
                response = self.client.post("/api/analyze", data={"resume_text": self.resume, "use_ai": "true"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["advice_status"], "failed")
        report["dimensions"][1]["label"] = report["dimensions"][0]["label"]
        with mock_provider(provider_events(report)):
            response = self.client.post("/api/analyze", data={"resume_text": self.resume, "use_ai": "true"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["dimensions"], local_analyze(self.resume, "")["dimensions"])

    def test_optional_ai_match_and_rewrites_can_be_absent(self):
        report = local_analyze(self.resume, self.job)
        del report["job_match_score"]
        report["rewrites"] = None
        with mock_provider(provider_events(report)):
            response = self.client.post("/api/analyze", data={"resume_text": self.resume, "job_description": self.job, "use_ai": "true", "stream": "true"})
        final = parse_events(response)[-1]
        self.assertEqual(final["type"], "complete")
        self.assertEqual(final["report"]["job_match_score"], local_analyze(self.resume, self.job)["job_match_score"])
        self.assertEqual(final["report"]["rewrites"], [])

    def test_stream_upstream_error_is_visible(self):
        with mock_provider(status_code=401):
            response = self.client.post("/api/analyze", data={"resume_text": self.resume, "use_ai": "true", "stream": "true"})
        final = parse_events(response)[-1]
        self.assertEqual(final["type"], "complete")
        self.assertEqual(final["report"]["advice_status"], "failed")
        self.assertTrue(final["report"]["advice_error"])

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
