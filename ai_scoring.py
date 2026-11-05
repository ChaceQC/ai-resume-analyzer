"""校验模型的逐项判断，并按公开权重加总；不通过关键词替模型判分。"""

from scoring import SKILLS, fingerprint, quote_reference, rounded
from rubric import DIMENSIONS


def explanation(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 800:
        raise ValueError("missing scoring reason")
    return value.strip()


def references(values, text, source="resume"):
    if not isinstance(values, list) or len(values) > 5:
        raise ValueError("invalid scoring evidence")
    return [quote_reference(text, quote, source) for quote in values]


def indexed(values, expected):
    if not isinstance(values, list) or len(values) != len(expected):
        raise ValueError("incomplete scoring items")
    result = {}
    for item in values:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or item["id"] in result:
            raise ValueError("invalid or duplicate scoring id")
        result[item["id"]] = item
    if set(result) != {item["id"] for item in expected}:
        raise ValueError("unknown or missing scoring id")
    return result


def apply_scores(data: dict, report: dict, text: str, job: str):
    scores = indexed(data.get("score_items"), report["score_items"])
    for item in report["score_items"]:
        assessed = scores[item["id"]]
        points = assessed.get("points")
        if type(points) is not int or not 0 <= points <= item["max_points"]:
            raise ValueError("quality points outside rubric")
        reason = explanation(assessed.get("reason"))
        evidence = references(assessed.get("evidence"), text)
        if points and not evidence:
            raise ValueError("awarded quality points need resume evidence")
        item.update(points=points, evidence=evidence, observation=reason, missing=reason if points < item["max_points"] else "")
    report["overall_score"] = sum(item["points"] for item in report["score_items"])
    report["dimensions"] = []
    for label, maximum in DIMENSIONS.items():
        earned = sum(item["points"] for item in report["score_items"] if item["dimension"] == label)
        report["dimensions"].append({"label": label, "points": earned, "max_points": maximum,
                                     "score": rounded(earned * 100 / maximum), "detail": f"{earned}/{maximum}分"})

    requirements = data.get("requirements")
    if not isinstance(requirements, list) or len(requirements) > 40 or (requirements and not job):
        raise ValueError("invalid AI job requirements")
    records, seen = [], set()
    for entry in requirements:
        if not isinstance(entry, dict):
            raise ValueError("invalid job requirement")
        title, reason = explanation(entry.get("title")), explanation(entry.get("reason"))
        priority, match = entry.get("priority"), entry.get("match")
        if priority not in {"必备", "加分"} or match not in {"met", "partial", "missing"}:
            raise ValueError("invalid requirement assessment")
        job_ref = quote_reference(job, entry.get("job_quote"), "job")
        key = fingerprint([title, job_ref["quote"]])[:12]
        if key in seen:
            raise ValueError("duplicate job requirement")
        seen.add(key)
        evidence = references(entry.get("evidence"), text)
        if match != "missing" and not evidence:
            raise ValueError("matched requirement needs resume evidence")
        skills = entry.get("skills", [])
        if not isinstance(skills, list) or any(not isinstance(name, str) for name in skills):
            raise ValueError("invalid requirement skills")
        records.append({"id": "req-" + key, "title": title, "skills": [name for name in skills if name in SKILLS],
                        "priority": priority, "weight": 3 if priority == "必备" else 1, "match": match,
                        "status": {"met": "AI判断满足", "partial": "AI判断部分满足", "missing": "缺少明确证据"}[match],
                        "rule": "AI对照岗位原文判断：满足得100%，部分满足得50%，无证据得0%；必备权重3，加分权重1。",
                        "rule_version": report["meta"]["scoring_version"], "observation": reason,
                        "missing": reason if match != "met" else "", "evidence": [job_ref], "resume_evidence": evidence,
                        "matched": (skills or [title]) if match != "missing" else []})
    total = sum(item["weight"] for item in records)
    remaining = 100.0
    for index, item in enumerate(records):
        maximum = round(100 * item["weight"] / total, 2) if index < len(records) - 1 else round(remaining, 2)
        remaining = round(remaining - maximum, 2)
        item.update(max_points=maximum, points=round(maximum * {"met": 1, "partial": .5, "missing": 0}[item["match"]], 2))
    report["requirements"] = records
    report["job_match_score"] = rounded(sum(item["points"] for item in records)) if records else None
    report["matched_skills"] = list(dict.fromkeys(name for item in records for name in item["matched"]))
    report["missing_skills"] = list(dict.fromkeys(name for item in records if item["match"] != "met" for name in (item["skills"] or [item["title"]])))

    market = report.get("market", {"items": []})
    assessed_market = indexed(data.get("market_items"), market["items"])
    for item in market["items"]:
        assessed = assessed_market[item["id"]]
        match = assessed.get("match")
        if match not in {"practiced", "stated", "missing"}:
            raise ValueError("invalid market assessment")
        reason = explanation(assessed.get("reason"))
        evidence = references(assessed.get("evidence"), text)
        if match != "missing" and not evidence:
            raise ValueError("matched market skill needs resume evidence")
        item.update(points=round(item["max_points"] * {"practiced": 1, "stated": .25, "missing": 0}[match], 2),
                    observation=reason, evidence=evidence, missing=reason if match != "practiced" else "")
    if market.get("status") == "available":
        market.update(score=rounded(sum(item["points"] for item in market["items"])),
                      message="AI已对照同类公开职位判断能力证据；这是有限样本的要求适配参考。")
