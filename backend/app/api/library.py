"""Library management API (host only): directories, scans, movies, matching, facets."""
from __future__ import annotations

import os
import string
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, selectinload

from .. import security as sec
from ..context import ctx
from ..db import get_db
from ..models import (
    Category,
    Genre,
    LibraryDirectory,
    LibraryScan,
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
from ..services import categories as cats
from ..services import metadata, settings
from ..services.matcher import score_candidate
from ..services.scanner import primary_file, runner
from ..services.tmdb import TMDBError
from .common import fail, iso

router = APIRouter(prefix="/api", tags=["library"], dependencies=[Depends(sec.require_host)])


# ------------------------------------------------------------------ directories

class DirectoryIn(BaseModel):
    path: str = Field(min_length=1, max_length=1024)
    excluded_paths: list[str] = Field(default_factory=list, max_length=200)
    enabled: bool = True


class DirectoryPatch(BaseModel):
    path: str | None = Field(None, max_length=1024)
    excluded_paths: list[str] | None = Field(None, max_length=200)
    enabled: bool | None = None


def _dir_view(db: Session, d: LibraryDirectory) -> dict:
    n = db.query(func.count(MovieFile.id)).filter(MovieFile.directory_id == d.id, MovieFile.status == "ok").scalar()
    return {"id": d.id, "path": d.path, "enabled": d.enabled, "excluded_paths": d.excluded_paths or [],
            "reachable": os.path.isdir(d.path), "files": n, "last_scanned_at": iso(d.last_scanned_at),
            "added_at": iso(d.added_at)}


def _validate_excludes(root: Path, items: list[str]) -> list[str]:
    out = []
    for raw in items:
        raw = (raw or "").strip()
        if not raw:
            continue
        p = Path(raw)
        if not p.is_absolute():
            p = root / raw
        if not sec.is_within(p, root):
            fail(422, "bad_exclude", f"Excluded folder must be inside {root}: {raw}")
        out.append(os.path.realpath(p))
    return sorted(set(out))


def _check_overlap(db: Session, path: Path, exclude_id: int | None = None) -> None:
    for d in db.query(LibraryDirectory):
        if d.id == exclude_id:
            continue
        if sec.is_within(path, d.path) or sec.is_within(d.path, path):
            fail(409, "overlap", f"That folder overlaps an existing movie folder ({d.path}).")


@router.get("/directories")
def list_dirs(db: Session = Depends(get_db)):
    return [_dir_view(db, d) for d in db.query(LibraryDirectory).order_by(LibraryDirectory.path)]


@router.post("/directories")
def add_dir(body: DirectoryIn, db: Session = Depends(get_db)):
    try:
        p = sec.validate_directory_input(body.path)
    except ValueError as e:
        fail(422, "bad_path", str(e))
    _check_overlap(db, p)
    d = LibraryDirectory(path=str(p), enabled=body.enabled, excluded_paths=_validate_excludes(p, body.excluded_paths))
    db.add(d)
    db.commit()
    return _dir_view(db, d)


@router.patch("/directories/{dir_id}")
def edit_dir(dir_id: int, body: DirectoryPatch, db: Session = Depends(get_db)):
    d = db.get(LibraryDirectory, dir_id)
    if d is None:
        fail(404, "not_found", "Folder not found.")
    if body.path is not None and body.path != d.path:
        try:
            p = sec.validate_directory_input(body.path)
        except ValueError as e:
            fail(422, "bad_path", str(e))
        _check_overlap(db, p, d.id)
        d.path = str(p)
    if body.excluded_paths is not None:
        d.excluded_paths = _validate_excludes(Path(d.path), body.excluded_paths)
    if body.enabled is not None:
        d.enabled = body.enabled
    db.commit()
    return _dir_view(db, d)


@router.delete("/directories/{dir_id}")
def remove_dir(dir_id: int, db: Session = Depends(get_db)):
    """Removes the folder from the library. Files on disk are never touched; their records are marked missing so
    game history stays intact. The next scan cleans up movies that are no longer used anywhere."""
    d = db.get(LibraryDirectory, dir_id)
    if d is None:
        fail(404, "not_found", "Folder not found.")
    n = db.query(MovieFile).filter(MovieFile.directory_id == d.id).update(
        {"status": "missing", "error": "Its movie folder was removed from the library.", "directory_id": None})
    db.delete(d)
    db.commit()
    return {"ok": True, "files_detached": n}


@router.get("/browse")
def browse(path: str | None = None):
    """Folder picker for the host. Lists sub-folders only (never files)."""
    if not path:
        if os.name == "nt":
            roots = [f"{c}:\\" for c in string.ascii_uppercase if os.path.isdir(f"{c}:\\")]
        else:
            roots = ["/"] + [str(p) for p in (Path.home(), Path("/mnt"), Path("/media")) if p.is_dir()]
        return {"path": None, "parent": None, "folders": [{"name": r, "path": r} for r in roots]}
    try:
        p = sec.validate_directory_input(path)
    except ValueError as e:
        fail(422, "bad_path", str(e))
    folders = []
    try:
        with os.scandir(p) as it:
            for e in it:
                try:
                    if e.is_dir(follow_symlinks=False) and not e.name.startswith((".", "$")):
                        folders.append({"name": e.name, "path": str(Path(p) / e.name)})
                except OSError:
                    continue
    except OSError as e:
        fail(403, "unreadable", f"Cannot open that folder: {e.strerror or e}")
    folders.sort(key=lambda x: x["name"].lower())
    parent = str(p.parent) if p.parent != p else None
    return {"path": str(p), "parent": parent, "folders": folders[:2000]}


# ------------------------------------------------------------------ scans

def _scan_view(s: LibraryScan | None) -> dict | None:
    if s is None:
        return None
    return {"id": s.id, "trigger": s.trigger, "status": s.status, "phase": s.phase, "started_at": iso(s.started_at),
            "finished_at": iso(s.finished_at), "current": s.progress_current, "total": s.progress_total,
            "counts": s.counts or {}, "message": s.message}


class ScanStart(BaseModel):
    mode: str = Field("scan", pattern="^(scan|match|refresh)$")


@router.post("/scans")
def start_scan(body: ScanStart | None = None, db: Session = Depends(get_db)):
    mode = (body.mode if body else "scan")
    if mode != "scan" and not ctx().tmdb_credential():
        fail(409, "no_tmdb_key", "Add a TMDB API key in Settings > TMDB first.")
    if mode == "scan" and not db.query(LibraryDirectory).filter_by(enabled=True).count():
        fail(409, "no_directories", "Add a movie folder first.")
    try:
        scan_id = runner.start("manual" if mode == "scan" else mode, ctx().tmdb_client, mode=mode)
    except RuntimeError as e:
        fail(409, "busy", str(e))
    return {"id": scan_id}


@router.post("/scans/cancel")
def cancel_scan():
    runner.stop()
    return {"ok": True}


@router.get("/scans/current")
def current_scan(db: Session = Depends(get_db)):
    s = db.query(LibraryScan).order_by(LibraryScan.id.desc()).first()
    return {"running": runner.busy, "scan": _scan_view(s)}


@router.get("/scans")
def scans(db: Session = Depends(get_db)):
    return [_scan_view(s) for s in db.query(LibraryScan).order_by(LibraryScan.id.desc()).limit(25)]


# ------------------------------------------------------------------ movies

SORTS = {
    "title": func.lower(Movie.title), "year": Movie.release_year, "added": Movie.created_at,
    "rating": Movie.vote_average, "popularity": Movie.popularity, "runtime": Movie.runtime,
}


def card(m: Movie) -> dict:
    f = primary_file(m)
    statuses = sorted({x.status for x in m.files})
    return {"id": m.id, "title": m.title, "release_year": m.release_year, "poster_path": m.poster_path,
            "vote_average": m.vote_average, "metadata_status": m.metadata_status,
            "match_confidence": m.match_confidence, "excluded_from_game": m.excluded_from_game,
            "playable": f is not None, "duration": f.duration if f else None, "file_statuses": statuses,
            "file_count": len(m.files), "parsed_title": m.parsed_title, "parsed_year": m.parsed_year,
            "genres": [g.name for g in m.genres][:3], "created_at": iso(m.created_at)}


@router.get("/movies")
def movies(db: Session = Depends(get_db), q: str | None = None, status: str | None = None,
           genre: int | None = None, decade: int | None = None, collection: int | None = None,
           person: int | None = None, role: str | None = None, studio: int | None = None,
           category: int | None = None, excluded: bool | None = None, playable: bool | None = None,
           sort: str = "title", order: str = "asc", page: int = Query(1, ge=1),
           page_size: int = Query(60, ge=1, le=500)):
    query = db.query(Movie).options(selectinload(Movie.files), selectinload(Movie.genres))
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(Movie.title.ilike(like), Movie.original_title.ilike(like),
                                 Movie.parsed_title.ilike(like)))
    if status == "missing":
        query = query.filter(~Movie.files.any(MovieFile.status == "ok"))
    elif status:
        query = query.filter(Movie.metadata_status == status)
    if genre is not None:
        query = query.filter(Movie.id.in_(db.query(MovieGenre.movie_id).filter(MovieGenre.genre_id == genre)))
    if decade is not None:
        query = query.filter(Movie.release_year >= decade, Movie.release_year < decade + 10)
    if collection is not None:
        query = query.filter(Movie.collection_id == collection)
    if person is not None:
        sub = db.query(MoviePerson.movie_id).filter(MoviePerson.person_id == person)
        if role in ("cast", "director"):
            sub = sub.filter(MoviePerson.role == role)
        query = query.filter(Movie.id.in_(sub))
    if studio is not None:
        query = query.filter(Movie.id.in_(db.query(MovieProductionCompany.movie_id)
                                          .filter(MovieProductionCompany.company_id == studio)))
    if category is not None:
        c = db.get(Category, category)
        if c is None:
            fail(404, "not_found", "Category not found.")
        query = query.filter(Movie.id.in_(list(cats.category_movie_ids(db, c)) or [0]))
    if excluded is not None:
        query = query.filter(Movie.excluded_from_game.is_(excluded))
    if playable is not None:
        has = Movie.files.any(MovieFile.status == "ok")
        query = query.filter(has if playable else ~has)
    total = query.count()
    col = SORTS.get(sort, SORTS["title"])
    col = col.desc() if order == "desc" else col.asc()
    rows = query.order_by(col.nulls_last(), Movie.id).offset((page - 1) * page_size).limit(page_size).all()
    return {"total": total, "page": page, "page_size": page_size, "items": [card(m) for m in rows]}


