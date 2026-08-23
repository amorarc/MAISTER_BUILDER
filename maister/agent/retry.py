"""Fixing a tool call that came back with an error, and calling it again.

A tool that errors currently costs a whole step. The result goes back into the
conversation, the builder reads it on its next turn, and - if it reads it well -
makes the call again with the argument corrected. That is one turn of the model
plus one wasted call for a mistake that is usually a typo:

    {"error": "no such file: projects/769903f45562/parts/tree.ldr",
     "hint": "Omit model_path to plan a new model from nothing."}

The hint says exactly what to do. Nobody needs a reasoning turn to act on it,
and a builder under instructions to be decisive is as likely to shrug and carry
on without a plan.

So the call is repaired and retried in place, up to ``MAX_ATTEMPTS`` times, and
the builder sees one result instead of a failure and a correction.

# Two ways to repair, cheapest first

**Deterministic rules** handle the errors with only one sensible fix - a path
that does not exist, an argument the tool does not take. They cost nothing and
they are exact.

**The model itself** handles the rest: it is given the tool's schema, the
arguments that failed and the error, and asked for corrected arguments as JSON.
One short call, no tools, and its answer is checked to be a JSON object before
anything is run with it.

# What is deliberately never repaired

Not every error is a mistake in the call. Some are the tool telling the builder
something it has to know:

* ``edit_model`` - it writes. An ``expect`` that did not match means the
  builder's line numbers are stale, and the repair for that is to read the file
  again, not to try once more with a guess. A wrong write is not recoverable
  the way a wrong lookup is.
* ``finish`` - a refusal is the gate doing its job, and it already says what is
  missing.
* ``ask_about_image`` - the questions are rationed. A retry would spend the
  allowance on the same question.

Those three are the ones where a retry could do damage, spend a budget, or hide
something the builder is supposed to read.
"""

import json

# Attempts in total, the first call included. Three means a bad call gets two
# corrections, which covers a wrong path and then a wrong argument on top of
# it; past that the error is not a typo and belongs in front of the builder.
MAX_ATTEMPTS = 3

# Tools whose errors are usually a malformed call. Everything not listed here
# is left alone - see the module docstring.
REPAIRABLE = frozenset((
    "plan_construction", "read_model", "get_part_details", "get_set_details",
    "search_parts", "search_reference", "validate_model", "add_note",
    "move_submodel", "rotate_submodel", "assemble_model",
))

# Errors that no argument change can fix, so retrying is only a slower way to
# arrive at the same message.
_HOPELESS = (
    "is not available in this run",
    "unknown tool",
    "the user stopped the run",
)


def _text(result):
    """The error out of a tool result, or None if it was not an error."""
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except (ValueError, TypeError):
            return None
    if not isinstance(result, dict):
        return None
    error = result.get("error")
    return str(error) if error else None


def should_retry(name, result):
    """Whether this failure is worth another attempt."""
    if name not in REPAIRABLE:
        return False
    error = _text(result)
    if not error:
        return False
    return not any(phrase in error.lower() for phrase in _HOPELESS)


# -- deterministic repairs ---------------------------------------------------

def _hint(result):
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except (ValueError, TypeError):
            return ""
    return str((result or {}).get("hint") or "") if isinstance(result, dict) else ""


def _missing_path(error):
    """The path out of a 'no such file' error, or None."""
    lowered = error.lower()
    for marker in ("no such file:", "no such model:", "not found:"):
        if marker in lowered:
            index = lowered.index(marker) + len(marker)
            return error[index:].strip().strip("`'\"") or None
    return None


def _path_key(name, arguments):
    """Which argument of this tool holds the path that was wrong."""
    for key in ("model_path", "path", "source"):
        if key in arguments:
            return key
    return None


def _properties(schema):
    parameters = (schema or {}).get("parameters") or {}
    return parameters.get("properties") or {}, set(parameters.get("required") or ())


def _unexpected(error):
    """The argument name a TypeError complained about, or None."""
    marker = "unexpected keyword argument"
    if marker not in error:
        return None
    tail = error.split(marker, 1)[1].strip()
    return tail.split()[0].strip("'\" ,)") if tail else None


