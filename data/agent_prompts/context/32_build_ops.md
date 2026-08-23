# Building by operation

You do not have to type coordinates. `build_ops` takes a short list of
operations and works the numbers out from the parts themselves.

```json
[
  {"op": "stack", "part": "2456", "colour": 70, "at": [0, -24, 0], "count": 3,
   "note": "trunk"},
  {"op": "row",   "part": "3941", "colour": 2,  "at": [-40, -120, 0], "count": 5,
   "note": "canopy"}
]
```

That is a three-brick trunk with a five-brick canopy across it. You did not say
how tall a `2456` is or how wide a `3941` is, and that is the point: **the
spacing is never something you enter.**

## Why this exists

A row of 2x2 round bricks was once written by hand at x = 40, 60, 80, 100, 120.
A 2x2 brick is 40 LDU across. At a 20 LDU pitch every pair of them shares a full
stud of solid plastic, and the model could not be built out of real bricks.

Nobody decided that. It is an arithmetic slip, of the kind that is very easy to
make while turning "five round bricks in a row" into five lines of coordinates,
and very hard to see afterwards. Written as `{"op": "row", "count": 5}` it
cannot happen, because there is nowhere to type the wrong number.

Everything regular in a build - a course of a wall, a floor, a fence, a stack, a
field of tiles - is that same slip waiting to happen. Use ops for all of it.

## The operations

| op | what it does | needs |
|---|---|---|
| `wall` | bonded course-work, bricks chosen for you | `at`, `axis`, `length_studs`, `courses` |
| `box` | four bonded walls round a rectangle | `at`, `size_studs`, `courses` (2+) |
| `fill` | a region tiled with the parts **you** name, bonded | `at`, `size_studs`, `courses`, `parts` |
| `row` | n parts along x or z, flush | `at`, `count`, `axis` (default x) |
| `grid` | n by m parts over a rectangle | `at`, `counts: [along_x, along_z]` |
| `stack` | n parts upward, each on the last | `at`, `count` |
| `ring` | 2 or 4 round a centre, each turned to face out | `at`, `count`, `radius_ldu` |
| `mirror` | a symmetric pair | `at`, `about` (x or z), `plane` |
| `place` | one part, exactly there | `at` |

`wall`, `box` and `fill` are the ones that lay course-work. `wall` and `box`
choose their own bricks and take no `part`; `fill` takes `parts` - the list to
tile with - so the palette stays yours. Everything else needs one `part`.

And four more that place nothing at all. They say what happens to the ops
**inside** them, and they are how you say *and again*, *and the same on the
other side*, and *that thing, six times*:

| op | what it does | needs |
|---|---|---|
| `repeat` | the ops inside, n times, each moved on from the last | `times`, `step`, `ops` |
| `reflect` | the ops inside, and their mirror image | `about` (x or z), `plane`, `ops` |
| `define` | name an assembly; builds it nowhere | `name`, `ops` |
| `call` | put a defined assembly somewhere | `name`, `at` |

**`at` is the position of the first part only.** Everything after it is worked
out. This is the part that is easy to miss: you are not listing positions, you
are giving a starting corner and a count.

### Never lay a wall as rows of 2x4s

This is the most common thing you do and it is wrong twice.

```json
{"op": "row", "part": "3001", "colour": 4, "at": [0, 0, 0], "count": 3}
{"op": "row", "part": "3001", "colour": 4, "at": [0, -24, 0], "count": 3}
{"op": "row", "part": "3001", "colour": 4, "at": [0, -48, 0], "count": 3}
```

Every course breaks in the same place. Those aligned joints run straight up the
wall, which is precisely where a real wall comes apart - a bricklayer would call
it unbonded and it is the one thing masonry exists to avoid. And the whole thing
is one shape repeated twelve times, which is what makes a model read as
assembled rather than designed.

`wall` is the same wall, bonded:

```json
{"op": "wall", "colour": 4, "at": [0, 0, 0], "axis": "x",
 "length_studs": 12, "courses": 3, "note": "front wall"}
```

You give the run and the height. It takes the longest brick that fits, breaks
the seams course to course so no joint runs through, and never strands a 1x1 in
the middle of a run. **The lengths and the offsets are not inputs** - same rule
as the spacing, for the same reason. That wall is 10 parts and 3 shapes; the
twelve 2x4s above are 12 parts and 1.

`box` closes it into a rectangle - a cube, a room, a tower, a chimney, a planter:

```json
{"op": "box", "colour": 4, "at": [0, 0, 0], "size_studs": [10, 8],
 "courses": 4, "note": "the hull"}
```

It alternates which pair of walls runs corner to corner, course by course, so
the corners interlock rather than becoming four vertical joints of their own. It
**refuses a single-course box**, and that refusal is worth understanding: one
course is four walls that meet at the corners with no stud between them, so it
falls into four pieces the moment it is lifted - and nothing in the geometry says
so. Every part is on the grid and nothing overlaps. Two courses tie it.

