"""Real FFmpeg tests: probing, frame-accurate clip encoding from MKV/HEVC/AC-3 and AVI sources, metadata
stripping, and the full scan -> play pipeline. Skipped when FFmpeg is not installed."""
from __future__ import annotations

import subprocess
import time

import pytest

from app.services import media_tools
from app.services.streaming import StreamManager
from tests.conftest import make_players, scan_and_wait


def make_video(path, seconds=90, vcodec="libx265", acodec="ac3", fmt=None, title="Secret Movie Title"):
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size=320x240:rate=24:duration={seconds}",
           "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-c:v", vcodec, "-c:a", acodec,
           "-metadata", f"title={title}"]
    if vcodec == "libx265":
        cmd += ["-x265-params", "log-level=error"]
    if fmt:
        cmd += ["-f", fmt]
    subprocess.run(cmd + [str(path)], check=True)
    return path


def test_probe_real_files(ffmpeg_available, tmp_path):
    mkv = make_video(tmp_path / "a.mkv", 20)
    r = media_tools.probe(str(mkv))
    assert r.ok and abs(r.duration - 20) < 0.5 and r.video_codec == "hevc" and r.audio_codec == "ac3"
    bad = tmp_path / "bad.mkv"
    bad.write_bytes(b"\x00" * 1000)
    assert not media_tools.probe(str(bad)).ok


@pytest.mark.parametrize("vcodec,acodec,ext", [("libx265", "ac3", "mkv"), ("mpeg4", "mp3", "avi")])
def test_clip_is_accurate_and_clean(ffmpeg_available, tmp_path, vcodec, acodec, ext):
    src = make_video(tmp_path / f"src.{ext}", 60, vcodec, acodec)
    mgr = StreamManager(tmp_path / "streams")
    token = "t" * 24
    t0 = time.time()
    mgr.start(token, str(src), 21.5, 6, continuous=False, audio_index=0, max_height=720, preset="ultrafast")
    assert mgr.wait_ready(token, 60) == "ready"
    assert time.time() - t0 < 30
    playlist = (tmp_path / "streams" / token / "index.m3u8").read_text()
    assert "#EXT-X-ENDLIST" in playlist and "src" not in playlist
    # probe a segment directly (some static FFmpeg builds crash when ffprobe reads an HLS playlist)
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
                          str(tmp_path / "streams" / token / "s00000.ts")],
                         capture_output=True, text=True, check=True).stdout
    assert "Secret Movie Title" not in out
    assert '"h264"' in out and '"aac"' in out
    # the first decoded frame is the frame at 21.5 s of the source (testsrc2 frames are unique per time)
    a = subprocess.run(["ffmpeg", "-v", "error", "-i", str(tmp_path / "streams" / token / "s00000.ts"),
                        "-frames:v", "1", "-vf", "scale=64:48", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                       capture_output=True, check=True).stdout
    frames = {}
    for t in (21.5, 21.0, 22.0):
        frames[t] = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(src), "-frames:v", "1",
                                    "-vf", "scale=64:48", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                                   capture_output=True, check=True).stdout

    def diff(x, y):
        return sum(abs(p - q) for p, q in zip(x, y, strict=False)) / len(x)

    assert diff(a, frames[21.5]) < diff(a, frames[21.0]) and diff(a, frames[21.5]) < diff(a, frames[22.0])
    mgr.stop(token, remove=True)
    assert not (tmp_path / "streams" / token).exists()


def test_continuous_ready_early_and_stop(ffmpeg_available, tmp_path):
    src = make_video(tmp_path / "long.mkv", 120, "libx264", "aac")
    mgr = StreamManager(tmp_path / "streams")
    token = "c" * 24
    mgr.start(token, str(src), 5, 100, continuous=True, audio_index=0, max_height=480, preset="ultrafast")
    assert mgr.wait_ready(token, 60) == "ready"
    mgr.stop(token, remove=True)
    assert mgr.get(token) is None


def test_missing_file_reports_error(ffmpeg_available, tmp_path):
    mgr = StreamManager(tmp_path / "streams")
    token = "e" * 24
    mgr.start(token, str(tmp_path / "nope.mkv"), 0, 5, continuous=False, audio_index=None, max_height=480,
              preset="ultrafast")
    assert mgr.wait_ready(token, 30) == "error"
    assert "FFmpeg" in mgr.get(token).error


def test_end_to_end_real_media(ffmpeg_available, host, tmdb_key, tmp_path, app_client):
    """Scan real files with FFprobe, match through (mocked) TMDB, play a real clip through the HTTP stream."""
    root = tmp_path / "Movies"
    # long enough to pass the default 20-minute 'sample' filter: lower the limit instead of encoding 20 minutes
    # The test clips are 1 minute long, so TMDB's runtime disagrees; accept on title + year for this test.
    host.patch("/api/settings/library", json={"min_duration_minutes": 0.5, "auto_accept_confidence": 70})
    make_video(root / "Alien (1979).mkv", 60)
    make_video(root / "Airplane! (1980).avi", 60, "mpeg4", "mp3")
    host.post("/api/directories", json={"path": str(root)})
    scan = scan_and_wait(host)
    assert scan["counts"]["added"] == 2
    items = host.get("/api/movies").json()["items"]
    assert {m["title"] for m in items} == {"Alien", "Airplane!"}
    assert {m["metadata_status"] for m in items} == {"matched"}
    assert all(abs(m["duration"] - 60) < 1 for m in items)
    host.patch("/api/settings/playback", json={"encoder_preset": "ultrafast", "max_resolution": 480})
    pid = make_players(host, ("Real",))[0]
    g = host.post("/api/games", json={"mode": "library", "players": [{"player_id": pid}], "clip_seconds": 5}).json()
    r = host.post(f"/api/games/{g['id']}/rounds", json={})
    assert r.status_code == 200, (g, r.text)
    rnd = r.json()["round"]
    assert rnd["window"]["max"] <= 60 - 5
    host.post(f"/api/rounds/{rnd['token']}/timestamp", json={"seconds": 10})
    from app.context import ctx

    assert ctx().streams.wait_ready(rnd["token"], 60) == "ready"
    for _ in range(50):
        state = host.get(f"/api/games/{g['id']}").json()["round"]["playback"]
        if state["media_status"] == "ready":
            break
        time.sleep(0.1)
    assert state["media_status"] == "ready" and state["stream_url"]
    playlist = host.get(state["stream_url"])
    assert playlist.status_code == 200 and "s00000.ts" in playlist.text
    seg = host.get(state["stream_url"].replace("index.m3u8", "s00000.ts"), headers={"Range": "bytes=0-187"})
    assert seg.status_code == 206 and len(seg.content) == 188
    assert host.post(f"/api/rounds/{rnd['token']}/playback", json={"action": "play"}).status_code == 200
