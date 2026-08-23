"""The construction planner behind the ``plan_construction`` tool.

The builder plans best when it plans once, in writing, before the first part is
placed — footprint, levels, bill of materials, order of assembly. This draws
that plan up in a call of its own: it gathers evidence (official sets that
solved something similar, the model being extended), asks a model with no tools
for a plan in a fixed JSON shape, and then resolves every shape it named against
the real parts catalogue.

The resolution step is the point. A plan is only worth following if the parts in
it exist, so the planner names shapes ("brick 2 x 4") and this module turns them
into part numbers with real footprints and stacking heights — or says plainly
that a shape had no match, which is a thing the builder needs to know before it
starts writing coordinates.
"""

import json
import math
import re

from . import buildir, catalog
from .config import BLUEPRINT_MODEL, BLUEPRINT_PROMPT_FILE, BLUEPRINT_TEMPERATURE

# Two references, not ten: this call sits between the user and the first brick
# they see, so it buys evidence only up to the point where it starts costing
# them the wait. It is also two assemblies of real LDraw now rather than two
# parts lists, and four of those would be more than anyone plans against.
MAX_REFERENCE_SETS = 2
# A long existing model would crowd out everything else; the plan for a change
# only needs to see the shape of what is there.
MAX_MODEL_LINES = 400


class PlanningFailed(RuntimeError):
    """The planning model could not be reached or returned nothing."""


_llm_instance = None
# Set by the app when the user picks a model; None leaves BLUEPRINT_MODEL alone.
_model = None


def _llm():
    """The planning model, built once and reused.

    Deliberately toolless: this call plans, and a planner that could reach for
    ``edit_model`` would stop being a planner. Streamed even though nobody is
    watching the stream, because a stop is only noticed between chunks.
    """
    global _llm_instance
    if _llm_instance is None:
        from .llm import LLM

        _llm_instance = LLM(model=_model or BLUEPRINT_MODEL,
                            temperature=BLUEPRINT_TEMPERATURE,
                            task="plan")
    return _llm_instance


def set_model(model):
    """Point the planner at another model.

    Dropped rather than retargeted, so the next call rebuilds it: the cached
    instance carries flags negotiated against the old model — whether it would
    take tools, whether it would stream — and those say nothing about the new
    one. Pass None to go back to ``BLUEPRINT_MODEL``.
    """
    global _model, _llm_instance
    _model = (model or "").strip() or None
    _llm_instance = None


def _prompt():
    if not BLUEPRINT_PROMPT_FILE.is_file():
        return ""
    return BLUEPRINT_PROMPT_FILE.read_text(encoding="utf-8").strip()


# -- evidence ---------------------------------------------------------------

def references(subject, max_pieces=None, limit=MAX_REFERENCE_SETS):
    """Official models that solved something like this, opened up.

    The **geometry**, not a shopping list. This used to return each set's
    most-used parts — "a car: 4 tyres, 4 wheel rims, 2 grille tiles" — and that
    is a parts bin, not a construction. It says nothing about the thing the
    planner cannot work out for itself and the corpus already knows: that a car
    starts on a `2441` car base with the wheels in its recesses, that the
    windscreen lies back on a hinge, that the bonnet is two wedge slopes
    meeting at 24 LDU. Planned from the shopping list, every vehicle came out a
    box on four wheels, which is exactly what a parts list describes.

    So this hands over what `refsets` hands the builder: the assemblies each set
    comes apart into, and the real LDraw of the one worth copying. Same
    function, same digests — see refsets.py.

    Best effort: an unbuilt vector index or a retrieval failure means the plan
    is written from the subject alone, which is worse but still a plan.
    """
    from . import refsets

    try:
        return refsets.find(subject, limit=limit,
                            max_pieces=max_pieces or refsets.MAX_PIECES)
    except Exception:
        return []


def _reference_text(rows):
    """The reference sets as the planner reads them. See refsets.as_text."""
    from . import refsets

    return refsets.as_text(rows) or ""