def movie_detail(db: Session, m: Movie) -> dict:
    d = card(m)
    member_cats = []
    for c in cats.ordered_categories(db):
        if m.id in cats.category_movie_ids(db, c):
            member_cats.append({"id": c.id, "name": c.name, "kind": c.kind})
    excluded_in = [{"id": c.id, "name": c.name, "kind": c.kind} for c in
                   db.query(Category).join(MovieCategory, MovieCategory.category_id == Category.id)
                   .filter(MovieCategory.movie_id == m.id, MovieCategory.mode == "exclude")]
    d.update({
        "tmdb_id": m.tmdb_id, "original_title": m.original_title, "release_date": m.release_date,
        "overview": m.overview, "runtime": m.runtime, "backdrop_path": m.backdrop_path,
        "original_language": m.original_language, "popularity": m.popularity, "vote_count": m.vote_count,
        "keywords": m.keywords or [], "match_source": m.match_source, "confirmed": m.confirmed,
        "metadata_updated_at": iso(m.metadata_updated_at),
        "genres": [{"id": g.id, "name": g.name} for g in m.genres],
        "collection": {"id": m.collection.id, "name": m.collection.name} if m.collection else None,
        "directors": [{"id": c.person.id, "name": c.person.name} for c in m.credits if c.role == "director"],
        "cast": [{"id": c.person.id, "name": c.person.name, "character": c.character,
                  "profile_path": c.person.profile_path} for c in m.credits if c.role == "cast"],
        "studios": [{"id": c.id, "name": c.name} for c in m.companies],
        "files": [{"id": f.id, "path": f.path, "filename": f.filename, "size": f.size, "duration": f.duration,
                   "container": f.container, "video_codec": f.video_codec, "audio_codec": f.audio_codec,
                   "width": f.width, "height": f.height, "status": f.status, "error": f.error,
                   "duplicate_of_id": f.duplicate_of_id, "audio_streams": f.audio_streams,
                   "chapters": len(f.chapters or []), "last_scanned_at": iso(f.last_scanned_at)} for f in m.files],
        "categories": member_cats, "excluded_categories": excluded_in,
        "candidates": [{"tmdb_id": c.tmdb_id, "title": c.title, "original_title": c.original_title,
                        "release_year": c.release_year, "overview": c.overview, "poster_path": c.poster_path,
                        "score": c.score, "signals": c.signals} for c in m.candidates],
    })
    return d


