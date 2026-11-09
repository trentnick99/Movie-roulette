"""Client for the official TMDB API (v3), with caching, retries and rate-limit handling.

Accepts either a v3 API key or a v4 "API Read Access Token" (sent as a Bearer token).
JSON responses are cached in the tmdb_cache table; images are cached on disk under data/cache/images.
Credentials are never written to logs or cache keys.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from .. import db
from ..models import TMDBCache

log = logging.getLogger(__name__)

API_BASE = "https://api.themoviedb.org/3"
IMAGE_BASE = "https://image.tmdb.org/t/p"
IMAGE_SIZES = {"w92", "w154", "w185", "w300", "w342", "w500", "w780", "w1280", "original"}
_IMAGE_PATH = re.compile(r"^/[A-Za-z0-9_\-]{1,100}\.(?:jpg|jpeg|png|webp|svg)$")


class TMDBError(Exception):
    def __init__(self, code: str, message: str, status: int | None = None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.message = message


class RateLimiter:
    """Simple token bucket shared by all threads (TMDB allows roughly 40-50 requests per second)."""

    def __init__(self, per_second: float = 20.0):
        self.interval = 1.0 / per_second
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = self._next - now
            self._next = max(now, self._next) + self.interval
        if delay > 0:
            time.sleep(delay)


_limiter = RateLimiter()


def _db_cache_get(key: str, max_age: timedelta) -> dict | None:
    try:
        with db.session_scope() as s:
            row = s.get(TMDBCache, key)
            if row is None:
                return None
            fetched = row.fetched_at if row.fetched_at.tzinfo else row.fetched_at.replace(tzinfo=UTC)
            if datetime.now(UTC) - fetched > max_age:
                return None
            return row.payload
    except Exception:  # cache must never break a lookup
        return None


def _db_cache_put(key: str, payload: dict) -> None:
    try:
        with db.session_scope() as s:
            row = s.get(TMDBCache, key)
            if row is None:
                s.add(TMDBCache(key=key, payload=payload))
            else:
                row.payload = payload
                row.fetched_at = datetime.now(UTC)
    except Exception:
        log.debug("tmdb cache write failed", exc_info=True)


class TMDBClient:
    def __init__(self, credential: str | None, *, language: str = "en-US", include_adult: bool = False,
                 cache_days: int = 30, transport: httpx.BaseTransport | None = None,
                 cache_get: Callable[[str, timedelta], dict | None] | None = _db_cache_get,
                 cache_put: Callable[[str, dict], None] | None = _db_cache_put,
                 max_retries: int = 4, sleep: Callable[[float], None] = time.sleep, base_url: str = API_BASE):
        self.credential = (credential or "").strip()
        self.language = language
        self.include_adult = include_adult
        self.max_age = timedelta(days=cache_days)
        self.cache_get = cache_get
        self.cache_put = cache_put
        self.max_retries = max_retries
        self.sleep = sleep
        headers = {"Accept": "application/json", "User-Agent": "MovieRoulette/1.0"}
        self._bearer = self.credential.startswith("eyJ") and len(self.credential) > 60
        if self._bearer:
            headers["Authorization"] = f"Bearer {self.credential}"
        self.http = httpx.Client(base_url=base_url, headers=headers, timeout=httpx.Timeout(20.0, connect=10.0),
                                 transport=transport, follow_redirects=True)

    @property
    def configured(self) -> bool:
        return bool(self.credential)

    def close(self) -> None:
        self.http.close()

    # ------------------------------------------------------------- core request
    def get(self, path: str, params: dict[str, Any] | None = None, *, use_cache: bool = True) -> dict:
        if not self.configured:
            raise TMDBError("no_key", "No TMDB API key is configured. Add one in Settings > TMDB.")
        params = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
        key = path + "?" + "&".join(f"{k}={params[k]}" for k in sorted(params))
        if use_cache and self.cache_get:
            hit = self.cache_get(key, self.max_age)
            if hit is not None:
                return hit
        send = dict(params)
        if not self._bearer:
            send["api_key"] = self.credential
        last: TMDBError | None = None
        for attempt in range(self.max_retries + 1):
            _limiter.wait()
            try:
                r = self.http.get(path, params=send)
            except httpx.HTTPError as e:
                last = TMDBError("network", f"Could not reach TMDB ({type(e).__name__}). Check the internet "
                                            "connection.")
                self.sleep(min(2 ** attempt, 16) * 0.5)
                continue
            if r.status_code == 200:
                try:
                    data = r.json()
                except ValueError as e:
                    raise TMDBError("server", "TMDB returned an unreadable response.") from e
                if use_cache and self.cache_put:
                    self.cache_put(key, data)
                return data
            if r.status_code == 401:
                raise TMDBError("auth", "TMDB rejected the API key. Check it in Settings > TMDB.", 401)
            if r.status_code == 404:
                raise TMDBError("not_found", "TMDB has no record for that request.", 404)
            if r.status_code == 429:
                try:
                    wait = float(r.headers.get("Retry-After", "1"))
                except ValueError:
                    wait = 1.0
                last = TMDBError("rate_limited", "TMDB rate limit reached; retrying.", 429)
                self.sleep(min(max(wait, 0.5), 30))
                continue
            if r.status_code >= 500:
                last = TMDBError("server", f"TMDB is having problems (HTTP {r.status_code}).", r.status_code)
                self.sleep(min(2 ** attempt, 16) * 0.5)
                continue
            raise TMDBError("http", f"TMDB request failed (HTTP {r.status_code}).", r.status_code)
        raise last or TMDBError("network", "TMDB request failed.")

    # ------------------------------------------------------------- endpoints
    def test_connection(self) -> dict:
        data = self.get("/configuration", use_cache=False)
        return {"ok": True, "image_base": (data.get("images") or {}).get("secure_base_url")}

    def search_movie(self, query: str, year: int | None = None) -> list[dict]:
        params = {"query": query, "include_adult": str(self.include_adult).lower(), "language": self.language,
                  "page": 1}
        if year:
            params["primary_release_year"] = year
        return list(self.get("/search/movie", params).get("results") or [])

    def movie(self, tmdb_id: int) -> dict:
        return self.get(f"/movie/{int(tmdb_id)}", {
            "language": self.language, "append_to_response": "credits,keywords,alternative_titles"})

    def genres(self) -> list[dict]:
        return list(self.get("/genre/movie/list", {"language": self.language}).get("genres") or [])

    def alternative_titles(self, tmdb_id: int) -> list[str]:
        data = self.get(f"/movie/{int(tmdb_id)}/alternative_titles")
        return [t.get("title") for t in data.get("titles") or [] if t.get("title")]


# ------------------------------------------------------------------ image cache

def image_local_path(image_dir: Path, size: str, path: str) -> Path:
    if size not in IMAGE_SIZES or not _IMAGE_PATH.match(path or ""):
        raise TMDBError("bad_image", "Invalid image reference.")
    return image_dir / size / path.lstrip("/")


def fetch_image(image_dir: Path, size: str, path: str, transport: httpx.BaseTransport | None = None,
                base: str = IMAGE_BASE) -> Path:
    """Return the cached image file, downloading it once from TMDB's image CDN when needed."""
    dest = image_local_path(image_dir, size, path)
    if dest.is_file() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with httpx.Client(timeout=20, transport=transport, follow_redirects=True) as c:
            r = c.get(f"{base.rstrip('/')}/{size}{path}")
    except httpx.HTTPError as e:
        raise TMDBError("network", "Could not download the image from TMDB.") from e
    if r.status_code != 200 or not r.content:
        raise TMDBError("not_found", "Image not available.", r.status_code)
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(r.content)
    tmp.replace(dest)
    return dest


def image_cache_size(image_dir: Path) -> tuple[int, int]:
    files = [p for p in image_dir.rglob("*") if p.is_file()]
    return len(files), sum(p.stat().st_size for p in files)
