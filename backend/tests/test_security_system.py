"""Authentication, request guards, secrets, settings, backups and migrations."""
from __future__ import annotations

import json
import logging

import pytest
from fastapi.testclient import TestClient

from app import security as sec
from app.context import ctx
from app.logging_setup import redact


def test_host_endpoints_require_host(app_client):
    for method, path in [("get", "/api/status"), ("get", "/api/movies"), ("post", "/api/directories"),
                         ("get", "/api/settings"), ("post", "/api/scans"), ("get", "/api/browse"),
                         ("post", "/api/games"), ("get", "/api/database/backups"), ("post", "/api/system/restart")]:
        r = getattr(app_client, method)(path, **({"json": {}} if method == "post" else {}))
        assert r.status_code == 401, (path, r.status_code)
    assert app_client.get("/api/health").status_code == 200


def test_host_login_rules(app_client, monkeypatch):
    # loopback + no password: allowed
    assert app_client.post("/api/auth/host/login", json={}).status_code == 200
    ctx().secrets.set(sec.HOST_PASSWORD_KEY, sec.hash_password("hunter22"))
    c = TestClient(app_client.app)
    c.portal = app_client.portal
    assert c.post("/api/auth/host/login", json={}).status_code == 401
    assert c.post("/api/auth/host/login", json={"password": "wrong"}).status_code == 401
    assert c.post("/api/auth/host/login", json={"password": "hunter22"}).status_code == 200
    # launch token: one per start, only from this computer
    tok = sec.new_launch_token()
    c2 = TestClient(app_client.app)
    c2.portal = app_client.portal
    assert c2.post("/api/auth/host/login", json={"launch_token": "nope"}).status_code == 401
    assert c2.post("/api/auth/host/login", json={"launch_token": tok}).status_code == 200
    # a remote device without a host password set gets a clear message
    ctx().secrets.set(sec.HOST_PASSWORD_KEY, None)
    monkeypatch.setattr(sec, "is_loopback", lambda h: False)
    monkeypatch.setattr(ctx(), "lan_allowed", True)
    c3 = TestClient(app_client.app)
    c3.portal = app_client.portal
    r = c3.post("/api/auth/host/login", json={})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "host_password_not_set"


def test_lan_disabled_refuses_other_devices(app_client, monkeypatch):
    monkeypatch.setattr(sec, "is_loopback", lambda h: False)
    r = app_client.get("/api/health")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "lan_disabled"
    monkeypatch.setattr(ctx(), "lan_allowed", True)
    assert app_client.get("/api/health").status_code == 200


def test_request_guards(host):
    assert host.get("/api/health", headers={"host": "evil.example.com"}).status_code == 403  # DNS rebinding
    assert host.get("/api/health", headers={"host": "192.168.1.20:8765"}).status_code == 200
    r = host.post("/api/players", json={"name": "X"}, headers={"origin": "http://evil.example"})
    assert r.status_code == 403
    r = host.post("/api/players", content=b"name=X", headers={"content-type": "application/x-www-form-urlencoded"})
    assert r.status_code == 415