def _request_text(subject, requirements, current_model, max_pieces,
                  footprint_studs, reference_rows, design_brief=None,
                  workbench=None, recalled=None):
    blocks = [f"Subject: {subject}"]
    if requirements:
        blocks.append(f"Requirements: {requirements}")

    # The look was settled before this call, at a temperature this one does not
    # run at — see brief.py. It arrives as part of the request rather than as
    # evidence because that is what it is: the plan has to deliver it.
    if design_brief:
        from . import brief as brief_module

        rendered = brief_module.as_text(design_brief)
        if rendered:
            blocks.append(
                "Design brief — what this model has to look like. The "
                "silhouette, the palette and the signature detail are "
                "requirements, not suggestions: put the colours in the bill of "
                "materials, and give the signature detail and the technique "
                "steps of their own.\n\n" + rendered)
    if max_pieces:
        blocks.append(f"Piece budget: at most {int(max_pieces)} parts.")
    if footprint_studs:
        blocks.append(f"Footprint limit: {footprint_studs}.")

    # What is on the bench, before its source. The order is the point: this
    # says the file is a house, the source below says which lines it is made
    # of, and a plan written from the second without the first is a plan for a
    # collection of type-1 lines.
    if workbench:
        blocks.append(
            "What is already on the workbench, read and looked at before this "
            "run started. This is what you are changing:\n\n" + str(workbench))

    if current_model:
        every = current_model.strip().splitlines()
        lines = every[:MAX_MODEL_LINES]
        blocks.append(
            "This is a change to an existing model. Its current LDraw source:\n"
            "```\n" + "\n".join(lines) + "\n```"
            # Said rather than done quietly. A plan written from the first 400
            # lines of a 900-line model, believing it had seen all of it, will
            # put a step where a wall already is — and nothing downstream can
            # tell that the planner was working from half a model.
            + (f"\n\n**Only the first {MAX_MODEL_LINES} of this model's "
               f"{len(every)} lines are shown.** There is more of it than you "
               f"can see, so plan the change and do not assume anything about "
               f"what is not on this page."
               if len(every) > MAX_MODEL_LINES else "")
        )
    else:
        blocks.append("This is a new model, built from nothing.")

    if reference_rows:
        blocks.append(
            "Real LEGO sets that already built this, opened up — how they are "
            "actually put together, in their own LDraw. Plan from these: say "
            "in `graft` which assembly the build starts from, and spend the "
            "steps on what makes this model different from it.\n\n"
            + _reference_text(reference_rows)
        )

    # What this builder already worked out for itself, on a subject like this
    # one. Below the sets and weaker than them, for the reason recall.py gives:
    # a set is how LEGO solved the problem, a creation is only how this agent
    # solved it. It is here at all because a plan that has not seen the trunk
    # that came out right last time plans another trunk from nothing.
    if recalled:
        blocks.append(
            "This builder's own earlier work on something like this — models "
            "it built and saved, and notes it wrote. Weaker evidence than the "
            "sets above and it never overrules them; use it for what it "
            "already got right, and plan past what it did not.\n\n"
            + str(recalled))

    blocks.append("Write the plan as one JSON object.")
    return "\n\n".join(blocks)


# -- the plan ---------------------------------------------------------------

