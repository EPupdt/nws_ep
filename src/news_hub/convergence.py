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

# Words that are routinely capitalised without being the name of anything: they
# sit inside real names ("European Commission", "World Cup") and inside plain
# prose alike. They are still perfectly good topical tokens; they are just not
# evidence that two headlines describe the same event. Without this list,
# "European football vows to boycott World Cups if FIFA brings in private
# investment" and "Two 500-year-old mummies confirm Europeans brought smallpox
# to the Americas" merged on *European* and *World*.
# Institution words (Commission, Parliament, Court, Council) are deliberately
# NOT here: in EU coverage the institution often is the story, and it is
# frequently the only name two headlines share.
GENERIC_NAMES = frozenset("""
europe european west western east eastern north northern south southern
world global international national general new top big
president prime minister government
day week month year news update report live
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


# ---------------------------------------------------------------------------
# Vocabularies for the standalone tier of the panel.
#
# These lists exist because the per-item ``topics`` come from the source
# configuration, not from the article: every France 24 item is tagged
# politics/security/society whether it is a missile strike or a film review.
# Something has to tell those apart, and the only text we are allowed to hold
# is the headline. A hand-kept word list is crude, but it is auditable, it
# behaves identically on every run, and a reader can be shown exactly why an
# item was published. A model could judge this better, and a model is exactly
# what this module exists to survive the absence of.
# ---------------------------------------------------------------------------

# Countries, demonyms, capitals and the institutions of the Union. An item may
# only fill a slot on a European news site if it is about Europe, and on real
# radar data the single-source tail is full of Honduran politics, Ugandan
# court cases and Pakistani mining accidents that reach us only because our
# publishers are international.
EUROPE_TERMS = frozenset("""
eu uk europe european brussels strasbourg luxembourg schengen eurozone euro
nato ecb europol frontex commission parliament council mep meps
albania albanian andorra austria austrian belarus belarusian belgium belgian
bosnia bosnian bulgaria bulgarian croatia croatian cyprus cypriot czechia czech
denmark danish estonia estonian finland finnish france french georgia georgian
germany german greece greek hungary hungarian iceland icelandic ireland irish
italy italian kosovo latvia latvian liechtenstein lithuania lithuanian malta
maltese moldova moldovan monaco montenegro netherlands dutch macedonia norway
norwegian poland polish portugal portuguese romania romanian russia russian
serbia serbian slovakia slovak slovenia slovenian spain spanish sweden swedish
switzerland swiss turkey turkish ukraine ukrainian britain british england
english scotland scottish wales welsh kingdom
amsterdam athens ankara barcelona belgrade berlin bern bratislava bucharest
budapest copenhagen dublin helsinki kyiv kiev lisbon ljubljana london madrid
milan minsk moscow munich oslo paris prague riga rome sarajevo sofia stockholm
tallinn tirana vienna vilnius warsaw zagreb zurich hague ceuta catalonia bavaria
""".split())

# Something happened, as opposed to something being described. Measured on the
# radar of 1 August 2026 this cut ninety-five items to twenty, and everything it
# removed was a feature, an interview or a profile: "Nekonomics, or the great
# Japanese cat economy", "Madrid, a laboratory for evictions", "Eva Millet,
# writer: ...". Those are perfectly good journalism and they are not developing
# news.
EVENT_TERMS = frozenset("""
kill killed kills dead death deaths die dies died injure injured wound wounded
attack attacks attacked strike strikes struck blast bomb bombing explosion shelling
arrest arrested detain detained charge charged convict convicted sentence sentenced
jail jailed court ruling rules ruled verdict trial sue sued investigate investigation
resign resigns resigned quit quits fired sack sacked oust ousted dismiss dismissed
elect elected election vote votes voted referendum poll polls coalition
ban bans banned sanction sanctions sanctioned tariff tariffs embargo
approve approves approved reject rejects rejected veto vetoed adopt adopted
sign signs signed deal agreement ceasefire truce treaty summit talks negotiations
evacuate evacuates evacuated flee fled rescue rescued wildfire wildfires flood
floods earthquake storm crash crashed derail collapse collapses collapsed outage
blackout shortage recall protest protests walkout riot riots clash clashes unrest
launch launches launched deploy deployed troops military drone missile
warn warns warned threaten threatens threatened accuse accuses accused
raise raises raised cut cuts freeze frozen seize seizes seized suspend suspends
suspended announce announces announced propose proposes proposed
""".split())

# Publisher format markers. "Replay: US President Donald Trump addresses
# Cabinet meeting" is a video slot, not a story, and it reached the top of an
# early version of this ranking.
_FORMAT_PREFIX = re.compile(
    r"^\s*[\U0001F300-\U0001FAFF●•]*\s*"
    r"(replay|live|watch|video|podcast|photos|in pictures|listen|gallery|"
    r"as it happened|opinion|explainer|the debate|encore|revisited)\b\s*[:–—-]?",
    re.IGNORECASE,
)

# A headline that asks a question is explaining, not reporting.
_QUESTION_OPENER = re.compile(r"^\s*(how|why|what|who|when|where|which|is|are|was|were|do|does|did|can|should|will|would)\b", re.IGNORECASE)

_PLAIN_WORD = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]+")


def _plain_words(title: str) -> list[str]:
    return [word.lower() for word in _PLAIN_WORD.findall(title)]


def _shares_root(a: str, b: str) -> bool:
    """Same word family, judged on a common prefix of at least five letters.

    ``_normalise`` strips one trailing "s" and no more, so "Russia" and
    "Russian" are different tokens and "Russian attack on Ukrainian capital
    kills at least nine people" registered no connection at all to a radar full
    of Russia and Ukraine coverage. This is deliberately looser than the test
    used to merge stories: here a false match only nudges a ranking, it cannot
    join two articles into one.
    """
    if a == b:
        return True
    shortest = min(len(a), len(b))
    if shortest < 5:
        return False
    shared = 0
    while shared < shortest and a[shared] == b[shared]:
        shared += 1
    return shared >= 5


def _resonance(entry: dict[str, Any], pool: list[dict[str, Any]]) -> int:
    """How much of the rest of the day's news touches the same names.

    This is the closest deterministic stand-in we have for "on the pulse of the
    day". It counts other radar items whose headline contains a word from the
    same family as one of this item's proper nouns — so a story about Greek
    wildfires on a day full of Greek wildfires scores high, and a feature on
    the Japanese cat economy scores nothing.
    """
    if not entry["_proper"]:
        return 0
    return sum(
        1 for other in pool
        if other is not entry
        and any(_shares_root(name, token) for name in entry["_proper"] for token in other["_tokens"])
    )


def _is_developing_news(entry: dict[str, Any]) -> bool:
    """Would a European news desk call this a developing story?"""
    title = str(entry.get("title", ""))
    if _FORMAT_PREFIX.match(title) or _QUESTION_OPENER.match(title) or title.rstrip().endswith("?"):
        return False
    words = _plain_words(title)
    return any(word in EVENT_TERMS for word in words) and any(word in EUROPE_TERMS for word in words)


def _recent(radar: list[dict[str, Any]], now: datetime, window_hours: int) -> list[dict[str, Any]]:
    """The window of radar items every tier of the panel works from.

    Tokenising is done once here rather than in each caller, so the clustering
    and the standalone ranking below can never disagree about what a headline
    contains.
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

    # A single headline is too small a sample to tell a name from an opening
    # word. "Greece orders partial evacuation of Athens suburb" and "Greece's
    # wildfires worsen as strong winds spread blazes" both open with the only
    # name they share, so on its own neither can see that Greece is one.
    #
    # Deciding across the whole window fixes that, in two passes. A token is a
    # name if some headline capitalised it away from the start, or if every
    # headline that used it capitalised it at all — which is true of Greece and
    # false of Thousands, because somebody always writes "as thousands flee".
    mid_sentence: set[str] = set()
    capitalised: set[str] = set()
    lowercased: set[str] = set()

    for entry in recent:
        for index, raw in enumerate(_WORD.findall(str(entry.get("title", "")))):
            token = _normalise(raw)
            if raw[:1].isupper():
                capitalised.add(token)
                if index > 0:
                    mid_sentence.add(token)
            else:
                lowercased.add(token)

    corpus_proper = (mid_sentence | (capitalised - lowercased)) - GENERIC_NAMES
    for entry in recent:
        entry["_proper"] = entry["_tokens"] & corpus_proper

    return recent


def convergence_clusters(
    radar: list[dict[str, Any]],
    now: datetime,
    window_hours: int = 24,
    min_overlap: int = 2,
    min_publishers: int = 2,
    limit: int = 4,
) -> list[dict[str, Any]]:
    """Group recent radar items that describe the same event.

    Returns clusters sorted by how many *distinct publishers* corroborate them,
    which is the signal we actually care about — five wire copies from one
    outlet mean far less than three independent newsrooms.

    The window was twelve hours until 1 August 2026. Measured that morning at
    07:00, twelve hours reached back over a quiet European night and held only
    sixteen usable items, which produced exactly one corroborated cluster and a
    near-empty panel. Twenty-four hours over the same radar held ninety-six
    items and seven clusters. The bar for corroboration did not move; the
    window simply has to be long enough to contain a news cycle.
    """
    recent = _recent(radar, now, window_hours)

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

        Two shared words alone turned out not to be enough either. On 2 August
        2026 the panel published "Ugandan opposition leader Kizza Besigye rushed
        to hospital" and "Rakshya Bam, leader of the Nepali Gen Z movement" as
        one story corroborated by two publishers, on the strength of *leader*
        and *opposition*; and an El Nino warning with Spanish wildfire coverage
        on *fire* and *fuel*. Both pairs share only common nouns. So at least
        one of the shared words must be a name — a token this window has seen
        capitalised mid-headline. Ceuta/Spain, Moscow/blast, FIFA/Infantino and
        Greece/wildfire all still merge; the two false pairs do not.
        """
        shared = a["_tokens"] & b["_tokens"]
        if len(shared) < min_overlap:
            return False
        return bool(shared & (a["_proper"] | b["_proper"]))

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


def _cluster_urls(cluster: dict[str, Any]) -> set[str]:
    return {str(source.get("url", "")) for source in cluster.get("sources", []) if source.get("url")}


def developing_panel(
    radar: list[dict[str, Any]],
    now: datetime,
    exclude_urls: set[str] | None = None,
    window_hours: int = 24,
    limit: int = 4,
    min_fill_items: int = 2,
) -> list[dict[str, Any]]:
    """The Also developing panel: corroborated stories first, then the best of
    the rest, and never padding for the sake of a full box.

    Two publishers on the same event remains the bar for the top of the panel,
    and nothing here lowers it. But on a quiet morning the radar can hold fewer
    than four such stories while still holding real news, and a panel with one
    row in it reads as if the site had stopped working.

    So the panel is filled in three tiers, and a lower tier is only ever reached
    when the one above it has run out:

    1. two or more publishers on the same event — the bar, unchanged;
    2. one publisher that came back to the story, at least twice;
    3. one publisher, once, if the item passes the gates in
       ``_standalone_fills`` — an event rather than a description, and about
       Europe.

    Tier three exists because tiers one and two together are not enough. On the
    evening of 2 August 2026 the model had already taken the Greek wildfires and
    the Moscow bombing for the lead and the alert bar, ``exclude_urls`` removed
    them from here, and the panel published a single row while the Radar below
    it held a hundred and seventy items. A panel with one row in it reads as if
    the site had stopped working.

    When nothing clears the bar the panel is simply shorter. A short panel is
    honest; a padded one is not.

    ``exclude_urls`` drops clusters already visible elsewhere on the page. The
    lead and the alert bar are drawn from the model's selection, and a story
    shown there and repeated here reads as two stories.
    """
    exclude_urls = exclude_urls or set()

    chosen: list[dict[str, Any]] = [
        cluster for cluster in convergence_clusters(
            radar, now, window_hours=window_hours, min_publishers=2, limit=limit * 3,
        )
        if not (_cluster_urls(cluster) & exclude_urls)
    ][:limit]

    if len(chosen) >= limit:
        return chosen

    # Tier two: one publisher, but it came back to the story.
    # Same clustering, same window; only the publisher threshold differs, so the
    # single-publisher clusters are disjoint from the ones already chosen.
    repeated = [
        cluster for cluster in convergence_clusters(
            radar, now, window_hours=window_hours, min_publishers=1, limit=200,
        )
        if cluster["source_count"] == 1
        and cluster["item_count"] >= min_fill_items
        and not (_cluster_urls(cluster) & (exclude_urls | _taken_urls(chosen)))
        # Same bar as tier three: a slot filled for want of corroboration still
        # has to hold a European news story. Tier one is exempt because several
        # newsrooms agreeing on something is itself the evidence that European
        # readers are following it, wherever it happened.
        and _is_developing_news({"title": cluster["sources"][0]["headline"],
                                 "_tokens": _tokens(cluster["sources"][0]["headline"])[0],
                                 "_proper": set()})
    ]
    repeated.sort(key=lambda c: (c["item_count"], c["latest_at"]), reverse=True)
    for cluster in repeated:
        cluster["origin"] = "radar"
    chosen += repeated[: limit - len(chosen)]

    if len(chosen) >= limit:
        return chosen

    # Tier three: filed once, by one publisher, and still worth the slot.
    chosen += _standalone_fills(
        radar, now, window_hours,
        blocked_urls=exclude_urls | _taken_urls(chosen),
        blocked_tokens=_taken_tokens(chosen),
        wanted=limit - len(chosen),
    )
    return chosen[:limit]


def _taken_urls(clusters: list[dict[str, Any]]) -> set[str]:
    taken: set[str] = set()
    for cluster in clusters:
        taken |= _cluster_urls(cluster)
    return taken


def _taken_tokens(clusters: list[dict[str, Any]]) -> list[set[str]]:
    """Headline tokens of everything already in the panel.

    Blocking by URL alone is not enough: the same event reaches us as several
    different links, and a panel that carries one story four times is worse
    than a panel with one row in it.
    """
    taken: list[set[str]] = []
    for cluster in clusters:
        tokens: set[str] = set()
        for source in cluster.get("sources", []):
            tokens |= _tokens(str(source.get("headline", "")))[0]
        if tokens:
            taken.append(tokens)
    return taken


def _standalone_fills(
    radar: list[dict[str, Any]],
    now: datetime,
    window_hours: int,
    blocked_urls: set[str],
    blocked_tokens: list[set[str]],
    wanted: int,
    min_overlap: int = 2,
) -> list[dict[str, Any]]:
    """Single items that are strong enough to stand in the panel on their own.

    Three gates, all of which an item must pass, and then a ranking:

    * it reports an event rather than describing a subject (``EVENT_TERMS``),
    * it is about Europe (``EUROPE_TERMS``),
    * it is not already in the panel under a different link.

    What survives is ordered by resonance — how much of the rest of the day's
    radar touches the same names — so the free slots go to whatever Europe is
    actually talking about, and only then by recency.

    Measured against the radar of 1 August 2026, the three gates reduced
    ninety-five items to twelve, and all twelve were European news stories:
    Italy suspending Schengen, the Bordeaux wine region under fire, the strike
    on Kyiv, the Commission's AI hiring push, Poland's school phone ban. The
    same radar ranked by recency alone would have offered "'Nekonomics,' or the
    great Japanese cat economy" and "A Spider-Man for every person and
    generation" as developing European news.

    A missed story costs us nothing — it is still in the Radar below. A bad one
    is published as something Europe is following. So when nothing clears the
    gates the panel is simply shorter, which it is allowed to be.
    """
    if wanted <= 0:
        return []

    pool = _recent(radar, now, window_hours)
    candidates: list[tuple[int, dict[str, Any]]] = []

    for entry in pool:
        url = str(entry.get("url", ""))
        if not url or url in blocked_urls:
            continue
        if not _is_developing_news(entry):
            continue
        if any(len(entry["_tokens"] & tokens) >= min_overlap for tokens in blocked_tokens):
            continue
        candidates.append((_resonance(entry, pool), entry))

    candidates.sort(key=lambda pair: (pair[0], pair[1]["_when"]), reverse=True)

    fills: list[dict[str, Any]] = []
    used: list[set[str]] = list(blocked_tokens)

    for _, entry in candidates:
        if len(fills) >= wanted:
            break
        if any(len(entry["_tokens"] & tokens) >= min_overlap for tokens in used):
            continue
        used.append(entry["_tokens"])
        fills.append({
            "label": _label([entry]),
            "origin": "radar",
            "source_count": 1,
            "item_count": 1,
            "latest_at": entry["_when"].isoformat().replace("+00:00", "Z"),
            "sources": [{
                "publisher": entry.get("source_name") or entry.get("source_id") or "",
                "url": entry.get("url", ""),
                "headline": entry.get("title", ""),
                "published_at": entry.get("published_at", ""),
            }],
        })

    return fills