def test_no_arbitrary_file_access(host, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret")
    for path in ["/api/stream/../../secret.txt/index.m3u8", "/api/stream/aaaaaaaaaaaaaaaaaaaaaaaa/..%2F..%2Fx",
                 f"/api/images/w342/..%2F..%2F{secret.name}", "/api/images/w342/%2Fetc%2Fpasswd",
                 "/assets/..%2F..%2Fbackend%2Fapp%2Fconfig.py"]:
        r = host.get(path)
        assert r.status_code in (403, 404, 422), (path, r.status_code)
        assert "top secret" not in r.text
    # folder browser only lists folders, never files
    r = host.get("/api/browse", params={"path": str(tmp_path)})
    assert all(f["name"] != "secret.txt" for f in r.json()["folders"])
    assert host.get("/api/browse", params={"path": str(secret)}).status_code == 422


def test_file_allowed_checks_configured_dirs(host, tmp_path):
    from app import db

    inside = tmp_path / "Movies"
    inside.mkdir()
    (inside / "a.mkv").write_text("x")
    outside = tmp_path / "Other"
    outside.mkdir()
    (outside / "b.mkv").write_text("x")
    host.post("/api/directories", json={"path": str(inside)})
    with db.session_scope() as s:
        assert sec.file_allowed(s, str(inside / "a.mkv"))
        assert not sec.file_allowed(s, str(outside / "b.mkv"))
        assert not sec.file_allowed(s, str(inside / ".." / "Other" / "b.mkv"))


def test_api_key_never_exposed(host, fake_tmdb, caplog):
    caplog.set_level(logging.DEBUG)
    bad = host.post("/api/settings/tmdb/key", json={"key": "wrong-key-000000"})
    assert bad.status_code == 422  # validated before saving
    r = host.post("/api/settings/tmdb/key", json={"key": fake_tmdb.valid_key})
    assert r.status_code == 200
    settings = host.get("/api/settings").json()
    assert settings["tmdb_key"]["set"] and fake_tmdb.valid_key not in json.dumps(settings)
    assert fake_tmdb.valid_key not in caplog.text
    raw = ctx().cfg.secrets_file.read_text()
    assert "tmdb_credential" in raw
    assert host.post("/api/settings/tmdb/test").json()["ok"] is True
    assert host.delete("/api/settings/tmdb/key").json()["ok"]
    assert host.get("/api/settings").json()["tmdb_key"]["set"] is False


def test_log_redaction():
    assert "abc123" not in redact("GET /3/movie?api_key=abc123&x=1")
    assert "SECRET" not in redact("Authorization: Bearer SECRET")
    assert "pw1" not in redact('{"password": "pw1"}')


def test_settings_update_and_validation(host):
    r = host.patch("/api/settings/scoring", json={"correct": 20, "year_bonus": 4})
    assert r.status_code == 200 and r.json()["value"]["correct"] == 20
    r = host.patch("/api/settings/game", json={"timestamp_manual": False, "timestamp_random": False})
    assert r.status_code == 422
    r = host.patch("/api/settings/playback", json={"default_clip_seconds": 1})
    assert r.status_code == 422
    assert host.patch("/api/settings/nope", json={}).status_code == 404
    s = host.get("/api/settings").json()["sections"]
    assert s["scoring"]["correct"] == 20 and s["game"]["timestamp_manual"] is True
    r = host.patch("/api/settings/network", json={"lan_enabled": True})
    assert r.json()["restart_required"] is True
    net = host.get("/api/network").json()
    assert net["lan_enabled"] and not net["lan_active"] and net["restart_required"]


def test_host_password_change(host):
    assert host.post("/api/settings/host-password", json={"new_password": "abc"}).status_code == 422
    assert host.post("/api/settings/host-password", json={"new_password": "abcdef"}).json()["host_password_set"]
    assert host.get("/api/auth/me").json()["host_password_set"]


def test_backup_restore_integrity(host):
    host.post("/api/players", json={"name": "Before"})
    b = host.post("/api/database/backups").json()["name"]
    host.post("/api/players", json={"name": "After"})
    assert host.get("/api/database/integrity").json()["ok"]
    assert host.post("/api/database/restore", json={"name": "../../etc/passwd"}).status_code == 422
    r = host.post("/api/database/restore", json={"name": b})
    assert r.status_code == 200, r.text
    assert r.json()["safety_backup"].startswith("movie_roulette-")
    # sessions live in the database too: sign in again after a restore
    host.post("/api/auth/host/login", json={})
    names = [p["name"] for p in host.get("/api/players").json()]
    assert names == ["Before"]
    assert len(host.get("/api/database/backups").json()) == 2


def test_migrations_match_models(tmp_path):
    """The Alembic migrations build exactly the schema the models describe."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from sqlalchemy import create_engine

    from app import migrate
    from app.db import Base

    path = tmp_path / "m.db"
    migrate.upgrade(path)
    migrate.upgrade(path)  # idempotent
    eng = create_engine(f"sqlite:///{path}")
    with eng.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


@pytest.mark.parametrize("raw", ["", "relative/x", "/definitely/not/here", "C:\x00evil"])
def test_validate_directory_input(raw):
    with pytest.raises(ValueError):
        sec.validate_directory_input(raw)


def test_players_and_teams_crud(host):
    t = host.post("/api/teams", json={"name": "Owls", "max_size": 1}).json()
    p1 = host.post("/api/players", json={"name": "Zed", "team_id": t["id"]}).json()
    assert p1["team"] == "Owls"
    assert host.post("/api/players", json={"name": "zed"}).status_code == 409
    assert host.post("/api/players", json={"name": "Yan", "team_id": t["id"]}).status_code == 409  # team full
    assert host.post("/api/players", json={"name": "Bad", "color": "red"}).status_code == 422
    r = host.patch(f"/api/players/{p1['id']}", json={"name": "Zeb", "clear_team": True}).json()
    assert r["name"] == "Zeb" and r["team_id"] is None
    assert host.delete(f"/api/players/{p1['id']}").json()["archived"] is False
    assert host.delete(f"/api/teams/{t['id']}").status_code == 200