def plan(subject, requirements=None, current_model=None, max_pieces=None,
         footprint_studs=None, use_references=True, should_stop=None,
         design_brief=None, reference_sets=None, workbench=None,
         recalled=None):
    """A construction plan for ``subject``, with its parts resolved.

    ``reference_sets`` are digests the caller has already found — the harness
    opens them before the build starts, and passing them in rather than looking
    again does two things. It saves the second search, and it means the plan is
    written against **the same sets the builder will be holding**: a plan that
    says "graft the chassis from 1477-1" is only executable if 1477-1 is one of
    the sets the builder was given.

    Raises ``PlanningFailed`` if the prompt is missing or the model returned
    nothing; anything the model *did* return is kept, even unparseable, since
    an unstructured plan still beats no plan.
    """
    system = _prompt()
    if not system:
        raise PlanningFailed(
            f"the construction planner prompt is missing: {BLUEPRINT_PROMPT_FILE}")

    if should_stop and should_stop():
        return {"stopped": True, "note": "planning was stopped by the user"}

    if reference_sets:
        reference_rows = list(reference_sets)
    else:
        reference_rows = references(subject, max_pieces) if use_references else []
    body = _request_text(subject, requirements, current_model, max_pieces,
                         footprint_studs, reference_rows, design_brief,
                         workbench, recalled)

    reply = _llm().complete(
        [{"role": "system", "content": system},
         {"role": "user", "content": body}],
        # This is the longest single call in a build. Without a stop check of
        # its own, pressing Stop during it does nothing at all until it
        # returns, which is exactly when Stop stops looking like a button.
        should_stop=should_stop,
    )
    if getattr(reply, "stopped", False):
        return {"stopped": True, "note": "planning was stopped by the user"}

    text = (getattr(reply, "content", "") or "").strip()
    if not text:
        raise PlanningFailed("the planning model returned nothing")

    result = {"subject": subject}
    if reference_rows:
        # Named, not repeated: the source lines are already in the builder's
        # context from the same digests, and sending a second copy back inside
        # the plan would spend the budget saying it twice.
        result["reference_sets"] = [
            {k: v for k, v in row.items()
             if k in ("set_number", "set_name", "theme", "year", "pieces",
                      "shows", "shows_parts")}
            for row in reference_rows
        ]

    document = _extract_json(text)
    if document is None:
        # Rare, and not worth failing over: the builder can read prose.
        result["plan_text"] = text
        result["note"] = ("the planner did not return JSON, so the plan is "
                          "unstructured and its parts were not resolved "
                          "against the catalogue — check every part number "
                          "yourself with search_parts")
        return result

    bill, total, needs_check = resolve_parts(document.pop("parts", None))
    unresolved_ops = resolve_ops(document.get("steps"), bill)
    result["plan"] = document
    result["bill_of_materials"] = bill
    result["total_pieces"] = total

    # The positions, before anything is built from them. See check_geometry.
    off_grid = check_geometry(document)
    if off_grid:
        result["geometry_problems"] = off_grid
        result["geometry_note"] = (
            f"{len(off_grid)} coordinate(s) in this plan are not on the stud "
            f"grid. Each one comes with `nearest_legal` — use that value "
            f"instead of the one written in the step. Do not build the plan as "
            f"it stands: an off-grid placement fails validation, and it fails "
            f"it after the whole model has been written and rendered.")
    if needs_check:
        result["parts_to_confirm"] = needs_check
        result["note"] = (f"{needs_check} entr(y/ies) in the bill of materials "
                          f"are marked with a hint — resolve those with "
                          f"search_parts before placing them. The rest are "
                          f"catalogue-confirmed; do not search for them again.")
    warnings = []
    if max_pieces and total > int(max_pieces):
        warnings.append(f"the plan needs {total} parts, over the budget of "
                        f"{int(max_pieces)}; simplify it as you build")
    monotony = vocabulary_warning(bill, total)
    if monotony:
        warnings.append(monotony)
    if warnings:
        result["warning"] = warnings[0] if len(warnings) == 1 else warnings
    steps = [s for s in (document.get("steps") or []) if isinstance(s, dict)]
    runnable = sum(len(step.get("ops") or []) for step in steps)
    grouped = sum(1 for step in steps for op in (step.get("ops") or [])
                  if isinstance(op, dict)
                  and str(op.get("op") or "").lower() in buildir.GROUP_OPS)
    if runnable:
        result["ops_note"] = (
            f"{runnable} of the steps came back as `ops` with real part "
            f"numbers in them. Pass them to `build_ops` as they are — the "
            f"spacing is worked out from each part, so they are shorter to run "
            f"than to retype and cannot be mis-spaced."
            # A group is one op and many parts, so the count above understates
            # what the plan actually lays. Said out loud, because the mistake
            # it invites is the expensive one: a builder that reads "4 ops" and
            # decides that cannot be the whole build writes the copies out by
            # hand, which is the arithmetic these exist to remove.
            + (f" {grouped} of them are `repeat`/`reflect`/`call` groups, which "
               f"each lay many parts — run them as they are rather than writing "
               f"their copies out." if grouped else "")
            + (f" {unresolved_ops} op(s) had no catalogue match and were left "
               f"with the shape they named; resolve those with search_parts "
               f"first." if unresolved_ops else ""))

    # A graft named in the plan is turned into the call that performs it. The
    # plan is read once, at the point where the builder is deciding what to do
    # first, and "the plan says to start from set 1477-1" is a fact it has to
    # act on — whereas the call, written out, is the action itself.
    #
    # Unless grafting is off, in which case the planner has proposed a step
    # that cannot be taken. The set it found is still the right set, so it is
    # handed over as something to read: dropping it silently would lose the
    # one piece of research the plan actually did.
    from .tools import copy_from_set_enabled

    graft = document.get("graft")
    if (isinstance(graft, dict) and graft.get("set_number")
            and not copy_from_set_enabled()):
        result["study_first"] = {
            "read": f'read_model("set:{graft["set_number"]}"'
                    + (f', submodel="{graft["submodel"]}"'
                       if graft.get("submodel") else "") + ")",
            "for": graft.get("take"),
            "why": "grafting is switched off for this build, so this set is "
                   "here to be read rather than copied. Look at how it solves "
                   "the shape, then place your own parts.",
        }
        result["next"] = (
            "Read the set in `study_first` before you start — it is how this "
            "is really built — then build the steps in order (`build_ops` for "
            "the ops above, `edit_model` for anything irregular) in your own "
            "coordinates, and validate_model.")
    elif isinstance(graft, dict) and graft.get("set_number"):
        call = [f'set_number="{graft["set_number"]}"']
        if graft.get("submodel"):
            call.append(f'submodel="{graft["submodel"]}"')
        call.append("at=[0, 0, 0]")
        result["start_with"] = {
            "call": f"copy_from_set(path=…, {', '.join(call)})",
            "taking": graft.get("take"),
            "then": graft.get("change"),
            "why": "this is how the thing is really built, and it is already "
                   "measured. Graft it, validate, and spend the rest of the "
                   "build on what makes this model different from that set.",
        }
        result["next"] = (
            "Start with the `copy_from_set` in `start_with` — that is step 1 "
            "and everything else is measured from what it puts down. Then "
            "build the remaining steps in order (`build_ops` for the ops "
            "above, `edit_model` for anything irregular) and validate_model.")
    else:
        result["next"] = ("Build the steps in order — `build_ops` for the ops "
                          "above, `edit_model` for anything irregular — then "
                          "validate_model.")
    return result


