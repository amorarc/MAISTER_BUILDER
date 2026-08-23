"""Reference images: the picture the user wants the model to look like.

A text request says what to build. A picture says what it should *be* - the
proportions, the colour scheme, the arrangement of the parts, all the things a
sentence leaves out and a builder would otherwise invent. So a reference image
is not decoration on the request: it is the specification, and it outranks the
builder's own taste wherever the two disagree.

Two things happen with one:

* It is **described**, once, in detail - by a vision model, since the builder
  cannot see it. That description travels with the request from then on.
* It is **compared** against the renders of what was actually built, which is
  the only check that can tell whether the model resembles the thing that was
  asked for. Validation says a build is buildable; the description says what was
  wanted; only the comparison says whether one became the other.

Images live with the project rather than with the run. A reference given once
is still the reference three requests later - "make it taller" means taller and
*still like the picture*.

There can be several, up to ``MAX_IMAGES``, and **all of them are the
specification**. One photograph of a car shows you the side of it and nothing
else; the second shows the front, and the third the thing the first two left
out. So the pictures are read *together*, in one pass, and what comes back is
one description of one build rather than four descriptions of four. That
description is stored on every record, which is what lets any one of them
answer "what is this project's reference" without the rest of the pipeline
having to know how many there were.
"""

import json
import re
import time
import uuid
from pathlib import Path

from .config import OUT_DIR

# Kept beside the model file, inside the project, so a project is still one
# directory you can copy.
REFERENCE_DIRNAME = "reference"
INDEX_NAME = "references.json"

# What a browser will hand us from a file picker or a clipboard paste.
SUFFIXES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
}
MAX_BYTES = 12_000_000
# How many pictures one project may hold. The cap is not about disk: every one
# of them goes into every vision call that reads the reference, so a fifth is a
# fifth of the picture budget spent on each description, each comparison and
# each set of questions for the rest of the project's life.
MAX_IMAGES = 4
# Questions kept per image. Long past what a run can ask; the cap is only there
# so a project worked on for weeks does not grow an index file without end.
MAX_QA = 60
# Anything larger is downscaled before it is stored: a 12MP phone photo costs a
# vision call dearly in tokens and tells it nothing a 1600px version does not.
MAX_EDGE = 1600

# --------------------------------------------------------------------------
# The colours the chip is moulded out of
#
# The UI draws a reference as a brick moulded from the picture: each stud takes
# the average of the pixels above it, and the lip under it the average of the
# pixels it stands on. That is two rows of colour, and it is a property of the
# picture - it cannot change once the file is written.
#
# It is measured here, on the way in, and kept in the record. It used to be
# measured in the browser instead, by fetching the image back and averaging it
# on a canvas: one extra cross-origin request per chip, redone on every page
# load, and silently falling back to grey whenever the request lost a race with
# the cached copy the visible <img> had already put there. A number that never
# changes belongs in the record beside the description, not in a canvas.
# --------------------------------------------------------------------------

# Columns sampled across each edge. More than a chip ever has studs, so the
# browser can average down to whatever count it measures.
EDGE_SAMPLES = 24
# How far into the picture still counts as "at the edge".
EDGE_BAND = 0.16


def project_root(project_id, projects_dir=None):
    base = Path(projects_dir) if projects_dir else (OUT_DIR / "projects")
    return base / project_id


def reference_dir(project_id, projects_dir=None):
    return project_root(project_id, projects_dir) / REFERENCE_DIRNAME


def _index_path(project_id, projects_dir=None):
    return reference_dir(project_id, projects_dir) / INDEX_NAME


def load(project_id, projects_dir=None):
    """Every reference image recorded for a project, oldest first."""
    path = _index_path(project_id, projects_dir)
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict)]


