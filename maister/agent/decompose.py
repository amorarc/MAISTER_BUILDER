"""Splitting a petition into atomic subconstructions.

"Build a house with a tree and a car" is three builds, not one. Handed to a
single builder as one sentence it becomes one flat pile of bricks with a
house-ish region and a car-ish region and an overlap between them; handed to
three builders as three subjects it becomes three models that each work, and
an assembly step that places them.

This is the first thing that happens to a request, before any planning. It is
one cheap LLM call with no tools, and it is allowed to fail: a decomposition
that cannot be obtained falls back to treating the whole request as a single
subconstruction, which is what the agent did before this existed.
"""

import os
import re

from . import scale
from .config import DECOMPOSE_PROMPT_FILE, DEFAULT_MODEL

# A scene of more than this is not a scene, it is a diorama the run cannot
# finish. Extra objects are folded into the last one as requirements so nothing
# the user asked for is silently dropped.
#
# It was 6, and 6 is a good number for a scene: six objects, each with a brief,
# a checklist, a build loop and six renders, is already a long run. It is a bad
# number for the case that object *count* does not describe — a word. "MAISTER
# in big letters" is seven objects and every one of them is a letter four
# bricks wide; the seventh was folded into the sixth as "also asked for, if it
# fits: r", and the model came back spelling MAISTE.
#
# So the cap is set by the longest thing anyone reasonably asks for rather than
# by what a scene of houses costs. Twelve covers a word; a diorama of twelve
# buildings is still refused, and it is still refused by the wrong measure —
# the honest bound is total work rather than object count, and this is not it.
# Objects are built PARALLEL_SUBBUILDS at a time (see orchestrator.py), so
# twelve letters is four waves rather than twelve.
MAX_SUBCONSTRUCTIONS = max(1, int(os.environ.get("LDRAW_MAX_OBJECTS", "12")))
MAX_MODEL_LINES = 200


class Subconstruction:
    """One atomic object to build, and how the run is getting on with it."""

    __slots__ = ("name", "subject", "requirements", "quantity", "size_hint",
                 "extends", "status", "path", "validation", "critique", "note",
                 "unbuildable", "size_band", "max_pieces", "size_from")

    def __init__(self, name, subject, requirements="", quantity=1,
                 size_hint=None, extends=None, size_band=None,
                 max_pieces=None, size_from=None):
        self.name = name
        self.subject = subject
        self.requirements = requirements or ""
        self.quantity = max(1, int(quantity or 1))
        self.size_hint = size_hint
        self.extends = extends
        # Which band this is being built at, and the piece budget that goes
        # with it. See scale.py — the band is decided from the request, not
        # here, because "how big" is a different question from "how many
        # objects" and this pass only answers the second.
        self.size_band = size_band
        self.max_pieces = max_pieces
        # "asked" when the request stated a size, "default" when scale.py
        # supplied one. The requirements gate needs the difference: it may
        # hold a build to a size the user asked for and may not hold it to one
        # this project chose.
        self.size_from = size_from
        # pending -> building -> done | failed
        self.status = "pending"
        self.path = None
        self.validation = None
        self.critique = None
        self.note = None
        # Kept and shown, but not buildable: parts off the stud grid, or
        # validation failing at the moment the run ran out of steps. "done"
        # means the harness stopped working on it, never that it is sound.
        self.unbuildable = False

    def as_dict(self):
        return {
            "name": self.name,
            "subject": self.subject,
            "requirements": self.requirements,
            "quantity": self.quantity,
            "size_hint": self.size_hint,
            "size_band": self.size_band,
            "max_pieces": self.max_pieces,
            "size_from": self.size_from,
            "extends": self.extends,
            "status": self.status,
            "path": self.path,
            "note": self.note,
            "unbuildable": self.unbuildable,
        }

    def __repr__(self):
        return f"<Subconstruction {self.name} {self.status}>"


def _prompt():
    if not DECOMPOSE_PROMPT_FILE.is_file():
        return ""
    return DECOMPOSE_PROMPT_FILE.read_text(encoding="utf-8").strip()


