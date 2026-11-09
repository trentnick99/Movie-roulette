"""Unit tests: filename parsing, matching confidence, TMDB client behaviour, timestamps, selection fairness."""
from __future__ import annotations

import random

import httpx
import pytest

from app.services import timestamps as ts
from app.services.filename_parser import normalize_title, parse_movie_path
from app.services.matcher import find_matches, score_candidate, title_similarity
from app.services.settings import DifficultyPreset, GameSettings
from app.services.tmdb import TMDBClient, TMDBError, image_local_path
from tests.fake_tmdb import FakeTMDB


@pytest.mark.parametrize("path,title,year", [
    ("D:/Movies/Iron.Man.2008.1080p.BluRay.x264-GRP.mkv", "Iron Man", 2008),
    ("Blade Runner 2049 (2017).mp4", "Blade Runner 2049", 2017),
    ("2001 A Space Odyssey 1968.mkv", "2001 A Space Odyssey", 1968),
    ("2012.2009.720p.mkv", "2012", 2009),
    ("1917 (2019).mkv", "1917", 2019),
    ("[YTS.MX] Inception (2010) [1080p].mp4", "Inception", 2010),
    ("D:/Movies/Alien (1979)/movie.mkv", "Alien", 1979),
    ("D:/M/The Thing (1982)/the.thing.mkv", "the thing", 1982),
    ("Se7en.1995.REMASTERED.1080p.mkv", "Se7en", 1995),
    ("Spider-Man.Into.the.Spider-Verse.2018.mkv", "Spider-Man Into the Spider-Verse", 2018),
    ("www.Torrenting.com - Up (2009).mp4", "Up", 2009),
    ("Alien.mkv", "Alien", None),
    ("The.Lord.of.the.Rings.The.Fellowship.of.the.Ring.2001.EXTENDED.2160p.UHD.BluRay.x265-TERMiNAL.mkv",
     "The Lord of the Rings The Fellowship of the Ring", 2001),
])
def test_parse(path, title, year):
    p = parse_movie_path(path)
    assert (p.title, p.year) == (title, year)


def test_parse_parts_and_normalize():
    assert parse_movie_path("Star Wars (1977) CD2.avi").part == 2
    assert normalize_title("S.W.A.T.") == "swat"
    assert normalize_title("Amélie & Co.") == "amelie and co"


def test_title_similarity():
    assert title_similarity("The Thing", "Thing") == 1.0
    assert title_similarity("Iron Man", "Iron Man 2") > 0.8
    assert title_similarity("Alien", "Toy Story") < 0.4


def test_score_uses_year_and_runtime():
    r82 = {"id": 1, "title": "The Thing", "release_date": "1982-06-25", "popularity": 30}
    r11 = {"id": 2, "title": "The Thing", "release_date": "2011-10-12", "popularity": 25}
    a = score_candidate(r82, "The Thing", 1982, 109 * 60, 109)
    b = score_candidate(r11, "The Thing", 1982, 109 * 60, 103)
    assert a.score > b.score + 30
    # without a year the runtime decides
    a = score_candidate(r82, "The Thing", None, 103 * 60, 109)
    b = score_candidate(r11, "The Thing", None, 103 * 60, 103)
    assert b.score > a.score


def _client(fake: FakeTMDB, **kw) -> TMDBClient:
    store: dict = {}
    kw.setdefault("cache_get", lambda k, age: store.get(k))
    kw.setdefault("cache_put", lambda k, v: store.__setitem__(k, v))
    return TMDBClient(fake.valid_key, transport=fake.transport(), sleep=lambda s: None, **kw)


def test_find_matches_auto_accept_and_ambiguous():
    fake = FakeTMDB()
    c = _client(fake)
    res = find_matches(c, "Iron Man", 2008, 126 * 60)
    assert res.auto_accept and res.best.tmdb_id == 1726
    res = find_matches(c, "The Thing", None, 103 * 60)
    assert not res.auto_accept and {x.tmdb_id for x in res.candidates} >= {1091, 60935}
    res = find_matches(c, "Flying High", 1980, 88 * 60)  # alternative title
    assert res.best.tmdb_id == 813
    assert find_matches(c, "Completely Unknown Film", None, None).best is None


def test_tmdb_retries_and_errors():
    fake = FakeTMDB()
    c = _client(fake)
    fake.fail_next = [429, 500, 503]
    assert c.search_movie("Alien")[0]["id"] == 348  # retried through rate limit and server errors
    fake.fail_next = [500] * 10
    with pytest.raises(TMDBError) as e:
        c.get("/movie/348", use_cache=False)
    assert e.value.code == "server"
    fake.fail_next = []
    bad = TMDBClient("wrong-key-000000", transport=fake.transport(), sleep=lambda s: None)
    with pytest.raises(TMDBError) as e:
        bad.test_connection()
    assert e.value.code == "auth"
    with pytest.raises(TMDBError) as e:
        TMDBClient(None).get("/configuration")
    assert e.value.code == "no_key"

    def down(request):
        raise httpx.ConnectError("offline")

    off = TMDBClient("k" * 20, transport=httpx.MockTransport(down), sleep=lambda s: None, max_retries=1)
    with pytest.raises(TMDBError) as e:
        off.get("/configuration", use_cache=False)
    assert e.value.code == "network"