# -- parts ------------------------------------------------------------------

def resolve_parts(specs):
    """Turn the planner's shapes into catalogue parts.

    Returns ``(bill_of_materials, total_pieces, needs_check_count)``.
    """
    bill, total, needs_check = [], 0, 0

    for spec in specs or []:
        if not isinstance(spec, dict):
            spec = {"shape": str(spec)}

        shape = str(spec.get("shape") or spec.get("part") or
                    spec.get("description") or "").strip()
        quantity = _int(spec.get("quantity"), 1)

        entry = {"shape": shape, "quantity": quantity}
        for key in ("role", "colour", "color"):
            if spec.get(key) is not None:
                entry["role" if key == "role" else "colour"] = spec[key]

        # A part number in the plan came from the user's own request, so it is
        # checked rather than searched for.
        candidates = []
        found = catalog.get_part(spec["part_id"]) if spec.get("part_id") else None
        if found is None and shape:
            candidates = lookup(shape, spec.get("category"))
            found = _best_match(shape, candidates)

        if found is None:
            entry["part_id"] = None
            entry["hint"] = ("no catalogue match for this shape — search_parts "
                             "for it before placing it")
            needs_check += 1
        else:
            for key in ("part_id", "description", "category", "width_studs",
                        "depth_studs", "kind", "place_height_ldu"):
                if found.get(key) is not None:
                    entry[key] = found[key]

            # Semantic search always returns its nearest neighbour, so "no good
            # match" comes back looking exactly like a good one. A number the
            # builder trusts blindly is worse than one it is told to check.
            wrong_thing = not _plausible(shape, found.get("description"))
            wrong_size = not _footprint_ok(shape, found)
            if wrong_thing or wrong_size:
                entry["uncertain"] = True
                entry["hint"] = (
                    f"the nearest the catalogue came is "
                    f"{found.get('description')!r}, which is not the size this "
                    f"asked for — confirm with search_parts before placing it"
                    if wrong_size else
                    "this is the closest the catalogue came to the shape, and "
                    "it may be wrong — confirm with search_parts before "
                    "placing it")
                others = [c for c in candidates[1:] if c.get("part_id")]
                if others:
                    entry["alternatives"] = [
                        {"part_id": c.get("part_id"),
                         "description": c.get("description")} for c in others
                    ]
                needs_check += 1

        total += quantity
        bill.append(entry)

    return bill, total, needs_check


# A plan where one shape is more than this is a plan to approximate something.
# The blueprint prompt asks for the same figure; this is the half that notices
# when the plan came back anyway, which is most of the time — a rule in a prompt
# is a preference, and a number checked after the fact is a fact.
MAX_SHAPE_SHARE = 1.0 / 3.0
# Below this there is nothing to be varied about and nothing to say.
MIN_PLAN_PARTS = 12


