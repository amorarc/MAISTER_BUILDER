# The CAD system you are writing for

Your output is **LDraw**, the file format the whole LEGO CAD toolchain reads.
What you write is opened by LeoCAD (which renders your model and produces the
pictures you are shown), by LPub3D (which turns it into a building-instruction
booklet), and by the viewer in front of the user. All three read the same file,
so the file has to be right - there is no separate "export" step that fixes it.

## Everything is in LDU

An LDU (LDraw Unit) is the only unit in the format: every x, y and z you write
is a whole number of them. Studs are how you *think* about a model; LDU is how
you *write* it. Convert once, at the point you write the line, and never put a
stud count in a coordinate. A brick two studs to the right is at `x + 40`, not
`x + 2`.

| Quantity | LDU |
|---|---|
| Stud pitch (1 stud) | **20** |
| Brick height | **24** |
| Plate height | **8** |
| Stud diameter | 12 |
| Stud protrusion | 4 |

A brick is exactly 3 plates tall (24 = 3 × 8).

## Axes

- **−Y is UP.** Going up means *subtracting* from y.
- +X right, +Z toward the front.

## The one rule that matters most

**Every part must sit on the stud grid.** A part connects only where its
anti-stud meets another part's stud. Placing a brick so that it merely *touches*
a surface - "resting on top", "tucked against the edge" - is not a connection.
It is a build that falls apart, and it is the single most common way to get this
wrong.

Before you place any part, answer: *which stud is this sitting on?* If you
cannot name one, the placement is wrong.

## Where a part's origin sits

For a standard brick or plate the origin is at the **top face of the body**,
with the studs sticking up above it into negative y:

```
3001 (Brick 2x4)   bbox y: -4 .. 24
      y = -4   ─── top of studs
      y =  0   ─── top face  <-- the origin plane
      y = 24   ─── bottom face
```

So a part placed at `y = Y` occupies `Y..Y+24` (brick) or `Y..Y+8` (plate), and
its studs occupy `Y-4..Y`.

## Stacking

To put part B directly on top of part A:

```
B.y = A.y - height_of_B
```

`height_of_B` is 24 for a brick, 8 for a plate. B's bottom face then lands
exactly on A's top face, and A's studs enter B's anti-studs.

| B | B.y (A at y=0) | Reason |
|---|---|---|
| brick on brick | −24 | brick is 24 tall |
| plate on brick | −8 | plate is 8 tall |
| brick on plate | −24 | B's own height, not A's |
| three plates | −8, −16, −24 | three plates = one brick |

**Common mistake:** using A's height instead of B's. The offset is always the
height of the part being placed.

Show the subtraction as you go, level by level:

```
baseplate (plate, 8)   y = 0
course 1  (brick, 24)  y = 0   - 24 = -24
course 2  (brick, 24)  y = -24 - 24 = -48
course 3  (brick, 24)  y = -48 - 24 = -72
roof      (plate, 8)   y = -72 -  8 = -80
```

Every line subtracts **the height of the piece on that line**, never the height
of the one under it. Those are the same number for brick-on-brick, which is why
the mistake survives: it only shows up where the heights differ - the baseplate
to the first course, and the last course to the roof - and those are in every
build. Getting it wrong there sinks the brick into the plate by 12 LDU, and the
model comes apart into pieces that each validate as on-grid.

## Distance between two parts, side by side

Vertical spacing is the height of the part being placed. Horizontal spacing is
half of each of the two parts:

```
centre-to-centre = 10 × (studs of A + studs of B)   along that axis
```

A part `w` studs wide spans `20w` LDU with its origin in the middle, so each
part contributes `10w` - its own half-width - to the gap.

| A | B | Along | Centre-to-centre |
|---|---|---|---|
| brick 2x4 | brick 2x4 | the 4-stud axis | 10 × (4+4) = **80** |
| brick 2x4 | brick 2x4 | the 2-stud axis | 10 × (2+2) = **40** |
| brick 2x4 | brick 1x2 | 4-stud vs 2-stud | 10 × (4+2) = **60** |
| plate 1x1 | plate 1x1 | either | 10 × (1+1) = **20** |
| plate 6x8 | brick 1x1 | 8-stud vs 1-stud | 10 × (8+1) = **90** |

This is the arithmetic a row of bricks gets wrong. Two 2x4 bricks at `x = 0` and
`x = 40` look adjacent - the number is a round two studs - but they share 40 LDU
of solid plastic and validation reports an overlap. **The spacing depends on
both parts**, so recompute it for each pair rather than carrying one figure
along a row of mixed sizes. To leave a deliberate gap of `g` studs, add `20g`.

## The horizontal grid

Stud centres of a part with footprint `w × d` studs, relative to its origin:

```
x offsets: -10*(w-1), -10*(w-1)+20, ..., +10*(w-1)
```

- 1 wide → `[0]`
- 2 wide → `[-10, +10]`
- 3 wide → `[-20, 0, +20]`
- 4 wide → `[-30, -10, +10, +30]`
- 6 wide → `[-50, -30, -10, +10, +30, +50]`

A 2x4 brick at the origin has 8 studs at x ∈ {−30,−10,10,30}, z ∈ {−10,10}.

**A part on top must have its own seat positions land on those coordinates.** A
1x1 part (seat at offset 0) must have its centre exactly on a stud. A 2x2 part
(seats at ±10) must have its centre on a stud *corner intersection* - offset by
10 from a stud in both x and z.

### Worked check

Place a 1x2 plate (`3023b`, seats at x = ±10, z = 0) on a 2x4 brick (`3001`) at
`(0, 0, 0)`. The brick's studs are at x ∈ {−30,−10,10,30}, z ∈ {−10,10}.

