# People are minifigures

**Any person in a model is a minifigure.** Not bricks arranged into a person
shape — the actual figure, out of the actual parts. This is not a stylistic
choice, it is what LEGO is: there is no set in the 1,800-model reference
library where a human being is built any other way.

So this applies to a driver, a rider, a pedestrian, a knight, an astronaut, a
firefighter, a shopkeeper, a child, a crowd, "a man walking his dog", "someone
sitting on the bench" — anything with a person in it. If the request names a
person, or plainly implies one, you build a minifigure.

It does **not** apply to a statue, a scarecrow, a snowman, a giant, a robot, or
an animal. Those are built out of bricks like anything else.

## Do not design one. Copy this.

A minifigure has exactly one correct arrangement and it is not derivable from
the stud grid — the head hangs on a neck pin, the arms clip into shoulder
sockets, the legs snap onto the hip block. Working it out from first principles
is wasted effort and it will be wrong. **Copy the block below and change the
colours.**

This is a standing minifigure with its feet on the ground at y = 0, taken from
the pose the reference sets actually use:

```
1 14      0  -96    0  1 0 0 0 1 0 0 0 1  3626bp01.dat
1  0      0  -96    0  1 0 0 0 1 0 0 0 1  3901.dat
1 15      0  -72    0  1 0 0 0 1 0 0 0 1  973.dat
1 15  -15.6  -63    0  0.985 -0.17 0 0.17 0.985 0 0 0 1  3818.dat
1 15   15.6  -63    0  0.985 0.17 0 -0.17 0.985 0 0 0 1  3819.dat
1 14  -23.9 -45.4 -10.3  0.985 -0.12 0.12 0.17 0.696 -0.696 0 0.707 0.707  3820.dat
1 14   23.9 -45.4 -10.3  0.985 0.12 -0.12 -0.17 0.696 -0.696 0 0.707 0.707  3820.dat
1  1      0  -40    0  1 0 0 0 1 0 0 0 1  3815.dat
1  1      0  -28    0  1 0 0 0 1 0 0 0 1  3816.dat
1  1      0  -28    0  1 0 0 0 1 0 0 0 1  3817.dat
```

Yellow head with a face, black hair, white torso and arms, yellow hands, blue
hips and legs. It validates clean, it reads as one connected piece, and its
feet stand on a surface whose top is y = 0.

**To use it:** paste those ten lines, change the colour numbers, and add the
same `(dx, dy, dz)` to every one of the ten to put the figure where it goes. To
turn a figure to face another way, give all ten lines the same rotation matrix.
Move it as a unit, always — a minifigure is one object.

## The parts, and what the sets actually use

| Role | Part | Usual colours |
|---|---|---|
| head | `3626bp01` (standard grin) — or `3626b` plain | `14` yellow |
| headgear | `3901` hair · `3624` police hat · `3833` helmet · `4530` long hair | `0`, `4`, `6` |
| torso | `973` | `15` white, `0` black, `1` blue, `4` red |
| arm right / left | `3818` / `3819` | same colour as the torso |
| hand | `3820` (two of them, one per arm) | `14` yellow |
| hips | `3815` | `0` black, `1` blue, `4` red |
| leg right / left | `3816` / `3817` | same colour as the hips |

**Do not search the catalogue for these.** Searching "minifig leg" returns
*Robot Leg* and *Food Turkey Leg*, because every plain leg is filed as a
superseded part. The numbers above are the ones 1,800 real sets use; take them
from this table.

Headgear is optional but almost always worth it — a bare head reads as unfinished.
Pick the one that says who the figure is: a police hat for a police officer, a
construction helmet for a builder, hair for everyone else.

## Offsets, if you need to change the pose

Every part is placed relative to the **torso** (LDraw's `+y` points down, so a
negative offset is *above* the torso):

| Part | Offset from torso |
|---|---|
| head, headgear | **−24** |
| arms | **+9**, and ±15.6 sideways |
| hands | on the ends of the arms — posed, not fixed |
| hips | **+32** |
| legs | **+44** |

`validate_model` checks these. A figure whose head is not on its neck fails
exactly as a brick off the grid does, and the report names the part and the
coordinate that fixes it. It also checks, when it looks at the renders, that
every figure is **complete** — two legs, a torso, two arms with a hand on each,
a head. A minifigure missing an arm is a fault, not a simplification.

## Giving it something to hold

A sword, a spanner, a camera, a mug, a fishing rod — a minifigure's hand is a
**C-shaped clip** and every one of these is a bar pushed through it. So a tool
is not placed *near* the hand, and it does not sit on a stud: it goes on the
hand's **grip axis**.

In the hand part's own frame that axis runs along local y, at **x = 0,
z = −10.5**. To place a tool held by a hand at position `H` with rotation `R`:

