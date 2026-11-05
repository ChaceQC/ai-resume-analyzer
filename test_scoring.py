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
from analyzer import local_analyze
from app import ROOT, app
from score_store import remember
from scoring import DIMENSIONS, VERSION, extract_skills, facts, fingerprint, parse_requirements, rounded
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
    @classmethod
    def setUpClass(cls):
        cls.resume = (ROOT / "samples/resume.txt").read_text(encoding="utf-8")
        cls.job = (ROOT / "samples/job.txt").read_text(encoding="utf-8")

    def test_twenty_cold_calculations_have_zero_variation(self):
        reports = [local_analyze(self.resume, self.job) for _ in range(20)]
        self.assertEqual(len({report["overall_score"] for report in reports}), 1)
        self.assertEqual(len({report["job_match_score"] for report in reports}), 1)
        self.assertTrue(all(report["score_items"] == reports[0]["score_items"] for report in reports))

    def test_soft_wrapping_and_heading_aliases_preserve_scores(self):
        sentence = "通过 Redis 缓存热门商品，将接口平均响应时间从 320ms 降低到 110ms。"
        baseline = local_analyze(self.resume, self.job)
        variants = [self.resume.replace(sentence, sentence.replace("将接口", "\n将接口")),
                    self.resume.replace("项目经历", "相关实践"), self.resume.replace("项目经历", "研究经历"),
                    self.resume.replace("专业技能", "【核心能力】"), self.resume.replace("\n", "\r\n")]
        for text in variants:
            with self.subTest(text=text[:20]):
                report = local_analyze(text, self.job)
                self.assertLessEqual(abs(report["overall_score"] - baseline["overall_score"]), 2)
                self.assertEqual(report["job_match_score"], baseline["job_match_score"])

    def test_negation_future_and_contrast(self):
        for text in ("不熟悉 Python，尚未使用过 Docker。", "计划学习 Python 和 Docker。",
                     "No experience with Python; never used Docker.", "Python、Docker 都不会。"):
            self.assertEqual(extract_skills(text), [], text)
        self.assertEqual(extract_skills("不熟悉 Python，但熟悉 Docker。"), ["Docker"])
        self.assertEqual(extract_skills("不仅熟悉 Python，还熟悉 Docker。"), ["Python", "Docker"])
        self.assertEqual(local_analyze("不熟悉 Python，尚未使用 Docker。", "Python Docker")["job_match_score"], 0)

    def test_or_requirements_optional_priority_and_practice(self):
        report = local_analyze(self.resume, "熟悉 FastAPI 或 Django。")
        self.assertEqual(report["job_match_score"], 100)
        self.assertEqual(report["missing_skills"], [])
        requirements = parse_requirements("必备条件\n熟悉 Python\n加分项\n了解 Kubernetes")
        by_name = {item["skills"][0]: item for item in requirements}
        self.assertEqual(by_name["Python"]["weight"], 3)
        self.assertEqual(by_name["Kubernetes"]["weight"], 1)
        self.assertEqual(local_analyze(self.resume, "必备条件\n熟悉 Python\n加分项\n了解 Kubernetes")["job_match_score"], 75)
        self.assertEqual(len(parse_requirements("熟悉 Python。熟悉 Python。")), 1)
        self.assertEqual(parse_requirements("不要求 Python，仅要求 Docker。")[0]["skills"], ["Docker"])
        self.assertEqual(local_analyze("专业技能\nPython", "需要 Python 项目经验")["job_match_score"], 50)

    def test_team_result_does_not_establish_personal_ownership(self):
        text = "项目经历\n团队将接口响应耗时从 200ms 降低至 50ms，本人参与接口文档整理。"
        evidence = facts(text)
        self.assertEqual(evidence["owned"], [])
        self.assertEqual(evidence["comparisons"], [])
        self.assertEqual(evidence["outcomes"], [])
        report = local_analyze("项目经历\n团队使用 Python 开发模型，本人仅负责接口文档。", "Python")
        self.assertEqual(report["job_match_score"], 0)
        self.assertNotIn("Python", report["skills"])
        self.assertEqual(local_analyze("项目经历\n团队独立使用 Python 实现整个系统。", "Python")["job_match_score"], 0)
        self.assertEqual(local_analyze("Work Experience\nMy team used Python to build the service.", "Python")["job_match_score"], 0)
        self.assertEqual(local_analyze("项目经历\n团队使用 Python 开发模型，本人使用 SQL 实现报表查询。", "SQL")["job_match_score"], 100)

    def test_job_titles_do_not_count_as_actions_or_practical_skill_evidence(self):
        text = "陈同学\nPython 后端开发工程师\n电话：13800138000\n专业技能\n熟悉 Python。"
        evidence = facts(text)
        self.assertEqual(evidence["actions"], [])
        self.assertNotIn("Python", evidence["practiced"])
        report = local_analyze(text, "需要 Python 项目经验")
        self.assertEqual(report["job_match_score"], 50)

    def test_degree_years_and_certifications_are_explicit_job_conditions(self):
        text = "教育背景\n示例大学 本科\n专业技能\n持有 CPA 注册会计师资格\n工作经历\n具有5年财务会计工作经验。"
        report = local_analyze(text, "本科及以上，至少3年财务工作经验，CPA 优先。")
        self.assertEqual(report["job_match_score"], 100)
        self.assertEqual({item.get("kind", "skill") for item in report["requirements"]}, {"degree", "years", "skill"})
        self.assertEqual(local_analyze("教育背景\n示例大学 本科在读", "本科及以上学历")["job_match_score"], 0)
        self.assertEqual(local_analyze("教育背景\n示例大学 本科在读", "本科在读可申请")["job_match_score"], 100)
        self.assertEqual(local_analyze("本人尚未取得本科学历，也没有3年工作经验。", "本科，3年工作经验")["job_match_score"], 0)
        self.assertEqual(local_analyze("教育背景\n2020—2025 示例大学 本科", "5年工作经验")["job_match_score"], 0)
        self.assertEqual(extract_skills("Python is not required, Docker preferred."), ["Docker"])

    def test_ledger_sums_and_all_awarded_points_have_exact_citations(self):
        report = local_analyze(self.resume, self.job)
        self.assertEqual(sum(item["max_points"] for item in report["score_items"]), 100)
        self.assertEqual(sum(item["points"] for item in report["score_items"]), report["overall_score"])
        for label, maximum in DIMENSIONS.items():
            group = [item for item in report["score_items"] if item["dimension"] == label]
            self.assertEqual(sum(item["max_points"] for item in group), maximum)
        for item in report["score_items"] + report["requirements"]:
            if item["points"] and item in report["score_items"]:
                self.assertTrue(item["evidence"], item["id"])
            for ref in item.get("evidence", []) + item.get("resume_evidence", []):
                source = self.resume if ref["source"] == "resume" else self.job
                self.assertEqual(source[ref["start"]:ref["end"]], ref["quote"])

    def test_full_width_and_pdf_quotes_use_original_character_offsets(self):
        text = self.resume.replace("Python", "Ｐｙｔｈｏｎ").replace("将接口", "\n将接口")
        report = local_analyze(text, self.job)
        for item in report["score_items"]:
            for ref in item["evidence"]:
                self.assertEqual(text[ref["start"]:ref["end"]], ref["quote"])

    def test_duplicates_do_not_improve_action_or_result_points(self):
        sentence = "通过 Redis 缓存热门商品，将接口平均响应时间从 320ms 降低到 110ms。"
        baseline = local_analyze(self.resume, "")
        repeated = local_analyze(self.resume.replace("自我评价", "\n".join(f"{n}. {sentence}" for n in range(10)) + "\n自我评价"), "")
        for identifier in ("results.action", "results.outcome", "results.comparison", "skills.method"):
            self.assertEqual(next(item["points"] for item in baseline["score_items"] if item["id"] == identifier),
                             next(item["points"] for item in repeated["score_items"] if item["id"] == identifier))

    def test_level_changes_are_explicit_and_rounding_is_half_up(self):
        graduate = local_analyze(self.resume, self.job, {"level": "graduate"})
        senior = local_analyze(self.resume, self.job, {"level": "senior"})
        self.assertNotEqual(graduate["context"], senior["context"])
        self.assertEqual(rounded(78.5), 79)
        self.assertEqual(graduate["meta"]["scoring_version"], VERSION)


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
        self.assertEqual(result["score"], rounded(sum(item["points"] for item in result["items"])))
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
        report = local_analyze(self.resume, "熟悉 Python", self.context)
        market.attach(report, self.resume, "熟悉 Python", self.snapshot)
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
        report = local_analyze(self.resume, self.job)
        market.attach(report, self.resume, self.job, {})
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
        with TestClient(app) as client, patch("market.load_snapshot", return_value=fixture_snapshot()):
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
