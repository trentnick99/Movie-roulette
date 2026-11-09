"""Round video streaming.

Browsers cannot be relied on to play MKV/AVI/WMV, HEVC, 10-bit H.264, AC-3/DTS sound and so on. So for every
round FFmpeg encodes *only the part of the movie the round needs* - from the chosen timestamp, for the clip
length (or up to the continuous-mode cap) - into a short-segment HLS stream (H.264 + AAC) in a temporary folder:

- Accurate start: input seeking with re-encoding is frame-accurate, unlike stream copy (keyframes only).
- Universal playback: H.264/AAC HLS plays in Safari natively and in Chrome/Edge/Firefox/TV browsers via hls.js.
- No full transcodes: a 30 s clip encodes in a few seconds; continuous mode encodes ahead faster than real time.
- One encode per round, shared by every connected device (segments are plain files served with Range support).
- Spoiler-safe: all container metadata, chapters and subtitles are stripped; files and URLs carry only the
  random round token, never a file name or movie id.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .media_tools import HIDE, find_tool

log = logging.getLogger(__name__)

PLAYLIST = "index.m3u8"
SEGMENT_RE = re.compile(r"^(?:index\.m3u8|s\d{5}\.ts)$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
SEGMENT_SECONDS = 2
CONTINUOUS_READY_SEGMENTS = 3


class StreamError(RuntimeError):
    pass


@dataclass
class StreamJob:
    token: str
    folder: Path
    length: float
    continuous: bool
    proc: subprocess.Popen | None = None
    status: str = "preparing"  # preparing | ready | error | stopped
    error: str | None = None
    started: float = field(default_factory=time.time)
    finished: bool = False
    stderr_tail: list[str] = field(default_factory=list)


def build_command(ffmpeg: str, src: str, out_dir: Path, start: float, length: float, *,
                  audio_index: int | None, max_height: int = 1080, preset: str = "veryfast") -> list[str]:
    vf = (f"scale='trunc(ih*dar/2)*2':'trunc(ih/2)*2',setsar=1,"
          f"scale=-2:'min({int(max_height)},ih)':flags=bicubic,format=yuv420p")
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-y",
           "-ss", f"{max(0.0, start):.3f}", "-i", src, "-t", f"{max(0.5, length):.3f}",
           "-map", "0:v:0"]
    if audio_index is not None:
        cmd += ["-map", f"0:a:{int(audio_index)}?"]
    cmd += ["-vf", vf, "-c:v", "libx264", "-preset", preset, "-crf", "21", "-profile:v", "high",
            "-force_key_frames", f"expr:gte(t,n_forced*{SEGMENT_SECONDS})", "-sc_threshold", "0",
            "-c:a", "aac", "-b:a", "160k", "-ac", "2", "-ar", "48000",
            "-map_metadata", "-1", "-map_chapters", "-1", "-sn", "-dn",
            "-metadata", "title=", "-metadata:s", "title=", "-metadata:s", "language=und",
            "-f", "hls", "-hls_time", str(SEGMENT_SECONDS), "-hls_list_size", "0",
            "-hls_playlist_type", "event", "-hls_flags", "independent_segments",
            "-hls_segment_filename", str(out_dir / "s%05d.ts"), str(out_dir / PLAYLIST)]
    return cmd


def _segments(folder: Path) -> int:
    try:
        return sum(1 for p in folder.iterdir() if p.suffix == ".ts")
    except OSError:
        return 0


class StreamManager:
    def __init__(self, root: Path, on_change: Callable[[str, str], None] | None = None):
        self.root = root
        self.jobs: dict[str, StreamJob] = {}
        self.lock = threading.Lock()
        self.on_change = on_change

    def cleanup_all(self) -> None:
        """Remove streams left over from a previous run."""
        if self.root.is_dir():
            for p in self.root.iterdir():
                if p.is_dir() and TOKEN_RE.match(p.name):
                    shutil.rmtree(p, ignore_errors=True)

    def _notify(self, token: str, status: str) -> None:
        if self.on_change:
            try:
                self.on_change(token, status)
            except Exception:
                log.exception("stream change callback failed")

    def start(self, token: str, src: str, start: float, length: float, *, continuous: bool,
              audio_index: int | None, max_height: int, preset: str) -> StreamJob:
        if not TOKEN_RE.match(token):
            raise StreamError("bad stream token")
        ffmpeg = find_tool("ffmpeg")
        if not ffmpeg:
            raise StreamError("FFmpeg was not found. Install FFmpeg (see README) and restart Movie Roulette.")
        self.stop(token)
        folder = self.root / token
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True, exist_ok=True)
        job = StreamJob(token=token, folder=folder, length=length, continuous=continuous)
        cmd = build_command(ffmpeg, src, folder, start, length, audio_index=audio_index, max_height=max_height,
                            preset=preset)
        try:
            job.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.PIPE, **HIDE)
        except OSError as e:
            raise StreamError(f"Could not start FFmpeg: {e}") from e
        with self.lock:
            self.jobs[token] = job
        threading.Thread(target=self._watch, args=(job,), daemon=True, name=f"ffmpeg-{token[:6]}").start()
        threading.Thread(target=self._drain, args=(job,), daemon=True).start()
        return job

    def _drain(self, job: StreamJob) -> None:
        assert job.proc is not None and job.proc.stderr is not None
        for raw in job.proc.stderr:
            line = raw.decode("utf-8", "replace").strip()
            if line:
                job.stderr_tail = (job.stderr_tail + [line])[-15:]

    def _watch(self, job: StreamJob) -> None:
        proc = job.proc
        assert proc is not None
        while proc.poll() is None:
            if job.status == "preparing" and job.continuous and _segments(job.folder) >= CONTINUOUS_READY_SEGMENTS:
                job.status = "ready"
                self._notify(job.token, "ready")
            time.sleep(0.25)
        job.finished = True
        if job.status == "stopped":
            return
        if proc.returncode == 0 and (job.folder / PLAYLIST).is_file() and _segments(job.folder) > 0:
            if job.status != "ready":
                job.status = "ready"
                self._notify(job.token, "ready")
        else:
            job.status = "error"
            tail = " | ".join(job.stderr_tail[-3:])
            job.error = (f"FFmpeg could not prepare this clip (exit {proc.returncode}). {tail}").strip()[:600]
            log.warning("stream %s failed: %s", job.token[:6], job.error)
            self._notify(job.token, "error")

    def get(self, token: str) -> StreamJob | None:
        with self.lock:
            return self.jobs.get(token)

    def stop(self, token: str, remove: bool = False) -> None:
        with self.lock:
            job = self.jobs.pop(token, None) if remove else self.jobs.get(token)
        if job is not None and job.proc is not None and job.proc.poll() is None:
            job.status = "stopped"
            job.proc.terminate()
            try:
                job.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                job.proc.kill()
        if remove:
            shutil.rmtree(self.root / token, ignore_errors=True)

    def stop_all(self) -> None:
        for token in list(self.jobs):
            self.stop(token, remove=True)

    def file_path(self, token: str, name: str) -> Path | None:
        if not TOKEN_RE.match(token) or not SEGMENT_RE.match(name):
            return None
        p = self.root / token / name
        return p if p.is_file() else None

    def wait_ready(self, token: str, timeout: float = 60) -> str:
        end = time.time() + timeout
        while time.time() < end:
            job = self.get(token)
            if job is None:
                return "missing"
            if job.status in ("ready", "error"):
                return job.status
            time.sleep(0.1)
        return "timeout"


def ffmpeg_available() -> bool:
    return bool(find_tool("ffmpeg")) and bool(find_tool("ffprobe"))


def disk_usage(root: Path) -> int:
    total = 0
    for dirpath, _d, files in os.walk(root):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, f))
            except OSError:
                pass
    return total
