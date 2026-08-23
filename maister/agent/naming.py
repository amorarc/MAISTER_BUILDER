"""A name for a project, taken from what was actually built in it.

A project starts life as "Untitled", because at the moment it is created nobody
knows what it will become - not the user, who has only typed a request, and not
the app. The first build is the first time there is something to name, so the
model that built it names it.

Only the default name is ever replaced. A name the user typed is theirs.
"""

import re

MAX_TITLE = 42

SYSTEM = (
    "You name LEGO models.\n\n"
    "Reply with a name for the model described below and nothing else: two to "
    "five words, no quotes, no file extension, no full stop, no preamble.\n\n"
    "Name it the way someone labels a drawer they will open again - what the "
    "thing is, and the one detail that tells it apart from the others. "
    "'Red pickup truck'. 'Small castle tower'. 'Yellow biplane'.\n\n"
    "Not 'Model'. Not 'LEGO build'. Not a sentence about what you did."
)

# The default name, and the numbered variants a few of them would collect.
_UNTITLED = re.compile(r"untitled(\s*\(?\d+\)?)?$", re.IGNORECASE)

# "Name: Red pickup truck", "Title - Red pickup truck"
_LABEL = re.compile(r"^\s*(name|title)\s*[:\-\u2014]\s*", re.IGNORECASE)

# Quotes, markdown emphasis, list dashes and closing punctuation, at either end.
_EDGES = re.compile(r"""^[\s"'`*_\-\u2014.,;:!]+|[\s"'`*_\-\u2014.,;:!]+$""")

_EXTENSION = re.compile(r"\.(ldr|mpd|dat)$", re.IGNORECASE)


def needs_title(name):
    """True for a name nobody chose: the default, and nothing else."""
    return bool(_UNTITLED.fullmatch((name or "").strip()))


def title_for(request, built=None, context=None, llm=None):
    """A short name for what is being built, or None if one cannot be had.

    ``context`` is what the run has worked out about the model - the reference
    picture as it was described, or the construction plan. A request is what
    someone typed in a hurry; the description and the plan are what the thing
    actually is, and they name it far better. "Build this" names nothing on its
    own, and names a red two-storey cottage perfectly well once the picture has
    been read.

    Never raises. A project that keeps the name "Untitled" is a small
    disappointment; a build that fails because naming it did not work would be
    a much larger one.
    """
    if llm is None:
        return None

    body = f"The user asked for: {request}"
    if context:
        body += f"\n\n{' '.join(str(context).split())[:900]}"
    if built:
        # what the builder said it made, which is closer to the truth than the
        # request when the two came out different
        body += f"\n\nThe builder reported: {' '.join(str(built).split())[:600]}"
    body += "\n\nName it."

    try:
        reply = llm.complete([{"role": "system", "content": SYSTEM},
                              {"role": "user", "content": body}])
    except Exception:
        return None

    if getattr(reply, "stopped", False):
        return None
    return clean(getattr(reply, "content", ""))


# How long a catalogue entry may run. Long enough to say how a thing is built,
# short enough that a search returning eight of them is still readable.
MAX_DESCRIPTION = 700

DESCRIBING = (
    "You write the catalogue entry for a LEGO model someone has just decided "
    "to keep.\n\n"
    "It goes in a library they search months later, having forgotten the model "
    "existed, by describing the problem they are trying to solve. So write "
    "what this model would be a good starting point for, and how it is "
    "actually built - the techniques, the parts doing the real work, the "
    "proportions. Not a retelling of the conversation, and not praise.\n\n"
    "Two to four sentences of plain prose. No markdown, no heading, no "
    "preamble, no quotes. Say what the thing is in the first few words."
)


def description_for(title=None, conversation=None, facts=None, model=None,
                    llm=None):
    """A catalogue entry for a finished model, or None if one cannot be had.

    Written from everything the project knows about itself - what it ended up
    being called, the whole conversation that produced it, and the model file's
    own arithmetic - because the description is what the library is searched
    on, and a description that only repeats the title makes the library
    unsearchable exactly when it has grown big enough to need searching.

    Never raises. Saving a model with no description is a small loss; a save
    button that fails because the description could not be written would be a
    much larger one.
    """
    if llm is None:
        return None

    body = []
    if title:
        body.append(f"The model is called: {title}")
    if facts:
        body.append(f"What the file contains: {' '.join(str(facts).split())[:700]}")
    if model:
        body.append(f"The model file begins:\n{str(model)[:1200]}")
    if conversation:
        # Last, and the largest share: it is the only part that says what the
        # user was actually after and what had to be worked out on the way.
        body.append(f"The conversation that built it:\n"
                    f"{str(conversation)[-4000:]}")
    if not body:
        return None
    body.append("Write the catalogue entry.")

    try:
        reply = llm.complete([{"role": "system", "content": DESCRIBING},
                              {"role": "user", "content": "\n\n".join(body)}])
    except Exception:
        return None
    if getattr(reply, "stopped", False):
        return None

    text = " ".join(str(getattr(reply, "content", "") or "").split())
    text = _EDGES.sub("", _LABEL.sub("", text))
    if not text:
        return None
    return text[:MAX_DESCRIPTION].strip()


def clean(text):
    """The one line of a reply that is a name, or None."""
    line = " ".join((text or "").split())
    if not line:
        return None

    # Twice around: the extension can only be found once the quotes and
    # emphasis around it are gone, and taking it off can expose more of them.
    line = _EDGES.sub("", _LABEL.sub("", line))
    line = _EDGES.sub("", _EXTENSION.sub("", line))

    # A model that ignored the instruction and wrote a sentence has not given a
    # name, and half a sentence is not one either.
    if not line or len(line) > MAX_TITLE or needs_title(line):
        return None
    return line
