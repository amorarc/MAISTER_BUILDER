# Skill: Your Own Library and Notes

You have two databases that are yours, separate from the official LEGO data:

- **Creations** - models you built and chose to keep (`save_creation`,
  `search_creations`, `read_creation`)
- **Notes** - things you worked out (`add_note`, `search_notes`)

Both persist across sessions. Nothing else you do in a run survives it.

## Your creations are not LEGO sets

This distinction matters and the tools keep it deliberately separate.

| | `search_sets` | `search_creations` |
|---|---|---|
| what it holds | 1,800 real released sets | models you built |
| what it proves | how LEGO solves a problem | what you already tried |
| trust it | as a design reference | only if `validated` is true |

An official set was designed by people, built physically, and shipped. One of
your creations is only as good as the run that produced it. **Never present one
of your own creations to a user as though it were an official set**, and when
reusing your own work, pass `validated_only=true` - an unvalidated creation is a
record of an attempt, not a solution.

When both could help, read the real set first.

## Saving - only when asked

**`save_creation` is not yours to call unprompted.** The library is shown to the
user as a gallery of their models, so putting something there is a change to
their workspace, not a note to yourself. Call it only when the user explicitly
asks you to keep the model - "save this", "keep it", "put it in the gallery".

Validating cleanly is not permission. Finishing a build is not permission. If
you believe a model is worth keeping, end your reply by saying so and offering;
the user says yes or nothing happens.

When you are asked, save the current model - not every draft along the way. A
library of near-identical half-finished attempts is worse than an empty one,
because searching it costs you a turn and returns noise.

```
save_creation(
  path="tree/oak.ldr",
  name="oak tree",
  description="Round-brick trunk with a 3-layer canopy of 2x2 round plates,
               offset with jumpers so the foliage is not a solid block.",
  tags=["nature"])
```

The `description` is what `search_creations` matches on. Describe the
*techniques* and what it is a good starting point for, not just the subject -
"a tree" is nearly useless to your future self; the description above is not.

Saving under an existing name **updates** that creation. Improve a model and
re-save it under the same name rather than accumulating `oak tree 2`.

## Notes

A note is one specific claim attached to a part, a set, one of your creations,
or nothing in particular.

```
add_note("part", "3062b", "Stacks into a good tree trunk, 4-6 tall. More looks lumpy.")
add_note("set", "31009-1", "Clean small-house reference; roof is two rows of 3040.")
add_note("general", None, "Jumpers are the only honest way to get a half-stud offset.")
```

Write down what the catalogue cannot tell you: what a part is *good for*, which
set is a clean reference for a subject, a technique that worked, and especially
a technique that **failed** and why. A note saying "stacking 3070b tiles here
does not work, nothing attaches above a tile" saves you the same dead end later.

Rules that keep the notes worth reading:

- **One claim per note**, specific enough to act on. "This part is nice" is not
  a note.
- **Only record what you verified in this run.** A note is retrieved later and
  believed. Guessing here poisons your own future work.
- Notes on a part or set **surface automatically** when you next call
  `get_part_details` or `get_set_details` on that subject - you do not need to
  search for them. That is the main way they pay off.
- The subject must exist. `add_note("part", "9999zzz", ...)` is rejected rather
  than filed against a part number you invented.

## Starting a build

When a new subject is one you might have built before, spend **one** turn - a
single call, not two - checking:

```
search_creations(query="a tree", validated_only=true)
```

These tools return nothing rather than the least-bad match, so an empty result
means you genuinely have no prior work here, not that you phrased it wrong. Take
it as a clean answer and move on; do not rephrase and search again.

Skip this entirely for an edit to an existing model, or for a shape you can
state in one line. Your notes surface on their own when you call
`get_part_details` or `get_set_details`, so you rarely need `search_notes` up
front.
