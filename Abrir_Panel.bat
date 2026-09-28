@echo off
rem Double-click to open the pbix2html panel in the browser.
rem Requires someone from the technical team to have run beforehand:
rem   pip install -e ".[live]"
cd /d "%~dp0"
pbix2html gui
pause
