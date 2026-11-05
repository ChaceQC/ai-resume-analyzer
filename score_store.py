"""只保存输入指纹、分值和版本，不保存简历、岗位原文或引用。"""

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from scoring import VERSION, fingerprint


def remember(report: dict, text: str, job: str) -> None:
    key = fingerprint({"resume_hash": fingerprint(text), "job_hash": fingerprint(job), "context": report["context"],
                       "rule_version": VERSION, "market_snapshot": report["market"].get("snapshot_id"),
                       "market_status": report["market"]["status"]})
    groups = {"quality": report["score_items"], "job": report["requirements"], "market": report["market"]["items"], "application": report["application"]["items"]}
    values = {group: {item["id"]: item["points"] for item in items} for group, items in groups.items()}
    payload = json.dumps(values, sort_keys=True, separators=(",", ":"))
    meta = report["meta"]
    meta.update(score_snapshot_id=key[:20], score_cache="unavailable", scored_at=datetime.now(timezone.utc).isoformat())
    path = Path(os.getenv("SCORE_CACHE_PATH", str(Path(__file__).resolve().parent / "data" / "scores.sqlite3")))
    if not path.is_absolute():
        path = Path(__file__).resolve().parent / path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path, timeout=5) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS scores (id TEXT PRIMARY KEY, version TEXT NOT NULL, points TEXT NOT NULL, created_at TEXT NOT NULL)")
            connection.execute("INSERT OR IGNORE INTO scores VALUES (?, ?, ?, ?)", (key, VERSION, payload, meta["scored_at"]))
            stored = connection.execute("SELECT points, created_at FROM scores WHERE id=?", (key,)).fetchone()
            if stored[0] != payload:
                # 每次重算校验；损坏或不一致的缓存不能覆盖规则结果。
                connection.execute("UPDATE scores SET points=?, created_at=? WHERE id=?", (payload, meta["scored_at"], key))
                meta["score_cache"] = "repaired"
            else:
                meta["score_cache"] = "hit" if stored[1] != meta["scored_at"] else "stored"
                meta["scored_at"] = stored[1]
    except (OSError, sqlite3.Error):
        logging.getLogger("resume_analyzer").warning("Score snapshot storage unavailable; deterministic calculation retained")
