# Checking your work

You get two kinds of feedback, and they do not overlap. A model can pass one
completely and fail the other. `validate_model` gives you both in one call.

| | the grid | the eyes |
|---|---|---|
| asks | is it **buildable**? | does it **look right**? |
| catches | off-grid parts, overlaps, invented part numbers | wrong shape, wrong proportions, floating parts, missing features |
| blind to | whether it resembles anything at all | whether the studs actually line up |

A grey lump of correctly-connected bricks passes the grid with full marks. A
model that looks perfect in a render may be held together by nothing. This is
why one call does both, every time, with no way to ask for one of them.

**You get the eyes even when the grid check fails.** An overlap is one line out
of a hundred; the shape those hundred lines make is a separate fact and it is
still true while the overlap is there. Read both reports and fix both in the
same edit - repairing the arithmetic first and discovering next iteration that
the shape was wrong too is two rounds for one problem.

---

# Channel 1 - `validate_model`

Call it after every write.

## Read it in this order

### 0. `missing_parts` - fix these before anything else

Each entry names a part number that exists nowhere: not in the catalogue, not in
the LDraw library, not as a submodel of your own file. The verdict names them:

```
FAIL - fix missing_parts (3001x.dat, wheel.dat - no such part).
```

Nothing downstream is trustworthy while these are present - a part that does not
exist has no geometry, so it cannot be checked for alignment or overlap, and it
renders as a hole. This is almost always a part number you guessed. Do not guess
again: `search_parts` for the piece you meant and use the `part_id` it returns,
or add the matching `0 FILE <name>` block if you meant a sub-assembly of your
own.

### 1. `misaligned_parts` - hard errors, fix every one

A misaligned part has no valid stud connection but sits within one stud pitch of
one. It is off the grid by a non-grid amount: physically unbuildable.

```
line 515: 3024.dat @ (-6, -78, 38)  nearest mating point is 7.21 LDU away
```

To fix: find the part below it, list that part's stud coordinates, and move this
part onto one of them. The gap tells you how far off you are.

### 1b. `lattice` - read this *before* the misaligned list

Present when the model is built across more than one stud grid. It is the cause;
`misaligned_parts` is the symptom, and there is one symptom per part on the
losing side:

```json
"lattice": {
  "on_two_lattices": true,
  "parts_on_the_wrong_one": 68,
  "detail": "x: 36 part(s) half a stud off, z: 33 part(s) half a stud off",
  "move_these": [{"line": 9, "part": "3958.dat", "move": "z +10"}]
}
```

**Do not fix these one at a time.** Twenty-two "off the grid" rows can be one
mistake made once - a floor laid on one grid and everything else built on the
other. Apply the `move` against each line in `move_these` and the whole group
joins the rest of the model. The move is the same for most of them.

If `lattice_fixed` is in the report instead, it has already been done for you:
the parts were shifted onto the majority's grid, line numbers unchanged. Read
what is left rather than repeating it.

### 2. `overlapping_parts` - hard errors, and each carries its own fix

Two pieces sharing the same solid space: one brick buried inside another, or the
same brick placed twice. **Every entry carries `suggested_move`** - the exact
axis and distance in LDU that resolves it, already on the grid - and `fix` says
it in words.

Apply that move, or delete the duplicate. Do not invent a different one, and
**never move a part off the stud grid to clear an overlap** - that trades one
defect for a worse one.

`shared_ldu3` is how much solid plastic the two actually share, measured off the
parts' real shapes rather than their bounding boxes. It is not a score to argue
with: a 1x1 plate is 3,200 cubic LDU in total, so an entry reading four figures
is a substantial piece of one part inside another. Slopes, wedges, brackets,
dishes and round parts are all measured the same way as a plain brick, and so is
a part at any rotation.

The other direction holds too, and it is why you should not "fix" what is not
listed: parts that are correctly built share **nothing**. A stud goes into a
hollow tube, a bar through a clip, a plate beside a bracket's upstand, a dish
nested in a dish - all of those read as zero. If a pair is not in this list, it
is not sharing plastic, however close together it looks in the render.

### 2b. `unchecked_deep_overlaps` - not a pass

Pairs that overlap by more than a stud's engagement and whose shapes could not
be measured - a part the library cannot resolve, or one too large to rasterise.
**This is not a report that they are fine.** It is the checker saying it did not
look. There is usually nothing to do about it; look at the render before
trusting that region, and if a pair does look wrong, move it apart on the grid.

### 3. `contacts` - informational, nothing to do

Parts touching as they should. A stud sitting inside the part above it is what a
connection *is*.

### 4. `unverified_parts` - usually fine, worth a glance

No stud connection and nothing nearby. Either the part is genuinely floating, or
it is held by a joint the checker does not model: clips, bars, Technic pins,
hinges, brackets, minifig hands. Expected if you used one deliberately.