def vocabulary_warning(bill, total):
    """Whether one shape is running away with the build, as a line to show.

    This is the same measurement style.py makes on the finished model, moved
    forward to where it is cheap to act on: changing a bill of materials costs
    nothing, and changing a model that has already been written costs a repair
    round. Returns None when the plan is fine or too small to judge.
    """
    if not bill or total < MIN_PLAN_PARTS:
        return None

    biggest, quantity = None, 0
    for entry in bill:
        count = _int(entry.get("quantity"), 1)
        if count > quantity:
            biggest, quantity = entry, count
    if not biggest or quantity / total <= MAX_SHAPE_SHARE:
        return None

    from . import style

    typical = style.baseline(total) or {}
    expected = typical.get("top_share")
    comparison = (f", where real sets this size are about "
                  f"{round(expected * 100)}%" if expected else "")
    shape = biggest.get("shape") or biggest.get("part_id") or "one shape"

    return (f"`{shape}` is {round(100 * quantity / total)}% of this plan "
            f"({quantity} of {total} parts){comparison}. That is the shape of a "
            f"build that approximates something — a curve stepped out of "
            f"plates, foliage made of one round brick, a slope built as a "
            f"staircase. Before you place it that many times, search_parts for "
            f"the shape you are approximating; it almost certainly exists, and "
            f"one of it beats eight of these.")


# The stud grid, as a plan is allowed to use it. x and z land on whole studs,
# or on a half stud where a jumper plate provides one; y moves in plates.
STUD_LDU = 20
HALF_STUD_LDU = 10
PLATE_LDU = 8
# Listed rather than counted: a plan with forty bad coordinates has one bad
# habit, and forty lines saying so would push the plan itself out of context.
MAX_GEOMETRY_PROBLEMS = 8


def _snap(number, step):
    """``number`` to the nearest multiple of ``step``, ties away from zero.

    Not ``round``: Python rounds halves to even, so a rotation of 45 degrees
    snapped to right angles came back as 0 — which is not the nearest legal
    turn, it is *no turn*, and following it drops the intent the plan was
    expressing. Half a step is genuinely ambiguous; resolving it away from zero
    at least keeps the movement the plan asked for.
    """
    scaled = number / step
    whole = math.floor(abs(scaled) + 0.5) * (1 if scaled >= 0 else -1)
    return whole * step


