# Architecture

One local process serves everything: a FastAPI backend (REST + WebSocket + media), the built React interface, and
background jobs. There are no other services to run.

```
backend/app/
  main.py              app factory: middleware (Host/Origin/LAN guards), routers, SPA serving, lifespan
  __main__.py          launcher: migrations, bind address (localhost unless LAN is on), restart loop, browser
  config.py            process configuration (.env / MR_* variables)
  db.py, models.py     SQLAlchemy 2 models on SQLite (WAL, foreign keys) - migrations in backend/alembic
  security.py          sessions, host/player roles, password hashing, path guards
  api/                 routers: auth, library, categories, players, games, media, system, stats, ws
  services/
    filename_parser    download-style names -> title/year hint
    media_tools        FFmpeg/FFprobe discovery and probing
    scanner            discovery, change detection, fingerprints, background job runner
    tmdb, matcher      TMDB client (cache, retries, rate limit) and confidence scoring
    metadata           applying TMDB records, merging duplicate movies
    categories         automatic/custom categories and game filters
    selection          uniform random selection and repeat rules
    timestamps         allowed windows, validation, random starts
    streaming          per-round FFmpeg HLS clips
    games, scoring     round state machine, guesses, hints, reveal, points, role-filtered views
    realtime           WebSocket hub
    stats, maintenance statistics; backup/restore/integrity; LAN addresses
frontend/src/          React + TypeScript + Tailwind; pages/, components/, lib/
```

## Library scanning

1. Walk enabled folders (skipping excluded folders, hidden folders and names like *Extras*).
2. Files whose size and modification time are unchanged are skipped (incremental scans).
3. New/changed files are probed with FFprobe (duration, codecs, resolution, audio tracks, chapters) and
   fingerprinted (SHA-1 of size + first/middle/last MiB).
4. A new file whose fingerprint matches a file that just went missing is a **move/rename**: the existing record
   (and its movie link and history) moves to the new path. A fingerprint matching an available file is an
   **identical duplicate**. Otherwise a new movie is created from the parsed name.
5. Files that are unreadable (permissions), unplayable (no video, zero length, FFprobe errors) or too short are
   recorded with the reason. Files that disappear are marked missing, never deleted from history.
6. Unmatched movies are matched with TMDB; automatic categories are synced.

Files are opened read-only. Nothing renames, moves, writes or deletes anything in a movie folder.

## Matching

For each candidate from TMDB search (with and without the year, plus title variants): title similarity (character
ratio, word overlap, containment, leading articles ignored) against title, original title and alternative titles;
year agreement; the difference between the file's measured duration and TMDB's runtime; a tiny popularity tiebreak.
The top candidates are refined with full details. A match is accepted automatically only when the score clears the
threshold (default 85) **and** leads the runner-up by the margin (default 8). Confirmed matches (automatic or
manual) are never re-matched by a scan; they change only when the host picks another match or removes it.

## Video streaming

Browsers cannot play MKV/AVI/WMV containers, HEVC, 10-bit H.264, AC-3/DTS audio reliably. Instead of transcoding
whole movies, each round encodes only what it needs:

```
ffmpeg -ss <start> -i <movie> -t <clip or continuous limit> -map 0:v:0 -map 0:a:<preferred>
       square pixels, <= 1080p, yuv420p, libx264, AAC stereo, no metadata/chapters/subtitles
       -f hls (2-second segments, keyframe every segment)  ->  data/cache/streams/<round token>/
```

- Input seeking with re-encoding is frame-accurate (verified by a test that compares decoded frames).
- One encode per round, shared by all devices; segments are plain files served with HTTP Range support.
- Clips are ready in seconds; continuous rounds become playable after three segments and keep encoding ahead.
- hls.js plays the stream in Chrome/Edge/Firefox and most TV browsers; Safari/iOS play HLS natively.
- The stream folder is deleted when the round closes and at startup.

## Real-time sync

Every change goes through the REST API and then the hub pushes a fresh, **role-filtered snapshot** to every
WebSocket connection of that game. Reconnecting is therefore just reconnecting. Playback is shared as
`state`, `position_base`, `started_at` (server clock) and `length`; each screen estimates its offset to the server
clock with ping/pong and keeps its `<video>` within 0.8 s of the shared position. A timer on the server marks a
clip as ended at the right moment for everyone.

## Spoiler protection

- Players see rounds only through a random 32-character token; stream files are named `index.m3u8`/`s00000.ts`.
- The player view of a round contains no title, year, poster, ids, file name or other players' guess texts until
  the host reveals. Hints are deliberate, host-triggered disclosures.
- Clips carry no container metadata, chapters or subtitles.
- The page title is always "Movie Roulette". The host screen blurs the answer unless the host chooses to peek.
- Tests check the player API, WebSocket messages and the rendered page for leaks.

## Security

- Binds to `127.0.0.1` unless *Allow devices on my network* is on (and then restarts on the LAN bind address);
  non-loopback requests are refused while LAN access is off.
- Host role: a launch token generated at each start and opened by the start script (accepted only from the PC itself), the host password (PBKDF2-SHA256), or - only when no
  password is set - a browser on the PC itself. Player role: join code + name. Separate HttpOnly, SameSite=Strict
  cookies; sessions are stored hashed.
- Unknown `Host` names are refused (DNS rebinding); cross-origin state-changing requests and non-JSON bodies are
  refused; WebSocket origins are checked; failed logins/joins are rate limited.
- No endpoint takes a file path to read. Media is found by round token, and the source file must be inside an
  enabled, configured movie folder. Folder inputs are validated; image names are whitelisted.
- The TMDB key is stored outside the database, encrypted with Windows DPAPI, never returned by the API and redacted
  from logs.
