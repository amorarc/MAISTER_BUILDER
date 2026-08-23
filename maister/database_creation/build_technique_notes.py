"""Mine the OMR corpus for what real sets are built out of, and how.

    python -m maister.database_creation.build_technique_notes

Writes ``data/agent_prompts/context/26_techniques.md`` - a context block naming
the shapes real sets use and the rotations they use them at.

# Why this is mined rather than written

The agent's standing vocabulary is a table of about thirty part numbers in
``20_pieces.md``, and it is a good table: every entry checked, usable with no
lookup, which is exactly what a builder needs for the common cases. The trouble
is that it is also *complete* - a model built only out of what is on it comes
out as rectangles, and measuring the results says so. Real sets of 26 to 60
parts name 23 distinct shapes; this agent's builds of that size have named four
to six.

The rest of the vocabulary is already on disk, in the 1,800 sets the reference
tools search. But finding it costs a tool call each time, and a builder told to
be decisive spends those calls on the shape it already had in mind. So the parts
that professional designers reach for most are pulled out here, once, and put
where they cost nothing to read.

The same goes for rotation. ``10_lego_cad.md`` explains rotation matrices
correctly and then frames them defensively - never mirror, only multiples of
90°. What it never says is that seven parts in ten in a real set carry one. The
four matrices below are simply the ones the corpus uses, in order, with what
each one does.

# The three filters that make the output usable

* **Catalogue parts only.** A quarter of the corpus files embed part
  definitions, and those are built from LDraw primitives - ``4-4cyli``,
  ``rect3``, ``1-8edge`` - which appear as ordinary part references and are
  nothing a designer chose.
* **No ``~Moved to`` or obsolete stubs.** ``20_pieces.md`` already warns that
  ``3023`` is a redirect and ``3023b`` is the plate; a mined list that
  recommended the stub would undo that warning.
* **Ranked by how many sets use a part, not by how many times.** One mosaic
  with four thousand identical tiles should not decide what the vocabulary is.
"""

import csv
import glob
from collections import Counter, defaultdict
from pathlib import Path

from ..agent.config import (CONTEXT_DIR, OMR_SETS_DIR, PART_ROTATION,
                            PARTS_CATALOG)

OUTPUT = CONTEXT_DIR / "26_techniques.md"

# The same scan, kept as data as well as prose.
#
# The page below can only name a hundred parts, and the fact it is trying to
# teach - that this shape is one real sets turn - is a fact about every part in
# the catalogue. Written out per part, it reaches the agent at the moment it
# matters instead: on the search result and in the details call, where the part
# is actually being chosen. See catalog.turn_share.
ROTATION_CSV = PART_ROTATION
# Below this the share is noise. Three placements of a part, two of them turned,
# is not "real sets turn this two thirds of the time".
MIN_PLACEMENTS = 12

IDENTITY = (1, 0, 0, 0, 1, 0, 0, 0, 1)

# Already in 20_pieces.md as "the parts you already know". Listing them again
# would spend the block's attention on what the agent uses too much of.
ALREADY_KNOWN = {
    "3005", "3004", "3622", "3010", "3009", "3008",
    "3024", "3023b", "3623", "3710", "3666", "3460",
    "3003", "3002", "3001", "2456", "3958",
    "3022", "3021", "3020", "3795",
    "3070b", "3069b", "3068b", "3040b", "3039", "3038",
    "3062b", "54200", "15573", "6141", "98138",
}

# Categories that decide what a model *looks like*. Technic and Electric are
# left out on purpose: they are the most-used categories in the corpus by a
# wide margin and almost none of it is shape - it is pins, axles and bushes
# holding other things together, and a builder given that list builds a chassis
# nobody asked for. Minifig parts have a context block of their own.
SHAPE_CATEGORIES = (
    "Slope", "Tile", "Plate", "Brick", "Bracket", "Panel", "Arch",
    "Wedge", "Cone", "Dish", "Cylinder", "Windscreen", "Hinge",
    "Plant", "Animal", "Door", "Window", "Wing", "Turntable",
)

