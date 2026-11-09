"""End-to-end game flow over the HTTP API and WebSocket, with spoiler protection checks."""
from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.context import ctx
from tests.conftest import make_players


@pytest.fixture
def no_ffmpeg_stream(monkeypatch):
    """Pretend clips are prepared instantly (real FFmpeg encoding is covered in test_streaming)."""
    from app.services import streaming

    def start(self, token, src, start, length, **kw):
        job = streaming.StreamJob(token=token, folder=self.root / token, length=length,
                                  continuous=kw.get("continuous", False))
        job.folder.mkdir(parents=True, exist_ok=True)
        (job.folder / "index.m3u8").write_text("#EXTM3U\n#EXT-X-ENDLIST\n")
        (job.folder / "s00000.ts").write_bytes(b"\x47" * 188)
        job.status = "ready"
        self.jobs[token] = job
        self._notify(token, "ready")
        return job

    monkeypatch.setattr(streaming.StreamManager, "start", start)


def new_game(host, players, **over):
    body = {"name": "Friday", "mode": "library", "players": [{"player_id": p} for p in players],
            "difficulty": "medium", "playback_mode": "clip", "clip_seconds": 15, **over}
    r = host.post("/api/games", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def other_client(app_client) -> TestClient:
    """Another browser: own cookies, but the same event loop as the running app (like one uvicorn server)."""
    c = TestClient(app_client.app)
    c.portal = app_client.portal
    return c


def player_client(app_client, code, name) -> TestClient:
    c = other_client(app_client)
    r = c.post("/api/join", json={"code": code, "name": name})
    assert r.status_code == 200, r.text
    return c


SPOILERS = ("Iron Man", "Alien", "Airplane", "Toy Story", "The Thing", "1726", "poster", "backdrop", ".mkv", ".avi",
            ".m4v", ".mp4", "Movies", "Favreau", "Weaver")


def assert_no_spoilers(payload, title: str):
    text = json.dumps(payload)
    assert title not in text, f"title leaked: {title}"
    for s in ("tmdb_id", "movie_id", "file_name", "poster_path", "\"path\"", "overview"):
        assert s not in text, f"{s} leaked"


def test_full_game_night(library, host, app_client, no_ffmpeg_stream):
    ids = make_players(host)
    g = new_game(host, ids)
    assert g["status"] == "setup" and g["join_code"]
    ana = player_client(app_client, g["join_code"], "Ana")
    ben = player_client(app_client, g["join_code"], "ben")  # name matching is case-insensitive

    # round 1
    r = host.post(f"/api/games/{g['id']}/rounds", json={})
    assert r.status_code == 200, r.text
    state = r.json()
    rnd = state["round"]
    title = rnd["host"]["movie"]["title"]
    token = rnd["token"]
    assert rnd["status"] == "timestamp" and rnd["turn_player"] == "Ana"

    # players never see the answer, file name or ids before reveal
    pstate = ana.get("/api/play/state").json()
    assert_no_spoilers(pstate, title)
    assert pstate["round"]["can_pick_timestamp"] is True  # Ana's turn
    assert ben.get("/api/play/state").json()["round"]["can_pick_timestamp"] is False

    # Ben cannot choose the timestamp, Ana can - with random suggestion then accept
    assert ben.post(f"/api/rounds/{token}/timestamp/random").status_code == 403
    sug = ana.post(f"/api/rounds/{token}/timestamp/random").json()["round"]["suggested_seconds"]
    w = rnd["window"]
    assert w["min"] <= sug <= w["max"]
    r = ana.post(f"/api/rounds/{token}/timestamp", json={"seconds": sug, "source": "random"})
    assert r.status_code == 200, r.text
    assert_no_spoilers(r.json(), title)

    # players cannot control playback or reveal
    assert ana.post(f"/api/rounds/{token}/playback", json={"action": "play"}).status_code == 403
    assert ana.post(f"/api/rounds/{token}/reveal").status_code == 403
    r = host.post(f"/api/rounds/{token}/playback", json={"action": "play"})
    assert r.status_code == 200, r.text
    assert r.json()["round"]["playback"]["state"] == "playing"
    # stream: the player gets an opaque URL; files are served by round token only
    url = ana.get("/api/play/state").json()["round"]["playback"]["stream_url"]
    assert url == f"/api/stream/{token}/index.m3u8"
    assert ana.get(url).status_code == 200
    assert other_client(app_client).get(url).status_code == 403

    # guesses
    r = ben.post(f"/api/rounds/{token}/guesses", json={"text": "Some wrong film"})
    assert r.status_code == 200
    r = ana.post(f"/api/rounds/{token}/guesses", json={"text": title})
    guesses = host.get(f"/api/games/{g['id']}").json()["round"]["guesses"]
    # other players' guess texts stay hidden from players until the reveal
    ben_view = ben.get("/api/play/state").json()["round"]["guesses"]
    assert all("text" not in x for x in ben_view if x["player"] == "Ana")
    wrong = next(x for x in guesses if x["player"] == "Ben")
    right = next(x for x in guesses if x["player"] == "Ana")
    assert host.post(f"/api/rounds/{token}/guesses/{wrong['id']}/judge", json={"verdict": "incorrect"}).is_success
    r = host.post(f"/api/rounds/{token}/guesses/{right['id']}/judge", json={"verdict": "correct"})
    assert r.is_success
    # double click on 'correct' does not double score
    host.post(f"/api/rounds/{token}/guesses/{right['id']}/judge", json={"verdict": "correct"})
    board = host.get(f"/api/games/{g['id']}").json()["scoreboard"]["players"]
    ana_pts = next(p for p in board if p["name"] == "Ana")["points"]
    assert ana_pts == (10 + 5) * 1.5  # correct + first attempt, medium multiplier

    # bonus once only
    assert host.post(f"/api/rounds/{token}/bonus", json={"kind": "year", "player_id": ids[0]}).is_success
    assert host.post(f"/api/rounds/{token}/bonus", json={"kind": "year", "player_id": ids[0]}).status_code == 409

    # reveal
    r = host.post(f"/api/rounds/{token}/reveal")
    assert r.is_success
    revealed = ana.get("/api/play/state").json()["round"]
    assert revealed["answer"]["title"] == title and revealed["answer"]["start_seconds"] == sug
    assert host.post(f"/api/rounds/{token}/close").is_success
    # stream is gone once the round is closed
    assert ana.get(url).status_code == 404

    # round 2: Ben's turn, manual timestamp as text, a different movie
    r = host.post(f"/api/games/{g['id']}/rounds", json={}).json()
    rnd2 = r["round"]
    assert rnd2["turn_player"] == "Ben"
    assert rnd2["host"]["movie"]["title"] != title
    bad = ben.post(f"/api/rounds/{rnd2['token']}/timestamp", json={"seconds": 10 ** 6})
    assert bad.status_code == 422
    r = ben.post(f"/api/rounds/{rnd2['token']}/timestamp", json={"text": "0:20:00"})
    assert r.status_code == 200, r.text
    assert r.json()["round"]["start_seconds"] == 1200
    assert host.post(f"/api/rounds/{rnd2['token']}/correct", json={"player_id": ids[2]}).is_success  # spoken answer
    assert host.post(f"/api/rounds/{rnd2['token']}/close").is_success

    # manual adjustment is idempotent per request id
    rid = str(uuid.uuid4())
    for _ in range(2):
        host.post(f"/api/games/{g['id']}/score", json={"player_id": ids[1], "points": 2, "request_id": rid})
    final = host.post(f"/api/games/{g['id']}/finish").json()
    assert final["status"] == "finished" and final["join_code"] is None
    pts = {p["name"]: p["points"] for p in final["scoreboard"]["players"]}
    assert pts["Ben"] == 2 and pts["Cal"] == (10 + 5) * 1.5
    assert final["scoreboard"]["players"][0]["name"] == "Ana"

    # history & stats
    review = host.get(f"/api/games/{g['id']}/review").json()
    assert len(review["rounds_detail"]) == 2 and review["rounds_detail"][0]["movie"]["title"] == title
    stats = host.get("/api/stats").json()
    assert stats["totals"]["games_finished"] == 1 and stats["totals"]["correct"] == 2
    ana_stats = next(p for p in stats["players"] if p["name"] == "Ana")
    assert ana_stats["wins"] == 1


def test_websocket_sync_and_roles(library, host, app_client, no_ffmpeg_stream):
    ids = make_players(host, ("Dee", "Eve"))
    g = new_game(host, ids)
    dee = player_client(app_client, g["join_code"], "Dee")
    outsider = other_client(app_client)
    with pytest.raises(WebSocketDisconnect), outsider.websocket_connect(f"/ws/game/{g['id']}?role=player") as ws:
        ws.receive_text()
    # a player cannot open the host channel
    with pytest.raises(WebSocketDisconnect), dee.websocket_connect(f"/ws/game/{g['id']}?role=host") as ws:
        ws.receive_text()
    with dee.websocket_connect(f"/ws/game/{g['id']}?role=player") as pws, \
            host.websocket_connect(f"/ws/game/{g['id']}?role=host") as hws:
        first = json.loads(pws.receive_text())
        assert first["type"] == "state" and first["state"]["round"] is None
        host.post(f"/api/games/{g['id']}/rounds", json={})
        title = None
        for _ in range(6):
            msg = json.loads(hws.receive_text())
            if msg["state"]["round"]:
                title = msg["state"]["round"]["host"]["movie"]["title"]
                break
        assert title
        for _ in range(6):
            msg = json.loads(pws.receive_text())
            if msg["state"]["round"]:
                break
        assert msg["state"]["round"]["status"] == "timestamp"
        assert_no_spoilers(msg, title)
        pws.send_text(json.dumps({"type": "ping", "t": 1}))
        for _ in range(6):
            m = json.loads(pws.receive_text())
            if m["type"] == "pong":
                break
        assert m["type"] == "pong"
    # reconnection: a new socket immediately receives the current state
    with dee.websocket_connect(f"/ws/game/{g['id']}?role=player") as pws:
        msg = json.loads(pws.receive_text())
        assert msg["state"]["round"]["status"] == "timestamp"


def test_websocket_rejects_foreign_origin(library, host):
    ids = make_players(host, ("Fay",))
    g = new_game(host, ids)
    url = f"/ws/game/{g['id']}?role=host"
    with pytest.raises(WebSocketDisconnect), host.websocket_connect(url, headers={"origin": "http://evil.x"}) as ws:
        ws.receive_text()


def test_pool_exhausted_and_allow_repeat(library, host, no_ffmpeg_stream):
    ids = make_players(host, ("Gus",))
    # Iron Man franchise: two movies
    facets = host.get("/api/facets").json()
    coll = next(c for c in facets["collections"] if c["name"] == "Iron Man Collection")
    g = new_game(host, ids, mode="franchise", filters=[{"type": "collection", "id": coll["id"]}])
    seen = set()
    for _ in range(2):
        r = host.post(f"/api/games/{g['id']}/rounds", json={}).json()
        seen.add(r["round"]["host"]["movie"]["title"])
        host.post(f"/api/rounds/{r['round']['token']}/close?skip=true")
    assert seen == {"Iron Man", "Iron Man 2"}
    r = host.post(f"/api/games/{g['id']}/rounds", json={})
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["code"] == "pool_exhausted" and "allow_repeats" in detail["options"]
    r = host.post(f"/api/games/{g['id']}/rounds", json={"allow_repeat": True})
    assert r.status_code == 200 and r.json()["round"]["host"]["movie"]["title"] in seen


def test_mode_validation_and_custom_logic(library, host):
    ids = make_players(host, ("Hal",))
    r = host.post("/api/games", json={"mode": "decade", "players": [{"player_id": ids[0]}]})
    assert r.status_code == 422
    facets = host.get("/api/facets").json()
    horror = next(x for x in facets["genres"] if x["name"] == "Horror")
    pool = host.post("/api/games/pool-preview", json={
        "filters": [{"type": "genre", "id": horror["id"]}, {"type": "decade", "id": 1970}], "logic": "all"}).json()
    assert pool["eligible"] == 1  # Alien
    pool_any = host.post("/api/games/pool-preview", json={
        "filters": [{"type": "genre", "id": horror["id"]}, {"type": "decade", "id": 1970}], "logic": "any"}).json()
    assert pool_any["eligible"] == 2  # Alien, The Thing (1982); the unreviewed file is not eligible


def test_team_game_scoring(library, host, app_client, no_ffmpeg_stream):
    ids = make_players(host, ("Ivy", "Jon", "Kim", "Lou"))
    t1 = host.post("/api/teams", json={"name": "Reds"}).json()["id"]
    t2 = host.post("/api/teams", json={"name": "Blues"}).json()["id"]
    players = [{"player_id": ids[0], "team_id": t1}, {"player_id": ids[1], "team_id": t2},
               {"player_id": ids[2], "team_id": t1}, {"player_id": ids[3], "team_id": t2}]
    g = host.post("/api/games", json={"mode": "library", "team_mode": True, "players": players,
                                      "scoring": {"team_bonus": 2}}).json()
    r = host.post(f"/api/games/{g['id']}/rounds", json={}).json()["round"]
    assert r["turn_team_id"] == t1
    host.post(f"/api/rounds/{r['token']}/timestamp", json={"seconds": r["window"]["min"]})
    host.post(f"/api/rounds/{r['token']}/correct", json={"player_id": ids[2]})
    # a teammate cannot score the same round again
    assert host.post(f"/api/rounds/{r['token']}/correct", json={"player_id": ids[0]}).status_code == 409
    board = host.get(f"/api/games/{g['id']}").json()["scoreboard"]
    reds = next(t for t in board["teams"] if t["name"] == "Reds")
    assert reds["points"] == (10 + 5) * 1.5 + 2 and reds["rank"] == 1


def test_hints_replays_and_continuous(library, host, no_ffmpeg_stream):
    ids = make_players(host, ("Max",))
    g = new_game(host, ids, difficulty="easy", playback_mode="continuous", continuous_max_seconds=120)
    r = host.post(f"/api/games/{g['id']}/rounds", json={}).json()["round"]
    assert r["mode"] == "continuous" and r["clip_seconds"] is None
    tok = r["token"]
    host.post(f"/api/rounds/{tok}/timestamp", json={"seconds": r["window"]["min"]})
    st = host.post(f"/api/rounds/{tok}/playback", json={"action": "play"}).json()["round"]["playback"]
    assert st["length"] == 120
    hint = host.post(f"/api/rounds/{tok}/hint").json()["round"]["hints"]
    assert hint and hint[0].startswith("Released in the")
    # easy allows 3 hints
    host.post(f"/api/rounds/{tok}/hint")
    host.post(f"/api/rounds/{tok}/hint")
    assert host.post(f"/api/rounds/{tok}/hint").status_code == 409
    # correct answer stops continuous playback; hint penalty applied (3 hints x 2)
    host.post(f"/api/rounds/{tok}/correct", json={"player_id": ids[0]})
    v = host.get(f"/api/games/{g['id']}").json()
    assert v["round"]["playback"]["state"] == "ended"
    assert v["scoreboard"]["players"][0]["points"] == (10 + 5) * 1 - 6


def test_replay_limit(library, host, no_ffmpeg_stream):
    ids = make_players(host, ("Ned",))
    g = new_game(host, ids, difficulty="hard")
    r = host.post(f"/api/games/{g['id']}/rounds", json={}).json()["round"]
    tok = r["token"]
    host.post(f"/api/rounds/{tok}/timestamp", json={"seconds": r["window"]["min"]})
    assert host.post(f"/api/rounds/{tok}/playback", json={"action": "play"}).is_success
    assert host.post(f"/api/rounds/{tok}/playback", json={"action": "replay"}).status_code == 409  # hard: 0
    assert host.post(f"/api/rounds/{tok}/playback", json={"action": "replay", "force": True}).is_success


def test_join_rules(library, host, app_client):
    ids = make_players(host, ("Oli",))
    g = new_game(host, ids, allow_walk_ins=False)
    c = other_client(app_client)
    assert c.post("/api/join", json={"code": "ZZZZZZ", "name": "x"}).status_code == 404
    assert c.post("/api/join", json={"code": g["join_code"], "name": "Stranger"}).status_code == 403
    assert c.post("/api/join", json={"code": g["join_code"].lower(), "name": "oli"}).status_code == 200
    # a player session does not grant host access
    assert c.get("/api/status").status_code == 401
    assert c.get(f"/api/games/{g['id']}").status_code == 401


def test_stream_needs_ready_media(library, host, monkeypatch):
    """Without a prepared clip, play is refused with a clear message (no fake success)."""
    from app.services import streaming

    def boom(self, *a, **k):
        raise streaming.StreamError("FFmpeg was not found.")

    monkeypatch.setattr(streaming.StreamManager, "start", boom)
    ids = make_players(host, ("Pam",))
    g = new_game(host, ids)
    r = host.post(f"/api/games/{g['id']}/rounds", json={}).json()["round"]
    v = host.post(f"/api/rounds/{r['token']}/timestamp", json={"seconds": r["window"]["min"]}).json()
    assert v["round"]["playback"]["media_status"] == "error"
    res = host.post(f"/api/rounds/{r['token']}/playback", json={"action": "play"})
    assert res.status_code == 409 and "FFmpeg" in res.json()["detail"]["message"]
    assert ctx().streams.get(r["token"]) is None
