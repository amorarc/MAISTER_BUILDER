"""How a part attaches to other parts, and what it needs in order to.

The catalogue says what a piece *is* — "Technic Pin with Friction and Slots",
40 x 20 x 20 LDU. It says nothing about the only question that decides whether
two pieces can go together: what kind of connection each of them offers. A stud
does not go into a pin hole, a bar does not go into a cross hole, and a tyre
goes onto nothing at all except its own rim. A builder that knows a part's
dimensions and not its connection type will place it flush against something it
can never actually join.

So each part is read for its connections. Two sources, and they are not equally
good, which is why every finding says which one it came from:

* **Geometry** — the part's own file, recursed. LDraw builds parts out of named
  primitives, and several connections have a primitive that means exactly one
  thing: `confric*` is a Technic friction pin, `peghole`/`npeghol*` is the hole
  one goes into, `tooth*` is a gear tooth, `clip*` is a clip. Studs are better
  still: they carry a direction, so a stud pointing up is a stud you build on,
  one pointing down is the tube underneath, and one pointing sideways is SNOT.
  Where a primitive is decisive this is the answer, and it is not a guess.

* **The name** — where geometry is not decisive. The axle family is the reason:
  `axlehol8` is "Technic Axle Perimeter", the shaft, while `axlehole` is the
  hole, so the prefix cannot tell a cross-hole from the axle that goes in it.
  LDraw part names are rigorously standardised, so "Technic Axle  4" and "with
  Axle Hole" separate cleanly where the primitives do not. Turntables, hinges,
  track and tyres are named reliably and modelled out of plain boxes and
  cylinders, so they are read the same way.

Nothing here pretends to more certainty than it has: a finding from the name is
labelled as such, and a part whose connections cannot be read reports none
rather than a guess.
"""

import re

from .config import CHECKER_DIR

# Connections sort two ways at once, and a builder needs both.
#
# **Which system** it belongs to, because systems do not mix: a stud does not
# enter a pin hole and a bar does not enter a cross hole, so knowing that two
# parts are in the same system is the first thing that has to be true.
#
# **What the joint does**, because that is the question a build actually starts
# from — "I need this to turn", "I need this to hold an angle", "I need to
# drive that wheel". Two connections in different systems can do the same job,
# and two in the same system can behave completely differently: a friction pin
# and a smooth pin are the same shape and one holds while the other spins.
#
# Sorting only by system would put "anything that is not a stud" in one bucket,
# which is not a category — it is the absence of one.
GROUPS = [
    ("system", "The stud grid",
     "The System connection every brick has, and the parts that turn it "
     "through ninety degrees."),
    ("articulated", "Joints that move",
     "Everything meant to turn, fold, swivel or swing after it is built."),
    ("technic", "Technic mechanism",
     "Pins, axles and gears — the connections that carry motion and torque "
     "from one part of a model to another."),
    ("standalone", "Its own system",
     "Connections that answer to nothing else in the model: track that clicks "
     "to track, a tyre that stretches over its rim."),
]

# What a joint does once it is built. The second axis, and the one worth
# filtering on when you know the motion you need but not the part that gives it.
MOTIONS = [
    ("rigid", "Holds fast", "No movement once assembled."),
    ("swings", "Swings and unclips", "Turns about the bar it grips, and comes "
     "off it again."),
    ("folds", "Folds on one axis", "Free, or held at fixed angles by a click "
     "hinge."),
    ("spins", "Spins a full circle", "Unlimited rotation about one axis."),
    ("swivels", "Swivels any direction", "A cone of movement, not a single "
     "axis."),
    ("pivots", "Pivots or holds", "Free-turning with a smooth pin, fixed with "
     "a friction one — the same hole either way."),
    ("drives", "Carries torque", "Rotation transmitted rather than allowed: "
     "what actually turns a wheel."),
]