# Stubs and redirects. A recommendation to use one of these is a recommendation
# to put a hole in the model.
DEAD_CATEGORIES = {"moved", "obsolete", "subpart", "primitive", "sticker",
                   "helper"}

# Per category, how many parts the block names. Enough to be a vocabulary,
# short enough that the whole thing stays readable in one pass.
PER_CATEGORY = 8
# A part in fewer sets than this is not part of the common vocabulary.
MIN_SETS = 60


def catalogue():
    """``{part_id_lower: row}`` for every real part."""
    rows = {}
    try:
        with Path(PARTS_CATALOG).open(encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                pid = (row.get("part_id") or "").strip()
                if pid:
                    rows[pid.lower()] = row
    except OSError:
        pass
    return rows


def _int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _dead(row):
    """Whether this catalogue entry is a redirect, a stub or not a real part."""
    category = (row.get("category") or "").strip().lower()
    description = (row.get("description") or "").strip()
    status = (row.get("status") or "").strip().lower()
    return (category in DEAD_CATEGORIES
            or description.startswith("~")
            or "moved to" in description.lower()
            or status in ("moved", "obsolete"))


def scan_rotations(known):
    """``{part_id: (uses, rotated)}`` and the corpus-wide matrix counts."""
    per_part = defaultdict(lambda: [0, 0])
    matrices = Counter()

    patterns = ("**/*.mpd", "**/*.ldr")
    for pattern in patterns:
        for path in glob.glob(str(Path(OMR_SETS_DIR) / pattern), recursive=True):
            try:
                text = Path(path).read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue

            blocks = {line.strip()[7:].strip().lower()
                      for line in text.splitlines()
                      if line.strip().lower().startswith("0 file ")}

            for line in text.splitlines():
                fields = line.split()
                if len(fields) < 15 or fields[0] != "1":
                    continue
                target = " ".join(fields[14:]).strip().lower()
                if target in blocks or not target.endswith(".dat"):
                    continue
                part = target.rsplit("/", 1)[-1][:-4]
                if part not in known:
                    continue
                try:
                    values = [float(v) for v in fields[5:14]]
                except ValueError:
                    continue
                # Rounded to integers where they are integers: a matrix is
                # almost always one of the eight axis-aligned rotations, and
                # 0.9999999 is that matrix written by a CAD tool.
                matrix = tuple(int(round(v)) if abs(v - round(v)) < 1e-6
                               else round(v, 3) for v in values)
                per_part[part][0] += 1
                if matrix != IDENTITY:
                    per_part[part][1] += 1
                matrices[matrix] += 1

    return per_part, matrices


# What the four commonest matrices actually do. The corpus says which are
# common; only a person can say what they mean, so these are named here and the
# counts come from the scan.
MATRIX_MEANING = {
    (1, 0, 0, 0, 1, 0, 0, 0, 1): "no rotation - the part as the catalogue draws it",
    (0, 0, 1, 0, 1, 0, -1, 0, 0): "90° about Y - turned a quarter turn clockwise seen from above",
    (0, 0, -1, 0, 1, 0, 1, 0, 0): "−90° about Y - a quarter turn the other way",
    (-1, 0, 0, 0, 1, 0, 0, 0, -1): "180° about Y - facing backwards",
    (1, 0, 0, 0, 0, -1, 0, 1, 0): "90° about X - laid on its back, studs facing you",
    (1, 0, 0, 0, 0, 1, 0, -1, 0): "−90° about X - laid forward, studs facing away",
    (0, -1, 0, 1, 0, 0, 0, 0, 1): "90° about Z - on its side, studs facing left",
    (0, 1, 0, -1, 0, 0, 0, 0, 1): "−90° about Z - on its side, studs facing right",
    (-1, 0, 0, 0, -1, 0, 0, 0, 1): "180° about Z - upside down",
    (1, 0, 0, 0, -1, 0, 0, 0, -1): "180° about X - upside down, facing backwards",
}


def write_rotation_csv(per_part, path=ROTATION_CSV):
    """Per part: how often the corpus places it turned. Returns how many rows."""
    rows = sorted((pid, uses, rotated) for pid, (uses, rotated)
                  in per_part.items() if uses >= MIN_PLACEMENTS)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["part_id", "placements", "rotated", "share"])
        for pid, uses, rotated in rows:
            writer.writerow([pid, uses, rotated, round(rotated / uses, 3)])
    return len(rows)