def _slug(name, taken):
    """A filename-safe, unique name for a subconstruction."""
    base = re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-")[:32]
    base = base or "part"
    candidate, n = base, 2
    while candidate in taken:
        candidate = f"{base}-{n}"
        n += 1
    taken.add(candidate)
    return candidate


def _reference_block(description):
    """What the picture says about how many things there are, for the split.

    Only the part of the description that bears on splitting: what the objects
    are, and what they are doing with each other. The rest of it — silhouettes,
    part-by-part sizes, colours — is for the builder that gets each object, and
    putting it here would bury the one question this call has to answer.

    The two kinds are listed apart rather than tagged. `role: scenery` in the
    middle of a line is a word among other words, and a split that put the
    grass beside the tree as its own object came back from exactly that; two
    headings say the same thing in a way that cannot be skimmed past.

    Scenery is named and then explicitly excluded. It used to be folded into the
    subject's requirements, and "build this chair" came back as a chair, a rug,
    a wooden floor, a lamp and a tray — because a photograph of a chair is also
    a photograph of the room it is in, and every one of those was in the
    picture. Nine 6x6 plates went into the floor, and the floor was laid on a
    different stud lattice from the chair.

    A photograph always has a floor, a wall and a surface in it. None of them is
    what was asked for. Scenery from a *picture* is the setting it was taken in;
    scenery the user asked for in words arrives through the request instead, and
    that path is untouched.
    """
    if not isinstance(description, dict):
        return None

    objects = [o for o in (description.get("objects") or [])
               if isinstance(o, dict) and o.get("name")]
    standing = [o for o in objects
                if "scener" not in str(o.get("role") or "").lower()]
    scenery = [o for o in objects if o not in standing]

    # With one object standing there is nothing for it to stand *with*, and
    # `with_others` then describes the scenery — "the chair is standing on a
    # rug" — which is the sentence that puts a rug in the requirements.
    keys = ("what", "size", "with_others") if len(standing) > 1 else ("what", "size")

    def describe(entry):
        bits = [f"- {entry['name']}"]
        for key in keys:
            if entry.get(key):
                bits.append(f"{key}: {entry[key]}")
        return "  ".join(bits)

    lines = []
    subject = description.get("subject") or description.get("one_line")
    if subject:
        lines.append(f"The picture shows: {subject}")

    if standing:
        lines.append(f"\nIt holds {len(standing)} thing"
                     f"{'' if len(standing) == 1 else 's'} that "
                     f"{'stands' if len(standing) == 1 else 'stand'} apart. "
                     f"{'This is the subconstruction' if len(standing) == 1 else 'These are the subconstructions'}:")
        lines += [describe(o) for o in standing]
    if scenery:
        lines.append(
            f"\nIt also holds {len(scenery)} piece"
            f"{'' if len(scenery) == 1 else 's'} of **setting**. "
            f"{'This is' if len(scenery) == 1 else 'These are'} the room the "
            f"photograph was taken in, not the thing that was asked for: **do "
            f"not build "
            f"{'it' if len(scenery) == 1 else 'them'}, do not give "
            f"{'it' if len(scenery) == 1 else 'them'} a subconstruction, and do "
            f"not put {'it' if len(scenery) == 1 else 'them'} in anything's "
            f"requirements.** "
            f"{'It is' if len(scenery) == 1 else 'They are'} listed only so you "
            f"know {'it has' if len(scenery) == 1 else 'they have'} been "
            f"considered and left out:")
        lines += [f"- {o['name']}" for o in scenery]
    # Only when there is more than one thing to arrange. With a single subject
    # this field is a tour of the room — "the rug is on a wooden floor, behind
    # the chair is a lamp" — and it is the sentence that talks a split into
    # building the room.
    if len(standing) > 1 and description.get("arrangement"):
        lines.append(f"\nHow they stand together: {description['arrangement']}")

    if not lines:
        return None
    return ("A reference picture is attached and has been read. It is the "
            "specification for what the objects *are* — their shape, their "
            "colours, their proportions — and it outranks the wording of the "
            "request wherever the two describe the same thing differently.\n\n"
            "What it is not is a list of things to build. A photograph comes "
            "with a floor, a wall and whatever else was in the room, and none "
            "of that was asked for. Build the objects; leave the "
            "setting.\n\n" + "\n".join(lines))