def _off_grid(value, step):
    """How far ``value`` is from the nearest multiple of ``step``, or 0."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number - _snap(number, step), 3)


def _nearest(value, step):
    return int(_snap(float(value), step))


def check_geometry(document):
    """Every coordinate the plan states, against the stud grid.

    This is the half of the plan nothing was checking. ``resolve_parts`` makes
    sure the parts *exist*; until this existed nothing made sure the positions
    were **buildable**, even though the prompt asks for exactly that in so many
    words — "every position is a real number", "everything sits on the stud
    grid". A rule in a prompt is a preference; the same rule measured after the
    fact is a fact, which is the argument `vocabulary_warning` already makes
    one section up.

    The cost of not having it is paid late and in full: a plan with x=17 in it
    is built, validated, rendered from six angles and critiqued before anybody
    notices, and then repaired. The arithmetic here is free and happens before
    a single brick is placed.

    Returns a list of problems, each naming the step, the value and the nearest
    legal one — so the builder corrects rather than re-plans.
    """
    problems = []
    for step in (document or {}).get("steps") or []:
        if not isinstance(step, dict):
            continue
        where = f"step {step.get('n', '?')}"

        drift = _off_grid(step.get("y_ldu"), PLATE_LDU) if step.get("y_ldu") is not None else None
        if drift:
            problems.append({
                "where": where, "field": "y_ldu", "value": step.get("y_ldu"),
                "problem": f"not a whole number of plates ({PLATE_LDU} LDU)",
                "nearest_legal": _nearest(step["y_ldu"], PLATE_LDU)})

        for index, op in enumerate(step.get("ops") or [], start=1):
            if not isinstance(op, dict):
                continue
            at = op.get("at")
            spot = f"{where} op {index}"
            if isinstance(at, (list, tuple)) and len(at) >= 3:
                for axis, value, unit, name in (
                        (0, at[0], HALF_STUD_LDU, "x"),
                        (1, at[1], PLATE_LDU, "y"),
                        (2, at[2], HALF_STUD_LDU, "z")):
                    drift = _off_grid(value, unit)
                    if drift:
                        problems.append({
                            "where": spot, "field": f"at[{name}]", "value": value,
                            "problem": (
                                f"off the stud grid by {abs(drift)} LDU — x and "
                                f"z land on multiples of {STUD_LDU}, or "
                                f"{HALF_STUD_LDU} for a half stud a jumper "
                                f"plate provides"
                                if name != "y" else
                                f"not a whole number of plates ({PLATE_LDU} LDU)"),
                            "nearest_legal": _nearest(value, unit)})
            rotate = op.get("rotate")
            if rotate is not None and _off_grid(rotate, 90):
                problems.append({
                    "where": spot, "field": "rotate", "value": rotate,
                    "problem": "turns are whole right angles",
                    "nearest_legal": _nearest(rotate, 90)})
            if len(problems) >= MAX_GEOMETRY_PROBLEMS:
                return problems
    return problems


def resolve_ops(steps, bill):
    """Turn each step's ``part_shape`` into a real ``part`` id, in place.

    The planner names shapes, because a part number it invents is worse than
    none — but an op is only worth having if it can be *run*, and `build_ops`
    takes part numbers. So the shapes are resolved here, against the bill of
    materials this plan already resolved wherever possible: the same shape
    should not become two different parts depending on which field it was
    written in.

    Returns how many ops could not be resolved. Those keep the shape they
    named, so the builder can see what was meant and search for it.
    """
    known = {}
    for entry in bill or []:
        shape = str(entry.get("shape") or "").strip().lower()
        if shape and entry.get("part_id") and not entry.get("uncertain"):
            known.setdefault(shape, entry["part_id"])

    def one_shape(shape):
        """A named shape as a part id, or None when nothing plausible matches."""
        part_id = known.get(shape.lower())
        if part_id is None:
            # Semantic search always returns its nearest neighbour, so a
            # shape nobody makes comes back looking like a confident hit.
            # `resolve_parts` guards the bill of materials against that and
            # an op needs the guard more, because an op is *run*: an
            # unchecked number here places forty of the wrong part.
            hits = lookup(shape)
            best = hits[0] if hits else None
            if best and _plausible(shape, best.get("description")):
                part_id = best.get("part_id")
        return part_id

    def walk(ops):
        """Resolve a list of ops, descending into the groups. Returns misses."""
        missed = 0
        for op in ops or []:
            if not isinstance(op, dict):
                continue
            kind = str(op.get("op") or "").strip().lower()

            # A group places nothing itself; what needs resolving is inside it.
            if kind in buildir.GROUP_OPS:
                missed += walk(op.get("ops"))
                continue

            # `fill` names a ladder rather than one part.
            if kind == "fill":
                shapes = op.pop("part_shapes", None)
                if shapes and not op.get("parts"):
                    found = [one_shape(str(s).strip()) for s in shapes
                             if str(s).strip()]
                    if all(found) and found:
                        op["parts"] = found
                    else:
                        op["part_shapes"] = shapes
                        op["hint"] = ("some of these shapes had no catalogue "
                                      "match — search_parts for them and put "
                                      "their part_ids in `parts`, or drop "
                                      "`parts` to use the standard ladder")
                        missed += 1
                continue

            # `wall` and `box` choose their own bricks, so there is nothing
            # here to resolve and nothing missing. They used to be counted as
            # unresolved, which reported every plan that laid a wall as a plan
            # with a part the catalogue could not find.
            if kind in ("wall", "box") or op.get("part"):
                continue

            shape = str(op.pop("part_shape", "") or "").strip()
            if not shape:
                missed += 1
                continue
            part_id = one_shape(shape)
            if part_id:
                op["part"] = part_id
            else:
                op["part_shape"] = shape
                op["hint"] = ("no catalogue match — search_parts for this "
                              "shape and put its part_id in `part`")
                missed += 1
        return missed

    unresolved = 0
    for step in steps or []:
        if not isinstance(step, dict):
            continue
        unresolved += walk(step.get("ops"))
    return unresolved


def lookup(query, category=None, limit=3):
    """Catalogue matches for a shape described in words, best first.

    Hybrid search when the vector index is there, keyword search when it is
    not — the same fallback the search_parts tool makes, so a plan resolves
    even on a checkout where the indexes were never built.
    """
    try:
        from ..retrieval import search

        hits = search.search_parts(query, category=category, max_results=limit)
    except Exception:
        hits = []
    if not hits:
        hits = catalog.search_parts(query, category, max_results=limit)
    return hits


# Dimensions and filler carry no evidence that a match is the right shape:
# "2 x 4" appears in thousands of descriptions, and every part is a "part".
_NOISE = frozenset((
    "and", "for", "the", "with", "part", "piece", "element", "brick's",
    "degree", "degrees", "deg",
))


def _words(text):
    return {w for w in re.findall(r"[a-z]+", (text or "").lower())
            if len(w) > 2 and w not in _NOISE}


def _plausible(shape, description):
    """Whether a match shares any real word with the shape that was asked for.

    Deliberately weak: it is there to catch a wheel returned for a chimney, not
    to judge which slope is the better slope. It says nothing about *size* —
    ``_words`` keeps letters only — which is what ``_footprint_ok`` is for.
    """
    wanted = _words(shape)
    if not wanted:
        return True
    return bool(wanted & _words(description))


# "2 x 4", "2x4", "2 × 4" — the size in a shape or in a catalogue description.
_DIMENSIONS = re.compile(r"(\d+)\s*[x×]\s*(\d+)")


def _pairs(text):
    """Every ``a x b`` in some text, as unordered pairs."""
    return {frozenset((int(m.group(1)), int(m.group(2))))
            for m in _DIMENSIONS.finditer(text or "")}


def _wanted_footprint(shape):
    """The size the plan asked for, or None where it named none."""
    found = _DIMENSIONS.search(shape or "")
    return frozenset((int(found.group(1)), int(found.group(2)))) if found else None


def _footprint_ok(shape, row):
    """Whether a catalogue match is the size the shape asked for.

    ``_plausible`` cannot answer this and never could: it compares words, and a
    size is digits. So `plate 20 x 20` resolved to *Plate 2 x 2 Round with 1
    Centre Stud* and came back with no warning on it at all — the word "plate"
    was shared, and that was the whole test. Measured over the plans on disk,
    10% of every entry that named a size resolved to a part of a different one,
    every one of them silently.

    Two ways to be right, because neither alone is enough. The **description**
    naming the same pair is the stronger signal and catches the parts whose
    footprint is not their name — *Slope Brick 45 1 x 2 Double / Inverted*
    measures 2 x 2 and is exactly what "slope 45 1 x 2" meant. The
    **catalogue footprint** catches the rest.

    A shape that named no size is not judged here: most parts are named by what
    they are, not how big they are.
    """
    want = _wanted_footprint(shape)
    if want is None:
        return True
    if want in _pairs(row.get("description")):
        return True
    width, depth = row.get("width_studs"), row.get("depth_studs")
    if width and depth:
        return frozenset((int(width), int(depth))) == want
    return False


def _best_match(shape, candidates):
    """The first candidate that is the right thing *and* the right size.

    Search returns its nearest neighbour by similarity, and similarity does not
    read numbers — so the best match for `brick 2 x 16` can be `Brick 1 x 16`
    while the right part sits second in the same list. Preferring a candidate
    that agrees on the footprint costs nothing: they were all fetched already.
    """
    for candidate in candidates or ():
        if _plausible(shape, candidate.get("description")) \
                and _footprint_ok(shape, candidate):
            return candidate
    return candidates[0] if candidates else None


# -- parsing ----------------------------------------------------------------

_FENCE = re.compile(r"```(?:json)?\s*(.+?)```", re.S)


def extract_json(text):
    """Public name for ``_extract_json``, for the other planning passes.

    The brief has exactly the same problem this module already solved — a model
    asked for one JSON object and nothing else answers with a sentence, a fence
    and then the object — and solving it twice is how the two come to disagree.
    """
    return _extract_json(text)


def _extract_json(text):
    """The first complete JSON object in ``text``, or None.

    Models wrap JSON in fences and introduce it with a sentence however firmly
    they were asked not to, so the object is found by scanning braces rather
    than by trusting the whole reply to parse.
    """
    body = (text or "").strip()
    fence = _FENCE.search(body)
    if fence:
        body = fence.group(1).strip()

    start = body.find("{")
    if start < 0:
        return None

    depth, in_string, escaped = 0, False, False
    for index in range(start, len(body)):
        char = body[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    document = json.loads(body[start:index + 1])
                except ValueError:
                    return None
                return document if isinstance(document, dict) else None
    return None


def _int(value, default=1):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default
