"""Seeing the model: LeoCAD renders, and a vision model that reads them.

``validate_model`` answers a question about geometry — is every part on the
stud grid, does anything share solid plastic. It cannot answer the question the
user actually asked, which is whether the thing looks like a car. A model can
pass every check and be a grey lump.

So the model gets rendered and looked at:

* **Rendering** is LeoCAD, headless, six viewpoints, about a third of a second
  for a small model. It happens on *every* write, wrong models included — the
  picture is what the user is waiting for, and one of a broken build is worth
  more to them than a clean report they cannot see.
* **Looking** is a separate vision model, because the builder is text-only. It
  is given the six views as one contact sheet and asked what is wrong with
  the thing in them, in words the builder can act on.

Everything here degrades rather than fails. No LeoCAD means no pictures and a
build that still works; no vision model means pictures with no critique.
"""

import base64
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .config import (PROJECT_ROOT, RENDER_ANGLES, RENDER_SIZE, RENDER_TIMEOUT,
                     RENDER_VIEWS, RENDERS_DIR, VISION_DESCRIBE_MAX_TOKENS,
                     VISION_ENABLED, VISION_FALLBACKS, VISION_MAX_TOKENS,
                     VISION_MODEL, VISION_MODEL_PINNED, VISION_NO_THINKING,
                     VISION_TEMPERATURE, VISION_TEMPLATE_KWARGS)

# Three views on a row: six tiles come out as a 3x2 sheet close to square, and
# a squarish sheet survives a vision model's resizing better than a tall one,
# which loses width off every tile at once.
SHEET_COLUMNS = 3
LABEL_HEIGHT = 22


class NotAvailable(RuntimeError):
    """LeoCAD is not installed, or the vision model could not be reached."""


# -- rendering ---------------------------------------------------------------

def leocad_binary():
    """The LeoCAD executable, or None. Renders with no display, unlike LPub3D."""
    found = shutil.which("leocad")
    if found:
        return found
    images = sorted((PROJECT_ROOT / "simulator").glob("LeoCAD-*.AppImage"))
    return str(images[0]) if images else None


def available():
    return leocad_binary() is not None


def render(source, out_dir, stem="model", views=RENDER_VIEWS, size=RENDER_SIZE):
    """Render one model from several viewpoints. Returns ``[(view, path)]``.

    ``source`` is a path to an existing model file. Each view is a separate
    LeoCAD invocation — it places the camera once per run — and a view that
    fails is dropped rather than failing the set, since five pictures are
    nearly as good as six and no pictures is much worse.
    """
    binary = leocad_binary()
    if not binary:
        raise NotAvailable(
            "LeoCAD is not installed, so the model cannot be rendered. See "
            "simulator/README.md.")

    source = Path(source).resolve()
    if not source.is_file():
        raise NotAvailable(f"no such model file: {source}")

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    width, height = size

    made = []
    for view in views:
        target = out_dir / f"{stem}-{view}.png"
        # A preset name goes to --viewpoint; anything in RENDER_ANGLES is a
        # camera orbited to a latitude and longitude LeoCAD has no name for.
        angles = RENDER_ANGLES.get(view)
        placement = (["--camera-angles", str(angles[0]), str(angles[1])]
                     if angles else ["--viewpoint", view])
        try:
            subprocess.run(
                [binary, str(source), "--image", str(target),
                 "-w", str(width), "-h", str(height),
                 *placement, "--aa-samples", "4", "--shading", "full"],
                capture_output=True, text=True, timeout=RENDER_TIMEOUT)
        except (subprocess.TimeoutExpired, OSError):
            continue
        if target.is_file():
            made.append((view, target))

    if not made:
        raise NotAvailable(
            "LeoCAD produced no image. The model may have no parts in it, or "
            "every part reference in it may be unresolvable.")
    return made


def render_model_file(path, project=None, views=RENDER_VIEWS, size=RENDER_SIZE):
    """Render a model into the shared renders directory.

    One directory per project so a second project never overwrites the first,
    and stable filenames inside it so the app can point an <img> at a URL that
    does not change between builds.
    """
    path = Path(path)
    stem = _safe(project or path.parent.name or path.stem)
    out_dir = RENDERS_DIR / stem
    return render(path, out_dir, stem=_safe(path.stem), views=views, size=size)


def _safe(name):
    keep = [c if (c.isalnum() or c in "-_") else "-" for c in str(name or "model")]
    return "".join(keep).strip("-")[:60] or "model"


# -- the contact sheet -------------------------------------------------------

