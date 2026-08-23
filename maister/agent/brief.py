"""What the model should look like, decided before anything decides where.

Every pass in this project between the request and the first brick is an
*engineering* pass. ``decompose`` says how many objects there are.
``plan_construction`` says what the levels are and what the parts are and what
order they go on in. Both are careful, both are grounded, and neither of them
ever asks what the thing should look like - so nobody does, and the answer
defaults to whatever satisfies the description with the least effort. That is
reliably a box.

This is the one pass that asks. It runs between the split and the plan, it has
no tools, it produces five short fields, and it costs one call:

* **reads_as** - the silhouette, which is what makes a thing recognisable
* **palette** - three colours as LDraw codes, and where each goes
* **signature** - the one small detail a person would point at
* **technique** - one way of joining bricks that is not stacking them
* **avoid** - the dull version, named out loud so it is not what gets built

# Why it is a call of its own

Partly the temperature. ``plan_construction`` runs at 0.3, and config.py is
right about why: a plan is arithmetic, and a model that gets creative with
arithmetic writes a plan that does not add up. The same call was also being
asked what the model should look like, and those two jobs want opposite
settings. So they are two calls: this one runs hot and decides the look, the
planner stays cold and works out how to build the look it was handed.

**But the temperature is the least of it, and this is worth being exact about,
because it is the half that reads like the whole reason and is not.** Sampling
hotter does not move the answer, it re-words it - measured, and the finding is
that temperature and sampling changes alone buy almost no diversity while
conditioning on a constraint buys a great deal (arXiv:2602.20408). The reason is
that the first idea is not a sampling accident. It is the *typical* answer, and
a model returns the typical answer because its preference data was written by
annotators who preferred familiar text (arXiv:2510.01171) - the distribution is
sharpened onto it, and turning the temperature up samples the same sharpened
peak more noisily.

What actually moves it is the three things below, in rising order of effect:

* **naming the dull answer** so the model is conditioned against it - the
  ``avoid`` field, which is asked for *first* so that everything after it is
  written knowing what it must not be (defixation, arXiv:2606.00875);
* **a constraint and a stance** to write from, drawn per build - ``VARIATIONS``
  and ``PERSONAS`` below, two independent axes, of which the persona kind is
  the strongest single intervention measured (arXiv:2602.20408);
* **asking for a distribution instead of an answer** - five briefs with the
  model's own probabilities, and one taken from the tail. See
  ``BRIEF_CANDIDATES`` in config.py.

None of it makes the model more creative. All of it stops the run taking the
median of everything ever written about houses and calling it a decision.

# Where it sits, which is load-bearing

Before ``refsets`` fetches the real sets, and that ordering is not incidental.
Being shown an example is the single most reliable way to lose the ideas you
would otherwise have had - generative assistance measurably *increases*
fixation on whatever was shown first (arXiv:2403.11164), and models fixate on
examples the same way people do (arXiv:2502.05870). The brief is written before
any set is opened, so the sets arrive as evidence about *how to build* a look
that has already been chosen, rather than as the place the look comes from.
Move this pass after ``_reference_sets`` and it stops working, silently.

# It never overrules the user

A brief adds; it does not decide. Everything the request stated - a colour, a
size, a feature - passes through untouched, and when a reference picture is
attached the picture's description is the specification and the brief only fills
what the description left open. This pass exists to stop the model inventing
nothing, not to let it invent over the top of what it was told.

# How much it invents is the request's decision, not this pass's

Everything above was built to answer one failure - the model returns the median
of everything ever written about houses - and it answered it at one fixed
intensity, on every build, whatever was asked for. That is the second failure,
and it is the one users actually report: **ask for a table and you get the fifth
most likely table.** A random angle to push on, a random stance, and a brief
drawn from the tail of the distribution are, between them, a machine for making
sure the plain answer is never the one that gets built. For "a wizard's crooked
table" that machine is right. For "a table" it is the whole problem.

So the intensity is read off the request. See ``licence`` below:

* **PLAIN** - the default, and what a bare request gets. The *mode* of the
  distribution rather than the tail, no angle, and a stance fixed at the one
  that means "build the standard version of this well". The brief still runs and
  still does its original job: it still names the dull version in ``avoid``, it
  still sets a palette, it still decides a silhouette. What it stops doing is
  reaching for an answer the model itself rated unlikely.
* **INVITED** - the request asked for invention in words. Then all of the
  machinery above applies exactly as it did.

The distinction is worth stating as a rule, because it is what both settings are
serving: **a brief may decide how a thing looks; it may not decide what the
thing is.** A table is a top and legs at sitting height in every brief this pass
should ever write. The colour, the proportions, the edge treatment and the one
detail worth pointing at are what it gets to choose.
"""

