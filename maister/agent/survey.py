"""Reading the workbench before anything is decided.

Every run used to start by handing the model file's *text* to the decomposer
and hoping. That is enough to notice a file has 96 lines in it; it is not
enough to know what those lines are. A project opened from an official set
arrives holding a finished BrickHeadz figure, a project on its fourth turn
holds whatever the last three left behind, and a brand new project holds
nothing at all - and the split, the brief and the builder were all deciding
what to do next without anyone having looked.

So a run now begins here, before the request is split and before anything is
built:

* **counted** - parts, distinct shapes, colours, submodels, and the sets any of
  it was grafted from, straight off the source;
* **measured** - how wide, how deep, how many bricks tall, from the same
  flattened geometry the collision checker uses;
* **checked** - the ordinary validation report, so a run inherits its faults
  knowingly rather than discovering them at the end;
* **rendered and looked at** - six views through LeoCAD and one vision call, so
  the answer to "what is on the workbench" is a sentence rather than a line
  count.

Everything degrades. No LeoCAD, no vision model, an unparseable file: the
survey comes back with less in it and the run carries on. What it must never do
is raise - this sits in front of every build, and a run that cannot start
because the *reading* failed would be a worse harness than the one that never
read anything.
"""

import re
from pathlib import Path

from . import geometry, render
from .config import OUT_DIR
from .validation import validate

# `0 // 53 parts grafted from set 8641-1 "Flame Glider", submodel …` - the
# credit copy_from_set leaves behind. Worth surfacing: a build standing on
# somebody else's set should say so before it is added to.
_GRAFT = re.compile(
    # The credit line writes the set name in typographic quotes; a file edited
    # by hand may well use straight ones.
    r"grafted from set ([\w.\-]+)(?:\s+[\"“]([^\"”]*)[\"”])?", re.I)
_PART_LINE = re.compile(r"^\s*1\s+(\S+)\s+(?:\S+\s+){12}(\S+)")
_FILE_LINE = re.compile(r"^\s*0\s+FILE\s+(.+?)\s*$", re.I)
# `0 !LDRAW_ORG`, `0 Name:`, `0 Author:` and friends are bookkeeping; the title
# is the first plain comment, which is where LDraw keeps the name of the thing.
_META = re.compile(r"^\s*0\s+(!|//|Name:|Author:|Untitled)", re.I)

# How many of each list is worth carrying into a prompt.
MAX_SUBMODELS = 12
MAX_COLOURS = 8
MAX_ISSUES = 4


def read(path):
    """The facts, off the file, with no API calls and no library lookups.

    Cheap enough to call anywhere. Returns ``{"empty": True}`` for a file that
    is missing, unreadable, or holds nothing but a header.
    """
    target = Path(path)
    if not target.is_absolute():
        target = OUT_DIR / target
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"empty": True, "missing": True}

    parts, colours, submodels, sets = [], {}, [], []
    title = None
    for line in text.splitlines():
        block = _FILE_LINE.match(line)
        if block:
            submodels.append(block.group(1))
            continue
        piece = _PART_LINE.match(line)
        if piece:
            colour, name = piece.group(1), piece.group(2)
            parts.append(name.lower())
            colours[colour] = colours.get(colour, 0) + 1
            continue
        if line.lstrip().startswith("0"):
            credit = _GRAFT.search(line)
            if credit:
                sets.append({"set": credit.group(1),
                             "name": (credit.group(2) or "").strip() or None})
            elif title is None and not _META.match(line) and line.strip() != "0":
                title = line.strip()[1:].strip() or None

    if not parts:
        return {"empty": True, "title": title,
                "submodels": submodels[:MAX_SUBMODELS]}

    ranked = sorted(colours.items(), key=lambda kv: -kv[1])
    return {
        "empty": False,
        "title": title,
        "parts": len(parts),
        "distinct": len(set(parts)),
        "submodels": submodels[:MAX_SUBMODELS],
        "submodel_count": len(submodels),
        "colours": [c for c, _ in ranked[:MAX_COLOURS]],
        "grafted_from": _unique(sets),
    }


def _unique(sets):
    """The sets this model was built out of, each named once."""
    seen, out = set(), []
    for entry in sets:
        if entry["set"] in seen:
            continue
        seen.add(entry["set"])
        out.append(entry)
    return out


