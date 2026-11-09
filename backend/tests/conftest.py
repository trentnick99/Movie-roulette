from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config as config_mod  # noqa: E402
from app import context as context_mod  # noqa: E402
from app.services import media_tools  # noqa: E402
from app.services.media_tools import ProbeResult  # noqa: E402
from app.services.tmdb import TMDBClient  # noqa: E402
from tests.fake_tmdb import FakeTMDB  # noqa: E402

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setenv("MR_DATA_DIR", str(data))
    monkeypatch.delenv("MR_TMDB_API_KEY", raising=False)
    monkeypatch.setenv("MR_FRONTEND_DIST", str(tmp_path / "no-frontend"))
    config_mod.get_config.cache_clear()
    context_mod.reset_ctx()
    from app.api import auth

    auth._attempts.clear()
    yield data
    from app import db
    from app.services.scanner import runner

    runner.stop()
    runner.wait(10)
    db.dispose()
    config_mod.get_config.cache_clear()
    context_mod.reset_ctx()


@pytest.fixture
def fake_tmdb(monkeypatch):
    fake = FakeTMDB()

    def factory(self, db):
        cred = self.tmdb_credential()
        if not cred:
            return None
        return TMDBClient(cred, transport=fake.transport(), sleep=lambda _s: None)

    monkeypatch.setattr(context_mod.AppContext, "tmdb_client", factory)
    real_init = TMDBClient.__init__

    def init(self, credential, **kw):  # the key-validation endpoint builds its own client
        kw.setdefault("transport", fake.transport())
        kw.setdefault("sleep", lambda _s: None)
        real_init(self, credential, **kw)

    monkeypatch.setattr(TMDBClient, "__init__", init)
    return fake


def write_fake_movie(path: Path, duration: float = 6000.0, *, corrupt: bool = False, content: str | None = None,
                     chapters: list | None = None) -> Path:
    """Fake 'video' file whose probe result is encoded in the file itself (used with fake_probe)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"duration": duration, "corrupt": corrupt, "chapters": chapters or [], "id": content or str(path)}
    path.write_text(json.dumps(payload))
    return path


def fake_probe_fn(path: str) -> ProbeResult:
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return ProbeResult(ok=False, error="Invalid data found when processing input")
    if data.get("corrupt"):
        return ProbeResult(ok=False, error="moov atom not found")
    return ProbeResult(ok=True, duration=float(data["duration"]), container="matroska,webm", video_codec="hevc",
                       audio_codec="ac3", width=1920, height=1080, pix_fmt="yuv420p10le",
                       audio_streams=[{"index": 0, "codec": "ac3", "channels": 6, "language": "eng",
                                       "default": True, "title": None}], chapters=data.get("chapters") or [])


@pytest.fixture
def fake_probe(monkeypatch):
    from app.services import scanner

    monkeypatch.setattr(scanner, "probe", fake_probe_fn)
    return fake_probe_fn


@pytest.fixture
def app_client():
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c


@pytest.fixture
def host(app_client):
    r = app_client.post("/api/auth/host/login", json={})
    assert r.status_code == 200, r.text
    return app_client


@pytest.fixture
def tmdb_key(host, fake_tmdb):
    r = host.post("/api/settings/tmdb/key", json={"key": fake_tmdb.valid_key})
    assert r.status_code == 200, r.text
    return fake_tmdb


def scan_and_wait(client, mode: str = "scan") -> dict:
    from app.services.scanner import runner

    r = client.post("/api/scans", json={"mode": mode})
    assert r.status_code == 200, r.text
    runner.wait(30)
    cur = client.get("/api/scans/current").json()
    assert cur["scan"]["status"] == "completed", cur
    return cur["scan"]


@pytest.fixture
def library(host, tmdb_key, fake_probe, tmp_path):
    """A scanned library of 7 matched movies (+1 ambiguous)."""
    root = tmp_path / "Movies"
    files = {
        "Iron.Man.2008.1080p.BluRay.x264-GRP.mkv": 126 * 60,
        "Iron Man 2 (2010)/Iron Man 2 (2010).mkv": 124 * 60,
        "Alien (1979).mkv": 117 * 60,
        "Aliens.1986.REMASTERED.mkv": 137 * 60,
        "The Thing (1982).mp4": 109 * 60,
        "Airplane! (1980).avi": 88 * 60,
        "Toy Story (1995).m4v": 81 * 60,
        "The Thing.mkv": 103 * 60,  # no year -> ambiguous between 1982 and 2011
    }
    for rel, dur in files.items():
        write_fake_movie(root / rel, dur)
    r = host.post("/api/directories", json={"path": str(root)})
    assert r.status_code == 200, r.text
    scan_and_wait(host)
    return root


def make_players(client, names=("Ana", "Ben", "Cal")) -> list[int]:
    ids = []
    for n in names:
        r = client.post("/api/players", json={"name": n})
        assert r.status_code == 200, r.text
        ids.append(r.json()["id"])
    return ids


@pytest.fixture
def ffmpeg_available():
    if not HAS_FFMPEG:
        pytest.skip("FFmpeg not installed")
    media_tools.find_tool.cache_clear()
    return True


os.environ.setdefault("PYTHONHASHSEED", "0")
