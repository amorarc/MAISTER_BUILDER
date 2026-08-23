"""Assembles the agent's context out of data/agent_prompts.

The context an agent works from is four different kinds of thing, and running
them together as one wall of markdown makes all four harder to use:

* **Standing knowledge** — how LDraw works, what the pieces are, what the tools
  do, what "checked" means. Long, stable, identical on every run.
* **The task** — this petition, this subconstruction, this file.
* **The state of play** — what has been built so far, what the checks said.
* **The conversation** — what the user has actually asked for, turn by turn.

So each one is delimited and named. Blocks come from ``context/`` in filename
order, and each is wrapped in a tag naming what it is, which gives the model
something to navigate by and gives anyone reading the prompt a table of
contents instead of an essay.

The old ``skills/`` directory is still read if it exists, after the context
blocks, so a checkout mid-migration does not silently lose half its prompt.
"""

import re

from .config import CONTEXT_DIR, KNOWLEDGE_FILE, SKILLS_DIR, SYSTEM_PROMPT_FILE

# What is said when grafting has been switched off. The context blocks are
# written for the normal case and they are emphatic about it — "copy first,
# design second" is in there twice — so withdrawing the tool without saying so
# leaves a builder reading instructions to call something it has not been
# given. It goes last, where the context map's own rule ("where two say the
# same thing, the more specific one wins") makes it the one that counts.
NO_GRAFTING = """\
**`copy_from_set` is switched off for this build.** It is not in your tools and
calling it will fail. Wherever your context tells you to graft an assembly out
of a set, copy first and design second, or start from what a set already built
— that instruction does not apply here, and this section is the one that wins.

This is not a restriction on the sets themselves. `search_reference`,
`get_set_details` and `read_model("set:<n>")` all work as they always did, and
reading them is now more valuable rather than less: a real set is the record of
how a designer solved a shape with real parts, and studying one before you
build is the whole of what you are being asked to do with it.

What changes is that you place every part yourself, with `build_ops` and
`edit_model`. Take the *technique* — how a wheel arch is stepped, where the
plates go to turn a corner, which part does a job you were going to approximate
with three. Do not reproduce the set line by line: a hand-copied graft is the
thing that was switched off, and doing it by typing is not a way round it.

The point of this mode is to find out what you design. A simpler model that is
yours is the wanted outcome here."""

# Filename stem -> the tag its content is wrapped in. A block with no entry
# here is wrapped under its own stem, so adding a file to context/ is enough to
# get it into the prompt.
SECTIONS = {
    "00_role_and_method": "role_and_method",
    "10_lego_cad": "lego_cad_knowledge",
    "20_pieces": "pieces_context",
    "25_connections": "how_pieces_join",
    "26_techniques": "what_real_sets_are_built_of",
    "27_minifigures": "minifigures",
    "30_tools": "available_tools",
    "32_build_ops": "building_by_operation",
    "40_feedback": "checking_your_work",
    "50_references": "reference_sets",
    "60_memory": "your_library_and_notes",
}


def _read(path):
    return path.read_text(encoding="utf-8").strip() if path.is_file() else ""


def _tag(stem):
    return SECTIONS.get(stem, stem.lstrip("0123456789_") or stem)


def _block(tag, body):
    return f"<{tag}>\n{body}\n</{tag}>"


# What the picture says about the room rather than about the thing. A
# photograph of a chair is also a photograph of a floor, a wall and a rug, and a
# builder handed all of it builds all of it — one did, and nine 6x6 plates went
# into a floor nobody asked for. The setting is dropped before the description
# reaches anyone who could build it.
#
# `arrangement` goes too, and it is the subtle one. It is the only field about
# how the objects stand relative to *each other and the room* — "the rug is on a
# wooden floor, behind the chair is a lamp" — where `composition`, `relations`
# and `whole` are all about the object itself and are exactly what a builder
# needs. Anything an object must actually *have* to meet its neighbours is put
# in its requirements by the decomposer, so nothing is lost by dropping the
# narration of the photograph.
_SETTING_KEYS = ("background", "setting", "environment", "surroundings",
                 "arrangement")


