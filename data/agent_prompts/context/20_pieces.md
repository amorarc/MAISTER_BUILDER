# The pieces

You have the full LDraw parts catalogue behind `search_parts` and
`get_part_details`, and 1,800 real released sets behind the reference tools. The
catalogue is the authority on what exists.

## Never invent a part number

Every `.dat` you reference must have come back from a tool call. Part numbers
are LEGO Design IDs - they are not guessable, and a wrong one either fails to
resolve or silently pulls in a completely different element. Validation reports
it as `missing_parts`, and the model renders with a hole in it.

## The parts you already know

Every part below was checked against this catalogue. **Use them directly, with
no lookup.** Searching for a 2x4 brick is the single biggest waste of a run.

| | 1x1 | 1x2 | 1x3 | 1x4 | 1x6 | 1x8 | 1x10 | 1x12 | 1x16 |
|---|---|---|---|---|---|---|---|---|---|
| **Brick** | `3005` | `3004` | `3622` | `3010` | `3009` | `3008` | `6111` | `6112` | `2465` |
| **Plate** | `3024` | `3023b` | `3623` | `3710` | `3666` | `3460` | `4477` | - | - |

| | 2x2 | 2x3 | 2x4 | 2x6 | 2x8 | 2x10 | 2x12 | 4x6 | 6x6 |
|---|---|---|---|---|---|---|---|---|---|
| **Brick** | `3003` | `3002` | `3001` | `2456` | `3007` | `3006` | - | - | - |
| **Plate** | `3022` | `3021` | `3020` | `3795` | `3034` | `3832` | `2445` | `3032` | `3958` |

Standing up rather than lying down: `3245c` is a brick 1x2x2, `14716` a
1x1x3 column, `2453b` a 1x1x5. A wall five bricks high is four parts, not
twenty.

Also: tiles `3070b` (1x1), `3069b` (1x2), `3068b` (2x2); 45° slopes `3040b`
(2x1), `3039` (2x2), `3038` (2x3); round brick 1x1 `3062b`; cheese slope
`54200`; jumper plate `15573` (1x2 with one centre stud).

Note `3023b`, **not** `3023` - the bare number is a "~Moved to" stub, and the
same is true of a few older plates.

Never search for these. One search per unknown part otherwise, and take the best
hit rather than searching again to confirm it.

## Span it with one part, not four

**Take the longest part that fits the run.** A wall 8 studs long is one `3008`,
not two `3001`s and not four `3004`s. A 10-stud floor edge is `4477`. A column
three bricks high is `14716`.

This is the measured fault in builds like yours, against the 1,801 real sets:

```
parts 6 studs or longer     6% of what this agent places
                           19% of what real sets place
2-stud parts               50% of this agent's bricks, 28% of a real set's
one part, 3003 (2x2)       26% of every part this agent places
```

A quarter of everything, one part. That is not a vocabulary, it is a habit.

It is not about tidiness. Every extra joint is a seam that shows in the render,
a place the build comes apart when it is picked up, and a line of LDraw whose
coordinates can be wrong. Four parts where one belongs is four chances to put
something a stud out, and the checker will find all four.

Working out which part: the run is `n` studs, so take the part `n` long. Where
none exists, take the longest that fits and finish the remainder - 14 studs is
`2465` (1x16) trimmed to fit if it fits the footprint, otherwise `6112` + a
`3004`. Never start from the 2x4 and repeat it.

The same for height. A tower is not forty 2x2 bricks; it is `14716` columns, or
bricks 6 and 8 studs long laid in courses that overlap at the corners the way a
real wall is bonded.

## Three sizes, three jobs

Every part carries a `size_class` on every search result and in
`get_part_details`. It is worked out from the part's own footprint, and it says
which of the three jobs in a build that part is for:

| class | what it is | what it does |
|---|---|---|
| **structural** | 8+ studs of footprint, or 6+ studs long - `3008`, `3001`, `2456`, `3020`, `3958` | the spine: spans, floors, the parts that carry the model and tie it together |
| **medium** | 2 to 7 studs - `3003`, `3004`, `3010`, `3039`, `3069b` | the body: the walls and masses that give it its shape |
| **detail** | one stud or less - `3005`, `3024`, `3070b`, `54200`, `3062b` | what makes it read as the thing it is |

**A model needs all three.** Not as a target to hit - as a consequence of
building something rather than assembling it. Measured over the 1,801 reference
sets, 98.6% of them use all three classes, and the mix is stable at every size:

```
structural  15%       medium  42%       detail  43%
```