Both take `thickness_studs` (1 or 2, default 1) and `kind` (`brick` or `plate`,
default brick).

### `fill` is those two with the bricks left to you

`wall` and `box` pick their own parts, which is what makes them worth reaching
for when you do not care which. Often you do care - you have a palette, a brief
and a shape in mind - and `fill` is the same bonding with the ladder handed
over:

```json
{"op": "fill", "colour": 4, "at": [0, 0, 0], "size_studs": [10, 6],
 "courses": 2, "parts": ["3008", "3010", "3004"], "note": "the plinth"}
```

It also does the one thing neither of the others does: a **solid** region. A
floor, a slab, a plinth, a mass, a solid tower. That is the case you have been
laying with `grid` - one shape repeated over a rectangle, which is the single
biggest reason a model comes back reading as one brick - and `fill` lays the
same rectangle bonded out of three.

- `size_studs` is the region, `courses` how many high.
- `hollow: true` makes it a shell of walls instead - a room, a tower - and then
  it needs 2+ courses for the same reason `box` does.
- `parts` must all be the **same width and the same height** (a course is one
  course) and each a **different length**, since the length is what there is to
  choose between. Leave `parts` out and it uses the standard brick ladder.
- `axis` says which way the runs go, default x.

**What they do not do is finish the model.** A wall comes out structural and
medium with no detail in it, which is correct - detail is what goes *on* the
wall. Lay the courses with these, then put the tiles, slopes and 1x1s on by
hand. See *Three sizes, three jobs*.

## The groups: `repeat`, `reflect`, `define`, `call`

The ops above each place one part, n times. Most of what you actually build is
not one part n times - it is a *group* of ops that happens more than once, or
happens on both sides. Four identical courses of wall. A pair of wings. Six
windows. Until you can say that, you write the group out by hand every time,
and that is where the coordinates come back.

**This is the commonest thing you get wrong.** Across the models on disk,
**82% of every op written is `place`** - not because anyone wanted to type
coordinates, but because the thing being built was a group and there was no
word for one. Look at what you are about to write. If two of your ops differ
only by a fixed offset, that is a `repeat`.

### `repeat` - the same ops, moved on each time

```json
{"op": "repeat", "times": 4, "step": [0, -24, 0], "note": "four courses",
 "ops": [
   {"op": "row", "part": "3010", "colour": 4, "at": [0, 0, 0], "count": 3}
 ]}
```

Four courses of wall from one course written once. `step` is how far each copy
moves from the one before it: `[0, -24, 0]` is one brick course up, `[80, 0, 0]`
is one 2x4 along, `[0, -8, 0]` is one plate. `times` counts the first one.

The copies cannot drift, because there is only one position written down. That
is the whole of it - the same rule as the spacing in `row`, applied to a group
instead of a part.

`repeat` nests. A grid of windows is a `repeat` along x holding a `repeat`
down y.

**One thing it does not do: bond across the joint.** `wall`, `box` and `fill`
break their seams over *their own* courses, and a repeated one starts that
again from scratch each copy - so two stacked 2-course boxes can put a seam
above a seam at the join. For plain course-work give the one op more `courses`
instead; use `repeat` for a group that genuinely repeats.

### `reflect` - and the same on the other side

```json
{"op": "reflect", "about": "x", "plane": 0, "note": "the wings",
 "ops": [
   {"op": "row", "part": "3040b", "colour": 4, "at": [60, 0, 0], "count": 2,
    "rotate": 90}
 ]}
```

That builds the wing **and** its mirror image, each part turned so the pair
reads as a mirror rather than as two copies facing the same way. `mirror` does
this for one part; `reflect` does it for everything inside it, which is what
symmetry nearly always means - a wing, an arm, a wheel arch, a whole side of a
building.

Use it wherever the model has an axis. A detail landing a stud off on one side
only is the commonest reason a build reads as unfinished, and writing the far
side by hand is exactly how that happens.

`plane` is where the mirror sits (default 0, the centre line). `keep: false`
builds only the far side, for the rare case where you already placed the near
one.

### `define` and `call` - a shape you build more than once

```json
{"op": "define", "name": "window", "ops": [
   {"op": "place", "part": "3005", "colour": 15, "at": [-30, 0, 0]},
   {"op": "place", "part": "3005", "colour": 15, "at": [30, 0, 0]},
   {"op": "place", "part": "3010", "colour": 15, "at": [0, -24, 0]}
]}
{"op": "call", "name": "window", "at": [-30, -8, 0]}
{"op": "call", "name": "window", "at": [50, -8, 0], "rotate": 90}
```

Define it once around `[0, 0, 0]`, then `call` it wherever it goes. `call`
takes `at`, and optionally `rotate` (turns the whole assembly about Y) and
`mirror` (`"x"` or `"z"`, for the handed copy).