### 5. `objects_in_pieces` - **one object must be one object**

You are given one thing to build. Every part of it has to be joined to the rest
of it, because a model whose parts are not touching each other is not a model -
it is a handful of loose bricks that falls apart the moment it is picked up. A
tree whose trunk stands here and whose canopy hangs in the air over there passes
every other check on this page: both halves sit perfectly on the grid, nothing
overlaps, nothing floats if both reach the ground. It is still not a tree.

**This fails the model when you were given one object to build**, which is the
usual case and always the case for a subconstruction.

Each entry names one object, says how many separate clumps it came out in, and
lists the parts adrift from the biggest clump with the `gap_ldu` between them.
Fix it by moving each detached part onto the build until it touches - a brick is
24 LDU tall, a plate 8, a stud 20 across - or by bridging the gap with a part
that reaches both.

**What this is not asking for.** Separate objects must *not* be joined to each
other. A scene of a tree and a car is right when the tree and the car stand
apart, and gluing them together to satisfy this check would be the fault, not
the fix. Only the inside of each object has to hold together. When a scene is
assembled out of finished objects the check is reported and does not fail, for
exactly that reason.

This is the measured half of what the looking half reports as `one_build` and
`separate_pieces` (Channel 2 below). When both fire they are the same fault seen
two ways - the critic saw the clumps, this says which lines they are on and how
far apart. Fix it once.

### 5b. `subassemblies` - the count you may not finish above

`subassemblies` beside it is a different, **stricter** count: the clumps the
model falls into over stud connections *only*, where `objects_in_pieces` also
counts parts that merely touch. So the two answer different questions, and a
build can be clean there and still be in trouble here - bricks that rest against
each other pass the first and are held together by nothing.

**You may not finish with more than three.** `finish` is refused above that
however cleanly everything else validates, and giving up does not get you out of
it either. Three is not a target - most builds that are actually built come out
as one - it is the ceiling, and it exists because a model in nine pieces is not
a model, it is nine things standing near each other that fall apart when the
build is picked up.

When you are over it, `loose_pieces` names every clump that is not the main
body: how many parts it has, a part and line inside it, and the `gap_ldu`
between it and the build. For each one, move it until its parts seat on real
studs of the main body - x and z on multiples of 20 LDU, y on the level the part
beneath puts it at - or bridge the gap with a plate long enough to reach both.

One row is not your fault and is marked so: `held_by_other_means` is a clump
held by a clip, a pin or a minifigure's grip, which no stud checker can see. It
still counts, so if it is what puts you over, the answer is usually to seat the
part it hangs off on a stud rather than to take the accessory away.

### 6. `style` - not a fault, and the only one about the model being *good*

Present only when the build is a long way outside what real sets its size look
like. It measures three things against the 1,800 sets in the corpus:

| | what it catches |
|---|---|
| `top_share` | one shape doing all the work - 62 of 76 parts being the same round plate |
| `rotated_share` | a build with every part at the same angle |
| `colours` | one or two colours where a real set of that size has nine |

**This never fails a model and it is not in the verdict.** It is the answer to a
question nothing else here asks: the grid check says the model can be built, the
renders say it looks like the right thing, and neither of them notices that it
is a correct, recognisable, thoroughly dull box.

The numbers are real and they are not close. A tree you build out of 62
identical round plates sits against real sets where the commonest part is 10% of
the model - that gap is not a matter of taste, it is the difference between
approximating a shape by repeating a brick and using the part that *is* the
shape. When you see it:

- **`top_share` high** - name what you were approximating (a curve, foliage, a
  slope, a texture) and `search_parts` for that shape. The catalogue has 5,399
  parts and most of them exist because a box was wrong.
- **`rotated_share` low** - you have built everything facing one way. Slopes
  turned to face four directions, a bracket putting a surface on its side, a
  wedge at an angle: rotation is most of what stops a build reading as stacked
  boxes.
- **`colours` low** - an accent on edges, frames and the details that catch the
  eye.

Act on it while the build still has steps left. Ignore it where repetition is
the honest answer - a brick wall is made of bricks, and a model that is right
does not need changing to score better.

## What "clean" means

- `missing_parts` absent - every number in the file is a real part
- `misaligned == 0` - every part is on the grid
- `overlapping == 0` - nothing shares solid plastic

Everything else is description, not failure - including `style`.

## The repair pass

A repair is one pass over **every** error, not one pass per error - and it is an
edit, not a rewrite. Every fault above names the line it is on, and those are
`edit_model`'s line numbers:

1. Read every fault, all of them.
2. Work out each correct placement: `y = below.y - this_part_height`, x/z on one
   of the stud coordinates underneath.
