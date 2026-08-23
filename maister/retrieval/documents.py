"""Turns catalogue rows into the text that gets embedded.

Embedding quality is mostly decided here. Two rules shape these templates:

* **Spell out what the CSV encodes tersely.** "Brick  2 x  4" tells the model
  little; "Brick 2 x 4 - a brick, 2 x 4 studs, 1 brick tall, studs on top" is
  something a natural-language query can actually land on.
* **Include the vocabulary a user would use, not the vocabulary the file uses.**
  Sets get their theme and era in words ("Star Wars", "from 2002") because
  queries look like "a big grey spaceship", not like "theme=Star Wars".

Both the payload stored in the database and the text handed to the reranker come
from here, so a template change means rebuilding the index - the manifest records
:data:`TEMPLATE_VERSION` for exactly that reason.
"""

TEMPLATE_VERSION = 2

_KIND_WORDS = {
    "brick": "a brick, 1 brick tall (24 LDU)",
    "plate": "a plate, 1 plate tall (8 LDU)",
    "other": "",
}


def _clean(text):
    return " ".join((text or "").split())


def part_document(row, geometry, themes=None):
    """The embedded text for one catalogue part."""
    parts = [_clean(row.get("description"))]

    category = _clean(row.get("category"))
    if category:
        parts.append(f"category: {category}")

    if geometry:
        width, depth = geometry.get("width_studs"), geometry.get("depth_studs")
        if width and depth:
            parts.append(f"footprint {width} x {depth} studs")
        kind = _KIND_WORDS.get(geometry.get("kind"), "")
        if kind:
            parts.append(kind)
        parts.append("has studs on top" if geometry.get("has_top_studs")
                     else "no studs on top, nothing can attach above it")

    keywords = _clean(row.get("keywords"))
    if keywords:
        # keywords carry marketplace ids ("Bricklink 3001") that add nothing
        # semantic and crowd out the real words
        useful = [k.strip() for k in keywords.split(",")
                  if k.strip() and not any(c.isdigit() for c in k)]
        if useful:
            parts.append("also known as: " + ", ".join(useful[:6]))

    if themes:
        parts.append("used in " + ", ".join(t for t, _ in themes[:3]) + " sets")

    try:
        uses = int(row.get("total_uses") or 0)
    except ValueError:
        uses = 0
    if uses >= 500:
        parts.append("very common part")
    elif uses <= 5:
        parts.append("rare part")

    return ". ".join(p for p in parts if p)


def creation_document(record):
    """The embedded text for one model the agent built and saved."""
    parts = [_clean(record.get("name"))]

    description = _clean(record.get("description"))
    if description:
        parts.append(description)

    tags = [t for t in (record.get("tags") or []) if t]
    if tags:
        parts.append("tags: " + ", ".join(tags))

    pieces = record.get("total_pieces") or 0
    if pieces:
        parts.append(f"{pieces} pieces")

    # Whether it validated is the single most useful thing to know about an
    # agent-built model, so it belongs in the retrievable text, not just the
    # metadata - "a working model of a car" should not surface a broken one.
    if record.get("validated"):
        parts.append("validated, every part on the stud grid")
    elif record.get("validated") is False:
        parts.append("did not pass validation, has misaligned parts")

    return ". ".join(p for p in parts if p)


def note_document(record):
    """The embedded text for one thing the agent learned.

    The subject is spelled out rather than left as an id, so a search for
    "trees" can reach a note filed under ``part:3062b``.
    """
    text = _clean(record.get("text"))
    subject_type = record.get("subject_type")
    subject_id = _clean(record.get("subject_id"))

    if subject_type == "general":
        return text
    if subject_type == "part":
        return f"About LEGO part {subject_id}: {text}"
    if subject_type == "set":
        return f"About LEGO set {subject_id}: {text}"
    return f"About the model '{subject_id}': {text}"


def set_document(row):
    """The embedded text for one official model."""
    parts = [_clean(row.get("set_name"))]

    theme = _clean(row.get("theme"))
    if theme:
        parts.append(f"{theme} theme")

    year = _clean(row.get("year"))
    if year:
        parts.append(f"released in {year}")

    pieces = row.get("total_pieces") or 0
    if pieces:
        parts.append(f"{pieces} pieces")
        if pieces < 100:
            parts.append("a small model")
        elif pieces > 1000:
            parts.append("a large detailed model")

    parts.append(f"LEGO set {_clean(row.get('set_number'))}")
    return ". ".join(p for p in parts if p)
