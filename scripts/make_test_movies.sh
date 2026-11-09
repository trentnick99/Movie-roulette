#!/usr/bin/env bash
# Generates short synthetic test videos (colour bars + tone) in several containers/codecs for end-to-end tests.
# They contain no film content; names match the fake TMDB catalogue used by the tests.
set -euo pipefail
out="${1:?usage: make_test_movies.sh <folder>}"
mkdir -p "$out/Sci-Fi"
gen() { ffmpeg -v error -y -f lavfi -i "testsrc2=size=640x360:rate=24:duration=$2" -f lavfi -i "sine=frequency=$3:duration=$2" $4 "$out/$1"; }
gen "Iron.Man.2008.1080p.BluRay.x264-GRP.mkv" 180 300 "-c:v libx265 -x265-params log-level=error -c:a ac3" &
gen "Alien (1979).mkv" 180 400 "-c:v libx264 -preset ultrafast -c:a aac" &
gen "Sci-Fi/Aliens.1986.REMASTERED.avi" 180 500 "-c:v mpeg4 -c:a mp3" &
gen "Airplane! (1980).mp4" 180 600 "-c:v libx264 -preset ultrafast -c:a aac" &
gen "Toy Story (1995).m4v" 180 700 "-c:v libx264 -preset ultrafast -c:a aac" &
gen "The Thing.mkv" 180 800 "-c:v libx264 -preset ultrafast -c:a aac" &
gen "Iron Man 2 (2010).mov" 180 900 "-c:v libx264 -preset ultrafast -c:a aac" &
wait
echo "test movies in $out"
