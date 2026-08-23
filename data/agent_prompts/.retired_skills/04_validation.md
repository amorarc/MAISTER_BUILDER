# Skill: Validation and Repair

Call `validate_model` after every write. A model you have not validated is a model
you do not know is correct.

## What comes back

```json
{
  "missing_parts": [{"part": "3001x.dat", "references": 2, "lines": [3, 4]}],
  "connectivity": {
    "parts": 11,
    "connected": 5,
    "misaligned": 4,
    "unverified": 2,
    "subassemblies": 6,
    "misaligned_parts": [...],
    "fragmented_submodels": [{"submodel": "dog.ldr", "pieces": 6, "parts": 11}]
  },
  "collision": { "collisions": 13, "deep_collisions": 5, "possibly_disconnected": 2 }
}
```

## Read it in this order

### 0. `missing_parts` — fix these first, before anything else

Every entry names a part number that does not exist: not a part in the catalogue,
not a file in the LDraw library, and not a submodel defined inside your own file.
The verdict names the numbers, and each entry gives the source lines that use it:

```
FAIL - fix missing_parts (3001x.dat, wheel.dat - no such part).
```

Nothing downstream is trustworthy while these are present — a part that does not
exist has no geometry, so it cannot be checked for grid alignment or overlap, and
it renders as a hole. This is almost always a part number you guessed. Do not
guess again: call `search_parts` for the piece you meant and use the `part_id` it
gives back, or — if you meant a sub-assembly of your own — add the matching
`0 FILE <name>` block to the file so the reference resolves.

Fix these, rewrite, and validate again before reading the sections below.

### 1. `misaligned` — hard errors, fix every one

A misaligned part has no valid stud connection but sits within one stud pitch of
one. It is off the grid by a non-grid amount: physically unbuildable. Each entry
gives the source line, the part, its position and the gap:

```
line 515: 3024.dat @ (-6, -78, 38)  nearest mating point is 7.21 LDU away
```

To fix: find the part below it, list that part's stud coordinates, and move this
part onto one of them. The gap value tells you how far off you are — a gap of
7.21 with a 1x1 plate usually means the centre is a few LDU off in two axes.

**One exception, and it is a technique worth using.** A 1x1 *round* brick or
plate (3062a, 6141, 98138…) may sit at the centre of four studs — 10 LDU off in
both x and z — where the four stud walls grip its barrel. Its own stud then
enters the tube of whatever plate goes on top. This is a legal connection, the
validator knows it, and such a part comes back CONNECTED, not misaligned. It
only works for round parts one stud across: a square 1x1 or a 2x2 round brick in
the same position grips nothing and is reported, correctly, as misaligned.

### 2. `fragmented_submodels` — informational, read it and judge

`{"submodel": "dog.ldr", "pieces": 6, "parts": 11}` means those 11 parts form six
groups the checker found no connection between.

**This does not fail a model.** Plenty of correct builds are in several pieces on
purpose — a minifig standing beside a car, a removable roof, anything joined by a
clip, bar, hinge or Technic pin the checker cannot see. A high count on a model
you *intended* to be one solid lump is worth a look; a count on a model with
separable parts is just a description of it.

When you do decide a split is wrong, work bottom-up: confirm the lowest layer is
on the grid, then check each layer seats on the one below it. Usually the same
misaligned parts from step 1 are the cause, and fixing those fixes this too.

### 3. `unverified` — usually fine, worth a glance

No stud connection and nothing nearby. Either the part is genuinely floating, or it
is held by a joint the checker does not model: clips, bars, Technic pins, hinges,
brackets, minifig hands. If you used one of those deliberately, this is expected.
If you did not, the part is floating in space.

### 4. `collisions` — informational, do not chase

Correctly connected bricks **overlap by about 4 LDU on purpose** (the stud sits
inside the anti-stud), and a bracket's bounding box legitimately encloses the brick
it wraps. A real, hand-verified LEGO set reports thousands of "collisions".

Treat the count as a relative signal only. **Never move a part off the stud grid to
silence a collision** — that trades a false alarm for a genuine defect.

The one collision worth investigating: two unrotated parts of the same footprint
overlapping substantially, e.g. two 1x1 plates 12 LDU apart. A 1x1 plate is 20 LDU
wide, so they cannot be closer than 20. That is a true interpenetration.

## The repair pass

`write_model` replaces the whole file, so a repair is one pass over every error,
not one pass per error:

1. Read `misaligned_parts` — all of them.
2. For each, work out the correct placement: `y = below.y - this_part_height`,
   x/z on one of the stud coordinates of the part underneath. Call
   `get_part_details` on a part below only when you do not already know its studs.
3. Rewrite the file **once**, with every fix applied.
4. Validate again.

Two passes should clear a model. If the same error survives a third pass, stop
and report it — repeating the pass a fourth time is a loop, and the placement is
wrong for a reason you have not found yet. Say which part and what the gap was.

If a repair makes the count *worse*, the fix was wrong: go back to the last state
that validated better rather than layering another guess on top.

## Done means

- `missing_parts` absent — every number in the file is a real part
- `misaligned == 0` — this is the bar
- `unverified` limited to parts you attached with a non-stud joint on purpose
- `subassemblies` and `fragmented_submodels` at whatever they are. More than one
  piece is not a failure and never blocks finishing.

If you cannot reach that, say so explicitly and name the parts you could not
resolve. Do not report a model as finished while validation still fails.