3. **One `edit_model` call**, with one edit per fault:
   - a misaligned or overlapping part → `replace` its line with the corrected
     coordinates
   - a duplicate part → `delete` its line
   - a part that has to exist → `insert` it where it belongs
   - an invented part number → `replace` that line with the real `part_id`
     `search_parts` gave you
4. Validate again.

Put `expect` on every `replace` and `delete` - the line as the report quoted it.
If it comes back saying the line is not what you expected, your numbers are from
an older version of the file: nothing was changed, so `read` and edit
again. Do not rewrite the whole model to get around it.

Two passes should clear a model. If the same error survives a third, stop and
report which part and what the gap was. If a repair makes things *worse*, the
fix was wrong - go back to the last state that validated better rather than
layering another guess on top.

---

# Channel 2 - the looking half of `validate_model`

**This is the only way you can see your own work.** You are writing coordinates;
you have no idea what they add up to. `validate_model` draws the model from four
viewpoints and has a vision model report what is actually there.

It comes back from the same call as the grid check, so every validation shows
you the model - unless the grid check failed, in which case fix that first and
the looking arrives with the next one.

## What comes back

```json
{
  "seen": {
    "reads_as": "a red box on four wheels",
    "recognisable": true,
    "issues": [
      {"what": "the roof floats above the walls",
       "where": "FRONT view, top of the cabin",
       "fix": "lower it 8 LDU so it seats on the wall studs"}
    ],
    "good": ["four wheels correctly placed"],
    "character": {
      "generic": true,
      "why": "it is a correct red box; nothing says pickup rather than van",
      "one_flourish": "two 1x1 round tiles in trans-clear at the front face for headlights"
    },
    "verdict": "..."
  }
}
```

## How to use it

- **Treat every issue as real.** You cannot see the model; this is the only
  report you get. An issue you dismiss is one the user will notice.
- **`character` is the exception: it is advice, not a fault.** It is the only
  thing in this whole report that is about the model being *good* rather than
  being *correct*, and it appears precisely when everything else has come back
  clean. If `generic` is true and you have steps left, do the `one_flourish` -
  it is one change, chosen to be the cheapest thing with the largest effect. If
  you are near the end of your budget, or the model is finished and plain, leave
  it and say so. Never treat it as something blocking `finish`.
- **Fix them by rewriting the model, then render again.** Same discipline as a
  validation repair: all of them in one pass.
- **If `issues` is empty and the verdict says it is finished, stop changing it.**
  Continuing to "improve" a model that has been reported correct is how a good
  build gets worse.
- **`reads_as` is the important line.** If you built a car and it reads as "a red
  box", the proportions are wrong and no amount of grid-correctness will fix
  that. Go back to the shape.

Give it a real `subject` - "a red pickup truck" gets a far more useful answer
than "a model".

## When the user attached a reference picture

Sometimes the request comes with an image of what they want. When it does, that
picture is **the specification** - it outranks your own judgement about how the
thing should look, and it outranks the wording of the request wherever the two
disagree.

You cannot see it. Two tools stand between you and it:

**`ask_about_image`, with no questions** - call it **first**, before planning
anything. A vision model reads the picture and returns it as **the objects in
it**, then at **three zoom levels**, coarse to fine.

0. **`objects`** - the free-standing things in the picture, each with what it
   is, whether it is the subject or scenery, how big it is next to the others,
   and `with_others`: what it is *doing* with them - holding, leaning on,
   facing, standing to the left of. `arrangement` says how they stand together.
   On a build of several objects this is already settled before you start, and
   what it means for you is what your object must **have** so it can meet the
   others: a notch where the axe strikes, an arm raised to hold something, a
   flat side where the next thing butts against it.

Then the three levels. Build in that order; they are the order the model gets
made in:

1. **`whole`** - the whole object seen from across the room: its silhouette,
   where the bulk sits, how it stands, which way it faces, and the ratios of the
   entire thing. Get this wrong and no amount of correct detail rescues it, so
   settle the overall footprint and height from here before anything else.
2. **`parts`** - the three to eight large pieces it is made of, each with its
   **width, height and depth given relative to the others**, where it starts and
   stops up the object (`sits_at`), its shape, its colour, and **how it
   attaches**: resting on, hanging under, set into, butted against, overlapping,
   flush with. This is your subtask list and your Y levels. The attachment is
   the difference between a chimney *on* the roof and a chimney *beside the
   house*.
3. **`details`** - everything sitting on those parts: windows, doors, wheels,
   handles, markings. Every one names the part it belongs to, how many there
   are, how big it is *relative to that part*, and whether it sticks out, sits
   flush, or is recessed. Build these last, onto parts that are already there.