def rules(name, arguments, result, state=None, schema=None):
    """A corrected set of arguments from the error alone, or None.

    Only where there is exactly one sensible correction. Anything needing a
    judgement is left to ``ask_model``.
    """
    error = _text(result) or ""
    arguments = dict(arguments or {})

    missing = _missing_path(error)
    if missing:
        key = _path_key(name, arguments)
        if key:
            # The tool said so itself: this path is optional and leaving it out
            # is the documented fix.
            if "omit" in _hint(result).lower() and key != "path":
                arguments.pop(key, None)
                return arguments

            # A tool that needs a path gets the one this run is actually
            # building, which is nearly always what was meant.
            target = getattr(state, "target", None) or getattr(state, "current", lambda: None)()
            if target and target != arguments.get(key):
                arguments[key] = target
                return arguments

            if key != "path":
                arguments.pop(key, None)
                return arguments

    # An argument the tool does not take.
    #
    # Usually the builder used a near-miss name for a real parameter -
    # `read_model(path=...)` when the parameter is `source` - so the fix is to
    # *rename* it, not to drop it. Dropping was the first version of this rule
    # and it turned a wrong argument name into an empty call, which fails just
    # as reliably and tells nobody why.
    unexpected = _unexpected(error) if "bad arguments for" in error.lower() else None
    if unexpected and unexpected in arguments:
        properties, required = _properties(schema)
        missing = [key for key in required if key not in arguments]
        if missing:
            arguments[missing[0]] = arguments.pop(unexpected)
            return arguments
        # Nothing required is missing, so the argument really is surplus. The
        # tool's own default is what leaving it out would have given.
        if properties or not required:
            arguments.pop(unexpected)
            return arguments

    return None


# -- asking the model --------------------------------------------------------

_SYSTEM = """\
You fix one broken tool call.

You are given a tool's schema, the arguments it was called with, and the error \
it returned. Reply with the corrected arguments and nothing else.

Rules:
- **One JSON object**, the arguments only. No prose, no fence, no explanation. \
The first character is `{` and the last is `}`.
- **Change as little as possible.** Fix what the error names and leave every \
other argument exactly as it was.
- **Do not invent values.** If an argument refers to something that does not \
exist and the schema says it is optional, leave it out rather than guessing a \
replacement.
- If the error cannot be fixed by changing arguments, reply with `{}`.\
"""


def _schema_of(name, tools):
    for tool in tools or ():
        function = tool.get("function") or {}
        if function.get("name") == name:
            return function
    return None


def ask_model(name, arguments, result, llm, tools=None):
    """Corrected arguments from the model, or None.

    Toolless and short. Anything that is not a JSON object comes back as None,
    so a model that answers in prose costs one call and changes nothing.
    """
    if llm is None:
        return None
    schema = _schema_of(name, tools)
    if schema is None:
        return None

    body = (f"Tool: {name}\n\n"
            f"Schema:\n{json.dumps(schema, ensure_ascii=False)}\n\n"
            f"Arguments that failed:\n"
            f"{json.dumps(arguments, ensure_ascii=False, default=str)}\n\n"
            f"Error:\n{json.dumps(result, ensure_ascii=False, default=str) if not isinstance(result, str) else result}\n\n"
            f"Corrected arguments:")

    try:
        reply = llm.complete(
            [{"role": "system", "content": _SYSTEM},
             {"role": "user", "content": body}],
            task="chat")
    except Exception:
        return None

    text = (getattr(reply, "content", "") or "").strip()
    if not text:
        return None

    from .blueprint import extract_json

    fixed = extract_json(text)
    if not isinstance(fixed, dict) or not fixed:
        return None
    return fixed


def _usable(fixed, arguments, schema):
    """Whether a proposed repair is worth spending a call on.

    A repair that drops a required argument, or empties the call altogether, is
    not a correction - it is a different way to fail, and running it costs a
    call to be told so.
    """
    if not isinstance(fixed, dict) or fixed == arguments:
        return False
    if arguments and not fixed:
        return False
    _, required = _properties(schema)
    return all(key in fixed for key in required)


def repair(name, arguments, result, state=None, llm=None, tools=None):
    """Corrected arguments for a failed call, or None if there is no repair.

    Rules first because they are free and exact; the model only for what they
    do not cover.
    """
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments or "{}")
        except ValueError:
            arguments = {}
    arguments = dict(arguments or {})
    schema = _schema_of(name, tools)

    fixed = rules(name, arguments, result, state, schema)
    if _usable(fixed, arguments, schema):
        return fixed

    fixed = ask_model(name, arguments, result, llm, tools)
    if _usable(fixed, arguments, schema):
        return fixed
    return None


def note(result, attempts, original, final):
    """Mark a result that only succeeded after the call was corrected.

    The builder has to know its own call was wrong, or it writes the next one
    the same way and learns nothing from a run that looked like it worked.
    """
    if isinstance(result, str):
        try:
            payload = json.loads(result)
        except ValueError:
            return result
    else:
        payload = result
    if not isinstance(payload, dict):
        return result

    changed = sorted(k for k in set(original) | set(final)
                     if original.get(k) != final.get(k))
    payload["retried"] = {
        "attempts": attempts,
        "corrected": changed,
        "called_with": final,
        "note": ("your first call failed and was corrected for you - make the "
                 "next one this way round"),
    }
    return json.dumps(payload, ensure_ascii=False, default=str)
