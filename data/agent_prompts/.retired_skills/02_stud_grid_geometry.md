# Skill: Stud Grid Geometry

This is the skill that decides whether your model is buildable. Work through the
arithmetic explicitly - do not eyeball placements.

## Axes

- **−Y is UP.** Going up means *subtracting* from y.
- +X right, +Z toward the front.
- Stud pitch 20, brick 24, plate 8, stud protrusion 4.

## Where a part's origin sits

For a standard brick or plate the origin is at the **top face of the body**, with
the studs sticking up above it into negative y:

```
3001 (Brick 2x4)   bbox y: -4 .. 24
      y = -4   ─── top of studs
      y =  0   ─── top face  <-- the origin plane
      y = 24   ─── bottom face
```

So a part placed at `y = Y` occupies `Y..Y+24` (brick) or `Y..Y+8` (plate), and its
studs occupy `Y-4..Y`.

## Stacking recipe

To put part B directly on top of part A:

```
B.y = A.y - height_of_B
```

where `height_of_B` is 24 for a brick, 8 for a plate. B's bottom face then lands
exactly on A's top face, and A's studs enter B's anti-studs.

Examples, all with A at `y = 0`:

| B | B.y | Reason |
|---|---|---|
| brick on brick | −24 | brick is 24 tall |
| plate on brick | −8 | plate is 8 tall |
| brick on plate | −24 | B's own height, not A's |
| plate on plate on plate | −8, −16, −24 | three plates = one brick |

**Common mistake:** using A's height instead of B's. The offset is always the
height of the part being placed.

## Distance between two parts, side by side

Vertical spacing is the height of the part being placed. Horizontal spacing is
half of each of the two parts:

```
centre-to-centre = 10 × (studs of A + studs of B)   along that axis
```

Because a part `w` studs wide spans `20w` LDU and its origin is in the middle,
each part contributes `10w` - its own half-width - to the gap.

| A | B | Along | Centre-to-centre |
|---|---|---|---|
| brick 2x4 | brick 2x4 | the 4-stud axis | 10 × (4+4) = **80** |
| brick 2x4 | brick 2x4 | the 2-stud axis | 10 × (2+2) = **40** |
| brick 2x4 | brick 1x2 | 4-stud vs 2-stud | 10 × (4+2) = **60** |
| plate 1x1 | plate 1x1 | either | 10 × (1+1) = **20** |
| plate 6x8 | brick 1x1 | 8-stud vs 1-stud | 10 × (8+1) = **90** |

This is the arithmetic that a row of bricks gets wrong. Two 2x4 bricks at
`x = 0` and `x = 40` look adjacent - the number is a round two studs - but they
share 40 LDU of solid plastic and validation reports them as an overlap. The
spacing depends on **both** parts, so recompute it for each pair rather than
carrying one figure along a row of mixed sizes.

To leave a deliberate gap of `g` studs between them, add `20g`.

## Horizontal grid

Stud centres of a part with footprint `w × d` studs, relative to its origin:

```
x offsets: -10*(w-1), -10*(w-1)+20, ..., +10*(w-1)
z offsets: -10*(d-1), -10*(d-1)+20, ..., +10*(d-1)
```

- 1 wide → `[0]`
- 2 wide → `[-10, +10]`
- 3 wide → `[-20, 0, +20]`
- 4 wide → `[-30, -10, +10, +30]`
- 6 wide → `[-50, -30, -10, +10, +30, +50]`

So a 2x4 brick at the origin has 8 studs at x ∈ {−30,−10,10,30}, z ∈ {−10,10}.

**A part on top must have its own seat positions land on those coordinates.** For a
1x1 part (seat at offset 0) that means its centre must be exactly on a stud. For a
2x2 part (seats at ±10) its centre must be on a stud *corner intersection* -
midway between four studs, i.e. offset by 10 from a stud in both x and z.

### Worked check

Place a 1x2 plate (`3023`, seats at x = ±10, z = 0) on a 2x4 brick (`3001`) at
`(0, 0, 0)`. The brick's studs are at x ∈ {−30,−10,10,30}, z ∈ {−10,10}.

Try plate centre `(0, −8, 10)`: its seats land at `(−10, 10)` and `(+10, 10)`.
Both are real studs. ✅

Try plate centre `(0, −8, 0)`: seats land at `(−10, 0)` and `(+10, 0)`. There is no
stud at z = 0. ❌ - this is exactly the failure the validator reports as MISALIGNED.

## Rotation matrices

Rotation about Y (the vertical axis), written as the nine values `a b c d e f g h i`:

| Angle | Matrix |
|---|---|
| 0° | `1 0 0 0 1 0 0 0 1` |
| 90° | `0 0 1 0 1 0 -1 0 0` |
| 180° | `-1 0 0 0 1 0 0 0 -1` |
| 270° | `0 0 -1 0 1 0 1 0 0` |

Upside down (180° about Z): `-1 0 0 0 -1 0 0 0 1`.

Rotating a part by 90° swaps its footprint: a 1x4 becomes 4x1, so its seats move
from x-offsets to z-offsets. Recompute the grid after rotating - do not assume the
old offsets still apply.

**Never mirror a part** (a matrix with negative determinant, e.g. `-1 0 0 0 1 0 0 0 1`).
It produces a part that does not exist and corrupts parts lists.

Only use rotations that are multiples of 90° unless you are deliberately modelling
a hinge or a clip joint. Arbitrary angles put the part off the grid.

## Building sideways (SNOT)

If you rotate a brick 90° about X or Z, its studs point sideways and the stud grid
no longer aligns with the vertical one - the vertical pitch is 20 in one direction
and 24 in the other, so they only meet at specific offsets. Unless you have a
concrete reason, build everything stud-up. Sideways construction is the fastest
route to an unbuildable model.

## Sanity checklist before writing a file

For every part, be able to state:

1. Which part is below it.
2. Which stud coordinate of that part it seats on.
3. Its y = (the part below's y) − (this part's height).

If any of the three is unanswerable, the placement is wrong.
