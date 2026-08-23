"""What has to be true before a build is allowed to end.

Every pass before this one describes the model. ``decompose`` says how many
objects there are, ``brief`` says what each should look like,
``plan_construction`` says what it is made of and in what order. None of them
says **when it is done**, and so nothing ever checked: the gate asked whether
the file had been validated, rendered and looked at, which are properties of the
*run* rather than of the model. A table with three legs passed every one of
them.

So this is the pass that writes the acceptance criteria, and the node that
checks them:

* ``compose`` — once per object, before the first brick. A list of statements
  that are true or false of the finished model and nothing in between. Stored on
  disk, so a resumed run is held to the same list it started with and a reader
  can see afterwards what the build was actually judged on.
* ``check`` — at the end of every iteration. Each requirement answered
  ``true``/``false`` against the renders and the measurements, one at a time.
  All true ends the run; anything false is handed back as the work remaining.

# Why they must be boolean

An acceptance test that can be argued with is not a test. "Well proportioned"
lets a model through on the checker's mood, and worse, lets it *fail* on the
same — a builder cannot act on "not quite right", so a soft criterion produces
either a rubber stamp or an endless loop. The prompt in
``requirements_prompt.md`` is mostly one long argument for countability, because
that is the property the whole design rests on.

# Why the agent no longer calls `finish`

It used to decide for itself that it was done, against a gate that could only
check generic properties. Deciding you have finished is the one judgement a
builder is worst placed to make — it is the same model that has just spent
twenty steps convincing itself the thing it built is the thing it was asked for.

So the decision moved out. The builder builds and validates; the harness runs
this check when an iteration ends, and *the harness* ends the run. There is
still a way to stop honestly — see ``give_up`` in tools.py — because a build
that genuinely cannot satisfy a requirement must not spin forever.
"""

import json
import re
import time
from pathlib import Path

from .config import (OUT_DIR, REQUIREMENTS_CHECK_PROMPT_FILE,
                     REQUIREMENTS_SOURCE_PROMPT_FILE,
                     REQUIREMENTS_PROMPT_FILE, BLUEPRINT_TEMPERATURE,
                     DEFAULT_MODEL)

STORE_NAME = "requirements.json"

# A build with more than this many acceptance criteria is not being checked, it
# is being buried — and every one of them costs the checker attention it needs
# for the ones that matter. The user asked for as many as the model thinks the
# build needs, and this is only the ceiling on "thinks".
MAX_REQUIREMENTS = 40

# Words that make a requirement unanswerable. Kept as a filter rather than only
# as prompt guidance, because this is the one property the whole design rests on
# and a prompt is advice: a model that slips "well-proportioned" past the
# instructions would put a criterion in the list that can never be settled, and
# the run would iterate against it until it was stopped.
VAGUE = (
    "good", "nice", "realistic", "appropriate", "sufficient", "adequate",
    "proper", "properly", "well-proportioned", "well proportioned", "detailed",
    "interesting", "clean", "polished", "appealing", "aesthetic", "pleasing",
    "attractive", "convincing", "reasonable", "suitable", "acceptable",
    "believable", "natural-looking", "lifelike", "roughly right", "as needed",
    "if possible", "where appropriate", "visually",
)
_VAGUE = re.compile(r"\b(" + "|".join(re.escape(w) for w in VAGUE) + r")\b",
                    re.I)


# Colour words, for the one invention this pass cannot be trusted not to make.
#
# A prompt is advice and this failed in practice: a run was blocked for
# twenty-seven minutes on "the sofa is white" against a request that said
# `build a sofa`, because the design brief had chosen white and this pass read
# a brief as a specification. The brief is no longer given to it — see
# ``compose`` — and this is the belt to that pair of braces.
_COLOURS = (
    "white", "black", "grey", "gray", "red", "blue", "green", "yellow",
    "orange", "purple", "violet", "pink", "brown", "tan", "beige", "cream",
    "gold", "silver", "bronze", "copper", "chrome", "azure", "lime", "olive",
    "magenta", "cyan", "turquoise", "teal", "maroon", "navy", "nougat",
    "sand", "lavender", "coral", "amber", "ivory", "charcoal",
    "colour", "color", "colours", "colors", "palette", "coloured", "colored",
)
_COLOUR = re.compile(r"\b(" + "|".join(_COLOURS) + r")\b", re.I)


def mentions_colour(text):
    return bool(_COLOUR.search(str(text or "")))


# A requirement that *forbids* symmetry, which is an invention exactly like a
# colour nobody asked for and needs the same kind of guard.
#
# The prompt asks for a symmetry requirement only where the subject has an
# axis, and for silence everywhere else. Asked for silence about a rock, the
# model writes "the rock is not symmetric about any plane" instead — checkable,
# confidently wrong, and it refuses a rock that happened to come out tidy.
# Telling it not to in the prompt did not stop it: measured over the subjects
# in run_agent's checks, the negation came back for a rock and for a ruined
# wall on both wordings of the instruction.
#
# So it is a filter, for the same reason VAGUE and _COLOURS are: this is the
# half of the design that a prompt cannot be trusted with, and a requirement
# demanding irregularity is one no build can argue its way out of.
#
# Matched as a negation standing before a symmetry word, so the positive form
# that names its own exceptions — "symmetric apart from the door" — is left
# alone.
_NO_SYMMETRY = re.compile(
    r"\basymmetr\w*"
    r"|\b(not|no|never|avoids?|without|lacks?|free of)\b[^.]{0,40}?"
    r"\b(symmetr\w*|mirror\w*)",
    re.I,
)


