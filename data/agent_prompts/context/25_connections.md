# How pieces actually join

Two pieces fit together only if one offers a connection the other accepts. This
is not the same question as whether they are the right size, and it is the one
that decides whether a build is real. A bar laid against a cross hole is the
right length, the right colour, touching on every side - and falls apart in your
hands, because a bar goes in a clip and a cross hole takes an axle.

`get_part_details` returns `connections` for every part: which families it
belongs to, and whether it offers the male half, the female half, or both. It
also returns `attachment` - what has to already be there for the part to go on
at all - and `studs_required`, the number of studs it covers and therefore needs
underneath it. **Read those before you place an unfamiliar part.** A part with no
connection family in common with what you are attaching it to cannot be attached
to it, whatever the coordinates say, and no checker will catch that for you:
validation checks the stud grid and solid plastic, not whether a join exists.

## Two questions, not one

Connections sort two ways, and a build needs both answers.

**Which system** - because systems do not mix. A stud does not enter a pin hole
and a bar does not enter a cross hole. Two parts in the same system *might* go
together; two in different systems cannot, and need a part that bridges them.

| system | what is in it |
|---|---|
| `system` - the stud grid | stud and tube, SNOT |
| `articulated` - joints that move | clip and bar, hinge, turntable, ball and socket |
| `technic` - mechanism | Technic pin, axle and cross-hole, gear |
| `standalone` - its own system | rail and track, wheel rim and tyre |

**What the joint does** - because this is what a build actually starts from.
`get_part_details` reports it as `does`, and the part's `moves` lists every
motion it offers:

| motion | meaning |
|---|---|
| `rigid` | no movement once assembled |
| `swings` | turns about a bar it grips, and unclips again |
| `folds` | one axis; free, or held at fixed angles by a click hinge |
| `spins` | unlimited rotation about one axis |
| `swivels` | a cone of movement rather than an axis |
| `pivots` | free with a smooth pin, fixed with a friction one |
| `drives` | rotation *transmitted* - what actually turns a wheel |

The two are independent, and that is the point. A hinge and a ball joint are
both articulated and behave nothing alike. A pin hole and a cross hole are both
Technic and are opposites: one lets its pin spin, the other locks the axle so it
carries torque. When something in the build has to move, decide the motion
first, then find a part that offers it.

## The families

**Stud and tube** - the baseline. Studs press into the tubes underneath the part
above; the grip is friction, from a stud very slightly too big for its tube.
Three quarters of the connections in a real set are this and nothing else. A
part in this family with `studs_required: 8` needs eight studs under it, in a
4x2 rectangle, and every one of them free.

**SNOT - studs not on top.** Studs facing sideways or downward, so a section can
be built at right angles to the grid. Brackets, headlight bricks (a 1x1 with a
stud on one face), and jumper plates, which put a single stud at the centre of a
1x2 and so offset everything above them by half a stud. This is how you get
detail finer than the grid, and how you turn a surface ninety degrees.

**Clip and bar** - a C-shaped clip snaps around a round bar. Bars are 4 LDU
across, often moulded onto the end of a piece: a flag pole, a lightsaber blade,
an animal's limb. Clips come horizontal, vertical, and as a minifig hand. Use it
for anything that has to swing, hang, or be held.

**Technic pin** - a ribbed pin bites into a round pin hole. A *friction* pin
resists rotation and holds an angle under load; a *smooth* pin turns freely and
is what you want for a pivot or a wheel. Axle pins have a pin at one end and a
cross axle at the other, which is how you cross between the two Technic systems.

**Axle and cross-hole** - a `+`-section axle in a matching `+` socket. This is
rotationally *locked*: the axle and the hole turn as one unit, which is the
whole point, because it is what transmits torque to gears and wheels. Do not
confuse it with a round pin hole, which lets its pin spin. Axle lengths are
counted in studs, and bushes stop an axle sliding along its own length.

**Ball and socket** - a ball snaps into a slightly smaller socket and then
swivels through a cone. Multi-axis poseable joints, creature limbs, and Technic
suspension and steering.

**Hinge** - two interlocking halves turning on one axis. A plain hinge swings
free; a *click* hinge has internal teeth and holds at fixed angular steps, which
is what you need when the joint has to stay where you put it under load - a
door, a crane arm, an opening panel.

**Turntable** - a ring that rotates a full circle against its base, held down
but free to spin. Some have gear teeth around the rim so a separate gear can
drive them: rotating turrets, cranes.

**Gear** - teeth meshing to transfer rotation. Spur gears run parallel, bevel
gears turn the drive through ninety degrees, worm gears give a large reduction
and will not be back-driven, and a gear rack turns rotation into straight-line
travel.

**Rail and track** - train and monorail track, which clicks end to end on its
own tab-and-groove geometry and has nothing to do with studs.

**Wheel rim and tyre** - a soft tyre stretched over a hard rim, held by nothing
but its own elastic tension. No studs, no clips. A tyre goes on its rim and
onto nothing else, and the rim is what attaches to the model.

## Parts that come in pairs

Many parts are half of something. `get_part_details` reports `used_with`: the
parts real sets put beside this one, and in what share of them. Read it as a
completeness check.

* A wheel rim shows its tyre at 66%. A rim on its own is a hubcap.
* A turntable top shows its own base at 93% - they are separate part numbers
  and neither turns without the other.
* A Technic gear shows an axle at 93%. A gear on nothing does not drive
  anything.
* A 2x4 brick shows nothing at all, which is the truthful answer: it is used
  with everything, so it is characteristic of nothing.

When a part comes back with a companion above about 60%, placing it alone is
almost certainly leaving an assembly unfinished. Search for the companion and
place both.

## What this means when you build

* Before placing an unfamiliar part, read its `connections` and `attachment`.
  "Sits on 4 studs" means find four free studs, not four studs with something
  already on them.
* A part that reports no family in common with its neighbour is not attached to
  it, however close together you put them. Either change one of the two parts,
  or add the piece that bridges them - that is what brackets, axle pins and
  clip-with-bar parts are *for*.
* Prefer stud and tube. It is the strongest connection, the easiest to get
  right, and the one the checker can verify. Reach for the others when the shape
  actually needs them: something that turns, hangs, angles off the grid, or sits
  between studs.
* A studded part needs its studs *free*. Two things cannot share one stud, and a
  round element standing between four studs occupies all four - nothing else can
  sit on them.

## Minifigures are assembled, not built

A minifigure does not go together on studs - the head hangs on a neck pin, the
arms clip into shoulder sockets, the legs snap onto the hip block. None of the
reasoning about studs on this page applies to one, and there is nothing to work
out: there is a single correct arrangement and a ready-made block to copy. See
*People are minifigures*.
