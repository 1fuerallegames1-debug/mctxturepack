#!/usr/bin/env sh
# Angel unter Linux/macOS starten:  ./start.sh   (Textfenster: ./start.sh --terminal)
cd "$(dirname "$0")" || exit 1
if command -v python3 >/dev/null 2>&1; then PY=python3; else PY=python; fi
exec "$PY" -m angel "$@"
