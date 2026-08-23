# What real sets are built out of

Mined from the 1,819 official models in the reference corpus. Every
part here is real, is in the catalogue, and is used by enough different sets to
count as common vocabulary - so you can place any of them **without a lookup**,
exactly like the table in *The pieces*.

That table is the thirty parts you need constantly. These are the 71 after
them, and they are here because a model built only out of the first thirty comes
out as a stack of rectangles. When you are about to approximate a shape by
repeating a brick, the part is probably on this page.

## Rotation is normal

**76% of all part placements in real sets carry a rotation.** Not a
special case, not an advanced technique - it is what most parts do. A build
where everything faces the same way is the unusual one, and it reads as a stack
of boxes because that is what it is.

These are the rotation matrices the corpus actually uses, as the nine values
that go in a type-1 line:

| matrix | what it does | share of all placements |
|---|---|---|
| `1 0 0 0 1 0 0 0 1` | no rotation - the part as the catalogue draws it | 23.6% |
| `0 0 1 0 1 0 -1 0 0` | 90° about Y - turned a quarter turn clockwise seen from above | 12.7% |
| `0 0 -1 0 1 0 1 0 0` | −90° about Y - a quarter turn the other way | 11.4% |
| `-1 0 0 0 1 0 0 0 -1` | 180° about Y - facing backwards | 9.9% |
| `1 0 0 0 0 -1 0 1 0` | 90° about X - laid on its back, studs facing you | 1.3% |
| `0 -1 0 1 0 0 0 0 1` | 90° about Z - on its side, studs facing left | 1.1% |
| `1 0 0 0 0 1 0 -1 0` | −90° about X - laid forward, studs facing away | 1.1% |
| `-1 0 0 0 -1 0 0 0 1` | 180° about Z - upside down | 1.0% |
| `0 1 0 -1 0 0 0 0 1` | −90° about Z - on its side, studs facing right | 1.0% |

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

Those three turned matrices are **34% of every placement in the corpus**
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

### Slope

| part | what it is | sets using it | rotated |
|---|---|---|---|
| `3665a` | Slope Brick 45 2 x 1 Inverted without Inner Stopper Ring | 303 | 75% |
| `4286` | Slope Brick 33 3 x 1 | 293 | 60% |
| `85984` | Slope Brick 31 1 x 2 x 0.667 | 256 | 81% |
| `3298` | Slope Brick 33 3 x 2 | 229 | 72% |
| `50950` | Slope Brick Curved 3 x 1 | 228 | 69% |
| `11477` | Slope Brick Curved 2 x 1 | 218 | 81% |
| `3037` | Slope Brick 45 2 x 4 | 210 | 75% |
| `15068` | Slope Brick Curved 2 x 2 x 0.667 | 172 | 84% |

### Tile

| part | what it is | sets using it | rotated |
|---|---|---|---|
| `2412b` | Tile 1 x 2 Grille with Groove | 687 | 79% |
| `2431` | Tile 1 x 4 with Groove | 678 | 74% |
| `4162` | Tile 1 x 8 | 372 | 75% |
| `6636` | Tile 1 x 6 | 310 | 75% |
| `87079` | Tile 2 x 4 | 267 | 75% |
| `2555` | Tile 1 x 1 with Clip | 227 | 84% |
| `2432` | Tile 1 x 2 with Handle | 219 | 73% |
| `63864` | Tile 1 x 3 | 202 | 75% |

### Plate

| part | what it is | sets using it | rotated |
|---|---|---|---|
| `3034` | Plate 2 x 8 | 611 | 68% |
| `2420` | Plate 2 x 2 Corner | 591 | 78% |
| `3031` | Plate 4 x 4 | 539 | 46% |
| `4032a` | Plate 2 x 2 Round with Axlehole | 466 | 59% |
| `3032` | Plate 4 x 6 | 437 | 66% |
| `3832` | Plate 2 x 10 | 384 | 63% |
| `4477` | Plate 1 x 10 | 376 | 68% |
| `3794a` | Plate 1 x 2 without Groove with 1 Centre Stud | 366 | 63% |

### Brick

| part | what it is | sets using it | rotated |
|---|---|---|---|
| `4070` | Brick 1 x 1 with Headlight | 558 | 71% |
| `3941` | Brick 2 x 2 Round without Reinforcement | 294 | 61% |
| `2357` | Brick 2 x 2 Corner | 282 | 76% |
| `6091` | Brick 2 x 1 x 1.333 with Curved Top | 238 | 74% |
| `30414` | Brick 1 x 4 with Studs on Side | 209 | 71% |
| `87087` | Brick 1 x 1 with Stud on 1 Side | 200 | 66% |
| `2877` | Brick 1 x 2 with Grille | 194 | 74% |
| `11211` | Brick 1 x 2 with Two Studs on One Side | 140 | 72% |