import hashlib
import json
import random
import re

from . import blueprint
from .config import (BRIEF_CANDIDATES, BRIEF_ENABLED, BRIEF_MODEL,
                     BRIEF_PROMPT_FILE, BRIEF_TAIL_CEILING, BRIEF_TEMPERATURE)

# How much invention the request licensed. See the module docstring.
PLAIN = "plain"
INVITED = "invited"

# Words that are a user asking, in so many words, for something other than the
# standard version. Matched on whole words against the subject and whatever the
# decomposer put in `requirements`.
#
# Deliberately a short list of *explicit* invitations rather than an attempt to
# detect descriptive language in general. "A small red car" is a precise request
# and gets the plain treatment - precision is the opposite of an invitation to
# invent, and a parser that read every adjective as licence would put us back
# where we started.
_INVITES = re.compile(
    r"\b("
    r"creative|creatively|imaginative|inventive|original|unusual|unique|"
    r"surprise|surprising|whimsical|quirky|fanciful|fantastical|elaborate|"
    r"ornate|artistic|stylised|stylized|exotic|dramatic|"
    r"interesting|impressive|"
    r"your own (?:take|version|idea|spin)|go wild|be bold|have fun with"
    r")\b", re.I)


def licence(subject=None, requirements=None, reference=None):
    """How much this request licenses the brief to invent: PLAIN or INVITED.

    PLAIN unless the request asked for otherwise, and the default is the whole
    point - a bare noun is a request for the thing that noun names, built well,
    and every user who has complained about this pass has complained about
    getting something else.

    A reference picture is always PLAIN, whatever words came with it. The
    picture is the specification; a brief drawn from the tail there is one that
    fills the gaps in the description as unlike the photograph as it can.
    """
    if reference:
        return PLAIN
    text = f"{subject or ''} {requirements or ''}"
    return INVITED if _INVITES.search(text) else PLAIN


# The stance a plain request is written from. Fixed rather than drawn, so that
# "a table" is never handed to the sculptor of shape who builds curves "even
# where it costs parts". A stance is still passed - it is the intervention with
# the most evidence behind it, and this one points at the canonical answer
# rather than away from it.
PLAIN_STANCE = (
    "a LEGO set designer building the standard version of this: it has to be "
    "recognisable as the thing it is named as, from across a room, before any "
    "detail resolves - well proportioned and well finished rather than "
    "unusual"
)

# An angle to push on, one per build, so that asking twice for "a house" does
# not produce the same house twice.
#
# The agent is deterministic in the way that matters here: same request, same
# context, same first idea. Temperature alone does not fix it, because the
# first idea is not a sampling accident - it is the median of everything ever
# written about houses. A constraint moves the median.
#
# They are deliberately mild. Each one is a nudge that a competent designer
# could take or leave, not an instruction to build something strange, and the
# brief is told plainly that the user's own words outrank it.
VARIATIONS = (
    "asymmetry - make one side genuinely different from the other",
    "an unexpected accent colour, somewhere small and deliberate",
    "one part used for something other than its obvious purpose",
    "a diagonal, or something set at an angle to everything else",
    "texture over smoothness - a surface that is broken up rather than flat",
    "exaggerate the proportion that makes this thing recognisable",
    "a moment of life - something posed, opened, mid-action, or worn",
    "one element that overhangs or cantilevers past what holds it up",
    "layering - a surface that shows two depths rather than one",
    "make one detail unusually fine, and leave the rest plain",
)


