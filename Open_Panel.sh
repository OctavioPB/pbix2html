#!/bin/sh
# Double-click (or run from a terminal) to open the pbix2html panel in the browser (Linux).
# Requires someone from the technical team to have run beforehand:
#   pip install -e ".[live]"
#
# First time only: this file needs to be executable. If double-clicking doesn't run it
# (most file managers ask "Run" vs "Display" the first time; some just open a text
# editor instead), run once from a terminal:
#   chmod +x Open_Panel.sh

cd "$(dirname "$0")" || exit 1

# "python3 -m pbix2html" instead of the bare "pbix2html" command: it works even if
# the install's bin folder isn't on PATH (a common cause of "command not found" errors).
if command -v python3 >/dev/null 2>&1; then
    PYTHON=python3
else
    PYTHON=python
fi

# Free up port 8765 if a previous run is still holding it (closing the terminal window
# directly, instead of a real Ctrl+C, can leave uvicorn running in the background).
if command -v lsof >/dev/null 2>&1; then
    lsof -ti tcp:8765 2>/dev/null | while read -r pid; do kill -9 "$pid" 2>/dev/null; done
elif command -v fuser >/dev/null 2>&1; then
    fuser -k 8765/tcp >/dev/null 2>&1
fi

"$PYTHON" -m pbix2html gui

echo
printf 'Press Enter to close this window...'
read -r _
