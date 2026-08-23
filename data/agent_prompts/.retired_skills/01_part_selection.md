# Skill: Part Selection

## Never invent a part number

Every `.dat` you reference must have come back from `search_parts` or
`get_part_details`. Part numbers are LEGO Design IDs - they are not guessable, and
a wrong one either fails to resolve or silently pulls in a completely different
element. If you are unsure a part exists, look it up.

## How to search

`search_parts` is hybrid: exact keyword matching over description, part number
and keywords, fused with semantic vector search over the whole catalogue. Both
kinds of query work, and it can be narrowed by category and by size in studs.

Catalogue wording, when you know the element you want:

```
search_parts(query="brick 2 x 4")
search_parts(query="plate round", category="Plate")
search_parts(query="slope", width_studs=2, depth_studs=1)
```

Plain description, when you know the *shape or job* but not the name - this is
the case where the old keyword-only search returned nothing and you had to
guess:

```
search_parts(query="something curved for a car roof")
search_parts(query="a piece that lets two sections pivot")
search_parts(query="round transparent piece for a headlight")
```

Describe the function if the form is hard to name. "A part that holds a bar at
right angles" retrieves better than "bracket thing".

An exact part number or an exact catalogue description always wins: `"3001"`
returns part 3001 first, and `"brick 2 x 4"` returns the plain brick rather than
a patterned variant that happens to embed nearby.

Prefer parts with a high `total_uses` - those are common, well-modelled elements
that appear in many real sets. A part used in 3 sets is likely an obscure variant.

If you cannot find a part after two differently-worded searches, look at how a
real set solved the same problem (see the reference-sets skill) - the parts a
set actually used are a better starting point than a third guess at wording.

## Reading `get_part_details`

It returns the exact bounding box in LDU plus the derived stud grid:

```
part_id: 3003
description: Brick  2 x  2
bbox: x [-20, 20]  y [-4, 24]  z [-20, 20]
studs_top:   [(-10, -10), (-10, 10), (10, -10), (10, 10)]
seats_bottom:[(-10, -10), (-10, 10), (10, -10), (10, 10)]
```

- `y` runs from −4 to 24: the studs occupy −4…0 and the body 0…24. The part's
  **origin sits at the top face**, with stud bases at y=0.
- `studs_top` are where *other* parts can attach on top of this one.
- `seats_bottom` are where this part must meet studs below it.

To stack part B on part A at A's stud `(sx, sz)`:

```
B.y = A.y - (A.body_top_offset)      # see the stacking recipes skill
B.x = A.x + sx
B.z = A.z + sz
```

## The core System vocabulary

These cover most builds. Confirm each with a tool call before use - descriptions
here are for orientation, not for copying blindly.

| Part | What it is | Footprint |
|---|---|---|
| `3005` | Brick 1 x 1 | 1x1 |
| `3004` | Brick 1 x 2 | 1x2 |
| `3622` | Brick 1 x 3 | 1x3 |
| `3010` | Brick 1 x 4 | 1x4 |
| `3003` | Brick 2 x 2 | 2x2 |
| `3001` | Brick 2 x 4 | 2x4 |
| `3024` | Plate 1 x 1 | 1x1 |
| `3023` | Plate 1 x 2 | 1x2 |
| `3022` | Plate 2 x 2 | 2x2 |
| `3020` | Plate 2 x 4 | 2x4 |
| `3070b` | Tile 1 x 1 | 1x1, no top stud |
| `3069b` | Tile 1 x 2 | 1x2, no top stud |
| `54200` | Slope 1 x 1 x 0.667 ("cheese") | 1x1, no top stud |
| `15573` | Plate 1 x 2 with centre stud ("jumper") | 1x2, one stud at centre |

## Tiles, slopes and other studless parts

A part with no top studs (`3070b`, `54200`, most slopes) is a **terminator**:
nothing can attach above it. If you need to build upward, do not put a tile there.

A jumper (`15573`) is the exception that lets you offset by half a stud: it seats
on two normal positions but presents one stud at the centre, shifting everything
above it by 10 LDU. Use it deliberately when you need half-stud offsets - do not
fake them by placing parts at non-grid coordinates.

## Colours

Use real LDraw colour codes, not 16, on parts inside a model. Common ones:

`0` black · `1` blue · `2` green · `4` red · `14` yellow · `15` white ·
`19` tan · `70` reddish brown · `71` light bluish grey · `72` dark bluish grey ·
`28` dark tan · `84` medium nougat

Colours 71 and 72 are the modern greys; 7 and 8 are the pre-2004 versions.
