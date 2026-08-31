from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sys
from dataclasses import dataclass, asdict
from datetime import UTC, datetime, timedelta
from http.client import IncompleteRead
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import feedparser
import yaml

from news_hub import archive
from news_hub.convergence import corpus_names, developing_panel, shares_a_name

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


def due_to_collect(now: datetime, policy: dict[str, Any], force: bool,
                   last_at: str | None = None) -> bool:
    """Use local editorial hours; GitHub's cron itself only understands UTC.

    Cadence is measured as time elapsed since the last collection, not by exact
    clock alignment. The previous rule required
    ``(minutes since band start) % every_minutes == 0``, which only holds when a
    run starts on the exact minute. GitHub Actions cron and external dispatchers
    routinely arrive a minute or two late, and every late run was silently
    dropped — a fault that stayed invisible only because the workflow was
    forcing every dispatch and bypassing this function entirely.

    Where bands overlap at a boundary minute, the tighter interval wins.
    """
    if force:
        return True

    local = now.astimezone(ZoneInfo(policy["timezone"]))
    bands = policy["schedule_bands"]["weekday" if local.weekday() < 5 else "weekend"]

    intervals: list[int] = []
    for band in bands:
        start_hour, start_minute = map(int, band["start"].split(":"))
        end_hour, end_minute = map(int, band["end"].split(":"))
        start = local.replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)
        end = local.replace(hour=end_hour, minute=end_minute, second=0, microsecond=0)
        if start <= local <= end:
            intervals.append(int(band["every_minutes"]))

    if not intervals:
        return False

    if not last_at:
        return True

    try:
        previous = datetime.fromisoformat(str(last_at).replace("Z", "+00:00"))
    except ValueError:
        return True

    # One minute of slack, so a dispatcher that fires a few seconds early is not
    # pushed into waiting a whole extra interval.
    elapsed_minutes = (now - previous).total_seconds() / 60
    return elapsed_minutes >= min(intervals) - 1


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


def load_last_public_selection() -> dict[str, Any]:
    if not PUBLIC_PATH.exists():
        return {"europe_now": [], "top_stories": []}
    try:
        with PUBLIC_PATH.open(encoding="utf-8") as handle:
            payload = json.load(handle)
        return {"europe_now": payload.get("europe_now", []), "top_stories": payload.get("top_stories", [])}
    except (OSError, json.JSONDecodeError):
        return {"europe_now": [], "top_stories": []}


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
        # One unavailable publisher must not hold the whole editorial cycle.
        with urlopen(request, timeout=12) as response:
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
        isinstance(item, dict)
        and isinstance(item.get("title"), str)
        and isinstance(item.get("summary"), str)
        and isinstance(item.get("article_ids"), list)
        and all(isinstance(article_id, str) for article_id in item["article_ids"])
        for item in value["top_stories"] + value["europe_now"]
    )


