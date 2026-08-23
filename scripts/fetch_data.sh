#!/usr/bin/env bash
# Fetch and build everything the repo deliberately does not carry.
#
#   ./scripts/fetch_data.sh            # all steps, skipping what is already done
#   ./scripts/fetch_data.sh --force    # redo everything
#
# What this repo DOES carry (already in data/):
#   agent_prompts/    the system prompt — the part that is actually the design
#   parts/            the measured part catalogue, as CSV (~7 MB)
#   agent_creations/  models the agent built and saved
#   agent_knowledge/  notes it wrote while building
#
# What this fetches:
#   1. data/lego_pieces/       ~570 MB  the LDraw parts library (needed to
#                                       render, measure and validate anything)
#   2. data/ldraw_omr_sets/    ~363 MB  1,800 official sets as LDraw source
#                                       (optional — only for reference/grafting)
#   3. data/vector_db/          ~12 MB  the search indexes, built locally
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-python3}"
FORCE=0
[ "${1:-}" = "--force" ] && FORCE=1
cd "$ROOT"

step() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }
have() { [ -d "$1" ] && [ -n "$(ls -A "$1" 2>/dev/null)" ]; }

# --- 1. the LDraw parts library -------------------------------------------
# Every checker, the renderer and the catalogue builder resolve part geometry
# out of this. Nothing works without it.
step "1/3  LDraw parts library -> data/lego_pieces/"
if have data/lego_pieces && [ $FORCE -eq 0 ]; then
    echo "    already present ($(ls data/lego_pieces | wc -l) files) — skipping"
else
    # Delegated to the project's own seeder rather than reimplemented here: it
    # knows the archive layout, writes the flat cache layout the rest of the
    # code expects, and leaves a _bootstrap.done marker so a re-run is a no-op.
    echo "    downloading complete.zip (~145 MB) and extracting ..."
    "$PYTHON" - <<'PYEOF'
import requests
from maister.database_creation.build_part_catalog import (
    DEFAULT_CACHE_DIR, LDrawLibrary, bootstrap_from_zip)

with requests.Session() as session:
    library = LDrawLibrary(session, DEFAULT_CACHE_DIR)
    bootstrap_from_zip(library, session)
PYEOF
    echo "    done: $(ls data/lego_pieces | wc -l) entries"
fi

# --- 2. the OMR set corpus -------------------------------------------------
# Optional. Without it the agent still builds; it just cannot look at how real
# sets solved a shape, and copy_from_set has nothing to copy from.
step "2/3  OMR set corpus -> data/ldraw_omr_sets/  (optional, ~363 MB)"
if have data/ldraw_omr_sets && [ $FORCE -eq 0 ]; then
    echo "    already present ($(ls data/ldraw_omr_sets | wc -l) files) — skipping"
elif [ "${SKIP_OMR:-0}" = "1" ]; then
    echo "    SKIP_OMR=1 — skipping"
else
    echo "    scraping library.ldraw.org/omr (this takes a while) ..."
    "$PYTHON" -m maister.database_creation.download_ldraw_omr
fi

# --- 3. the vector indexes -------------------------------------------------
# Derived from the CSVs and JSON already in the repo. Downloads the embedding
# and reranker models on first run (~1.2 GB, cached by HuggingFace).
step "3/3  search indexes -> data/vector_db/"
if have data/vector_db && [ $FORCE -eq 0 ]; then
    echo "    already present — skipping"
else
    "$PYTHON" -m maister.retrieval.build_indexes
fi

step "Done"
echo "Check it worked:   $PYTHON -m maister.agent.run_agent --self-test"