def buildable_reference(description):
    """A picture's description with the room taken out of it.

    Public because the design brief needs the same view: a brief written from a
    photograph puts the floor in the palette if it is shown the floor.
    """
    return _buildable(description)


def _buildable(description):
    """A picture's description with the room taken out of it."""
    if not isinstance(description, dict):
        return description

    kept = {}
    for key, item in description.items():
        if key.lower() in _SETTING_KEYS:
            continue
        if key == "objects" and isinstance(item, (list, tuple)):
            item = [o for o in item
                    if not (isinstance(o, dict)
                            and "scener" in str(o.get("role") or "").lower())]
            # With one object left there are no others for it to relate to, and
            # `with_others` is then a description of the scenery just removed —
            # "the chair is standing on a rug" is how a rug gets built.
            if len(item) == 1 and isinstance(item[0], dict):
                item = [{k: v for k, v in item[0].items() if k != "with_others"}]
        kept[key] = item
    return kept


def _as_text(value):
    """A description as readable text, whether it was stored as JSON or prose."""
    if isinstance(value, dict):
        import json

        lines = []
        for key, item in value.items():
            if key.startswith("_") or key == "vision_model" or item in (None, "", []):
                continue
            if isinstance(item, (list, tuple)):
                item = "; ".join(
                    json.dumps(v, ensure_ascii=False) if isinstance(v, dict) else str(v)
                    for v in item)
            lines.append(f"- **{key.replace('_', ' ')}**: {item}")
        return "\n".join(lines)
    return str(value or "")


def numbered_lines(text):
    """A model file with line numbers, in the format read_model uses.

    The same format on purpose: the numbers a builder is shown here and the
    numbers it is shown by a tool have to be the same numbers, or an edit
    written against one of them lands on the wrong line of the other.
    """
    # Trailing whitespace only: stripping a leading blank line would shift
    # every number by one against the file the tools actually read.
    lines = str(text).rstrip().splitlines()
    return "\n".join(f"{i:5d} | {line}" for i, line in enumerate(lines, start=1))


def load_context():
    """Every context block, in filename order, as ``(tag, body)``."""
    if not CONTEXT_DIR.is_dir():
        return []
    return [(_tag(p.stem), _read(p)) for p in sorted(CONTEXT_DIR.glob("*.md"))]


def load_skills():
    """The retired skills directory, if a checkout still has one."""
    if not SKILLS_DIR.is_dir():
        return []
    return [(p.stem, _read(p)) for p in sorted(SKILLS_DIR.glob("*.md"))]


# A person, in any of the words a request uses for one. The decomposer makes
# every person its own subconstruction whose subject is a minifigure (see
# decompose_prompt.md), so a subbuild whose subject says none of these is a
# subbuild with no figure in it.
_PEOPLE = re.compile(
    r"\b(minifig\w*|figure|person|people|human|"
    r"man|men|woman|women|boy|girl|child|children|kid|baby|"
    r"driver|rider|pilot|captain|sailor|soldier|knight|king|queen|prince|"
    r"princess|wizard|witch|ninja|pirate|astronaut|diver|firefighter|"
    r"police|officer|doctor|nurse|chef|farmer|worker|builder|shopkeeper|"
    r"villager|crew|passenger|character|skeleton|zombie)\b",
    re.IGNORECASE,
)


