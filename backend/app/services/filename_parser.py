"""Turn file and folder names into a probable movie title and release year.

The result is only a *search hint* for TMDB - never treated as the truth. Release-group tags, resolutions,
codecs, language labels and edition words are removed; the parent folder is used when the file name itself
says nothing useful ("movie.mkv", "CD1.avi", "VTS_01_1.vob").
Adapted from the HERO TV name tidier.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import PurePath

YEAR_MIN, YEAR_MAX = 1888, 2035

_RELEASE = re.compile(
    r"(?i)[ ._\-\[(]+(?:(?:240|360|480|540|576|720|1080|1440|2160|4320)[pi]|4k|8k|uhd|blu-?ray|bdrip|brrip|bdremux|"
    r"web-?dl|web-?rip|webrip|hdtv|dvd-?rip|dvdscr|dvd5|dvd9|hdrip|remux|x26[45]|h\.?26[45]|hevc|avc|xvid|divx|"
    r"aac[\d.]*|ac3|eac3|dts(?:-?hd)?|truehd|atmos|ddp?[\d.]*|10-?bit|8-?bit|hdr10\+?|hdr|dv|dolby[ ._]?vision|"
    r"sdr|yts(?:\.\w+)?|yify|rarbg|ettv|eztv|psa|galaxyrg|tigole|qxr|nf|amzn|dsnp|hmax|atvp|hulu|pcok|itunes|"
    r"proper|repack|internal|limited|hybrid|multi|dual|subbed|dubbed)(?![A-Za-z0-9]).*$")
_EDITION = re.compile(
    r"(?i)[ ._\-\[(]+(?:imax(?:[ ._]edition)?|remastered|extended(?:[ ._](?:edition|cut))?|unrated|uncut|"
    r"theatrical(?:[ ._]cut)?|director'?s?[ ._]?cut|final[ ._]?cut|special[ ._]?edition|collector'?s[ ._]edition|"
    r"anniversary[ ._]edition|criterion|open[ ._]matte|3d|half-?sbs|sbs)(?![A-Za-z])[\])]?")
_LANG_TAIL = re.compile(
    r"(?i)(?:[ ._\-\[(]+(?:eng|english|ita|italian|fre|french|ger|german|spa|spanish|latino|castellano|jpn|japanese|"
    r"kor|korean|hindi|tamil|telugu|rus|russian|chi|chinese|por|portuguese|pol|polish|esub|esubs|subs?)[\])]?)+\s*$")
_BRACKETS = re.compile(r"\[[^\]]*\]|\{[^}]*\}")
_SITE_PREFIX = re.compile(r"(?i)^\s*(?:www\.)?[a-z0-9-]+\.(?:com|net|org|io|in|me|tv|to|cc|info|xyz|site|club|ws)"
                          r"\s*[-–—|]+\s*")
_PART = re.compile(r"(?i)[ ._\-\[(]+(?:cd|disc|disk|dvd|part|pt)[ ._-]?([1-9])[\])]?\s*$")
_YEAR_ANY = re.compile(r"(?<![0-9])(19\d{2}|20[0-3]\d)(?![0-9])")
_GENERIC = re.compile(r"(?i)^(?:movie|film|video|feature|main|title[ _]?\d*|vts_\d+_\d+|cd\d|disc\d|part\d|"
                      r"sample|trailer|\d+)$")
_TRAILING_GROUP = re.compile(r"-[A-Za-z0-9]{2,15}$")


_EPISODE = re.compile(
    r"(?i)(?:\bS\d{1,2}[ ._-]?E\d{1,3}\b"      # S02E06, s1e3
    r"|(?<![0-9a-z])\d{1,2}x\d{2}(?![0-9])"       # 2x06
    r"|(?:^|[ ._\-\[(])ep(?:isode)?\.?[ ._-]?\d{1,4}(?![0-9]))")  # ep06, Ep.34, Ep 12, episode 05 (not 'Episode IV')


def looks_like_episode(path: str) -> str | None:
    """The episode marker when a file name looks like a TV episode, else None."""
    m = _EPISODE.search(PurePath(path.replace("\\", "/")).stem)
    return m.group(0).strip(" ._-[(") if m else None


# 'Copy of X', 'X - Copy', 'X - Copy (2)' (Windows Explorer copies)
_COPY = re.compile(r"(?i)^\s*copy\s+(?:\(\d+\)\s+)?of\s+|\s*-\s*copy(?:\s*\(\d+\))?\s*$")
# MakeMKV / disc-rip track names: 'Movie-FPL_MainFeature_t14', 'Movie_t00', 'title_t01'
_RIP = re.compile(r"(?i)[ ._-]*(?:(?:FPL_)?MainFeature(?:_t\d+)?|_t\d{2})\s*$")


@dataclass(frozen=True)
class ParsedName:
    title: str
    year: int | None
    part: int | None = None
    source: str = "file"  # file | folder


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def normalize_title(s: str) -> str:
    """Comparison key: lower case, no accents, '&' -> 'and', punctuation removed, single spaces."""
    s = _strip_accents(s).lower().replace("&", " and ")
    s = re.sub(r"['’`]", "", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    # 's w a t' (from 'S.W.A.T.') -> 'swat'
    return re.sub(r"\b(?:[a-z0-9] ){2,}[a-z0-9]\b", lambda m: m.group(0).replace(" ", ""), s)


def _tidy(stem: str) -> tuple[str, int | None, int | None]:
    t = _COPY.sub("", _SITE_PREFIX.sub("", stem.strip()))
    t = _RIP.sub("", t)
    part = None
    m = _PART.search(t)
    if m:
        part = int(m.group(1))
        t = t[: m.start()]
    t = _BRACKETS.sub(" ", t)
    dotted = " " not in t.strip() and bool(re.search(r"[._]", t))
    t = _EDITION.sub(" ", t)
    t = _RELEASE.sub("", t)
    if dotted or t.count(".") >= 2:
        t = re.sub(r"[._]+", " ", t)
    t = _TRAILING_GROUP.sub("", t) if dotted else t
    t = _LANG_TAIL.sub("", t)

    year = None
    # The release year is the LAST plausible year that is not the whole title ('2001 A Space Odyssey 1968',
    # 'Blade Runner 2049 (2017)').
    years = list(_YEAR_ANY.finditer(t))
    for ym in reversed(years):
        before = t[: ym.start()].strip(" ._-([")
        if before:
            y = int(ym.group(1))
            if YEAR_MIN <= y <= YEAR_MAX:
                year = y
                t = before
                break
    t = re.sub(r"\(\s*\)", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" ._-:([")
    return t, year, part


def parse_movie_path(path: str) -> ParsedName:
    p = PurePath(path.replace("\\", "/"))
    title, year, part = _tidy(p.stem)
    if not title or _GENERIC.match(title) or len(normalize_title(title)) < 1:
        parent = p.parent.name
        if parent:
            ptitle, pyear, _ = _tidy(parent)
            if ptitle and not _GENERIC.match(ptitle):
                return ParsedName(ptitle, year or pyear, part, "folder")
    if year is None and p.parent.name:  # 'The Thing (1982)/the.thing.mkv'
        ptitle, pyear, _ = _tidy(p.parent.name)
        if pyear and normalize_title(ptitle) == normalize_title(title):
            year = pyear
    return ParsedName(title or p.stem, year, part, "file")
