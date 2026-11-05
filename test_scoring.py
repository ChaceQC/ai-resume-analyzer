"""评分语义、重复计算、行情依据与持久化回归，不调用外部模型或招聘接口。"""

import copy
import gzip
import json
import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import market
from analyzer import prepare_analysis
from ai_scoring import apply_scores
from test_support import model_assessment
from test_app import mock_provider
from app import ROOT, app
from score_store import remember
from scoring import VERSION, evidence_catalog, fingerprint, quote_reference, rounded
from rubric import DIMENSIONS
from update_market import build_snapshot, market_requirements


def fixture_snapshot():
    captured = datetime.now(timezone.utc).isoformat()
    jobs = [{"id": f"{company}-{index}", "company": company, "industry": "software", "role": "software", "level": "junior",
             "title": f"Software Engineer {index}", "location": "Singapore", "url": f"https://example.org/{company}/{index}",
             "updated_at": captured, "requirements": [{"skill": skill, "quote": f"Experience with {skill} is required."} for skill in ("Python", "Docker", "云平台")]}
            for company in ("Example A", "Example B", "Example C") for index in range(10)]
    snapshot = {"schema_version": 1, "captured_at": captured, "scope": "自动化测试样本，不是真实行情",
                "jobs": jobs, "sources": [{"company": name, "url": "https://example.org/jobs"} for name in ("Example A", "Example B", "Example C")]}
    snapshot["id"] = fingerprint(snapshot)
    return snapshot