def _droppable(subject):
    """Context blocks this build has no use for, by tag.

    The standing prompt is 30,412 tokens and every turn of every subbuild pays
    all of it — there is no prompt caching in front of the router, so it is
    paid in full, not once. Two blocks are big and are dead weight far more
    often than not:

    * **minifigures** (3,252 tok) — assembly rules for a figure, shipped while
      building the letter M. Dropped only where the subject is known and says
      nothing about a person: with no subject, this cannot be decided and the
      block stays.
    * **reference_sets** (1,696 tok) — how to graft an assembly out of a real
      set, shipped when grafting has been switched off, where it is not merely
      wasted but *contradicted* three sections later by `grafting_is_off`.

    Conservative by construction: every uncertain case keeps the block, since
    what is lost by carrying it is tokens and what is lost by dropping it
    wrongly is the build.
    """
    drop = set()
    from .tools import copy_from_set_enabled

    if not copy_from_set_enabled():
        drop.add("reference_sets")
    if subject and not _PEOPLE.search(str(subject)):
        drop.add("minifigures")
    return drop


def build_system_prompt(include_knowledge=False, subject=None):
    """The standing context: everything that is the same on every run.

    ``subject`` is what this agent is building, when the caller knows. It is
    used only to leave out context the build cannot need — see ``_droppable``.
    Omit it and nothing is dropped.
    """
    unwanted = _droppable(subject)
    blocks = [(tag, body) for tag, body in load_context()
              if body and tag not in unwanted]

    # A preamble that names the sections, so the model knows what it has before
    # it has read any of it.
    if blocks:
        contents = "\n".join(f"- <{tag}>" for tag, _ in blocks)
        head = ("You are given your context in labelled sections:\n\n"
                f"{contents}\n\n"
                "Read them as one brief. Where two say the same thing, the more "
                "specific one wins.")
        parts = [_block("context_map", head)]
        parts += [_block(tag, body) for tag, body in blocks]
    else:
        # No context/ directory: the pre-migration single-file prompt, if a
        # checkout still has one.
        parts = [_read(SYSTEM_PROMPT_FILE)]

    for name, body in load_skills():
        if body:
            parts.append(_block("skill", f"# {name}\n\n{body}"))

    if include_knowledge:
        knowledge = _read(KNOWLEDGE_FILE)
        if knowledge:
            parts.append(_block("ldraw_specification", knowledge))

    # Read at build time, not at import: the setting can change between one
    # message and the next, and a system prompt frozen at process start would
    # go on describing the tools a run had an hour ago.
    from .tools import copy_from_set_enabled

    if not copy_from_set_enabled():
        parts.append(_block("grafting_is_off", NO_GRAFTING))

    prompt = "\n\n".join(p for p in parts if p)
    if not prompt.strip():
        # An agent with an empty system prompt does not fail, it just builds
        # badly and gives no sign why. Fail here instead, where the cause is
        # still visible.
        raise RuntimeError(
            f"the agent has no context: {CONTEXT_DIR} holds no readable .md "
            f"blocks and there is no {SYSTEM_PROMPT_FILE.name} to fall back on")
    return prompt


# How many faults of each kind the brief lists. The full report is what
# `validate_model` gives; this is the standing reminder that they are there.
FAULTS_LISTED = 8


