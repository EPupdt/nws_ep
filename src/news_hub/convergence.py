"""Deterministic 'what is the biggest story right now' from the radar alone.

The model-driven selection can return nothing — on a network error, a rate
limit, a malformed JSON reply, or simply because it judged nothing worthy. When
that happens the site currently has no top stories at all, even when several
newsrooms are visibly converging on the same event.

This module answers the question the radar can already answer without a model:
*how many independent publishers are covering the same thing right now?*

It deliberately does NOT write editorial prose. Inventing a summary without a
model would be exactly the failure mode the project forbids, and reusing a
publisher's headline as our own title would breach the sourcing policy. A
cluster is published as a neutral keyword label plus the publishers' own
headlines shown as attributed links — the same treatment the radar already uses
and the same treatment the licence terms allow.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, UTC
from typing import Any

# Words that carry no topical signal. Kept deliberately small: over-filtering
# destroys the overlap we rely on.
STOPWORDS = frozenset("""
about after against also among another any are around
back because been before being below best between both but
came can come could
did does doing done down during
each even every
few first for from
had has have her here hers him his how
into its itself
just
last late later least less like long
made make many may might more most much must
near need never new next not now
off often once only onto other our out over own
said same says see set she should since some still such
take than that the their them then there these they this those three through
too top toward two
under until upon used using
very
was way well were what when where which while who whom why will with within without would
year years yet you your
""".split())

# Short tokens that are still meaningful in a European news context.
KEEP_SHORT = frozenset({"eu", "uk", "us", "un", "war", "gas", "oil", "ecb", "nato", "law", "aid"})

_WORD = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9]+")


def _normalise(word: str) -> str:
    """Crude singularisation so 'plans'/'plan' and 'cups'/'cup' cluster together."""
    lower = word.lower()
    if len(lower) > 4 and lower.endswith("s") and not lower.endswith(("ss", "us", "is")):
        lower = lower[:-1]
    return lower


def _tokens(title: str) -> tuple[set[str], set[str]]:
    """Significant tokens from a headline, plus those that look like proper nouns.

    A word only counts as a proper noun if it is capitalised somewhere other
    than the first position, or is an acronym. Without that test, every headline
    donates its opening word — which is how "Thousands" ends up "linking"
    a Japanese earthquake to Spanish wildfires.
    """
    words = _WORD.findall(title)
    out: set[str] = set()
    proper: set[str] = set()

    for index, raw in enumerate(words):
        norm = _normalise(raw)
        if norm in STOPWORDS:
            continue
        is_acronym = raw.isupper() and len(raw) >= 2
        if not is_acronym and norm not in KEEP_SHORT and len(norm) < 4:
            continue
        out.add(norm)
        if is_acronym or (index > 0 and raw[:1].isupper()):
            proper.add(norm)

    return out, proper


def _label(cluster: list[dict[str, Any]]) -> str:
    """A neutral topic label built from the tokens the cluster shares.

    Not a headline and not editorial prose — a keyword fingerprint. Original
    capitalisation is preserved so proper nouns read correctly.
    """
    counts: dict[str, int] = {}
    display: dict[str, str] = {}

    for item in cluster:
        seen: set[str] = set()
        for raw in _WORD.findall(item["title"]):
            norm = _normalise(raw)
            if norm in STOPWORDS or norm in seen:
                continue
            if not (raw.isupper() and len(raw) >= 2) and norm not in KEEP_SHORT and len(norm) < 4:
                continue
            seen.add(norm)
            counts[norm] = counts.get(norm, 0) + 1
            # Prefer the most "proper-looking" rendering we have seen.
            current = display.get(norm)
            if current is None or (raw[:1].isupper() and not current[:1].isupper()) or raw.isupper():
                display[norm] = raw

    shared = [tok for tok, n in counts.items() if n >= 2]
    shared.sort(key=lambda t: (-counts[t], t))

    if not shared:
        shared = sorted(counts, key=lambda t: (-counts[t], t))

    return " · ".join(display[tok] for tok in shared[:4])


def convergence_clusters(
    radar: list[dict[str, Any]],
    now: datetime,
    window_hours: int = 12,
    min_overlap: int = 2,
    min_publishers: int = 2,
    limit: int = 4,
) -> list[dict[str, Any]]:
    """Group recent radar items that describe the same event.

    Returns clusters sorted by how many *distinct publishers* corroborate them,
    which is the signal we actually care about — five wire copies from one
    outlet mean far less than three independent newsrooms.
    """
    cutoff = now - timedelta(hours=window_hours)
    recent: list[dict[str, Any]] = []

    for item in radar:
        # Think-tank output is context, not breaking news; it must not lead.
        if "analysis" in (item.get("topics") or []):
            continue
        try:
            when = datetime.fromisoformat(str(item.get("published_at", "")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if when < cutoff:
            continue
        entry = dict(item)
        entry["_when"] = when
        entry["_tokens"], entry["_proper"] = _tokens(item.get("title", ""))
        if len(entry["_tokens"]) >= 3:
            recent.append(entry)

    recent.sort(key=lambda e: e["_when"], reverse=True)

    def same_story(a: dict[str, Any], b: dict[str, Any]) -> bool:
        """Two headlines are the same event only if they share two real words.

        An earlier version also merged on a single shared proper noun that was
        rare across the radar. It looked principled and it was wrong: on real
        data it chained "FIFA's boss and his friendship with Trump" to
        "Netanyahu praises meeting with Trump" — because *Trump* happened to
        appear in exactly two headlines. Politicians' names recur across
        unrelated stories, so rarity is not evidence of sameness.

        This module feeds the lead slot on the homepage. A missed pair costs
        nothing — the story still appears in the radar. A false merge publishes
        nonsense as the biggest story in Europe. Precision wins.
        """
        return len(a["_tokens"] & b["_tokens"]) >= min_overlap

    clusters: list[list[dict[str, Any]]] = []
    for entry in recent:
        placed = False
        for cluster in clusters:
            # Compare against the cluster's strongest member, not every member,
            # so a chain of weak links cannot drag unrelated stories together.
            if same_story(entry, cluster[0]):
                cluster.append(entry)
                placed = True
                break
        if not placed:
            clusters.append([entry])

    scored: list[dict[str, Any]] = []
    for cluster in clusters:
        publishers: list[str] = []
        for entry in cluster:
            name = entry.get("source_name") or entry.get("source_id") or ""
            if name and name not in publishers:
                publishers.append(name)
        if len(publishers) < min_publishers:
            continue

        # One link per publisher, newest first — no duplicate outlets in the list.
        sources: list[dict[str, Any]] = []
        used: set[str] = set()
        for entry in sorted(cluster, key=lambda e: e["_when"], reverse=True):
            name = entry.get("source_name") or ""
            if name in used:
                continue
            used.add(name)
            sources.append({
                "publisher": name,
                "url": entry.get("url", ""),
                "headline": entry.get("title", ""),
                "published_at": entry.get("published_at", ""),
            })

        scored.append({
            "label": _label(cluster),
            "origin": "convergence",
            "source_count": len(publishers),
            "item_count": len(cluster),
            "latest_at": max(e["_when"] for e in cluster).isoformat().replace("+00:00", "Z"),
            "sources": sources[:4],
        })

    scored.sort(key=lambda c: (-c["source_count"], -c["item_count"], c["latest_at"]), reverse=False)
    scored.sort(key=lambda c: (c["source_count"], c["item_count"], c["latest_at"]), reverse=True)
    return scored[:limit]