def contact_sheet(images, target, columns=SHEET_COLUMNS):
    """Tile the views into one labelled image.

    One image rather than four: a vision model handles a single picture more
    reliably than a sequence it has to keep in order, and the labels are what
    let a critique say "the front view" and mean something.
    """
    from PIL import Image, ImageDraw

    from ..instructions import _font

    tiles = [(view, Image.open(path).convert("RGB")) for view, path in images]
    if not tiles:
        raise NotAvailable("nothing to tile")

    cell_w = max(im.width for _, im in tiles)
    cell_h = max(im.height for _, im in tiles) + LABEL_HEIGHT
    columns = max(1, min(columns, len(tiles)))
    rows = (len(tiles) + columns - 1) // columns

    sheet = Image.new("RGB", (cell_w * columns, cell_h * rows), "white")
    draw = ImageDraw.Draw(sheet)
    font = _font("dm-sans-500.woff2", 15)

    for index, (view, image) in enumerate(tiles):
        x = (index % columns) * cell_w
        y = (index // columns) * cell_h
        draw.text((x + 8, y + 4), view.upper(), font=font, fill="#17191E")
        sheet.paste(image, (x + (cell_w - image.width) // 2, y + LABEL_HEIGHT))
        draw.rectangle([x, y, x + cell_w - 1, y + cell_h - 1], outline="#D8DCE3")

    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(target)
    return target


# -- the vision model --------------------------------------------------------

CRITIC_PROMPT = """\
You are looking at renders of a LEGO model built out of real LDraw parts.

The views are labelled. **HOME, ORBIT90, ORBIT180 and ORBIT270 are the same \
model seen from four corners, each a quarter turn further round**; FRONT and \
TOP look straight down an axis. Between the four corner views there is nowhere \
on the model that is not visible from at least one of them, so if something \
looks wrong in one view, check the next corner round before calling it: a part \
that is hidden in one and clearly attached in another is attached. And if \
something is visible in only one view, it is still there — an arm, a tool or a \
chimney that only ORBIT180 can see is a real part of this model, not an \
artefact.

You are the only one who can see this model. The person who built it works in \
coordinates and cannot look at it at all, so your job is to report what the \
numbers do not show: whether it reads as the thing it is meant to be.

**You are helping them improve it, not grading it.** Say what already works and \
should be kept, then give changes concrete enough to carry out without seeing \
anything — which part, where, how far, in studs or LDU. Be straight about what \
is wrong; write it as work to do.

Judge only what is visible. Do not comment on part numbers, colours you cannot \
see, or anything you are guessing at. A white or grey background is the render, \
not the model.

# What has already been measured, and what only you can see

You may be given a block of **measured facts** about this model — its size in \
studs, whether every part is joined to the rest of it, whether anything is \
unsupported. Those were computed from the coordinates by a geometry checker. \
They are not opinions and they are not up for debate.

**Where a measurement contradicts your impression, the measurement is right.** \
If you are told every part is connected and it *looks* to you like two clumps, \
then it is one build that reads as two — which is worth saying, in exactly \
those words, as a proportion or spacing problem. It is not a disconnection, and \
reporting it as one sends someone to reattach parts that are already attached.

This is the division of labour, and it is what makes your report worth having:

| measured for you | yours alone |
|---|---|
| is it connected, is it floating, does it overlap | does it *read* as the thing |
| how many studs wide, tall, deep | are the proportions right |
| every coordinate | is anything missing, is it characterless |

So do not estimate distances. You are looking at small tiles of a downscaled \
sheet, and a number you produce by eye — "move it 8 LDU" — arrives at the \
builder looking exactly like a number that was measured, and gets applied. Say \
**what the relationship should be** instead: *"the roof should sit down on the \
tops of the walls, not hover above them"*, *"the wheels should be tucked under \
the body rather than standing outside it"*. The builder has every coordinate \
and can work out the arithmetic; what it cannot do is look.

**Answer immediately. Do not think step by step, do not weigh the views \
against each other in writing, and do not narrate what you are noticing.** \
Look, then write the JSON. Nothing before it, nothing after it, no ```json \
fence — the first character you write is `{` and the last is `}`.

Answer as one JSON object and nothing else:

{
  "reads_as": "what the model actually looks like, in a few words",
  "recognisable": true or false,
  "one_build": true or false,
  "separate_pieces": ["anything standing apart from the main build, named"],
  "issues": [
    {"what": "one concrete visual problem",
     "where": "which view shows it, and where in it",
     "fix": "the change that resolves it, said as a relationship rather than "
            "a measurement: which part, and where it should end up relative "
            "to what it belongs to",
     "severity": "fatal" | "major" | "minor"}
  ],
  "good": ["what already works and should be kept as it is — be specific, so "
           "it does not get broken while something else is fixed"],
  "character": {
    "generic": true or false,
    "why": "if generic: what makes this read as a placeholder rather than as \
the thing itself — one sentence",
    "one_flourish": "the single cheapest change that would give it character, \
concrete enough to carry out blind: which part, where, what colour"
  },
  "verdict": "one sentence: what it has got right, and the single most \
valuable next change — or that it is finished"
}

## About `character`

This is the one part of your answer that is **not** about anything being wrong.

A model can have every part in the right place, no gaps, correct proportions, \
nothing floating — and still be a grey box that happens to be house-shaped. \
Nothing else in this report catches that, because on every other axis it is \
fine. So: does it read as *the thing*, or as a placeholder for the thing?

Set `generic` true when the model is correct but characterless, and give **one** \
flourish — the smallest change with the largest effect. An accent colour on the \
window frames. A row of cheese slopes along the roof ridge. A chimney. A pair of \
round tiles for headlights. One thing, specific, buildable.

`character` is **advice, not a fault.** Never put it in `issues`, never let it \
make `recognisable` false, and never let it change `verdict` if the model is \
otherwise done. A build that is finished and plain is finished. If the model \
already has character, set `generic` false and leave `one_flourish` empty.

Look hardest for these, in order:

1. **Is it ONE build?** Everything asked for should be joined into a single \
connected model — attached to it, standing on it, built into it. If you can see \
a clump of bricks sitting on its own with clear air between it and the main \
model, say so: set `one_build` false, name every stray clump in \
`separate_pieces`, and raise it as the first issue with a fix that says what it \
should attach to. Grass, stones, flowers, a fence, a chimney, a door, a sign — \
these belong ON the build.

   **Unless you were told otherwise in the measured facts.** Connectivity is \
the one thing on this list that is measured exactly, and if the block above \
says every part is joined to the rest, then it is, whatever the picture \
suggests — a thin connection is easy to lose at this resolution and behind a \
near part. In that case leave `one_build` true and, if it still reads as \
separate lumps, say *that*: it is a problem of spacing or proportion, and the \
fix is to close the gap visually rather than to reattach anything.

   The other exception: things that are genuinely their own object — a car \
parked beside a house, a minifigure standing next to a tree, a separate tree in \
a scene. Those are meant to stand apart, and a scene of several objects on the \
same ground is correct. Judge it by what the model is meant to be, given above.

2. **Parts floating or hanging in the air** with nothing under them.
3. **Gaps and holes** where the build should be solid.
4. **Anything sticking through something else**, or a part clearly in the wrong \
place.
5. **Proportions** — a roof wider than its walls, a car taller than it is long, \
a tree with no trunk.
6. **Missing essentials** — a car with three wheels, a house with no roof, a \
chair with no seat.

7. **Every minifigure, part by part.** A minifigure is the one thing here that \
is assembled rather than built, and a half-assembled one still reads as a \
person at a glance — so count instead of glancing. For each figure in the \
model, check it has: **two legs on a hip block, a torso, two arms, a hand on \
each arm, and a head.** Headgear only if the model is meant to have it.

   Name what is missing as its own issue, one per figure: *"the minifigure by \
the door has no left arm"*, *"the figure on the roof is a torso and head with \
no legs"*. A figure missing a limb is a `major` fault; a figure that is a head \
floating with no body under it is `fatal`.

   Check anything a figure is meant to be **holding**, too. A tool, a weapon, \
a mug — it belongs in the fist, gripped, not hovering beside the hand and not \
lying on the floor at the figure's feet. A sword floating an inch from an open \
hand is one of the easiest faults to see and one of the least likely to be \
mentioned, so mention it: say which figure, which hand, and that the tool needs \
to move into it. A tool pointing through the figure's own leg or arm is the \
same fault wearing a different rotation.

   Then check the figure against **what the model is meant to be**, given \
above. If a firefighter was asked for and the figure has no helmet, if a rider \
was asked for and the figure is not on the horse, if the description says two \
people and you can see one — that is an issue, and it is about this figure, so \
say which one. A minifigure that is correctly assembled but is not the person \
that was asked for is still wrong.

An empty "issues" list is a real answer when the model is right. Do not invent \
faults to fill it.\
"""


def _data_uri(path):
    payload = base64.b64encode(Path(path).read_bytes()).decode("ascii")
    return f"data:image/png;base64,{payload}"


def _client():
    from .llm import make_client

    return make_client()


# Set from the app's Settings window; None leaves VISION_MODEL alone.
_model = None


def set_model(model):
    """Point the critic at another vision model.

    The mirror of ``blueprint.set_model``: the app stores a vision model
    alongside the builder's, and this is where that choice reaches the code
    that makes the call. Pass None to go back to ``VISION_MODEL``.
    """
    global _model
    _model = (model or "").strip() or None


def current_model():
    """The vision model a critique would use right now."""
    return _model or VISION_MODEL


def _candidates(model):
    """The vision models to try, in order.

    One only when the caller named a model, the app chose one, or the
    environment pinned one — working around someone's explicit choice is not
    robustness, it is ignoring them. The fallback chain exists for the default,
    which nobody picked.
    """
    if model:
        return [model]
    if _model:
        return [_model]
    if VISION_MODEL_PINNED:
        return [VISION_MODEL]
    return [VISION_MODEL] + [m for m in VISION_FALLBACKS if m != VISION_MODEL]


_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)


def _why(exc):
    """Why a candidate failed, in a few words.

    The exception's *message* is the whole of the diagnosis here — "returned
    nothing" and "answered with reasoning only" and a 503 from the provider are
    three different problems with three different fixes, and the class name is
    `RuntimeError` for two of them. Reporting only the class turned every one
    of these into the same unactionable line.
    """
    message = " ".join(str(exc).split())
    name = type(exc).__name__
    if not message:
        return name
    return f"{name}: {message[:160]}"


def _ask(client, model, system, body, max_tokens=None):
    """One vision call. Returns the reply text, or raises.

    Falls back to ``reasoning_content`` when a reasoning model spends its whole
    budget deliberating and emits no content: what it was thinking still names
    what it saw, and half an answer beats calling the model unreachable.
    """
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": body}]
    kwargs = {
        "model": model,
        "messages": messages,
        "temperature": VISION_TEMPERATURE,
        "max_tokens": max_tokens or VISION_MAX_TOKENS,
    }
    if VISION_NO_THINKING:
        kwargs["extra_body"] = {"chat_template_kwargs": dict(VISION_TEMPLATE_KWARGS)}

    # Streamed, like every other call this project makes. A model given a
    # budget large enough to think *and* answer takes long enough that the
    # router's gateway gives up on a single non-streamed response — a plain
    # 504, with nothing to show for the wait. Chunks keep it alive.
    try:
        content, reasoning = _stream(client, kwargs)
    except Exception:
        if "extra_body" not in kwargs:
            raise
        # An endpoint that rejects the template arguments still answers without
        # them, and a refused critique is worse than a deliberating one.
        kwargs.pop("extra_body")
        content, reasoning = _stream(client, kwargs)

    text = _THINK.sub("", content).strip()
    if text:
        return text

    # It deliberated instead of answering. Its reasoning is NOT used as the
    # critique: it is prose rather than the JSON the builder acts on, and
    # passing it off as an answer means the run records "the model was looked
    # at" on the strength of a monologue. Fail, so the next candidate is tried
    # and the caller is told plainly that this one only thinks.
    if reasoning.strip():
        raise RuntimeError(
            "answered with reasoning only and never wrote its conclusion")
    raise RuntimeError("returned nothing")


def _stream(client, kwargs):
    """One streamed completion, folded back into (content, reasoning)."""
    content, reasoning = [], []
    stream = client.chat.completions.create(**kwargs, stream=True)
    for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta is None:
            continue
        if getattr(delta, "content", None):
            content.append(delta.content)
        if getattr(delta, "reasoning_content", None):
            reasoning.append(delta.reasoning_content)
    return "".join(content), "".join(reasoning)


def _as_requirements(value):
    """The acceptance checklist as something a reader can read.

    It arrives as the stored record — a dict with ids, `check` kinds, `why`
    clauses and a `written_at` timestamp — and it used to be interpolated into
    the prompt with an f-string, which put a Python dict repr in front of the
    vision model under the heading "The person asked for:". Most of those 1,200
    characters were punctuation, and `why` is explicitly documented as a note
    for human readers that nothing checks.
    """
    if not value:
        return ""
    if isinstance(value, str):
        return value.strip()
    try:
        from . import requirements as acceptance

        # The list, not `as_text`'s whole block: that opens by telling the
        # reader the run does not end until it says so, which is addressed to
        # the builder. Said to the critic it invites it to believe it is the
        # gate, and it is not — it reports what it sees and something else
        # decides.
        wanted = acceptance.items(value)
        return "\n".join(f"- {r['text']}" for r in wanted)
    except Exception:
        return ""


def _as_brief(value):
    """The design brief as text, however it was stored."""
    if not value:
        return ""
    if isinstance(value, str):
        return value.strip()
    try:
        from . import brief as brief_module

        return (brief_module.as_text(value) or "").strip()
    except Exception:
        return ""


def critique(sheet, subject, requirements=None, question=None, model=None,
             client=None, measured=None, brief=None):
    """Ask a vision model what is wrong with the model in ``sheet``.

    ``measured`` is what the geometry checker already established about this
    model — its size, whether it is one connected piece, whether anything is
    unsupported. It is handed over as text beside the picture, and the reason is
    that those are the facts a vision model is worst at and this project is best
    at. A critic left to guess them guesses, and a guess arrives in the builder's
    lap indistinguishable from something that was seen. See ``_measured_facts``
    in tools.py for what goes in it.

    Returns the parsed JSON critique, or ``{"critique": <raw text>}`` when the
    model answered in prose. Raises ``NotAvailable`` if vision is switched off
    or no candidate model could be reached.
    """
    if not VISION_ENABLED:
        raise NotAvailable(
            "visual critique is switched off (LDRAW_VISION=0); the renders "
            "were still written.")

    asked = [f"This model is meant to be: {subject}." if subject else
             "Say what this model appears to be."]
    # What the model is supposed to look like, decided before it was built.
    # The critic used to be given no brief at all, and the cost of that is not
    # theoretical: a house whose brief chose a gabled roof was built with a
    # flat one, the checklist did not ask for a gable, and the critic — judging
    # against its own idea of a house — reported the roof "completely wrong".
    # It was right, and nothing it had been shown said so.
    rendered_brief = _as_brief(brief)
    if rendered_brief:
        asked.append("What it was meant to look like — this was decided before "
                     "the build started, so a model that departs from it is "
                     "departing from its own design:\n" + rendered_brief)
    rendered = _as_requirements(requirements)
    if rendered:
        asked.append("What it has to satisfy:\n" + rendered)
    if question:
        asked.append(f"They specifically want to know: {question}")
    if measured:
        asked.append(measured)
    asked.append("Report what you see.")

    body = [
        {"type": "text", "text": "\n".join(asked)},
        {"type": "image_url", "image_url": {"url": _data_uri(sheet)}},
    ]

    client = client or _client()
    candidates = _candidates(model)
    chosen = len(candidates) == 1  # named by the caller, the app or the env

    tried, loose = [], None
    for candidate in candidates:
        try:
            text = _ask(client, candidate, CRITIC_PROMPT, body)
        except Exception as exc:
            tried.append(f"{candidate} ({_why(exc)})")
            continue

        result = _structured(text)
        result["vision_model"] = candidate
        if result.get("_unstructured") and not chosen:
            # It answered, but in prose rather than the shape the builder can
            # act on — usually a reasoning model that spent its budget
            # deliberating and never wrote its conclusion. Keep it in case
            # nothing better turns up, and try the next model.
            tried.append(f"{candidate} (unstructured)")
            loose = loose or result
            continue
        result.pop("_unstructured", None)
        return result

    if loose is not None:
        loose.pop("_unstructured", None)
        loose["note"] = ("this critique is prose, not the structured report "
                         "that was asked for — read it as a comment rather "
                         "than a checklist")
        return loose

    raise NotAvailable(
        "no vision model could be reached, so the model was rendered but not "
        f"looked at. Tried: {', '.join(tried)}. Set LDRAW_VISION_MODEL to one "
        f"your token can reach, or LDRAW_VISION=0 to stop asking.")


# -- the reference image -----------------------------------------------------

DESCRIBE_PROMPT = """\
You are describing a picture that someone wants rebuilt out of LEGO bricks. \
The person who will build it **cannot see the picture at all** — they work in \
coordinates and part numbers, and your description is the only thing standing \
between them and guessing. Everything you leave out, they will invent.

So describe it completely and concretely. Not what it evokes — what is there.

# First: how many separate things are in the picture?

Before describing anything, count the **free-standing objects** — the things \
that would still be whole if you picked them up and carried them away. A \
lumberjack standing beside a tree is two objects. A house with a chimney is \
one: the chimney comes away in pieces. A car on a road is one object and some \
scenery.

This decides how the build is split up, so it is the first thing anybody needs \
and the easiest thing to get wrong by describing a scene as though it were one \
object. List them in `objects`, largest first, and say which is the real \
subject and which is scenery. **If there is only one thing, say so with one \
entry** — that is the common case and it is not a failure.

Then say what they are **doing with each other**. Two objects in a picture are \
almost never just near each other: one holds, carries, leans on, sits on, \
faces, stands in front of or reaches into another, and the model is wrong if \
they end up side by side like items on a shelf. Put each object's own part of \
that in `with_others`, and the whole layout in `arrangement`.

Everything after this describes the objects *together*, as the picture shows \
them: `whole` is the silhouette of all of them, and a `parts` entry says which \
object it belongs to when there is more than one.

# Then describe it at four zoom levels, in this order

The description is built the way the model will be: the big shape first, then \
the parts that make it up, then the small things sitting on those parts, then \
the markings and finishes on those. Each level is a pass over the whole picture \
at a different distance, and **none of them may be skipped or traded for \
another**.

**Level 1 — THE WHOLE THING, seen from across the room.** Squint at it. What \
is the overall silhouette, how is the bulk distributed, how does it sit, which \
way does it face, and what are the ratios of the entire object? At this \
distance you cannot see any detail and you should not report any. This is the \
level that decides whether the finished model is recognisable at all: get the \
big shape wrong and no amount of correct detail rescues it.

**Level 2 — THE MAJOR PARTS, seen from a few steps away.** Break the whole \
into the handful of large pieces it is made of — the cab, the roof, the left \
wing, the trunk, the base. Aim for three to eight of them. For each one: how \
big it is *relative to the whole and to its neighbours*, where it sits, what \
shape it is, what colour it is, and **how it meets the parts around it**. \
These are the pieces the builder will actually assemble.

**Level 3 — THE SMALL DETAILS, seen with your nose against it.** Now go part \
by part and report everything sitting on, in or through each one. Not the \
interesting ones — **all** of them. Sweep each part for every one of these \
before you move to the next:

- things that stick out: handles, hinges, mirrors, pipes, antennae, spouts, \
ladders, steps, hooks, latches, knobs, lamps, chimneys, exhausts
- things that go in: windows, doors, hatches, vents, grilles, slots, recesses, \
panels, seams
- things that go all the way through: holes, arches, gaps, openings, the space \
under a table or between two legs
- things that turn: wheels, rollers, dials, propellers, and which way each faces
- things that repeat: railings, slats, planks, ribs, studs, bolts, tiles, \
fence posts — with a count and a spacing
- the edges themselves: sharp, rounded, chamfered, stepped, bevelled, frayed

Every detail must say **which part of Level 2 it belongs to**, how big it is \
relative to *that part*, and where on it it sits. A detail that is not attached \
to a named part is a detail the builder will put in the wrong place.

**Level 4 — THE SURFACE, held up to the light.** The last pass is what is \
*printed on* rather than *built into* the object: writing, numbers, letters, \
logos, signs, badges, stripes, chevrons, decals, dials with faces on them, and \
the finish of each surface — glossy, matte, transparent, translucent, metallic, \
chrome, rough, woodgrain, rusted, worn, dirty. Quote text exactly, character for \
character, and say where it sits and how big it is. These go in `markings`.

The levels nest: a marking belongs to a detail or a part, a detail belongs to a \
part, and a part belongs to the whole. Work down, never sideways — finish the \
whole before you name a part, finish the parts before you name a detail, and \
finish the details before you read the writing on them.

# Walk round it: six faces, not the one facing the camera

An object has a front, a back, a left, a right, a top and a bottom, and the \
picture shows you at most three of them. The builder builds all six. So for the \
whole object and for **every part**, say what is on each face you can see, and \
say plainly which faces you cannot — that is what `faces` is for.

Where a face is hidden, the answer is not silence. Say what the object being \
what it is implies: the back of a house has a wall like the front but usually \
plainer, the underside of a car is flat and dark, the far side of a symmetrical \
thing is the mirror of the near side. Put that in `unseen`, marked as the \
reading it is. **A hidden face described as "not visible" and nothing else is a \
face the builder leaves as a hole.**

# This is being built as a solid object, so give it three dimensions

The picture is flat. **What is being built is not.** It is a construction out \
of bricks, standing in space, and it has to be right from every side — so \
every size you give must cover all three:

- **width** — left to right, across the front
- **height** — bottom to top, which is how many courses of brick get stacked
- **depth** — front to back, which is the one a flat picture hides and the one \
that is therefore always forgotten

Say all three for the whole object and for **every part** in `parts`, always \
relative — "the wall is four times as wide as it is thick, and half as tall as \
it is wide". A part given only its width and height gets built one brick deep, \
flat as a stage set, and the model falls apart the moment it is turned around.

**Height especially.** Say how tall each part is against its neighbours and \
against the whole — what stands proud of what, what is level with what, where \
each part starts and stops up the object. The builder stacks in layers from \
the ground up and works out every Y level from exactly this.

**Depth is usually inferred, and you must infer it rather than omit it.** A \
photograph taken from the front does not show how deep a house is. Say what \
you can see, then give your best reading of the depth from the perspective, \
the shadows, the visible side faces, and from what the object plainly is — a \
house is roughly as deep as it is wide, a signpost is a few centimetres thick, \
a car is much longer than it is wide. Mark it as inferred in \
`whole.depth_confidence` and in the part's own `depth`. **An inferred depth \
that says so is useful; a missing depth is a model built flat.**

# The answer

Answer as one JSON object and nothing else. Start with `{` and end with `}`. Do \
not think step by step first; look, then write.

{
  "subject": "what the thing is, in a few words",
  "one_line": "a single sentence a builder could work from",

  "objects": [
    {"name": "one free-standing thing, in a word or two — 'the lumberjack', \
'the pine tree'. Largest first",
     "what": "one sentence a builder could work from, for this object alone",
     "role": "subject if it is what the picture is of, scenery if it is only \
there to sit around the subject",
     "size": "how big it is next to the others — 'twice the height of the \
man', 'a third of the width of the house'",
     "with_others": "what this one is DOING with the others, and where it \
stands: 'holding the axe in both hands', 'leaning against the tree', \
'standing about its own width to the left of the trunk, facing it', \
'nothing — it stands on its own at the far side'"}
  ],
  "arrangement": "how the objects stand together as a group, in one or two \
sentences: who is where relative to whom, which way each faces, what touches \
what and what has a gap. This is how they get placed beside each other once \
each has been built, so give left/right, front/back and any distance as a \
multiple of an object's own size — 'the man stands one man-width to the left \
of the trunk, turned towards it; the axe is in his hands, not on the ground'. \
Say 'a single object' when there is only one",

  "whole": {
    "silhouette": "the outline of the entire object as one shape, as if it \
were a solid black cut-out — 'a tall narrow box with a triangle on top'",
    "mass": "where the bulk is: bottom-heavy, evenly spread, wide at the base \
and tapering, one big block with a small one beside it",
    "proportions": "the ratios of the WHOLE in all three dimensions, as \
ratios only — 'about twice as long as it is wide, and as tall as it is wide'. \
Never centimetres or inches",
    "depth_confidence": "how much of the front-to-back depth you can actually \
see, and how much of it you are inferring — say which",
    "symmetry": "what is mirrored and what is not: 'symmetrical left to right \
about the middle, not front to back', 'the left and right sides are different — \
a door on the left only'. Say it plainly, because half a symmetrical object \
described once is half a model built twice",
    "scale": "how big the real thing is, against something everyone knows — \
'about the size of a person', 'a hand's width', 'twice the height of a car'. \
This is what decides how many studs across the model gets built",
    "stance": "how it sits: standing on a flat base, on wheels, on legs, \
hanging, leaning",
    "footprint": "what actually touches the ground and where — 'four wheels at \
the corners', 'a flat base the whole width', 'two legs a third of the way in \
from each end'. What is underneath is what the model gets built up from",
    "orientation": "which way it faces, and from what angle the picture was \
taken — and therefore which sides you can and cannot see",
    "faces": "what is on each of the six faces of the whole object: front, \
back, left, right, top, bottom. Name each one and say either what is on it or \
that the picture does not show it",
    "dominant_colours": "the two or three colours someone would name from \
across the room"
  },

  "parts": [
    {"name": "what this piece of the object is — 'the cab', 'the roof', \
'the left wing'",
     "width": "left to right, relative to the whole and to its neighbours — \
'about a third of the total width, as wide as the door is tall'",
     "height": "bottom to top, the same way — and say what it is level with \
and what it stands proud of",
     "depth": "front to back, the same way. Give it even when the picture \
does not show it, and say so: 'not visible; about as deep as it is wide, \
since it is a house'",
     "sits_at": "how far up the object this part starts and stops — 'from the \
ground to a third of the way up', 'the top quarter, resting on the walls'",
     "shape": "its own three-dimensional shape: a box, a wedge, a cylinder, a \
slab, a tapering column — not just its outline",
     "angles": "every face of it that is not flat or upright, and by how much: \
'the roof rises about 40 degrees', 'the front slopes back one unit for every \
three it rises', 'the sides taper inwards towards the top', 'the corner is \
rounded over about a quarter of the width'. Say 'all faces square' when it is a \
plain box — that is an answer, and a useful one",
     "position": "where it sits: front/back, left/right, top/bottom, and how \
far along",
     "attaches": "how it meets the parts around it — resting on top of, \
hanging under, set into, butted against the side of, wrapped around, \
overlapping, sticking out past the edge of",
     "faces": "what is on this part's own front, back, left, right, top and \
bottom, and which of those the picture does not show",
     "openings": "anything that goes into or through this part — a window, an \
arch, a hole, a gap, a hollow — with roughly where and how big. 'none, it is \
solid' is an answer",
     "edges": "how its edges and corners are finished: sharp, rounded, \
chamfered, stepped",
     "colour": "the colour of this part in plain words, with its shade — 'dark \
red', 'pale sand yellow', 'light bluish grey', not 'red' or 'grey'",
     "finish": "glossy, matte, transparent, translucent, metallic, chrome, \
rough, woodgrain, worn, dirty — whatever the surface is doing with the light"}
  ],

  "composition": "how it is arranged in space: what sits on what, what is at \
the front, back, left, right, top and bottom, and how the parts meet",
  "relations": "how the parts sit together as a whole: what is aligned with \
what, what is centred and what is offset, what is flush and what overhangs, \
what is symmetrical and what is not, what touches and what has a gap between it",

  "details": [
    {"on": "which entry in `parts` this detail sits on — use the same name",
     "what": "the detail itself: a window, a door, a wheel, a stripe, a handle",
     "count": "how many of them, if there is more than one, and how they are \
spaced — 'four, evenly spaced along the front'",
     "size": "how big it is relative to THE PART IT IS ON — 'a quarter of the \
wall's height and an eighth of its width'",
     "shape": "its own outline: round, square, arched, oval, cross-shaped, \
tapered",
     "depth": "does it stick out from that part, sit flush with it, or is it \
recessed into it, or does it go all the way through — and by how much relative \
to the part's own depth",
     "position": "where on that part it sits — 'centred on the front face, \
just under the roofline'. Say which face, always",
     "colour": "its colour and shade, and the colour of any frame or outline \
around it"}
  ],

  "markings": [
    {"on": "which part or detail it is printed on — use the same name",
     "kind": "writing, a number, a logo, a sign, a badge, a stripe, a chevron, \
a pattern",
     "content": "exactly what it says or shows, quoted character for \
character where it is text; 'unreadable' if you cannot make it out, and never a \
guess at wording",
     "size": "how big it is against the thing it is on — 'about a third of the \
door's width'",
     "position": "where on that thing it sits, and which face",
     "colour": "the marking's colour, and the colour behind it"}
  ],

  "colours": [
    {"part": "which piece of it", "colour": "the colour and its shade in plain \
words — 'dark bluish grey', 'olive green', 'warm off-white'",
     "boundary": "where this colour starts and stops, and what it meets — 'the \
whole roof down to the gutter line, meeting the cream wall in a straight edge'",
     "note": "glossy, matte, striped, two-tone, weathered, and whether what you \
are seeing is the colour itself or a shadow or highlight on it"}
  ],

  "unseen": "the faces and features the picture does not show, and what to \
build there — your best reading, marked as one. 'The back is not visible; a \
house of this kind has the same wall as the front with a smaller window in it.' \
Never leave this empty: the builder builds every side either way, and the only \
question is whether they build it from your reading or from nothing",
  "background": "what is behind it, and whether any of that is part of the \
subject or just scenery",
  "build_priorities": ["the three to five things that must be right for a LEGO \
model to read as this, most important first"]
}

# Rules that hold at every level

1. **Sizes are always relative, and always in three dimensions.** The builder \
works on a stud grid and needs to know how big a thing is *compared to \
something else*, because that is what a count of studs comes from. "The cabin \
is about a quarter of the length, half the height of the body, and as deep as \
the body is" is buildable. "A small cabin" is not — small compared to what? \
Never give a measurement in centimetres or inches, and never give two \
dimensions where three are wanted.

2. **Colour on every visible thing, with its shade.** Not optional, not a \
finishing touch, and not only at Level 2 — a detail with no colour gets built in \
the colour of the part under it. A builder given no colour builds everything \
grey, and grey is not what anyone asked for. Say *which* red: dark red, bright \
red, reddish brown, coral. The bricks come in dozens of shades and the builder \
picks one from your words alone. Say where each colour stops, too — "red down to \
the waistline, white below it" is a model; "red and white" is a guess.

And tell colour from lighting. A photograph puts a highlight on the top of \
everything and a shadow under it, and neither is paint. If one end of a wall \
looks darker only because it is in shade, say the wall is one colour and note \
the shadow. A builder handed the shadow builds it out of dark grey bricks.

3. **How things meet, not just where they are.** Two objects in the same place \
is not the same as one sitting on the other. Say which: resting on top, hanging \
underneath, set into a recess, butted against the side, overlapping, passing \
through, wrapped around, joined at one edge, or simply near each other with a \
gap. A builder who knows a chimney *sits on* the roof puts it on the roof; one \
told only that there is a chimney puts it beside the house.

4. **Count things. Never write "some", "several", "a few" or "multiple".** \
Four windows is a different model from three, and "several windows" is a model \
with whatever number the builder felt like. Give the number and the spacing; \
where there are genuinely too many to count, give the number you would bet on \
and say it is an estimate — "about twenty planks, roughly a hand's width apart".

5. **Separate the subject from the background.** If it stands on grass, the \
grass is scenery unless it is clearly part of the thing.

6. **Do not invent features you cannot see — but do estimate the depth, and the \
hidden faces, that you cannot see.** These are different things, and the \
difference is not "visible or not", it is *how much the object itself tells \
you*. A logo on the hidden back face is a feature and you have no way to know it \
is there: leave it out. That the back has a wall at all, roughly as tall as the \
front, is not a guess — it is what a house is. Estimate the second kind, put it \
in `unseen` and in the part's own `depth`, and mark it as read rather than seen. \
Leaving a feature out costs a detail; leaving the depth out costs the whole \
model its third dimension; leaving the back out costs it a whole side.

7. **Do not talk about LEGO parts or part numbers.** Describe the *object* — \
its pieces, their sizes and how they meet. Choosing bricks is the builder's job.

8. **Write densely.** Every field is a sentence or two of fact, not a \
paragraph. There is a great deal to get through here and an answer that is \
lavish at the top runs out of room before the details — and an answer that stops \
part-way is thrown away entirely, not kept as far as it got. Short, specific, \
complete, in that order of priority.

**Every field above is required.** A description with perfect colours and no \
arrangement builds a pile of the right-coloured bricks; one with perfect \
arrangement and no colours builds a grey model of the right shape; one with the \
parts but no details builds a blank box; one with widths and heights but no \
depths builds a flat cut-out that falls over; one that describes only the face \
the camera saw builds a stage flat with nothing behind it. All of those are \
failures. Fill in every field, and where the honest answer is "the picture does \
not show this", write that rather than nothing — a field left empty reads as a \
feature that is not there.\
"""

COMPARE_PROMPT = """\
You are given two pictures of the same intended thing.

**The first image is the REFERENCE** — what the person asked for.
**The second image is renders of the LEGO model that was built** to match it, \
shown from six labelled viewpoints: HOME, ORBIT90, ORBIT180 and ORBIT270 are \
four corners a quarter turn apart, and FRONT and TOP look straight down an \
axis. Judge the model by all six together — the reference is one fixed angle, \
so something it never shows may be perfectly built on a side you can now see.

**You are a build coach, not a judge.** The person reading this is going to use \
it to improve the model, and they cannot see either picture. A verdict tells \
them they failed; a list of changes tells them what to do next. Write the \
second one.

So: say what already works and must be kept, then give the specific changes \
that would bring the model closer to the reference — concrete enough to act on \
without looking at anything.

Judge it as LEGO. It is built out of rectangular bricks on a stud grid, so it \
can never match a photograph exactly, and you must not ask it to. Curves come \
out stepped, fine detail disappears, textures become flat colour. None of that \
is a failure. What matters is whether someone shown the model would recognise \
the reference in it.

**Judge the object, not the room.** The reference is a photograph, so it has a \
floor, a wall, a surface, and whatever else happened to be lying about. None of \
that was asked for and none of it is being built. A model standing on nothing, \
against a plain background, with no floor and no scenery around it, is exactly \
right — never list "add a base", "add the floor", "add the rug" or "add the \
background" as a change. Only the thing itself counts.

Answer as one JSON object and nothing else. Start with `{` and end with `}`. Do \
not think step by step first.

{
  "matches": true or false,
  "confidence": "high" | "medium" | "low",
  "closeness": "how far along it is, in a few words: 'nearly there', 'the right \
shape but the wrong colours', 'a good start, missing the main feature'",
  "reads_as": "what the LEGO model actually looks like, ignoring the reference",
  "keep": ["what the model already gets right and must NOT be changed — be \
specific, so it does not get broken while something else is fixed"],
  "changes": [
    {"do": "one concrete change, as an instruction: 'recolour the four cabin \
bricks red', 'add two wheels under the rear', 'lower the roof by one brick'",
     "where": "where on the model, and which view shows it",
     "how": "how to actually build it — part shapes, how many, which studs, \
how far to move something in studs or LDU",
     "brings": "what this gets you: which part of the reference it matches",
     "aspect": "composition" | "colour" | "shape" | "proportion" | "detail",
     "severity": "fatal" | "major" | "minor"}
  ],
  "differences": [
    {"aspect": "composition" | "colour" | "shape" | "proportion" | "detail",
     "reference": "what the reference has",
     "model": "what the model has instead",
     "severity": "fatal" | "major" | "minor",
     "fix": "the same change, in one line"}
  ],
  "verdict": "one encouraging sentence: what it has got right, and the single \
most valuable next change"
}

**`changes` is the important field.** Order it by how much each change buys — \
the one that most improves recognition first. Every entry must be something a \
builder could carry out immediately:

- Good: *"add a 2x2 red brick either side of the rear platform, seated on the \
studs at the back corners, to give it the raised sides the reference has"*
- Useless: *"make it look more like the reference"*, *"improve the proportions"*

Give three to six changes when the model needs work, and an empty list when it \
does not. Say the *how* even when it seems obvious — the builder is working in \
coordinates and cannot see what you can.

**Tone.** Be straight about what is wrong — a builder who is told a broken \
model is fine cannot fix it — but write it as work to do, not as a failure. \
"The body and colours are right; it needs wheels and a lower roof to read as \
the reference" is honest *and* useful. "The model fails to capture the \
reference" is neither.

How to decide `matches`:

- **false** — the model is a different thing, or is unrecognisable. A car built \
for a reference of a house. A grey lump. Colours with no relation to the \
reference. The main shapes arranged completely differently. Anything you would \
not recognise without being told.
- **true** — someone would look at the model and say "that's meant to be the \
thing in the picture". The big shapes are in the right places, the main colours \
are right, and the features that make the subject what it is are present.

Grade every difference honestly, and grade it by how much it hurts recognition:

- `fatal` — on its own it makes the model read as something else.
- `major` — clearly wrong and worth fixing, but you still recognise the subject.
- `minor` — a detail. Stepped curves, a missing window, one brick of a slightly \
different shade. **Do not fail a model over minor differences.**

Check all four, every time:

1. **Composition** — is the arrangement the same? Right things on top, in \
front, beside. This is the one that decides recognition.
2. **Colour** — are the main colours those of the reference? A blue car built \
red is a real failure, not a detail.
3. **Shape and silhouette** — does the outline read the same?
4. **Distinguishing details** — the features that make the subject that \
subject: wheels, windows, a chimney, a mast, ears.
5. **People.** If the reference has a person in it and the model has a \
minifigure, check the figure the way you would check any other subject: is it \
**complete** — two legs, a torso, two arms with a hand on each, a head — and is \
it the person the reference shows? Right colours for the clothing, the right \
headgear or hair, in the same place doing the same thing. A figure missing an \
arm or a leg is `major`; a figure that is a head and torso with nothing under \
it is `fatal`.

   And if the reference has a person and the model has none at all, that is a \
missing subject, not a missing detail — say so as a change with the parts it \
needs.\
"""


ASK_PROMPT = """\
You are looking at a picture that someone is rebuilding out of LEGO bricks. \
The builder **cannot see it at all** — they work in coordinates and part \
numbers. They have already been given a full written description of it, and \
they are now asking you the specific things that description did not settle.

**They get one set of questions.** Answer every one of them, completely, and \
answer the question that was asked rather than the one you would rather answer.

Rules that matter more than anything else here:

- **Answer only from the picture.** If the picture does not show what they are \
asking about — the back of the object, the underside, something hidden behind \
another part — say so plainly and set `visible` to false. A confident invention \
is far worse than "not visible": they will build it.
- **Answer in what a builder can use.** Counts, ratios, positions, colours, \
which part sits on which. "The chimney is about one sixth the width of the \
roof and sits on the left slope, two thirds of the way back" is an answer. \
"It has a chimney" is not.
- **Never give measurements in centimetres or inches.** Everything relative: \
against the whole object, or against another part of it.
- **Do not talk about LEGO parts or part numbers.** Describe the object. \
Choosing bricks is the builder's job.
- If a question rests on something false about the picture, say so — that is \
the most valuable answer you can give.

Answer as one JSON object and nothing else. Start with `{` and end with `}`. Do \
not think step by step first; look, then write.

{
  "answers": [
    {"question": "the question, repeated back",
     "answer": "the answer, concrete and relative, in one to four sentences",
     "visible": true or false,
     "confidence": "high" | "medium" | "low"}
  ],
  "also_worth_knowing": "anything you can see that they did not ask about but \
plainly needed to — or an empty string if there is nothing"
}

`answers` must hold exactly one entry per question, in the order they were \
asked.\
"""


def _pictures(image):
    """One picture, or several, as checked paths.

    Every call that reads the reference takes either, because a project may
    hold up to four and all of them are the specification — see reference.py.
    """
    many = [image] if isinstance(image, (str, Path)) else list(image or ())
    paths = [Path(p) for p in many]
    if not paths:
        raise NotAvailable("there is no reference picture to look at")
    for path in paths:
        if not path.is_file():
            raise NotAvailable(f"no such image: {path}")
    return paths


def _one_subject(count):
    """How to read several reference pictures, when there are several."""
    if count < 2:
        return None
    return (f"There are {count} pictures, numbered 1 to {count} in the order "
            f"they are given. They are all references for the **same build** — "
            f"usually the same thing from different angles, sometimes a detail "
            f"of it, occasionally something the others do not show at all. "
            f"Read them together, as one specification: say a thing once "
            f"rather than once per picture it appears in, and where only one "
            f"picture shows something, say which.")


def ask(image, questions, request=None, description=None, model=None,
        client=None):
    """Answer specific questions about the reference picture, or pictures.

    ``describe`` says everything about a picture in one shape; this answers what
    is left over — the things a builder only discovers they need once they are
    placing bricks. The written description travels with the questions so the
    answer can correct it rather than repeat it.
    """
    if not VISION_ENABLED:
        raise NotAvailable(
            "asking about the picture needs a vision model and vision is "
            "switched off (LDRAW_VISION=0)")

    images = _pictures(image)

    asked = ["Someone is rebuilding this in LEGO and cannot see it. "
             "Answer their questions about it."]
    together = _one_subject(len(images))
    if together:
        asked.append(together)
    if request:
        asked.append(f"They are building: {request}")
    if description:
        asked.append("This is the description they were already given, so do "
                     "not simply repeat it — answer what it left open, and "
                     "correct it where it is wrong:\n"
                     f"{_brief(description)}")
    asked.append("Their questions, in order:\n" + "\n".join(
        f"{i}. {q}" for i, q in enumerate(questions, start=1)))

    body = [{"type": "text", "text": "\n\n".join(asked)}]
    body += [{"type": "image_url", "image_url": {"url": _data_uri(p)}}
             for p in images]
    return _vision(body, ASK_PROMPT, model, client,
                   expected=("answers", "also_worth_knowing"))


def describe(image, request=None, model=None, client=None):
    """Describe the reference in detail. Returns the parsed description.

    ``image`` is one picture or several. Several are described in **one** call
    rather than one call each, and that is the whole point of taking them
    together: four separate descriptions of four photographs of one car are
    four cars as far as everything downstream can tell, and the run splits into
    four builds. One call sees that it is one car from four sides.

    ``request`` is what the user asked for in words. The pair travels together:
    the same picture is described differently depending on whether the person
    wants the vehicle in it or the building behind it.
    """
    if not VISION_ENABLED:
        raise NotAvailable(
            "image description needs a vision model and vision is switched off "
            "(LDRAW_VISION=0)")

    images = _pictures(image)

    asked = ["Describe this for someone who will rebuild it in LEGO."]
    together = _one_subject(len(images))
    if together:
        asked.append(together)
    if request:
        asked.append(f"They asked for: {request}\n\nDescribe the part of the "
                     f"picture that request is about, and say plainly if the "
                     f"picture does not show it.")
    body = [{"type": "text", "text": "\n\n".join(asked)}]
    body += [{"type": "image_url", "image_url": {"url": _data_uri(p)}}
             for p in images]
    return _vision(body, DESCRIBE_PROMPT, model, client,
                   expected=("subject", "objects", "arrangement", "whole",
                             "composition", "parts", "colours", "details"),
                   max_tokens=VISION_DESCRIBE_MAX_TOKENS)


def compare(sheet, reference, subject=None, description=None, model=None,
            client=None):
    """Judge renders of the built model against the reference image.

    Every picture goes in one call, the reference or references first and the
    renders last, so the model is comparing rather than recalling. The written
    description goes in too — it is what the builder was working from, and a
    mismatch between it and the reference is worth catching here.
    """
    if not VISION_ENABLED:
        raise NotAvailable(
            "comparing against the reference needs a vision model and vision "
            "is switched off (LDRAW_VISION=0)")

    references = _pictures(reference)
    sheet = Path(sheet)
    if not sheet.is_file():
        raise NotAvailable(f"no such render: {sheet}")

    first = ("The first image is the REFERENCE. The second is renders of the "
             "LEGO model built to match it."
             if len(references) == 1 else
             f"The first {len(references)} images are the REFERENCE — the same "
             f"thing photographed more than once, so a difference that shows "
             f"in one of them and not the others is still a difference. The "
             f"last image is renders of the LEGO model built to match them.")
    asked = [first]
    if subject:
        asked.append(f"It is meant to be: {subject}")
    if description:
        asked.append("This is how the reference was described to the builder:\n"
                     f"{_brief(description)}")
    asked.append("Does the model read as the reference?")

    body = [{"type": "text", "text": "\n\n".join(asked)}]
    body += [{"type": "image_url", "image_url": {"url": _data_uri(p)}}
             for p in references]
    body.append({"type": "image_url", "image_url": {"url": _data_uri(sheet)}})
    return _vision(body, COMPARE_PROMPT, model, client,
                   expected=("matches", "differences", "verdict"))


def _brief(description, limit=5200):
    """A description as text, however it was stored.

    The limit is generous because a description is four passes over the picture
    — whole, parts, details, markings — plus a walk round all six faces, and
    clipping it at a paragraph would hand the comparison only the silhouette. It
    survives clipping gracefully all the same: the fields are ordered coarse to
    fine, so what falls off the end is always the finest detail rather than the
    shape of the thing.

    Raised from 2600 when the description grew: at the old limit a rich one was
    cut off around its first few parts, so the comparison was judging a model
    against a specification that stopped before the details it was being asked
    about.
    """
    if isinstance(description, dict):
        description = json.dumps(description, ensure_ascii=False)
    text = " ".join(str(description or "").split())
    return text if len(text) <= limit else text[:limit] + " ..."


def _vision(body, system, model, client, expected, max_tokens=None):
    """One vision request, over the candidate models, parsed and checked."""
    client = client or _client()
    candidates = _candidates(model)
    chosen = len(candidates) == 1

    tried, loose = [], None
    for candidate in candidates:
        try:
            text = _ask(client, candidate, system, body, max_tokens=max_tokens)
        except Exception as exc:
            tried.append(f"{candidate} ({_why(exc)})")
            continue

        parsed = _parse(text, expected)
        parsed["vision_model"] = candidate
        if parsed.get("_unstructured") and not chosen:
            tried.append(f"{candidate} (unstructured)")
            loose = loose or parsed
            continue
        # Kept as a public flag rather than dropped. A reply that is not the
        # shape that was asked for is usually one that was cut off mid-answer,
        # and the caller has to be able to tell — a truncated description
        # stored as *the* description is wrong for the life of the project.
        if parsed.pop("_unstructured", None):
            parsed["unstructured"] = True
        return parsed

    if loose is not None:
        loose.pop("_unstructured", None)
        loose["unstructured"] = True
        return loose

    raise NotAvailable(f"no vision model could be reached. Tried: "
                       f"{', '.join(tried)}.")


def _parse(text, expected):
    from .blueprint import _extract_json

    parsed = _extract_json(text)
    if isinstance(parsed, dict) and any(k in parsed for k in expected):
        return parsed
    return {"text": text, "_unstructured": True}


# The keys that make a critique actionable. A reply carrying none of them is
# prose about the model rather than a report on it.
_EXPECTED = ("reads_as", "issues", "verdict", "recognisable")


def _structured(text):
    """A critique reply as a dict, flagged when it is not the shape asked for."""
    from .blueprint import _extract_json

    parsed = _extract_json(text)
    if isinstance(parsed, dict) and any(k in parsed for k in _EXPECTED):
        return parsed
    return {"critique": text, "_unstructured": True}


def look(path, subject=None, requirements=None, question=None, project=None,
         views=RENDER_VIEWS, size=RENDER_SIZE, model=None, measured=None,
         brief=None):
    """Render a model and have it looked at. The whole loop in one call.

    ``measured`` is what the geometry checker already knows about this model —
    see ``critique``.

    Returns ``(images, sheet, critique_or_None, note_or_None)``. A critique that
    could not be obtained is a note, not an exception: the pictures are worth
    having on their own.
    """
    images = render_model_file(path, project=project, views=views, size=size)
    sheet_path = images[0][1].parent / f"{_safe(Path(path).stem)}-sheet.png"

    try:
        sheet = contact_sheet(images, sheet_path)
    except Exception as exc:
        return images, None, None, f"the views could not be tiled ({exc})"

    try:
        return images, sheet, critique(sheet, subject, requirements, question,
                                       model=model, measured=measured,
                                       brief=brief), None
    except NotAvailable as exc:
        return images, sheet, None, str(exc)


def check_vision(model=None):
    """Probe the configured vision model with a one-pixel image.

    Used by the self-test, so a missing or wrong ``LDRAW_VISION_MODEL`` is
    something you find out about before a build depends on it.
    """
    with tempfile.TemporaryDirectory(prefix="vision-probe-") as tmp:
        from PIL import Image

        probe = Path(tmp) / "probe.png"
        Image.new("RGB", (32, 32), "red").save(probe)
        result = critique(probe, "a solid red square, for testing",
                          question="What colour is this image?", model=model)
    return result
