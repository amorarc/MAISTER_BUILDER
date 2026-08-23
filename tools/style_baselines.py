"""Regenerate the style baselines in maister/agent/style.py from the OMR corpus.

The numbers in ``style.BASELINES`` are what real LEGO sets look like on four
axes — distinct shapes, how much of the model the commonest part takes, colours,
and how many parts carry a rotation. This is where they come from.

    python tools/style_baselines.py

It prints a table to read and the ``BASELINES`` dict to paste. It does not write
to style.py: these numbers change what the agent is told about its own work, so
they get looked at by a person before they land.

Measurement matches style.measure exactly — type-1 lines that name a real part,
submodel references excluded, no flattening. If you change one, change both.
"""

import csv
import glob
import json
import statistics as st
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "data" / "ldraw_omr_sets"
CATALOG = ROOT / "data" / "parts" / "parts_catalog.csv"


def catalogue_names():
    """Every real part name, lowercased and without .dat.

    A quarter of the OMR files embed part definitions inside themselves, and
    those definitions are built out of LDraw *primitives* — `4-4cyli`, `rect3`,
    `1-8edge`, the internal geometry every part is made of. Those appear as
    type-1 lines like anything else, and counting them adds thousands of
    "distinct shapes" no designer ever chose: 6.5% of all part lines in the
    corpus, in 27% of its files.

    So a line counts only when it names something in the catalogue. That is the
    same authority validation.py checks part existence against.
    """
    names = set()
    try:
        with CATALOG.open(encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                for key in ("part_id", "dat_name"):
                    value = (row.get(key) or "").strip().lower()
                    if value.endswith(".dat"):
                        value = value[:-4]
                    if value:
                        names.add(value)
    except OSError:
        return set()
    return names

IDENTITY = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)

# Buckets by piece count. A 20-part build and a 2,000-part build share none of
# these statistics, so comparing one against the other's median is worse than
# not comparing at all.
BUCKETS = [(1, 25), (26, 60), (61, 150), (151, 400), (401, 1200), (1201, 10 ** 9)]

# A bucket with fewer models than this is not a distribution, it is an anecdote.
MIN_MODELS = 12
# Below this a model has nothing to say about variety either way.
MIN_PARTS = 8


def read(path, known):
    """(shapes, colours, rotated) for one LDraw document."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None

    # "0 FILE x.ldr" declares a block inside this document. A type-1 line
    # pointing at one of those places a submodel, not a part, and counting it
    # would report the scene's arrangement as though it were the build.
    blocks = {line.strip()[7:].strip().lower()
              for line in text.splitlines()
              if line.strip().lower().startswith("0 file ")}

    shapes, colours, rotated = Counter(), Counter(), 0
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 15 or fields[0] != "1":
            continue
        target = " ".join(fields[14:]).strip().lower()
        if target in blocks or not target.endswith(".dat"):
            continue
        if known and target.rsplit("/", 1)[-1][:-4] not in known:
            continue  # an LDraw primitive, not a part anyone chose
        shapes[target] += 1
        colours[fields[1]] += 1
        try:
            matrix = tuple(round(float(v), 6) for v in fields[5:14])
        except ValueError:
            continue
        if matrix != IDENTITY:
            rotated += 1

    return shapes, colours, rotated


def metrics(shapes, colours, rotated):
    total = sum(shapes.values())
    if total < MIN_PARTS:
        return None
    return {
        "parts": total,
        "distinct": len(shapes),
        "top_share": max(shapes.values()) / total,
        "colours": len(colours),
        "rotated_share": rotated / total,
    }


def percentile(rows, key, fraction):
    values = sorted(row[key] for row in rows)
    index = int(fraction * len(values))
    return values[max(0, min(len(values) - 1, index))]


def main():
    if not CORPUS.is_dir():
        raise SystemExit(f"no corpus at {CORPUS} — nothing to measure")

    known = catalogue_names()
    if not known:
        print(f"warning: no catalogue at {CATALOG} — primitives will be "
              f"counted as parts and the numbers will be wrong")

    rows = []
    for pattern in ("**/*.mpd", "**/*.ldr"):
        for path in glob.glob(str(CORPUS / pattern), recursive=True):
            got = read(path, known)
            if not got:
                continue
            found = metrics(*got)
            if found:
                rows.append(found)

    if not rows:
        raise SystemExit(f"found no readable models under {CORPUS}")
    print(f"scanned {len(rows)} models\n")

    table = {}
    for low, high in BUCKETS:
        group = [r for r in rows if low <= r["parts"] <= high]
        if len(group) < MIN_MODELS:
            print(f"{low:>5}-{high:<10} skipped, only {len(group)} model(s)")
            continue

        median = lambda key: st.median(r[key] for r in group)  # noqa: E731
        entry = {
            "distinct": round(median("distinct")),
            "top_share": round(median("top_share"), 3),
            # The gate for a remark: a model worse than this is duller than
            # nine out of ten real sets its size.
            "top_share_p90": round(percentile(group, "top_share", 0.9), 3),
            "colours": round(median("colours")),
            "colours_p10": percentile(group, "colours", 0.1),
            "rotated_share": round(median("rotated_share"), 3),
            "rotated_share_p10": round(percentile(group, "rotated_share", 0.1), 3),
        }
        table[f"({low}, {high})"] = entry
        print(f"{low:>5}-{high:<10} n={len(group):<5} "
              f"distinct={entry['distinct']:<4} "
              f"top={entry['top_share']:<6} (p90 {entry['top_share_p90']:<5}) "
              f"colours={entry['colours']:<3} (p10 {entry['colours_p10']:<2}) "
              f"rotated={entry['rotated_share']:<6} "
              f"(p10 {entry['rotated_share_p10']})")

    print("\n--- paste into maister/agent/style.py ---\n")
    print(json.dumps(table, indent=4))


if __name__ == "__main__":
    main()
