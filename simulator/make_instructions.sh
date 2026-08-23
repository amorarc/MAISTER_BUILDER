#!/usr/bin/env bash
#
# make_instructions.sh - generate LEGO building instructions from an LDraw
# .mpd/.ldr model with LPub3D (console mode, no GUI interaction needed).
#
# Default model: the first .mpd in data/ldraw_omr_sets/, else the first
# model in out/. Pass a path to use a specific one.
# Everything lands in out/instructions/.
#
# Usage:
#   ./make_instructions.sh <command> [model.mpd] [extra lpub3d options...]
#
# Commands:
#   pdf         Full instruction booklet          -> out/instructions/<name>.pdf
#   png         One PNG per page                  -> out/instructions/png/
#   pages <r>   Same as png but only page range r (e.g. 1-10, or 1,4,9)
#   parts       Parts list CSV + BrickLink XML    -> out/instructions/
#   export <o>  Any other -o option (stl, 3ds, pov, dae, obj, htmlparts)
#   open        Open the model in the LPub3D GUI (edit page layout, callouts...)
#   meta        Dump the LPUB meta-command reference -> out/instructions/meta-commands.html
#   clean       Delete LPub3D render caches and the working copy
#
# Environment:
#   BG=#FFFFFF   Page background colour. BG=none keeps whatever the model says.
#   RENDERER=native   native | ldglite | ldview | ldview-sc | povray | povray-ldv
#   VERBOSE=1    Show LPub3D's full per-page log instead of errors only
#
# Examples:
#   ./make_instructions.sh pdf
#   ./make_instructions.sh pages 1-10
#   BG=none ./make_instructions.sh pdf path/to/model.mpd
#   ./make_instructions.sh pdf -fs -hs -ss 4     # fade prior steps, highlight new
#                                                #  parts, rounded-logo studs
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
OUT_ROOT="$ROOT/out/instructions"   # per-model subfolder is appended below

BG="${BG:-#FFFFFF}"
RENDERER="${RENDERER:-native}"

# --- locate LPub3D -----------------------------------------------------------
if command -v lpub3d >/dev/null 2>&1; then
    LPUB=(lpub3d)
else
    APPIMAGE="$(ls "$ROOT"/simulator/LPub3D-*.AppImage 2>/dev/null | head -1 || true)"
    if [[ -n "$APPIMAGE" ]]; then
        chmod +x "$APPIMAGE"
        LPUB=("$APPIMAGE")
    else
        echo "error: lpub3d not found on PATH and no AppImage in $ROOT/simulator/" >&2
        exit 1
    fi
fi

# -ns  keep the log out of stdout twice
# -ll  load the LEGO parts library (required in console mode)
# -p   renderer; native is the fastest and the only one that needs no helper exe
export LPUB3D_DISABLE_UPDATE_CHECK=1
lpub() {
    if [[ -n "${VERBOSE:-}" ]]; then
        "${LPUB[@]}" -ns -ll -p "$RENDERER" "$@"
    else
        # LPub3D logs one INFO line per page; keep only what matters.
        "${LPUB[@]}" -ns -ll -p "$RENDERER" "$@" 2>&1 \
            | sed 's/\x1b\[[0-9;]*m//g' \
            | grep -E "ERROR|WARNING|FATAL|process succeeded|process failed" \
            || true
    fi
}

# --- args --------------------------------------------------------------------
CMD="${1:-pdf}"
shift || true

RANGE=""
EXPORT_OPT=""
case "$CMD" in
pages)  RANGE="${1:?usage: $0 pages <page range, e.g. 1-10>}"; shift ;;
export) EXPORT_OPT="${1:?usage: $0 export <stl|3ds|pov|dae|obj|htmlparts>}"; shift ;;
esac

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
MODEL="$(cd "$(dirname "$MODEL")" && pwd)/$(basename "$MODEL")"   # LPub3D wants
NAME="$(basename "${MODEL%.*}")"                                  # absolute paths
EXTRA=("$@")