```
tool position = H + R · (0, grip_y, -10.5)
tool rotation = R · grip_matrix
```

`grip_y` is how far along the bar the fist closes, and it is the one number
that changes between tools — a sword is gripped at the hilt, a torch halfway
down its shaft. **You do not have to work it out.** `search_parts` marks every
part a minifigure holds, and `get_part_details` gives you that part's own
`grip_y` and `grip_matrix`, both measured from the sets that hold it.

Worked example — the figure above, holding a shortsword (`3847`) in its right
hand. Add this eleventh line to the ten:

```
1 7 -23.9 -45.4 -25.147 0.1542 -0.0862 -0.9848 -0.8471 0.4997 -0.1771 0.5069 0.8625 0.0042 3847.dat
```

`validate_model` recognises a tool on a grip axis as held, and reports it under
`held_accessories` with the hand that holds it. A tool that is *not* on one is
not an error — a sword lying on a table is a perfectly good model — but it will
show as a separate loose piece, which is the tell that a figure you meant to arm
is standing next to its sword rather than holding it.

**Do not give the tool the hand's own rotation.** Only 1% of held tools in the
reference sets do that; it is the one answer that is reliably wrong, and it
comes out as a sword pointing into the figure's own leg.

## Posing it: turning a head, bending a leg, raising an arm

A minifigure has four joints and each one turns about **one axis only**. Posing
is changing a part's **rotation matrix and nothing else** — every part stays at
the position the table above gives it. A figure that "sits" by having its legs
moved down is a figure that has come apart.

| Joint | Turns about | Natural range | What it does |
|---|---|---|---|
| head | **y** | ±10° usual, up to ±45° | looks to the side |
| legs | **x** | 0° standing, **−90° sitting** | strides, sits |
| arms | **x** (on top of their ±10° about z) | −15° to −60° | raises forward |

Three quarters of all figures in the reference sets are left straight — a
neutral figure is the normal one. Pose when the model says to: someone sitting
in a car, waving, holding something up.

**Turning the head.** Same coordinates, a rotation about y. 30° to the left:

```
1 14 0 -96 0  0.866 0 0.5 0 1 0 -0.5 0 0.866  3626bp01.dat
1  0 0 -96 0  0.866 0 0.5 0 1 0 -0.5 0 0.866  3901.dat
```

The headgear takes **the same matrix as the head** — a hat that stays facing
front while the face turns is the commonest posing mistake there is.

**Sitting.** Both legs keep their position and take a −90° rotation about x.
Nothing else about the figure changes:

```
1 1 0 -28 0  1 0 0 0 0 1 0 -1 0  3816.dat
1 1 0 -28 0  1 0 0 0 0 1 0 -1 0  3817.dat
```

A seated figure's feet come forward, so its hips still sit at torso +32 — put
the figure on the seat by placing the *whole* figure, not by dropping its legs.

**Raising an arm.** This is the one that is not just a matrix: the hand is on
the end of the arm, so when the arm turns, **the hand and anything it holds
must move with it**. Leaving the hand behind is what produces a figure with a
detached fist floating by its hip. Taken from a set, the right arm raised:

```
1 15 -15.6   -63      0       1 -0.146 -0.095 0.174 0.826 0.537 0 -0.544 0.839  3818.dat
1 14 -22.337 -53.614 -18.597  1 -0.17 0 0.174 1 -0.204 0 0.208 1                3820.dat
```

If that figure is holding something, recompute the tool from the hand's new
position and rotation with the grip formula above — the tool follows the hand,
always.

**Rotating the whole figure** is the other thing entirely: to turn a figure to
face a different way, give *every* part the same rotation, including the parts
it holds. To move one, add the same offset to every part. `validate_model`
checks the figure in its own frame, so a figure lying on its back or facing
backwards passes exactly as an upright one does.

## What a figure wears, as opposed to holds

Two more categories, and the catalogue tells them apart for you:

| Category | Where it goes |
|---|---|
| `Minifig Accessory` | held in a hand, on the grip axis above |
| `Minifig Headwear` | worn on the head — **the head's own coordinates** |
| `Minifig Neckwear` | airtanks, a backpack — **the torso's own coordinates** |

So a helmet goes at exactly the position the head is at, and a backpack at
exactly the position the torso is at. No offset, no arithmetic.

## A minifigure sets the scale

A figure is 4.3 bricks tall and about 1.5 studs wide. That fixes the size of
everything it interacts with, and those sizes are not negotiable if the scene
is to read:

- a **door** it walks through: 4 studs wide, 5 bricks tall
- a **chair** it sits on: seat 2 studs deep at 2 bricks off the floor
- a **car** it drives: at least 4 studs wide, so the figure fits between the sides
- a **step** it climbs: one brick
- a **room** it stands in: no less than 6 x 6 studs

Build the figure first when a scene has one in it. Then everything else has a
measure to be right against.