def survey(path, project=None, look=True, should_stop=None):
    """Everything the run should know about the model file before it starts.

    ``look`` renders the model and asks the vision model what it is - the
    expensive half, and the half worth having: a builder told "96 parts, 8
    studs wide" still does not know it is holding an Iron Man.
    """
    facts = read(path)
    facts["path"] = str(path)
    if facts.get("empty"):
        return facts

    target = Path(path)
    if not target.is_absolute():
        target = OUT_DIR / target

    try:
        measured = geometry.measure(target)
    except Exception:
        measured = None
    if measured and not measured.get("error"):
        facts["size"] = measured.get("size_studs")

    try:
        report = validate(target)
    except Exception:
        report = None
    if report and not report.get("error"):
        facts["validates"] = bool(report.get("passed"))
        facts["verdict"] = report.get("verdict")
        connectivity = report.get("connectivity") or {}
        facts["misaligned"] = len(connectivity.get("misaligned_parts") or [])
        facts["overlapping"] = (report.get("collision") or {}).get("overlapping")

    if not look or (should_stop and should_stop()):
        return facts

    try:
        images, sheet, critique, note = render.look(
            target, project=project,
            # No subject: the question is what this *is*, and naming a subject
            # here would be handing the vision model the answer. A survey that
            # agrees with whatever it was told is not a survey.
            subject=None,
            question=("What is this model, and how finished does it look? Say "
                      "what is already built and what is plainly unfinished."))
    except Exception as exc:
        facts["render_note"] = str(exc)
        return facts

    facts["renders"] = [str(p) for _, p in images]
    facts["contact_sheet"] = str(sheet) if sheet else None
    facts["_images"] = images
    facts["_sheet"] = sheet
    if note:
        facts["render_note"] = note
    if critique:
        facts["looks_like"] = critique.get("reads_as")
        facts["seen"] = critique
        issues = [i for i in (critique.get("issues") or []) if isinstance(i, dict)]
        if issues:
            facts["unfinished"] = [i.get("what") for i in issues[:MAX_ISSUES]
                                   if i.get("what")]
    return facts


def as_text(surveyed):
    """The survey as a block for a prompt, or None when there is nothing to say.

    Written as what it is - a reading of the file taken before this run touched
    anything - because that is how it stays honest once a build is under way
    and the file has moved on from it.
    """
    if not surveyed:
        return None

    if surveyed.get("empty"):
        return ("The workbench is **empty**. The model file has no parts in it, "
                "so nothing is being extended and nothing has to be preserved - "
                "this is a build from nothing.")

    lines = ["**There is already a model on the workbench**, and this is it as "
             "the run found it, before anything was built this time."]

    what = surveyed.get("looks_like")
    if what:
        lines.append(f"- It looks like: **{what}**")
    if surveyed.get("title"):
        lines.append(f"- The file calls it: {surveyed['title']}")
    lines.append(f"- {surveyed.get('parts', 0)} part(s), "
                 f"{surveyed.get('distinct', 0)} distinct shape(s)")

    size = surveyed.get("size") or {}
    if size:
        lines.append(f"- About {size.get('width')} studs wide, "
                     f"{size.get('depth')} deep and "
                     f"{size.get('height_bricks')} bricks tall")

    blocks = [s for s in (surveyed.get("submodels") or [])
              if not s.lower().endswith(".dat")]
    if len(blocks) > 1:
        lines.append(f"- Built out of {len(blocks)} named assemblies: "
                     + ", ".join(blocks))

    grafted = surveyed.get("grafted_from") or []
    if grafted:
        lines.append("- Parts of it were copied from real sets: " + ", ".join(
            f"{g['set']}" + (f" ({g['name']})" if g.get("name") else "")
            for g in grafted))

    if surveyed.get("validates") is False:
        lines.append(f"- It does **not** validate as it stands: "
                     f"{surveyed.get('verdict')}")
        # Said out loud, because the alternative is a build that spends its
        # steps repairing somebody else's model. Official sets routinely report
        # a tile or two off the lattice - they are real sets, they were built
        # out of real bricks, and the checker is stricter than the plastic.
        lines.append(
            "  These faults were **already here before this run started**. "
            "They are not yours: do not spend the build fixing them unless the "
            "user asked for that, and do not add to them. If one of them sits "
            "in the way of what you were asked to do, fix that one and say so.")
    elif surveyed.get("validates"):
        lines.append("- It validates: every part is on the stud grid and "
                     "nothing overlaps.")

    unfinished = surveyed.get("unfinished") or []
    if unfinished:
        lines.append("- What was noticed looking at it: "
                     + "; ".join(str(u) for u in unfinished))

    lines.append(
        "Work **with** this, not over it. Anything already built that the user "
        "did not ask you to change stays exactly where it is - and if what they "
        "asked for is an addition, it attaches to this on real studs rather "
        "than standing beside it.")
    return "\n".join(lines)


def headline(surveyed):
    """One line for the log and the event stream."""
    if not surveyed:
        return "nothing read"
    if surveyed.get("empty"):
        return "the workbench is empty"
    bits = [f"{surveyed.get('parts', 0)} part(s)"]
    if surveyed.get("looks_like"):
        bits.append(f"reads as {surveyed['looks_like']}")
    if surveyed.get("validates") is False:
        bits.append("does not validate")
    return ", ".join(bits)