def decompose(message, current_model=None, reference=None, workbench=None,
              llm=None, should_stop=None):
    """Atomic subconstructions for ``message``.

    Returns ``(subconstructions, meta)``. ``meta`` carries the one-line summary,
    whether this is a scene, and how the split was arrived at — an LLM call, or
    the fallback. Never raises.
    """
    system = _prompt()
    if not system:
        return _fallback(message, "the decomposer prompt file is missing")

    if should_stop and should_stop():
        return _fallback(message, "stopped before the request was split")

    blocks = [f"Request: {message}"]
    seen = _reference_block(reference)
    if seen:
        blocks.append(seen)
    # What is on the workbench, as it was read at the start of the run: not the
    # source, but what the model actually *is* — see survey.py. It comes before
    # the source because it is what the source means, and because the split
    # turns on it: a request to "add a helmet" against a finished figure is one
    # change to one object, and the same words against an empty file are a
    # whole build.
    if workbench:
        blocks.append(workbench)
    if current_model and current_model.strip():
        lines = current_model.strip().splitlines()[:MAX_MODEL_LINES]
        blocks.append(
            "A model already exists. This request is probably a change to it. "
            "Its current source:\n```\n" + "\n".join(lines) + "\n```")
    elif not workbench:
        blocks.append("Nothing has been built yet.")
    blocks.append("Split it. Answer with the JSON object only.")

    try:
        reply = (llm or _llm()).complete(
            [{"role": "system", "content": system},
             {"role": "user", "content": "\n\n".join(blocks)}],
            should_stop=should_stop,
        )
    except Exception as exc:
        return _fallback(message, f"the decomposer could not be reached ({exc})")

    if getattr(reply, "stopped", False):
        return _fallback(message, "stopped while the request was being split")

    text = (getattr(reply, "content", "") or "").strip()
    if not text:
        return _fallback(message, "the decomposer returned nothing")

    from .blueprint import _extract_json

    document = _extract_json(text)
    if not isinstance(document, dict):
        return _fallback(message, "the decomposer did not return JSON")

    return _build(document, message)


def _apply_scale(out, message):
    """Give every subconstruction a size band, and default the size it builds at.

    The rule is scale.py's: a size that was asked for is obeyed, a size nobody
    asked for is small. What this adds is the one case where obeying it
    literally would be wrong.

    **A scene keeps its own proportions.** The decomposer sizes the objects in
    a scene against each other — a car beside a house is smaller than the house
    — and that is a real decision about the scene rather than an invention
    about its size. Overwriting every hint with one default would flatten a
    street to a row of equal boxes. So for a scene the hints stay and only the
    budget is set; for a single object, which is the case the default was
    written for, the hint is replaced.
    """
    band, hint, budget, why = scale.resolve(message)
    scene = len(out) > 1
    for sub in out:
        sub.size_band = band
        sub.size_from = why
        if why == "asked" and sub.size_hint:
            continue          # the request said a size and the split kept it
        if scene and sub.size_hint:
            continue          # relative sizing across a scene is a decision
        sub.size_hint = hint
        # The budget travels with the hint and only with it. A 16 x 16 house
        # kept from the decomposer's own scene proportions, handed the small
        # band's 45-piece budget, is two instructions that contradict each
        # other — and the builder would meet whichever it was reminded of last.
        # Where the hint is ours, the budget is too; where the hint is not, the
        # hint governs alone, as it did before any of this existed.
        sub.max_pieces = budget
    return band, why


