"""Timestamp rules: the window a clip may start in, validation and random suggestions.

Always based on the file's measured duration (ffprobe), never TMDB's runtime. Opening/ending credits are only
excluded when the host enables it; chapter markers are used when they clearly name the credits, otherwise the
configured number of seconds is used.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from typing import Any

from .settings import DifficultyPreset, GameSettings

CONTINUOUS_MIN_WINDOW = 30.0  # a continuous round needs at least this much movie after the start
END_MARGIN = 0.5

_OPENING = re.compile(r"(?i)\b(opening|intro|main title|title sequence|opening credits|prologue credits)\b")
_ENDING = re.compile(r"(?i)\b(end credits|closing credits|credits|end titles|closing)\b")


class TimestampError(ValueError):
    pass


@dataclass
class Window:
    start: float
    end: float  # latest allowed *start* time
    clip_seconds: float | None  # None = continuous
    duration: float
    notes: list[str] = field(default_factory=list)

    def contains(self, t: float) -> bool:
        return self.start - 1e-6 <= t <= self.end + 1e-6

    def as_dict(self) -> dict[str, Any]:
        return {"min": round(self.start, 2), "max": round(self.end, 2), "clip_seconds": self.clip_seconds,
                "notes": self.notes}


def chapter_bounds(chapters: list[dict[str, Any]], duration: float) -> tuple[float | None, float | None]:
    """(end of opening credits, start of end credits) when chapter titles clearly say so."""
    open_end = end_start = None
    for c in chapters or []:
        title = str(c.get("title") or "")
        try:
            s, e = float(c["start"]), float(c["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if _OPENING.search(title) and s <= duration * 0.15:
            open_end = max(open_end or 0.0, e)
        elif _ENDING.search(title) and s >= duration * 0.75:
            end_start = s if end_start is None else min(end_start, s)
    return open_end, end_start


def allowed_window(duration: float | None, clip_seconds: float | None, game: GameSettings,
                   preset: DifficultyPreset, chapters: list[dict[str, Any]] | None = None) -> Window:
    if not duration or duration <= 0:
        raise TimestampError("This movie file has no measurable length (zero-length or damaged file).")
    notes: list[str] = []
    span = clip_seconds if clip_seconds else CONTINUOUS_MIN_WINDOW
    if span >= duration:
        span = max(1.0, duration - END_MARGIN)
        notes.append("Clip shortened to fit the movie.")
        if clip_seconds:
            clip_seconds = span

    open_ch, end_ch = chapter_bounds(chapters or [], duration) if game.use_chapters else (None, None)
    lo = max(0.0, game.min_timestamp_seconds)
    end_limit = duration
    if game.exclude_opening:
        lo = max(lo, open_ch if open_ch is not None else game.opening_seconds)
        notes.append("Opening credits excluded" + (" (chapter markers)." if open_ch is not None else "."))
    if game.exclude_ending:
        end_limit = min(end_limit, end_ch if end_ch is not None else duration - game.ending_seconds)
        notes.append("End credits excluded" + (" (chapter markers)." if end_ch is not None else "."))
    hi = end_limit - span - END_MARGIN
    if game.max_timestamp_seconds is not None:
        hi = min(hi, game.max_timestamp_seconds)

    plo = max(lo, duration * preset.range_start_pct / 100.0)
    phi = min(hi, duration * preset.range_end_pct / 100.0)
    if phi >= plo:
        lo, hi = plo, phi
    else:
        notes.append("Difficulty range ignored for this movie (too short).")
    if hi < lo:
        # Exclusions do not fit this movie: fall back to the whole file rather than refusing the round.
        lo, hi = 0.0, max(0.0, duration - span - END_MARGIN)
        notes.append("Credit/limit exclusions do not fit this movie; using its full length.")
    return Window(start=round(lo, 2), end=round(max(lo, hi), 2), clip_seconds=clip_seconds, duration=duration,
                  notes=notes)


def validate(t: float, window: Window) -> float:
    try:
        t = float(t)
    except (TypeError, ValueError) as e:
        raise TimestampError("Timestamp must be a number of seconds.") from e
    if t != t or t < 0:
        raise TimestampError("Timestamp must be zero or more.")
    if t > window.duration:
        raise TimestampError("That timestamp is past the end of the movie.")
    if not window.contains(t):
        raise TimestampError(f"Choose a time between {fmt(window.start)} and {fmt(window.end)}.")
    return round(t, 2)


def random_timestamp(window: Window, rng: random.Random | None = None) -> float:
    rng = rng or random.SystemRandom()
    lo, hi = int(window.start + 0.999), int(window.end)
    if hi < lo:
        return round(window.start, 2)
    return float(rng.randint(lo, hi))


def fmt(seconds: float) -> str:
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}"


def parse_hms(text: str) -> float:
    """'1:02:03', '62:03', '3723', '1h2m3s' -> seconds."""
    t = (text or "").strip().lower()
    if not t:
        raise TimestampError("Enter a time.")
    m = re.fullmatch(r"(?:(\d+)h)?\s*(?:(\d+)m)?\s*(?:(\d+(?:\.\d+)?)s)?", t)
    if m and any(m.groups()):
        h, mi, s = m.groups()
        return int(h or 0) * 3600 + int(mi or 0) * 60 + float(s or 0)
    parts = t.split(":")
    try:
        nums = [float(p) for p in parts]
    except ValueError as e:
        raise TimestampError("Use hours:minutes:seconds, for example 1:05:30.") from e
    if len(nums) > 3 or any(n < 0 for n in nums) or any(n >= 60 for n in nums[1:]):
        raise TimestampError("Use hours:minutes:seconds, for example 1:05:30.")
    total = 0.0
    for n in nums:
        total = total * 60 + n
    return total
