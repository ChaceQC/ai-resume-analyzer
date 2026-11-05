"""有版本的公开招聘样本；分析请求只读取快照，不访问外部网络。"""

import gzip
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from scoring import INDUSTRIES, LEVELS, ROLES, VERSION, candidate_skills, citation, facts, fingerprint, rounded


ROOT = Path(__file__).resolve().parent
DEFAULT_SNAPSHOT = ROOT / "market_data" / "snapshot.json.gz"
MIN_JOBS, MIN_COMPANIES = 30, 3
MAX_SNAPSHOT_DAYS = 14


def role_for_title(title: str) -> str:
    for key in ("engineering", "healthcare", "design", "finance", "people", "data", "software", "sales", "product"):
        if re.search(ROLES[key][1], title, re.I):
            return key
    return ""


def level_for_title(title: str) -> str:
    if re.search(r"intern|graduate|new grad|early career|实习|校招", title, re.I):
        return "graduate"
    if re.search(r"manager|director|head of|vice president|\bvp\b|经理|总监", title, re.I):
        return "manager"
    if re.search(r"senior|staff|principal|lead |资深|高级|专家", title, re.I):
        return "senior"
    return "junior"


def load_snapshot(path=None) -> dict:
    path = Path(path or os.getenv("MARKET_SNAPSHOT_PATH", str(DEFAULT_SNAPSHOT)))
    if not path.is_absolute():
        path = ROOT / path
    try:
        if path.stat().st_size > 20 * 1024 * 1024:
            raise ValueError("market snapshot too large")
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt", encoding="utf-8") as stream:
            snapshot = json.load(stream)
        if snapshot.get("schema_version") != 1 or not isinstance(snapshot.get("jobs"), list):
            raise ValueError("invalid market schema")
        expected = fingerprint({key: value for key, value in snapshot.items() if key != "id"})
        if snapshot.get("id") != expected:
            raise ValueError("market snapshot checksum mismatch")
        return snapshot
    except (OSError, EOFError, ValueError, TypeError, AttributeError):
        return {}


def status() -> dict:
    snapshot = load_snapshot()
    return {"available": bool(snapshot), "snapshot_id": snapshot.get("id"), "captured_at": snapshot.get("captured_at"),
            "scope": snapshot.get("scope"), "jobs": len(snapshot.get("jobs", [])),
            "companies": len({job.get("company") for job in snapshot.get("jobs", [])}),
            "industries": INDUSTRIES, "roles": {key: value[0] for key, value in ROLES.items()}, "levels": LEVELS}


def location_matches(location: str, requested: str) -> bool:
    aliases = {"中国": r"china|中国|beijing|shanghai|shenzhen|hangzhou|guangzhou", "北京": "beijing|北京", "上海": "shanghai|上海",
               "深圳": "shenzhen|深圳", "杭州": "hangzhou|杭州", "广州": "guangzhou|广州", "香港": "hong kong|香港",
               "美国": "united states|usa|new york|san francisco|austin|seattle|美国", "新加坡": "singapore|新加坡"}
    return bool(re.search(aliases.get(requested, re.escape(requested)), location, re.I))


