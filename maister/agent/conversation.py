"""The chat, kept next to the model it is about.

The conversation used to live in the browser's localStorage, as one blob
holding every project's thread at once, and it broke in both of the ways that
arrangement always breaks. Two windows each read that blob once at startup and
each wrote the whole of it back, so whichever typed second erased what the
other had said. And the blob carried the tool-call rows for every message, so a
few real builds took it past the storage quota, at which point the write failed
silently and nothing was kept at all - close the tab, come back, and the
conversation was gone.

Meanwhile the backend kept its *own* record of the same conversation in a dict,
purely so a build could be told what had been asked before, and that one went
down with the process. So there were two records of one conversation, neither
of them durable, and the model's memory and the user's transcript could
disagree about what had been said.

There is one record now and it is this file, written where the model file and
the traces are written. The server owns it: it appends the request when the
request arrives and the reply when the run settles, which means a reply is kept
even if the tab that asked for it was closed before it came back. The browser
reads. Two windows showing one project therefore show the same conversation,
and so does a window opened tomorrow.

What is stored per assistant turn is the text, the run it came from, and the
handful of events the transcript actually draws - the step markers, the tool
calls and their one-line summaries. Not the whole event stream: that is what
the trace is for, and the run id here is the way back to it.
"""

import json
import os
import threading
import time

from .config import OUT_DIR

PROJECTS_DIR = OUT_DIR / "projects"
FILENAME = "chat.json"

# How many turns a project keeps. Long enough that scrolling back is useful,
# short enough that the file stays something a browser can load at once.
MAX_MESSAGES = 240

# The longest a single message body may be. A model that answers with an entire
# .ldr file has said something worth keeping, but not worth keeping whole.
MAX_TEXT = 20_000

# Events the transcript draws. Everything else a run emits - the token deltas,
# the sub-build bookkeeping - is either already folded into these or belongs
# only to the trace.
DISPLAY_EVENTS = frozenset((
    "step", "planning", "plan", "text", "tool_start", "tool_end",
    "renamed", "error",
))

# Tool rows kept per reply. A run that made four hundred calls is a run whose
# transcript nobody reads to the end, and the trace has all of them anyway.
MAX_EVENTS = 120

_locks = {}
_guard = threading.Lock()


def _lock(project):
    with _guard:
        return _locks.setdefault(str(project), threading.Lock())


def _path(project):
    return PROJECTS_DIR / str(project) / FILENAME


def _clip(text, limit=MAX_TEXT):
    text = str(text or "")
    return text if len(text) <= limit else text[:limit] + "\n… [truncated]"


def _read(project):
    """The raw list on disk, without locking. Callers hold the lock."""
    try:
        data = json.loads(_path(project).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    messages = data.get("messages") if isinstance(data, dict) else data
    return [m for m in (messages or []) if isinstance(m, dict)]


def _write(project, messages):
    """Replace the file, atomically - a torn chat.json loses the lot."""
    path = _path(project)
    body = json.dumps({"messages": messages}, ensure_ascii=False, indent=1,
                      default=str)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".json.tmp")
        temp.write_text(body, encoding="utf-8")
        os.replace(temp, path)
    except OSError:
        return False
    return True


def _tidy(message):
    """One message, reduced to what is worth keeping and safe to store."""
    role = str(message.get("role") or "assistant")
    kept = {
        "role": role if role in ("user", "assistant", "error") else "assistant",
        "text": _clip(message.get("text")),
        "at": message.get("at") or time.time(),
    }
    for key in ("run_id", "steps", "warning", "renamed"):
        if message.get(key) not in (None, "", []):
            kept[key] = message[key]
    if message.get("images"):
        kept["images"] = message["images"][:8]

    events = [e for e in (message.get("events") or [])
              if isinstance(e, dict) and e.get("type") in DISPLAY_EVENTS]
    if events:
        # The tail, not the head: the end of a run is the part that explains
        # what it finally did.
        kept["events"] = events[-MAX_EVENTS:]
    return kept


def load(project):
    """Every message of this project's conversation, oldest first."""
    with _lock(project):
        return _read(project)


def append(project, message):
    """Add one message and return the conversation as it now stands."""
    if not isinstance(message, dict):
        return load(project)
    with _lock(project):
        messages = _read(project)
        messages.append(_tidy(message))
        del messages[:-MAX_MESSAGES]
        _write(project, messages)
        return messages


def replace(project, messages):
    """Set the whole conversation. Used once, to carry over an old browser copy."""
    tidied = [_tidy(m) for m in (messages or []) if isinstance(m, dict)]
    del tidied[:-MAX_MESSAGES]
    with _lock(project):
        _write(project, tidied)
        return tidied


def clear(project):
    """Start a new conversation: the file goes with it."""
    with _lock(project):
        try:
            _path(project).unlink(missing_ok=True)
        except OSError:
            pass
    return []


def turns(project, limit=None):
    """``(role, text)`` for the turns that carry words, newest last."""
    found = [(m.get("role"), (m.get("text") or "").strip())
             for m in load(project)
             if m.get("role") in ("user", "assistant") and (m.get("text") or "").strip()]
    return found[-limit:] if limit else found


def history_text(project, limit=None):
    """The conversation as the text a builder is handed, or None."""
    found = turns(project, limit)
    if not found:
        return None
    return "\n\n".join(f"{'User' if role == 'user' else 'You'}: {text[:2000]}"
                       for role, text in found)


def as_messages(project, limit=None):
    """The conversation in the shape an LLM takes, for reseeding an agent.

    A restarted server builds a fresh agent with an empty head. Without this it
    would answer "make it taller" having no idea what *it* is - the transcript
    on screen would show a conversation the model was not part of.
    """
    return [{"role": "user" if role == "user" else "assistant", "content": text}
            for role, text in turns(project, limit)]
