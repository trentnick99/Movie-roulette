"""Category generation, custom categories, filters, random selection and repeat rules."""
from __future__ import annotations

import random
from collections import Counter
from datetime import UTC, datetime, timedelta

from app import db
from app.models import Category, Game, GameRound, Movie
from app.services import categories as cats
from app.services import selection


def cat_by(host, kind, name):
    return next(c for c in host.get("/api/categories", params={"include_empty": True}).json()
                if c["kind"] == kind and c["name"] == name)


def test_auto_categories(library, host):
    listing = host.get("/api/categories").json()
    kinds = Counter(c["kind"] for c in listing)
    assert kinds["genre"] >= 6 and kinds["decade"] == 5 and kinds["collection"] == 2
    assert cat_by(host, "genre", "Horror")["count"] == 2  # Alien, The Thing (1982)
    assert cat_by(host, "decade", "1980s")["count"] == 3  # Aliens, The Thing, Airplane!
    assert cat_by(host, "collection", "Iron Man Collection")["count"] == 2
    studios = {c["name"]: c["count"] for c in listing if c["kind"] == "studio"}
    assert studios == {"Marvel Studios": 2, "20th Century Fox": 2}  # single-movie studios stay as filters only
    # syncing again never duplicates a TMDB entity
    with db.session_scope() as s:
        before = s.query(Category).count()
        cats.sync_auto_categories(s)
        assert s.query(Category).count() == before


def test_person_and_studio_filters(library, host):
    weaver = next(p for p in host.get("/api/people", params={"q": "Weaver"}).json())
    assert weaver["count"] == 2
    titles = {m["title"] for m in host.get("/api/movies", params={"person": weaver["id"]}).json()["items"]}
    assert titles == {"Alien", "Aliens"}
    fav = host.get("/api/people", params={"q": "Favreau", "role": "director"}).json()[0]
    items = host.get("/api/movies", params={"person": fav["id"], "role": "director"}).json()["items"]
    assert {m["title"] for m in items} == {"Iron Man", "Iron Man 2"}
    studio = next(s for s in host.get("/api/facets").json()["studios"] if s["name"] == "Pixar")
    items = host.get("/api/movies", params={"studio": studio["id"]}).json()["items"]
    assert [m["title"] for m in items] == ["Toy Story"]


def test_custom_category_rules_and_membership(library, host):
    facets = host.get("/api/facets").json()
    sf = next(g["id"] for g in facets["genres"] if g["name"] == "Science Fiction")
    r = host.post("/api/categories", json={"name": "80s Sci-Fi", "rules": {"genres": [sf], "decades": [1980]}})
    assert r.status_code == 200, r.text
    c = r.json()
    assert c["count"] == 2  # Aliens, The Thing (1982)
    assert host.post("/api/categories", json={"name": "80s sci-fi"}).status_code == 409
    toy = next(m for m in host.get("/api/movies", params={"q": "Toy"}).json()["items"])
    c = host.post(f"/api/categories/{c['id']}/movies", json={"movie_ids": [toy["id"]], "mode": "include"}).json()
    assert c["count"] == 3
    aliens = next(m for m in host.get("/api/movies", params={"q": "Aliens"}).json()["items"])
    c = host.post(f"/api/categories/{c['id']}/movies", json={"movie_ids": [aliens["id"]], "mode": "exclude"}).json()
    assert c["count"] == 2
    # exclusions also work on automatic categories, and including into them is refused
    horror = cat_by(host, "genre", "Horror")
    r = host.post(f"/api/categories/{horror['id']}/movies", json={"movie_ids": [toy["id"]], "mode": "include"})
    assert r.status_code == 422
    alien = next(m for m in host.get("/api/movies", params={"q": "Alien"}).json()["items"] if m["title"] == "Alien")
    h = host.post(f"/api/categories/{horror['id']}/movies", json={"movie_ids": [alien["id"]], "mode": "exclude"})
    assert h.json()["count"] == 1
    # OR logic, unplayed rule, bad rules
    r = host.post("/api/categories", json={"name": "Either", "rules": {"logic": "any", "genres": [sf],
                                                                        "decades": [1990]}})
    assert r.json()["count"] == 6
    assert host.post("/api/categories", json={"name": "Bad", "rules": {"sql": "drop"}}).status_code == 422
    unplayed = host.post("/api/categories", json={"name": "Movies We Haven't Played", "rules": {"unplayed": True}})
    assert unplayed.json()["count"] == 7
    # ordering and hiding
    host.post("/api/categories/reorder", json={"ids": [unplayed.json()["id"], c["id"]]})
    first = host.get("/api/categories").json()[0]
    assert first["id"] == unplayed.json()["id"]
    assert host.patch(f"/api/categories/{horror['id']}", json={"hidden": True}).json()["hidden"]
    assert host.delete(f"/api/categories/{horror['id']}").status_code == 422  # auto: hide, don't delete
    assert host.delete(f"/api/categories/{c['id']}").status_code == 200


def test_selection_uniform_and_restricted(library):
    with db.session_scope() as s:
        horror = s.query(Category).filter_by(kind="genre", name="Horror").one()
        allowed = cats.category_movie_ids(s, horror)
        rng = random.Random(7)
        counts = Counter(selection.pick(s, [{"type": "category", "id": horror.id}], "all", game=None,
                                        policy="none", history_reset_at=None, rng=rng) for _ in range(4000))
        assert set(counts) == allowed  # never outside the chosen category
        assert all(abs(n - 2000) < 200 for n in counts.values())
        # whole library: every eligible (matched, playable) movie appears, none favoured
        counts = Counter(selection.pick(s, [], "all", game=None, policy="none", history_reset_at=None, rng=rng)
                         for _ in range(7000))
        assert len(counts) == 7 and all(abs(n - 1000) < 150 for n in counts.values())


def test_excluded_movies_never_selected(library, host):
    alien = next(m for m in host.get("/api/movies", params={"q": "Alien"}).json()["items"] if m["title"] == "Alien")
    host.patch(f"/api/movies/{alien['id']}", json={"excluded_from_game": True})
    with db.session_scope() as s:
        picks = {selection.pick(s, [], "all", game=None, policy="none", history_reset_at=None) for _ in range(300)}
    assert alien["id"] not in picks


def _played(s, movie_id: int, days_ago: float):
    g = Game(name="old", mode="library", status="finished", config={})
    s.add(g)
    s.flush()
    s.add(GameRound(game_id=g.id, number=1, token=f"tok{movie_id}x{days_ago}".ljust(24, "x"), movie_id=movie_id,
                    status="closed", created_at=datetime.now(UTC) - timedelta(days=days_ago)))
    s.flush()


def test_repeat_policies(library):
    with db.session_scope() as s:
        ids = sorted(m.id for m in s.query(Movie).filter_by(metadata_status="matched"))
        _played(s, ids[0], 10)
        _played(s, ids[1], 60)
        _played(s, ids[2], 200)
        b30 = selection.repeat_blocked(s, None, "days30", None)
        b90 = selection.repeat_blocked(s, None, "days90", None)
        assert ids[0] in b30 and ids[1] not in b30
        assert {ids[0], ids[1]} <= b90 and ids[2] not in b90
        assert selection.repeat_blocked(s, None, "none", None) == set()
        # session = since this server started (old rounds are not in this session)
        assert selection.repeat_blocked(s, None, "session", None) == set()
        # resetting history forgets earlier plays
        reset = datetime.now(UTC).isoformat()
        assert selection.repeat_blocked(s, None, "days90", reset) == set()