def assess(text: str, job: str, context: dict, snapshot=None, now=None) -> dict:
    snapshot = load_snapshot() if snapshot is None else snapshot
    now = now or datetime.now(timezone.utc)
    result = {"status": "unavailable", "score": None, "items": [], "sources": [], "sample_count": 0,
              "company_count": 0, "snapshot_id": snapshot.get("id"), "captured_at": snapshot.get("captured_at"),
              "scope": snapshot.get("scope", "尚未加载招聘样本"), "context": context,
              "target_label": " / ".join([INDUSTRIES.get(context.get("industry"), "未指定行业"), ROLES.get(context.get("role"), ("未指定岗位",))[0], LEVELS.get(context.get("level"), "未指定职级"), context.get("city") or "样本内不限地区"]),
              "method": "同岗位、行业、职级与指定地区筛选；按企业统计能力要求频次，单项权重不按重复职位数量累计。",
              "limitations": "样本来自企业公开职位，反映招聘要求；不代表全部市场、求职者排名、实际薪资或录用概率。"}
    if not snapshot:
        result["message"] = "暂无有效行情快照，请先运行招聘数据更新工具。"
        return result
    try:
        captured = datetime.fromisoformat(snapshot["captured_at"])
        age = now - captured
        if captured.tzinfo is None or age > timedelta(days=MAX_SNAPSHOT_DAYS) or age < -timedelta(hours=1):
            raise ValueError("stale or future snapshot")
    except (ValueError, TypeError, KeyError):
        result.update(status="stale", message="行情快照已过期或时间无效，需要更新后评估。")
        return result
    if not all(context.get(key) for key in ("industry", "role", "level")):
        result.update(status="needs_context", message="选择目标行业、岗位族和职级后，可对照同类招聘样本。")
        return result
    selected = [entry for entry in snapshot["jobs"] if entry.get("industry") == context["industry"]
                and entry.get("role") == context["role"] and entry.get("level") == context["level"]
                and (not context.get("city") or location_matches(entry.get("location", ""), context["city"]))]
    companies = {entry["company"] for entry in selected}
    result.update(sample_count=len(selected), company_count=len(companies))
    if len(selected) < MIN_JOBS or len(companies) < MIN_COMPANIES:
        result.update(status="insufficient", message=f"该范围只有 {len(selected)} 条职位、{len(companies)} 家企业；试点评估至少需要 {MIN_JOBS} 条职位和 {MIN_COMPANIES} 家企业，暂不计分。")
        return result
    mentions_by_company, supports = defaultdict(set), defaultdict(list)
    for entry in selected:
        for requirement in entry.get("requirements", []):
            skill, quote = requirement.get("skill"), requirement.get("quote")
            url = entry.get("url", "")
            if not skill or not quote or urlsplit(url).scheme != "https":
                continue
            mentions_by_company[skill].add(entry["company"])
            supports[skill].append({"id": entry["id"], "company": entry["company"], "title": entry["title"],
                                    "location": entry["location"], "url": url, "quote": quote,
                                    "updated_at": entry["updated_at"]})
    ranked = sorted((skill for skill in mentions_by_company if len(mentions_by_company[skill]) >= 2),
                    key=lambda skill: (-len(mentions_by_company[skill]), skill))[:10]
    if not ranked:
        result.update(status="insufficient", message="同类样本中尚未提取到跨企业的共同能力要求，暂不计分。")
        return result
    resume_facts = facts(text)
    mentions = set(candidate_skills(text, resume_facts["units"]))
    total = sum(len(mentions_by_company[skill]) for skill in ranked)
    remaining = 100.0
    items = []
    for index, skill in enumerate(ranked):
        weight = len(mentions_by_company[skill])
        maximum = round(weight * 100 / total, 2) if index < len(ranked) - 1 else round(remaining, 2)
        remaining = round(remaining - maximum, 2)
        ratio = 1 if skill in resume_facts["practiced"] else (.25 if skill in mentions else 0)
        evidence = ([citation(resume_facts["practiced"][skill])] if skill in resume_facts["practiced"] else
                    [citation(unit) for unit in resume_facts["units"] if skill in candidate_skills("", [unit])][:1])
        items.append({"id": "market-" + fingerprint(skill)[:12], "title": skill, "skill": skill,
                      "max_points": maximum, "points": round(maximum * ratio, 2), "company_mentions": weight,
                      "company_total": len(companies), "frequency": round(weight / len(companies), 3),
                      "rule": "权重按提及该能力的企业数分配；明确应用得100%，仅陈述得25%，未提供证据得0%。",
                      "rule_version": VERSION, "evidence": evidence,
                      "sources": sorted(supports[skill], key=lambda entry: (entry["company"], entry["id"]))})
    source_companies = {entry["company"] for item in items for entry in item["sources"]}
    result.update(status="available", score=rounded(sum(item["points"] for item in items)), items=items,
                  message="已对照当前快照中的同类公开职位；这是有限样本的要求适配参考。",
                  sources=[source for source in snapshot.get("sources", []) if source["company"] in source_companies])
    return result


def attach(report: dict, text: str, job: str, snapshot=None, now=None) -> None:
    market = assess(text, job, report["context"], snapshot, now)
    report["market"] = market
    report["application"] = {"score": None, "items": [], "message": "需要可评估的岗位要求和有效行情样本，才能计算投递综合分。"}
    if market["score"] is None or report["job_match_score"] is None:
        return
    explicit = {name for requirement in report["requirements"] for name in requirement["skills"]}
    additional = [item for item in market["items"] if item["skill"] not in explicit]
    job_budget, market_budget = (50, 10) if additional else (60, 0)
    ledger = []
    for group, budget, source_items in (("quality", 40, report["score_items"]), ("job", job_budget, report["requirements"]), ("market", market_budget, additional)):
        total = sum(item["max_points"] for item in source_items)
        remaining = float(budget)
        for index, item in enumerate(source_items):
            maximum = round(item["max_points"] / total * budget, 2) if index < len(source_items) - 1 else round(remaining, 2)
            remaining = round(remaining - maximum, 2)
            ledger.append({"id": group + ":" + item["id"], "source_group": group, "source_id": item["id"], "title": item["title"],
                           "max_points": maximum, "points": round(maximum * item["points"] / item["max_points"], 2)})
    raw = round(sum(item["points"] for item in ledger), 2)
    report["application"] = {"score": rounded(raw), "raw_score": raw, "items": ledger,
                             "message": f"质量40分、岗位{job_budget}分、行情补充{market_budget}分；行情只补充岗位未列出的能力，避免重复加权。最终总分四舍五入。"}