class ScoringTests(unittest.TestCase):
    def setUp(self):
        self.resume = (ROOT / "samples/resume.txt").read_text(encoding="utf-8")
        self.job = (ROOT / "samples/job.txt").read_text(encoding="utf-8")

    def prepared(self, text=None, job=""):
        text = self.resume if text is None else text
        report = prepare_analysis(text, job)
        market.attach(report, text, job, {})
        return report

    def test_preparation_never_assigns_quality_or_match_points(self):
        for text in (self.resume, "熟悉 Python Docker SQL，负责开发，提升1000%。" * 30):
            report = self.prepared(text, self.job)
            self.assertIsNone(report["overall_score"])
            self.assertIsNone(report["job_match_score"])
            self.assertTrue(all(item["points"] is None for item in report["score_items"]))
            self.assertEqual(sum(item["max_points"] for item in report["score_items"]), 100)
            self.assertEqual(len(report["score_items"]), 20)

    def test_model_judgments_drive_points_and_each_dimension_adds_up(self):
        data = model_assessment(self.resume)
        report = self.prepared()
        data["score_items"][9]["points"] = 7
        apply_scores(data, report, self.resume, "")
        self.assertEqual(report["score_items"][9]["points"], 7)
        self.assertEqual(report["overall_score"], sum(item["points"] for item in data["score_items"]))
        for dimension in report["dimensions"]:
            self.assertEqual(dimension["max_points"], DIMENSIONS[dimension["label"]])
            self.assertEqual(dimension["points"], sum(item["points"] for item in report["score_items"] if item["dimension"] == dimension["label"]))

    def test_bad_numeric_values_are_rejected_without_clamping(self):
        for invalid in (-1, 999, 1.5, "2", True, None, float("nan"), float("inf")):
            data = model_assessment(self.resume)
            data["score_items"][0]["points"] = invalid
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                apply_scores(data, self.prepared(), self.resume, "")

    def test_missing_duplicate_and_unknown_items_are_rejected(self):
        original = model_assessment(self.resume)
        variants = [original["score_items"][:-1], original["score_items"] + [original["score_items"][0]],
                    [original["score_items"][0]] * 20,
                    [dict(item, id="unknown") if index == 0 else item for index, item in enumerate(original["score_items"])]]
        for values in variants:
            data = dict(original, score_items=values)
            with self.subTest(items=len(values)), self.assertRaises(ValueError):
                apply_scores(data, self.prepared(), self.resume, "")

    def test_points_need_real_quotes_and_explanations(self):
        for changes in ({"evidence": []}, {"evidence": ["编造的简历原句"]}, {"reason": ""}, {"evidence": "not a list"}):
            data = model_assessment(self.resume)
            data["score_items"][0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                apply_scores(data, self.prepared(), self.resume, "")

    def test_original_unicode_coordinates_survive_normalized_quotes(self):
        text = "😀\n项目经历\n使用 Ｐｙｔｈｏｎ\n实现接口校验，生成报告。"
        ref = quote_reference(text, "使用 Python 实现接口校验")
        self.assertEqual(text[ref["start"]:ref["end"]], ref["quote"])
        self.assertIn("Ｐｙｔｈｏｎ\n", ref["quote"])
        self.assertGreater(ref["start"], 1)
        with self.assertRaises(ValueError):
            quote_reference(text, "完全没有的原句")

    def test_numbered_evidence_preserves_uploaded_text_without_model_copying(self):
        text = self.resume.replace("数据库维护", "数据\n库维护").replace("Python", "Ｐｙｔｈｏｎ")
        catalog = evidence_catalog(text)
        data = model_assessment(text)
        for item in data["score_items"]:
            item["evidence"] = [catalog[0]["id"]] if item["points"] else []
        report = self.prepared(text)
        apply_scores(data, report, text, "")
        self.assertGreater(report["overall_score"], 0)
        for item in report["score_items"]:
            for ref in item["evidence"]:
                self.assertEqual(text[ref["start"]:ref["end"]], ref["quote"])

    def test_numbered_job_evidence_cannot_be_used_as_resume_evidence(self):
        data = model_assessment(self.resume, self.job)
        job_id = evidence_catalog(self.job, "job")[0]["id"]
        data["requirements"][0].pop("job_quote")
        data["requirements"][0]["job_evidence"] = job_id
        report = self.prepared(job=self.job)
        apply_scores(data, report, self.resume, self.job)
        self.assertEqual(report["requirements"][0]["evidence"][0]["source"], "job")
        data["score_items"][0]["evidence"] = [job_id]
        with self.assertRaisesRegex(ValueError, "score_items.info.email.evidence.*unknown evidence id"):
            apply_scores(data, self.prepared(job=self.job), self.resume, self.job)

    def test_catalog_bounds_long_paragraphs_without_changing_characters(self):
        text = "😀 项目经历\n" + "使用Ｐｙｔｈｏｎ实现接口校验 " * 150
        catalog = evidence_catalog(text)
        self.assertGreater(len(catalog), 1)
        self.assertEqual(len(catalog), len({ref["id"] for ref in catalog}))
        for ref in catalog:
            self.assertLessEqual(len(ref["quote"]), 700)
            self.assertEqual(text[ref["start"]:ref["end"]], ref["quote"])

    def test_job_evidence_accepts_one_or_multiple_original_ids(self):
        catalog = evidence_catalog(self.job, "job")
        for ids in ([catalog[0]["id"]], [ref["id"] for ref in catalog[:2]]):
            data = model_assessment(self.resume, self.job)
            data["requirements"][0].pop("job_quote")
            data["requirements"][0]["job_evidence"] = ids
            report = self.prepared(job=self.job)
            apply_scores(data, report, self.resume, self.job)
            self.assertEqual(len(report["requirements"][0]["evidence"]), len(ids))
            for ref in report["requirements"][0]["evidence"]:
                self.assertEqual(self.job[ref["start"]:ref["end"]], ref["quote"])

    def test_integral_json_float_is_accepted_without_changing_score(self):
        data = model_assessment(self.resume)
        data["score_items"][0]["points"] = 1.0
        report = self.prepared()
        apply_scores(data, report, self.resume, "")
        self.assertEqual(report["score_items"][0]["points"], 1)
        self.assertIs(type(report["score_items"][0]["points"]), int)

    def test_ai_can_extract_non_keyword_job_requirements(self):
        job = "必备：本科毕业且具有三年工作经验。加分：持有消防执业资格。"
        data = model_assessment(self.resume)
        data["requirements"] = [
            {"title": "本科毕业", "priority": "必备", "job_quote": "本科毕业", "skills": [], "match": "met", "reason": "有毕业信息。", "evidence": ["2021.09 — 2025.06 示例大学 软件工程 本科"]},
            {"title": "三年工作经验", "priority": "必备", "job_quote": "具有三年工作经验", "skills": [], "match": "missing", "reason": "无明确年限。", "evidence": []},
            {"title": "消防执业资格", "priority": "加分", "job_quote": "持有消防执业资格", "skills": [], "match": "missing", "reason": "无对应资格。", "evidence": []}]
        report = self.prepared(job=job)
        apply_scores(data, report, self.resume, job)
        self.assertEqual(report["job_match_score"], 43)
        self.assertEqual(sum(item["max_points"] for item in report["requirements"]), 100)
        self.assertEqual(report["requirements"][0]["weight"], 3)
        self.assertEqual(report["requirements"][-1]["weight"], 1)

    def test_invented_job_quotes_and_unsupported_matches_are_rejected(self):
        for changes in ({"job_quote": "岗位没有这条要求"}, {"match": "met", "evidence": []}, {"priority": "任意"}):
            data = model_assessment(self.resume, self.job)
            data["requirements"][0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                apply_scores(data, self.prepared(job=self.job), self.resume, self.job)
        data = model_assessment(self.resume, self.job)
        with self.assertRaises(ValueError):
            apply_scores(data, self.prepared(), self.resume, "")

    def test_all_awarded_points_keep_exact_resume_and_job_coordinates(self):
        report = self.prepared(job=self.job)
        apply_scores(model_assessment(self.resume, self.job), report, self.resume, self.job)
        for item in report["score_items"] + report["requirements"]:
            for ref in item.get("evidence", []) + item.get("resume_evidence", []):
                source = self.resume if ref["source"] == "resume" else self.job
                self.assertEqual(source[ref["start"]:ref["end"]], ref["quote"])


class MarketTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = fixture_snapshot()
        self.context = {"industry": "software", "role": "software", "level": "junior", "city": ""}
        self.resume = (ROOT / "samples/resume.txt").read_text(encoding="utf-8")

    def test_matching_market_has_recomputable_points_and_sources(self):
        result = market.assess(self.resume, "Python", self.context, self.snapshot)
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["sample_count"], 30)
        self.assertEqual(result["company_count"], 3)
        self.assertIsNone(result["score"])
        self.assertTrue(all(item["points"] is None for item in result["items"]))
        self.assertAlmostEqual(sum(item["max_points"] for item in result["items"]), 100)
        for item in result["items"]:
            self.assertEqual(item["company_mentions"], 3)
            self.assertTrue(item["sources"])
            self.assertTrue(all(source["url"].startswith("https://") and source["quote"] for source in item["sources"]))

    def test_one_employer_cannot_increase_frequency_by_reposting(self):
        baseline = market.assess(self.resume, "", self.context, self.snapshot)
        repeated = copy.deepcopy(self.snapshot)
        repeated["jobs"].extend([copy.deepcopy(repeated["jobs"][0]) for _ in range(100)])
        result = market.assess(self.resume, "", self.context, repeated)
        self.assertEqual(result["score"], baseline["score"])
        self.assertEqual([item["max_points"] for item in result["items"]], [item["max_points"] for item in baseline["items"]])

    def test_missing_region_level_industry_and_stale_data_do_not_get_scores(self):
        for changes in ({"city": "上海"}, {"industry": "healthcare"}, {"level": "graduate"}, {"level": ""}):
            context = dict(self.context, **changes)
            result = market.assess(self.resume, "", context, self.snapshot)
            self.assertIsNone(result["score"])
            self.assertNotEqual(result["status"], "available")
        stale = copy.deepcopy(self.snapshot)
        stale["captured_at"] = (datetime.now(timezone.utc) - timedelta(days=15)).isoformat()
        self.assertEqual(market.assess(self.resume, "", self.context, stale)["status"], "stale")
        self.assertIsNone(market.assess(self.resume, "", self.context, {})["score"])

    def test_insufficient_company_diversity_is_not_a_market_score(self):
        self.snapshot["jobs"] = [dict(job, company="Only one company") for job in self.snapshot["jobs"]]
        result = market.assess(self.resume, "", self.context, self.snapshot)
        self.assertEqual(result["status"], "insufficient")
        self.assertIsNone(result["score"])

    def test_application_ledger_does_not_repeat_explicit_job_skills_in_market(self):
        report = prepare_analysis(self.resume, "熟悉 Python", self.context)
        market.attach(report, self.resume, "熟悉 Python", self.snapshot)
        apply_scores(model_assessment(self.resume, "熟悉 Python", report["market"]["items"]), report, self.resume, "熟悉 Python")
        market.combine(report)
        ledger = report["application"]["items"]
        self.assertIsNotNone(report["application"]["score"])
        self.assertAlmostEqual(sum(item["max_points"] for item in ledger), 100)
        self.assertEqual(report["application"]["score"], rounded(sum(item["points"] for item in ledger)))
        self.assertNotIn("Python", [item["title"] for item in ledger if item["source_group"] == "market"])

    def test_snapshot_integrity_and_public_extract_filter(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "snapshot.json.gz"
            path.write_bytes(gzip.compress(json.dumps(self.snapshot).encode()))
            self.assertEqual(market.load_snapshot(path)["id"], self.snapshot["id"])
            self.snapshot["jobs"][0]["title"] = "tampered"
            path.write_bytes(gzip.compress(json.dumps(self.snapshot).encode()))
            self.assertEqual(market.load_snapshot(path), {})
        requirements = market_requirements("Our platform uses Python.\nRequirements\nExperience with Docker and SQL.\nBenefits\nWe sponsor Python meetups.")
        self.assertEqual({item["skill"] for item in requirements}, {"Docker", "SQL"})

    def test_import_deduplicates_posts_and_rejects_old_or_invalid_sources(self):
        now = datetime.now(timezone.utc).isoformat()
        entry = {"id": "one", "company": "Example", "industry": "software", "title": "Software Engineer",
                 "location": "Singapore", "url": "https://example.org/job", "content": "<h2>Requirements</h2><p>Experience with Python.</p>", "updated_at": now}
        old = dict(entry, id="old", updated_at=(datetime.now(timezone.utc) - timedelta(days=100)).isoformat())
        bad = dict(entry, id="bad", url="javascript:bad")
        snapshot = build_snapshot([entry, dict(entry, id="duplicate"), old, bad], [{"company": "Example"}], now)
        self.assertEqual(len(snapshot["jobs"]), 1)

    def test_city_quota_preserves_other_cities_and_explicit_campus_level(self):
        now = datetime.now(timezone.utc).isoformat()
        entries = [{"id": f"{city}-{index}", "company": "测试企业", "industry": "software",
                    "title": f"Software Engineer {index}", "location": city, "level": "graduate",
                    "url": f"https://example.org/{city}/{index}", "content": "任职要求\n熟悉 Python。", "updated_at": now}
                   for city in ("北京", "上海") for index in range(30)]
        snapshot = build_snapshot(entries, [{"company": "测试企业"}], now)
        self.assertEqual(len(snapshot["jobs"]), 50)
        self.assertEqual({job["location"] for job in snapshot["jobs"]}, {"北京", "上海"})
        self.assertTrue(all(job["level"] == "graduate" for job in snapshot["jobs"]))

    def test_coverage_preview_uses_the_same_strict_city_scope_as_assessment(self):
        for job in self.snapshot["jobs"]:
            job["location"] = "Shanghai, China"
        with TestClient(app) as client, patch("market.load_snapshot", return_value=self.snapshot):
            query = {"industry": "software", "role": "software", "level": "junior", "city": "上海市"}
            available = client.get("/api/market", params=query).json()
            self.assertEqual(available["selection"]["status"], "available")
            self.assertEqual(available["selection"]["sample_count"], 30)
            self.assertEqual(available["selection"]["company_count"], 3)
            self.assertIn({"city": "上海", "jobs": 30, "companies": 3}, available["regions"])
            query["city"] = "深圳"
            missing = client.get("/api/market", params=query).json()
            self.assertEqual(missing["selection"]["status"], "insufficient")
            self.assertEqual(missing["selection"]["sample_count"], 0)
            self.assertEqual(market.assess(self.resume, "", query, self.snapshot)["sample_count"], 0)
            self.assertEqual(client.get("/api/market?industry=invalid").status_code, 400)

    def test_domestic_titles_and_city_aliases(self):
        self.assertEqual(market.role_for_title("游戏客户端研发工程师"), "software")
        self.assertEqual(prepare_analysis(self.resume, "游戏客户端研发工程师")["context"]["role"], "software")
        self.assertEqual(market.role_for_title("大模型算法工程师"), "data")
        self.assertEqual(market.level_for_title("高级产品经理"), "senior")
        self.assertEqual(market.level_for_title("产品经理"), "junior")
        self.assertEqual(market.level_for_title("研发经理"), "manager")
        self.assertEqual(market.level_for_title("Senior Product Manager"), "senior")
        for location, city in (("杭州市", "hangzhou"), ("Beijing, China", "北京市"), ("成都市", "中国"), ("郑州市", "中国")):
            self.assertTrue(market.location_matches(location, city))
        self.assertFalse(market.location_matches("Singapore", "中国"))


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "scores.sqlite3"
        config = patch.dict(os.environ, {"SCORE_CACHE_PATH": str(self.path)})
        config.start()
        self.addCleanup(config.stop)
        self.resume = (ROOT / "samples/resume.txt").read_text(encoding="utf-8")
        self.job = "熟悉 Python"

    def report(self):
        report = prepare_analysis(self.resume, self.job)
        market.attach(report, self.resume, self.job, {})
        apply_scores(model_assessment(self.resume, self.job), report, self.resume, self.job)
        report["source"] = "ai"
        report["meta"]["model"] = "test-model"
        remember(report, self.resume, self.job)
        return report

    def test_reopened_store_reuses_same_snapshot_without_private_text(self):
        first, second = self.report(), self.report()
        self.assertEqual(first["meta"]["score_snapshot_id"], second["meta"]["score_snapshot_id"])
        self.assertEqual(first["meta"]["scored_at"], second["meta"]["scored_at"])
        self.assertEqual(second["meta"]["score_cache"], "hit")
        with sqlite3.connect(self.path) as connection:
            dump = "\n".join(connection.iterdump())
        for private in ("chen@example.com", "13800138000", "校园二手", self.job):
            self.assertNotIn(private, dump)

    def test_concurrent_requests_and_cache_corruption_do_not_change_scores(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            reports = list(pool.map(lambda _: self.report(), range(8)))
        self.assertEqual(len({report["meta"]["score_snapshot_id"] for report in reports}), 1)
        with sqlite3.connect(self.path) as connection:
            connection.execute("UPDATE scores SET points='invalid'")
        repaired = self.report()
        self.assertEqual(repaired["overall_score"], reports[0]["overall_score"])
        self.assertEqual(repaired["meta"]["score_cache"], "repaired")

    def test_api_context_validation_and_source_text(self):
        with TestClient(app) as client, patch("market.load_snapshot", return_value=fixture_snapshot()), mock_provider():
            for field in ("industry", "role", "level"):
                response = client.post("/api/analyze", data={"resume_text": self.resume, field: "invalid"})
                self.assertEqual(response.status_code, 400)
            response = client.post("/api/analyze", data={"resume_text": self.resume, "job_description": self.job,
                                                       "industry": "software", "role": "software", "level": "junior"})
            report = response.json()
            self.assertEqual(report["source_text"]["resume"], self.resume.strip())
            self.assertEqual(report["market"]["status"], "available")
            self.assertEqual(report["overall_score"], sum(item["points"] for item in report["score_items"]))
            self.assertEqual(client.get("/api/rubric").json()["max_points"], 100)
            self.assertEqual(client.get("/api/market/snapshot").status_code, 200)


if __name__ == "__main__":
    unittest.main()