def forbids_symmetry(text):
    """Whether a requirement demands the model be irregular."""
    return bool(_NO_SYMMETRY.search(str(text or "")))


# Language that genuinely needs eyes, and it is a much shorter list than it
# first looks.
#
# The first version of this demoted anything naming a feature — wall, roof,
# trunk, leg — on the reasoning that a bill of materials knows what a model is
# made of and nothing about where any of it went. That reasoning was right
# about a bill of materials and wrong about the file, and the difference is
# the thing the builder writes on its way past:
#
#     0 // thick trunk - four 2x2 bricks
#     1 70 0 -24 0 ... 3003.dat
#     1 70 0 -48 0 ... 3003.dat
#
# 116 of the 133 models on disk carry those comments, 1,004 lines of them, and
# `build_ops` emits one for every op that is given a `note`. So the file does
# say which parts are the trunk, and it says where each of them sits, to the
# LDU. "The trunk is four stacked round bricks" is answerable from it exactly;
# demoting it to a photograph was throwing away the better evidence.
#
# What a file still cannot settle is whether the assembled thing *reads* as the
# thing it is meant to be. No coordinate says a hull looks like a boat. Those
# are perceptual judgements and they are the only ones left here.
PERCEPTUAL = (
    "reads as", "read as", "reads like", "looks like", "look like",
    "looks as", "appears as", "appears to be", "recognisable", "recognizable",
    "silhouette", "proportion", "proportions", "proportioned",
    "resembles", "resemble", "convincing", "identifiable",
    "at a glance", "from across the room",
)
_PERCEPTUAL = re.compile(r"\b(" + "|".join(re.escape(w) for w in PERCEPTUAL)
                         + r")\b", re.I)


def answerable_from_source(text):
    """Whether the model file could honestly settle this requirement.

    True for almost everything, now that the file itself is shown: counts,
    colours, which parts belong to which named section, and where any of them
    sits. False only for claims about how the finished thing *looks*, which no
    coordinate answers and which are what the renders are actually for.
    """
    return not _PERCEPTUAL.search(str(text or ""))


def is_objective(text):
    """Whether a requirement can be answered without an opinion.

    A blunt instrument on purpose. It cannot tell a checkable statement from an
    unfalsifiable one in general — that is what the prompt is for — but it
    catches the specific way this fails in practice, which is a criterion
    carrying one of a small set of words that mean "to a standard nobody has
    stated".
    """
    text = " ".join(str(text or "").split())
    if len(text) < 8:
        return False
    return not _VAGUE.search(text)


# -- storage ----------------------------------------------------------------

def _path(project, projects_dir=None):
    base = Path(projects_dir) if projects_dir else (OUT_DIR / "projects")
    return base / project / STORE_NAME


def load(project, projects_dir=None):
    """Every object's requirements for a project: ``{name: record}``."""
    try:
        found = json.loads(_path(project, projects_dir).read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def save(project, name, record, projects_dir=None):
    """Write one object's requirements, keeping every other object's.

    Persistent because the list is what the build is being judged against, and
    a judgement that is rewritten each time it is applied is not one. A resumed
    run, a second turn on the same project and the trace all read the same list
    the first iteration was held to.
    """
    target = _path(project, projects_dir)
    found = load(project, projects_dir)
    found[name] = record
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(found, indent=2) + "\n", encoding="utf-8")
    except OSError:
        return None
    return record


def for_object(project, name, projects_dir=None):
    """One object's stored requirements, or None."""
    return load(project, projects_dir).get(name)


def items(record):
    """The requirement list out of a record, however it was stored."""
    if isinstance(record, dict):
        record = record.get("requirements")
    if not isinstance(record, list):
        return []
    return [r for r in record if isinstance(r, dict) and r.get("text")]


# -- writing them -----------------------------------------------------------

def _prompt(path):
    return path.read_text(encoding="utf-8").strip() if path.is_file() else ""


def _llm(model=None):
    from .llm import LLM

    return LLM(model=model or DEFAULT_MODEL,
               temperature=BLUEPRINT_TEMPERATURE, task="plan")


def _normalise(document, colour_asked=True):
    """The model's reply as a clean, numbered, objective, uninvented list.

    ``colour_asked`` is False when nothing in the request mentioned a colour and
    no reference picture was attached. Every colour requirement is then thrown
    out, because there is nowhere it could have come from except this pass
    deciding one — and a colour nobody asked for is a build refused for being
    the wrong shade of a thing that was never specified.
    """
    raw = document.get("requirements") if isinstance(document, dict) else document
    if not isinstance(raw, list):
        return [], [], []

    kept, dropped, invented = [], [], []
    for entry in raw:
        if isinstance(entry, str):
            entry = {"text": entry}
        if not isinstance(entry, dict):
            continue
        text = " ".join(str(entry.get("text") or "").split())
        if not text:
            continue
        if not is_objective(text):
            # Recorded rather than silently binned: a list that quietly lost
            # three of its criteria looks like a list that was never given them.
            dropped.append(text)
            continue
        if not colour_asked and mentions_colour(text):
            invented.append(text)
            continue
        # Nobody asks for a model to be asymmetric. See _NO_SYMMETRY.
        if forbids_symmetry(text):
            invented.append(text)
            continue
        kind = str(entry.get("check") or "visual").strip().lower()
        if kind in ("source", "parts", "inventory", "count", "counted"):
            # ...unless it is really a question about arrangement, which a
            # parts list cannot answer however it was labelled.
            kind = "source" if answerable_from_source(text) else "visual"
        kept.append({
            "id": f"r{len(kept) + 1}",
            "text": text,
            "check": kind if kind in ("visual", "measured", "source") else "visual",
            "why": " ".join(str(entry.get("why") or "").split()) or None,
        })
        if len(kept) >= MAX_REQUIREMENTS:
            break
    return kept, dropped, invented


