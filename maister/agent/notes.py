"""What the agent has worked out and written down.

The parts catalogue says `3062b` is a *Brick 1 x 1 Round*. It does not say that
it stacks into a convincing tree trunk, that four of them make a decent chimney,
or that a build using more than a handful of them looks lumpy. That kind of fact
is discovered while building and is otherwise lost the moment the run ends.

A note is a short claim attached to a subject:

    part:3062b      "stacks into a good tree trunk, 4-6 tall"
    set:31009-1     "clean reference for a small house; roof is two rows of 3040"
    creation:oak    "the canopy technique here only works above 3 studs of trunk"
    general         "jumper plates are the only honest way to get a half-stud offset"

``data/agent_knowledge/notes.json`` is the source of truth; the vector index in
``data/vector_db/notes`` is derived and can be rebuilt from it.

Subjects are validated on write. A note attached to a part number that does not
exist is worse than no note - it is a confident-looking record of something the
agent hallucinated, and it will be retrieved and believed later.
"""

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from functools import lru_cache

from .config import AGENT_KNOWLEDGE_DIR, NOTES_FILE

SUBJECT_TYPES = ("part", "set", "creation", "general")

MAX_NOTE_CHARS = 600


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@lru_cache(maxsize=1)
def _cache():
    if not NOTES_FILE.is_file():
        return []
    try:
        return json.loads(NOTES_FILE.read_text(encoding="utf-8"))
    except ValueError:
        return []


def load_notes():
    return list(_cache())


# Notes are one file shared by every builder, and subconstructions now run at
# the same time. Read-add-write has to be one step or two notes written at once
# become one, and the write itself has to land whole or a reader gets a half
# file and reads it as no notes at all.
_write_lock = threading.RLock()


def _write(records):
    AGENT_KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    temp = NOTES_FILE.with_suffix(".json.tmp")
    temp.write_text(json.dumps(records, indent=2, ensure_ascii=False),
                    encoding="utf-8")
    os.replace(temp, NOTES_FILE)
    _cache.cache_clear()


def check_subject(subject_type, subject_id):
    """Validate a subject. Returns ``(canonical_id, error)``.

    ``general`` notes carry no id. Everything else must name something that
    actually exists in the catalogue, the set library or the agent's own work.
    """
    subject_type = (subject_type or "").strip().lower()
    if subject_type not in SUBJECT_TYPES:
        return None, (f"subject_type must be one of {', '.join(SUBJECT_TYPES)}, "
                      f"not '{subject_type}'")

    if subject_type == "general":
        return "", None

    subject_id = (subject_id or "").strip()
    if not subject_id:
        return None, f"a '{subject_type}' note needs a subject_id"

    if subject_type == "part":
        from . import catalog

        part = catalog.get_part(subject_id)
        if part is None:
            return None, (f"part '{subject_id}' is not in the catalogue; "
                          f"use search_parts to find the right number")
        return part["part_id"], None

    if subject_type == "set":
        from . import sets

        rows = sets.resolve(subject_id)
        if not rows:
            return None, (f"no official set '{subject_id}'; "
                          f"use search_sets to find the right number")
        return rows[0]["set_number"], None

    from . import creations

    record = creations.resolve(subject_id)
    if record is None:
        return None, (f"no saved creation '{subject_id}'; "
                      f"use search_creations to see what has been saved")
    return record["name"], None


def add(subject_type, subject_id, text):
    """Record one note. Returns ``(record, error)``."""
    text = " ".join((text or "").split())
    if not text:
        return None, "a note needs some text"
    if len(text) > MAX_NOTE_CHARS:
        return None, f"note too long ({len(text)} chars, max {MAX_NOTE_CHARS})"

    canonical, error = check_subject(subject_type, subject_id)
    if error:
        return None, error

    subject_type = subject_type.strip().lower()
    with _write_lock:
        return _add(subject_type, canonical, text)


def _add(subject_type, canonical, text):
    """The write itself, under the lock its caller holds."""
    records = list(_cache())

    # The same observation written twice is noise in every future search.
    for existing in records:
        if (existing["subject_type"] == subject_type
                and existing["subject_id"] == canonical
                and existing["text"].lower() == text.lower()):
            return existing, None

    record = {
        "note_id": uuid.uuid4().hex[:10],
        "subject_type": subject_type,
        "subject_id": canonical,
        "text": text,
        "created_at": _now(),
    }
    records.append(record)
    _write(records)
    return record, None


def delete(note_id):
    with _write_lock:
        records = _cache()
        remaining = [r for r in records if r.get("note_id") != note_id]
        if len(remaining) == len(records):
            return False
        _write(remaining)
        return True


def for_subject(subject_type, subject_id):
    """Every note on one subject, newest first. An exact lookup, no embedding."""
    subject_type = (subject_type or "").strip().lower()
    key = (subject_id or "").strip().lower()
    found = [r for r in _cache()
             if r["subject_type"] == subject_type
             and r["subject_id"].lower() == key]
    return sorted(found, key=lambda r: r.get("created_at") or "", reverse=True)


def summarize(record):
    return {
        "note_id": record.get("note_id"),
        "subject": _subject_label(record),
        "text": record.get("text"),
        "created_at": record.get("created_at"),
    }


def _subject_label(record):
    if record.get("subject_type") == "general":
        return "general"
    return f"{record.get('subject_type')}:{record.get('subject_id')}"
