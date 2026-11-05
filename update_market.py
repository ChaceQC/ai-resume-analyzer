"""更新可审计的行情快照。python update_market.py --help"""

import argparse
import gzip
import html
import json
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from market import DEFAULT_SNAPSHOT, level_for_title, role_for_title
from scoring import LEVELS, VERSION, fingerprint, normalize, positive_mentions


BOARDS = {
    "canonical": ("Canonical", "software"), "gitlab": ("GitLab", "software"),
    "mongodb": ("MongoDB", "software"), "cloudflare": ("Cloudflare", "software"),
    "datadog": ("Datadog", "software"), "stripe": ("Stripe", "finance"),
    "affirm": ("Affirm", "finance"), "brex": ("Brex", "finance"), "robinhood": ("Robinhood", "finance"),
    "oscar": ("Oscar Health", "healthcare"), "flatironhealth": ("Flatiron Health", "healthcare"),
    "natera": ("Natera", "healthcare"), "insitro": ("insitro", "healthcare"),
    "spacex": ("SpaceX", "engineering"), "andurilindustries": ("Anduril", "engineering"),
    "rocketlab": ("Rocket Lab", "engineering"),
}


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {"p", "li", "br", "div", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"p", "li", "div", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)


def plain_text(content: str) -> str:
    parser = PlainText()
    parser.feed(html.unescape(html.unescape(content)))
    return "\n".join(line.strip() for line in "".join(parser.parts).splitlines() if line.strip())


def market_requirements(content: str) -> list[dict]:
    requirements = {}
    qualification = False
    for line in re.split(r"\n|(?<=[.!?])\s+", content):
        if len(line) <= 160 and re.search(r"qualifications|requirements|what you.{0,12}bring|what we look for|who you are|about you|your (?:skills|experience)|任职要求|任职资格", line, re.I):
            qualification = True
        if len(line) <= 160 and re.search(r"benefits|compensation|our (?:benefits|culture)|what we offer|equal opportunity", line, re.I):
            qualification = False
        if not qualification and not re.search(r"experience (?:in|with)|knowledge of|proficien|familiar with|expertise in|熟悉|掌握|工作经验", line, re.I):
            continue
        for mention in positive_mentions(line):
            start = max(0, mention["start"] - 90)
            quote = normalize(line)[start:mention["end"] + 120].strip()
            requirements.setdefault(mention["skill"], {"skill": mention["skill"], "quote": quote})
    return sorted(requirements.values(), key=lambda item: item["skill"])


def build_snapshot(entries: list[dict], sources: list[dict], captured_at: str) -> dict:
    now = datetime.now(timezone.utc)
    jobs, seen, counts = [], set(), Counter()
    for entry in sorted(entries, key=lambda item: (item.get("updated_at", ""), str(item.get("id", ""))), reverse=True):
        try:
            updated = datetime.fromisoformat(entry["updated_at"])
            if updated.tzinfo is None or not now - timedelta(days=60) <= updated <= now + timedelta(hours=1):
                continue
            role = role_for_title(entry["title"])
            level = entry.get("level") or level_for_title(entry["title"])
            if level not in LEVELS:
                continue
            if not role or urlsplit(entry["url"]).scheme != "https":
                continue
            group = (entry["company"], role, level, normalize(entry["location"]).casefold())
            if counts[group] >= 25:
                continue
            content = plain_text(entry["content"])
            requirements = market_requirements(content)
            if not requirements:
                continue
            key = fingerprint([entry["company"], normalize(entry["title"]).casefold(), normalize(entry["location"]).casefold(), requirements])
            if key in seen:
                continue
            seen.add(key)
            counts[group] += 1
            jobs.append({"id": key[:20], "company": entry["company"], "industry": entry["industry"],
                         "title": entry["title"], "location": entry["location"], "role": role, "level": level,
                         "url": entry["url"], "updated_at": entry["updated_at"], "requirements": requirements,
                         "description_hash": fingerprint(content)})
        except (KeyError, TypeError, ValueError):
            continue
    source_names = {entry["company"] for entry in jobs}
    snapshot = {"schema_version": 1, "extractor_version": VERSION, "captured_at": captured_at,
                "scope": "所列国内与国际企业的公开招聘样本，按企业、岗位族、职级和工作地点限额抽取；不代表全国或整个行业。",
                "selection": "企业公开职位；最近60天更新；同企业/岗位族/职级/工作地点最多25条；公司、职位、地点、要求去重。更新时间不等于首次发布时间。",
                "sources": [source for source in sources if source["company"] in source_names],
                "jobs": sorted(jobs, key=lambda entry: entry["id"])}
    snapshot["id"] = fingerprint(snapshot)
    return snapshot