# Who is writing the brief, one per build. A second axis, independent of the
# angle above, and the reason there are two is that they multiply rather than
# add: ten angles and eight stances is eighty starting points for "a house",
# where one list of ten is ten.
#
# This axis is the one with the most evidence behind it. Across the
# interventions measured against LLM idea homogeneity - temperature, sampling,
# prompt restructuring, chain-of-thought, personas - conditioning on a stance
# was the strongest, and it did not cost the ideas any quality
# (arXiv:2602.20408). The mechanism offered is that a stance partitions what the
# model knows: asked plainly it averages every house it has ever read about,
# asked as a set designer it reaches for the part of that knowledge where sets
# are designed.
#
# They are stances about *building*, not personalities. "You are a quirky
# artist" produces a quirky brief and an unbuildable model; "you build for play"
# produces a different, ordinary, buildable house.
PERSONAS = (
    "a LEGO set designer: this has to read as a product on a shelf, with a "
    "front that is clearly the front",
    "a model-maker after likeness: the proportions of the real thing matter "
    "more than the convenience of the grid",
    "a play-feature designer: something has to open, turn, lift or come off, "
    "and that feature shapes the build",
    "a sculptor of shape: curves, angles and texture over flat walls, even "
    "where it costs parts",
    "a minifigure-scale storyteller: it is built for a figure to use, and the "
    "signs of use are part of it",
    "an economical builder: few part types, used cleverly, nothing decorative "
    "that is not doing structural work",
    "a diorama builder: it is one moment of a scene, and it shows what was "
    "happening just before",
    "an engineer of the real object: it looks the way it does because of how "
    "the real one works",
)


class BriefFailed(RuntimeError):
    """The brief model could not be reached or returned nothing usable."""


_llm_instance = None
_model = None


def _llm():
    """The brief model, built once and reused. Toolless, and hot."""
    global _llm_instance
    if _llm_instance is None:
        from .llm import LLM

        # task="plan" and not "build": this call must answer, not deliberate.
        # It is short by design and it sits between the user and the first
        # brick they see.
        _llm_instance = LLM(model=_model or BRIEF_MODEL,
                            temperature=BRIEF_TEMPERATURE, task="plan")
    return _llm_instance


def set_model(model):
    """Point the brief at another model; None restores ``BRIEF_MODEL``."""
    global _model, _llm_instance
    _model = (model or "").strip() or None
    _llm_instance = None


def _draw(options, seed, offset):
    """One of ``options``, chosen by ``seed`` if there is one.

    ``offset`` is which slice of the seed's digest to read, and it is what keeps
    two draws from the same seed independent of one another. Reading the same
    bits twice would tie the angle and the stance together into one axis of ten
    combinations wearing the costume of eighty.
    """
    if seed is None:
        return random.choice(options)
    digest = hashlib.sha256(str(seed).encode("utf-8")).hexdigest()
    return options[int(digest[offset:offset + 8], 16) % len(options)]


def variation(seed=None, allowed=INVITED):
    """One angle to push on, chosen from ``VARIATIONS``. None for a plain request.

    Seeded from the caller's string so a project keeps the same variation while
    it is being built and a different one next time - a scene whose tree and
    whose house pulled in two unrelated directions would read as two scenes.

    ``allowed`` is the request's licence. None when it is PLAIN, and that is the
    single biggest thing this change does: read the list, and notice that
    "asymmetry - make one side genuinely different from the other" and "a moment
    of life - something posed, opened, mid-action, or worn" are instructions to
    build a *different object*, not to build the same one differently. Handed to
    a bare request they are how a table comes out lopsided.
    """
    if allowed != INVITED:
        return None
    return _draw(VARIATIONS, seed, 0)


def persona(seed=None, allowed=INVITED):
    """One stance to write from. ``PLAIN_STANCE`` for a plain request.

    Seeded like the angle and from the same string, so a scene is written by one
    designer throughout - and drawn from different bits of it, so which designer
    is not decided by which angle.
    """
    if allowed != INVITED:
        return PLAIN_STANCE
    return _draw(PERSONAS, seed, 8)


def _prompt():
    if not BRIEF_PROMPT_FILE.is_file():
        return ""
    return BRIEF_PROMPT_FILE.read_text(encoding="utf-8").strip()