def compose(subject, requirements=None, reference=None,
            size_hint=None, size_from=None, project=None, name=None, llm=None,
            should_stop=None, projects_dir=None, brief=None):
    """Write the acceptance criteria for one object. Returns the record, or None.

    Best effort in the same way every other pre-pass here is: a list that cannot
    be written leaves the run with none, and a run with no requirements falls
    back on the generic gate rather than refusing to build. It is a stricter
    ending, not a precondition for starting.
    """
    system = _prompt(REQUIREMENTS_PROMPT_FILE)
    if not system or not subject:
        return None
    if should_stop and should_stop():
        return None

    # Did anyone actually ask for a colour? A picture counts — the user chose to
    # attach it, so what it shows is specified. Nothing else does.
    colour_asked = bool(reference) or mentions_colour(f"{subject} {requirements or ''}")

    blocks = [f"The object to build: {subject}"]
    if not colour_asked:
        blocks.append(
            "**No colour was asked for.** Do not write a single requirement "
            "that mentions a colour or a palette — any colour is a correct "
            "answer here, and one that names a colour would refuse a finished "
            "model for being the wrong shade of something nobody specified.")
    # A size the user asked for is a requirement. A size this project chose
    # because the user said nothing is a starting point, and a gate that
    # refuses a model for missing it is a gate refusing its own invention —
    # which is the fault the colour rule above already names. scale.py sets
    # `size_from` to "default" in exactly that case.
    if not size_hint or size_from == "default":
        blocks.append(
            "**No size was asked for.** Do not write a requirement bounding "
            "the size in studs; whatever size it comes out is right. A size "
            "may be mentioned below as the size it is being built at — that is "
            "direction for the builder, not something to hold it to.")
    if requirements:
        blocks.append(
            f"What the user asked for, in their own words — every one of these "
            f"becomes a requirement:\n{requirements}")
    if size_hint:
        blocks.append(
            f"The size it is being built at: {size_hint}"
            + ("" if size_from == "default" else " — this one was asked for."))
    # The design brief is deliberately NOT passed. It is direction for the
    # builder, not a contract with the user: it picks a palette and a signature
    # detail so the model is not a grey box, and the builder is free to go
    # somewhere better. Shown to this pass it was read as a specification, and a
    # sofa was refused for being red when the brief had said white and the user
    # had said nothing at all. `brief` stays in the signature so existing
    # callers do not break, and is ignored.
    if reference:
        blocks.append(
            "A reference picture is the specification for this build. Its "
            "`build_priorities` are requirements almost as written:\n\n"
            + str(reference))
    blocks.append("Write the checklist. Answer with the JSON object only.")

    try:
        reply = (llm or _llm()).complete(
            [{"role": "system", "content": system},
             {"role": "user", "content": "\n\n".join(blocks)}],
            should_stop=should_stop)
    except Exception:
        return None
    if getattr(reply, "stopped", False):
        return None

    text = (getattr(reply, "content", "") or "").strip()
    if not text:
        return None

    from .blueprint import _extract_json

    document = _extract_json(text)
    kept, dropped, invented = _normalise(document, colour_asked=colour_asked)
    if not kept:
        return None

    record = ensure_connected({
        "subject": subject,
        "requirements": kept,
        "written_at": time.time(),
    })
    if dropped:
        record["rejected_as_unmeasurable"] = dropped
    if invented:
        record["rejected_as_not_asked_for"] = invented
    if project and name:
        save(project, name, record, projects_dir)
    return record


# -- the ones a picture must never be asked about ---------------------------
#
# Every requirement used to be answered the same way: hand the vision model the
# renders, hand it the geometry as a paragraph of text, and let it say
# true/false for all of them. For "the roof slopes" that is the only method
# there is. For "the model is one connected object" it is indefensible, and it
# failed exactly as you would expect — a build came back with the one-piece
# requirement answered YES while the checker had counted more than one
# sub-assembly, because the number was context for a language model rather than
# the answer itself.
#
# A picture cannot show this. Two clumps a stud apart look identical to two
# clumps touching from every angle a render is taken at, which is the whole
# reason the stud checker exists. So the geometry answers these, the vision
# model is never asked, and its opinion cannot overrule the count.
#
# `_SETTLERS` maps a pattern over the requirement's text to a function of the
# validation report. Each returns ``(met, evidence)`` or None for "this report
# cannot settle it after all", in which case it falls through to the vision
# model as before.

# The one requirement that is always present, whatever the composer wrote. A
# model in two pieces is not a model, and leaving it to a language model to
# remember to ask for it is how it goes missing.
CONNECTED_TEXT = ("The model is one connected object: every part is joined to "
                  "the rest through real stud connections, with nothing "
                  "standing separate.")


