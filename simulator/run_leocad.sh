#!/usr/bin/env bash
#
# run_leocad.sh - drive LeoCAD against an LDraw .mpd model.
#
# Default model: the first .mpd in data/ldraw_omr_sets/, else the first
# model in out/. Pass a path to use a specific one.
#
# Usage:
#   ./run_leocad.sh <command> [model.mpd] [extra leocad options...]
#
# Commands:
#   open        Open the model in the LeoCAD GUI
#   info        List the submodels (0 FILE entries) inside the .mpd
#   render      Render one image of the whole model      -> out/<name>.png
#   steps       Render one image per building step       -> out/steps/step01.png ...
#   views       Render front/back/left/right/top/home    -> out/views/<view>.png
#   sub <name>  Render a single submodel by its file name (see `info`)
#   parts       Export the parts list as CSV             -> out/<name>.csv
#   html        Export browsable HTML instructions       -> out/html/index.html
#   obj         Export the geometry to Wavefront OBJ     -> out/<name>.obj
#   all         info + render + steps + parts
#
# Examples:
#   ./run_leocad.sh open
#   ./run_leocad.sh render
#   ./run_leocad.sh render path/to/model.mpd --shading full --aa-samples 8
#   ./run_leocad.sh sub "40440 - Puppy.ldr"
#   ./run_leocad.sh steps
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# No model is shipped with the repo: the ones this was developed against are
# other people's work from the LDraw OMR corpus and are not ours to
# redistribute. Pick up whatever the corpus download provided, else say so.
_default_model() {
    local first
    first="$(find "$ROOT/data/ldraw_omr_sets" -maxdepth 1 -name '*.mpd' 2>/dev/null | sort | head -1)"
    if [ -n "$first" ]; then printf '%s' "$first"; return; fi
    first="$(find "$ROOT/out" -name '*.ldr' -o -name '*.mpd' 2>/dev/null | sort | head -1)"
    printf '%s' "$first"
}
DEFAULT_MODEL="$(_default_model)"
OUT="$ROOT/out"

WIDTH="${WIDTH:-1600}"
HEIGHT="${HEIGHT:-1200}"

# --- locate LeoCAD -----------------------------------------------------------
# Prefers the installed binary; falls back to the AppImage shipped in simulator/.
if command -v leocad >/dev/null 2>&1; then
    LEOCAD=(leocad)
else
    APPIMAGE="$(ls "$ROOT"/simulator/LeoCAD-*.AppImage 2>/dev/null | head -1 || true)"
    if [[ -n "$APPIMAGE" ]]; then
        chmod +x "$APPIMAGE"
        LEOCAD=("$APPIMAGE")
    else
        echo "error: leocad not found on PATH and no AppImage in $ROOT/simulator/" >&2
        exit 1
    fi
fi

# The parts library ships inside the AppImage (usr/share/leocad/library.bin).
# Point at your own LDraw library instead by exporting LDRAW_LIBRARY_PATH.
LIB_OPT=()
[[ -n "${LDRAW_LIBRARY_PATH:-}" ]] && LIB_OPT=(--libpath "$LDRAW_LIBRARY_PATH")

leo() { "${LEOCAD[@]}" "${LIB_OPT[@]}" "$@"; }

# Steps of the *main* model only (the first "0 FILE" block of the .mpd).
# Counting "0 STEP" over the whole file would include every submodel and make
# LeoCAD re-render the finished model once per extra step.
count_steps() {
    # LDraw files are commonly CRLF, so trim the carriage return first.
    awk 'BEGIN{n=0;blk=0}
         {sub(/\r$/,"")}
         /^0 [Ff][Ii][Ll][Ee] /{blk++; if(blk>1) exit; next}
         /^0 [Ss][Tt][Ee][Pp]$/{n++}
         END{print n+1}' "$1"
}

# --- args --------------------------------------------------------------------
CMD="${1:-render}"
shift || true

SUBMODEL=""
if [[ "$CMD" == "sub" ]]; then
    SUBMODEL="${1:?usage: $0 sub \"<submodel file name>\" [model.mpd]}"
    shift