Read that twice, because the obvious guess is wrong in both directions. Real
sets are **detail-heavy**: the big parts are a spine, about one part in seven,
not the bulk of the model. And they are never *only* detail either.

This is the axis your builds are furthest off. Across the models this agent has
produced, only 54.8% use all three classes and **13% are built out of a single
one** - 10.7% are almost entirely structural, which is a pile of big bricks, and
half are under 10% structural, which is a heap of small parts with nothing
holding it up. That second one is not a cosmetic fault: a model with no
structural part in it is held together by the studs of 1x1s alone, and it comes
apart when it is picked up. It is the single most common way a build that
validates is still returned as unbuildable.

So, in order:

1. **Lay the spine first.** The baseplate, the floors, the long runs. This is
   what everything else attaches to, and it is the cheapest moment to get the
   footprint right.
2. **Fill the body.** Walls, masses, the courses that make the shape. Use
   `build_ops` `wall` and `box` for anything made of courses - see *Building by
   operation*.
3. **Finish with detail.** Tiles, cheese slopes, round bricks, the signature the
   design brief named. This is the pass that is skipped when a run is going
   badly, and it is the one that decides whether the model reads.

A build that stops after step 2 is a box. A build that never does step 1 is a
pile.

## But do not build only out of these

The table above is the vocabulary, not the palette. A model assembled entirely
from rectangular bricks reads as a staircase of boxes no matter how carefully it
is measured - and the catalogue has 5,399 parts, most of which exist precisely
because a box was the wrong shape.

**Every build with a curved, round, tapered or angled surface needs a search
for that surface.** A car roof is not four stacked plates, it is a curved slope.
A tree is not a green cuboid, it is round bricks and a cone. A chimney is a
cylinder. Whenever you are about to approximate a shape by stepping bricks,
that is the moment to search instead - the part almost certainly exists.

The families worth knowing, with the parts real sets actually reach for. **These
are checked and need no lookup either** - the count is how often the 1,801-set
corpus places one, so they are the common answer rather than a curiosity:

| when you need | reach for | |
|---|---|---|
| **a roof, a bonnet, any 45° face** | `3040b` 2x1 · `3039` 2x2 · `3037` 2x4 | shallower: `4286` 33° 3x1 · `3298` 33° 3x2 |
| **a shallow chamfer, a tiny angle** | `54200` cheese 1x1 · `85984` 31° 1x2 | |
| **a curve - car roof, fender, nose** | `11477` curved 2x1 · `50950` 3x1 · `61678` 4x1 | wider: `15068` 2x2 · `93606` 4x2 |
| **a doorway or an opening spanned** | `4490` arch 1x3 · `3659` 1x4 · `3455` 1x6 | tall: `6005` 1x3x2 · `2339` 1x5x4 |
| **a thin wall - no double thickness** | `4865a` panel 1x2x1 · `87552` 1x2x2 · `4215a` 1x4x3 | `30413` 1x4x1 |
| **a tapered plan - hull, wing, prow** | `6564`/`6565` wedge 3x2 R/L · `41747`/`41748` 2x6 R/L | `4856` 6x4 inverted |
| **round - trunk, barrel, chimney** | `3062b` round 1x1 · `3941` round 2x2 · `6143` reinforced | |
| **a taper or a point** | `4589` cone 1x1 · `59900` with stop | domes: `4740` dish 2x2 · `3960` 4x4 |
| **a surface facing sideways or up** | `44728` bracket 1x2-2x2 down · `99207` up | `2436a` 1x2-1x4 · `99780` 1x2-1x2 up |
| **half a stud of offset** | `15573` jumper 1x2 | |
| **a finished top, no studs showing** | `3070b` tile 1x1 · `3069b` 1x2 · `3068b` 2x2 | |
| **windscreens, plants, animals, figures** | nothing built out of bricks will pass - search the family | `category="Windscreen"`, `"Plant"`, `"Animal"` |

Two whole families are currently absent from this agent's output: **wedges and
panels are 0% of what it builds and 0.9% of a real set.** If a shape tapers in
plan, it is a wedge. If a wall should be one plate thick, it is a panel. Neither
is exotic; both are on the table above.

`search_parts` also reports `other_shape_families` - the families the same
search reached and had no room to show. When nothing in the results is the shape
you pictured, that list is where to look next. Take it: settling for a plain
brick is the one outcome worth another call.

## What you have already found

Every search you run adds its results to a palette that is kept for the whole
project - across subconstructions, across turns. It comes back on every search
as `parts_you_have_found`, and it is in your context as
`<parts_you_have_found>`.

