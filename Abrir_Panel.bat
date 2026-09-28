@echo off
rem Double-click to open the pbix2html panel in the browser.
rem Requires someone from the technical team to have run beforehand:
rem   pip install -e ".[live]"
cd /d "%~dp0"
rem "python -m pbix2html" instead of the bare "pbix2html" command: it works even if
rem Python's Scripts folder isn't on PATH (a common cause of "term not recognized" errors).
python -m pbix2html gui
pause
