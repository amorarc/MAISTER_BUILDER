"""What a stopped run had got through, kept so the next one can carry on.

Stop is not cancel. A scene of four objects that is stopped after the third has
three finished models on disk and a decomposition, a design brief and a plan
behind each of them - and until now the next run threw all of that away, split
the request again from scratch, and rebuilt what was already built.

So a stopped run writes down where it got to. The snapshot holds:

* the petition it was working on, so a later run can tell whether it is the
  same job;
* every subconstruction with its status and its file, so the ones already
  finished are not built twice;
* the design brief settled for each of them, so the look stays the same across
  the join rather than being re-decided halfway through a scene;
* the conversation so far, which is what gives a resumed builder its continuity.

# It is invisible

Nobody is asked whether to resume. The file is written when a run stops, read
when the next run in that project turns out to be the same job, and deleted the
moment a run finishes properly. A user who stops a build and sends the same
request again simply finds it faster; a user who stops and asks for something
else gets a clean start, because the snapshot is dropped rather than applied to
a job it was not about.

# It expires

A snapshot older than ``MAX_AGE_DAYS`` is ignored and removed. Picking up a
fortnight-old half-build is not resuming, it is surprising someone with work
they had forgotten about, and the model file has probably moved on without it.
"""

import json
import os
import time
from pathlib import Path

from .config import OUT_DIR

MAX_AGE_DAYS = 14

# What a user types when they mean "carry on" rather than a new request. A
# resumed run needs no ceremony, but it does need to be able to tell the
# difference between "keep going" and a fresh instruction.
CONTINUATIONS = (
    "", "continue", "carry on", "keep going", "go on", "resume", "finish it",
    "continua", "continuar", "sigue", "seguir",
)


def path(project):
    name = "".join(c for c in str(project or "") if c.isalnum() or c in "-_")
    return OUT_DIR / "projects" / name / "resume.json"


def save(project, petition, subconstructions=None, briefs=None, history=None,
         note=None):
    """Write down where a stopped run got to. Never raises.

    Best effort throughout: a snapshot that cannot be written costs the next run
    a fresh start, which is exactly what used to happen every time, so there is
    nothing here worth failing a run over.
    """
    if not project:
        return None
    try:
        record = {
            "petition": petition or "",
            "at": time.time(),
            "subconstructions": [dict(s) for s in (subconstructions or [])],
            "briefs": dict(briefs or {}),
            "history": history or "",
            "note": note or "",
        }
        target = path(project)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_suffix(".json.tmp")
        temp.write_text(json.dumps(record, indent=1, ensure_ascii=False,
                                   default=str), encoding="utf-8")
        os.replace(temp, target)
        return record
    except (OSError, TypeError, ValueError):
        return None


def load(project):
    """The snapshot for this project, or None. Expired ones are removed."""
    target = path(project)
    if not target.is_file():
        return None
    try:
        record = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        clear(project)
        return None
    if not isinstance(record, dict):
        clear(project)
        return None
    age = time.time() - float(record.get("at") or 0)
    if age > MAX_AGE_DAYS * 86400:
        clear(project)
        return None
    return record


def clear(project):
    """Drop the snapshot. Called when a run finishes properly."""
    try:
        path(project).unlink(missing_ok=True)
    except OSError:
        pass


def _normalise(text):
    return " ".join(str(text or "").lower().split())


def is_continuation(message):
    """Whether this message means 'carry on' rather than a new request."""
    return _normalise(message) in CONTINUATIONS


def matches(record, petition):
    """Whether ``record`` is a snapshot of the job ``petition`` describes.

    Deliberately strict. Applying a half-built scene to a request it was not
    about would silently skip work the user is now asking for, which is a far
    worse failure than starting again - so it is the same words, or an explicit
    "carry on", and nothing else.
    """
    if not record:
        return False
    if is_continuation(petition):
        return True
    return _normalise(record.get("petition")) == _normalise(petition)


def finished(record):
    """Names of the subconstructions this snapshot already has built."""
    done = set()
    for entry in record.get("subconstructions") or []:
        if entry.get("status") != "done" or entry.get("unbuildable"):
            continue
        target = entry.get("path")
        if target and (OUT_DIR / target).is_file():
            done.add(entry.get("name"))
    return done
