#!/bin/sh
# Finder/Spotlight entrypoint: paths are literal, with no shell command from a task.
set -eu
JARVIS_REPOSITORY=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd -P)
JARVIS_PYTHON="$JARVIS_REPOSITORY/.venv/bin/python"
if [ ! -x "$JARVIS_PYTHON" ]; then
  printf '%s\n' 'Jarvis: окружение .venv не подготовлено. Попросите Codex установить Jarvis на этот Mac.' >&2
  exit 1
fi
exec "$JARVIS_PYTHON" "$JARVIS_REPOSITORY/scripts/macos/launch.py"