def _table(rows, headers):
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    for row in rows:
        out.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(out)


def build():
    parts = catalogue()
    if not parts:
        raise SystemExit(f"no parts catalogue at {PARTS_CATALOG}")

    known = set(parts)
    per_part, matrices = scan_rotations(known)
    if not per_part:
        raise SystemExit(f"no models found under {OMR_SETS_DIR}")

    # -- the vocabulary, by category ------------------------------------
    by_category = defaultdict(list)
    for pid, row in parts.items():
        if pid in ALREADY_KNOWN or _dead(row):
            continue
        sets_using = _int(row.get("set_count"))
        if sets_using < MIN_SETS:
            continue
        category = (row.get("category") or "").strip()
        if category not in SHAPE_CATEGORIES:
            continue
        by_category[category].append((sets_using, pid, row))

    sections = []
    for category in SHAPE_CATEGORIES:
        entries = sorted(by_category.get(category, []), reverse=True)[:PER_CATEGORY]
        if not entries:
            continue
        rows = []
        for sets_using, pid, row in entries:
            uses, rotated = per_part.get(pid, (0, 0))
            share = f"{round(100 * rotated / uses)}%" if uses else "-"
            description = (row.get("description") or "").strip()
            # The catalogue writes "Slope Brick 45  2 x  1" with padding.
            description = " ".join(description.split())
            rows.append((f"`{row['part_id']}`", description, sets_using, share))
        sections.append(f"### {category}\n\n" + _table(
            rows, ["part", "what it is", "sets using it", "rotated"]))

    # -- the rotations --------------------------------------------------
    total = sum(matrices.values())
    rotation_rows = []
    for matrix, count in matrices.most_common(10):
        meaning = MATRIX_MEANING.get(matrix)
        if not meaning:
            continue
        written = " ".join(str(v) for v in matrix)
        rotation_rows.append((f"`{written}`", meaning,
                              f"{round(100 * count / total, 1)}%"))

    rotated_total = sum(c for m, c in matrices.items() if m != IDENTITY)
    rotated_share = round(100 * rotated_total / total) if total else 0

    # The three Y turns on their own. Computed rather than read off the table
    # above, so the sentence quoting it cannot drift away from the numbers in
    # it the next time this is regenerated.
    y_turns = ((0, 0, 1, 0, 1, 0, -1, 0, 0),
               (0, 0, -1, 0, 1, 0, 1, 0, 0),
               (-1, 0, 0, 0, 1, 0, 0, 0, -1))
    y_share = round(100 * sum(matrices[m] for m in y_turns) / total) if total else 0

    named = sum(len(sorted(by_category.get(c, []), reverse=True)[:PER_CATEGORY])
                for c in SHAPE_CATEGORIES)

    corpus_size = len(glob.glob(str(Path(OMR_SETS_DIR) / "**" / "*.mpd"),
                                recursive=True))

    document = f"""\
# What real sets are built out of

Mined from the {corpus_size:,} official models in the reference corpus. Every
part here is real, is in the catalogue, and is used by enough different sets to
count as common vocabulary - so you can place any of them **without a lookup**,
exactly like the table in *The pieces*.

That table is the thirty parts you need constantly. These are the {named} after
them, and they are here because a model built only out of the first thirty comes
out as a stack of rectangles. When you are about to approximate a shape by
repeating a brick, the part is probably on this page.

## Rotation is normal

**{rotated_share}% of all part placements in real sets carry a rotation.** Not a
special case, not an advanced technique - it is what most parts do. A build
where everything faces the same way is the unusual one, and it reads as a stack
of boxes because that is what it is.

These are the rotation matrices the corpus actually uses, as the nine values
that go in a type-1 line:

{_table(rotation_rows, ["matrix", "what it does", "share of all placements"])}

The `rotated` column in every table below is how often that specific part is
placed with a rotation. A part at 90% is a part that is nearly always turned -
that is what it is *for*, and placing it unrotated is usually a mistake.

## Decoration faces a direction

A slope is not a shape, it is a **direction**. So is a curved slope, a wedge, a
bracket, a windscreen, a plant, a tile with a print on it - everything that is
on a model to be looked at rather than to hold something up.

Placed square, four slopes around a roof all slope the same way and the roof
has one edge and three cliffs. Placed facing outward, the same four parts are a
roof. Nothing else changed: the parts, the colours and the coordinates are
identical, and only the nine numbers in the middle of the line are different.

**Every decoration piece you place, decide which way it faces.** These are the
only four rotations you need for it - all about Y, the vertical axis, so the
part stays flat on the studs and the seats underneath it are unchanged:

| facing | matrix | `build_ops` |
|---|---|---|
| as drawn | `1 0 0 0 1 0 0 0 1` | `"rotate": 0` |
| a quarter turn | `0 0 1 0 1 0 -1 0 0` | `"rotate": 90` |
| backwards | `-1 0 0 0 1 0 0 0 -1` | `"rotate": 180` |
| the other quarter | `0 0 -1 0 1 0 1 0 0` | `"rotate": 270` |

Those three turned matrices are **{y_share}% of every placement in the corpus**
on their own. They are the ordinary way to place a part, not an embellishment.

A Y rotation keeps the part flat on the grid, which is why these three are safe
to reach for and the ones about X and Z are not. Two things it does change:

**The footprint turns with the part.** A turned 1x4 occupies four studs in z
rather than four in x. `build_ops` works the spacing out from that when you
pass `rotate`; writing the matrix by hand, you swap it yourself.

**Turning can move a part half a stud.** Most slopes have their origin on their
back stud row rather than in the middle of their footprint - `3039` runs from
z −30 to z +10, not −20 to +20 - so a quarter turn moves where its studs fall.
The same 2x2 slope that needed z+10 unturned needs x+10 at 90°. `build_ops`
puts it back on the lattice and tells you the offset it used, which is the
reason to turn parts through it rather than by writing the nine numbers.

Turning does not need a lookup or a validation pass to justify it. If you
cannot say which way a decoration piece faces, that is the thing to decide
before you place it - not after `validate_model` has told you it is on the
grid, because it will be on the grid either way.

## The vocabulary

{(chr(10) * 2).join(sections)}

## How to use this page

- **Place these directly.** They are catalogue-verified. Searching for one is a
  wasted call.
- **Reach for the category, not the part.** When you need a shape, find its
  category above and take the first entry that fits; the ordering is by how many
  real sets use it, so the first entry is the one designers reach for.
- **A part with a high `rotated` share wants turning.** Slopes face four ways.
  Brackets exist to turn a surface. A bracket placed unrotated is a bracket
  doing nothing. You do not have to remember which parts those are: any part
  the corpus usually turns says so on its own search result and in
  `get_part_details`, with the matrix to use.
- **This does not replace `search_parts`.** It is the common vocabulary; the
  catalogue has {len(parts):,} parts and the unusual shape you need for a
  specific thing is still a search away.
"""

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(document, encoding="utf-8")
    written = write_rotation_csv(per_part)
    print(f"wrote {OUTPUT}")
    print(f"  {named} parts named across {len(sections)} categories")
    print(f"  {rotated_share}% of corpus placements are rotated")
    print(f"wrote {ROTATION_CSV}")
    print(f"  {written} parts with {MIN_PLACEMENTS}+ placements to judge by")


if __name__ == "__main__":
    build()
