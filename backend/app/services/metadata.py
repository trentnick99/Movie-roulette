"""Applying TMDB records to library movies (genres, people, studios, collections) and linking/merging matches."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from ..models import (
    GameRound,
    Genre,
    MatchCandidate,
    Movie,
    MovieCategory,
    MovieFile,
    MovieGenre,
    MoviePerson,
    MovieProductionCompany,
    Person,
    ProductionCompany,
    TMDBCollection,
)
from .tmdb import TMDBClient


def _year(d: str | None) -> int | None:
    return int(d[:4]) if d and len(d) >= 4 and d[:4].isdigit() else None


def _get_or_create_genre(db: Session, gid: int, name: str) -> Genre:
    g = db.get(Genre, gid)
    if g is None:
        clash = db.query(Genre).filter(Genre.name == name).first()
        if clash is not None:
            return clash
        g = Genre(id=gid, name=name)
        db.add(g)
        db.flush()
    elif g.name != name:
        g.name = name
    return g


def _person(db: Session, data: dict) -> Person:
    p = db.query(Person).filter_by(tmdb_id=int(data["id"])).first()
    if p is None:
        p = Person(tmdb_id=int(data["id"]), name=data.get("name") or "Unknown",
                   profile_path=data.get("profile_path"), known_for_department=data.get("known_for_department"))
        db.add(p)
        db.flush()
    else:
        p.name = data.get("name") or p.name
        p.profile_path = data.get("profile_path") or p.profile_path
    return p


def apply_details(db: Session, movie: Movie, d: dict, *, cast_limit: int = 15) -> Movie:
    """Overwrite a movie's metadata with a TMDB detail record (append_to_response=credits,keywords)."""
    movie.tmdb_id = int(d["id"])
    movie.title = d.get("title") or movie.title
    movie.original_title = d.get("original_title")
    movie.release_date = (d.get("release_date") or None) or None
    movie.release_year = _year(d.get("release_date"))
    movie.overview = d.get("overview")
    movie.runtime = d.get("runtime") or None
    movie.poster_path = d.get("poster_path")
    movie.backdrop_path = d.get("backdrop_path")
    movie.original_language = d.get("original_language")
    movie.popularity = d.get("popularity")
    movie.vote_average = d.get("vote_average")
    movie.vote_count = d.get("vote_count")
    kw = d.get("keywords") or {}
    movie.keywords = [k.get("name") for k in (kw.get("keywords") or kw.get("results") or []) if k.get("name")][:40]
    movie.metadata_updated_at = datetime.now(UTC)

    coll = d.get("belongs_to_collection")
    if coll and coll.get("id"):
        c = db.query(TMDBCollection).filter_by(tmdb_id=int(coll["id"])).first()
        if c is None:
            c = TMDBCollection(tmdb_id=int(coll["id"]), name=coll.get("name") or "Collection",
                               poster_path=coll.get("poster_path"), backdrop_path=coll.get("backdrop_path"))
            db.add(c)
            db.flush()
        else:
            c.name = coll.get("name") or c.name
        movie.collection_id = c.id
    else:
        movie.collection_id = None

    db.query(MovieGenre).filter_by(movie_id=movie.id).delete()
    for g in d.get("genres") or []:
        genre = _get_or_create_genre(db, int(g["id"]), g.get("name") or str(g["id"]))
        db.add(MovieGenre(movie_id=movie.id, genre_id=genre.id))

    db.query(MoviePerson).filter_by(movie_id=movie.id).delete()
    db.flush()
    credits = d.get("credits") or {}
    seen: set[tuple[int, str]] = set()
    for i, c in enumerate(sorted(credits.get("cast") or [], key=lambda x: x.get("order", 999))[:cast_limit]):
        p = _person(db, c)
        if (p.id, "cast") not in seen:
            seen.add((p.id, "cast"))
            db.add(MoviePerson(movie_id=movie.id, person_id=p.id, role="cast", character=c.get("character"),
                               credit_order=i))
    for c in credits.get("crew") or []:
        if c.get("job") == "Director":
            p = _person(db, c)
            if (p.id, "director") not in seen:
                seen.add((p.id, "director"))
                db.add(MoviePerson(movie_id=movie.id, person_id=p.id, role="director", credit_order=0))

    db.query(MovieProductionCompany).filter_by(movie_id=movie.id).delete()
    for pc in d.get("production_companies") or []:
        comp = db.query(ProductionCompany).filter_by(tmdb_id=int(pc["id"])).first()
        if comp is None:
            comp = ProductionCompany(tmdb_id=int(pc["id"]), name=pc.get("name") or "Studio",
                                     logo_path=pc.get("logo_path"), origin_country=pc.get("origin_country"))
            db.add(comp)
            db.flush()
        if not db.get(MovieProductionCompany, (movie.id, comp.id)):
            db.add(MovieProductionCompany(movie_id=movie.id, company_id=comp.id))
    db.flush()
    return movie


def merge_into(db: Session, source: Movie, target: Movie) -> Movie:
    """Move everything that refers to `source` onto `target`, then delete `source` (duplicate copies of a movie)."""
    if source.id == target.id:
        return target
    db.query(MovieFile).filter_by(movie_id=source.id).update({"movie_id": target.id})
    db.query(GameRound).filter_by(movie_id=source.id).update({"movie_id": target.id})
    for mc in db.query(MovieCategory).filter_by(movie_id=source.id).all():
        if db.get(MovieCategory, (mc.category_id, target.id)) is None:
            db.add(MovieCategory(category_id=mc.category_id, movie_id=target.id, mode=mc.mode))
        db.delete(mc)
    target.excluded_from_game = target.excluded_from_game or source.excluded_from_game
    db.flush()
    db.delete(source)
    db.flush()
    return target


def link_movie(db: Session, movie: Movie, tmdb_id: int, client: TMDBClient, *, source: str,
               confidence: float | None, cast_limit: int = 15) -> Movie:
    """Point a library movie at a TMDB record. If another library movie already has that record, the two are
    merged (they are copies of the same film)."""
    details = client.movie(tmdb_id)
    other = db.query(Movie).filter(Movie.tmdb_id == int(tmdb_id), Movie.id != movie.id).first()
    if other is not None:
        movie = merge_into(db, movie, other)
    if movie.tmdb_id is not None and movie.tmdb_id != int(tmdb_id):
        movie.tmdb_id = None  # release the old id before re-pointing (unique constraint)
        db.flush()
    apply_details(db, movie, details, cast_limit=cast_limit)
    movie.metadata_status = "matched"
    movie.match_source = source
    movie.match_confidence = confidence
    movie.confirmed = True
    db.query(MatchCandidate).filter_by(movie_id=movie.id).delete()
    db.flush()
    return movie


def refresh_movie(db: Session, movie: Movie, client: TMDBClient, cast_limit: int = 15) -> Movie:
    """Re-download metadata for an already matched movie. The match itself is never changed."""
    if movie.tmdb_id is None:
        return movie
    client_details = client.get(f"/movie/{movie.tmdb_id}", {
        "language": client.language, "append_to_response": "credits,keywords,alternative_titles"}, use_cache=False)
    return apply_details(db, movie, client_details, cast_limit=cast_limit)
