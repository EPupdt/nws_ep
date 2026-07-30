"""Append-only record of what the hub actually published.

The selection log next to this module answers "did a run happen, and did the
model work". It cannot answer "what did we say", because it stores counts
only. That gap showed on 30.07.2026: two runs returned model=failed and
nothing in the log identified what had been dropped.

This module keeps the answer. One line per run whose published selection
differs from the previous one, so a quiet hour costs nothing.

Only material the project is allowed to republish is stored. The editorial
title and summary are our own words. For third-party items nothing is kept
beyond publisher, headline, publication time and the canonical link - the
same treatment the Radar already uses. Publisher excerpts are stripped
upstream by public_article() and must never be added back here.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

# The archive under data/ is the durable record and only ever grows at the
# end, which git stores cheaply. The copy under docs/ is what the page reads,
# and it is split one file per day for the same reason: a day that has passed
# is never rewritten again. A single rolling file would be republished on
# every run, and with the bot committing around the clock that would add tens
# of megabytes of near-identical blobs a year.
PUBLISHED_DAYS = 14
PUBLISHED_TZ = "Europe/Bratislava"


def _story(item: dict[str, Any]) -> dict[str, Any]:
    """Our own headline and summary, plus attributed links. Nothing else."""
    return {
        "title": str(item.get("title", ""))[:300],
        "summary": str(item.get("summary", ""))[:800],
        "source_count": int(item.get("source_count", 0) or 0),
        "sources": [
            {"publisher": str(source.get("publisher", ""))[:120], "url": str(source.get("url", ""))[:600]}
            for source in (item.get("sources") or [])
            if isinstance(source, dict) and source.get("url")
        ][:4],
    }


def _cluster(item: dict[str, Any]) -> dict[str, Any]:
    """A convergence cluster: a keyword label and the publishers' own headlines."""
    return {
        "label": str(item.get("label", ""))[:200],
        "source_count": int(item.get("source_count", 0) or 0),
        "item_count": int(item.get("item_count", 0) or 0),
        "sources": [
            {
                "publisher": str(source.get("publisher", ""))[:120],
                "headline": str(source.get("headline", ""))[:300],
                "url": str(source.get("url", ""))[:600],
                "published_at": str(source.get("published_at", ""))[:32],
            }
            for source in (item.get("sources") or [])
            if isinstance(source, dict) and source.get("url")
        ][:3],
    }


def entry(at: str, model: str, selection: dict[str, Any], developing: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "at": at,
        "model": model,
        "europe_now": [_story(item) for item in selection.get("europe_now", [])],
        "top_stories": [_story(item) for item in selection.get("top_stories", [])],
        "developing": [_cluster(item) for item in (developing or [])],
    }


def fingerprint(record_: dict[str, Any]) -> str:
    """Identity of a published state, ignoring the clock and the model used.

    Two consecutive runs that publish the same stories are the same editorial
    moment. Writing both would inflate the archive and make the history page
    read as though something happened every ten minutes when nothing did.
    """
    parts = (
        [item["title"] for item in record_["europe_now"]]
        + ["|"]
        + [item["title"] for item in record_["top_stories"]]
        + ["|"]
        + [item["label"] for item in record_["developing"]]
    )
    return hashlib.sha256(" ".join(parts).encode("utf-8")).hexdigest()[:16]


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            # A truncated final line must not cost us the whole archive.
            continue
    return records


def _local_day(value: str) -> str:
    """The editorial day an entry belongs to, in the newsroom timezone."""
    when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return when.astimezone(ZoneInfo(PUBLISHED_TZ)).strftime("%Y-%m-%d")


def _publish(root: Path, now: datetime) -> None:
    """Rewrite the day file this run belongs to, and refresh the day index.

    The model name is dropped here. It stays in the durable archive because
    it is worth having when something goes wrong, but the history page is an
    editorial record, not an operations dashboard, and which provider
    answered is already logged run by run in selection_logs.
    """
    out = root / "docs" / "data" / "history"
    out.mkdir(parents=True, exist_ok=True)

    cutoff = now - timedelta(days=PUBLISHED_DAYS)
    by_day: dict[str, list[dict[str, Any]]] = {}

    for path in sorted((root / "data" / "archive").glob("*.jsonl"))[-2:]:
        for item in _read_jsonl(path):
            try:
                when = datetime.fromisoformat(str(item.get("at", "")).replace("Z", "+00:00"))
            except ValueError:
                continue
            if when < cutoff:
                continue
            clean = {key: value for key, value in item.items() if key != "model"}
            by_day.setdefault(_local_day(item["at"]), []).append(clean)

    today = _local_day(now.isoformat().replace("+00:00", "Z"))

    for day, entries in by_day.items():
        # Only today can still change. Rewriting a finished day would produce
        # an identical file and a pointless commit.
        if day != today and (out / f"{day}.json").exists():
            continue
        entries.sort(key=lambda item: item["at"], reverse=True)
        (out / f"{day}.json").write_text(
            json.dumps({"day": day, "entries": entries}, ensure_ascii=False) + "\n", encoding="utf-8")

    index = sorted(({"day": day, "count": len(entries)} for day, entries in by_day.items()),
                   key=lambda item: item["day"], reverse=True)
    (out / "index.json").write_text(
        json.dumps({"days": index}, ensure_ascii=False) + "\n", encoding="utf-8")

    # Days that have fallen out of the window stop being served.
    keep = {f"{item['day']}.json" for item in index} | {"index.json"}
    for path in out.glob("*.json"):
        if path.name not in keep:
            path.unlink()


def record(root: Path, now: datetime, at: str, model: str,
           selection: dict[str, Any], developing: list[dict[str, Any]]) -> bool:
    """Append this run to the archive if it published something new.

    Returns True when a line was written. Never raises into the collector:
    the archive is a record of the run, not a precondition for it.
    """
    try:
        candidate = entry(at, model, selection, developing)
        if not (candidate["europe_now"] or candidate["top_stories"] or candidate["developing"]):
            return False

        month = root / "data" / "archive" / f"{now:%Y-%m}.jsonl"
        month.parent.mkdir(parents=True, exist_ok=True)

        previous = _read_jsonl(month)
        if previous and fingerprint(previous[-1]) == fingerprint(candidate):
            return False

        with month.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(candidate, ensure_ascii=False) + "\n")

        _publish(root, now)
        return True
    except OSError:
        return False
