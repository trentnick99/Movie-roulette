@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title Movie Roulette
set "ROOT=%~dp0"
set "VENV=%ROOT%.venv"
set "PY="

echo.
echo  ===============================================
echo   Movie Roulette
echo  ===============================================
echo.

rem ------------------------------------------------------------------ 1. Python 3.11 or newer
where py >nul 2>nul
if not errorlevel 1 (
  py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" >nul 2>nul
  if not errorlevel 1 set "PY=py -3"
)
if not defined PY (
  where python >nul 2>nul
  if not errorlevel 1 (
    python -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" >nul 2>nul
    if not errorlevel 1 set "PY=python"
  )
)
if not defined PY goto :nopython
echo  [ok] Python found.

rem ------------------------------------------------------------------ 2. private Python environment + packages
if not exist "%VENV%\Scripts\python.exe" (
  echo  Creating the Python environment (first start only^)...
  %PY% -m venv "%VENV%"
  if errorlevel 1 goto :venvfail
)
set "VPY=%VENV%\Scripts\python.exe"
set "STAMP=%VENV%\requirements.stamp"
fc /b "%ROOT%backend\requirements.txt" "%STAMP%" >nul 2>nul
if errorlevel 1 (
  echo  Installing Python packages (first start or after an update^)...
  "%VPY%" -m pip install --disable-pip-version-check -q --upgrade pip
  "%VPY%" -m pip install --disable-pip-version-check -q -r "%ROOT%backend\requirements.txt"
  if errorlevel 1 goto :pipfail
  copy /y "%ROOT%backend\requirements.txt" "%STAMP%" >nul
)
echo  [ok] Python packages ready.

rem ------------------------------------------------------------------ 3. the web interface (pre-built in release downloads)
if exist "%ROOT%frontend\dist\index.html" goto :frontend_ok
echo  The web interface has not been built yet (normal for a git clone).
where npm >nul 2>nul
if errorlevel 1 goto :nonode
pushd "%ROOT%frontend"
echo  Installing interface packages...
call npm ci --no-audit --no-fund
if errorlevel 1 ( popd & goto :buildfail )
echo  Building the interface...
call npm run build
if errorlevel 1 ( popd & goto :buildfail )
popd
:frontend_ok
echo  [ok] Web interface ready.

rem ------------------------------------------------------------------ 4. FFmpeg (needed to read and play movies)
set "HAVE_FFMPEG="
if exist "%ROOT%tools\ffmpeg\bin\ffprobe.exe" set "HAVE_FFMPEG=1"
where ffprobe >nul 2>nul
if not errorlevel 1 set "HAVE_FFMPEG=1"
if not defined HAVE_FFMPEG (
  echo.
  echo  [!] FFmpeg was not found. Movie Roulette needs it to read and play your movies.
  where winget >nul 2>nul
  if not errorlevel 1 (
    choice /C YN /M "     Install FFmpeg now with winget"
    if not errorlevel 2 (
      winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
      echo.
      echo  FFmpeg installed. Close this window and start Movie Roulette again so Windows finds it.
      pause
      exit /b 0
    )
  )
  echo     You can also unzip an FFmpeg build into tools\ffmpeg so that tools\ffmpeg\bin\ffprobe.exe exists.
  echo     Movie Roulette starts anyway, but scanning and playback will not work until FFmpeg is installed.
  echo.
) else (
  echo  [ok] FFmpeg found.
)

rem ------------------------------------------------------------------ 5. start (database migrations run automatically)
echo.
echo  Starting Movie Roulette. Your browser opens in a moment.
echo  Keep this window open while you play. Close it to stop Movie Roulette.
echo.
pushd "%ROOT%backend"
"%VPY%" -m app %*
set "RC=%ERRORLEVEL%"
popd
if not "%RC%"=="0" (
  echo.
  echo  Movie Roulette stopped because of a problem - read the messages above.
  echo  Details are also in data\logs\movie_roulette.jsonl
  pause
)
exit /b %RC%

rem ------------------------------------------------------------------ error messages
:nopython
echo  [!] Movie Roulette needs Python 3.11 or newer, and it was not found.
where winget >nul 2>nul
if errorlevel 1 goto :manualpython
choice /C YN /M "     Install Python 3.12 now with winget"
if errorlevel 2 goto :manualpython
winget install --id Python.Python.3.12 -e --accept-source-agreements --accept-package-agreements
echo.
echo  Python installed. Close this window and start Movie Roulette again.
pause
exit /b 0
:manualpython
echo     Download it from https://www.python.org/downloads/windows/
echo     During setup tick "Add python.exe to PATH". Then start Movie Roulette again.
pause
exit /b 1

:venvfail
echo  [!] Could not create the Python environment in "%VENV%".
echo     Check that the folder is writable, or delete the .venv folder and try again.
pause
exit /b 1

:pipfail
echo  [!] Installing Python packages failed. Check the internet connection and try again.
echo     If it keeps failing, delete the .venv folder and start again.
pause
exit /b 1

:nonode
echo  [!] Building the interface needs Node.js 20 or newer, which was not found.
echo     Either download a release ZIP from the GitHub Releases page (the interface is pre-built there),
echo     or install Node.js LTS from https://nodejs.org/ (or: winget install OpenJS.NodeJS.LTS) and start again.
pause
exit /b 1

:buildfail
echo  [!] Building the interface failed - see the messages above.
pause
exit /b 1