def _one_piece(report):
    connectivity = (report or {}).get("connectivity") or {}
    clumps = connectivity.get("subassemblies")
    apart = connectivity.get("objects_in_pieces") or []
    if clumps is None:
        return None
    if clumps <= 1 and not apart:
        return True, f"the stud checker found {clumps} connected piece"
    detail = f"the stud checker found {clumps} separate stud-connected pieces"
    loose = connectivity.get("loose_pieces") or []
    if loose:
        named = ", ".join(
            f"{row.get('parts')} part(s) at line {row.get('line')}"
            for row in loose[:3])
        detail += f" — the ones adrift from the main body are {named}"
    if apart:
        detail += (f"; {len(apart)} object(s) came apart into pieces with "
                   f"nothing joining them")
    return False, detail


def _nothing_floating(report):
    connectivity = (report or {}).get("connectivity") or {}
    floating = connectivity.get("floating")
    if floating is None:
        return None
    return (floating == 0,
            f"{floating} part(s) are held up by nothing — no path of "
            f"connections down to the ground" if floating else
            "every part has support down to the ground")


def _nothing_overlapping(report):
    collision = (report or {}).get("collision") or {}
    overlapping = collision.get("overlapping")
    if overlapping is None:
        return None
    return (overlapping == 0,
            f"{overlapping} pair(s) of parts share solid plastic"
            if overlapping else "no two parts share solid plastic")


def _on_the_grid(report):
    connectivity = (report or {}).get("connectivity") or {}
    misaligned = connectivity.get("misaligned")
    if misaligned is None:
        return None
    return (misaligned == 0,
            f"{misaligned} part(s) sit off the stud grid" if misaligned else
            "every part is on the stud grid")


# Order matters: the first pattern that matches settles the requirement. The
# patterns are deliberately narrow — a requirement this cannot recognise is
# answered the old way, which is the safe direction to be wrong in. "The tree
# has one trunk" must not read as a connectivity criterion.
_SETTLERS = (
    (re.compile(
        r"\b(one|single|a\s+single)\b[^.]{0,40}\b(connected|piece|object|"
        r"assembly|unit|whole)\b"
        r"|\bconnected\s+(object|whole|model|piece)\b"
        r"|\b(no|nothing|none of)\b[^.]{0,50}\b(separate|detached|adrift|"
        r"disconnected|standing apart|falls? apart|come apart)\b"
        r"|\bhold(s)?\s+together\b"
        r"|\bsurvives?\s+being\s+picked\s+up\b"
        r"|\bsub-?assembl", re.I), _one_piece),
    (re.compile(r"\bfloat\w*|\bheld up by nothing\b|\bunsupported\b"
                r"|\brests? on (the ground|something)\b", re.I), _nothing_floating),
    (re.compile(r"\boverlap\w*|\bshare\w*\s+(solid\s+)?plastic\b"
                r"|\bintersect\w*|\boccupy the same space\b", re.I),
     _nothing_overlapping),
    (re.compile(r"\b(on|off)\s+the\s+stud\s+grid\b|\bmisalign\w*"
                r"|\baligned to the (stud )?grid\b", re.I), _on_the_grid),
)


def settle(text, report):
    """Answer a requirement from the geometry alone, or None if it cannot."""
    if not isinstance(report, dict):
        return None
    for pattern, answer in _SETTLERS:
        if pattern.search(str(text or "")):
            return answer(report)
    return None


def ensure_connected(record):
    """Guarantee the one-piece requirement is on the list, machine-checked.

    Appended rather than hoped for. The composer is asked for it — item 7 of
    the prompt — and a language model asked for eight things reliably writes
    seven, so the one criterion that no picture can answer is the one most
    worth not leaving to chance. An existing requirement that already says it
    is re-tagged instead, so the list never carries the same demand twice.
    """
    if not isinstance(record, dict):
        return record
    rows = record.get("requirements")
    if not isinstance(rows, list):
        return record

    for row in rows:
        if _SETTLERS[0][0].search(str(row.get("text") or "")):
            row["check"] = "measured"
            row["settled_by"] = "geometry"
            return record

    rows.append({
        "id": f"r{len(rows) + 1}",
        "text": CONNECTED_TEXT,
        "check": "measured",
        "settled_by": "geometry",
        "why": "a model in several pieces is not one model — always checked",
        "auto": True,
    })
    return record


# -- checking them ----------------------------------------------------------

def _facts(report):
    """What the geometry checker established, for the `measured` criteria."""
    if not isinstance(report, dict):
        return "No measurements are available for this model."

    connectivity = report.get("connectivity") or {}
    collision = report.get("collision") or {}
    size = report.get("size") or {}
    studs = size.get("size_studs") or {}

    lines = [f"parts in the model: {report.get('parts')}"]
    if studs:
        lines.append(
            f"size: {studs.get('width')} studs wide, {studs.get('depth')} "
            f"studs deep, {studs.get('height_bricks')} bricks tall")
    lines += [
        f"separate stud-connected pieces: {connectivity.get('subassemblies')}",
        f"parts held up by nothing: {connectivity.get('floating')}",
        f"parts off the stud grid: {connectivity.get('misaligned')}",
        f"pairs sharing solid plastic: {collision.get('overlapping')}",
        f"objects that came apart: "
        f"{len(connectivity.get('objects_in_pieces') or [])}",
        f"overall verdict: {report.get('verdict')}",
    ]
    return "\n".join(f"- {line}" for line in lines)


