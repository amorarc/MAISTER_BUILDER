# Building from real sets

You have 1,800 real LEGO models in LDraw - released sets, real coordinates,
built by the people who design LEGO for a living. **Look at how they did it
before you work out how to do it yourself.** Deriving a shape from first
principles is the hardest way to build anything, and it is the way you will
default to unless you go and look.

## They are already on the page

Look for a **`real_sets_that_built_this`** block in your task. When it is there,
the search has been run for you and the sets are already open: which assemblies
each one comes apart into, how many parts each assembly has, the **real LDraw of
the one worth copying** - with `0 STEP` markers, so you can see the order it goes
together in - the parts it uses with their descriptions, and the exact
`copy_from_set` call that grafts it.

**That block is where the build starts.** Not after you have decided an
approach - before. Graft the closest assembly, recolour it, and spend your build
on the difference between it and what was asked for. Reading it and then
building from nothing anyway is the one way to waste it, and it is what happens
if you treat it as background.

It does not stop you searching for more: a car handed a car may still want a
search for wheels, or a windscreen, or a driver. It means you never start blind.

When the block is absent, nothing in the corpus matched - go and look yourself,
the way the rest of this page describes.

## Build out of them, do not just look at them

**A model that could have borrowed and did not is a worse model.** You have
1,800 designs that were drawn by professionals, moulded, and sold in boxes. When
one of them contains the thing you need - a wheel assembly, a cockpit, a wing, a
head - taking it is not a shortcut around building. It *is* building, and the
alternative is spending your steps producing a poorer version of a solved
problem.

So for anything with real shape to it, the expected shape of a build is:

1. `search_reference(kind="sets")` for the subject - and then **again** for its
   parts. A racing car is a search for a racer, and another for wheels, and
   another for a cockpit or a windscreen.
2. `get_set_details` on each hit, to see what assemblies it is made of.
3. **`copy_from_set` several times**, from more than one set, taking the piece
   each one does best.
4. Then make it the thing that was asked for: recolour, move, cut away what does
   not belong, and build the rest yourself with `build_ops`.

Grafting from three sets and adapting is normal and good. Wheels from a racer,
a windscreen from another, a chassis you lay yourself - that is how a real
designer works, and every graft says in the file where it came from.

`copy_from_set` narrows to what you actually want, so you are not obliged to
take a whole assembly and delete half of it:

```
copy_from_set(..., set_number="8155-1", matching="wheel")     # just the wheels
copy_from_set(..., set_number="8155-1", only_parts=["4600"])  # just these
copy_from_set(..., set_number="8155-1", exclude_parts=["3001"])
```

## The default move

For anything with a shape to it - a vehicle, a creature, a building, a plant,
furniture, a machine - the first thing you do after understanding the request
is find out how it has been done:

```
search_reference(kind="sets", query="small medieval house with a sloped roof")
search_reference(kind="sets", query="four wheeled pickup truck", max_pieces=300)
```

Plain description, not catalogue wording: it is a semantic search, so
"something a minifigure can sit inside and drive" works. **Always set
`max_pieces`** near the size you intend to build - a 3,000-piece Star Destroyer
teaches a 40-piece spaceship nothing.

Skip this only for a shape you can state in one line (a 4x4 tower, a wall, a
staircase) or an edit to a model that already exists.

## Then open it

A set number teaches you nothing. Read the build:

- **`get_set_details(number)`** - the LDraw source, and the index of the set's
  submodels. That index is the thing to read carefully: a real set is a handful
  of named assemblies, and their names tell you how LEGO decomposed the problem.
- **`read_model("set:<number>", submodel="<name>")`** - the assembly you
  actually need, by name. Stuck on the roof? Read the roof submodel. This is
  the whole point: thirty lines of real coordinates for the exact feature you
  are about to invent.

Never name a set you have not opened. That is claiming research you did not do.

## Then take it

Reading a set teaches you how it was done. **`copy_from_set` brings the
assembly across**, as real part lines at your coordinates, which you then own:

```
copy_from_set(path="projects/x/model.ldr", set_number="41590-1",
              submodel="41590 - Iron Man.ldr", at=[0, 0, 0],
              recolour={"320": 2, "191": 14})
```

It re-anchors the assembly so its footprint is centred on `at` and its underside
sits at that height, turns it by `rotate`, repaints it, aligns it to the lattice
your model is already on, brings any printed elements' definitions with it, and
writes a comment in the file saying which set and submodel it came from.

You do not retype coordinates and you do not shift them by hand. That is the
whole point: forty lines of arithmetic is where builds break, and this is a set
that has been designed, moulded and sold - its geometry is worth more intact
than re-derived.

**Once it is in, it is yours.** Recolour it, delete what you do not want with
`edit_model`, build onto it with `build_ops`. Grafting a BrickHeadz figure and
redressing it into a different character is exactly the intended use: the
proportions and the head construction are a solved problem, and what makes it
*your* character is the colours, the face, the hair and the accessories you put
on it.

Take **assemblies**, not models. A wing, a wheel arch, a cab, a torso. Grafting
a whole set and handing it back is not building anything, and the comment in the
file will say so.

## What to take, when you are not grafting

**Take:** the part vocabulary, the proportions in studs, the layer structure,
how a feature is attached - a roof slope, a wheel assembly, a hinge joint.

**Do not take:** coordinates copied out by hand. If the geometry is worth having
verbatim, `copy_from_set` moves it correctly; if it is not, take the idea and
place your own parts on your own grid.

`search_reference(kind="sets", like="<number>")` widens a hit that is close but
not the subject.

If nothing comes back that is genuinely related - common for small builds like
a chair or a sign - say so and build it directly. A weak reference is worse
than none: it pulls the design toward the wrong subject.
