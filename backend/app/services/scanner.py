"""Library scanning engine.

Discovers video files under the configured directories, measures them with FFprobe, detects new / changed /
moved / renamed / duplicate / missing / corrupt / inaccessible files, parses names, and matches unmatched movies
against TMDB. Original files are only ever *read* (stat, a few MB for the fingerprint, ffprobe).
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from .. import db as dbm
from ..models import GameRound, LibraryDirectory, LibraryScan, MatchCandidate, Movie, MovieFile
from ..security import is_within, norm
from . import categories, settings
from .filename_parser import looks_like_episode, parse_movie_path
from .matcher import find_matches
from .media_tools import IGNORED_EXTENSIONS, VIDEO_EXTENSIONS, ProbeResult, probe
from .metadata import link_movie
from .tmdb import TMDBClient, TMDBError

log = logging.getLogger(__name__)

FINGERPRINT_CHUNK = 1024 * 1024


def fingerprint(path: str, size: int) -> str:
    """Fast content identity: size + first, middle and last MiB. Identifies moved/renamed files and duplicates
    without reading whole movies."""
    h = hashlib.sha1(str(size).encode())
    with open(path, "rb") as f:
        h.update(f.read(FINGERPRINT_CHUNK))
        if size > 3 * FINGERPRINT_CHUNK:
            f.seek(size // 2)
            h.update(f.read(FINGERPRINT_CHUNK))
            f.seek(max(0, size - FINGERPRINT_CHUNK))
            h.update(f.read(FINGERPRINT_CHUNK))
    return h.hexdigest()


@dataclass
class Discovered:
    path: str
    directory_id: int
    size: int = 0
    mtime: float = 0.0
    error: str | None = None


@dataclass
class ScanStats:
    counts: dict[str, int] = field(default_factory=lambda: {
        "files_seen": 0, "added": 0, "updated": 0, "unchanged": 0, "moved": 0, "duplicates": 0, "missing": 0,
        "corrupt": 0, "inaccessible": 0, "unsupported": 0, "episodes": 0, "matched": 0, "needs_review": 0,
        "match_errors": 0})

    def inc(self, k: str, n: int = 1) -> None:
        self.counts[k] = self.counts.get(k, 0) + n


def _skip_dir(name: str, skip_names: set[str]) -> bool:
    return name.startswith(".") or name.lower() in skip_names


def discover(directories: list[LibraryDirectory], skip_names: list[str],
             cancel: threading.Event | None = None) -> tuple[list[Discovered], set[int]]:
    """Walk all enabled directories. Returns the files found and the ids of directories that were reachable."""
    out: list[Discovered] = []
    reachable: set[int] = set()
    skip = {s.lower() for s in skip_names}
    for d in directories:
        if not os.path.isdir(d.path):
            log.warning("movie directory not reachable: %s", d.path)
            continue
        reachable.add(d.id)
        excluded = [norm(x) for x in (d.excluded_paths or [])]

        def onerror(err: OSError) -> None:
            log.warning("cannot read folder %s: %s", getattr(err, "filename", "?"), err)

        for root, dirs, files in os.walk(d.path, onerror=onerror, followlinks=False):
            if cancel is not None and cancel.is_set():
                return out, reachable
            dirs[:] = sorted(x for x in dirs if not _skip_dir(x, skip)
                             and not any(is_within(os.path.join(root, x), e) for e in excluded))
            for name in sorted(files):
                ext = os.path.splitext(name)[1].lower()
                if ext not in VIDEO_EXTENSIONS:
                    continue
                full = os.path.join(root, name)
                item = Discovered(path=os.path.abspath(full), directory_id=d.id)
                try:
                    st = os.stat(full)
                    item.size, item.mtime = st.st_size, st.st_mtime
                    if not os.access(full, os.R_OK):
                        item.error = "permission denied"
                except OSError as e:
                    item.error = str(e)
                out.append(item)
    return out, reachable


def unsupported_extension(name: str) -> bool:
    ext = os.path.splitext(name)[1].lower()
    return ext not in VIDEO_EXTENSIONS and ext not in IGNORED_EXTENSIONS


class Scanner:
    def __init__(self, session: Session, scan: LibraryScan, *, probe_fn: Callable[[str], ProbeResult] = probe,
                 tmdb_client: TMDBClient | None = None, cancel: threading.Event | None = None,
                 progress: Callable[[LibraryScan], None] | None = None):
        self.db = session
        self.scan = scan
        self.probe = probe_fn
        self.tmdb = tmdb_client
        self.cancel = cancel or threading.Event()
        self.stats = ScanStats()
        self.progress = progress
        self.lib = settings.library(session)
        self._missing_now: set[int] = set()

    # --------------------------------------------------------------- helpers
    def _commit_progress(self, phase: str | None = None, current: int | None = None, total: int | None = None):
        if phase:
            self.scan.phase = phase
        if current is not None:
            self.scan.progress_current = current
        if total is not None:
            self.scan.progress_total = total
        self.scan.counts = dict(self.stats.counts)
        self.db.commit()
        if self.progress:
            self.progress(self.scan)

    def _apply_probe(self, f: MovieFile, res: ProbeResult) -> None:
        f.duration = res.duration
        f.container = res.container
        f.video_codec = res.video_codec
        f.audio_codec = res.audio_codec
        f.width, f.height, f.pix_fmt = res.width, res.height, res.pix_fmt
        f.audio_streams = res.audio_streams
        f.chapters = res.chapters

    def _new_movie_for(self, path: str) -> Movie:
        parsed = parse_movie_path(path)
        m = Movie(title=parsed.title, parsed_title=parsed.title, parsed_year=parsed.year,
                  release_year=None, metadata_status="unmatched")
        self.db.add(m)
        self.db.flush()
        return m

    # --------------------------------------------------------------- phases
    def run(self) -> LibraryScan:
        dirs = self.db.query(LibraryDirectory).filter_by(enabled=True).all()
        self._commit_progress("discovering", 0, 0)
        found, reachable = discover(dirs, self.lib.skip_folder_names, self.cancel)
        self.stats.inc("files_seen", len(found))
        self._commit_progress("inspecting", 0, len(found))
        seen_paths: set[str] = set()
        new_files: list[tuple[MovieFile, Discovered]] = []
        for i, item in enumerate(found):
            if self.cancel.is_set():
                break
            seen_paths.add(item.path)
            f = self.db.query(MovieFile).filter_by(path=item.path).first()
            if item.error:
                self._mark_inaccessible(f, item)
            elif f is not None and f.size == item.size and abs(f.mtime - item.mtime) < 1 and f.status == "ok":
                f.last_scanned_at = datetime.now(UTC)
                if not self._set_aside_episode(f):
                    self.stats.inc("unchanged")
            else:
                created = self._inspect(f, item)
                if created is not None:
                    new_files.append((created, item))
            if i % 10 == 0:
                self._commit_progress(current=i + 1)
        self._commit_progress(current=len(found))
        if not self.cancel.is_set():
            self._mark_missing(seen_paths, reachable, dirs)
            self._resolve_new(new_files)
            self._cleanup_orphans()
            self.db.commit()
        if self.tmdb is not None and self.tmdb.configured and not self.cancel.is_set():
            self.match_pending()
        if not self.cancel.is_set():
            categories.sync_auto_categories(self.db)
        for d in dirs:
            if d.id in reachable:
                d.last_scanned_at = datetime.now(UTC)
        self.scan.status = "cancelled" if self.cancel.is_set() else "completed"
        self.scan.finished_at = datetime.now(UTC)
        self._commit_progress("done")
        return self.scan

    def _mark_inaccessible(self, f: MovieFile | None, item: Discovered) -> None:
        self.stats.inc("inaccessible")
        if f is None:
            f = MovieFile(path=item.path, filename=os.path.basename(item.path), directory_id=item.directory_id)
            self.db.add(f)
        f.status, f.error = "inaccessible", f"Cannot read file: {item.error}"
        f.last_scanned_at = datetime.now(UTC)
        self.db.flush()

    def _inspect(self, f: MovieFile | None, item: Discovered) -> MovieFile | None:
        """Probe a new or changed file. Returns the record when it is newly created and playable."""
        res = self.probe(item.path)
        now = datetime.now(UTC)
        is_new = f is None
        if f is None:
            f = MovieFile(path=item.path, filename=os.path.basename(item.path), directory_id=item.directory_id,
                          first_seen=now)
            self.db.add(f)
        f.size, f.mtime, f.last_scanned_at = item.size, item.mtime, now
        try:
            f.fingerprint = fingerprint(item.path, item.size)
        except OSError as e:
            f.status, f.error = "inaccessible", f"Cannot read file: {e}"
            self.stats.inc("inaccessible")
            self.db.flush()
            return None
        if not res.ok:
            f.status = "corrupt" if res.error and "no video" not in res.error else "unsupported"
            f.error = res.error or "unreadable media"
            self.stats.inc("corrupt" if f.status == "corrupt" else "unsupported")
            self.db.flush()
            return None
        self._apply_probe(f, res)
        if (res.duration or 0) < self.lib.min_duration_minutes * 60:
            f.status = "unsupported"
            f.error = (f"Shorter than {self.lib.min_duration_minutes:g} minutes - treated as a sample, trailer or "
                       "extra (change in Settings > Library).")
            self.stats.inc("unsupported")
            self.db.flush()
            return None
        f.status, f.error = "ok", None
        self.db.flush()
        if self._set_aside_episode(f):
            return None
        if is_new or f.movie_id is None:
            return f
        self.stats.inc("updated")
        return None

    def _set_aside_episode(self, f: MovieFile) -> bool:
        """TV episodes are not movies: record them as unsupported (with the reason) so they stay out of matching,
        the review queue and games. A movie the host already confirmed is left alone."""
        if not self.lib.skip_tv_episodes:
            return False
        marker = looks_like_episode(f.path)
        if not marker:
            return False
        movie = self.db.get(Movie, f.movie_id) if f.movie_id else None
        if movie is not None and movie.confirmed:
            return False
        f.status = "unsupported"
        f.error = (f"Looks like a TV episode ({marker}), not a movie. "
                   "To include episodes, turn off 'Skip TV episodes' in Settings > Library.")
        f.movie_id, f.duplicate_of_id = None, None
        self.stats.inc("episodes")
        self.db.flush()
        return True

    def _mark_missing(self, seen: set[str], reachable: set[int], dirs: list[LibraryDirectory]) -> None:
        enabled_roots = [d.path for d in dirs]
        for f in self.db.query(MovieFile).filter(MovieFile.status != "missing").all():
            if f.path in seen:
                continue
            in_enabled = any(is_within(f.path, r) for r in enabled_roots) if enabled_roots else False
            if f.directory_id in reachable or not in_enabled:
                f.status = "missing"
                f.error = ("File no longer found." if in_enabled
                           else "Its movie folder was removed from the library.")
                self.stats.inc("missing")
                self._missing_now.add(f.id)
            elif not os.path.exists(f.path):
                f.status, f.error = "missing", "Drive or folder is not reachable right now."
                self.stats.inc("missing")
                self._missing_now.add(f.id)
        self.db.flush()

    def _resolve_new(self, new_files: list[tuple[MovieFile, Discovered]]) -> None:
        """New playable files: a moved/renamed known file, a duplicate copy, or a brand-new movie."""
        for f, _item in new_files:
            moved = (self.db.query(MovieFile)
                     .filter(MovieFile.id != f.id, MovieFile.fingerprint == f.fingerprint,
                             MovieFile.status == "missing", MovieFile.movie_id.isnot(None)).first())
            if moved is not None:
                # Same content found at a new path: keep the old record (its movie link, history) on the new path.
                old_path = moved.path
                self.db.delete(f)
                self.db.flush()
                for attr in ("path", "filename", "directory_id", "size", "mtime", "duration", "container",
                             "video_codec", "audio_codec", "width", "height", "pix_fmt", "audio_streams",
                             "chapters", "last_scanned_at"):
                    setattr(moved, attr, getattr(f, attr))
                moved.status, moved.error = "ok", None
                if moved.id in self._missing_now:  # it was not really missing, it moved
                    self._missing_now.discard(moved.id)
                    self.stats.inc("missing", -1)
                self.db.flush()
                log.info("file moved/renamed: %s -> %s", os.path.basename(old_path), moved.filename)
                self.stats.inc("moved")
                continue
            twin = (self.db.query(MovieFile)
                    .filter(MovieFile.id != f.id, MovieFile.fingerprint == f.fingerprint,
                            MovieFile.status == "ok", MovieFile.movie_id.isnot(None)).first())
            if twin is not None:
                f.movie_id = twin.movie_id
                f.duplicate_of_id = twin.duplicate_of_id or twin.id
                self.stats.inc("duplicates")
                self.stats.inc("added")
                continue
            parsed = parse_movie_path(f.path)
            if parsed.part and parsed.part > 1:  # 'CD2' of a split movie: attach to part 1 if present
                sib = self._find_part_one(f, parsed.title)
                if sib is not None:
                    f.movie_id, f.duplicate_of_id = sib.movie_id, sib.id
                    self.stats.inc("added")
                    continue
            f.movie_id = self._new_movie_for(f.path).id
            self.stats.inc("added")
        self.db.flush()

    def _find_part_one(self, f: MovieFile, title: str) -> MovieFile | None:
        from .filename_parser import normalize_title

        folder = os.path.dirname(f.path)
        for other in self.db.query(MovieFile).filter(MovieFile.id != f.id, MovieFile.movie_id.isnot(None),
                                                      MovieFile.status == "ok"):
            if os.path.dirname(other.path) == folder:
                p = parse_movie_path(other.path)
                if normalize_title(p.title) == normalize_title(title) and (p.part or 1) == 1:
                    return other
        return None

    def _cleanup_orphans(self) -> None:
        used = {m for (m,) in self.db.query(GameRound.movie_id).distinct()}
        for m in self.db.query(Movie).filter(~Movie.files.any()).all():
            if m.id not in used:
                self.db.delete(m)
        self.db.flush()

    def match_pending(self) -> None:
        pending = (self.db.query(Movie)
                   .filter(Movie.metadata_status.in_(("unmatched", "failed", "needs_review")),
                           Movie.confirmed.is_(False))
                   .filter(Movie.files.any(MovieFile.status == "ok")).all())
        self._commit_progress("matching", 0, len(pending))
        tm = settings.tmdb(self.db)
        for i, movie in enumerate(pending):
            if self.cancel.is_set():
                break
            try:
                self.match_movie(movie, cast_limit=tm.cast_limit)
            except TMDBError as e:
                if e.code in ("auth", "no_key"):
                    self.scan.message = e.message
                    self.db.rollback()
                    break
                movie.metadata_status = "failed"
                self.stats.inc("match_errors")
                log.warning("TMDB lookup failed for %r: %s", movie.parsed_title, e.message)
            self.db.commit()
            self._commit_progress(current=i + 1)

    def refresh_metadata(self, older_than_days: float = 0) -> None:
        """Re-download TMDB metadata for matched movies (incremental: only records older than the limit).
        The chosen TMDB match itself is never changed."""
        from datetime import timedelta

        from .metadata import refresh_movie

        cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
        movies = [m for m in self.db.query(Movie).filter(Movie.metadata_status == "matched",
                                                          Movie.tmdb_id.isnot(None)).all()
                  if m.metadata_updated_at is None or older_than_days <= 0
                  or (m.metadata_updated_at if m.metadata_updated_at.tzinfo
                      else m.metadata_updated_at.replace(tzinfo=UTC)) < cutoff]
        self._commit_progress("refreshing", 0, len(movies))
        tm = settings.tmdb(self.db)
        for i, m in enumerate(movies):
            if self.cancel.is_set():
                break
            try:
                refresh_movie(self.db, m, self.tmdb, cast_limit=tm.cast_limit)
                self.stats.inc("updated")
            except TMDBError as e:
                if e.code in ("auth", "no_key"):
                    self.scan.message = e.message
                    break
                self.stats.inc("match_errors")
            self.db.commit()
            self._commit_progress(current=i + 1)

    def match_movie(self, movie: Movie, cast_limit: int = 15) -> Movie:
        assert self.tmdb is not None
        if movie.confirmed:  # never silently replace a confirmed match
            return movie
        primary = primary_file(movie)
        res = find_matches(self.tmdb, movie.parsed_title or movie.title, movie.parsed_year,
                           primary.duration if primary else None, threshold=self.lib.auto_accept_confidence,
                           margin=self.lib.auto_accept_margin)
        self.db.query(MatchCandidate).filter_by(movie_id=movie.id).delete()
        if res.auto_accept and res.best is not None:
            movie = link_movie(self.db, movie, res.best.tmdb_id, self.tmdb, source="auto",
                               confidence=res.best.score, cast_limit=cast_limit)
            self.stats.inc("matched")
            return movie
        for c in res.candidates:
            self.db.add(MatchCandidate(movie_id=movie.id, tmdb_id=c.tmdb_id, title=c.title,
                                       original_title=c.original_title, release_year=c.release_year,
                                       overview=c.overview, poster_path=c.poster_path, popularity=c.popularity,
                                       score=c.score, signals=c.signals))
        movie.metadata_status = "needs_review"
        movie.match_confidence = res.best.score if res.best else 0
        self.stats.inc("needs_review")
        self.db.flush()
        return movie


def primary_file(movie: Movie, preference: str = "highest_resolution") -> MovieFile | None:
    """The copy used for playback: an available, non-duplicate file chosen by the duplicate preference
    (Settings > Library): highest resolution, largest file, or the first copy found."""
    ok = [f for f in movie.files if f.status == "ok" and f.duration and f.duplicate_of_id is None]
    if not ok:
        ok = [f for f in movie.files if f.status == "ok" and f.duration]
    if not ok:
        return None
    if preference == "largest":
        key = lambda f: (-(f.size or 0), f.id)  # noqa: E731
    elif preference == "first_found":
        key = lambda f: (f.id,)  # noqa: E731
    else:
        key = lambda f: (-(f.height or 0), -(f.size or 0), f.id)  # noqa: E731
    return sorted(ok, key=key)[0]


# ------------------------------------------------------------------ background job runner

def _default_probe() -> Callable[[str], ProbeResult]:
    return probe  # looked up at call time (lets tests substitute a fake prober)


class JobRunner:
    """Runs one library job (scan / match / refresh) at a time in a background thread."""

    def __init__(self):
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.cancel = threading.Event()
        self.current_scan_id: int | None = None

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, trigger: str, client_factory: Callable[[Session], TMDBClient | None],
              probe_fn: Callable[[str], ProbeResult] | None = None, mode: str = "scan",
              on_done: Callable[[], None] | None = None) -> int:
        with self._lock:
            if self.busy:
                raise RuntimeError("A library scan is already running.")
            with dbm.session_scope() as s:
                scan = LibraryScan(trigger=trigger, status="running", phase="starting")
                s.add(scan)
                s.flush()
                scan_id = scan.id
            self.cancel = threading.Event()
            self.current_scan_id = scan_id
            self._thread = threading.Thread(target=self._run, name=f"scan-{scan_id}", daemon=True,
                                            args=(scan_id, client_factory, probe_fn, mode, on_done))
            self._thread.start()
            return scan_id

    def _run(self, scan_id, client_factory, probe_fn, mode, on_done) -> None:
        s = dbm.new_session()
        client = None
        try:
            scan = s.get(LibraryScan, scan_id)
            client = client_factory(s)
            sc = Scanner(s, scan, probe_fn=probe_fn or _default_probe(), tmdb_client=client, cancel=self.cancel)
            if mode in ("match", "refresh"):
                if client is None:
                    raise RuntimeError("No TMDB API key is configured.")
                if mode == "match":
                    sc.match_pending()
                else:
                    sc.refresh_metadata()
                categories.sync_auto_categories(s)
                scan.status = "cancelled" if self.cancel.is_set() else "completed"
                scan.finished_at = datetime.now(UTC)
                sc._commit_progress("done")
            else:
                sc.run()
        except Exception as e:  # report, never crash the server
            log.exception("library job failed")
            s.rollback()
            scan = s.get(LibraryScan, scan_id)
            if scan is not None:
                scan.status, scan.message = "failed", f"{type(e).__name__}: {e}"[:500]
                scan.finished_at = datetime.now(UTC)
                s.commit()
        finally:
            if client is not None:
                client.close()
            s.close()
            if on_done:
                try:
                    on_done()
                except Exception:
                    log.exception("scan completion callback failed")

    def wait(self, timeout: float | None = None) -> None:
        t = self._thread
        if t is not None:
            t.join(timeout)

    def stop(self) -> None:
        self.cancel.set()


runner = JobRunner()