def _save_index(project_id, rows, projects_dir=None):
    path = _index_path(project_id, projects_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")


def latest(project_id, projects_dir=None):
    """The reference in force, which is the most recent one added."""
    rows = load(project_id, projects_dir)
    return rows[-1] if rows else None


def active(project_id, projects_dir=None):
    """Every picture in force for a project, oldest first.

    All of them, not the last one. A user who attaches four pictures has told
    you four things about what they want built, and reading only the fourth
    throws three of them away - which is what happened before this existed.
    Capped in case an index written by an older version holds more.
    """
    return load(project_id, projects_dir)[-MAX_IMAGES:]


def _ids(image_id):
    """One image id, or several, as a list."""
    if image_id is None:
        return []
    if isinstance(image_id, (str, bytes)):
        return [str(image_id)]
    return [str(i) for i in image_id]


def paths(records, project_id=None, projects_dir=None):
    """Where a set of records sits on disk, skipping any that has gone."""
    found = [image_path(r, project_id, projects_dir) for r in (records or ())]
    return [p for p in found if p is not None]


def described(records):
    """The description in force across these pictures, or None.

    None when any of them has not been described, and - the case that matters -
    when what is stored was written from a *different* set of pictures. A fifth
    photograph attached after the fact changes the specification, and a
    description of the other four is no longer it, so this asks for the reading
    to be done again rather than handing back one that is out of date.

    A picture described before descriptions were shared has no ``describes``,
    which for a project with one picture is exactly the set it was written
    from. Those stay cached rather than being re-read at a vision call's cost.
    """
    rows = [r for r in (records or ()) if isinstance(r, dict)]
    if not rows:
        return None
    covered = sorted(str(r.get("image_id")) for r in rows)
    for row in rows:
        if not row.get("description"):
            return None
        wrote_from = row.get("describes") or [row.get("image_id")]
        if sorted(str(i) for i in wrote_from) != covered:
            return None
    return rows[0]["description"]


def resolve(project_id, image_id=None, projects_dir=None):
    """One recorded reference by id, or the latest. None if there is none."""
    rows = load(project_id, projects_dir)
    if not rows:
        return None
    if not image_id:
        return rows[-1]
    wanted = str(image_id).strip().lower()
    for row in rows:
        if (row.get("image_id") or "").lower() == wanted:
            return row
        if (row.get("file") or "").lower() == wanted:
            return row
    return None


def image_path(record, project_id=None, projects_dir=None):
    """Where a recorded reference actually sits on disk."""
    if record is None:
        return None
    stored = record.get("path")
    if stored:
        candidate = Path(stored)
        if not candidate.is_absolute():
            candidate = OUT_DIR / candidate
        if candidate.is_file():
            return candidate
    if project_id and record.get("file"):
        candidate = reference_dir(project_id, projects_dir) / record["file"]
        if candidate.is_file():
            return candidate
    return None


def _safe_name(name):
    stem = re.sub(r"[^\w.\- ]+", "_", str(name or "")).strip(". ")
    return stem[:80] or "image"


def edge_colours(path):
    """The two edge bands of a picture, as rows of ``[r, g, b]``.

    ``{"top": [...], "bottom": [...]}``, ``EDGE_SAMPLES`` columns each, or None
    if the file cannot be read - an unmeasured chip falls back to the frame's
    grey, which is what it looked like before any of this.

    Sampled from the same square the chip shows. The visible image is
    ``object-fit: cover`` inside a square frame, so it is centre-cropped to its
    short edge; measuring the whole picture instead would paint the brick from
    pixels the user cannot see. Each band is then resized to a single row, which
    *is* the averaging: with ``BOX`` every pixel that comes out is the mean of
    the strip of picture behind it.
    """
    try:
        from PIL import Image

        with Image.open(path) as image:
            image.load()
            image = image.convert("RGB")
            side = min(image.size)
            left = (image.width - side) // 2
            top = (image.height - side) // 2
            band = max(1, int(side * EDGE_BAND))

            def row(upper):
                strip = image.crop((left, upper, left + side, upper + band))
                strip = strip.resize((EDGE_SAMPLES, 1), Image.BOX)
                return [list(px) for px in strip.getdata()]

            return {"top": row(top), "bottom": row(top + side - band)}
    except Exception:
        return None


def ensure_edges(project_id, projects_dir=None):
    """Measure any reference recorded before the colours were kept.

    Old records have no ``edges`` key, and re-uploading a picture to get a
    coloured brick is not a thing anyone should have to do. Measured once here
    and written back, so this costs a project one pass and never runs again.
    """
    rows = load(project_id, projects_dir)
    changed = False
    for row in rows:
        if row.get("edges") is not None:
            continue
        path = image_path(row, project_id, projects_dir)
        if path is None:
            continue
        edges = edge_colours(path)
        if edges is None:
            continue
        row["edges"] = edges
        changed = True
    if changed:
        _save_index(project_id, rows, projects_dir)
    return rows


def add(project_id, data, content_type=None, filename=None, projects_dir=None):
    """Store an image against a project. Returns its record.

    Big images are downscaled on the way in. The point of the picture is its
    composition and its colours, and neither needs twelve megapixels - but a
    vision call is billed by them.
    """
    if not data:
        raise ValueError("the image is empty")
    if len(data) > MAX_BYTES:
        raise ValueError(f"image too large (max {MAX_BYTES // 1_000_000} MB)")
    if len(load(project_id, projects_dir)) >= MAX_IMAGES:
        raise ValueError(f"a project takes at most {MAX_IMAGES} reference "
                         f"images - remove one before attaching another")

    suffix = SUFFIXES.get((content_type or "").lower().split(";")[0].strip())
    if suffix is None and filename:
        ext = Path(filename).suffix.lower()
        suffix = ext if ext in set(SUFFIXES.values()) | {".jpeg"} else None
    if suffix is None:
        raise ValueError("that is not an image format this accepts "
                         "(png, jpeg, webp, gif or bmp)")
    suffix = ".jpg" if suffix == ".jpeg" else suffix

    image_id = uuid.uuid4().hex[:12]
    folder = reference_dir(project_id, projects_dir)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{image_id}{suffix}"
    target.write_bytes(data)

    width = height = None
    try:
        from PIL import Image

        with Image.open(target) as image:
            image.load()
            width, height = image.size
            if max(image.size) > MAX_EDGE:
                image.thumbnail((MAX_EDGE, MAX_EDGE), Image.LANCZOS)
                converted = image.convert("RGB") if suffix in (".jpg",) else image
                converted.save(target)
                width, height = converted.size
    except Exception as exc:
        # A file Pillow cannot open is not an image, whatever it claimed to be.
        target.unlink(missing_ok=True)
        raise ValueError(f"that image could not be read ({exc})") from None

    record = {
        "image_id": image_id,
        "file": target.name,
        "path": str(target.relative_to(OUT_DIR)) if OUT_DIR in target.parents
                else str(target),
        "original_name": _safe_name(filename) if filename else None,
        "width": width,
        "height": height,
        "bytes": target.stat().st_size,
        "added_at": time.time(),
        # Filled in the first time describe_image runs, and reused afterwards:
        # the description of a picture does not change, and it is a vision call.
        "description": None,
        # The colours the chip is moulded out of, measured off the stored file
        # so they match the picture after any downscale above.
        "edges": edge_colours(target),
    }

    rows = load(project_id, projects_dir)
    rows.append(record)
    _save_index(project_id, rows, projects_dir)
    return record


def set_description(project_id, image_id, description, projects_dir=None):
    """Remember what a picture - or a set of them - was described as.

    ``image_id`` may be one id or several. A description written from four
    pictures at once belongs to all four, and it is stored on each: any record
    then answers "what is this project's reference", so nothing downstream has
    to know how many pictures there were or which of them to look in.

    ``describes`` records the set it was written from. That is what lets
    ``described`` tell a current description from one that covered a picture
    that has since been removed, or missed one that has since been added.
    """
    wanted = set(_ids(image_id))
    rows = load(project_id, projects_dir)
    covered = sorted(wanted)
    touched = []
    for row in rows:
        if row.get("image_id") in wanted:
            row["description"] = description
            row["describes"] = covered
            touched.append(row)
    if touched:
        _save_index(project_id, rows, projects_dir)
    return touched


def add_qa(project_id, image_id, entries, projects_dir=None):
    """Remember questions that were asked about a picture, and their answers.

    Kept with the image rather than with the run for the same reason the
    description is: a picture does not change, so an answer about it does not
    either. Each subbuild gets a fresh agent with no memory, and every one of
    them is building against this picture - the second should not have to spend
    its one set of questions rediscovering what the first was told.

    ``image_id`` may be one id or several. A question is put to all the
    project's pictures at once and the answer is about all of them, so it is
    written to each of them, the same way a description is.
    """
    wanted = set(_ids(image_id))
    rows = load(project_id, projects_dir)
    entries = [e for e in (entries or []) if isinstance(e, dict) and e.get("question")]
    if not entries:
        return None
    touched = []
    for row in rows:
        if row.get("image_id") not in wanted:
            continue
        kept = [e for e in (row.get("qa") or []) if isinstance(e, dict)]
        known = {normalize_question(e.get("question")) for e in kept}
        kept += [e for e in entries if normalize_question(e["question"]) not in known]
        row["qa"] = kept[-MAX_QA:]
        touched.append(row)
    if touched:
        _save_index(project_id, rows, projects_dir)
    return touched


def answered(record):
    """Question -> entry, for everything already asked about this picture."""
    if not record:
        return {}
    return {normalize_question(e.get("question")): e
            for e in (record.get("qa") or []) if isinstance(e, dict)}


def normalize_question(question):
    """A question as a comparison key: wording is not the question."""
    text = " ".join(str(question or "").lower().split())
    return text.rstrip("?. ")


def delete(project_id, image_id, projects_dir=None):
    rows = load(project_id, projects_dir)
    kept, removed = [], None
    for row in rows:
        if row.get("image_id") == image_id and removed is None:
            removed = row
            continue
        kept.append(row)
    if removed is None:
        return None
    path = image_path(removed, project_id, projects_dir)
    if path:
        Path(path).unlink(missing_ok=True)
    _save_index(project_id, kept, projects_dir)
    return removed


def summarize(record):
    """The parts of a record worth showing, without the file's guts."""
    if not record:
        return None
    return {
        "image_id": record.get("image_id"),
        "file": record.get("file"),
        "original_name": record.get("original_name"),
        "width": record.get("width"),
        "height": record.get("height"),
        "described": bool(record.get("description")),
        "questions_answered": len(record.get("qa") or []),
        # Two rows of [r, g, b] - how the chip paints its studs and its lip.
        # Sent with the record precisely so the browser never has to fetch the
        # picture a second time to work them out for itself.
        "edges": record.get("edges"),
    }
