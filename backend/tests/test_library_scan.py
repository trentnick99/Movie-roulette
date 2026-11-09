"""Directory scanning, change detection, matching and manual correction."""
from __future__ import annotations

import os

from app import db
from app.models import Movie, MovieFile
from tests.conftest import scan_and_wait, write_fake_movie


def movies_by_title(client):
    items = client.get("/api/movies", params={"page_size": 500}).json()["items"]
    return {(m["title"], m["release_year"]): m for m in items}


def test_scan_matches_and_flags_ambiguous(library, host):
    movies = movies_by_title(host)
    assert ("Iron Man", 2008) in movies
    assert ("Iron Man 2", 2010) in movies
    assert ("Alien", 1979) in movies and ("Aliens", 1986) in movies
    assert ("Airplane!", 1980) in movies
    assert movies[("Iron Man", 2008)]["metadata_status"] == "matched"
    # 'The Thing.mkv' (103 min, no year) is ambiguous: it must wait for the host, never be auto-picked
    review = host.get("/api/movies", params={"status": "needs_review"}).json()["items"]
    assert [m["parsed_title"] for m in review] == ["The Thing"]
    detail = host.get(f"/api/movies/{review[0]['id']}").json()
    assert {c["tmdb_id"] for c in detail["candidates"]} >= {1091, 60935}
    # runtime is a supporting signal: the 103-minute file scores the 2011 film higher
    best = max(detail["candidates"], key=lambda c: c["score"])
    assert best["tmdb_id"] == 60935


def test_metadata_relationships(library, host):
    iron = movies_by_title(host)[("Iron Man", 2008)]
    d = host.get(f"/api/movies/{iron['id']}").json()
    assert d["tmdb_id"] == 1726
    assert [x["name"] for x in d["directors"]] == ["Jon Favreau"]
    assert d["cast"][0]["name"] == "Robert Downey Jr."
    assert d["collection"]["name"] == "Iron Man Collection"
    assert {g["name"] for g in d["genres"]} == {"Action", "Science Fiction", "Adventure"}
    assert d["studios"][0]["name"] == "Marvel Studios"
    assert d["files"][0]["duration"] == 126 * 60


def test_manual_match_and_merge_duplicates(library, host, tmp_path):
    review = host.get("/api/movies", params={"status": "needs_review"}).json()["items"][0]
    r = host.post(f"/api/movies/{review['id']}/match", json={"tmdb_id": 1091})  # same film as 'The Thing (1982)'
    assert r.status_code == 200, r.text
    merged = r.json()
    assert merged["tmdb_id"] == 1091 and merged["confirmed"]
    assert len([f for f in merged["files"] if f["status"] == "ok"]) == 2  # two copies of one movie
    dups = host.get("/api/files/duplicates").json()
    assert any(d["movie_id"] == merged["id"] for d in dups)


def test_rescan_never_replaces_confirmed_match(library, host):
    iron = movies_by_title(host)[("Iron Man", 2008)]
    # the host deliberately points this file at a different TMDB record (Toy Story)
    r = host.post(f"/api/movies/{iron['id']}/match", json={"tmdb_id": 862})
    assert r.status_code == 200
    mid = r.json()["id"]
    scan_and_wait(host)
    scan_and_wait(host, "match")
    d = host.get(f"/api/movies/{mid}").json()
    assert d["tmdb_id"] == 862 and d["match_source"] == "manual"