A window, a wheel, a battlement, a leg, a chimney: anything the model has more
than one of. The second one is then *the same* as the first by construction,
rather than by you retyping it correctly.

Definitions last for the one `build_ops` call that made them. `define` goes at
the top of the call, not inside another op.

### `place` is the last of these, not the first

The repeating ops are the reason this tool exists. If you are about to write two
`place` ops for the same part in a line, that is a `row` with `count: 2` - and
the row cannot be mis-spaced where the two `place` ops can.

```json
{"op": "place", "part": "3024", "colour": 71, "at": [0, 0, 0]}
{"op": "place", "part": "3024", "colour": 71, "at": [20, 0, 0]}
{"op": "place", "part": "3024", "colour": 71, "at": [40, 0, 0]}
```

is three chances to get a number wrong, and this is none:

```json
{"op": "row", "part": "3024", "colour": 71, "at": [0, 0, 0], "count": 3}
```

Keep `place` for the part that genuinely belongs to no pattern - a door, a
chimney, a single tile. A call made of twenty `place` ops is this tool being
used as a typewriter, and the arithmetic has quietly moved back to you.

The same test, one level up: if two of your **ops** differ only by a fixed
offset, that is a `repeat`. If two of them are the same thing on opposite sides
of a centre line, that is a `reflect`. If the same handful of ops appears twice
anywhere in the call, that is a `define` and two `call`s.

Every op needs `part`, `colour` and `at`. Every op may take:

- **`rotate`** - degrees about Y, a multiple of 90. The footprint turns with the
  part, so a row of 2x4 bricks rotated 90° spaces itself 40 LDU apart instead of
  80. You never adjust for it.
- **`gap_studs`** - a deliberate gap between the parts. `0`, the default, sits
  them flush. Use it for a fence, a row of windows, anything meant to be spaced.
- **`note`** - written above those parts as a comment, so the file reads like a
  build rather than a list.

`at` is `[x, y, z]` in LDU and it is the **first** part's position; the rest
follow from it. x and z land on multiples of 10. Remember −Y is up, so a stack
goes to *smaller* y and `build_ops` does that subtraction for you.

## It refuses rather than writing something broken

Before it writes anything, `build_ops` runs the same checks `validate_model`
runs, over the model as it *would* be. If the parts it is about to place would
overlap something, sit off the stud grid, or crowd a stud, **nothing is written
at all** and you are told which lines and what to move. The file on disk is
exactly as it was, so there is nothing to undo.

This is the whole reason to build with ops rather than by hand: you find out on
the first call, not on the fifteenth. A build that writes fifteen times and
validates once at the end has spent its budget before it knows anything.

It only judges the parts *it* is placing. Faults that were already in the model
will not block you; `validate_model` is still what tells you the model as a
whole is sound, and it is still not optional.

### The lattice check

The one it will refuse most often, and the one worth understanding:

```
"3 of these parts would stand on a different stud lattice from the model
 this is being added to, half a stud out, where they can never connect"
  line 17  6141.dat at [140, 0]   move x +10, z +10
```

A part's studs sit at a fixed offset from its origin that depends on the part -
a 6x6 plate's are at ±10, ±30, ±50 from its centre, a 1x1's is at 0. So a 6x6
plate at x = −180 and a 1x1 plate at x = 140 are both on multiples of 20 and are
still half a stud apart. Everything in a model has to agree on one grid.

**When every part in the call needs the same move, it is applied for you** and
the reply says so under `aligned_to_lattice`:

```
"aligned_to_lattice": {"x": 10, "z": 10, "parts": 4,
  "note": "…all 4 of them were moved x+10, z+10 onto the lattice the rest of
           it uses…"}
```

Nothing was wrong with the build; it was half a stud out of step, and that is
arithmetic rather than a decision. Read the offset and use it for the next call
instead of repeating the slip.

You only get a refusal when the parts need *different* moves, which is not a
phase slip - it is two parts that disagree with each other about where the grid
is, and only you can say which is right. Pass `allow_half_offset: true` when the
offset is deliberate because those parts sit on jumper plates.

## What it will not do

- **Angles that are not right angles.** A hinge held open at 30°, a limb posed,
  an axe in a fist - those are `edit_model`, with the matrix written out.
- **Moving or deleting.** Ops add parts. Changing one that is already there is
  `edit_model` on its line.
- **Minifigures.** A figure is ten parts on neck pins and shoulder sockets, not
  on the stud grid. Assemble it as *Minifigures* describes.

## Working with the plan

`plan_construction` returns steps. A step that says "four 2x4 bricks at x = 0,
80, 160, 240, all at z = 0" is one `row` op with `count: 4` - and you no longer
have to check whether 80 was the right pitch, because you are not the one
choosing it. When a plan step already carries an `ops` list, pass it straight
through.

## Keep writing early

`build_ops` renders on every call, exactly as `edit_model` does. Lay the first
op down as soon as you know the footprint. A rough model on screen is worth more
to the user than a perfect plan they cannot see.