### Bracket

| part | what it is | sets using it | rotated |
|---|---|---|---|
| `2436a` | Bracket 1 x 2 - 1 x 4 with Square Corners | 295 | 69% |
| `44728` | Bracket 1 x 2 - 2 x 2 Down | 273 | 68% |
| `99780` | Bracket 1 x 2 - 1 x 2 Up | 147 | 74% |
| `99207` | Bracket 1 x 2 - 2 x 2 Up | 106 | 80% |
| `99781` | Bracket 1 x 2 - 1 x 2 Down | 101 | 70% |
| `3956` | Bracket 2 x 2 - 2 x 2 Up | 97 | 82% |
| `2436b` | Bracket 1 x 2 - 1 x 4 with Rounded Corners | 64 | 76% |

### Panel

| part | what it is | sets using it | rotated |
|---|---|---|---|
| `4865a` | Panel 1 x 2 x 1 with Square Corners | 224 | 81% |
| `6231` | Panel 1 x 1 x 1 Corner with Rounded Corners | 165 | 78% |
| `4865b` | Panel 1 x 2 x 1 with Rounded Corners | 111 | 78% |
| `30413` | Panel 1 x 4 x 1 with Rounded Corners, Thick Wall | 68 | 77% |

### Arch

| part | what it is | sets using it | rotated |
|---|---|---|---|
| `3659` | Arch 1 x 4 | 95 | 72% |
| `6005` | Arch 1 x 3 x 2 with Curved Top | 60 | 83% |

### Wedge

| part | what it is | sets using it | rotated |
|---|---|---|---|
| `45677` | Wedge 4 x 4 x 0.667 Curved | 77 | 50% |

### Cone

| part | what it is | sets using it | rotated |
|---|---|---|---|
| `4589` | Cone 1 x 1 | 264 | 75% |
| `59900` | Cone 1 x 1 with Stop | 161 | 62% |

### Dish

| part | what it is | sets using it | rotated |
|---|---|---|---|
| `4740` | Dish 2 x 2 Inverted | 281 | 76% |
| `3960` | Dish 4 x 4 Inverted | 87 | 86% |
| `43898` | Dish 3 x 3 Inverted | 61 | 77% |

### Cylinder

| part | what it is | sets using it | rotated |
|---|---|---|---|
| `4-4cyli` | Cylinder 1.0 | 73 | 100% |

### Windscreen

| part | what it is | sets using it | rotated |
|---|---|---|---|
| `3823` | Windscreen 2 x 4 x 2 | 141 | 68% |

### Hinge

| part | what it is | sets using it | rotated |
|---|---|---|---|
| `3937` | Hinge 1 x 2 Base | 328 | 74% |
| `3938` | Hinge 1 x 2 Top | 229 | 70% |
| `4593` | Hinge Control Stick | 215 | 97% |
| `4592` | Hinge Control Stick Base | 215 | 82% |
| `4315` | Hinge Plate 1 x 4 with Car Roof Holder | 140 | 55% |
| `4213` | Hinge Car Roof 4 x 4 | 133 | 65% |
| `6134` | Hinge 2 x 2 Top | 129 | 84% |
| `2430` | Hinge Plate 1 x 4 Top | 127 | 86% |

### Door

| part | what it is | sets using it | rotated |
|---|---|---|---|
| `3822` | Door 1 x 3 x 1 Left | 146 | 61% |
| `3821` | Door 1 x 3 x 1 Right | 144 | 62% |
| `60596` | Door 1 x 4 x 6 Frame | 60 | 82% |

### Wing

| part | what it is | sets using it | rotated |
|---|---|---|---|
| `4859` | Wing 3 x 4 with 1 x 2 Cutout without Stud Notches | 111 | 55% |
| `51739` | Wing 2 x 4 | 89 | 73% |
| `54383` | Wing 3 x 6 Right | 64 | 71% |
| `54384` | Wing 3 x 6 Left | 63 | 61% |

### Turntable

| part | what it is | sets using it | rotated |
|---|---|---|---|
| `3679` | Turntable 2 x 2 Plate Top | 111 | 42% |
| `3680` | Turntable 2 x 2 Plate Base | 109 | 62% |
| `3680c01` | Turntable 2 x 2 Plate with Light Grey Top | 62 | 53% |

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
  catalogue has 5,878 parts and the unusual shape you need for a
  specific thing is still a search away.