def _request_text(subject, requirements, reference, angle, size_hint,
                  stance=None, candidates=1, failed=None):
    blocks = [f"Subject: {subject}"]
    if requirements:
        blocks.append(f"The user specifically asked for: {requirements}")
    if size_hint:
        blocks.append(f"Rough size: {size_hint}.")
    if stance:
        blocks.append(f"Write this brief as {stance}.")
    if reference:
        blocks.append(
            "A reference picture is attached and it is the specification. This "
            "is what it holds - fill the fields from it, and choose only where "
            "it is silent:\n\n" + str(reference))
    elif angle:
        blocks.append(f"Angle to push on for this build: {angle}")

    # A build that was made from an earlier brief and came back reading as the
    # wrong thing. This is the whole of proposal 3 as it lands here: the critic
    # saw a silhouette fault, which is not a fault the *builder* can repair -
    # "the hull is a rectangular box, not a boat" names no line and has no edit
    # - so the complaint comes back to the pass that chose the silhouette
    # instead. See orchestrator._replan.
    if failed:
        blocks.append(
            "**An earlier attempt at this was built, rendered and looked at, "
            "and it came back as the wrong thing.** This is what was seen:\n\n"
            + str(failed)
            + "\n\nThat is a fault in what was asked for, not in how it was "
            "assembled - a build that comes out unrecognisable was aimed at "
            "the wrong silhouette. So aim at a different one. Do not restate "
            "the brief that produced it in new words: change what gets built, "
            "starting with `reads_as`, and put what was seen into `avoid` so "
            "it cannot be arrived at twice.")

    if candidates > 1:
        # The instruction that does the work. Asking for a distribution rather
        # than an answer is what gets at the briefs the model would not have
        # led with - see BRIEF_CANDIDATES in config.py. The spread is asked for
        # explicitly because a model left to itself will write five briefs that
        # differ in their adjectives and agree on the model.
        blocks.append(
            f"Write {candidates} different briefs for this subject, each with "
            f"the probability that it is the brief you would have given if you "
            f"had been asked for exactly one. They must differ in what gets "
            f"built - a different silhouette, a different signature, a "
            f"different palette - not in how the same build is described. "
            f"Include the obvious one and give it its real probability.\n\n"
            f"Answer as one JSON object, shaped exactly like this - every "
            f"entry carries its own `probability`, and every `brief` has all "
            f"five fields in the order given above:\n\n"
            f'{{"briefs": [\n'
            f'  {{"probability": 0.40, "brief": {{"avoid": "...", '
            f'"reads_as": "...", "palette": {{...}}, "signature": "...", '
            f'"technique": "..."}}}},\n'
            f'  {{"probability": 0.25, "brief": {{"avoid": "...", '
            f'"reads_as": "...", "palette": {{...}}, "signature": "...", '
            f'"technique": "..."}}}}\n'
            f"]}}\n\n"
            f"Nothing outside that object.")
    else:
        blocks.append("Write the brief as one JSON object.")
    return "\n\n".join(blocks)


def _json_objects(text):
    """Every complete JSON object in ``text`` at any depth, outermost first.

    ``blueprint.extract_json`` returns the first complete object and is what a
    single brief wants. This exists for the case it cannot handle: a reply
    holding a list of candidates that was cut off before its closing brace. The
    outer object never completes, so a scanner that only recognises objects at
    the top level finds nothing at all - and the four intact briefs sitting
    inside it are perfectly good. Reading every balanced object, nested ones
    included, salvages those rather than throwing the call away.

    Outermost first because that is the order the caller wants to try them in:
    the wrapper, if it survived, answers the question on its own.
    """
    out, stack, in_string, escaped = [], [], False, False
    for index, char in enumerate(text or ""):
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
            stack.append(index)
        elif char == "}" and stack:
            start = stack.pop()
            try:
                found = json.loads(text[start:index + 1])
            except ValueError:
                continue
            if isinstance(found, dict):
                out.append((start, found))
    return [found for _, found in sorted(out, key=lambda pair: pair[0])]


# The fields a brief is made of. Used to recognise one in a reply whose shape
# was not what was asked for.
_FIELDS = ("reads_as", "palette", "signature", "technique", "avoid")


def _looks_like_brief(entry):
    return isinstance(entry, dict) and any(k in entry for k in _FIELDS)


def _candidates(text):
    """The briefs in a reply, as ``[(probability, brief)]``.

    Lenient on purpose, because this is the one call in the project whose reply
    shape changed, and every way it can come back wrong has a reading that is
    better than no brief at all: the wrapper as asked for; a wrapper whose
    entries hold the fields inline rather than under `brief`; a bare list; a
    truncated reply with intact briefs inside it; or a single brief, which is
    what an older prompt and a smaller model both produce.
    """
    objects = _json_objects(text)
    if not objects:
        return []

    entries = None
    for document in objects:
        found = document.get("briefs")
        if isinstance(found, list) and found:
            entries = found
            break
    if entries is None:
        # No wrapper - a truncated list, or a model that answered with one
        # brief. Take the candidate entries where they survived, since those
        # still carry their probabilities, and bare briefs where they did not.
        # An entry is preferred over the brief nested inside it, which is why
        # the nested one is then dropped rather than counted twice.
        entries, nested = [], []
        for document in objects:
            inner = document.get("brief")
            if _looks_like_brief(inner):
                entries.append(document)
                nested.append(inner)
            elif _looks_like_brief(document) and document not in nested:
                entries.append(document)
        if not entries:
            return []

    out = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        document = entry.get("brief")
        if not _looks_like_brief(document):
            # The fields inline beside the probability, rather than nested.
            document = {k: v for k, v in entry.items() if k != "probability"}
        if not _looks_like_brief(document):
            continue
        probability = entry.get("probability")
        if probability is None and isinstance(entry.get("brief"), dict):
            # Written inside the brief rather than beside it. Taken out, so it
            # cannot travel on into the document the builder is handed.
            probability = document.pop("probability", None)
        try:
            probability = float(probability)
        except (TypeError, ValueError):
            probability = None
        out.append((probability, document))
    return _as_distribution(out)


