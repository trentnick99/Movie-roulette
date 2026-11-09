# Movie Roulette

A movie trivia game built from your own movie collection. Movie Roulette scans the movie folders on your
Windows PC, identifies every film with [TMDB](https://www.themoviedb.org/), sorts them into categories, and then
plays a random scene from a random movie. Everyone guesses - on the TV, or on their own phone.

It runs entirely on your computer. Your movie files are only ever read: never uploaded, never modified.

## What it does

- **Library** - scans any number of folders (MKV, MP4, AVI, MOV, M4V, WMV, WebM, MPEG-TS and anything else FFmpeg
  reads), measures every file with FFprobe, notices new, changed, moved, renamed, duplicate, missing, corrupt and
  unreadable files, and keeps everything in a local SQLite database.
- **TMDB matching** - cleans up download-style file names, searches TMDB, and scores each candidate on title,
  original and alternative titles, year and the file's real running time. Confident matches are accepted
  automatically; anything uncertain waits for you with the best candidates ready to pick. A match you confirmed is
  never replaced by a rescan.
- **Categories** - genres, decades, franchises (TMDB collections) and studios are created automatically; browse by
  actor or director; build your own categories from rules (genres, decades, people, studios, rating, "movies we
  haven't played") and hand-picked movies; hide, reorder, and keep single movies out of any category.
- **Seven game modes** - Category Roulette, Full Library Roulette, Franchise Challenge, Decade Challenge, Director or
  Actor Challenge, Custom Game (any filters, AND or OR) and Difficulty Challenge.
- **Choosing the scene** - the guesser drags along a film-strip timeline, types hours/minutes/seconds, or asks for
  a random start (and another, and another). Start times are always inside the file's measured length, leave room
  for the clip, and can skip opening/end credits (using chapter markers when they name the credits).
- **Playback** - fixed clips (10/15/30/45/60 seconds or any length) that stop by themselves, or continuous playback
  until someone gets it, the host stops it, or a time limit is reached. Frame-accurate start, replays, volume,
  full screen.
- **Multiplayer** - players and teams, turns, guesses from phones, host judging, hints, bonuses (year, actor, team),
  manual adjustments, tie-aware scoreboards, final results, full game history and statistics.
- **Spoiler protection** - players' screens, URLs, page titles and network traffic never contain the movie's title,
  poster, year, TMDB id or file name until the host reveals it. Even the host screen hides the answer until you
  choose to peek, so it can sit on the TV.

## Install on Windows

**Easiest: a release download.** On the GitHub page open **Releases**, download `MovieRoulette-vX.Y.Z-windows.zip`,
and extract it somewhere permanent (for example `C:\MovieRoulette`). The web interface is pre-built in release
downloads, so you do not need Node.js.

**From a git clone.** `git clone https://github.com/trentnick99/movie-roulette.git` works too; the first start then also
needs [Node.js LTS](https://nodejs.org/) to build the interface once.

You also need:

| What | Why | How |
|---|---|---|
| Python 3.11 or newer | runs Movie Roulette | the start script offers to install it with `winget`, or get it from python.org (tick "Add python.exe to PATH") |
| FFmpeg | reads and plays your movies | the start script offers `winget install Gyan.FFmpeg`, or unzip an FFmpeg build into `tools\ffmpeg` |
| A free TMDB API key | identifies your movies | see below |

## Start it

Double-click **`START_MOVIE_ROULETTE.bat`**. It:

1. checks for Python, FFmpeg and (only for git clones) Node.js, and explains what to do if one is missing;
2. creates a private Python environment in `.venv` and installs the packages (first start, and after updates);
3. builds the web interface if it is not built yet;
4. updates the database (migrations run automatically) and starts the server;
5. opens Movie Roulette in your browser, already signed in as the host.

Keep the black window open while you play; closing it stops Movie Roulette. Starting it again while it runs just
opens the browser. The address is `http://127.0.0.1:8765/`.

## Set up the library

1. **TMDB key** - make a free account at themoviedb.org, open *Settings > API*, request an API key (personal use),
   and copy either the **API Key** or the **API Read Access Token**. In Movie Roulette open
   *Settings > TMDB*, paste it, and choose *Check and save*. The key is checked with TMDB first, then stored only on
   your PC (`data\secrets.json`, encrypted for your Windows account). It is never shown again and never committed.
2. **Movie folders** - *Settings > Movie library > Add folder* (type the path or *Browse*). Exclude sub-folders
   you do not want (*Edit*). Folders named Extras, Featurettes, Trailers, Samples ... are skipped automatically, and
   files shorter than 20 minutes are treated as extras (both adjustable).
3. **Scan now.** The first scan probes every file and asks TMDB about each movie; later scans only look at what
   changed. Turn on automatic rescans in the same screen if you like.
4. **Check uncertain matches** - the home screen and *Movie Library > Status: Needs a match check* list movies that
   were not matched with enough confidence. Open one, choose *Choose the right movie*, search if needed, and pick
   *This is it*. Two files of the same film are grouped as one movie.

## Play

1. *Play Game* - choose who plays (or play in teams), a mode, the categories, difficulty, clip or continuous
   playback, the number of rounds and the repeat rule. The bar at the bottom shows how many movies can come up.
2. *Create game* opens the host screen with the **join code**.
3. *Spin the first round* - a random eligible movie is chosen. The player whose turn it is picks where the scene
   starts (on their phone, or you pick on the host screen).
4. *Play the scene*. Players type guesses on their phones, or call them out - mark the player correct with
   *Someone said it out loud*.
5. Mark guesses *Correct* or *Wrong*, give hints, then *Reveal the movie*, give bonuses, and *Close round*.
6. *Finish game* shows the final results. Everything is kept in *Game History* and *Statistics*.

### Phones, tablets and TVs

Turn on *Settings > Network > Allow devices on my network* and choose *Restart now*. The host screen then shows a
QR code and an address like `http://192.168.1.20:8765/join`. Phones on the same Wi-Fi open it and enter the join
code and their name. Windows may ask whether Python may use private networks - allow it.

Players only get the player screen. To control games from another device (a tablet as a remote, say), set a host
password in *Settings > Network* first; without one, host controls only work on the PC itself.

## Documentation

- [docs/CONFIGURATION.md](docs/CONFIGURATION.md) - every setting, the `.env` options, where data lives, backups.
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) - common problems and fixes.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) - how it works: scanning, matching, streaming, sync, security.
- [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) - running from source, tests, migrations, releases.

## Credits

Movie data and images come from TMDB. This product uses the TMDB API but is not endorsed or certified by TMDB.
Movie Roulette only plays files you already own; it does not download, stream from, or decrypt anything.