def test_moved_renamed_missing_corrupt_short(library, host, tmp_path):
    root = library
    src = root / "Airplane! (1980).avi"
    with db.session_scope() as s:
        before = s.query(MovieFile).filter(MovieFile.filename == src.name).one()
        movie_id = before.movie_id
    os.makedirs(root / "Comedy", exist_ok=True)
    os.rename(src, root / "Comedy" / "airplane-renamed.avi")  # moved AND renamed
    (root / "Alien (1979).mkv").unlink()  # removed
    write_fake_movie(root / "Broken (2001).mkv", corrupt=True)
    write_fake_movie(root / "Some Trailer (2001).mkv", duration=120)  # too short = trailer/extra
    (root / "notes.mkv").write_bytes(b"")  # unreadable media
    scan = scan_and_wait(host)
    assert scan["counts"]["moved"] == 1
    assert scan["counts"]["missing"] == 1
    with db.session_scope() as s:
        moved = s.query(MovieFile).filter(MovieFile.filename == "airplane-renamed.avi").one()
        assert moved.movie_id == movie_id and moved.status == "ok"  # history and match kept
        assert s.query(MovieFile).filter(MovieFile.filename == "Alien (1979).mkv").one().status == "missing"
    issues = {i["filename"]: i for i in host.get("/api/files/issues").json()}
    assert issues["Broken (2001).mkv"]["status"] == "corrupt"
    assert issues["Some Trailer (2001).mkv"]["status"] == "unsupported"
    assert issues["notes.mkv"]["status"] in ("corrupt", "unsupported")
    assert issues["Alien (1979).mkv"]["status"] == "missing"
    # files on disk are never modified by a scan
    assert (root / "Comedy" / "airplane-renamed.avi").exists()


def test_identical_copy_is_duplicate(library, host):
    root = library
    data = (root / "Toy Story (1995).m4v").read_text()
    (root / "Backup").mkdir()
    (root / "Backup" / "copy.m4v").write_text(data)
    scan = scan_and_wait(host)
    assert scan["counts"]["duplicates"] == 1
    with db.session_scope() as s:
        copy = s.query(MovieFile).filter(MovieFile.filename == "copy.m4v").one()
        orig = s.query(MovieFile).filter(MovieFile.filename == "Toy Story (1995).m4v").one()
        assert copy.movie_id == orig.movie_id and copy.duplicate_of_id == orig.id


def test_excluded_folders_and_skip_names(host, tmdb_key, fake_probe, tmp_path):
    root = tmp_path / "Lib"
    write_fake_movie(root / "Alien (1979).mkv")
    write_fake_movie(root / "Private" / "Iron Man (2008).mkv")
    write_fake_movie(root / "Extras" / "Aliens (1986).mkv")
    r = host.post("/api/directories", json={"path": str(root), "excluded_paths": ["Private"]})
    assert r.status_code == 200, r.text
    scan_and_wait(host)
    with db.session_scope() as s:
        names = {f.filename for f in s.query(MovieFile)}
    assert names == {"Alien (1979).mkv"}


def test_incremental_scan_skips_unchanged(library, host, monkeypatch):
    calls = []
    from app.services import scanner
    from tests.conftest import fake_probe_fn

    monkeypatch.setattr(scanner, "probe", lambda p: (calls.append(p), fake_probe_fn(p))[1])
    scan = scan_and_wait(host)
    assert calls == []
    assert scan["counts"]["unchanged"] == 8


def test_directory_management(host, tmp_path):
    a = tmp_path / "A"
    a.mkdir()
    (a / "sub").mkdir()
    r = host.post("/api/directories", json={"path": str(a)})
    assert r.status_code == 200
    d = r.json()
    # overlapping folders are refused
    assert host.post("/api/directories", json={"path": str(a / "sub")}).status_code == 409
    # relative / missing / file paths are refused
    assert host.post("/api/directories", json={"path": "relative/path"}).status_code == 422
    assert host.post("/api/directories", json={"path": str(tmp_path / "nope")}).status_code == 422
    # excluded paths must stay inside the folder
    r = host.patch(f"/api/directories/{d['id']}", json={"excluded_paths": [str(tmp_path)]})
    assert r.status_code == 422
    b = tmp_path / "B"
    b.mkdir()
    r = host.patch(f"/api/directories/{d['id']}", json={"path": str(b)})
    assert r.status_code == 200 and r.json()["path"] == os.path.realpath(b)
    assert host.delete(f"/api/directories/{d['id']}").status_code == 200
    assert host.get("/api/directories").json() == []