def _as_distribution(candidates):
    """Rescale probabilities that are plainly not probabilities.

    A model asked for probabilities will sometimes answer in percentages, and
    sometimes in weights that simply do not add to one. Read literally, "35"
    means a candidate sits far above any sensible tail ceiling, and every
    candidate would be judged likely - which quietly turns the tail rule into
    "take the least likely", losing most of what the sampling is for.

    Only the obviously-not-a-distribution case is touched, and it is rescaled by
    its own total rather than assumed to be percentages: that handles 35/25/20
    and 3.5/2.5/2.0 alike, and leaves anything that already looks like a
    distribution exactly as the model wrote it.
    """
    rated = [p for p, _ in candidates if p is not None]
    total = sum(rated)
    if not rated or total <= 0 or (max(rated) <= 1.0 and total <= 1.5):
        return candidates
    return [(None if p is None else p / total, d) for p, d in candidates]


def _select(candidates, seed=None, allowed=INVITED):
    """One brief out of the several that were written.

    Which end of the distribution is taken is the request's decision:

    **PLAIN takes the mode** - the brief the model would have given if it had
    been asked for exactly one. That sounds like it wastes the call, and it does
    not: the five were still written, and writing five is what made the model
    enumerate rather than answer, so even the mode of five is a considered
    answer rather than a reflex. What it does give up is unlikeliness, which for
    a bare request was never wanted.

    **INVITED takes the tail** - a brief the model itself rates unlikely and
    still considers a real answer; it wrote it, so it is one. This is the
    original behaviour and the reason the whole distribution is asked for.

    Unrated candidates count as tail, and never as the mode. A model that gave
    no probabilities has not told us which one is obvious, and reading its
    output order as a ranking it never claimed would pick a brief at random and
    call it the standard one.
    """
    if not candidates:
        return None, None
    if len(candidates) == 1:
        return candidates[0][1], candidates[0][0]

    if allowed != INVITED:
        rated = [c for c in candidates if c[0] is not None]
        if rated:
            probability, document = max(rated, key=lambda c: c[0])
            return document, probability
        # Nothing rated. There is no mode to take, so the seeded choice below is
        # the only honest answer - but it is made over everything rather than
        # over the tail, since without probabilities there is no tail either.
        rng = random.Random(str(seed)) if seed is not None else random
        probability, document = rng.choice(candidates)
        return document, probability

    tail = [c for c in candidates
            if c[0] is None or c[0] <= BRIEF_TAIL_CEILING]
    if not tail:
        # Everything came back likely - an even spread, or a model that rated
        # its five at 0.9 each. The least likely of them is the same choice
        # made where it can still be made.
        least = min(c[0] for c in candidates)
        tail = [c for c in candidates if c[0] == least]

    rng = random.Random(str(seed)) if seed is not None else random
    probability, document = rng.choice(tail)
    return document, probability