# --------------------------------------------------------------------------
# The bill of materials, and the third way to answer a requirement
#
# Two things could settle a criterion before this: the geometry checker, which
# knows whether the model is one piece and on the grid, and a vision model
# looking at a contact sheet. Between them they leave a whole class of
# requirement unanswerable, and it is the most *countable* class there is —
# "four red 1x1 round plates", "no more than two colours", "a 2x4 brick at the
# base".
#
# A picture is the wrong instrument for those and it fails in a specific way:
# a vision model asked how many 1x1 plates are on a roof does not count them,
# it estimates, and it estimates the number the roof looks like it ought to
# have. It cannot see a part hidden behind another one at all. Meanwhile the
# answer is sitting in the .ldr file in plain text, exact, one line per part.
#
# So: code counts, and the model judges what the count means.
#
# That split is the whole of this. Counting is arithmetic and belongs in
# Python, where it is exact and free. Deciding that "green" covers colour 2 and
# colour 10 but not 288, or that "the trunk" is the brown column rather than
# the brown roof tile, is interpretation and belongs to a language model. Give
# the model the counts and it does the second job well; ask it for the first
# and it will guess.
#
# Where a requirement is countable *unambiguously* — an explicit number and a
# part id — `settle_source` answers it with no model at all. See `_COUNTABLE`.

# How many rows of each breakdown reach the checker. A bill of materials is
# long and the tail of it is never what a criterion turns on.
INVENTORY_LIMIT = 24


def inventory(path):
    """What is actually in the .ldr file: parts, colours, counts.

    Read off the source rather than the flattened build, and off type-1 lines
    that name a real catalogue part — the same rule ``style.measure`` uses, and
    for the same reason: LDraw primitives are the internals of a part
    definition and are nothing anybody chose to put in the model.

    Returns None when the file cannot be read, which callers must treat as "not
    established" rather than as an empty model. A criterion answered "there are
    zero red bricks" from a file that failed to open is exactly the confident
    wrong answer this whole module exists to prevent.
    """
    from . import catalog, palette

    try:
        text = Path(path).read_text(errors="ignore")
    except (OSError, TypeError):
        return None

    known = {(row.get("part_id") or "").strip().lower(): row
             for row in catalog.load_catalog()}
    by_part, by_colour, by_class = {}, {}, {}
    total = 0

    for line in text.splitlines():
        fields = line.split()
        # 1 <colour> x y z a b c d e f g h i <part>
        if len(fields) < 15 or fields[0] != "1":
            continue
        name = fields[14].strip().lower().replace("\\", "/").rsplit("/", 1)[-1]
        if name.endswith(".dat"):
            name = name[:-4]
        row = known.get(name)
        if row is None:
            continue
        try:
            code = int(fields[1])
        except ValueError:
            continue
        total += 1
        by_part[name] = by_part.get(name, 0) + 1
        by_colour[code] = by_colour.get(code, 0) + 1
        sized = catalog.size_class(row)
        if sized:
            by_class[sized] = by_class.get(sized, 0) + 1

    if not total:
        return {"parts": 0, "by_part": [], "by_colour": [], "by_size_class": {},
                "distinct_shapes": 0, "distinct_colours": 0}

    return {
        "parts": total,
        "distinct_shapes": len(by_part),
        "distinct_colours": len(by_colour),
        "by_part": [
            {"part_id": pid, "count": n,
             "description": " ".join((known[pid].get("description") or "").split()),
             "size_class": catalog.size_class(known[pid])}
            for pid, n in sorted(by_part.items(), key=lambda kv: -kv[1])],
        "by_colour": [
            {"code": code, "count": n, "name": palette.colour_name(code)}
            for code, n in sorted(by_colour.items(), key=lambda kv: -kv[1])],
        "by_size_class": by_class,
    }


# How much of the model file goes to the checker verbatim. Measured over the
# 133 models on disk: median 40 lines, 90th percentile 124, and only three over
# 400. So the whole file fits, and capping is for the pathological case rather
# than the normal one.
SOURCE_LINE_LIMIT = 500


def sections(path):
    """The model's own account of itself: what each `0 //` comment covers.

    LDraw files carry comments, and this project's builder writes them on every
    op — ``build_ops`` turns a step's ``note`` into ``0 // front wall`` above
    the parts it places. Across the 133 models on disk, 116 carry them: 1,004
    lines saying *trunk lower*, *canopy level 1 mound*, *root flare*, *thick
    trunk - four 2x2 bricks*.

    That is the piece a bill of materials was missing. A parts list knows the
    model contains eighteen red parts and cannot say which are the walls; the
    file says which are the walls, because the builder wrote it down on the way
    past. Grouping the parts under the comment that introduces them turns a bag
    of parts back into a model with named sections.

    Returns ``[{label, line, parts: [{part_id, colour, count}], total}]``.
    Empty when the file has no comments, which is the case this cannot help
    with and must not pretend to.
    """
    from . import catalog, palette

    try:
        text = Path(path).read_text(errors="ignore")
    except (OSError, TypeError):
        return []

    known = {(row.get("part_id") or "").strip().lower(): row
             for row in catalog.load_catalog()}
    out, current = [], None

    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("0 //"):
            label = stripped[4:].strip()
            if label:
                current = {"label": label, "line": number, "counts": {},
                           "colours": set()}
                out.append(current)
            continue
        fields = stripped.split()
        if len(fields) < 15 or fields[0] != "1" or current is None:
            continue
        name = fields[14].strip().lower().replace("\\", "/").rsplit("/", 1)[-1]
        if name.endswith(".dat"):
            name = name[:-4]
        if name not in known:
            continue
        try:
            colour = int(fields[1])
        except ValueError:
            continue
        current["counts"][(name, colour)] = \
            current["counts"].get((name, colour), 0) + 1
        current["colours"].add(colour)

    rows = []
    for entry in out:
        if not entry["counts"]:
            continue
        parts = [{"part_id": pid, "colour": colour, "count": n,
                  "colour_name": palette.colour_name(colour),
                  "description": " ".join(
                      (known[pid].get("description") or "").split())}
                 for (pid, colour), n in
                 sorted(entry["counts"].items(), key=lambda kv: -kv[1])]
        rows.append({"label": entry["label"], "line": entry["line"],
                     "parts": parts,
                     "total": sum(entry["counts"].values())})
    return rows