def test_scan_without_tmdb_key_leaves_movies_unmatched(host, fake_probe, tmp_path):
    root = tmp_path / "M"
    write_fake_movie(root / "Alien (1979).mkv")
    host.post("/api/directories", json={"path": str(root)})
    scan_and_wait(host)
    with db.session_scope() as s:
        m = s.query(Movie).one()
        assert m.metadata_status == "unmatched" and m.parsed_year == 1979


def test_refresh_and_retry_jobs(library, host, tmdb_key):
    """'Refresh all metadata' re-downloads records without changing matches; 'retry' re-runs unmatched only."""
    before = {m["id"]: m["title"] for m in host.get("/api/movies").json()["items"]}
    calls_before = tmdb_key.calls["/movie/1726"]
    scan = scan_and_wait(host, "refresh")
    assert scan["counts"]["updated"] == 7
    assert tmdb_key.calls["/movie/1726"] > calls_before  # bypasses the cache
    after = {m["id"]: m["title"] for m in host.get("/api/movies").json()["items"]}
    assert after == before
    scan = scan_and_wait(host, "match")
    assert scan["counts"].get("matched", 0) == 0  # the 'needs review' file is not silently decided


def test_scan_requires_folder_and_key(host):
    assert host.post("/api/scans", json={"mode": "scan"}).json()["detail"]["code"] == "no_directories"
    assert host.post("/api/scans", json={"mode": "refresh"}).json()["detail"]["code"] == "no_tmdb_key"


def test_tv_episodes_are_set_aside(host, tmdb_key, fake_probe, tmp_path):
    root = tmp_path / "Media"
    write_fake_movie(root / "Alien (1979).mkv")
    write_fake_movie(root / "South Park" / "ep06 The Mexican Staring Frog.mkv", 22 * 60 + 0.5)
    write_fake_movie(root / "Invincible" / "Invincible.S02E06.1080p.mkv", 45 * 60)
    host.patch("/api/settings/library", json={"min_duration_minutes": 20})
    host.post("/api/directories", json={"path": str(root)})
    scan = scan_and_wait(host)
    assert scan["counts"]["episodes"] == 2
    assert host.get("/api/movies", params={"status": "needs_review"}).json()["total"] == 0
    issues = {i["filename"]: i for i in host.get("/api/files/issues").json()}
    assert "TV episode (S02E06)" in issues["Invincible.S02E06.1080p.mkv"]["error"]
    # turning the setting off brings them back as candidates on the next scan
    host.patch("/api/settings/library", json={"skip_tv_episodes": False})
    scan = scan_and_wait(host)
    with db.session_scope() as s:
        assert s.query(MovieFile).filter(MovieFile.status == "ok").count() == 3


def test_existing_episodes_cleaned_up_on_rescan(host, tmdb_key, fake_probe, tmp_path):
    """Libraries scanned before episode detection: unchanged episode files leave the review queue."""
    root = tmp_path / "Media"
    write_fake_movie(root / "Show.S01E01.mkv", 45 * 60)
    host.patch("/api/settings/library", json={"skip_tv_episodes": False})
    host.post("/api/directories", json={"path": str(root)})
    scan_and_wait(host)
    with db.session_scope() as s:
        assert s.query(Movie).count() == 1
    host.patch("/api/settings/library", json={"skip_tv_episodes": True})
    scan = scan_and_wait(host)
    assert scan["counts"]["episodes"] == 1
    with db.session_scope() as s:
        assert s.query(Movie).count() == 0  # unused, unconfirmed placeholder removed


def test_rescan_retries_needs_review(library, host, tmdb_key):
    """Movies waiting for review are looked up again on later scans (e.g. after better matching rules)."""
    with db.session_scope() as s:
        m = s.query(Movie).filter(Movie.title == "Toy Story").one()
        m.metadata_status, m.confirmed, m.tmdb_id = "needs_review", False, None
    scan_and_wait(host, "match")
    with db.session_scope() as s:
        assert s.query(Movie).filter(Movie.tmdb_id == 862).one().metadata_status == "matched"