@router.get("/movies/{movie_id}")
def get_movie(movie_id: int, db: Session = Depends(get_db)):
    m = db.get(Movie, movie_id)
    if m is None:
        fail(404, "not_found", "Movie not found.")
    return movie_detail(db, m)


class MoviePatch(BaseModel):
    excluded_from_game: bool | None = None


@router.patch("/movies/{movie_id}")
def patch_movie(movie_id: int, body: MoviePatch, db: Session = Depends(get_db)):
    m = db.get(Movie, movie_id)
    if m is None:
        fail(404, "not_found", "Movie not found.")
    if body.excluded_from_game is not None:
        m.excluded_from_game = body.excluded_from_game
    db.commit()
    return card(m)


class MatchSearch(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    year: int | None = Field(None, ge=1880, le=2100)


@router.post("/movies/{movie_id}/match/search")
def match_search(movie_id: int, body: MatchSearch, db: Session = Depends(get_db)):
    m = db.get(Movie, movie_id)
    if m is None:
        fail(404, "not_found", "Movie not found.")
    client = ctx().tmdb_client(db)
    if client is None:
        fail(409, "no_tmdb_key", "Add a TMDB API key in Settings > TMDB first.")
    try:
        results = client.search_movie(body.query, body.year)
        if not results and body.year:
            results = client.search_movie(body.query)
    except TMDBError as e:
        fail(502, e.code, e.message)
    finally:
        client.close()
    f = primary_file(m)
    scored = [score_candidate(r, body.query, body.year, f.duration if f else None) for r in results[:20]]
    scored.sort(key=lambda c: c.score, reverse=True)
    return [{"tmdb_id": c.tmdb_id, "title": c.title, "original_title": c.original_title,
             "release_year": c.release_year, "overview": c.overview, "poster_path": c.poster_path,
             "score": c.score, "signals": c.signals,
             "in_library": db.query(Movie.id).filter(Movie.tmdb_id == c.tmdb_id).scalar()} for c in scored]


class MatchSet(BaseModel):
    tmdb_id: int = Field(gt=0)


@router.post("/movies/{movie_id}/match")
def set_match(movie_id: int, body: MatchSet, db: Session = Depends(get_db)):
    m = db.get(Movie, movie_id)
    if m is None:
        fail(404, "not_found", "Movie not found.")
    client = ctx().tmdb_client(db)
    if client is None:
        fail(409, "no_tmdb_key", "Add a TMDB API key in Settings > TMDB first.")
    cand = db.query(MatchCandidate).filter_by(movie_id=m.id, tmdb_id=body.tmdb_id).first()
    try:
        m = metadata.link_movie(db, m, body.tmdb_id, client, source="manual",
                                confidence=cand.score if cand else None)
        cats.sync_auto_categories(db)
        db.commit()
    except TMDBError as e:
        db.rollback()
        fail(502, e.code, e.message)
    finally:
        client.close()
    return movie_detail(db, m)


@router.post("/movies/{movie_id}/unmatch")
def unmatch(movie_id: int, db: Session = Depends(get_db)):
    """Explicitly clear a match (the only way a confirmed match changes besides choosing another one)."""
    m = db.get(Movie, movie_id)
    if m is None:
        fail(404, "not_found", "Movie not found.")
    for rel in (MovieGenre, MoviePerson, MovieProductionCompany):
        db.query(rel).filter_by(movie_id=m.id).delete()
    m.tmdb_id = None
    m.title = m.parsed_title or m.title
    for attr in ("original_title", "release_date", "release_year", "overview", "runtime", "poster_path",
                 "backdrop_path", "original_language", "popularity", "vote_average", "vote_count", "collection_id",
                 "match_confidence", "match_source", "metadata_updated_at"):
        setattr(m, attr, None)
    m.keywords = []
    m.confirmed = False
    m.metadata_status = "unmatched"
    db.commit()
    return movie_detail(db, m)


@router.post("/movies/{movie_id}/refresh")
def refresh(movie_id: int, db: Session = Depends(get_db)):
    m = db.get(Movie, movie_id)
    if m is None or m.tmdb_id is None:
        fail(404, "not_found", "Movie is not matched.")
    client = ctx().tmdb_client(db)
    if client is None:
        fail(409, "no_tmdb_key", "Add a TMDB API key in Settings > TMDB first.")
    try:
        metadata.refresh_movie(db, m, client)
        cats.sync_auto_categories(db)
        db.commit()
    except TMDBError as e:
        db.rollback()
        fail(502, e.code, e.message)
    finally:
        client.close()
    return movie_detail(db, m)


# ------------------------------------------------------------------ files, issues, duplicates

@router.get("/files/issues")
def file_issues(db: Session = Depends(get_db)):
    rows = db.query(MovieFile).filter(MovieFile.status != "ok").order_by(MovieFile.status, MovieFile.path).limit(2000)
    return [{"id": f.id, "path": f.path, "filename": f.filename, "status": f.status, "error": f.error,
             "movie_id": f.movie_id, "size": f.size, "last_scanned_at": iso(f.last_scanned_at)} for f in rows]


@router.get("/files/duplicates")
def duplicates(db: Session = Depends(get_db)):
    """Movies that have more than one available copy (identical files or different versions)."""
    rows = (db.query(MovieFile.movie_id, func.count(MovieFile.id)).filter(MovieFile.status == "ok",
                                                                          MovieFile.movie_id.isnot(None))
            .group_by(MovieFile.movie_id).having(func.count(MovieFile.id) > 1).all())
    out = []
    for mid, _n in rows:
        m = db.get(Movie, mid)
        if m is None:
            continue
        p = primary_file(m, settings.library(db).duplicate_preference)
        out.append({"movie_id": m.id, "title": m.title, "release_year": m.release_year,
                    "files": [{"id": f.id, "path": f.path, "size": f.size, "height": f.height,
                               "identical_copy": f.duplicate_of_id is not None, "primary": p is not None and
                               f.id == p.id} for f in m.files if f.status == "ok"]})
    return out


# ------------------------------------------------------------------ facets

@router.get("/facets")
def facets(db: Session = Depends(get_db)):
    genres = (db.query(Genre.id, Genre.name, func.count(MovieGenre.movie_id)).join(MovieGenre)
              .group_by(Genre.id).order_by(Genre.name).all())
    decades: dict[int, int] = {}
    for (y,) in db.query(Movie.release_year).filter(Movie.release_year.isnot(None)):
        d = cats.decade_of(y)
        decades[d] = decades.get(d, 0) + 1
    collections = (db.query(TMDBCollection.id, TMDBCollection.name, func.count(Movie.id))
                   .join(Movie, Movie.collection_id == TMDBCollection.id).group_by(TMDBCollection.id)
                   .order_by(TMDBCollection.name).all())
    studios = (db.query(ProductionCompany.id, ProductionCompany.name, func.count(MovieProductionCompany.movie_id))
               .join(MovieProductionCompany).group_by(ProductionCompany.id)
               .order_by(func.count(MovieProductionCompany.movie_id).desc(), ProductionCompany.name).limit(300).all())
    status_counts = dict(db.query(Movie.metadata_status, func.count(Movie.id)).group_by(Movie.metadata_status).all())
    return {"genres": [{"id": i, "name": n, "count": c} for i, n, c in genres],
            "decades": [{"id": d, "name": f"{d}s", "count": c} for d, c in sorted(decades.items())],
            "collections": [{"id": i, "name": n, "count": c} for i, n, c in collections],
            "studios": [{"id": i, "name": n, "count": c} for i, n, c in studios],
            "status_counts": status_counts}


@router.get("/people")
def people(db: Session = Depends(get_db), q: str = "", role: str | None = None, limit: int = Query(30, le=100)):
    query = (db.query(Person.id, Person.name, Person.profile_path, MoviePerson.role,
                      func.count(MoviePerson.movie_id).label("n"))
             .join(MoviePerson, MoviePerson.person_id == Person.id)
             .group_by(Person.id, MoviePerson.role))
    if q:
        query = query.filter(Person.name.ilike(f"%{q.strip()}%"))
    if role in ("cast", "director"):
        query = query.filter(MoviePerson.role == role)
    rows = query.order_by(func.count(MoviePerson.movie_id).desc(), Person.name).limit(limit).all()
    return [{"id": i, "name": n, "profile_path": p, "role": r, "count": c} for i, n, p, r, c in rows]
