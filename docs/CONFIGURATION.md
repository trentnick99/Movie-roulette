# Configuration

Almost everything is configured in the app (*Settings*). Values are validated before they are saved and stored in
the database, so they survive updates and are included in backups.

## Settings screens

| Screen | Settings |
|---|---|
| Movie library | folders (add, edit path, exclude sub-folders, turn scanning on/off, remove), automatic rescan interval, minimum length (shorter files count as extras), auto-accept confidence for TMDB matches, folder names to skip, which copy to play when a movie has several |
| TMDB | API key (checked before saving), connection test, language, adult titles in searches, how long TMDB answers are cached, cast members stored, refresh all metadata, clear the cache |
| Playback | default clip or continuous, default clip length, continuous time limit, default volume, stream quality (1080p/720p/480p), clip preparation speed, preferred audio language, video on players' phones |
| Game rules | manual and/or random timestamps, players choosing on their own phone, host override, skip opening/end credits (seconds, or chapter markers), earliest/latest start, difficulty presets (clip length, replays, hints, allowed part of the movie in %, points multiplier), default difficulty, rounds, who may guess, hints, default repeat rule, default categories |
| Scoring | correct answer, first-try bonus, year and actor bonuses, team bonus, hint penalty, wrong-guess penalty, difficulty multiplier on/off |
| Network | allow devices on the network (needs a restart, button provided), addresses, host password, connected devices, sign out all players |
| Database | back up now, restore a backup (a safety backup is made first), integrity check, reset played history, version information |
| Appearance | theme, animations, poster size, ratings in the library |

Changes to scoring and game rules apply to games created afterwards; a running game keeps the rules it started with.

### Defaults worth knowing

- Scoring: correct answer 10, first-try bonus 5, year bonus 3, actor bonus 3, hint penalty 2, multiplier easy x1,
  medium x1.5, hard x2.
- Difficulty: easy = 45 s clips, 3 replays, 3 hints, starts between 10% and 90% of the movie; medium = 30 s,
  1 replay, 1 hint; hard = 10 s, no replays, no hints. Difficulty never guesses how recognisable a scene is.
- Repeats: never within a game. Other rules: since Movie Roulette started, last 30 days, last 90 days, or allow.
  If a selection runs out, you are told why and offered a choice - a movie outside the chosen categories is never
  picked on its own.

## The `.env` file

Optional. Copy `.env.example` to `.env` in the project folder to change process-level options:

| Variable | Default | Meaning |
|---|---|---|
| `MR_PORT` | `8765` | port to listen on |
| `MR_DATA_DIR` | `data` in the project folder | database, caches, backups, logs, secrets |
| `MR_LAN_BIND` | `0.0.0.0` | address used when network access is on (otherwise always `127.0.0.1`) |
| `MR_FFMPEG_PATH`, `MR_FFPROBE_PATH` | PATH, then `tools\ffmpeg\bin` | explicit FFmpeg locations |
| `MR_TMDB_API_KEY` | - | key from the environment instead of the Settings screen |
| `MR_LOG_LEVEL` | `INFO` | `DEBUG` for more detail |

Never commit `.env` (it is in `.gitignore`).

## Where things are stored

Everything lives in the data folder (`data\` by default):

| Path | Contents |
|---|---|
| `movie_roulette.db` | SQLite database: library, metadata, categories, players, games, settings |
| `secrets.json` | TMDB key and host password hash (DPAPI-encrypted on Windows, owner-only elsewhere) |
| `cache\images` | TMDB posters/backdrops, downloaded once |
| `cache\streams` | temporary per-round clips, removed when a round closes and at every start |
| `backups\` | database backups |
| `logs\movie_roulette.jsonl` | structured log (secrets are redacted) |

To move Movie Roulette to another PC, copy the project folder including `data\` (re-enter the TMDB key there if the
Windows account differs, because the encrypted key is tied to the account).

## Database migrations

The schema is managed with Alembic and upgraded automatically at every start. To run migrations by hand:

```
cd backend
..\.venv\Scripts\python -m app migrate
```
