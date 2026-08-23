# Skill: Building From Reference Sets

You have 1,800 real LEGO models from the Official Model Repository - actual
released sets, in LDraw, with real coordinates. Designing a shape from first
principles is the hardest way to work. Finding out how LEGO themselves did it is
the easiest.

## When to reach for a reference

Searching sets is for **a new model whose shape you do not already know how to
make** - a vehicle, a creature, a building with real proportions. It is not for
editing a model that already exists, and not for a shape you can state in one
line (a 4x4 tower, a wall, a staircase). Building those directly is faster and
just as correct.

When it does apply, run `search_sets` on what the user asked for before planning
geometry.

```
search_sets(query="small medieval house with a sloped roof")
search_sets(query="four wheeled pickup truck", max_pieces=300)
search_sets(query="fighter spaceship", theme="Star Wars", max_pieces=500)
```

The query is a plain description, not catalogue wording - this is a semantic
search, so "something a minifigure can sit inside and drive" works.

**Set `max_pieces`.** A 3,000-piece Star Destroyer is not a useful reference for
a 40-piece spaceship: the techniques do not transfer down. Ask for a reference
of roughly the size you intend to build, or a little larger.

## Then actually read it

A set number on its own teaches you nothing. Two tools open it up:

- `get_set_details(set_number)` - theme, year, piece count, and the parts the
  set uses most. That parts list is the important half: it is a set of elements
  that are *known to work together* for this subject. A house reference telling
  you it used 40 of part `3004` and 12 of `3040` is telling you what a wall and
  a roof are made of.
- `read_set_model(set_number, start_line=..., end_line=...)` - the LDraw source.
  Real part choices, real coordinates, real submodel structure. Read the first
  60 lines to see how the build is decomposed into submodels, then read the
  submodel that matches the part you are stuck on.

`find_similar_sets(set_number)` widens a reference you already like, and is the
right move when the first hit is close but not quite the subject.

## What to take and what not to take

**Take:** the part vocabulary, the proportions in studs, the layer structure,
how a specific feature is attached (a roof slope, a wheel assembly, a hinge).

**Do not take:** coordinates copied verbatim into a different model. A reference
tells you a roof is two rows of `3040` slopes meeting at a ridge - you still
place them on *your* stud grid, at *your* origin, and you still run
`validate_model`. Copied coordinates that were correct in their own model are
usually misaligned in yours.

Do not reference a set you have not read. Naming a set number in your reply
without having called `read_set_model` or `get_set_details` on it is claiming
research you did not do.

## When there is no good reference

Small builds - a chair, a tree, a sign - often have no close match, and
`search_sets` will return things that are only loosely related. That is fine:
say so and build it directly with `search_parts`. A weak reference is worse than
none, because it pulls the design toward the wrong subject.
