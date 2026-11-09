# Troubleshooting

Details of every error are in `data\logs\movie_roulette.jsonl`. `..\.venv\Scripts\python -m app check` (run in the
`backend` folder) reports Python, FFmpeg and the interface build.

## Starting

| Problem | Fix |
|---|---|
| "needs Python 3.11 or newer" | let the script install it with winget, or install from python.org with *Add python.exe to PATH*. If `python` opens the Microsoft Store, turn off *App execution aliases* for python in Windows settings. |
| "Installing Python packages failed" | check the internet connection; delete the `.venv` folder and start again. |
| "Building the interface needs Node.js" | download a release ZIP instead (pre-built), or install Node.js LTS. |
| "Port 8765 is used by another program" | set `MR_PORT=8766` (or any free port) in `.env`. |
| The browser does not open | open `http://127.0.0.1:8765/` yourself. On the PC itself you are signed in as host automatically unless a host password is set. |

## Library and TMDB

| Problem | Fix |
|---|---|
| "FFprobe was not found" / no files found | install FFmpeg (`winget install Gyan.FFmpeg`) and start again, or put it in `tools\ffmpeg\bin`. |
| Movies missing after a scan | check *Settings > Movie library > Files that could not be used*: files shorter than the minimum length are treated as extras, unreadable files are listed with the reason. Folders named Extras/Trailers/Samples are skipped. |
| A movie is shown as "Needs a match check" | the name or running time did not identify it with confidence. Open it in the library and pick the right film. Lower *Auto-accept matches at confidence* if your files are named well. |
| Wrong match | open the movie, *Change the match*. Your choice is kept on every rescan. |
| "TMDB rejected the API key" | paste the key again; both the v3 *API Key* and the v4 *Read Access Token* work. |
| "Could not reach TMDB" | check the internet connection or firewall. Already-matched movies keep working offline. |
| A drive was unplugged | its movies show *File missing* and are skipped in games; they come back on the next scan once the drive is connected. |

## Playback

| Problem | Fix |
|---|---|
| "Preparing the scene" takes long | each round converts only the clip. On slow PCs choose *Stream quality: 720p* and a faster *Clip preparation speed* in Settings > Playback. 4K HEVC files take longest. |
| "FFmpeg could not prepare this clip" | the file is probably damaged at that point; choose another time or skip the movie. |
| No sound on a phone | browsers start videos muted until you tap - tap *Tap for sound*. |
| Video stutters on phones | lower the stream quality, or turn off *Show the video on players' phones* and watch the main screen. |

## Phones and other devices

| Problem | Fix |
|---|---|
| Phones cannot open the address | *Settings > Network*: is *Other devices can connect* green? If you just turned it on, press *Restart now*. The phone must be on the same network (not a guest Wi-Fi). Allow Python through Windows Defender Firewall for **private** networks: *Windows Security > Firewall > Allow an app through firewall*. Make sure the network is set to *Private*, not *Public*. |
| "Network access is turned off on the host" | turn on network access in Settings and restart. |
| "No running game has that join code" | the game finished, or the code was mistyped (codes never use 0/O or 1/I/L). |
| Phone shows "Reconnecting" | it reconnects by itself and gets the current state; check Wi-Fi. |
| Host controls from a tablet | set a host password in *Settings > Network*, then sign in on the tablet at the normal address. |

## Data

- *Settings > Database > Check database* runs SQLite's integrity and foreign-key checks.
- *Restore* replaces the database with a backup after making a safety backup of the current one.
- Deleting `data\cache` is always safe.