def safe_llm_error_detail(error: Exception, key: str) -> str:
    """Return a short diagnostic without exposing credentials."""
    detail = ""
    if isinstance(error, HTTPError):
        try:
            detail = error.read(2048).decode("utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
    if not detail:
        detail = str(error)
    if key:
        detail = detail.replace(key, "[REDACTED]")
    detail = re.sub(r"(?i)([?&]key=)[^&\s\"\\]+", r"\1[REDACTED]", detail)
    return " ".join(detail.split())[:400]


def log_llm_failure(provider: str, model: str, detail: str, status: int | None = None) -> None:
    status_text = f" status={status}" if status is not None else ""
    print(f"LLM attempt failed provider={provider} model={model}{status_text} detail={detail}", file=sys.stderr)


def load_json_response(response: Any) -> Any:
    """Parse JSON and salvage a complete payload missing only its final HTTP chunk."""
    try:
        return json.load(response)
    except IncompleteRead as error:
        if not error.partial:
            raise
        try:
            return json.loads(error.partial)
        except (TypeError, UnicodeDecodeError, json.JSONDecodeError):
            raise error


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
                endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
                body = {"contents": [{"parts": [{"text": raw}]}], "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1}}
                request = Request(endpoint, data=json.dumps(body).encode(),
                                  headers={"Content-Type": "application/json", "x-goog-api-key": key}, method="POST")
                with urlopen(request, timeout=30) as response:
                    result = load_json_response(response)
                text = result["candidates"][0]["content"]["parts"][0]["text"]
            else:
                body = {"model": model, "messages": [{"role": "user", "content": raw}], "temperature": 0.1,
                        "response_format": {"type": "json_object"}}
                request = Request("https://openrouter.ai/api/v1/chat/completions", data=json.dumps(body).encode(),
                                  headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "HTTP-Referer": "https://europepulse.eu"}, method="POST")
                with urlopen(request, timeout=30) as response:
                    result = load_json_response(response)
                text = result["choices"][0]["message"]["content"]
            if not isinstance(text, str) or not text.strip():
                log_llm_failure(provider, model, "empty-or-non-text response")
                continue
            selected = json.loads(text)
            if valid_selection(selected, policy["top_story_count"], policy["max_europe_now"]):
                return selected, f"{provider}:{model}"
            log_llm_failure(provider, model, "response failed schema validation")
        except HTTPError as error:
            log_llm_failure(provider, model, safe_llm_error_detail(error, key), error.code)
        except (URLError, TimeoutError, IncompleteRead, ValueError, KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
            log_llm_failure(provider, model, safe_llm_error_detail(error, key))
            continue
    return {"europe_now": [], "top_stories": []}, "failed"


def enrich_selection(selection: dict[str, Any], articles: list[dict[str, Any]]) -> dict[str, Any]:
    """Attach the sources behind each selected story.

    Two things travel with the link that did not before, both of them inside the
    sourcing policy, which allows the publisher's own title, the publication
    time and the canonical URL:

    * the publisher's headline and the time it was filed. Without them the page
      could only offer "France 24 English - Read the original", twice, under the
      same story, which tells a reader nothing about what either report said.
      The publisher's excerpt is still never carried: it is dropped in
      public_article() before anything reaches this function.
    * a source is only kept if its own headline names something our title or
      summary also names. An item whose every reference fails that test is
      dropped: an editorial summary with no attributable source is not
      publishable under the sourcing rule, and the caller already keeps the
      previous selection when a run yields nothing.
    * where the model named more articles than the two we show, the two are
      taken from different publishers when it offered any. It routinely returns
      two items from one newsroom, and a block headed "what the sources report"
      listing the same outlet twice shows less than the material allows.
    """
    by_id = {article["id"]: article for article in articles}
    names = corpus_names(str(article.get("title", "")) for article in articles)

    for group in ("europe_now", "top_stories"):
        valid: list[dict[str, Any]] = []
        for item in selection.get(group, []):
            refs = [by_id[key] for key in item.get("article_ids", []) if key in by_id]
            # The model's article_ids are not always about the story it wrote.
            # On 3 August 2026 it filed "Machthaber: Leo XIV." under Hungary's
            # nuclear shutdown and a Tour de France Femmes stage report under the
            # Moscow restaurant bombing, and both went to the homepage as "what
            # the sources report". An id is a claim; this checks it.
            subject = "{} {}".format(item.get("title", ""), item.get("summary", ""))
            refs = [ref for ref in refs if shares_a_name(subject, str(ref.get("title", "")), names)]
            chosen: list[dict[str, Any]] = []
            publishers: set[str] = set()
            for ref in refs:
                if len(chosen) == 2:
                    break
                if ref["source_id"] in publishers:
                    continue
                publishers.add(ref["source_id"])
                chosen.append(ref)
            # If the model only ever named one newsroom, a second link from that
            # same newsroom is still better than one link.
            for ref in refs:
                if len(chosen) == 2:
                    break
                if ref not in chosen:
                    chosen.append(ref)
            if chosen:
                item["sources"] = [{
                    "publisher": ref["source_name"],
                    "url": ref["url"],
                    "headline": ref["title"],
                    "published_at": ref["published_at"],
                } for ref in chosen]
                item["source_count"] = len({ref["source_id"] for ref in chosen})
                valid.append(item)
        selection[group] = valid
    return selection


def verified_sources(selection: dict[str, Any], articles: list[dict[str, Any]]) -> dict[str, Any]:
    """Check every source about to be published, not only freshly chosen ones.

    enrich_selection guards the model's own output, but two paths never reach
    it: a run with no new items, and a run whose model failed. Both republish
    the stored selection untouched, and on 3 August 2026 that copy still carried
    "Machthaber: Leo XIV." under Hungary's nuclear shutdown — the guard had
    shipped, the workflow had run, and the wrong attribution stayed on the
    homepage because that run had nothing new to select from.

    So the check moves to the last moment before publication, where everything
    passes through exactly once. A source with no headline is kept: it predates
    the field and there is nothing to check it against.
    """
    names = corpus_names(str(article.get("title", "")) for article in articles)

    for group in ("europe_now", "top_stories"):
        kept: list[dict[str, Any]] = []
        for item in selection.get(group, []):
            subject = "{} {}".format(item.get("title", ""), item.get("summary", ""))
            sources = [
                source for source in (item.get("sources") or [])
                if not source.get("headline")
                or shares_a_name(subject, str(source["headline"]), names)
            ]
            if not sources:
                continue
            item["sources"] = sources
            item["source_count"] = len({source.get("publisher", "") for source in sources})
            kept.append(item)
        selection[group] = kept

    return selection


def main() -> None:
    policy, source_config, now = read_yaml("policy.yml"), read_yaml("sources.yml"), utcnow()
    force = os.getenv("FORCE_RUN", "").lower() == "true"
    # State is loaded first because the cadence check now depends on when we
    # last actually collected, rather than on the clock alone.
    state = load_state()
    if not due_to_collect(now, policy, force, state.get("last_collection_at")):
        print("Outside the scheduled collection band; no action taken.")
        return
    # The repository is public: remove legacy excerpts created by earlier runs.
    state["radar"] = [public_article(item) for item in state.get("radar", [])]
    state.setdefault("last_selection", load_last_public_selection())
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
    # Think-tank material is useful context in the Radar, but not breaking-news input.
    candidates = [item for item in state["radar"] if "analysis" not in item.get("topics", [])][:policy["max_llm_items"]]
    if new_articles:
        selection, model = llm_selection(candidates, policy, state.get("recent_topics", []))
        selection = enrich_selection(selection, state["radar"])
        if selection["europe_now"] or selection["top_stories"]:
            state["last_selection"] = selection
            state["recent_topics"] = (state.get("recent_topics", []) + [{"at": iso(now), "title": item["title"]} for item in selection["top_stories"]])[-80:]
        else:
            # The model failed, timed out, or judged nothing worthy. Publishing
            # its empty result would blank Europe Now and Top stories on the
            # site — which is exactly what happened on 30 July 2026. Keep the
            # last good selection instead; it is stale, but it is not nothing.
            selection = state.get("last_selection", {"europe_now": [], "top_stories": []})
            model = f"{model}-kept-previous"
    else:
        selection, model = state.get("last_selection", {"europe_now": [], "top_stories": []}), "not-run-no-new-items"
    # Whatever route the selection arrived by, its sources are checked once here
    # before anything is written, and the repaired copy replaces the stored one
    # so a bad attribution cannot survive in state and come back on a quiet run.
    # If nothing survives we publish nothing rather than something wrong; the
    # homepage falls back to the strongest convergence cluster, which is drawn
    # from the radar and needs no model.
    selection = verified_sources(selection, state["radar"])
    if selection["europe_now"] or selection["top_stories"]:
        state["last_selection"] = selection
    # Independent of the model: what are several newsrooms converging on right
    # now? This is computed from the radar alone, so the hub always has
    # something to lead with even when the model contributes nothing.
    state["last_collection_at"] = iso(now)
    # Anything the model already put in the lead or the alert bar must not come
    # back a second time as a developing story.
    shown_urls = {
        str(source.get("url", ""))
        for group in ("europe_now", "top_stories")
        for item in selection.get(group, [])
        for source in item.get("sources", [])
        if source.get("url")
    }
    developing = developing_panel(state["radar"], now, exclude_urls=shown_urls)
    payload = {"generated_at": iso(now), "last_successful_collection_at": iso(now), "source_health": health,
               "europe_now": selection["europe_now"], "top_stories": selection["top_stories"],
               "developing": developing, "radar": state["radar"]}
    write_json(STATE_PATH, state)
    write_json(PUBLIC_PATH, payload)
    # The published file only ever holds the current briefing. This keeps the
    # record of what it held before, and writes nothing when a run repeats the
    # previous selection.
    archive.record(ROOT, now, iso(now), model, selection, developing)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with (LOG_DIR / f"{now:%Y-%m}.jsonl").open("a", encoding="utf-8") as log:
        log.write(json.dumps({"at": iso(now), "model": model, "input_count": len(candidates), "new_items": len(new_articles),
                              "top_story_count": len(selection["top_stories"]), "alert_count": len(selection["europe_now"]),
                              "developing_count": len(developing)}, ensure_ascii=False) + "\n")
    print(f"Collected {len(all_articles)} items; {len(new_articles)} new; model={model}; developing={len(developing)}")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Fatal collection error: {error}", file=sys.stderr)
        raise