def sections_text(rows):
    """The named sections as the checker reads them."""
    if not rows:
        return ("The file has no section comments, so which parts make up "
                "which feature is not written down in it.")
    out = []
    for row in rows:
        out.append(f"- \"{row['label']}\" (line {row['line']}, "
                   f"{row['total']} part(s)):")
        for part in row["parts"]:
            name = (f" ({part['colour_name']})" if part.get("colour_name")
                    else "")
            out.append(f"    {part['count']} x {part['part_id']} in colour "
                       f"{part['colour']}{name} — {part['description']}")
    return "\n".join(out)


def source_text(path, limit=SOURCE_LINE_LIMIT):
    """The model file itself, numbered, for the checker to read directly.

    The digests above are convenient and they are also lossy: they drop every
    coordinate. A requirement about where something sits — a tile on each
    corner, the roof above the walls, one section centred on another — is
    answerable from the file and from nothing else, and only if the file is
    actually shown.
    """
    try:
        lines = Path(path).read_text(errors="ignore").splitlines()
    except (OSError, TypeError):
        return None
    shown = lines[:limit]
    out = [f"{n:>4} | {line}" for n, line in enumerate(shown, start=1)]
    if len(lines) > limit:
        out.append(f"     | ... {len(lines) - limit} more line(s) not shown")
    return "\n".join(out)


def inventory_text(stock, limit=INVENTORY_LIMIT):
    """The bill of materials as the checker reads it."""
    if not stock:
        return "The model file could not be read, so nothing about its contents is established."
    if not stock["parts"]:
        return "The model file contains no parts."

    lines = [f"total parts: {stock['parts']}",
             f"distinct shapes: {stock['distinct_shapes']}",
             f"distinct colours: {stock['distinct_colours']}"]
    if stock.get("by_size_class"):
        lines.append("by size: " + ", ".join(
            f"{k} {v}" for k, v in sorted(stock["by_size_class"].items())))

    out = ["- " + line for line in lines]
    out.append("")
    out.append("Every colour in the model, counted exactly:")
    for row in stock["by_colour"][:limit]:
        name = f" ({row['name']})" if row.get("name") else ""
        out.append(f"  - colour {row['code']}{name}: {row['count']} part(s)")
    if len(stock["by_colour"]) > limit:
        out.append(f"  - ... and {len(stock['by_colour']) - limit} more colours")

    out.append("")
    out.append("Every part in the model, counted exactly:")
    for row in stock["by_part"][:limit]:
        out.append(f"  - {row['count']} x {row['part_id']} — {row['description']}")
    if len(stock["by_part"]) > limit:
        out.append(f"  - ... and {len(stock['by_part']) - limit} more shapes")
    return "\n".join(out)


# Requirements this can answer with no model at all. Deliberately narrow, on
# the same reasoning as `_SETTLERS`: a criterion this does not recognise falls
# through to the model, which is the safe direction to be wrong in. "The tree
# has one trunk" contains a number and a noun and means nothing countable.
#
# So a pattern only fires where the requirement names something the file can be
# asked about *exactly* — a catalogue part id, the total part count, the number
# of colours — together with an explicit comparison. Everything vaguer is
# interpretation, and interpretation is the model's half of the job.
_AT_LEAST = r"(?:at\s+least|no\s+fewer\s+than|minimum\s+of|\d+\s*\+|or\s+more)"
_AT_MOST = r"(?:at\s+most|no\s+more\s+than|fewer\s+than|under|maximum\s+of|or\s+fewer)"
_EXACTLY = r"(?:exactly|precisely|a\s+total\s+of)"

_PART_ID = re.compile(r"\b(\d{3,5}[a-z]{0,2})\b")
_COUNT_OF_PART = re.compile(
    rf"\b(?:({_EXACTLY})|({_AT_LEAST})|({_AT_MOST}))\s+(\d+)\b", re.I)
_TOTAL_PARTS = re.compile(
    rf"\b(?:({_EXACTLY})|({_AT_LEAST})|({_AT_MOST}))\s+(\d+)\s+"
    rf"(?:parts?|pieces?|bricks?|elements?)\b", re.I)
_TOTAL_COLOURS = re.compile(
    rf"\b(?:({_EXACTLY})|({_AT_LEAST})|({_AT_MOST}))\s+(\d+)\s+colou?rs?\b", re.I)


def _compare(kind, wanted, actual):
    """``(met, evidence)`` for one counted comparison."""
    if kind == "at_least":
        return actual >= wanted, f"counted {actual}, needs at least {wanted}"
    if kind == "at_most":
        return actual <= wanted, f"counted {actual}, allows at most {wanted}"
    return actual == wanted, f"counted {actual}, needs exactly {wanted}"


def _which(match):
    return "exactly" if match.group(1) else (
        "at_least" if match.group(2) else "at_most")