fi

MODEL="$DEFAULT_MODEL"
if [[ $# -gt 0 && "$1" != -* ]]; then
    MODEL="$1"
    shift
fi

if [[ -z "$MODEL" || ! -f "$MODEL" ]]; then
    echo "error: no model to work on." >&2
    echo "Pass one:  $0 $CMD path/to/model.mpd" >&2
    echo "Or fetch the reference corpus:  ./scripts/fetch_data.sh" >&2
    echo "Or build something first:  python -m maister.agent.run_agent 'a red car'" >&2
    exit 1
fi
[[ -f "$MODEL" ]] || { echo "error: model not found: $MODEL" >&2; exit 1; }

NAME="$(basename "${MODEL%.*}")"
EXTRA=("$@")          # any further flags are passed straight to leocad

# Rendering defaults; override per-invocation by appending your own flags.
RENDER_OPTS=(-w "$WIDTH" -h "$HEIGHT" --shading full --aa-samples 4 --line-width 2)

# One folder per model, so rendering a second model never overwrites the first.
OUT="$OUT/$NAME"
mkdir -p "$OUT"

case "$CMD" in
open)
    echo "==> opening $MODEL in the LeoCAD GUI"
    leo "$MODEL" "${EXTRA[@]}"
    ;;

info)
    echo "==> submodels in $MODEL"
    grep -i '^0 FILE' "$MODEL" | sed -e 's/\r$//' -e 's/^0 FILE //I' | nl -w3 -s'. '
    echo "==> steps in main model: $(count_steps "$MODEL")   part lines (all submodels): $(grep -c '^1 ' "$MODEL")"
    ;;

render)
    echo "==> rendering $NAME -> $OUT/$NAME.png"
    leo "$MODEL" --image "$OUT/$NAME.png" "${RENDER_OPTS[@]}" --viewpoint home "${EXTRA[@]}"
    ;;

steps)
    # -f/-t select the step range; LeoCAD appends 01, 02, ... to the file name.
    LAST="$(count_steps "$MODEL")"
    mkdir -p "$OUT/steps"
    echo "==> rendering steps 1..$LAST -> $OUT/steps/"
    leo "$MODEL" --image "$OUT/steps/step.png" "${RENDER_OPTS[@]}" \
        -f 1 -t "$LAST" --viewpoint home --highlight "${EXTRA[@]}"
    ;;

views)
    mkdir -p "$OUT/views"
    for v in front back left right top home; do
        echo "==> $v -> $OUT/views/$v.png"
        leo "$MODEL" --image "$OUT/views/$v.png" "${RENDER_OPTS[@]}" \
            --viewpoint "$v" "${EXTRA[@]}"
    done
    ;;

sub)
    SAFE="${SUBMODEL// /_}"; SAFE="${SAFE%.ldr}"
    echo "==> rendering submodel '$SUBMODEL' -> $OUT/$SAFE.png"
    leo "$MODEL" --submodel "$SUBMODEL" --image "$OUT/$SAFE.png" \
        "${RENDER_OPTS[@]}" --viewpoint home "${EXTRA[@]}"
    ;;

parts)
    echo "==> parts list -> $OUT/$NAME.csv"
    leo "$MODEL" -csv "$OUT/$NAME.csv" "${EXTRA[@]}"
    ;;

html)
    mkdir -p "$OUT/html"
    echo "==> HTML instructions -> $OUT/html/"
    leo "$MODEL" -html "$OUT/html" "${EXTRA[@]}"
    ;;

obj)
    echo "==> OBJ export -> $OUT/$NAME.obj"
    leo "$MODEL" -obj "$OUT/$NAME.obj" "${EXTRA[@]}"
    ;;

all)
    "$0" info   "$MODEL"
    "$0" render "$MODEL"
    "$0" steps  "$MODEL"
    "$0" parts  "$MODEL"
    ;;

-h|--help|help)
    sed -n '2,30p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'
    ;;

*)
    echo "error: unknown command '$CMD' (try: $0 --help)" >&2
    exit 1
    ;;
esac
