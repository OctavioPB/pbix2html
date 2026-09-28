@echo off
rem Double-click to open the pbix2html panel in the browser.
rem Requires someone from the technical team to have run beforehand:
rem   pip install -e ".[live]"
cd /d "%~dp0"
rem "python -m pbix2html" instead of the bare "pbix2html" command: it works even if
rem Python's Scripts folder isn't on PATH (a common cause of "term not recognized" errors).

rem Free up port 8765 if a previous run is still holding it (closing the window with the
rem [X] button, instead of a real Ctrl+C, can leave uvicorn running in the background).
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8765 2^>nul') do taskkill /PID %%a /F >nul 2>nul

python -m pbix2html gui
pause
