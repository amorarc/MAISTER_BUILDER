"""The agent's own model library.

Kept deliberately separate from ``data/ldraw_omr_sets`` - those are real sets
designed by people and shipped in boxes, these are models this agent built. They
are useful for different reasons and must never be confused: an official set is
evidence of how LEGO solves a problem, an agent creation is evidence of how
*this agent* solved one, which is only worth reusing if it validated cleanly.

``data/agent_creations/metadata.json`` is the source of truth; the vector index
in ``data/vector_db/creations`` is derived from it and can always be rebuilt.

A creation is addressed by its ``name``. Saving under an existing name updates
that creation rather than making a second copy, so an agent that improves a
model does not leave the broken draft behind to be found later.
"""

import json
import re
import shutil
import uuid
from datetime import datetime, timezone
from functools import lru_cache

from .config import (
    AGENT_CREATIONS_DIR,
    CREATIONS_METADATA,
    CREATIONS_MODELS_DIR,
)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slugify(text):
    text = re.sub(r"[^\w\s-]", "", (text or ""), flags=re.UNICODE)
    text = re.sub(r"[\s_]+", "-", text.strip())
    return re.sub(r"-{2,}", "-", text).strip("-").lower()[:80] or "untitled"


@lru_cache(maxsize=1)
def _cache():
    """Mutable list of records, read once and kept in step with the file."""
    if not CREATIONS_METADATA.is_file():
        return []
    try:
        return json.loads(CREATIONS_METADATA.read_text(encoding="utf-8"))
    except ValueError:
        return []


def load_creations():
    return list(_cache())


def _write(records):
    AGENT_CREATIONS_DIR.mkdir(parents=True, exist_ok=True)
    CREATIONS_METADATA.write_text(
        json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
    _cache.cache_clear()


def resolve(identifier):
    """A record by name or creation id, case-insensitively. None if unknown."""
    key = (identifier or "").strip().lower()
    if not key:
        return None
    for record in _cache():
        if record.get("creation_id", "").lower() == key:
            return record
    for record in _cache():
        if (record.get("name") or "").lower() == key:
            return record
    # a slug is what the file is named, so accept that spelling too
    for record in _cache():
        if slugify(record.get("name")) == slugify(key):
            return record
    return None


def model_path(record):
    return CREATIONS_MODELS_DIR / record["model_file"]


def count_pieces(path):
    """Total and unique piece counts, submodels expanded.

    Reuses the OMR downloader's counter so a creation is measured exactly the
    way an official set is - otherwise "180 pieces" would mean two different
    things depending on which library it came from.
    """
    from ..database_creation.download_ldraw_omr import count_pieces as _count

    counts = _count(path)
    return {"total_pieces": counts.total,
            "unique_pieces": counts.unique,
            "unique_pieces_by_color": counts.unique_by_color}


def save(source_path, name, description, tags=None, validation=None):
    """Copy a model into the library and record it. Returns the record."""
    CREATIONS_MODELS_DIR.mkdir(parents=True, exist_ok=True)

    existing = resolve(name)
    record = dict(existing) if existing else {
        "creation_id": uuid.uuid4().hex[:10],
        "created_at": _now(),
    }
    slug = slugify(name)
    record["model_file"] = f"{record['creation_id']}_{slug}{source_path.suffix or '.ldr'}"

    destination = CREATIONS_MODELS_DIR / record["model_file"]
    if existing and destination != model_path(existing):
        model_path(existing).unlink(missing_ok=True)  # the name, and so the slug, changed
    shutil.copyfile(source_path, destination)

    record.update({
        "name": name.strip(),
        "description": (description or "").strip(),
        "tags": [t.strip() for t in (tags or []) if t and t.strip()],
        "updated_at": _now(),
        **count_pieces(destination),
    })
    if validation is not None:
        record["validated"] = bool(validation.get("passed"))
        record["verdict"] = validation.get("verdict")

    records = [r for r in _cache() if r.get("creation_id") != record["creation_id"]]
    records.append(record)
    _write(records)
    return record


def delete(identifier):
    record = resolve(identifier)
    if record is None:
        return None
    model_path(record).unlink(missing_ok=True)
    _write([r for r in _cache() if r.get("creation_id") != record["creation_id"]])
    return record


def summarize(record):
    """The fields worth showing the agent."""
    return {
        "creation_id": record.get("creation_id"),
        "name": record.get("name"),
        "description": record.get("description"),
        "tags": record.get("tags") or [],
        "total_pieces": record.get("total_pieces"),
        "unique_pieces": record.get("unique_pieces"),
        "validated": record.get("validated"),
        "created_at": record.get("created_at"),
        "model_file": record.get("model_file"),
    }


def matches_filters(record, tag=None, validated_only=False,
                    min_pieces=None, max_pieces=None):
    if validated_only and not record.get("validated"):
        return False
    if tag and tag.lower() not in [t.lower() for t in (record.get("tags") or [])]:
        return False
    pieces = record.get("total_pieces") or 0
    if min_pieces is not None and pieces < min_pieces:
        return False
    if max_pieces is not None and pieces > max_pieces:
        return False
    return True
