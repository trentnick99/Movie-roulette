"""Categories and movie filters.

Automatic categories (genre, decade, franchise/collection, studio) are created once per TMDB entity (unique on
kind + ref_id) and their membership is *derived* from TMDB metadata, so it is always current. A host can still
exclude a movie from any category; that exclusion is stored and survives rescans. Custom categories combine
hand-picked movies with optional rules (genres, decades, people, studios, unplayed ...).
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    Category,
    GameRound,
    Genre,
    Movie,
    MovieCategory,
    MovieGenre,
    MoviePerson,
    MovieProductionCompany,
    ProductionCompany,
    TMDBCollection,
)

AUTO_KINDS = ("genre", "decade", "collection", "studio")
STUDIO_MIN_MOVIES = 2
KIND_ORDER = {"custom": 0, "genre": 1, "decade": 2, "collection": 3, "studio": 4}


def slugify(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s or "category"


def _unique_slug(db: Session, base: str, exclude_id: int | None = None) -> str:
    slug, n = base, 2
    while True:
        q = db.query(Category).filter(Category.slug == slug)
        if exclude_id:
            q = q.filter(Category.id != exclude_id)
        if q.first() is None:
            return slug
        slug, n = f"{base}-{n}", n + 1


def decade_of(year: int | None) -> int | None:
    return None if year is None else (year // 10) * 10


def matched_movies_q(db: Session):
    return db.query(Movie.id).filter(Movie.metadata_status == "matched")


# ------------------------------------------------------------------ automatic categories

def sync_auto_categories(db: Session) -> dict[str, int]:
    """Create any missing automatic categories for entities that now have movies. Never duplicates a TMDB entity
    and never deletes a category a host may have customised (renamed, hidden, reordered)."""
    created = 0
    existing = {(c.kind, c.ref_id) for c in db.query(Category).filter(Category.kind.in_(AUTO_KINDS))}

    def add(kind: str, ref: int, name: str) -> None:
        nonlocal created
        if (kind, ref) in existing:
            return
        db.add(Category(kind=kind, ref_id=ref, name=name, slug=_unique_slug(db, slugify(f"{kind}-{name}"))))
        existing.add((kind, ref))
        created += 1
        db.flush()

    for g in db.query(Genre).join(MovieGenre, MovieGenre.genre_id == Genre.id).distinct():
        add("genre", g.id, g.name)
    for (year,) in db.query(Movie.release_year).filter(Movie.release_year.isnot(None),
                                                         Movie.metadata_status == "matched").distinct():
        d = decade_of(year)
        if d is not None:
            add("decade", d, f"{d}s")
    for c in db.query(TMDBCollection).join(Movie, Movie.collection_id == TMDBCollection.id).distinct():
        add("collection", c.id, c.name)
    studio_rows = (db.query(ProductionCompany.id, ProductionCompany.name, func.count(MovieProductionCompany.movie_id))
                   .join(MovieProductionCompany, MovieProductionCompany.company_id == ProductionCompany.id)
                   .group_by(ProductionCompany.id).having(func.count(MovieProductionCompany.movie_id)
                                                          >= STUDIO_MIN_MOVIES))
    for cid, name, _n in studio_rows:
        add("studio", cid, name)
    return {"created": created}


def _played_movie_ids(db: Session, since: datetime | None = None) -> set[int]:
    q = db.query(GameRound.movie_id).filter(GameRound.status != "skipped")
    if since is not None:
        q = q.filter(GameRound.created_at >= since)
    return {m for (m,) in q.distinct()}


def _rule_sets(db: Session, rules: dict[str, Any], history_since: datetime | None) -> list[set[int]]:
    """One movie-id set per rule present in a custom category definition."""
    sets: list[set[int]] = []
    if rules.get("genres"):
        sets.append({m for (m,) in db.query(MovieGenre.movie_id).filter(MovieGenre.genre_id.in_(rules["genres"]))})
    if rules.get("decades"):
        ys: set[int] = set()
        for d in rules["decades"]:
            ys |= {m for (m,) in db.query(Movie.id).filter(Movie.release_year >= int(d),
                                                           Movie.release_year < int(d) + 10)}
        sets.append(ys)
    if rules.get("collections"):
        sets.append({m for (m,) in db.query(Movie.id).filter(Movie.collection_id.in_(rules["collections"]))})
    if rules.get("people"):
        sets.append({m for (m,) in db.query(MoviePerson.movie_id).filter(MoviePerson.person_id.in_(rules["people"]))})
    if rules.get("studios"):
        sets.append({m for (m,) in db.query(MovieProductionCompany.movie_id)
                     .filter(MovieProductionCompany.company_id.in_(rules["studios"]))})
    if rules.get("keywords"):
        wanted = {k.lower() for k in rules["keywords"]}
        sets.append({m.id for m in db.query(Movie).filter(Movie.metadata_status == "matched")
                     if wanted & {k.lower() for k in (m.keywords or [])}})
    if rules.get("min_rating") is not None:
        sets.append({m for (m,) in db.query(Movie.id).filter(Movie.vote_average >= float(rules["min_rating"]))})
    if rules.get("max_runtime") is not None:
        sets.append({m for (m,) in db.query(Movie.id).filter(Movie.runtime <= int(rules["max_runtime"]))})
    if rules.get("unplayed"):
        played = _played_movie_ids(db, history_since)
        sets.append({m for (m,) in db.query(Movie.id).filter(Movie.metadata_status == "matched")} - played)
    return sets


def category_movie_ids(db: Session, cat: Category, history_since: datetime | None = None) -> set[int]:
    if cat.kind == "genre":
        ids = {m for (m,) in db.query(MovieGenre.movie_id).filter(MovieGenre.genre_id == cat.ref_id)}
    elif cat.kind == "decade":
        d = int(cat.ref_id or 0)
        ids = {m for (m,) in db.query(Movie.id).filter(Movie.release_year >= d, Movie.release_year < d + 10,
                                                       Movie.metadata_status == "matched")}
    elif cat.kind == "collection":
        ids = {m for (m,) in db.query(Movie.id).filter(Movie.collection_id == cat.ref_id)}
    elif cat.kind == "studio":
        ids = {m for (m,) in db.query(MovieProductionCompany.movie_id)
               .filter(MovieProductionCompany.company_id == cat.ref_id)}
    else:
        rules = cat.rules or {}
        sets = _rule_sets(db, rules, history_since)
        ruled: set[int] = set()
        if sets:
            ruled = set.intersection(*sets) if rules.get("logic", "all") == "all" else set.union(*sets)
        included = {m for (m,) in db.query(MovieCategory.movie_id).filter_by(category_id=cat.id, mode="include")}
        ids = ruled | included
    excluded = {m for (m,) in db.query(MovieCategory.movie_id).filter_by(category_id=cat.id, mode="exclude")}
    return ids - excluded


def category_counts(db: Session, cats: Iterable[Category]) -> dict[int, int]:
    return {c.id: len(category_movie_ids(db, c)) for c in cats}


def ordered_categories(db: Session, include_hidden: bool = True) -> list[Category]:
    q = db.query(Category)
    if not include_hidden:
        q = q.filter(Category.hidden.is_(False))
    cats = q.all()
    return sorted(cats, key=lambda c: (c.sort_order, KIND_ORDER.get(c.kind, 9),
                                       c.ref_id if c.kind == "decade" else 0, c.name.lower()))


# ------------------------------------------------------------------ game filters

FILTER_TYPES = {"category", "genre", "decade", "collection", "person", "studio"}


def describe_filter(db: Session, f: dict[str, Any]) -> str:
    t, ref = f.get("type"), f.get("id")
    if t == "category":
        c = db.get(Category, ref)
        return c.name if c else "Unknown category"
    if t == "genre":
        g = db.get(Genre, ref)
        return g.name if g else "Unknown genre"
    if t == "decade":
        return f"{int(ref)}s"
    if t == "collection":
        c = db.get(TMDBCollection, ref)
        return c.name if c else "Unknown franchise"
    if t == "studio":
        c = db.get(ProductionCompany, ref)
        return c.name if c else "Unknown studio"
    if t == "person":
        from ..models import Person

        p = db.get(Person, ref)
        return p.name if p else "Unknown person"
    return "?"


def filter_movie_ids(db: Session, f: dict[str, Any], history_since: datetime | None = None) -> set[int]:
    t, ref = f.get("type"), f.get("id")
    if t not in FILTER_TYPES or ref is None:
        raise ValueError(f"unknown filter {f!r}")
    if t == "category":
        c = db.get(Category, int(ref))
        if c is None:
            raise ValueError("That category no longer exists.")
        return category_movie_ids(db, c, history_since)
    if t == "genre":
        return {m for (m,) in db.query(MovieGenre.movie_id).filter(MovieGenre.genre_id == int(ref))}
    if t == "decade":
        d = int(ref)
        return {m for (m,) in db.query(Movie.id).filter(Movie.release_year >= d, Movie.release_year < d + 10)}
    if t == "collection":
        return {m for (m,) in db.query(Movie.id).filter(Movie.collection_id == int(ref))}
    if t == "studio":
        return {m for (m,) in db.query(MovieProductionCompany.movie_id)
                .filter(MovieProductionCompany.company_id == int(ref))}
    q = select(MoviePerson.movie_id).where(MoviePerson.person_id == int(ref))
    if f.get("role") in ("cast", "director"):
        q = q.where(MoviePerson.role == f["role"])
    return {m for (m,) in db.execute(q)}


def evaluate_filters(db: Session, filters: list[dict[str, Any]], logic: str = "all",
                     history_since: datetime | None = None) -> set[int] | None:
    """Movie ids matching the filters (None = no filters = whole library)."""
    if not filters:
        return None
    sets = [filter_movie_ids(db, f, history_since) for f in filters]
    return set.intersection(*sets) if logic == "all" else set.union(*sets)