def test_tmdb_cache_and_bearer():
    fake = FakeTMDB(valid_key="eyJ" + "a" * 80)
    c = _client(fake)
    c.movie(348)
    c.movie(348)
    assert fake.calls["/movie/348"] == 1  # second call served from cache
    # the v4 token is sent as a header, never as a query parameter
    seen = {}

    def spy(request):
        seen["url"], seen["auth"] = str(request.url), request.headers.get("authorization")
        return fake.handler(request)

    TMDBClient(fake.valid_key, transport=httpx.MockTransport(spy), cache_get=None, cache_put=None).test_connection()
    assert "api_key" not in seen["url"] and seen["auth"].startswith("Bearer ")


def test_cache_key_excludes_credentials():
    keys = []
    fake = FakeTMDB()
    c = _client(fake, cache_put=lambda k, v: keys.append(k))
    c.search_movie("Alien", 1979)
    assert keys and all(fake.valid_key not in k for k in keys)


def test_image_path_validation(tmp_path):
    assert image_local_path(tmp_path, "w342", "/abc.jpg") == tmp_path / "w342" / "abc.jpg"
    for size, p in (("w342", "/../../secrets.json"), ("w999", "/a.jpg"), ("w342", "/a/b.jpg"), ("w342", "abc.jpg")):
        with pytest.raises(TMDBError):
            image_local_path(tmp_path, size, p)


# ------------------------------------------------------------------ timestamps

PRESET = DifficultyPreset(clip_seconds=30, replays=1, hints=1)


def test_window_never_exceeds_duration():
    w = ts.allowed_window(6000, 30, GameSettings(), PRESET)
    assert w.start == 0 and w.end == 6000 - 30 - ts.END_MARGIN
    assert ts.validate(w.end, w) == w.end
    with pytest.raises(ts.TimestampError):
        ts.validate(w.end + 1, w)
    with pytest.raises(ts.TimestampError):
        ts.validate(6001, w)
    with pytest.raises(ts.TimestampError):
        ts.validate(-1, w)
    rng = random.Random(1)
    for _ in range(500):
        t = ts.random_timestamp(w, rng)
        assert w.start <= t <= w.end and t + 30 <= 6000


def test_window_credits_and_chapters():
    g = GameSettings(exclude_opening=True, opening_seconds=120, exclude_ending=True, ending_seconds=600)
    w = ts.allowed_window(6000, 30, g, PRESET)
    assert w.start == 120 and w.end == 6000 - 600 - 30 - ts.END_MARGIN
    chapters = [{"start": 0, "end": 95, "title": "Opening Credits"}, {"start": 95, "end": 5500, "title": "Ch 2"},
                {"start": 5500, "end": 6000, "title": "End Credits"}]
    w = ts.allowed_window(6000, 30, g, PRESET, chapters)
    assert w.start == 95 and w.end == 5500 - 30 - ts.END_MARGIN
    # credits are not excluded unless enabled, even when chapters exist
    w = ts.allowed_window(6000, 30, GameSettings(), PRESET, chapters)
    assert w.start == 0


def test_window_edge_cases():
    with pytest.raises(ts.TimestampError):
        ts.allowed_window(0, 30, GameSettings(), PRESET)
    with pytest.raises(ts.TimestampError):
        ts.allowed_window(None, 30, GameSettings(), PRESET)
    w = ts.allowed_window(20, 30, GameSettings(), PRESET)  # clip longer than the file
    assert w.clip_seconds < 20 and w.end >= w.start
    # exclusions that do not fit fall back to the full file
    w = ts.allowed_window(300, 30, GameSettings(exclude_opening=True, opening_seconds=280), PRESET)
    assert w.start == 0 and w.end > 0
    # continuous mode keeps a minimum window
    w = ts.allowed_window(6000, None, GameSettings(), PRESET)
    assert w.end == 6000 - ts.CONTINUOUS_MIN_WINDOW - ts.END_MARGIN
    # difficulty range
    easy = DifficultyPreset(clip_seconds=45, replays=3, hints=3, range_start_pct=10, range_end_pct=90)
    w = ts.allowed_window(6000, 45, GameSettings(), easy)
    assert w.start == 600 and w.end == 5400


