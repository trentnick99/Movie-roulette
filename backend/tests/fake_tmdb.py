"""A small in-memory stand-in for the TMDB API (test fixture data only - never used by the app)."""
from __future__ import annotations

import json
import re
from collections import Counter
from urllib.parse import parse_qs, urlparse

import httpx

GENRES = {28: "Action", 12: "Adventure", 16: "Animation", 35: "Comedy", 80: "Crime", 18: "Drama", 27: "Horror",
          878: "Science Fiction", 53: "Thriller", 10751: "Family"}


def _movie(mid, title, date, genres, runtime, director, cast, companies, collection=None, popularity=20.0,
           original_title=None, alt_titles=(), vote=7.0):
    return {
        "id": mid, "title": title, "original_title": original_title or title, "release_date": date,
        "overview": f"Overview of {title}.", "runtime": runtime, "poster_path": f"/poster{mid}.jpg",
        "backdrop_path": f"/backdrop{mid}.jpg", "original_language": "en", "popularity": popularity,
        "vote_average": vote, "vote_count": 1000, "genres": [{"id": g, "name": GENRES[g]} for g in genres],
        "belongs_to_collection": collection,
        "production_companies": [{"id": cid, "name": name, "logo_path": None, "origin_country": "US"}
                                 for cid, name in companies],
        "credits": {"cast": [{"id": pid, "name": name, "character": f"Role {i}", "order": i, "profile_path": None}
                             for i, (pid, name) in enumerate(cast)],
                    "crew": [{"id": director[0], "name": director[1], "job": "Director", "department": "Directing"}]},
        "keywords": {"keywords": [{"id": 1, "name": "test keyword"}]},
        "alternative_titles": {"titles": [{"title": t, "iso_3166_1": "US"} for t in alt_titles]},
    }


MARVEL = (420, "Marvel Studios")
FOX = (25, "20th Century Fox")
UNIVERSAL = (33, "Universal Pictures")
PARAMOUNT = (4, "Paramount")
IRON_COLL = {"id": 131292, "name": "Iron Man Collection", "poster_path": "/ironcoll.jpg", "backdrop_path": None}
ALIEN_COLL = {"id": 8091, "name": "Alien Collection", "poster_path": "/aliencoll.jpg", "backdrop_path": None}

CATALOG = {
    1726: _movie(1726, "Iron Man", "2008-04-30", [28, 878, 12], 126, (3293, "Jon Favreau"),
                 [(3223, "Robert Downey Jr."), (12052, "Gwyneth Paltrow")], [MARVEL], IRON_COLL, 80),
    10138: _movie(10138, "Iron Man 2", "2010-04-28", [28, 878, 12], 124, (3293, "Jon Favreau"),
                  [(3223, "Robert Downey Jr."), (1245, "Scarlett Johansson")], [MARVEL], IRON_COLL, 60),
    348: _movie(348, "Alien", "1979-05-25", [27, 878], 117, (578, "Ridley Scott"), [(10205, "Sigourney Weaver")],
                [FOX], ALIEN_COLL, 50),
    679: _movie(679, "Aliens", "1986-07-18", [28, 53, 878], 137, (2710, "James Cameron"),
                [(10205, "Sigourney Weaver")], [FOX], ALIEN_COLL, 45),
    1091: _movie(1091, "The Thing", "1982-06-25", [27, 878, 53], 109, (11770, "John Carpenter"),
                 [(6856, "Kurt Russell")], [UNIVERSAL], None, 30),
    60935: _movie(60935, "The Thing", "2011-10-12", [27, 878, 53], 103, (93364, "Matthijs van Heijningen Jr."),
                  [(1121, "Mary Elizabeth Winstead")], [UNIVERSAL], None, 25),
    813: _movie(813, "Airplane!", "1980-06-27", [35], 88, (5005, "Jim Abrahams"), [(2639, "Robert Hays")],
                [PARAMOUNT], None, 20, alt_titles=("Flying High",)),
    862: _movie(862, "Toy Story", "1995-11-22", [16, 12, 35, 10751], 81, (7879, "John Lasseter"),
                [(31, "Tom Hanks")], [(3, "Pixar")], None, 90),
}


def search(query: str, year: int | None) -> list[dict]:
    q = re.sub(r"[^a-z0-9 ]", "", query.lower()).strip()
    out = []
    for m in CATALOG.values():
        t = re.sub(r"[^a-z0-9 ]", "", m["title"].lower())
        alts = [re.sub(r"[^a-z0-9 ]", "", a["title"].lower()) for a in m["alternative_titles"]["titles"]]
        if q and (q in t or t in q or any(q in a for a in alts)):
            if year and not m["release_date"].startswith(str(year)):
                continue
            out.append({k: m[k] for k in ("id", "title", "original_title", "release_date", "overview",
                                          "poster_path", "popularity")})
    return sorted(out, key=lambda r: -r["popularity"])


class FakeTMDB:
    def __init__(self, valid_key: str = "test-key-123456"):
        self.valid_key = valid_key
        self.calls: Counter[str] = Counter()
        self.fail_next: list[int] = []  # status codes to return for the next requests

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = urlparse(str(request.url))
        params = {k: v[0] for k, v in parse_qs(url.query).items()}
        path = url.path.replace("/3", "", 1)
        self.calls[path] += 1
        key_ok = params.get("api_key") == self.valid_key or request.headers.get("authorization") == \
            f"Bearer {self.valid_key}"
        if self.fail_next:
            code = self.fail_next.pop(0)
            return httpx.Response(code, headers={"Retry-After": "0"}, json={"status_message": "fail"})
        if not key_ok:
            return httpx.Response(401, json={"status_message": "Invalid API key"})
        if path == "/configuration":
            return httpx.Response(200, json={"images": {"secure_base_url": "https://image.tmdb.org/t/p/"}})
        if path == "/genre/movie/list":
            return httpx.Response(200, json={"genres": [{"id": k, "name": v} for k, v in GENRES.items()]})
        if path == "/search/movie":
            year = params.get("primary_release_year")
            return httpx.Response(200, json={"results": search(params.get("query", ""), int(year) if year else None)})
        m = re.fullmatch(r"/movie/(\d+)", path)
        if m:
            mid = int(m.group(1))
            if mid not in CATALOG:
                return httpx.Response(404, json={"status_message": "not found"})
            return httpx.Response(200, content=json.dumps(CATALOG[mid]), headers={"content-type": "application/json"})
        return httpx.Response(404, json={})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)