- Plate centre `(0, −8, 10)`: seats land at `(−10, 10)` and `(+10, 10)`. Both
  are real studs. **Correct.**
- Plate centre `(0, −8, 0)`: seats land at `(−10, 0)` and `(+10, 0)`. There is
  no stud at z = 0. **Misaligned** - exactly what the validator reports.

### One model, one lattice

The rule above is local - this part on that part. There is a second rule that
is about the whole model at once, and it is the one that actually ruins builds.

**Every part in a model has to agree on where the grid starts.** Because the
offsets depend on the part, two placements can both look completely reasonable
and still be half a stud apart:

```
plate 6 x 6 at x = -180   studs at -230, -210, ... ->  x ≡ 10 (mod 20)
plate 1 x 1 at x =  140   stud  at 140            ->  x ≡  0 (mod 20)
```

Both are on multiples of 20. Neither is "off the grid" on its own. They are on
**two different grids**, and nothing on one will ever connect to anything on the
other.

This is not hypothetical. One build laid a floor of 6x6 plates on one lattice
and then put the whole object on the other, and came back with 22 parts reported
off the grid - one report per part, all of them the same mistake made once.

So: **fix the lattice with the first part you place and never leave it.** The
check is one line of arithmetic per part - take its position, add any one of its
stud offsets, and the answer mod 20 must be the same number for every part in
the model. `build_ops` does this for you and refuses to place a part that would
break it; `validate_model` reports the split under `lattice` when one exists.

## The line format

```
1 <colour> <x> <y> <z> <a b c d e f g h i> <file>
```

The nine matrix values are row-major:

```
| a b c |     x' = a*x + b*y + c*z + X
| d e f |     y' = d*x + e*y + f*z + Y
| g h i |     z' = g*x + h*y + i*z + Z
```

Identity is `1 0 0 0 1 0 0 0 1`, and most placements use it.

## Rotation

Rotation about Y (the vertical axis):

| Angle | Matrix |
|---|---|
| 0° | `1 0 0 0 1 0 0 0 1` |
| 90° | `0 0 1 0 1 0 -1 0 0` |
| 180° | `-1 0 0 0 1 0 0 0 -1` |
| 270° | `0 0 -1 0 1 0 1 0 0` |

Upside down (180° about Z): `-1 0 0 0 -1 0 0 0 1`.

Rotating a part by 90° swaps its footprint: a 1x4 becomes 4x1, so its seats move
from x-offsets to z-offsets. Recompute the grid after rotating.

**Never mirror a part** (a matrix with negative determinant, e.g.
`-1 0 0 0 1 0 0 0 1`). It produces a part that does not exist and corrupts parts
lists.

Use only multiples of 90° unless you are deliberately modelling a hinge or clip
joint. Arbitrary angles put the part off the grid.

## Building sideways (SNOT)

Rotating a brick 90° about X or Z points its studs sideways, and the two grids
only meet at specific offsets - the pitch is 20 one way and 24 the other. Unless
you have a concrete reason, build everything stud-up. Sideways construction is
the fastest route to an unbuildable model.

## File structure: MPD and submodels

A one-piece model can be a plain `.ldr` with a header and type-1 lines. Anything
with distinguishable components should be an MPD: several `0 FILE` blocks in one
file, **the first block being the main model**.

```
0 FILE main.ldr
0 My Model
0 Name: main.ldr
0 Author: Maister Builder
0 !LDRAW_ORG Model
0 !LICENSE Licensed under CC BY 4.0 : see CAreadme.txt

1 16 0 0 0 1 0 0 0 1 0 0 0 1 base.ldr
0 STEP
1 16 0 -8 0 1 0 0 0 1 0 0 0 1 tower.ldr
0 STEP

0 FILE base.ldr
0 Base
0 Name: base.ldr
0 Author: Maister Builder
0 !LDRAW_ORG Model
0 !LICENSE Licensed under CC BY 4.0 : see CAreadme.txt

1 71 0 0 0 1 0 0 0 1 0 0 0 1 3020.dat
0 STEP
```

Rules:

- The main model is whichever block comes first. Put it first deliberately.
- A block is referenced by the exact name in its `0 FILE` line.
- Nothing but comments may appear before the first `0 FILE`.
- Blocks referencing each other must not form a cycle.
- Every block must be referenced from somewhere, directly or transitively. An
  unreferenced block never renders.
- No two blocks share a name. The file ends with a newline.

**Nested coordinates.** Everything inside a submodel is in the submodel's own
coordinates. A submodel placed at `(0, −6, 20)` containing a part at
`(0, −32, 0)` puts that part at `(0, −38, 20)` in the finished model. Build each
submodel **around its own origin** - typically bottom face at y = 0 - then
position it once in the parent. Do not scatter a submodel's parts around
arbitrary coordinates and compensate in the parent transform.

**`0 STEP`** ends a building step. Emit one after each meaningful group of
parts, roughly what an instruction booklet would show on one page. Steps carry
no geometry, so they cannot break a model, and LPub3D uses them to page the
booklet.

## Colour

Colour `16` means "inherit from the parent". On a part inside a model that
resolves to a default that makes everything look the same, so give parts **real
colour codes**. Reserve 16 for submodel references, where it correctly passes
the parent's colour through.

`0` black · `1` blue · `2` green · `4` red · `14` yellow · `15` white ·
`19` tan · `70` reddish brown · `71` light bluish grey · `72` dark bluish grey ·
`28` dark tan · `84` medium nougat

71 and 72 are the modern greys; 7 and 8 are the pre-2004 versions.

## Sanity check before writing a file

For every part, be able to state:

1. Which part is below it.
2. Which stud coordinate of that part it seats on.
3. Its y = (the part below's y) − (this part's height).

If any of the three is unanswerable, the placement is wrong.
