# LDraw Knowledge Base

Distilled from the official LDraw.org documentation set (https://www.ldraw.org/docs-main.html)
and cross-checked against real files in `data/lego_pieces/` and `data/ldraw_omr_sets/`.

Sources:
- File Format 1.0.2 — https://www.ldraw.org/article/218.html
- BFC extension — https://www.ldraw.org/article/415.html
- !COLOUR extension — https://www.ldraw.org/article/299.html
- !TEXMAP extension — https://www.ldraw.org/texmap-spec.html
- !CATEGORY / !KEYWORDS — https://www.ldraw.org/article/340.html
- MPD / !DATA — https://www.ldraw.org/article/47.html
- Parts Library Specification — https://www.ldraw.org/article/512.html
- Header Specification — https://www.ldraw.org/article/398.html
- Part Number Specification — https://www.ldraw.org/part-number-spec.html
- OMR Specification — https://www.ldraw.org/article/593.html
- Primitives Reference — https://wiki.ldraw.org/wiki/Primitives_Reference
- Common Error Check Messages — https://www.ldraw.org/docs-main/ldraw-org-quick-reference-guides/common-error-check-messages.html

---

## 1. Fundamentals

### 1.1 File encoding and structure
- Plain text, **UTF-8 without BOM**. Extensions: `.ldr` (model), `.dat` (part/primitive), `.mpd` (multi-part document).
- One command per line. Lines **must** end `<CR><LF>` for official library files (error check #47); readers should accept bare `<LF>`.
- Tokens are whitespace-delimited; whitespace = spaces (#32) and/or tabs (#9). Leading whitespace is allowed.
- The first non-whitespace character of a meaningful line is the **line type digit 0–5**. Unknown types are ignored.
- Blank / whitespace-only lines are legal and have no effect. No line-length limit (but keep `!KEYWORDS`/`!HELP` lines ≤ 80 / ~50 chars).
- **Exception to tokenization:** in a type-1 line the filename field consumes the rest of the line (spaces allowed inside filenames); only leading/trailing whitespace is stripped.

### 1.2 Coordinate system — the single most important fact
- **Right-handed, with −Y pointing UP.** Y increases *downward*. Getting this wrong flips every model.
- +X is right, +Z is toward the viewer/front (right-handed with −Y up).

### 1.3 LDraw Unit (LDU)
| Quantity | LDU |
|---|---|
| Stud pitch (brick width/depth per stud) | **20** |
| Brick height (body) | **24** |
| Plate height | **8** (brick = 3 plates) |
| Stud diameter | 12 |
| Stud height above surface | 4 |
| Technic axle/pin hole radius | 6 (Ø12) |
| 1 LDU | ≈ 0.4 mm ≈ 1/64 inch |

Derived: 1 stud = 20 LDU = 8 mm. A 2×4 brick spans x ∈ [−40, 40], z ∈ [−20, 20], y ∈ [0, 24].

---

## 2. Line types — exact grammar

### Type 0 — comment / meta
```
0 // <comment>            <- preferred comment form
0 <comment>               <- legacy; first type-0 line of a file is its TITLE
0 !<METACOMMAND> <args>   <- the '!' positively identifies a registered meta command
```

### Type 1 — sub-file reference
```
1 <colour> x y z a b c d e f g h i <file>
```
The nine values are the top-left 3×3 of a 4×4 transform, **row-major**:

```
| a b c x |        u' = a·u + b·v + c·w + x
| d e f y |        v' = d·u + e·v + f·w + y
| g h i z |        w' = g·u + h·v + i·w + z
| 0 0 0 1 |
```
Identity, unrotated placement at the origin is:
`1 16 0 0 0 1 0 0 0 1 0 0 0 1 3001.dat`

Reference resolution order: `LDRAW/PARTS`, `LDRAW/P`, `LDRAW/MODELS`, current directory, relative path, absolute path. Sub-parts are referenced as `s\name.dat`, hi-res primitives as `48\name.dat`, low-res as `8\name.dat` (backslash is the canonical separator in the library).

### Type 2 — line (edge)
```
2 <colour> x1 y1 z1 x2 y2 z2
```
Must use colour **24** in library parts.

### Type 3 — triangle
```
3 <colour> x1 y1 z1 x2 y2 z2 x3 y3 z3
```

### Type 4 — quadrilateral
```
4 <colour> x1 y1 z1 x2 y2 z2 x3 y3 z3 x4 y4 z4
```
The four points **must be coplanar and the quad must be convex** — vertices in order around the perimeter. Non-convex ⇒ split into triangles.

### Type 5 — optional (conditional) line
```
5 <colour> x1 y1 z1 x2 y2 z2 x3 y3 z3 x4 y4 z4
```
Segment 1→2 is drawn only when control points 3 and 4 project to the same side of the line 1–2. Used for silhouette edges of curved surfaces. Colour 24.

---

## 3. Colours

### 3.1 The two magic codes
- **16 — main colour.** Geometry drawn in 16 inherits the colour of the type-1 line that referenced the file. Surfaces inside parts are almost always 16.
- **24 — complement/edge colour.** Inherits the *contrasting edge* colour of the referencing line. Reserved for line types 2 and 5.
- **Hard rule:** polygons (3/4) must **never** use 24; lines (2/5) must **always** use 24 in library parts (error checks #14, #67).

### 3.2 Direct colours
`0x2RRGGBB` — e.g. `0x2008000` = RGB(0,128,0). Hex letters **uppercase**. Allowed in patterns/stickers only; everywhere else use a code defined in `LDConfig.ldr`.

### 3.3 Common codes
| Code | Name | RGB | Edge |
|---|---|---|---|
| 0 | Black | #1B2A34 | #808080 |
| 1 | Blue | #1E5AA8 | #333333 |
| 2 | Green | #00852B | #333333 |
| 3 | Dark_Turquoise | #069D9F | #333333 |
| 4 | Red | #B40000 | #333333 |
| 5 | Dark_Pink | #D3359D | #333333 |
| 6 | Brown | #543324 | #1E1E1E |
| 7 | Light_Grey | #8A928D | #333333 |
| 8 | Dark_Grey | #545955 | #333333 |
| 9 | Light_Blue | #97CBD9 | #333333 |
| 10 | Bright_Green | #58AB41 | #333333 |
| 11 | Light_Turquoise | #00AAA4 | #333333 |
| 12 | Salmon | #F06D61 | #333333 |
| 13 | Pink | #F6A9BB | #333333 |
| 14 | Yellow | #FAC80A | #333333 |
| 15 | White | #F4F4F4 | #333333 |
| 33 | Trans_Dark_Blue | #0020A0 | α=128 |
| 36 | Trans_Red | #C91A09 | α=128 |
| 40 | Trans_Brown | #635F52 | α=128 |
| 46 | Trans_Yellow | #F5CD2F | α=128 |
| 47 | Trans_Clear | #FCFCFC | α=128 |

Modern LEGO grey: **71 Light_Bluish_Grey** and **72 Dark_Bluish_Grey** (post-2004 sets) vs legacy 7 / 8.

### 3.4 !COLOUR definition syntax
```
0 !COLOUR <name> CODE <x> VALUE <#RRGGBB> EDGE <#RRGGBB|code>
          [ALPHA <0-255>] [LUMINANCE <0-255>] [<finish>]
```
Finish (at most one): `CHROME | PEARLESCENT | RUBBER | MATTE_METALLIC | METAL |
MATERIAL GLITTER VALUE #RRGGBB FRACTION f VFRACTION vf SIZE s |
MATERIAL SPECKLE VALUE #RRGGBB FRACTION f SIZE s |
MATERIAL FABRIC [VELVET|CANVAS|STRING|FUR]`
Standard transparency is `ALPHA 128`.

---

## 4. BFC — Back Face Culling

### 4.1 Statements
```
0 BFC NOCERTIFY
0 BFC CERTIFY [CW|CCW]        <- CCW is implied if omitted
0 BFC CW | CCW
0 BFC CLIP [CW|CCW] | NOCLIP
0 BFC INVERTNEXT
```
All keywords are **case-sensitive uppercase**.

### 4.2 Rules
- `CERTIFY`/`NOCERTIFY` must appear **before any type 1–5 line**, and only once per file. Absent ⇒ file is treated as NOCERTIFY and culling is disabled for it.
- Default winding is **CCW**; default cull state is **CLIP**.
- Winding is judged **viewing the polygon from its front face**, and applies only to the current file — it is not inherited by subfiles.
- `INVERTNEXT` inverts exactly the next type-1 reference; it must be immediately followed by a type-1 line (error check #51). It is a boolean that accumulates down the reference tree (double inversion cancels).
- **A negative determinant of the accumulated matrix flips the effective winding.** Mirroring a subfile with e.g. `-1 0 0 0 1 0 0 0 1` inverts it; combining that with `INVERTNEXT` cancels back to normal.
- Culling actually happens only when the current file *and* every ancestor in the reference branch are certified and none disabled clipping.
- `0 BFC CERTIFY CCW` is **mandatory for all official library parts**, with correct winding throughout.
- Renderer algorithm carries three accumulators down the recursion: `AccumCull = AccumCull AND LocalCull`, `AccumInvert = AccumInvert XOR InvertNext`, `AccumTransform = AccumTransform × LocalMatrix`. Reset `InvertNext` after every type-1 line.

---

## 5. Part file header (official library)

Mandatory, **in this exact order**:
```
0 <Part Description>
0 Name: <filename.dat>
0 Author: <Real Name> [username]
0 !LDRAW_ORG <type> [qualifiers] ORIGINAL|UPDATE YYYY-RR
0 !LICENSE Licensed under CC BY 4.0 : see CAreadme.txt
```
Then, in any order: `0 BFC CERTIFY CCW`, `0 !HELP`, `0 !CATEGORY`, `0 !KEYWORDS`, `0 !CMDLINE`, `0 !HISTORY`, `0 //` comments.

Real example (`data/lego_pieces/3001.dat`):
```
0 Brick  2 x  4
0 Name: 3001.dat
0 Author: James Jessiman
0 !LDRAW_ORG Part UPDATE 2004-03
0 !LICENSE Licensed under CC BY 4.0 : see CAreadme.txt

0 BFC CERTIFY CCW

0 !HISTORY 2002-05-07 {unknown} BFC Certification
0 !HISTORY 2004-02-08 [Steffen] used s\3001s01.dat
```

### 5.1 !LDRAW_ORG types
`Part | Subpart | Primitive | 8_Primitive | 48_Primitive | Shortcut`, each also available as
`Unofficial_Part`, `Unofficial_Subpart`, … for files not yet released.
Optional qualifiers: `Alias`, `Flexible_Section`, `Physical_Colour` (deprecated).

### 5.2 !LICENSE values
- `Licensed under CC BY 4.0 : see CAreadme.txt` — current standard.
- `Licensed under CC BY 2.0 and CC BY 4.0 : see CAreadme.txt` — legacy dual.
- `Redistributable under CCAL version 2.0 : see CAreadme.txt` — legacy.
- `Not redistributable : see NonCAreadme.txt`.

### 5.3 !HISTORY
```
0 !HISTORY YYYY-MM-DD [username] <description>
0 !HISTORY YYYY-MM-DD {Real Name} <description>
```
Square brackets for LDraw.org usernames, braces for non-registered real names. Only `[]` accepted for usernames (error check #60).

### 5.4 Description conventions
- Dimensions padded so numbers align: `Brick  2 x  4`, `Plate  1 x 10`. Max 2 decimals.
- Prefix `~` = subpart or obsolete/hidden part; `=` = alias; `|` or `~|` = third-party part.
- Patterned: `<base description> with <pattern description> Pattern`.
- Stickers: `Sticker <z-dim> x <x-dim> with <description>`.
- Avoid "new"/"old" in descriptions; no tabs; no leading spaces.

### 5.5 !CATEGORY and !KEYWORDS
```
0 !CATEGORY <single category name>
0 !KEYWORDS <kw1>, <kw2>, <kw3>
```
- `!CATEGORY`: parts and subparts only, at most once. If absent, category = **first word of the description**. ~88 official categories (Brick, Plate, Slope, Tile, Panel, Arch, Cone, Cylinder, Sphere, Wedge, Technic, Hinge, Minifig, Sticker, Animal, Train, Duplo, Moved, Obsolete, Helper, …).
- `!KEYWORDS`: parts and models only, may repeat; comma-delimited; ≤80 chars/line; do not repeat words already in the description or the part number. Cross-references encouraged: `Bricklink 973pb0042`, `set 6285`.
- Patterned parts and stickers **require** at least one `!KEYWORDS Set <number>` entry.

---

## 6. Geometry quality rules (official library)

- **Precision:** 3 decimals for ordinary geometry, 4 for hi-res/scalable primitives, **5 maximum**. Strip trailing zeros; strip leading zeros except the one before the decimal point.
- **Matrices:** never singular; no all-zero rows/columns.
- **Angles:** every interior angle of a triangle or quad between **0.025° and 179.9°** (no degenerate slivers, no collinear vertices).
- **Coplanarity:** a quad's two triangles must have normals within **3°**; **< 1° is required** unless justified.
- **No duplicates / overlaps:** no identical lines, no duplicate vertices in one polygon, no overlapping coplanar surfaces, no overlapping conditional lines (except complementary curved-primitive edges).
- **T-junctions** are flagged as warnings — abutting polygons should share vertices exactly, otherwise cracks appear when rendered.
- **Orientation:** studs point up (i.e. toward **−Y**). Origin is centred on the topmost stud group with **stud bases at y = 0** (so studs occupy y ∈ [−4, 0] and the brick body y ∈ [0, 24]). Hinged parts put the origin at the rotation point.
- Prefer existing **primitives** over hand-built geometry; prefer **subparts** (`s\…`) for repeated internal structures.
- Body of a part may contain only: comments, BFC statements, `!TEXMAP` directives, and geometry.
- Forbidden in official parts: `0 WRITE`, `0 PRINT`, `0 ROTATION`, `0 COLOR`, embedded POV-Ray code, `0 BFC CERTIFY INVERTNEXT`.

### 6.1 Filenames
Max 25 chars including extension, characters `a-z 0-9 _ -`, all lowercase, `.dat` extension. Textures are `.png`. `0 Name:` must match the actual filename (error check #22).

---

## 7. Primitives cheat-sheet

Naming pattern `n-f<primitive>[r]` where `n/f` is the fraction of a full circle and the optional trailing number is a radius in LDU.

| Family | Meaning | Default geometry |
|---|---|---|
| `n-fedge` | circular edge arc | radius 1, {x,z} plane, centre at origin |
| `n-fdisc` | filled disc sector | radius 1, {x,z} plane |
| `n-fndis` | inverse disc (disc→bounding square padding) | radius 1 |
| `n-fchrd` / `n-fchrde` | chord segment / with edge | radius 1 |
| `n-fring<r>` | flat annulus, inner radius r, outer r+1 | e.g. `1-4ring3` = quarter, r 3→4 |
| `n-fcyli` | cylinder wall **with** conditional lines (preferred) | radius 1, height 1 along +Y |
| `n-fcyli2` | cylinder wall without conditional lines | special cases only |
| `n-fcylo` / `n-fcylc` | open-ended / closed-top cylinder | radius 1, height 1 |
| `n-fcyls` | cylinder cut by a 45° plane | radius 1 |
| `n-fcon<r>` | cone, inner radius r, outer r+1 | height 1 along +Y |
| `n-fsphe` | sphere section (1-8, 2-8, 4-8, 8-8) | radius 1, centred at origin |
| `t<ff><sec><radius>` | torus; ff=01/02/04/08/16/32/48 sweep, sec = i/o/q, radius has implied decimals | `t04i1333` = quarter torus, inner, minor r 0.1333 |
| `box`, `box5`, `box4`, `box3`, `box2`, `box0` | cuboids with N faces | 2 LDU per side, centred (box5 is 2×1×2, origin at missing face) |
| `rect`, `rect1`, `rect2a`, `rect2p`, `rect3` | rectangles with N edge lines | 2 LDU square, {x,z}, centred |
| `triangle` | isosceles right triangle | 1 LDU legs, origin at right angle |
| `stud`, `stud2`, `stud3`, `stud4`, … | studs (solid, hollow, open, etc.) | Ø12, height 4 |
| `studlogo`, `studlogo2`, `studlogo3` | studs with LEGO logo | |
| `axle`, `axleend`, `axlehole`, `axlehol2…9` | Technic axle cross-section & holes | scalable in Y only |
| `bush`, `bushlock`, `connect`, `confric` | Technic bushes / connectors | **must not be scaled** |

**Resolutions:** default = 16-segment circle (`p/`), high = 48-segment (`p/48/`, referenced as `48\…`), low = 8-segment (`p/8/`, referenced as `8\…`). A hi-res primitive filename must be referenced with the `48\` prefix (error check #36).

**Scaling rules:**
- 2D primitives need a non-zero Y scale in the matrix even though they are flat (avoids singular matrices).
- Circular primitives: scale x and z equally to stay circular; unequal scaling makes ellipses (legal but often flagged).
- `rect*` primitives must not be rotated to non-right angles.
- Technic primitives (axles, bushes, connectors) are **non-scalable** except axle length along Y (error checks #49/#50).

---

## 8. MPD (multi-part document)

```
0 FILE <filename>
… LDraw content …
0 FILE <another filename>
… content …
0 NOFILE                       <- terminates a block when non-LDraw follows
0 !DATA <filename.png>
0 !: <base64 chunk, ≤80 chars>
0 !: <base64 chunk>
```
- The **first block is the main model**; other blocks render only if referenced (directly or transitively).
- Any content before the first `0 FILE` / `0 !DATA` is discarded, and non-comment LDraw code there is an error.
- Blocks reference each other by the exact name given in `0 FILE`.
- `0 !:` base64 lines should all be the same length and a multiple of 4 characters, except the last.
- The spec explicitly notes there are **no formal scoping/namespace or case-sensitivity rules** for internal names — so use globally unique, prefixed names.

---

## 9. !TEXMAP (texture mapping)

```
0 !TEXMAP START <method> <params> <pngfile> [GLOSSMAP <pngfile>]
0 !: <textured geometry line>
0 !TEXMAP FALLBACK
<untextured fallback geometry>
0 !TEXMAP END
```
- `NEXT` instead of `START` applies the texture to the next 1–5 lines only and forbids `FALLBACK`.
- `0 !:` prefixes geometry that non-TEXMAP-aware readers must skip; it cannot nest.
- Textures stack; `END` pops. A texture also ends at end-of-file or at a `STEP`.
- Methods:
  - `PLANAR x1 y1 z1 x2 y2 z2 x3 y3 z3` — P1→P2 gives U, P1→P3 gives V.
  - `CYLINDRICAL x1 y1 z1 x2 y2 z2 x3 y3 z3 a` — bottom centre, top centre, edge point, sweep angle a.
  - `SPHERICAL x1 y1 z1 x2 y2 z2 x3 y3 z3 a b` — centre, surface point, orientation point, two angles.
- PNG lookup tries `textures/<name>` first, then the bare name. Quote filenames containing spaces.

---

## 10. Model files and the OMR

### 10.1 Model header
```
0 FILE <Set Number> - <name>.ldr
0 <short title>
0 Name: <same as FILE>
0 Author: <Real Name> [username]
0 !LDRAW_ORG Model            (or Unofficial_Model)
0 !LICENSE Licensed under CC BY 4.0 : see CAreadme.txt
0 !THEME <LEGO theme>
0 !KEYWORDS …
0 !HISTORY …
```
- MPD filename: `<Set Number>[-<Qualifier>] - <Set Name>[ - <Sub Model Name>].mpd`.
- Internal files: `<Set Number>[-<Qualifier>] - <individual name>.ldr`.
- One file per logical component (vehicle, minifig, building) — see `data/ldraw_omr_sets/10036-1_Pizza-To-Go.mpd` for a canonical example.
- **Mirrored geometry (negative-determinant matrices) is strongly discouraged in models** — it corrupts parts lists and rendering. Use a real rotation instead.
- Only replicas of actual LEGO sets are accepted in the OMR; MOCs are not.
- Unofficial parts may be embedded as MPD subfiles named by part number. Substituting an unpatterned part for an unavailable patterned one is allowed; note it in a comment.

### 10.2 Model meta commands
- `0 STEP` — end of a building step. `0 CLEAR`, `0 PAUSE`, `0 SAVE`, `0 WRITE`/`0 PRINT` are legacy ldraw.exe commands, valid in models but **banned in parts**.
- `0 ROTSTEP <x> <y> <z> [ABS|REL|ADD|END]` and `0 !LPUB …` are LPub3D/MLCad extensions, not part of the core spec — common in OMR files but ignore them when authoring library parts.

---

## 11. Practical matrix recipes

Rotation about **Y** (the vertical axis) by θ, with −Y up:
```
1 <c> x y z  cosθ 0 sinθ  0 1 0  -sinθ 0 cosθ  part.dat
```
| θ | a b c d e f g h i |
|---|---|
| 0° | `1 0 0 0 1 0 0 0 1` |
| 90° | `0 0 1 0 1 0 -1 0 0` |
| 180° | `-1 0 0 0 1 0 0 0 -1` |
| 270° | `0 0 -1 0 1 0 1 0 0` |
| 30° | `0.866 0 0.5 0 1 0 -0.5 0 0.866` |
| 45° | `0.7071 0 0.7071 0 1 0 -0.7071 0 0.7071` |

Upside-down (rotate 180° about Z, e.g. an inverted brick):
`-1 0 0 0 -1 0 0 0 1` — determinant +1, no BFC inversion needed.

Mirror across X (`-1 0 0 0 1 0 0 0 1`) has determinant −1: it flips winding and duplicates a part that may not exist as a real mould. Avoid in models.

Stacking maths (remember Y grows downward):
- Place a brick one brick **above** another: subtract 24 from y.
- One plate above: subtract 8 from y.
- Move one stud right: add 20 to x.

---

## 12. Authoring checklist (what "excellent quality" means)

Format
1. UTF-8, no BOM, CRLF line endings.
2. Header in exact order: description, `0 Name:`, `0 Author:`, `!LDRAW_ORG`, `!LICENSE`.
3. `0 Name:` matches the real filename; filename ≤25 chars, lowercase, `.dat`.
4. `0 BFC CERTIFY CCW` present before any geometry (parts).
5. `!HISTORY` entries dated `YYYY-MM-DD` with `[username]` or `{Real Name}`.
6. Comments use `0 //`, never bare `0 <text>` (except the title line).

Colour
7. Surfaces use colour 16 (or a real LDConfig code / direct colour for patterns); never 24.
8. Lines and conditional lines use colour 24; never 1/3/4.

Geometry
9. All quads planar (<1° normal deviation) and convex.
10. No duplicate lines, duplicate vertices, collinear vertices, or overlapping coplanar faces.
11. No T-junctions — abutting faces share exact vertices.
12. Consistent CCW winding when viewed from outside; `BFC INVERTNEXT` before every mirrored/inverted subfile, and immediately followed by the type-1 line.
13. Coordinates ≤5 decimals (3 typical); no all-zero or singular matrices.
14. Reuse primitives and subparts instead of raw polygons; respect each primitive's scaling restrictions.
15. Studs up = −Y; origin at the topmost stud group with stud bases at y=0.

Models
16. Use MPD with `0 FILE` blocks, main model first, one submodel per logical assembly.
17. Snap positions to the 20/24/8 LDU grid unless the part legitimately allows otherwise.
18. Never mirror parts to fake a variant.
19. Use `0 STEP` to express build order.