def settle_source(text, stock):
    """Answer a requirement from the bill of materials, or None if it cannot.

    Exact and free: no model is asked, because there is nothing here to
    interpret. Only fires on the three things the file answers without any
    reading of intent at all.
    """
    if not stock or not isinstance(stock, dict) or stock.get("parts") is None:
        return None
    said = str(text or "")

    match = _TOTAL_COLOURS.search(said)
    if match:
        met, why = _compare(_which(match), int(match.group(4)),
                            stock["distinct_colours"])
        return met, f"{why} colour(s) in the file"

    match = _TOTAL_PARTS.search(said)
    if match:
        met, why = _compare(_which(match), int(match.group(4)), stock["parts"])
        return met, f"{why} part(s) in the file"

    # A named part id, with an explicit count. Both halves are required: the
    # id alone ("uses 3001 somewhere") is a claim about presence that reads the
    # same as a claim about position, and this cannot tell those apart.
    ids = [pid for pid in _PART_ID.findall(said.lower())
           if any(row["part_id"] == pid for row in stock["by_part"])
           or _known_part(pid)]
    match = _COUNT_OF_PART.search(said)
    if ids and match:
        pid = ids[0]
        actual = next((row["count"] for row in stock["by_part"]
                       if row["part_id"] == pid), 0)
        met, why = _compare(_which(match), int(match.group(4)), actual)
        return met, f"{why} of part {pid} in the file"
    return None


def _known_part(part_id):
    from . import catalog

    return catalog.get_part(part_id) is not None


def _parse(text, wanted):
    """A checker reply as ``{id: (met, evidence)}``, missing ids counting false."""
    from .blueprint import _extract_json

    document = _extract_json(text)
    rows = document.get("results") if isinstance(document, dict) else document
    found = {}
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            key = str(row.get("id") or "").strip()
            if not key:
                continue
            met = row.get("met")
            if isinstance(met, str):
                met = met.strip().lower() in ("true", "yes", "met", "pass")
            found[key] = (bool(met),
                          " ".join(str(row.get("evidence") or "").split()))
    # An id the checker never mentioned is not established, and the whole point
    # of this gate is that nothing passes on silence.
    return {key: found.get(key, (False, "the check did not answer this one"))
            for key in wanted}


def _check_source(wanted, stock, subject=None, path=None, llm=None,
                  should_stop=None):
    """Answer requirements from the bill of materials. Text only, no picture.

    The cheap half of the gate: one text call against counts that are already
    exact, where the vision path costs a six-view render and a multimodal
    model. Returns ``({id: (met, evidence)}, summary)``.
    """
    system = _prompt(REQUIREMENTS_SOURCE_PROMPT_FILE)
    blocks = [
        f"The model is meant to be: {subject}." if subject else
        "Check this model against its requirements.",
        "What the model file contains, counted exactly:\n" + inventory_text(stock),
        # The builder's own labels for its own work. This is the half that
        # turns a bag of parts back into a model: the file says which parts are
        # the trunk because whoever built it wrote `0 // trunk` above them.
        "How the file labels its own sections:\n" + sections_text(sections(path)),
        # ...and the file itself, because every coordinate is in it and the
        # digests above drop them all.
        "The model file, in full:\n" + (source_text(path) or "(unreadable)"),
        "The requirements, in order:\n" + "\n".join(
            f"{r['id']}. {r['text']}" for r in wanted),
        "Answer every one of them. JSON object only.",
    ]
    reply = (llm or _llm()).complete(
        [{"role": "system", "content": system},
         {"role": "user", "content": "\n\n".join(blocks)}],
        should_stop=should_stop)
    if getattr(reply, "stopped", False):
        raise RuntimeError("stopped while checking the parts list")
    text = (getattr(reply, "content", "") or "").strip()
    answers = _parse(text, [r["id"] for r in wanted])

    from .blueprint import _extract_json

    document = _extract_json(text)
    summary = (document.get("summary")
               if isinstance(document, dict) else None)
    return answers, summary