def _build(document, message):
    """Turn the decomposer's JSON into Subconstruction objects."""
    rows = document.get("subconstructions")
    if not isinstance(rows, list) or not rows:
        return _fallback(message, "the decomposer returned no subconstructions")

    taken = set()
    out = []
    for row in rows[:MAX_SUBCONSTRUCTIONS]:
        if isinstance(row, str):
            row = {"name": row, "subject": row}
        if not isinstance(row, dict):
            continue
        subject = str(row.get("subject") or row.get("name") or "").strip()
        if not subject:
            continue
        out.append(Subconstruction(
            name=_slug(row.get("name") or subject, taken),
            subject=subject,
            requirements=str(row.get("requirements") or "").strip(),
            quantity=row.get("quantity", 1),
            size_hint=row.get("size_hint"),
            extends=row.get("extends") or None,
        ))

    if not out:
        return _fallback(message, "every subconstruction was unusable")

    out, folded = fold_attached(out)

    # Anything past the cap is not dropped: it is handed to the last object as
    # extra requirements, so a request for seven things comes back short rather
    # than missing three of them without a word.
    overflow = rows[MAX_SUBCONSTRUCTIONS:]
    if overflow:
        names = ", ".join(str(r.get("name") or r.get("subject") or r)
                          for r in overflow if r)
        out[-1].requirements = (out[-1].requirements +
                                f" Also asked for, if it fits: {names}.").strip()

    band, why = _apply_scale(out, message)

    meta = {
        "summary": str(document.get("summary") or message).strip(),
        "scene": bool(document.get("scene", len(out) > 1)) and len(out) > 1,
        "source": "decomposer",
        "count": len(out),
        "size_band": band,
        "size_from": why,
        "max_pieces": out[0].max_pieces if out else None,
    }
    notes = []
    if overflow:
        notes.append(f"the request named more than {MAX_SUBCONSTRUCTIONS} "
                     f"objects; the extras were folded into '{out[-1].name}'")
    if folded:
        meta["folded"] = folded
        notes.append("built into the object each belongs to rather than "
                     "separately: " + ", ".join(f"{n} -> {p}" for n, p in folded))
    if notes:
        meta["note"] = "; ".join(notes)
    return out, meta


def fold_attached(subs):
    """Merge every subconstruction that is detail of another into its parent.

    The decomposer is told that grass, stones, a chimney, a door are detail and
    belong in the requirements of whatever they sit on — but it will sometimes
    list them as objects anyway, and a list is not the place to argue about it.
    Anything whose ``extends`` names a sibling is folded into that sibling here,
    so it can never become a separate file laid out a few studs away from the
    thing it is supposed to be part of.

    Returns ``(remaining, [(folded_name, parent_name)])``.
    """
    by_name = {s.name: s for s in subs}
    kept, folded = [], []

    for sub in subs:
        parent = by_name.get((sub.extends or "").strip().lower())
        # A parent that is itself being folded would orphan this one, and a
        # subconstruction cannot be detail of itself.
        if parent is None or parent is sub or parent.extends:
            kept.append(sub)
            continue
        detail = sub.subject
        if sub.requirements:
            detail = f"{detail} ({sub.requirements})"
        if sub.quantity > 1:
            detail = f"{sub.quantity}x {detail}"
        parent.requirements = (
            f"{parent.requirements} Built as part of this, not beside it: "
            f"{detail}.").strip()
        folded.append((sub.name, parent.name))

    return (kept or subs), folded


def _fallback(message, reason):
    """One subconstruction covering the whole request.

    The build must go on. A run that refuses to start because the splitter had
    a bad minute is worse than a run that builds the request as one object,
    which is exactly what happened before this step existed.
    """
    text = " ".join((message or "a model").split())
    out = [Subconstruction(name="model", subject=text[:400])]
    # The size is decided from the request, not from the split, so it survives
    # the split failing — this path is exactly where a build with no size at
    # all used to start.
    band, why = _apply_scale(out, message)
    return (out,
            {"summary": text[:200], "scene": False, "source": "fallback",
             "count": 1, "note": reason, "size_band": band, "size_from": why,
             "max_pieces": out[0].max_pieces})


def _llm():
    """A toolless, low-temperature model for the split.

    Same reasoning as the blueprint planner: this returns a short structured
    document, and a model that deliberates first spends its budget on the
    thinking and runs out mid-JSON.
    """
    from .config import BLUEPRINT_TEMPERATURE
    from .llm import LLM

    return LLM(model=DEFAULT_MODEL, temperature=BLUEPRINT_TEMPERATURE,
               task="plan")
