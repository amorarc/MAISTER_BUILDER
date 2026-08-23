#!/usr/bin/env python3
"""What a minifigure can hold, learned from the sets that hold them.

    python -m maister.database_creation.build_minifig_grips

A minifigure's hand is a C-shaped clip and a tool is a bar pushed through it.
That means there is no single "held" position: the bar slides, so a sword
gripped at the hilt and a torch gripped halfway down its shaft sit at different
distances along the same axis. What is fixed is the axis itself.

Measured in the hand part's own frame, over every accessory placed near a hand
in the 1,800-model Official Model Repository:

    the grip axis runs along local y, at x = 0, z = -10.5

91% of held accessories sit within 5 LDU of that line. The ones that do not are
skirts, hair and airtanks — parts *worn* by a figure standing near the hand
rather than held in it, which is the distinction this file exists to draw.

The output is `data/parts/minifig_held.csv`: one row per accessory a real set
was seen holding, with where along the grip axis it was held. It is guidance
for the agent, not a closed list — validation uses the geometric rule above, so
a part that never appears here is still recognised as held if it is on the axis.
"""

import argparse
import collections
import csv
import math
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    __package__ = "maister.database_creation"

from ..agent import catalog, minifig                      # noqa: E402
from ..agent.config import OMR_SETS_DIR, PARTS_DIR        # noqa: E402

OUT_FILE = Path(__file__).resolve().parents[2] / "data" / "parts" / "minifig_held.csv"

# Anything this far from a hand is not being held by it, whatever it is.
NEAR = 26.0
# The grip axis, in the hand's own frame, and how far off it a part may sit.
GRIP_X, GRIP_Z = 0.0, -10.5
GRIP_RADIUS = 5.0
# Seen in at least this many sets before it is written down. One placement is
# as likely to be a coincidence as a convention.
MIN_SEEN = 2


def _local(matrix, delta):
    """``delta`` in the frame of a part with this rotation (R transpose)."""
    return (matrix[0] * delta[0] + matrix[3] * delta[1] + matrix[6] * delta[2],
            matrix[1] * delta[0] + matrix[4] * delta[1] + matrix[7] * delta[2],
            matrix[2] * delta[0] + matrix[5] * delta[1] + matrix[8] * delta[2])


def _relative(hand, tool):
    """The tool's rotation in the hand's frame: ``hand^T · tool``.

    Which way a tool points is mostly a pose — a figure can hold a sword up or
    out, and the sets do both — so this is not a rule the way the grip axis is.
    It is recorded because the *commonest* relative rotation per part is a
    sensible default, and a default beats a builder guessing at nine numbers.
    Only 1% of held tools take the hand's own rotation unchanged, so "just copy
    the hand's matrix" is the one answer that is definitely wrong.
    """
    return tuple(round(sum(hand[k * 3 + i] * tool[k * 3 + j] for k in range(3)), 3)
                 for i in range(3) for j in range(3))


def _blocks(text):
    """Each `0 FILE` block of an MPD, as its type-1 lines."""
    block = []
    for line in text.splitlines() + ["0 FILE ."]:
        if line.strip().lower().startswith("0 file"):
            if block:
                yield block
            block = []
        elif line.startswith("1 "):
            fields = line.split()
            if len(fields) >= 15:
                block.append((fields[14].lower().removesuffix(".dat"),
                              [float(v) for v in fields[2:5]],
                              [float(v) for v in fields[5:14]]))
    if block:
        yield block


def scan(sets_dir=OMR_SETS_DIR):
    """part_id -> the offsets along the grip axis it was held at."""
    found = collections.defaultdict(list)
    for path in sorted(Path(sets_dir).glob("*.mpd")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for block in _blocks(text):
            hands = [r for r in block if r[0].startswith("3820")]
            if not hands:
                continue
            for name, pos, mat in block:
                # A part of the figure itself is worn, not held.
                if minifig.role_of(name) or name.startswith("3820"):
                    continue
                _, hand_pos, hand_mat = min(
                    hands, key=lambda h: math.dist(pos, h[1]))
                if math.dist(pos, hand_pos) > NEAR:
                    continue
                dx, dy, dz = _local(
                    hand_mat, [pos[i] - hand_pos[i] for i in range(3)])
                if math.hypot(dx - GRIP_X, dz - GRIP_Z) > GRIP_RADIUS:
                    continue  # near the hand, but not in it
                found[name].append((dy, _relative(hand_mat, mat)))
    return found


def build(sets_dir=OMR_SETS_DIR, out_file=OUT_FILE):
    found = scan(sets_dir)
    described = {(row.get("part_id") or "").lower(): row.get("description") or ""
                 for row in catalog.load_catalog()}

    rows = []
    for part, seen in found.items():
        if len(seen) < MIN_SEEN:
            continue
        offsets = sorted(s[0] for s in seen)
        pose = collections.Counter(s[1] for s in seen).most_common(1)[0][0]
        rows.append({
            "part_id": part,
            "description": described.get(part, ""),
            "times_held": len(seen),
            # Where along the grip axis, which is the only fixed number.
            "grip_y": round(offsets[len(offsets) // 2], 1),
            "grip_y_min": round(offsets[0], 1),
            "grip_y_max": round(offsets[-1], 1),
            # The commonest way this part is turned in the hand that holds it.
            "grip_matrix": " ".join(str(v) for v in pose),
            "in_library": (Path(PARTS_DIR) / f"{part}.dat").is_file(),
        })
    rows.sort(key=lambda r: (-r["times_held"], r["part_id"]))

    out_file.parent.mkdir(parents=True, exist_ok=True)
    with out_file.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]) if rows else
                                ["part_id", "description", "times_held",
                                 "grip_y", "grip_y_min", "grip_y_max",
                                 "in_library"])
        writer.writeheader()
        writer.writerows(rows)
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sets-dir", default=str(OMR_SETS_DIR))
    args = ap.parse_args()

    rows = build(Path(args.sets_dir))
    print(f"{len(rows)} accessories a minifigure is seen holding "
          f"-> {OUT_FILE.relative_to(OUT_FILE.parents[2])}")
    for row in rows[:12]:
        print(f"   {row['part_id']:12s} x{row['times_held']:<4d} "
              f"grip_y {row['grip_y']:6.1f}   {row['description'][:44]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
