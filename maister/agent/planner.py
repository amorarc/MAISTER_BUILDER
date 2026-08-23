"""A planning pass that runs before the builder.

A one-line request ("add a chimney") leaves every position for the builder to
invent mid-build, which is where models drift off the stud grid. This turns the
request into a brief with real coordinates first: footprint, subtasks, Y levels
in LDU, and the parts each step needs by shape.

It is one extra LLM call, and it is skipped for anything that is not a build or
a fix - asking what is on the grid does not need a construction plan.
"""

import re

from .config import DEFAULT_MODEL, PLANNER_PROMPT_FILE

# Verbs that mean "change the model". Anything else - a question, a comment -
# goes straight through unplanned.
_BUILD = re.compile(
    r"\b(build|make|create|construct|design|assemble|"
    r"add|put|place|attach|stack|extend|expand|"
    r"fix|repair|correct|adjust|align|"
    r"change|modify|edit|update|replace|swap|convert|turn|"
    r"move|shift|rotate|resize|scale|"
    r"remove|delete|drop|strip|"
    r"paint|colou?r|recolou?r|"
    r"taller|shorter|wider|bigger|smaller|longer)\b",
    re.IGNORECASE,
)

# Asking about the model rather than changing it.
_ASK = re.compile(
    r"^\s*(what|which|how many|how much|why|where|is|are|does|do|can you tell|"
    r"describe|explain|list|show|tell me|summari[sz]e)\b",
    re.IGNORECASE,
)


# Verbs that change a model that already exists, rather than calling a new one
# into being. "quit" is here because people use it for "get rid of".
_MODIFY = re.compile(
    r"\b(add|attach|put|place|stick|mount|fit|include|give it|"
    r"remove|delete|drop|strip|erase|quit|take off|get rid of|"
    r"change|modify|edit|update|alter|adjust|fix|repair|correct|"
    r"replace|swap|convert|turn it|"
    r"move|shift|rotate|resize|scale|"
    r"paint|recolou?r|colou?r|"
    r"make it|taller|shorter|wider|narrower|bigger|smaller|longer)\b",
    re.IGNORECASE,
)


def has_parts(model_text):
    """True when a model file actually holds parts, not just a header."""
    return any(line.lstrip().startswith("1 ")
               for line in (model_text or "").splitlines())


def is_modification(message, current_model=None):
    """True when the request changes the model that is already there.

    This is the difference between "add a chimney" and "build a chimney". The
    first is an edit to one model and must come back as one model with a
    chimney on it; the second could reasonably be a chimney on its own. Only
    the presence of an existing build tells them apart, which is why the model
    is an argument rather than something guessed from the wording.
    """
    if not has_parts(current_model):
        return False
    if is_question(message):
        return False
    return bool(_MODIFY.search(message or ""))


def is_question(message):
    """True when the message asks about the model rather than changing it.

    Stricter than ``needs_plan``: a message that opens with a question word is
    a question even when it also contains a build verb. "What colour is the
    roof?" contains "colour", and treating that as a build request means
    answering a question by rebuilding the model.
    """
    text = (message or "").strip()
    return bool(text and _ASK.match(text)) and not text.lower().startswith(
        ("show me how", "make ", "build "))


def needs_plan(message):
    """True when the request is to build or fix something, by its wording.

    Verb-driven, and deliberately still so: this decides whether to spend the
    optional planner pre-pass and whether a run is worth naming, and for both
    of those a false negative costs nothing much. It is **not** what decides
    whether a request reaches the build harness - see ``wants_model``, which
    had to stop asking this question the moment it turned out that most people
    do not use a verb at all.
    """
    text = (message or "").strip()
    if not text:
        return False
    if _ASK.match(text) and not _BUILD.search(text):
        return False
    return bool(_BUILD.search(text))


# Messages that ask for nothing: a greeting, a thank-you, an acknowledgement.
# Matched whole, so "ok" is small talk and "ok now make it taller" is not.
_SMALL_TALK = re.compile(
    r"^\s*(hi|hey|hello|yo|greetings|"
    r"thanks?|thank you|cheers|ta|"
    r"ok|okay|k|kk|sure|right|fine|"
    r"cool|nice|great|good|lovely|perfect|awesome|beautiful|amazing|"
    r"yes|yep|yeah|no|nope|nah|"
    r"got it|i see|understood|"
    r"bye|goodbye|see you|good (morning|afternoon|evening|night))"
    r"[\s.!?,…]*$",
    re.IGNORECASE,
)


def is_small_talk(message):
    """True for a message that is courtesy rather than a request."""
    return bool(_SMALL_TALK.match((message or "").strip()))


def wants_model(message):
    """True when this turn should go through the build harness.

    **A verb is not required, and requiring one was a bug.** This used to be
    ``needs_plan``, which looks for "build", "make", "add" and their kin - so
    `add a chimney` reached the harness and `a house` did not. A bare noun
    phrase is the most natural way there is to ask for a model, and every one
    of them went down the conversational path instead: no workbench survey, no
    decomposition, one agent left to infer from the file what it was looking
    at. `a tree and a car` was built as a single muddled object for the same
    reason - nothing ever split it, because nothing had decided it was a build.

    So the default is inverted. Everything is a modelling request unless it is
    plainly not one, and there are only two of those: a question about the
    model, and courtesy. Both are cheap and certain to recognise, where "is
    this a build" is neither.
    """
    text = (message or "").strip()
    if not text:
        return False
    if is_question(text):
        return False
    return not is_small_talk(text)


def _prompt():
    if not PLANNER_PROMPT_FILE.is_file():
        return ""
    return PLANNER_PROMPT_FILE.read_text(encoding="utf-8").strip()


def plan(message, current_model, llm, on_delta=None, should_stop=None):
    """A construction brief for `message`, or None if planning was skipped.

    Never raises: a planner that fails just means the builder works from the
    raw request, which is what it did before this existed.
    """
    system = _prompt()
    if not system:
        return None

    body = (
        f"Current model file:\n```\n{(current_model or '').strip()}\n```\n\n"
        f"User request: {message}\n\n"
        f"Write the brief."
    )

    try:
        reply = llm.complete(
            [{"role": "system", "content": system},
             {"role": "user", "content": body}],
            on_delta=on_delta,
            # Planning is the first thing a run does and the longest single
            # generation in it, so Stop has to reach in here too.
            should_stop=should_stop,
        )
    except Exception:
        return None

    if getattr(reply, "stopped", False):
        return None

    text = (getattr(reply, "content", "") or "").strip()
    return text or None


def apply_plan(task, brief):
    """Fold a brief into the task the builder receives."""
    if not brief:
        return task
    return (
        f"{task}\n\n"
        f"---\n\n"
        f"A planning pass produced this brief for the request. The coordinates "
        f"in it have not been checked against the parts catalogue, so verify "
        f"part choices with your tools and correct anything that does not sit "
        f"on the stud grid - but follow its structure rather than starting a "
        f"plan of your own. The build is already planned: do not call "
        f"plan_construction as well.\n\n"
        f"{brief}"
    )


def planning_llm(client, model=None):
    """A non-tool LLM for the planning pass, so it cannot start building."""
    from .llm import LLM

    return LLM(client=client, model=model or DEFAULT_MODEL, task="plan")
