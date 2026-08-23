#!/usr/bin/env bash
# Start the Maister Builder backend and frontend together.
#
# Activate your Python environment first, or point PYTHON at the interpreter:
#
#     PYTHON=/path/to/venv/bin/python app/start.sh
#
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-python3}"

if ! "$PYTHON" -c "import fastapi, uvicorn" 2>/dev/null; then
    echo "error: '$PYTHON' cannot import fastapi and uvicorn." >&2
    echo "Activate your environment, or set PYTHON=/path/to/python." >&2
    echo "Install with:  pip install -r requirements.txt" >&2
    exit 1
fi

if [ ! -d "$ROOT/app/frontend/node_modules" ]; then
    echo "error: the frontend has no node_modules." >&2
    echo "Install with:  cd app/frontend && npm install" >&2
    exit 1
fi

cd "$ROOT"
# --reload so backend edits are picked up the way the frontend's already are.
# Without it the two halves drift apart silently: Vite hot-reloads a new button
# into the UI while the Python process still has no route for it to call, and
# the only symptom is a 404 that looks like a bug in the feature.
#
# The watched directories are named explicitly, and that is not tidiness. The
# default is to watch the working directory, which here contains out/ - where
# the agent writes a model file on every single write_model. Watching that
# would restart the backend in the middle of its own builds.
"$PYTHON" -m uvicorn app.backend.main:app --port 8000 \
    --reload --reload-dir app/backend --reload-dir maister &
BACKEND=$!
trap 'kill $BACKEND 2>/dev/null || true' EXIT

cd "$ROOT/app/frontend"
npm run dev