**Sizes come in three dimensions, and depth is one of them.** You are building a
solid object, not a picture of one. Every part carries `width`, `height` and
`depth`, all relative - a part built to its width and height alone comes out one
brick deep, flat as a stage set, and wrong from every angle but the front.

Depth is the one the picture could not show, so it is **estimated** and says so
(`whole.depth_confidence`, and each part's own `depth`). Treat an estimate as
the specification anyway: an inferred depth is a real instruction, and ignoring
it is how a house becomes a wall. Where a part says depth was not visible at
all, that one is yours to choose - choose something and keep it consistent with
the rest.

`composition`, `relations` and `colours` run across all three levels:
composition and relations are what is above, below, beside, in front of and
behind what, and what is aligned, centred, offset, flush or gapped. `colours`
covers every visible part - build it grey and it is wrong, however good the
shape. `orientation` and `build_priorities` come with it and matter too.

Build what that description says. The answer is remembered, so a second call
costs a step and tells you nothing new.

**`ask_about_image`, with `questions`** - for what the description did not
settle. It is looking
at the picture and you are not, so it can answer anything you would otherwise
have to invent: how many of something there are, which colour one particular
part is, what is behind what, how far along the roof the chimney sits.

**You get ten calls for the whole build**, up to six questions in each. That is
plenty for a build that is working and a hard stop for one that has started
interviewing a photograph instead of placing bricks. So each call is a prepared
list, not a conversation - and coming back later, when the model exists and you
find you are missing something specific, is exactly what the rest are for:

1. Read the description through.
2. Mark every place where you are about to guess at something the picture would
   have told you.
3. Ask about those, all of them, in one call.

What a good question looks like:

- Good: *"How many windows are on the front face, and are they evenly spaced?"*,
  *"Is the roof the same colour as the walls or darker?"*, *"Does the chimney
  sit on the ridge or on one slope, and how far back?"*
- Wasted: *"Tell me more about the house"* - that is what the description did.
  A question whose answer is already in the description spends the whole
  allowance on nothing.

Two answers you must respect: one that says something **was not visible** (then
it is yours to decide, and decide you should - do not ask again from another
angle), and one that **contradicts the description** (the answer wins; it was
looking at the picture with your question in mind).

Asking is not required. If the description settles it, build - the allowance is
not something to spend because you have it.

**The reference comparison** - when a reference exists `validate_model` makes a
second vision call on top of the usual critique: your renders and the reference picture, side by side. It
does not just grade the model, it tells you **how to close the gap**:

```json
{"matches": false,
 "closeness": "the right shape, but the wrong colours and no wheels",
 "reads_as": "a grey box",
 "keep": ["the two-storey proportion", "the flat roofline"],
 "changes": [
   {"do": "recolour the four cabin bricks red",
    "where": "the upper body, visible in HOME and FRONT",
    "how": "colour code 4 on the 2x4 bricks at y=-24 and y=-48",
    "brings": "the red cab the reference has",
    "aspect": "colour", "severity": "major"},
   {"do": "add two wheels under the rear",
    "where": "the back of the chassis, FRONT and RIGHT views",
    "how": "a wheel either side, seated on the studs at the rear corners",
    "brings": "it reads as a vehicle rather than a block",
    "aspect": "detail", "severity": "fatal"}
 ]}
```

**`changes_to_make` is the part to act on.** It is sorted by how much each
change buys, it is specific enough to build from without seeing anything, and
working down it in order is the fastest route to a model that matches.

How to use it:

- **Apply them in one `edit_model` call**, top of the list first, then render
  again. Not one change per round - that spends the run on round-trips. And an
  edit rather than a rewrite: the model already reads as the reference in the
  ways `keep` lists, and rewriting the file is how that gets undone.
- **Keep everything under `keep` exactly as it is.** That list exists because
  the easiest way to lose a good roof is to rebuild the walls under it.
- **Take `fatal` and `major` first.** Composition and colour decide whether
  anyone recognises the model. A blue car built red is a real failure; a
  stepped curve is not.
- **Ignore `minor`.** Stepped curves, lost fine detail, a shade off - that is
  what LEGO does to a photograph. `matches: true` with minor differences listed
  is a pass. Stop there.
- The *what* is not yours to argue with - you cannot see the picture and it
  can. The *how* is: build each change with the parts you judge best.

**`matches: false` blocks the run.** `finish` refuses while it stands, and it is
right to: a model that does not read as the picture is not the thing that was
asked for, however cleanly it validates. But it is a list of work, not a
rejection - do the work and it passes.

## Pictures happen anyway

Every `edit_model` renders automatically. That costs you nothing and it is what
the user is watching, so **write early even when the model is rough or wrong** -
they would far rather see a broken build than wait for a perfect one. The
automatic render does not include the critique; `validate_model` is what gets
the model described back to you.