# One folder per model, so a second model never overwrites the first.
OUT="$OUT_ROOT/$NAME"
WORK="$OUT/.work"          # working copy + LPub3D render cache live here,
                           # so the model folder in data/ stays clean

# --- working copy ------------------------------------------------------------
# LPub3D drops its render cache (LPub3D/), renderer logs and the csv/xml exports
# next to the model file it is given, and those paths are not configurable.
# Feeding it a copy under out/ keeps data/ pristine - and lets us set the page
# background, which is only reachable through an LPUB meta command.
prepare_source() {
    mkdir -p "$WORK"
    SRC="$WORK/$NAME.mpd"
    if [[ "$BG" == "none" ]]; then
        cp "$MODEL" "$SRC"
    else
        awk -v bg="$BG" 'NR==1{print; printf "0 !LPUB PAGE BACKGROUND COLOR \"%s\"\n", bg; next} {print}' \
            "$MODEL" > "$SRC"
    fi
    echo "$SRC"
}

mkdir -p "$OUT"

case "$CMD" in
pdf)
    SRC="$(prepare_source)"
    echo "==> building PDF instructions for $NAME (renderer: $RENDERER)"
    lpub -pe -o pdf -of "$OUT/$NAME.pdf" "${EXTRA[@]}" "$SRC"
    echo "==> $OUT/$NAME.pdf"
    ;;

png|pages)
    SRC="$(prepare_source)"
    mkdir -p "$OUT/png"
    RANGE_OPT=()
    [[ -n "$RANGE" ]] && RANGE_OPT=(-r "$RANGE")
    echo "==> rendering page images${RANGE:+ (pages $RANGE)} -> $OUT/png/"
    lpub -pe -o png -od "$OUT/png" "${RANGE_OPT[@]}" "${EXTRA[@]}" "$SRC"
    ls "$OUT/png" | tail -3
    ;;

parts)
    SRC="$(prepare_source)"
    echo "==> parts list (csv) and BrickLink wanted list (xml)"
    # Both formats ignore -od/-of and always write "<model>-export.<ext>" next
    # to the source file, hence the move.
    lpub -pe -o csv    "${EXTRA[@]}" "$SRC"
    lpub -pe -o bl-xml "${EXTRA[@]}" "$SRC"
    mv -f "$WORK/$NAME-export.csv" "$OUT/$NAME-parts.csv"
    mv -f "$WORK/$NAME-export.xml" "$OUT/$NAME-bricklink.xml"
    echo "==> $OUT/$NAME-parts.csv"
    echo "==> $OUT/$NAME-bricklink.xml"
    ;;

export)
    SRC="$(prepare_source)"
    mkdir -p "$OUT/$EXPORT_OPT"
    echo "==> exporting '$EXPORT_OPT' -> $OUT/$EXPORT_OPT/"
    lpub -pe -o "$EXPORT_OPT" -od "$OUT/$EXPORT_OPT" "${EXTRA[@]}" "$SRC"
    ;;

open)
    echo "==> opening $MODEL in the LPub3D GUI"
    "${LPUB[@]}" -ll "${EXTRA[@]}" "$MODEL"
    ;;

meta)
    # Model-independent reference, so it lives above the per-model folders.
    lpub -emc "$OUT_ROOT/meta-commands.html" >/dev/null
    echo "==> $OUT_ROOT/meta-commands.html ($(grep -c '<li>' "$OUT_ROOT/meta-commands.html") commands)"
    ;;

clean)
    find "$OUT_ROOT" -maxdepth 2 -name .work -type d -exec rm -rf {} + 2>/dev/null || true
    find "$ROOT/data" -maxdepth 3 \( -name LPub3D -type d -o -name 'std???-ldglite' \) \
        -exec rm -rf {} + 2>/dev/null || true
    echo "==> caches removed"
    ;;

-h|--help|help)
    sed -n '2,32p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'
    ;;

*)
    echo "error: unknown command '$CMD' (try: $0 --help)" >&2
    exit 1
    ;;
esac