# id, label, group, motion, description
FAMILIES = [
    ("stud_tube", "Stud and tube", "system", "rigid",
     "The baseline system: studs press into the tubes underneath the part "
     "above. Around three quarters of every connection in a real set."),
    ("snot", "SNOT — studs not on top", "system", "rigid",
     "Studs facing sideways or down, so a part can be built on at right angles "
     "to the grid. Brackets, headlight bricks, and jumper plates that offset "
     "by half a stud."),
    ("clip_bar", "Clip and bar", "articulated", "swings",
     "A C-shaped clip snaps around a round bar. Weapons, flags, curtains, "
     "animal limbs, anything that swings."),
    ("hinge", "Hinge", "articulated", "folds",
     "Two interlocking halves turning on one axis. Click hinges hold at fixed "
     "angles under load; plain hinges swing free."),
    ("turntable", "Turntable", "articulated", "spins",
     "A ring that rotates a full circle against its base, held axially. Some "
     "have gear teeth so they can be driven."),
    ("ball_socket", "Ball and socket", "articulated", "swivels",
     "A ball snaps into a smaller socket and then swivels through a cone. "
     "Poseable joints and suspension."),
    ("technic_pin", "Technic pin", "technic", "pivots",
     "A ribbed pin bites into a round pin hole. Friction pins resist rotation; "
     "smooth pins turn freely as pivots."),
    ("axle", "Axle and cross-hole", "technic", "drives",
     "A cross-section axle in a matching cross-hole. Rotationally locked, so "
     "this is what transmits torque — unlike a round pin hole, which lets its "
     "pin spin."),
    ("gear", "Gear", "technic", "drives",
     "Teeth meshing to transfer rotation — spur, bevel, worm, or a rack that "
     "turns rotation into a straight line."),
    ("track", "Rail and track", "standalone", "rigid",
     "Train and monorail track, which clicks end to end on its own "
     "tab-and-groove geometry and ignores studs entirely."),
    ("tyre", "Wheel rim and tyre", "standalone", "rigid",
     "A soft tyre stretched over a hard rim, held by nothing but its own "
     "elastic tension."),
]

# The families whose count means something: a stud primitive is one stud, so
# these can be counted. Nothing else can — see `analyse`.
_COUNTABLE = ("stud_tube", "snot")

LABELS = {fid: name for fid, name, _g, _m, _b in FAMILIES}
BLURBS = {fid: blurb for fid, _n, _g, _m, blurb in FAMILIES}
GROUP_OF = {fid: group for fid, _n, group, _m, _b in FAMILIES}
MOTION_OF = {fid: motion for fid, _n, _g, motion, _b in FAMILIES}
FAMILY_IDS = [fid for fid, _n, _g, _m, _b in FAMILIES]
GROUP_IDS = [gid for gid, _n, _b in GROUPS]
MOTION_IDS = [mid for mid, _n, _b in MOTIONS]
MOTION_LABELS = {mid: name for mid, name, _b in MOTIONS}

# --------------------------------------------------------------------------
# Primitives that mean exactly one thing
# --------------------------------------------------------------------------

# The ribbed shaft of a Technic pin, and the hole it goes into.
_PIN_SHAFT = ("confric",)
_PIN_HOLE = ("peghole", "npeghol", "connhole", "beamhol")

# Anything in the axle family. Which end of it — shaft or cross-hole — the
# names do not say (see the module docstring), so that comes from the part name.
_AXLE = ("axle",)

_GEAR = ("tooth",)
_CLIP = ("clip",)
_CLICK_HINGE = ("clh",)          # "Click Lock Hinge Single Finger…"
_BALL = ("axlesphe",)            # "Technic Axle Truncated to Fit Ball Joint"

# --------------------------------------------------------------------------
# What the part is called
# --------------------------------------------------------------------------

_BY_NAME = (
    ("turntable", (r"turntable",)),
    ("gear", (r"\bgear\b", r"worm screw", r"\brack\b", r"differential")),
    ("hinge", (r"\bhinge\b",)),
    ("ball_socket", (r"ball joint", r"\btowball\b", r"\bball\b.*\bsocket\b",
                     r"with ball", r"ball with")),
    ("clip_bar", (r"\bclip\b", r"\bbar\b", r"\bhandle\b", r"\bflag\b")),
    ("tyre", (r"\btyre\b", r"\btire\b", r"\bwheel\b", r"\brim\b")),
    # Not a bare "rail" or "track": a door rail is a groove a door slides in
    # and a caterpillar track is a rubber loop, and neither clicks end to end
    # the way this family does.
    ("track", (r"train track", r"monorail", r"roller coaster")),
    ("snot", (r"\bbracket\b", r"headlight", r"studs? on (the )?side",
              r"\bjumper\b")),
    ("technic_pin", (r"technic pin", r"\bpin\b", r"pin hole", r"peg hole")),
    # "Axlehole" is one word in 63 catalogue entries and two in none of them,
    # so a pattern needing the space finds none of the Technic bricks whose
    # whole point is that they take an axle.
    ("axle", (r"\baxle ?holes?\b", r"cross ?holes?", r"\baxles?\b")),
)

# Whether a part seats on studs, in three tests, decisive first.
#
# Tubes underneath settle it outright — that is the anti-stud itself, and only
# a part meant to go onto studs has one. Studs on top settle it only together
# with a body that is a whole number of plates: a Technic brick has both and
# does seat, where a Technic pin connector has studs on nothing and a body 12
# LDU tall, and a rule that looked at height alone would have it sitting on
# studs it cannot reach. What is left is the parts with neither — a tile, a
# slope, a hinge base — and those are read from their category, which in LDraw
# is a fixed vocabulary and says plainly what family a part belongs to.
PLATE_LDU = 8.0
_HEIGHT_TOLERANCE = 1.0