def check(record, sheet, report=None, subject=None, model=None, client=None,
          path=None, llm=None):
    """Answer every requirement, by whichever of three routes can settle it.

    Returns ``{"met": [...], "unmet": [...], "passed": bool, ...}``. Raises
    ``render.NotAvailable`` when there is no vision model to ask, which the
    caller has to treat as "not established" rather than as a pass.

    The three routes, in the order they are tried — cheapest and most certain
    first, because every requirement one of them settles is a requirement the
    next one does not have to guess at:

    1. **the geometry checker** — connectivity, the stud grid, overlaps.
       Free and exact. See ``settle``.
    2. **the parts list** — counts of parts and colours read straight out of
       the ``.ldr``. Free and exact where the requirement names a part id or a
       total; see ``settle_source``. Where it names a *colour word* or a family
       of shapes, the counting is still exact but deciding what the words cover
       is not, so it goes to a text model with the counts in front of it —
       ``_check_source``. That is the "code counts, model judges" split, and it
       is the whole reason this route exists: a vision model asked how many 1x1
       plates are on a roof estimates, and cannot see the ones underneath.
    3. **the pictures** — everything about shape, position and proportion,
       which is what a render is actually evidence of.

    ``path`` is the ``.ldr`` to read the parts list from; it falls back to the
    file the geometry report was run on.
    """
    from . import render

    wanted = items(record)
    if not wanted:
        return {"passed": False, "met": [], "unmet": [],
                "note": "there are no requirements to check"}

    source_path = path or (report or {}).get("file")
    stock = inventory(source_path)

    # Settled from the geometry and the parts list first, and taken off the
    # list the vision model is shown. Not merely overridden afterwards: a
    # criterion the picture cannot answer is one it should never have been
    # asked, and every one left in the prompt costs the checker attention on
    # the ones that need eyes.
    settled, how = {}, {}
    for requirement in wanted:
        answer = settle(requirement["text"], report)
        if answer is not None:
            settled[requirement["id"]] = answer
            how[requirement["id"]] = "geometry"
            continue
        answer = settle_source(requirement["text"], stock)
        if answer is not None:
            settled[requirement["id"]] = answer
            how[requirement["id"]] = "parts list"

    remaining = [r for r in wanted if r["id"] not in settled]
    # Only what was *declared* a source criterion goes the text route. A
    # requirement about shape would be answered "the right parts are present"
    # from a parts list, which is the one way this mode is worse than nothing.
    from_source = [r for r in remaining if r.get("check") == "source"]
    looked_at = [r for r in remaining if r.get("check") != "source"]

    source_answers, source_summary = {}, None
    if from_source and stock:
        try:
            source_answers, source_summary = _check_source(
                from_source, stock, subject, path=source_path, llm=llm)
            for requirement in from_source:
                how[requirement["id"]] = "parts list"
        except Exception:
            # Best effort, like every other reading pass here. A checker that
            # cannot be reached leaves these unestablished, which `_parse`
            # already renders as false rather than as a pass.
            source_answers = {r["id"]: (False,
                                        "the parts-list check could not be reached")
                              for r in from_source}
    elif from_source:
        source_answers = {r["id"]: (False, "the model file could not be read")
                          for r in from_source}

    def assemble(answers, summary, vision_model=None, note=None):
        """One row per requirement, each saying which route settled it."""
        met, unmet = [], []
        for requirement in wanted:
            key = requirement["id"]
            if key in settled:
                ok, evidence = settled[key]
            elif key in source_answers:
                ok, evidence = source_answers[key]
            else:
                ok, evidence = answers.get(
                    key, (False, "the check did not answer this one"))
                how[key] = "pictures"
            row = {**requirement, "met": ok, "evidence": evidence,
                   "settled_by": how.get(key, "pictures")}
            (met if ok else unmet).append(row)
        out = {"passed": not unmet, "checked": len(wanted), "met": met,
               "unmet": unmet, "summary": summary,
               "settled_by": {k: how.get(r["id"], "pictures")
                              for k, r in ((r["id"], r) for r in wanted)}}
        if vision_model:
            out["vision_model"] = vision_model
        if note:
            out["note"] = note
        return out

    if not looked_at:
        # Nothing left that needs eyes, so no render is shown and no vision
        # call is spent. This is the case the parts-list route makes common:
        # a checklist of counts and connectivity answers itself.
        routes = sorted(set(how.values())) or ["geometry"]
        return assemble({}, source_summary or
                        f"answered from the {' and the '.join(routes)}; "
                        f"no picture was needed")

    asked = [
        f"The model is meant to be: {subject}." if subject else
        "Check this model against its requirements.",
        "What the geometry checker measured:\n" + _facts(report),
        # The parts list goes to the vision model too. It is not the evidence
        # for a visual criterion, but it is the thing that stops a picture
        # being over-read: a checker that can see there are two brown round
        # bricks does not have to decide from a render whether the trunk looks
        # like four.
        "What the model file contains, counted exactly:\n" + inventory_text(stock),
        "How the file labels its own sections:\n"
        + sections_text(sections(source_path)),
        "The requirements, in order:\n" + "\n".join(
            f"{r['id']}. [{r['check']}] {r['text']}" for r in looked_at),
        "Answer every one of them.",
    ]

    body = [{"type": "text", "text": "\n\n".join(asked)},
            {"type": "image_url",
             "image_url": {"url": render._data_uri(Path(sheet))}}]

    system = _prompt(REQUIREMENTS_CHECK_PROMPT_FILE)
    reply = render._vision(body, system, model, client,
                           expected=("results", "met", "summary"))

    answers = _parse(reply.get("text") if reply.get("unstructured")
                     else json.dumps(reply), [r["id"] for r in looked_at])

    return assemble(
        answers,
        reply.get("summary") if isinstance(reply, dict) else None,
        vision_model=(reply.get("vision_model")
                      if isinstance(reply, dict) else None))


def as_text(record):
    """The checklist as markdown, for the builder's context."""
    wanted = items(record)
    if not wanted:
        return ""
    lines = ["These are the requirements this build is judged against. The run "
             "does not end until every one of them is true — you do not decide "
             "that it is finished, they do.\n"]
    for requirement in wanted:
        lines.append(f"- **{requirement['id']}** [{requirement['check']}] "
                     f"{requirement['text']}")
    return "\n".join(lines)


def outstanding(result, limit=12):
    """What is still not true, phrased as the work left to do."""
    unmet = (result or {}).get("unmet") or []
    if not unmet:
        return ""
    lines = [f"{len(unmet)} requirement(s) are still not met. This is what the "
             f"run is waiting on — fix these and nothing else:\n"]
    for row in unmet[:limit]:
        lines.append(f"- **{row['id']}** {row['text']}")
        if row.get("evidence"):
            lines.append(f"  what was seen: {row['evidence']}")
    if len(unmet) > limit:
        lines.append(f"- …and {len(unmet) - limit} more")
    return "\n".join(lines)