def _faults_text(report):
    """What is already wrong with the model this run is about to change.

    A run gets a fresh agent, so the ``validate_model`` reports of the run
    before it went with the conversation that held them: the model could be
    handed a broken file and a request to paint the door, and it would paint
    the door and hand back a build that still does not stand up.

    So the faults are re-derived from the file rather than remembered. That is
    better than a replayed report as well as simpler — it cannot describe a
    model that has been edited since, by hand or by anything else.
    """
    lines = [
        "**The model in that file does not pass validation as it stands.** "
        "These faults were there before this request; they are not the change "
        "you were asked for, and they are yours to leave behind you either "
        "way — a run that finishes still holding them has produced a model "
        "that cannot be built.",
    ]

    connectivity = report.get("connectivity") or {}
    collision = report.get("collision") or {}

    def listing(rows, render):
        rows = list(rows or [])
        out = [f"- {render(r)}" for r in rows[:FAULTS_LISTED]]
        if len(rows) > FAULTS_LISTED:
            out.append(f"- …and {len(rows) - FAULTS_LISTED} more")
        return out

    groups = [
        ("Off the stud grid", listing(
            connectivity.get("misaligned_parts"),
            lambda r: f"line {r.get('line')}: `{r.get('part')}` at "
                      f"{r.get('position')}, {r.get('gap_ldu')} LDU off")),
        ("Sharing solid plastic", listing(
            collision.get("overlapping_parts"),
            lambda r: f"line {r.get('line')}: `{r.get('part')}` overlaps "
                      f"`{r.get('other_part') or r.get('other')}`")),
        ("Parts that do not exist", listing(
            report.get("missing_parts"),
            lambda r: f"`{r.get('part')}` on line(s) {r.get('lines')}")),
        ("Objects that are not one connected piece", listing(
            connectivity.get("objects_in_pieces"),
            lambda r: f"`{r.get('object')}` is in {r.get('pieces')} separate "
                      f"clumps ({r.get('largest_piece')} of its "
                      f"{r.get('parts')} parts in the biggest one)")),
        ("Minifigure parts out of place", listing(
            (report.get("minifigures") or {}).get("misassembled_parts"),
            lambda r: f"line {r.get('line')}: {r.get('problem')} — "
                      f"{r.get('fix')}")),
    ]
    for title, rows in groups:
        if rows:
            lines.append(f"**{title}**\n" + "\n".join(rows))

    lines.append(
        "Fix these with `edit_model` on the lines named, alongside whatever "
        "you were asked to change, and `validate_model` to confirm. If a fault "
        "turns out not to be real — the check is stricter than the build is — "
        "say so in your summary rather than leaving it unmentioned.")
    return "\n\n".join(lines)