def compose(subject, requirements=None, reference=None, angle=None,
            size_hint=None, should_stop=None, stance=None, seed=None,
            candidates=None, allowed=None, failed=None):
    """A design brief for ``subject``, or None if there is not one to be had.

    One call. ``candidates`` briefs are asked for in that one reply and one is
    taken from the probabilities the model gave them - see ``_select``, and
    ``BRIEF_CANDIDATES`` in config.py for why asking for several beats asking
    once and turning the temperature up.

    ``allowed`` is the request's licence to invent, and it decides which end of
    those probabilities is taken. Worked out from the request itself when the
    caller does not pass one, so that every path through this function defaults
    to the plain answer rather than only the paths somebody remembered.

    ``seed`` decides which of the tail is taken, so a project that is resumed or
    a scene whose objects are briefed in parallel does not get a different
    answer each time it asks.

    Best effort throughout. A brief is an improvement to a build, never a
    precondition for one, so every failure here returns None and the run carries
    on exactly as it did before this pass existed - which is also what makes it
    safe to leave switched on by default.
    """
    if not BRIEF_ENABLED or not subject:
        return None

    if allowed is None:
        allowed = licence(subject, requirements, reference)
    # An angle is only ever an invited request's. A caller that passed one
    # anyway does not get to smuggle it past the licence - that would be the
    # same bug in a new place.
    if allowed != INVITED:
        angle = None
        stance = stance or PLAIN_STANCE

    system = _prompt()
    if not system:
        return None
    if should_stop and should_stop():
        return None

    wanted = BRIEF_CANDIDATES if candidates is None else max(1, int(candidates))
    body = _request_text(subject, requirements, reference, angle, size_hint,
                         stance=stance, candidates=wanted, failed=failed)

    try:
        reply = _llm().complete(
            [{"role": "system", "content": system},
             {"role": "user", "content": body}],
            should_stop=should_stop,
        )
    except Exception:
        return None

    if getattr(reply, "stopped", False):
        return None

    text = (getattr(reply, "content", "") or "").strip()
    if not text:
        return None

    found = _candidates(text)
    document, probability = _select(found, seed=seed, allowed=allowed)

    if document is None:
        # Nothing that parsed as a brief. Prose is still a brief - the builder
        # reads English, and half a brief beats spending another call to get
        # the braces right.
        document = blueprint.extract_json(text)
        if not isinstance(document, dict):
            return {"notes": text[:800]} if text else None
        probability = None

    if angle:
        document["variation"] = angle
    if stance:
        document["stance"] = stance
    if len(found) > 1:
        # What this was chosen out of, for the trace and for anyone asking
        # later whether the sampling is doing anything. Ignored by `as_text` -
        # it is a note about the choice, not part of the brief.
        document["sampling"] = {
            "candidates": len(found),
            "chosen_probability": probability,
            "probabilities": [p for p, _ in found],
            # Which end was taken, and therefore why this brief and not another.
            # Without it a trace showing p=0.45 chosen out of five is unreadable
            # - it could be a plain request taking its mode or an invited one
            # whose candidates all came back likely.
            "licence": allowed,
        }
    return document or None


# -- rendering it for the passes downstream ---------------------------------

def _colour(entry):
    """One palette entry as `code - name (where)`, however it was written."""
    if not isinstance(entry, dict):
        return str(entry)
    code = entry.get("code")
    name = entry.get("name") or ""
    where = entry.get("where") or ""
    head = f"`{code}`" if code is not None else ""
    label = " ".join(p for p in (head, name) if p).strip()
    return f"{label} - {where}" if where else label


def colours(document):
    """The palette's LDraw colour codes, as a list of ints.

    Used to carry one scheme across every object of a scene: see palette.py.
    """
    found = []
    palette = (document or {}).get("palette")
    if isinstance(palette, dict):
        entries = palette.values()
    elif isinstance(palette, list):
        entries = palette
    else:
        return found
    for entry in entries:
        code = entry.get("code") if isinstance(entry, dict) else entry
        try:
            code = int(code)
        except (TypeError, ValueError):
            continue
        if code not in found:
            found.append(code)
    return found


def as_text(document):
    """The brief as markdown, for a prompt block. Empty string if there is none."""
    if not isinstance(document, dict) or not document:
        return ""

    lines = []
    if document.get("reads_as"):
        lines.append(f"- **Reads as**: {document['reads_as']}")

    palette = document.get("palette")
    if isinstance(palette, dict) and palette:
        rendered = "; ".join(
            f"{role}: {_colour(entry)}" for role, entry in palette.items()
            if entry)
        if rendered:
            lines.append(f"- **Palette**: {rendered}")
    elif isinstance(palette, list) and palette:
        lines.append("- **Palette**: "
                     + "; ".join(_colour(e) for e in palette if e))

    # `stance` and `sampling` are deliberately not among these. They are how the
    # brief was arrived at, not what it decided, and the builder is being handed
    # a decision - told which designer to imagine it is, it starts designing
    # again instead of building what the brief already settled. They stay on the
    # document for the trace and for the diversity measurement.
    for key, label in (("signature", "Signature detail"),
                       ("technique", "Technique to use"),
                       ("avoid", "Do not build"),
                       ("variation", "Angle for this build"),
                       ("notes", "Notes")):
        if document.get(key):
            lines.append(f"- **{label}**: {document[key]}")

    return "\n".join(lines)