def main():
    parser = argparse.ArgumentParser(description="从国内企业官网、Greenhouse 公开接口或标准 JSON 更新行情，分析请求不联网。")
    parser.add_argument("--proxy", help="可选 HTTP 代理，仅用于本次更新")
    parser.add_argument("--output", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--cache-dir", type=Path, help="可选：读取/保存 resume-market-公司.json 原始接口响应，24小时内复用")
    parser.add_argument("--input", type=Path, help="导入包含 entries、sources、captured_at 的标准 JSON，字段见 README")
    parser.add_argument("--sources", choices=("all", "domestic", "international"), default="all", help="默认同时采集国内与国际来源")
    args = parser.parse_args()
    if args.input:
        imported = json.loads(args.input.read_text(encoding="utf-8"))
        entries, sources, captured = imported["entries"], imported["sources"], imported["captured_at"]
        datetime.fromisoformat(captured)
    else:
        def fetch(board):
            company, industry = BOARDS[board]
            url = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
            cached = args.cache_dir / f"resume-market-{board}.json" if args.cache_dir else None
            try:
                if cached and cached.exists() and datetime.fromtimestamp(cached.stat().st_mtime, timezone.utc) > datetime.now(timezone.utc) - timedelta(hours=24):
                    payload = json.loads(cached.read_text(encoding="utf-8"))
                    fetched = datetime.fromtimestamp(cached.stat().st_mtime, timezone.utc).isoformat()
                else:
                    with httpx.Client(proxy=args.proxy, timeout=25) as client:
                        response = client.get(url)
                        response.raise_for_status()
                    payload = response.json()
                    fetched = datetime.now(timezone.utc).isoformat()
                    if cached:
                        cached.parent.mkdir(parents=True, exist_ok=True)
                        cached.write_text(json.dumps(payload), encoding="utf-8")
                entries = [{"id": item["id"], "company": company, "industry": industry, "title": item["title"],
                            "location": item["location"]["name"], "url": item["absolute_url"],
                            "updated_at": item["updated_at"], "content": item.get("content", "")} for item in payload["jobs"]]
                return entries, {"company": company, "url": url, "captured_at": fetched, "raw_count": len(entries)}
            except (httpx.HTTPError, OSError, KeyError, TypeError, ValueError) as exc:
                print(f"{company}: 更新失败 ({type(exc).__name__})")
                return [], None
        entries, sources = [], []
        if args.sources != "domestic":
            with ThreadPoolExecutor(max_workers=6) as pool:
                for fetched_entries, source in pool.map(fetch, BOARDS):
                    entries.extend(fetched_entries)
                    if source:
                        sources.append(source)
        if args.sources != "international":
            from market_sources import collect_domestic
            domestic_entries, domestic_sources = collect_domestic(args.proxy, args.cache_dir)
            entries.extend(domestic_entries)
            sources.extend(domestic_sources)
        if not entries:
            raise SystemExit("没有获取到有效数据，保留原快照。")
        captured = min(source["captured_at"] for source in sources)
    snapshot = build_snapshot(entries, sources, captured)
    if not snapshot["jobs"]:
        raise SystemExit("没有符合时间与内容要求的样本，保留原快照。")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")).encode()
    temporary = args.output.with_name(args.output.name + ".tmp")
    temporary.write_bytes(gzip.compress(data, mtime=0) if args.output.suffix == ".gz" else data)
    temporary.replace(args.output)
    groups = {}
    for entry in snapshot["jobs"]:
        group = (entry["industry"], entry["role"], entry["level"])
        counts = groups.setdefault(group, {"jobs": 0, "companies": set()})
        counts["jobs"] += 1
        counts["companies"].add(entry["company"])
    manifest = {"snapshot_id": snapshot["id"], "captured_at": snapshot["captured_at"], "extractor_version": VERSION,
                "scope": snapshot["scope"], "selection": snapshot["selection"], "job_count": len(snapshot["jobs"]),
                "company_count": len(snapshot["sources"]), "sources": snapshot["sources"],
                "coverage": [{"industry": group[0], "role": group[1], "level": group[2], "jobs": value["jobs"], "companies": len(value["companies"])} for group, value in sorted(groups.items())]}
    args.output.with_name("snapshot.manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"snapshot_id": snapshot["id"], "jobs": len(snapshot["jobs"]), "companies": len(snapshot["sources"]), "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
