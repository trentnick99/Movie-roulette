"""Locating and running FFmpeg / FFprobe, and reading media information."""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..config import PROJECT_DIR, get_config

log = logging.getLogger(__name__)

# Hide the console window that would otherwise flash up for every ffprobe call on Windows.
HIDE: dict[str, Any] = {"creationflags": 0x08000000} if os.name == "nt" else {}

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".wmv", ".webm", ".mpg", ".mpeg", ".ts", ".m2ts",
                    ".flv", ".ogv", ".3gp", ".divx", ".vob"}
# Seen in movie folders but never a playable movie on their own.
IGNORED_EXTENSIONS = {".srt", ".sub", ".idx", ".ass", ".ssa", ".nfo", ".txt", ".jpg", ".jpeg", ".png", ".webp",
                      ".url", ".exe", ".torrent", ".part", ".!qb", ".nzb", ".sfv", ".md5", ".db", ".ini", ".json",
                      ".xml", ".bif", ".ifo", ".bup"}


@lru_cache(maxsize=4)
def find_tool(name: str) -> str | None:
    cfg = get_config()
    explicit = cfg.ffmpeg_path if name == "ffmpeg" else cfg.ffprobe_path
    if explicit and Path(explicit).is_file():
        return explicit
    exe = name + (".exe" if os.name == "nt" else "")
    for local in (PROJECT_DIR / "tools" / "ffmpeg" / "bin" / exe, PROJECT_DIR / "tools" / exe):
        if local.is_file():
            return str(local)
    return shutil.which(name)


def tool_version(name: str) -> str | None:
    exe = find_tool(name)
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "-version"], capture_output=True, text=True, timeout=10, **HIDE)
        return (out.stdout.splitlines() or [""])[0].strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


@dataclass
class ProbeResult:
    ok: bool
    duration: float | None = None
    container: str | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    width: int | None = None
    height: int | None = None
    pix_fmt: str | None = None
    audio_streams: list[dict[str, Any]] = field(default_factory=list)
    chapters: list[dict[str, Any]] = field(default_factory=list)
    title_tag: str | None = None
    error: str | None = None


class ProbeUnavailable(RuntimeError):
    pass


def parse_probe_json(data: dict[str, Any]) -> ProbeResult:
    streams = data.get("streams") or []
    fmt = data.get("format") or {}
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not (s.get("disposition") or {}).get("attached_pic")), None)
    audios = [s for s in streams if s.get("codec_type") == "audio"]
    dur = None
    for cand in (fmt.get("duration"), (video or {}).get("duration")):
        try:
            d = float(cand)
            if d > 0:
                dur = d
                break
        except (TypeError, ValueError):
            continue
    audio_streams = [{
        "index": i,  # index among audio streams (ffmpeg 0:a:i)
        "codec": a.get("codec_name"),
        "channels": a.get("channels"),
        "language": ((a.get("tags") or {}).get("language") or "").lower() or None,
        "default": bool((a.get("disposition") or {}).get("default")),
        "title": (a.get("tags") or {}).get("title"),
    } for i, a in enumerate(audios)]
    chapters = []
    for c in data.get("chapters") or []:
        try:
            chapters.append({"start": float(c.get("start_time")), "end": float(c.get("end_time")),
                             "title": (c.get("tags") or {}).get("title") or ""})
        except (TypeError, ValueError):
            continue
    if video is None:
        return ProbeResult(ok=False, duration=dur, container=fmt.get("format_name"), error="no video stream",
                           audio_streams=audio_streams)
    if not dur:
        return ProbeResult(ok=False, container=fmt.get("format_name"), video_codec=video.get("codec_name"),
                           error="zero or unknown duration")
    return ProbeResult(
        ok=True, duration=dur, container=fmt.get("format_name"), video_codec=video.get("codec_name"),
        audio_codec=audios[0].get("codec_name") if audios else None, width=video.get("width"),
        height=video.get("height"), pix_fmt=video.get("pix_fmt"), audio_streams=audio_streams, chapters=chapters,
        title_tag=(fmt.get("tags") or {}).get("title"))


def probe(path: str, timeout: float = 60) -> ProbeResult:
    exe = find_tool("ffprobe")
    if not exe:
        raise ProbeUnavailable("FFprobe was not found. Install FFmpeg (see README) and restart Movie Roulette.")
    cmd = [exe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", "-show_chapters", path]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=timeout, **HIDE)
    except subprocess.TimeoutExpired:
        return ProbeResult(ok=False, error="ffprobe timed out (file may be damaged or on a slow drive)")
    except OSError as e:
        return ProbeResult(ok=False, error=f"ffprobe could not run: {e}")
    if out.returncode != 0:
        msg = out.stderr.decode("utf-8", "replace").strip().splitlines()
        return ProbeResult(ok=False, error=(msg[-1] if msg else f"ffprobe exit {out.returncode}")[:500])
    try:
        data = json.loads(out.stdout.decode("utf-8", "replace") or "{}")
    except ValueError:
        return ProbeResult(ok=False, error="ffprobe returned unreadable output")
    return parse_probe_json(data)


def pick_audio_stream(streams: list[dict[str, Any]], preferred_language: str | None) -> int | None:
    """Index (among audio streams) to use: preferred language, else the default track, else the first."""
    if not streams:
        return None
    pref = (preferred_language or "").lower()
    if pref:
        for s in streams:
            if (s.get("language") or "").startswith(pref[:2]) and "comment" not in (s.get("title") or "").lower():
                return int(s["index"])
    for s in streams:
        if s.get("default"):
            return int(s["index"])
    return int(streams[0]["index"])
