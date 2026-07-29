from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

import feedparser
import yaml

ROOT = Path(__file__).resolve().parents[2]
STATE_PATH = ROOT / "data" / "state.json"
PUBLIC_PATH = ROOT / "docs" / "data" / "news-hub.json"
LOG_DIR = ROOT / "data" / "selection_logs"


@dataclass
class Article:
    id: str
    source_id: str
    source_name: str
    title: str
    excerpt: str
    url: str
    published_at: str
    collected_at: str
    topics: list[str]


def utcnow() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith(("utm_", "fbclid", "gclid"))]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/") or "/", urlencode(query), ""))


def clean_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value or "")
    return " ".join(html.unescape(value).split())


def article_id(source_id: str, url: str, title: str) -> str:
    payload = f"{source_id}|{canonical_url(url)}|{clean_text(title).lower()}".encode()
    return hashlib.sha256(payload).hexdigest()[:24]


def read_yaml(name: str) -> dict[str, Any]:
    with (ROOT / "config" / name).open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"seen": {}, "radar": [], "recent_topics": [], "alerts": []}
    with STATE_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, content: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(content, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def public_article(article: Article | dict[str, Any]) -> dict[str, Any]:
    """Keep publishers' excerpts in memory only; never persist or publish them."""
    value = asdict(article) if isinstance(article, Article) else dict(article)
    value.pop("excerpt", None)
    return value


def parse_entry_date(entry: Any, fallback: datetime) -> datetime:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed:
        try:
            return datetime(*parsed[:6], tzinfo=UTC)
        except (TypeError, ValueError):
            pass
    return fallback


def collect_source(source: dict[str, Any], collected: datetime) -> tuple[list[Article], dict[str, str]]:
    feed_url = source.get("feed_url")
    if not feed_url:
        return [], {"id": source["id"], "status": "disabled", "detail": "No verified feed URL configured."}
    try:
        request = Request(feed_url, headers={"User-Agent": "EuropePulseNewsAgent/0.1 (+https://europepulse.eu)"})
        with urlopen(request, timeout=20) as response:
            payload = response.read()
        feed = feedparser.parse(payload)
        if feed.bozo and not feed.entries:
            raise ValueError(str(feed.bozo_exception))
        articles: list[Article] = []
        for entry in feed.entries[:80]:
            url = canonical_url(entry.get("link", ""))
            title = clean_text(entry.get("title", ""))
            if not url or not title:
                continue
            articles.append(Article(
                id=article_id(source["id"], url, title), source_id=source["id"], source_name=source["name"],
                title=title, excerpt=clean_text(entry.get("summary", entry.get("description", "")))[:600],
                url=url, published_at=iso(parse_entry_date(entry, collected)), collected_at=iso(collected),
                topics=source.get("topics", []),
            ))
        return articles, {"id": source["id"], "status": "ok", "count": str(len(articles)), "at": iso(collected)}
    except (HTTPError, URLError, TimeoutError, ValueError) as error:
        return [], {"id": source["id"], "status": "error", "detail": str(error)[:220], "at": iso(collected)}


def valid_selection(value: Any, max_top_stories: int, max_europe_now: int) -> bool:
    if not isinstance(value, dict) or not isinstance(value.get("top_stories"), list) or not isinstance(value.get("europe_now"), list):
        return False
    if len(value["top_stories"]) > max_top_stories or len(value["europe_now"]) > max_europe_now:
        return False
    return all(
        isinstance(item, dict) and isinstance(item.get("title"), str) and isinstance(item.get("summary"), str)
        for item in value["top_stories"] + value["europe_now"]
    )


def llm_selection(articles: list[dict[str, Any]], policy: dict[str, Any], recent_topics: list[dict[str, Any]]) -> tuple[dict[str, Any], str]:
    """Return empty selection on any model/network/JSON failure; collection remains authoritative."""
    if not articles:
        return {"europe_now": [], "top_stories": []}, "not-run"
    prompt = {
        "task": "Select Europe Pulse news. Return JSON only.",
        "policy": {
            "europe_now": f"Return at most {policy['max_europe_now']} alerts. Require Europe relevance >=2/3 and urgency, broad public impact, or systemic/security significance. Exclude routine statements, isolated local crime, opinion, unverified claims and stories that are merely important. Do not select two alerts for the same developing event.",
            "top_stories": "Create distinct themes. Each summary must be exactly two factual sentences using only supplied data. Write in original, neutral language: do not quote or closely reproduce a source headline or excerpt. Do not invent facts or claim corroboration not present.",
        },
        "schema": {"europe_now": [{"title": "string", "summary": "string", "article_ids": ["string"]}],
                   "top_stories": [{"title": "string", "summary": "string", "article_ids": ["string"]}]},
        "recent_topics": recent_topics[-30:], "articles": articles,
    }
    raw = json.dumps(prompt, ensure_ascii=False)
    attempts: list[tuple[str, str, str]] = []
    if os.getenv("GEMINI_API_KEY"):
        attempts.append(("gemini", policy["models"]["gemini"], os.environ["GEMINI_API_KEY"]))
    if os.getenv("OR_API_KEY"):
        for model in policy["models"]["openrouter"]:
            if model != "openrouter/free" and not model.endswith(":free"):
                continue
            attempts.append(("openrouter", model, os.environ["OR_API_KEY"]))
    for provider, model, key in attempts:
        try:
            if provider == "gemini":
                endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
                body = {"contents": [{"parts": [{"text": raw}]}], "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1}}
                request = Request(endpoint, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
                with urlopen(request, timeout=45) as response:
                    result = json.load(response)
                text = result["candidates"][0]["content"]["parts"][0]["text"]
            else:
                body = {"model": model, "messages": [{"role": "user", "content": raw}], "temperature": 0.1,
                        "response_format": {"type": "json_object"}}
                request = Request("https://openrouter.ai/api/v1/chat/completions", data=json.dumps(body).encode(),
                                  headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "HTTP-Referer": "https://europepulse.eu"}, method="POST")
                with urlopen(request, timeout=45) as response:
                    result = json.load(response)
                text = result["choices"][0]["message"]["content"]
            selected = json.loads(text)
            if valid_selection(selected, policy["top_story_count"], policy["max_europe_now"]):
                return selected, f"{provider}:{model}"
        except (HTTPError, URLError, TimeoutError, ValueError, KeyError, IndexError, json.JSONDecodeError):
            continue
    return {"europe_now": [], "top_stories": []}, "failed"


def enrich_selection(selection: dict[str, Any], articles: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = {article["id"]: article for article in articles}
    for group in ("europe_now", "top_stories"):
        valid: list[dict[str, Any]] = []
        for item in selection.get(group, []):
            refs = [by_id[key] for key in item.get("article_ids", []) if key in by_id][:2]
            if refs:
                item["sources"] = [{"publisher": ref["source_name"], "url": ref["url"]} for ref in refs]
                item["source_count"] = len(set(ref["source_id"] for ref in refs))
                valid.append(item)
        selection[group] = valid
    return selection


def main() -> None:
    policy, source_config, now = read_yaml("policy.yml"), read_yaml("sources.yml"), utcnow()
    state = load_state()
    # The repository is public: remove legacy excerpts created by earlier runs.
    state["radar"] = [public_article(item) for item in state.get("radar", [])]
    all_articles: list[Article] = []
    health: list[dict[str, str]] = []
    for source in source_config["sources"]:
        articles, report = collect_source(source, now)
        all_articles.extend(articles)
        health.append(report)
    seen_cutoff = now - timedelta(hours=policy["seen_window_hours"])
    state["seen"] = {key: value for key, value in state.get("seen", {}).items() if datetime.fromisoformat(value.replace("Z", "+00:00")) >= seen_cutoff}
    new_articles = [article for article in all_articles if article.id not in state["seen"]]
    for article in new_articles:
        state["seen"][article.id] = article.collected_at
    combined = {item["id"]: item for item in state.get("radar", [])}
    combined.update({article.id: public_article(article) for article in new_articles})
    radar_cutoff = now - timedelta(hours=policy["radar_window_hours"])
    radar = [item for item in combined.values() if datetime.fromisoformat(item["collected_at"].replace("Z", "+00:00")) >= radar_cutoff]
    radar.sort(key=lambda item: item["published_at"], reverse=True)
    state["radar"] = radar[:policy["max_radar_items"]]
    candidates = state["radar"][:policy["max_llm_items"]]
    selection, model = llm_selection(candidates, policy, state.get("recent_topics", []))
    selection = enrich_selection(selection, state["radar"])
    state["recent_topics"] = (state.get("recent_topics", []) + [{"at": iso(now), "title": item["title"]} for item in selection["top_stories"]])[-80:]
    payload = {"generated_at": iso(now), "last_successful_collection_at": iso(now), "source_health": health,
               "europe_now": selection["europe_now"], "top_stories": selection["top_stories"], "radar": state["radar"]}
    write_json(STATE_PATH, state)
    write_json(PUBLIC_PATH, payload)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with (LOG_DIR / f"{now:%Y-%m}.jsonl").open("a", encoding="utf-8") as log:
        log.write(json.dumps({"at": iso(now), "model": model, "input_count": len(candidates), "new_items": len(new_articles),
                              "top_story_count": len(selection["top_stories"]), "alert_count": len(selection["europe_now"])}, ensure_ascii=False) + "\n")
    print(f"Collected {len(all_articles)} items; {len(new_articles)} new; model={model}")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Fatal collection error: {error}", file=sys.stderr)
        raise