_SYSTEM_CATEGORIES = {"brick", "plate", "tile", "slope", "hinge", "bracket",
                      "baseplate", "panel", "wedge", "roof", "arch"}


def _checker():
    """The connectivity checker, which resolves a part name to its file."""
    import sys

    if str(CHECKER_DIR) not in sys.path:
        sys.path.insert(0, str(CHECKER_DIR))
    import ldr_collision_checker as coll
    import ldr_connectivity_checker as conn

    return coll, conn


_scan_cache = {}


def scan(part_name, library_root=None):
    """Every stud and every primitive in a part, found once and remembered.

    One walk answers both questions asked of a part — which studs it has and
    which primitives it is made of — because the walk is the expensive half and
    doing it twice for the same file is the only way this gets slow.
    """
    key = (part_name or "").strip().lower()
    if not key:
        return {"top_studs": 0, "tubes": 0, "side_studs": 0, "prims": {},
                "at": [], "readable": False}
    if not key.endswith(".dat"):
        key += ".dat"
    if key in _scan_cache:
        return _scan_cache[key]

    if library_root is None:
        from .library import ensure_library_root

        root = ensure_library_root()
        library_root = str(root) if root else None

    result = {"top_studs": 0, "tubes": 0, "side_studs": 0, "prims": {},
              "at": [], "readable": False}
    if library_root:
        try:
            coll, conn = _checker()
            studs = conn.part_studs(key, library_root, {}, None)
            for point, axis in studs:
                if axis[1] < -0.9:
                    result["top_studs"] += 1
                elif axis[1] > 0.9:
                    result["tubes"] += 1
                else:
                    result["side_studs"] += 1
            # Where they are, not only how many. The walk already knows — it
            # has the point and the direction of every one — and counting them
            # and throwing the coordinates away left the builder to work out
            # for itself where a part's studs sit, which is the one thing about
            # a part that cannot be guessed from its name.
            result["at"] = [(tuple(round(v, 2) for v in point),
                             tuple(round(v) for v in axis))
                            for point, axis in studs]
            result["prims"] = _primitives(coll, key, library_root)
            result["readable"] = True
        except Exception:
            pass
    _scan_cache[key] = result
    return result


def _primitives(coll, part_name, library_root, depth=0, seen=None, out=None):
    """Every primitive the part is built from, by base name, with counts."""
    seen = set() if seen is None else seen
    out = {} if out is None else out
    key = coll.norm_name(part_name)
    if key in seen or depth > 4:
        return out
    seen.add(key)
    lines = coll.get_part_lines(part_name, library_root, None)
    if lines is None:
        return out
    for line in lines:
        tokens = line.split()
        if len(tokens) < 15 or tokens[0] != "1":
            continue
        child = " ".join(tokens[14:])
        base = coll.norm_name(child).rsplit("/", 1)[-1].removesuffix(".dat")
        out[base] = out.get(base, 0) + 1
        _primitives(coll, child, library_root, depth + 1, seen, out)
    return out


def _count(prims, prefixes):
    return sum(n for base, n in prims.items() if base.startswith(prefixes))