Read it before you search. A part on that list has already been looked up, its
number is real, and searching for it again spends a call to be told what you
were already holding. It is also the answer to "what have I got to build the
next section out of" - the shapes an earlier section found are shapes this one
can use, and a build whose halves were made from different vocabularies looks
like two models glued together.

`search_parts` also returns `companion_parts`: the parts real sets put beside
the ones you found. A wheel rim comes back with its tyre at 66%, a turntable top
with its base at 93%. When a companion is up there, the result you liked is half
of an assembly - place both, or you have modelled a hubcap.

## How to search

`search_parts` is hybrid - exact keyword matching over description, part number
and keywords, fused with semantic vector search over the whole catalogue. Both
kinds of query work.

Catalogue wording, when you know the element:

```
search_parts(query="brick 2 x 4")
search_parts(query="plate round", category="Plate")
search_parts(query="slope", width_studs=2, depth_studs=1)
```

Plain description, when you know the *shape or job* but not the name:

```
search_parts(query="something curved for a car roof")
search_parts(query="a piece that lets two sections pivot")
search_parts(query="round transparent piece for a headlight")
```

Describe the function when the form is hard to name - "a part that holds a bar
at right angles" retrieves better than "bracket thing".

An exact part number or catalogue description always wins: `"3001"` returns part
3001 first, and `"brick 2 x 4"` returns the plain brick rather than a patterned
variant that happens to embed nearby.

**Use `commonness` to sort real parts from obscure variants - not to sort
interesting parts from boring ones.** Every result carries a band: `very
common`, `common`, `uncommon`, `rare`. Anything down to `uncommon` is a real
element that shipped in real sets and is safe to build with. Only `rare` is a
warning, and it means "check whether a plainer part does the same job", not
"use a brick instead".

## Every part has to be doing a job

A varied build is a good sign and it is never the goal. Variety is what a
well-fitted model *comes out* with: a roof needed slopes, an arch needed an
arch, a railing needed bars, and the shape count rose because each shape was the
right one. Working the other way round - reaching for an unusual part so the
build looks designed - produces the thing that actually reads as machine-made: a
model with pieces in it that nobody can explain.

Before you place anything that is not a plain brick, plate or tile, you must be
able to finish this sentence: **"this part is here because ______, and a plate
would not do it."**

- *"…because the roof is pitched, and a plate would step it into a staircase"* -
  place it.
- *"…because the wheel sits in an arch, and a plate would leave a square hole"* -
  place it.
- *"…because it adds interest"* / *"…because the build is all rectangles so far"* -
  that is not a reason. Place the plate.

This cuts both ways, and the other way is the more common fault: when you catch
yourself repeating one brick twenty times to approximate a curve, a slope or
foliage, the part that *is* that shape exists and `search_parts` will find it.
Both faults are the same mistake - choosing a shape by something other than the
job it has to do.

Do not rank by raw `total_uses`. It is dominated by the parts that are in every
set ever made, which are exactly the plain bricks and plates you already have -
ranking by it will hand you a 1x2 brick every time you ask for a shape.

If two differently-worded searches both fail, look at how a real set solved the
same problem. The parts a set actually used beat a third guess at wording.

## Reading `get_part_details`

```
part_id: 3003
description: Brick  2 x  2
bbox: x [-20, 20]  y [-4, 24]  z [-20, 20]
stud_grid:   [(-10, -10), (-10, 10), (10, -10), (10, 10)]
place_height_ldu: 24
```

- `stud_grid` entries are (x, z) offsets from this part's origin - where *other*
  parts attach on top of it.
- `place_height_ldu` is what to subtract from the y of the part below to stack
  this part on it.
- Notes you have written about a part surface here automatically.

## Studless parts terminate a stack

A part with no top studs - `3070b`, `54200`, most slopes and tiles - is a
**terminator**: nothing can attach above it. If you need to build upward, do not
put a tile there.

The jumper `15573` is the exception that buys a half-stud offset: it seats on
two normal positions but presents one stud at the centre, shifting everything
above it by 10 LDU. Use it deliberately when you need half-stud offsets - never
fake them by placing parts at non-grid coordinates.

## One legal off-grid placement

A 1x1 **round** brick or plate (`3062b`, `6141`, `98138`…) may sit at the centre
of four studs - 10 LDU off in both x and z - where the four stud walls grip its
barrel. Its own stud then enters the tube of whatever goes on top. This is a
real connection, the validator knows it, and such a part comes back CONNECTED.

It only works for round parts one stud across. A square 1x1, or a 2x2 round
brick, in the same position grips nothing and is correctly reported as
misaligned.