def build_task_context(petition=None, subconstruction=None, index=None,
                       total=None, siblings=None, model_path=None,
                       current_model=None, history=None, state=None,
                       requirements=None, size_hint=None, max_pieces=None,
                       quantity=1,
                       closing=None, modifying=False, reference=None,
                       known_faults=None, design_brief=None, workbench=None,
                       reference_sets=None, requirements_record=None,
                       recalled=None):
    """The per-run context: the task, the state of play, the conversation.

    Everything here changes between runs, which is exactly why it is kept apart
    from the standing context above. Sections with nothing in them are omitted
    rather than included empty — a heading with nothing under it reads as an
    instruction to go and find something to put there.
    """
    parts = []

    if petition:
        asked = [f"The user asked for: {petition}"]
        if siblings:
            asked.append(
                "That is more than one object. The others are being built "
                "separately and arranged with yours afterwards: "
                + ", ".join(siblings) + ". Do not build them.")
        parts.append(_block("chat_context", "\n\n".join(asked)))

    if subconstruction:
        if modifying:
            job = [f"Change the existing model: {subconstruction}",
                   "This is an edit, not a new build. Use `edit_model` on the "
                   "lines that this change actually touches, and leave every "
                   "other line exactly as it is. What comes out must be the "
                   "model that was already there with this change made to it — "
                   "one model, still connected, with the change attached to it "
                   "on real studs. Building the new part into a model of its "
                   "own, or placing it beside the build instead of on it, is "
                   "the one way to get this wrong."]
        else:
            job = [f"Build: {subconstruction}"]
        if index and total and total > 1:
            job.append(f"This is {'change' if modifying else 'object'} "
                       f"{index} of {total}.")
        if requirements:
            job.append(f"They specifically asked for: {requirements}")
        if size_hint:
            job.append(f"Aim for roughly {size_hint}. Everything in a scene has "
                       f"to be at the same scale, and this is the size that fits.")
        if max_pieces:
            job.append(
                f"Build it in about {int(max_pieces)} parts. Pass "
                f"`max_pieces={int(max_pieces)}` to plan_construction so the "
                f"plan is drawn to that budget rather than trimmed to it "
                f"afterwards. A finished small model beats an unfinished large "
                f"one — if the subject genuinely will not read at this size, "
                f"build it well at this size anyway and say so at the end.")
        if quantity and int(quantity) > 1:
            job.append(f"{int(quantity)} of these are wanted. Build ONE, well — "
                       f"the copies are made afterwards.")
        if model_path:
            job.append(f"{'It lives at' if modifying else 'Write it to'} "
                       f"`{model_path}` (relative to out/, which is what "
                       f"edit_model expects).")
        parts.append(_block("your_task", "\n\n".join(job)))

    # What it should look like, decided before this run started — see brief.py.
    # It sits directly under the task because it is part of the task: the
    # difference between a model that satisfies the description and one worth
    # looking at is almost entirely in these five lines.
    if design_brief:
        from . import brief as brief_module

        rendered = brief_module.as_text(design_brief)
        if rendered:
            parts.append(_block("design_brief", (
                "How this should look. It was decided for this build "
                "specifically, and it is not a suggestion — the palette, the "
                "silhouette and the signature detail are part of what you were "
                "asked for.\n\n"
                f"{rendered}\n\n"
                "Two things it does not do. It never overrules the user: "
                "anything they asked for by name wins over anything here. And "
                "where there is a reference picture, the picture is the "
                "specification and this only fills what it left open.")))

    # What actually ends this run. High in the context, directly under the task
    # and the brief: it is the definition of done, and a builder that reads it
    # last has already decided what it was going to build.
    checklist = requirements_record
    if checklist is None and state is not None:
        checklist = getattr(state, "requirements", None)
    if checklist:
        from . import requirements as acceptance

        rendered = acceptance.as_text(checklist)
        if rendered:
            parts.append(_block("requirements_to_finish", (
                f"{rendered}\n\n"
                "Every one of these is checked against your renders and your "
                "measurements each time you call `validate_model`, one at a "
                "time, true or false. The run ends by itself the moment they "
                "are all true — you do not call `finish` to end it and saying "
                "you are done does nothing. Anything not on this list is not "
                "what you are being judged on, so build these first.")))

    if reference:
        # One picture or four. The wording matters more than it looks: a
        # builder told about "the picture" when the user attached four goes
        # looking for one of them and builds from whichever it decides that is.
        many = int(reference.get("count") or 1) > 1
        count = int(reference.get("count") or 1)
        it = "them" if many else "it"
        block = [
            (f"The user attached **{count} reference pictures**. Together they "
             f"are what they want built — the same thing seen more than once, "
             f"so a detail visible in only one of them is still part of the "
             f"specification."
             if many else
             "The user attached a **reference picture**. It is what they want "
             "built,")
            + " and " + ("they outrank" if many else "it outranks")
            + " your own judgement about how this should look — the "
              "composition, the colours and the proportions in "
            + ("them" if many else "it") + " are the specification.",
            f"You cannot see {it}. **Call `describe_image` first**, before "
            f"planning anything, and build what the description says.",
            f"When you `validate_model`, the renders are compared against "
            f"{'those pictures' if many else 'that picture'} and you are told "
            f"where they differ. The run cannot finish until the model reads "
            f"as {it}.",
        ]
        block.append(
            f"Anything the description leaves open, **ask**: `ask_vision_model` "
            f"puts your questions to something that can see "
            f"{'the pictures' if many else 'the picture'}. You get one set of "
            f"questions, and another after each time you change the model — so "
            f"ask about what you are otherwise going to invent, all of it at "
            f"once.")
        block.append(
            "**Build the object, not the room it was photographed in.** A "
            "picture always comes with a floor, a wall and whatever else was "
            "lying about, and none of it was asked for. No baseplate, no "
            "ground, no backdrop unless the request asked for one in words.")
        if reference.get("description"):
            block.append("It has already been described — the setting has been "
                         "taken out, so what is left is what you are "
                         f"building:\n\n{_as_text(_buildable(reference['description']))}")
        answered = [q for q in (reference.get("qa") or []) if isinstance(q, dict)]
        if answered:
            # Carried over from earlier turns and earlier subbuilds. Asking one
            # of these again would spend the whole allowance on an answer that
            # is already on the page.
            block.append(
                "These have already been asked about it — do not ask them "
                "again:\n\n" + "\n".join(
                    f"- **{q.get('question')}** — {q.get('answer')}"
                    for q in answered))
        parts.append(_block("reference_image", "\n\n".join(block)))

    # Real sets that already built this, found and opened by the harness — see
    # refsets.py. High in the context on purpose: it is the material the build
    # should start from, and material nobody reads until after they have chosen
    # an approach is material that changes nothing.
    if reference_sets:
        block = reference_sets
        if reference:
            # The order of authority, said where both are on the page. The sets
            # are found by searching the subject, so "a red car" can return a
            # set with a trailer and a minifigure on it, and a builder told to
            # copy first will copy those too — into a model whose specification
            # is a photograph that has neither. Copying is for how a thing is
            # built; the picture decides what is in it.
            block += (
                "\n\n---\n\n"
                "**The picture the user attached outranks every one of these.**"
                " They are here for construction — how a wheel arch is made, "
                "how a roof is stepped, what a real designer spends plates on. "
                "They are not here to decide what the model contains.\n\n"
                "So: nothing goes into this build because a set had one. Not a "
                "minifigure, not a trailer, not a sticker, not a feature of the "
                "set's own theme. If it is not in the picture, it is not in the "
                "model — and if the sets found for this subject turn out to be "
                "about something else, take nothing from them at all and build "
                "what the picture shows.")
        parts.append(_block("real_sets_that_built_this", block))

    # And what this agent already worked out for itself — the models it built
    # and saved, and the notes it wrote. See recall.py. Below the sets, on
    # purpose: an official set is how LEGO solved the problem and a creation is
    # only how this agent solved it, so where the two disagree the set wins.
    if recalled:
        parts.append(_block("your_own_earlier_work", recalled))

    # What was already on the workbench, read before the run started — see
    # survey.py. It goes immediately above the source it is a reading of: the
    # numbered lines below say what is in the file, and this says what it *is*.
    if workbench:
        parts.append(_block("what_is_already_built", workbench))

    if current_model and current_model.strip():
        # Numbered, because those numbers are what `edit_model` takes. An
        # existing model shown as a bare block leaves the builder only one way
        # to change it — retype the whole thing — and that is how the parts
        # nobody asked about get lost.
        parts.append(_block(
            "current_model_file",
            f"This is what the file holds right now, with its line numbers. "
            f"Preserve anything the user did not ask you to change: use "
            f"`edit_model` with these line numbers to change only what they "
            f"did.\n\n```\n{numbered_lines(current_model)}\n```"))

    if known_faults:
        parts.append(_block("outstanding_faults", _faults_text(known_faults)))

    if history:
        parts.append(_block("conversation_so_far", history))

    # The parts this build has already turned up, from every search in every
    # subconstruction before this one. Without it each builder starts blind and
    # re-approximates a shape the last one already found the part for.
    if state is not None and getattr(state, "project", None):
        from . import palette

        found = palette.summary(state.project)
        if found:
            parts.append(_block("parts_you_have_found", (
                f"{found}\n\nThese are yours to use — they came out of searches "
                f"this build has already run, and every part number here is "
                f"real. Use one before searching for something to do the same "
                f"job, and never search again for a part that is on this list.")))

    if state is not None:
        snapshot = state.snapshot()
        parts.append(_block("progress", "\n".join(
            f"- {k}: {v}" for k, v in snapshot.items() if v is not None)))

    if closing:
        parts.append(_block("what_to_do_now", closing))

    return "\n\n".join(parts)