def test_parse_hms():
    assert ts.parse_hms("1:02:03") == 3723
    assert ts.parse_hms("62:03") == 3723
    assert ts.parse_hms("90") == 90
    assert ts.parse_hms("1h2m3s") == 3723
    for bad in ("", "1:99", "a:b", "1:2:3:4", "-5"):
        with pytest.raises(ts.TimestampError):
            ts.parse_hms(bad)
    assert ts.fmt(3723) == "1:02:03"


def test_settings_validation():
    with pytest.raises(ValueError):
        GameSettings(timestamp_manual=False, timestamp_random=False)
    with pytest.raises(ValueError):
        DifficultyPreset(clip_seconds=10, replays=0, hints=0, range_start_pct=50, range_end_pct=40)


@pytest.mark.parametrize("name,marker", [
    ("Invincible.S02E06.1080P.Hindi.English.mkv", "S02E06"),
    ("ep06 The Mexican Staring Frog of Southern Sri Lanka.mkv", "ep06"),
    ("Ep.34 - Piccolo vs Frost (1080p 60fps h264) [Slime Edit].mp4", "Ep.34"),
    ("Spider-Man The Animated Series S01E08 The Alien Costume (1).mkv", "S01E08"),
    ("Show 2x06.mkv", "2x06"),
    ("Star Wars Episode IV - A New Hope (1977).mkv", None),
    ("Sleepless.2017.1920x1080.mkv", None),
    ("Stepmom (1998).mkv", None),
    ("1917 (2019).mkv", None),
])
def test_episode_detection(name, marker):
    from app.services.filename_parser import looks_like_episode

    assert looks_like_episode(name) == marker


def test_scoring_real_library_cases():
    """Cases from a real library that should be accepted automatically."""
    def best(title, year, dur_min, runtime, popularity=10):
        r = {"id": 1, "title": title, "release_date": f"{year}-01-01", "popularity": popularity}
        return score_candidate(r, title_in, year_in, dur_min * 60, runtime)

    title_in, year_in = "Kick-Ass 2", None  # no year in the name, runtime matches
    assert best("Kick-Ass 2", 2013, 103, 103).score >= 85
    title_in, year_in = "Daredevil", 2003  # director's cut, 30 minutes longer than TMDB's runtime
    assert best("Daredevil", 2003, 133, 103).score >= 85
    title_in, year_in = "IronMan", 2008  # run-together name
    assert best("Iron Man", 2008, 126, 126).score >= 85
    # a much *shorter* file is still suspicious
    title_in, year_in = "Daredevil", 2003
    assert best("Daredevil", 2003, 60, 103).score < 85


def test_ironman_query_split():
    fake = FakeTMDB()
    res = find_matches(_client(fake), "IronMan", 2008, 126 * 60)
    assert res.auto_accept and res.best.tmdb_id == 1726


def test_image_download_concurrent(tmp_path):
    """Several screens asking for the same poster at once: one download, no errors, no leftover temp files."""
    import threading

    from app.services.tmdb import fetch_image

    calls = []

    def handler(request):
        calls.append(1)
        import time as _t

        _t.sleep(0.2)
        return httpx.Response(200, content=b"\xff\xd8jpeg-bytes")

    transport = httpx.MockTransport(handler)
    errors = []

    def go():
        try:
            fetch_image(tmp_path, "w342", "/abc.jpg", transport=transport, base="https://img.test/t/p")
        except Exception as e:  # pragma: no cover - would fail the test
            errors.append(e)

    threads = [threading.Thread(target=go) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and len(calls) == 1
    assert sorted(p.name for p in (tmp_path / "w342").iterdir()) == ["abc.jpg"]


@pytest.mark.parametrize("path,title,year", [
    ("Copy of [60FPS] The Dark Knight 2008 IMAX 1080p BluRay SDR [ENG + HIN] Esub.mkv", "The Dark Knight", 2008),
    ("The Matrix (1999) - Copy (2).mkv", "The Matrix", 1999),
    ("Spider-Man- Into the Spider-Verse-FPL_MainFeature_t14.mp4", "Spider-Man- Into the Spider-Verse", None),
    ("Heat_t00.mkv", "Heat", None),
    ("Copycat (1995).mkv", "Copycat", 1995),
])
def test_parse_copies_and_rips(path, title, year):
    p = parse_movie_path(path)
    assert (p.title, p.year) == (title, year)


def test_query_variants():
    from app.services.matcher import _queries

    assert "X-Men First Class" in _queries("5 X-Men 5 First Class")
    assert "X-Men Days of Future Past" in _queries("X-Men 7 Days of Future Past")
    assert _queries("Iron Man 3") == ["Iron Man 3"]
    assert "Thunderbolts" in _queries("Thunderboltsx")
    assert "Iron Man" in _queries("IronMan")
