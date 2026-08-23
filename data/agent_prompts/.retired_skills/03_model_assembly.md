# Skill: Model Assembly (file structure)

## Single model vs MPD

A one-piece model can be a plain `.ldr` with just a header and type-1 lines.
Anything with distinguishable components should be an MPD: several `0 FILE` blocks
in one file, the **first block being the main model**.

```
0 FILE main.ldr
0 My Model
0 Name: main.ldr
0 Author: LDraw Model Builder Agent
0 !LDRAW_ORG Model
0 !LICENSE Licensed under CC BY 4.0 : see CAreadme.txt

1 16 0 0 0 1 0 0 0 1 0 0 0 1 base.ldr
0 STEP
1 16 0 -8 0 1 0 0 0 1 0 0 0 1 tower.ldr
0 STEP

0 FILE base.ldr
0 Base
0 Name: base.ldr
0 Author: LDraw Model Builder Agent
0 !LDRAW_ORG Model
0 !LICENSE Licensed under CC BY 4.0 : see CAreadme.txt

1 71 0 0 0 1 0 0 0 1 0 0 0 1 3020.dat
0 STEP
...
```

Rules:
- The main model is whichever block comes first. Put it first deliberately.
- A block is referenced by the exact name in its `0 FILE` line.
- Nothing but comments may appear before the first `0 FILE`.
- Blocks referencing each other must not form a cycle.

## Submodel placement and nested coordinates

A submodel reference carries a transform, and everything inside the submodel is
expressed in the submodel's own coordinates. When you place a submodel at
`(0, −6, 20)`, a part inside it at `(0, −32, 0)` ends up at `(0, −38, 20)` in the
finished model.

Build each submodel **around its own origin** — typically with its bottom face at
y = 0 — then position it once in the parent. Do not scatter a submodel's parts
around arbitrary coordinates and try to compensate in the parent transform.

Two submodels that must connect to each other have to line up on the stud grid
*after* their parent transforms are applied. The easiest way to guarantee this is
to give both a parent offset that is a multiple of 20 in x and z, and a multiple
of 8 in y.

## Steps

`0 STEP` ends a building step. Emit one after each meaningful group of parts —
roughly what a real instruction booklet would show on one page. Steps carry no
geometry meaning, so they can never break a model, but they make it reviewable.

## Line format reminder

```
1 <colour> <x> <y> <z> <a b c d e f g h i> <file>
```

The nine matrix values are row-major:

```
| a b c |     x' = a*x + b*y + c*z + X
| d e f |     y' = d*x + e*y + f*z + Y
| g h i |     z' = g*x + h*y + i*z + Z
```

Identity is `1 0 0 0 1 0 0 0 1`. Most placements use it.

## Colour discipline

Inside a model, give parts real colour codes rather than 16. Colour 16 on a part
in a model means "inherit", and at the top level that resolves to a default that
makes every part look the same. Reserve 16 for submodel references, where it
correctly passes the parent's colour through.

## Before you finish

- Every submodel is referenced from somewhere, directly or transitively from the
  main model. An unreferenced block is dead weight that never renders.
- No block is named the same as another.
- File ends with a newline.