def _normalise(text):
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def analyse(part_name, description="", category="", keywords="",
            width_studs=None, depth_studs=None, body_height=None,
            library_root=None):
    """Which connection families a part belongs to, and what it needs.

    Returns ``connections`` (one entry per family, each saying what evidence
    put it there) and ``attachment`` (what has to be there for the part to go
    on at all).
    """
    found = {}

    def add(fid, role=None, count=0, evidence="geometry"):
        entry = found.setdefault(fid, {
            "id": fid, "name": LABELS[fid], "roles": set(), "count": 0,
            "evidence": set(),
        })
        if role:
            entry["roles"].add(role)
        entry["count"] += count
        entry["evidence"].add(evidence)

    scanned = scan(part_name, library_root)
    prims = scanned["prims"]

    # --- geometry, where a primitive means one thing ----------------------
    if scanned["top_studs"]:
        add("stud_tube", "male", scanned["top_studs"])
    if scanned["tubes"]:
        add("stud_tube", "female", scanned["tubes"])
    if scanned["side_studs"]:
        add("snot", "male", scanned["side_studs"])

    pins, pin_holes = _count(prims, _PIN_SHAFT), _count(prims, _PIN_HOLE)
    if pins:
        add("technic_pin", "male", pins)
    if pin_holes:
        add("technic_pin", "female", pin_holes)
    if _count(prims, _GEAR):
        add("gear", None, _count(prims, _GEAR))
    if _count(prims, _CLIP):
        add("clip_bar", "female", _count(prims, _CLIP))
    if _count(prims, _CLICK_HINGE):
        add("hinge", None, _count(prims, _CLICK_HINGE))
    if _count(prims, _BALL):
        add("ball_socket", None, _count(prims, _BALL))
    if _count(prims, _AXLE):
        # the family is certain; which end of it is not, so no role is claimed
        add("axle", None, 0)

    # --- the name, where geometry cannot decide ---------------------------
    text = _normalise(f"{description} {category} {keywords}")
    for fid, patterns in _BY_NAME:
        if any(re.search(p, text) for p in patterns):
            add(fid, None, 0, evidence="name")

    # An axle read from the name can say which end it is; the primitives could
    # not, so this is the only place the role comes from.
    if "axle" in found:
        if re.search(r"\baxle ?holes?\b|cross ?holes?|with axle", text):
            found["axle"]["roles"].add("female")
        elif re.search(r"\btechnic axle\s+\d", text):
            found["axle"]["roles"].add("male")

    attachment = _attachment(scanned, found, category, width_studs,
                             depth_studs, body_height)

    # A part that seats on studs is in the stud-and-tube family whatever its
    # geometry showed, and a tile shows nothing: no studs on top, no tubes
    # underneath, and still a stud needed under every position it covers. Said
    # here so the two fields cannot disagree about the same part.
    if attachment["seats_on_studs"]:
        add("stud_tube", "female", 0,
            evidence="geometry" if scanned["tubes"] else "name")

    connections = []
    for fid in FAMILY_IDS:
        entry = found.get(fid)
        if not entry:
            continue
        connections.append({
            "id": fid,
            "name": entry["name"],
            "group": GROUP_OF[fid],
            "motion": MOTION_OF[fid],
            "does": MOTION_LABELS[MOTION_OF[fid]],
            "roles": sorted(entry["roles"]),
            # Only studs are counted, because only studs are countable. A stud
            # primitive is one stud. A pin's shaft may be drawn from three
            # `confric` segments and a clip from one primitive or four, so the
            # number of references says how the part was modelled and nothing
            # about how many things can be plugged into it — and a number that
            # looks like an answer and is not is worse than no number.
            "count": entry["count"] if fid in _COUNTABLE else None,
            "evidence": "geometry" if "geometry" in entry["evidence"] else "name",
        })

    return {
        "connections": connections,
        # what makes a part interesting to a builder: anything beyond the
        # plain stud-and-tube every brick already has
        "special_connections": [c["id"] for c in connections
                                if c["id"] != "stud_tube"],
        "groups": sorted({c["group"] for c in connections}),
        # what this part lets you do, which is what a build is usually looking
        # for. "rigid" is dropped: every part holds still, so saying so of a
        # brick tells nobody anything.
        "moves": sorted({c["motion"] for c in connections
                         if c["motion"] != "rigid"}),
        "attachment": attachment,
        "readable": scanned["readable"],
    }


def _attachment(scanned, found, category, width_studs, depth_studs,
                body_height=None):
    """What has to be there for this part to go on: the other half of a fit.

    Dimensions say how much room a part takes. This says what it lands on —
    the number of studs it covers and must have under it, or the connectors it
    plugs into when it is not a studded part at all. A part whose attachment is
    unknown says so rather than claiming zero.
    """
    whole_plates = (body_height is not None and body_height > 0
                    and abs(body_height / PLATE_LDU
                            - round(body_height / PLATE_LDU)) * PLATE_LDU
                    <= _HEIGHT_TOLERANCE)
    seats = bool(scanned["tubes"]
                 or (scanned["top_studs"] and whole_plates)
                 or _normalise(category) in _SYSTEM_CATEGORIES)
    studs = None
    if seats and width_studs and depth_studs:
        studs = int(width_studs) * int(depth_studs)

    needs = []
    for fid, entry in found.items():
        # Studs of any kind are excluded: a part's own studs are what other
        # parts need, not what it needs. What belongs here is what has to
        # already be in the model for this part to have anywhere to go.
        if fid in ("stud_tube", "snot"):
            continue
        if "male" not in entry["roles"] or not entry["count"]:
            continue
        needs.append({"type": fid, "name": LABELS[fid]})

    if studs:
        summary = (f"sits on {studs} stud{'' if studs == 1 else 's'}"
                   + (f" ({width_studs}x{depth_studs})"
                      if width_studs and depth_studs else ""))
    elif needs:
        summary = "goes into " + " or ".join(
            f"a {n['name'].lower()} connection" for n in needs[:2])
    elif seats:
        summary = "seats on studs, but not on a plain rectangle of them"
    else:
        summary = "does not attach by studs — see its connection types"

    return {"studs_required": studs, "connectors_required": needs,
            "seats_on_studs": seats, "summary": summary}
