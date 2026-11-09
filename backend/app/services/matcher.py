"""Scores TMDB search results against what we know about a file.

Signals: title similarity (title, original title, alternative titles), release year, and the file's measured
duration compared with TMDB's runtime. The confidence is 0-100. A match is only accepted automatically when it
clears the configured threshold *and* is clearly ahead of the runner-up; everything else waits for the host.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from .filename_parser import normalize_title
from .tmdb import TMDBClient, TMDBError

_ARTICLE = re.compile(r"^(?:the|a|an|le|la|les|el|los|las|der|die|das|il) ")


def _variants(s: str) -> set[str]:
    n = normalize_title(s)
    out = {n}
    out.add(_ARTICLE.sub("", n))
    return {v for v in out if v}


def title_similarity(a: str, b: str) -> float:
    """0..1 - best of character similarity and word-set overlap, with leading articles ignored."""
    best = 0.0
    for x in _variants(a):
        for y in _variants(b):
            if x == y:
                return 1.0
            if x.replace(" ", "") == y.replace(" ", ""):  # 'ironman' / 'iron man', 'antman' / 'ant man'
                best = max(best, 0.98)
                continue
            ratio = SequenceMatcher(None, x, y).ratio()
            tx, ty = set(x.split()), set(y.split())
            jacc = len(tx & ty) / len(tx | ty) if tx and ty else 0.0
            # 'star wars episode iv a new hope' vs 'star wars': every word of one inside the other
            contain = 0.0
            if tx and ty and (tx <= ty or ty <= tx):
                contain = 0.55 + 0.4 * (min(len(tx), len(ty)) / max(len(tx), len(ty)))
            best = max(best, ratio, jacc, contain)
    return best


def _year_of(release_date: str | None) -> int | None:
    if release_date and len(release_date) >= 4 and release_date[:4].isdigit():
        return int(release_date[:4])
    return None


@dataclass
class ScoredCandidate:
    tmdb_id: int
    title: str
    original_title: str | None
    release_year: int | None
    overview: str | None
    poster_path: str | None
    popularity: float | None
    score: float  # 0-100, shown to the host
    rank: float = 0.0  # uncapped score, used for ordering and the lead over the runner-up
    signals: dict[str, Any] = field(default_factory=dict)


def score_candidate(result: dict, parsed_title: str, parsed_year: int | None, duration_s: float | None,
                    runtime_min: int | None = None, alt_titles: list[str] | None = None) -> ScoredCandidate:
    titles = [result.get("title") or "", result.get("original_title") or ""] + list(alt_titles or [])
    tsim = max((title_similarity(parsed_title, t) for t in titles if t), default=0.0)
    year = _year_of(result.get("release_date"))
    if parsed_year is None:
        ys = 12.0
    elif year is None:
        ys = 6.0
    else:
        d = abs(year - parsed_year)
        ys = 25.0 if d == 0 else 17.0 if d == 1 else -15.0 if d > 3 else 0.0
    rs = 4.0
    rdiff = None
    if duration_s and runtime_min:
        delta = duration_s / 60.0 - runtime_min
        rdiff = abs(delta)
        if rdiff <= 3:
            rs = 15.0
        elif rdiff <= 8:
            rs = 9.0
        elif rdiff <= 15:
            rs = 3.0
        elif rdiff <= 25 or 0 < delta <= 45:  # longer than TMDB: extended / director's / unrated cuts are common
            rs = 0.0
        else:
            rs = -10.0
    pop = float(result.get("popularity") or 0)
    ps = min(3.0, math.log10(pop + 1))
    raw = tsim * 62 + ys + rs + ps
    total = max(0.0, min(100.0, raw))
    return ScoredCandidate(
        tmdb_id=int(result["id"]), title=result.get("title") or "", original_title=result.get("original_title"),
        release_year=year, overview=result.get("overview"), poster_path=result.get("poster_path"),
        popularity=pop, score=round(total, 1), rank=raw,
        signals={"title_similarity": round(tsim, 3), "year": ys, "runtime": rs, "runtime_diff_min":
                 None if rdiff is None else round(rdiff, 1), "popularity": round(ps, 2)})


def _queries(title: str) -> list[str]:
    qs = [title]
    split = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", title)  # 'IronMan' -> 'Iron Man'
    if split != title:
        qs.append(split)
    words = title.split()
    if len(words) > 2:
        # series numbering inside a name: '5 X-Men 5 First Class', 'X-Men 7 Days of Future Past' (a trailing
        # number is kept: 'Iron Man 3')
        inner = [w for i, w in enumerate(words) if not (re.fullmatch(r"\d{1,2}", w) and i < len(words) - 1)]
        if inner != words:
            qs.append(" ".join(inner))
    if words and re.fullmatch(r"[A-Za-z]{6,}x", words[-1]):
        qs.append(" ".join(words[:-1] + [words[-1][:-1]]))  # 'Thunderboltsx' -> 'Thunderbolts' ('*' in names)
    for sep in (" - ", ": ", " – "):
        if sep in title:
            head, tail = title.split(sep, 1)
            qs += [head.strip(), tail.strip()]
    m = re.match(r"(?i)^(.*?)\s+(?:episode|part|chapter)\s+[ivx\d]+\b", title)
    if m:
        qs.append(m.group(1))
    out: list[str] = []
    for q in qs:
        if q and len(q) >= 1 and q.lower() not in (x.lower() for x in out):
            out.append(q)
    return out[:6]


@dataclass
class MatchResult:
    candidates: list[ScoredCandidate]
    best: ScoredCandidate | None
    auto_accept: bool
    reason: str


def find_matches(client: TMDBClient, parsed_title: str, parsed_year: int | None, duration_s: float | None, *,
                 threshold: float = 85, margin: float = 8, detail_top: int = 3) -> MatchResult:
    results: dict[int, dict] = {}
    for q in _queries(parsed_title):
        for year in ([parsed_year, None] if parsed_year else [None]):
            for r in client.search_movie(q, year):
                if r.get("id") is not None:
                    results.setdefault(int(r["id"]), r)
            if results and year:
                break
        if len(results) >= 5:
            break
    if not results:
        return MatchResult([], None, False, "no TMDB results")
    scored = [score_candidate(r, parsed_title, parsed_year, duration_s) for r in results.values()]
    scored.sort(key=lambda c: c.rank, reverse=True)
    # Refine the leaders with details (runtime, alternative titles): cached, so cheap on rescans.
    refined = []
    for c in scored[:detail_top]:
        try:
            d = client.movie(c.tmdb_id)
            alts = [t.get("title") for t in ((d.get("alternative_titles") or {}).get("titles") or [])
                    if t.get("title")]
            refined.append(score_candidate(results[c.tmdb_id], parsed_title, parsed_year, duration_s,
                                           d.get("runtime"), alts[:30]))
        except TMDBError:
            refined.append(c)
    scored = sorted(refined + scored[detail_top:], key=lambda c: c.rank, reverse=True)[:8]
    best = scored[0]
    second = scored[1].rank if len(scored) > 1 else 0.0
    ok = best.score >= threshold and (best.rank - second) >= margin
    reason = ("confident match" if ok else
              "below confidence threshold" if best.score < threshold else "several close candidates")
    return MatchResult(scored, best, ok, reason)
