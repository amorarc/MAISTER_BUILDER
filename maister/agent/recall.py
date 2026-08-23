"""What this agent already worked out, handed over before it starts again.

``refsets.py`` exists because the builder had the tools to go and find a real
set and, run after run, did not: four calls stand between "build a car" and
seeing how LEGO built one, and a model that believes it knows what a car looks
like spends them placing bricks. So the harness looks the sets up and puts them
in the task.

That reasoning was never about sets. It is about *anything the builder has to
decide to ask for*, and the two stores it applies to next are the ones this
project keeps of its own work:

* **creations** — models it built before and saved, with the LDraw of the ones
  that validated cleanly. A tree it already got right is the best possible
  answer to "build a tree", and it is sitting on disk being not looked at.
* **notes** — what it worked out while building and wrote down. That `3062b`
  stacks into a good trunk four to six tall; that a set is a clean reference
  for a small house. Facts discovered at cost, retrievable only by asking.

Neither has ever been pushed. Both are pulled, through `search_reference` and
through the notes folded into `get_part_details` — which is to say, both are
reached only by a builder that thought to reach, and the measured behaviour is
that it does not.

This is the same fix as ``refsets``, applied to the other two stores. No LLM
call and no network: it is the local vector index and a read off disk, and it
costs a few hundred milliseconds.

# What it is careful about

An agent creation is **not** an official set and must never be handed over as
though it were. A set is evidence of how LEGO solves a problem; a creation is
evidence of how this agent solved one, which is worth reusing only if it came
out right. So:

* **Only creations that validated.** An unvalidated one is a draft, and handing
  a draft over as a worked example teaches the mistake in it.
* **Only ones that are actually about the subject.** The relevance floor is the
  retrieval layer's own — a semantic search always answers, and the least-bad
  row is worse than nothing here for exactly the reason it is worse than
  nothing in ``refsets``: the builder has been told to start from what it is
  given.
* **Never raises.** Recall is a gift to the build, never a precondition.
"""

import re

from . import creations, notes

# How many past creations are opened per subconstruction. Two, for the same
# reason ``refsets`` shows two sets: one is a single opinion about what the
# subject looks like, and more source than that is more than anyone reads
# before starting.
MAX_CREATIONS = 2
# Lines of a creation's own LDraw shown. Its models are small — the library's
# median is under 40 parts — so this holds most of them whole.
SOURCE_LINES = 50
# Notes are one sentence each, so more of them fit than of anything else here.
MAX_NOTES = 6

# A creation below this is not a worked example, it is three bricks. The
# library genuinely holds some of those ("yellow 2x2 brick", 1 piece) and
# handing one over as prior art is noise.
MIN_PIECES = 8
# And one far larger than the thing being built is the same poor reference a
# 3,000-piece Technic set is: see refsets.MAX_PIECES.
MAX_PIECES = 400

_PART = re.compile(r"^1\s+\S+\s+\S+\s+\S+\s+\S+\s+(?:\S+\s+){9}(\S+?)(?:\.dat)?\s*$",
                   re.I)


def _retrieval():
    from ..retrieval import search

    return search


# -- creations ---------------------------------------------------------------

def find(subject, requirements=None, limit=MAX_CREATIONS,
         max_pieces=MAX_PIECES):
    """Models this agent built before that already solved something like this.

    Validated ones only, opened, with their source. Empty on anything at all
    going wrong.
    """
    query = " ".join(str(s) for s in (subject, requirements) if s).strip()
    if not query:
        return []

    try:
        hits = _retrieval().search_creations(
            query, validated_only=True, min_pieces=MIN_PIECES,
            max_pieces=max_pieces, max_results=max(3, limit * 2))
    except Exception:
        return []

    out = []
    for hit in hits:
        digest = _open(hit)
        if digest:
            out.append(digest)
        if len(out) >= limit:
            break
    return out


def _open(hit):
    """One creation, with the LDraw it actually came out as."""
    identifier = hit.get("creation_id") or hit.get("name")
    record = creations.resolve(identifier)
    if not record:
        return None
    record = record[0] if isinstance(record, list) else record
    if not record.get("validated"):
        # Belt and braces: the search filters on this, and a stale index entry
        # would otherwise slip a draft through as a worked example.
        return None

    try:
        text = creations.model_path(record).read_text(
            encoding="utf-8", errors="replace")
    except (OSError, AttributeError, TypeError):
        return None

    lines = [line for line in text.splitlines() if line.strip()]
    if _monotonous(text):
        return None
    digest = {
        "name": record.get("name"),
        "description": record.get("description"),
        "tags": record.get("tags") or [],
        "pieces": record.get("total_pieces"),
        "distinct_shapes": record.get("unique_pieces"),
        "lines": len(lines),
        "source": "\n".join(lines[:SOURCE_LINES]),
        "clipped": len(lines) > SOURCE_LINES,
    }
    digest["legend"] = _legend(lines)
    digest["notes"] = [n.get("text") for n
                       in notes.for_subject("creation", record.get("name") or "")]
    return digest


class _Instance:
    """The three fields ``style.measure`` reads off a type-1 line."""

    __slots__ = ("part_name", "color", "matrix")

    def __init__(self, part_name, color, matrix):
        self.part_name, self.color, self.matrix = part_name, color, matrix


def _monotonous(text):
    """Is this creation one brick repeated, rather than a model?

    The reason this filter has to exist, and it is not a nicety. Measured over
    every model this agent has saved, the median has **6 distinct shapes where
    real sets its size have 23**, and 83 of 93 are past the corpus's 90th
    percentile for one shape doing all the work. The library is therefore full
    of models that validate perfectly and are a single part repeated sixty
    times — and handing one of those back as a worked example does not merely
    fail to help, it teaches the fault. The bonsai in the library is 62 round
    plates out of 76.

    So the same yardstick `style.py` already holds a *new* build to is applied
    to an old one before it is offered as precedent: past the corpus 90th
    percentile for its size, it is not prior art, it is a mistake that was
    saved. Everything else — every model inside what real sets of its size look
    like — comes through untouched.

    Deliberately the one axis and not all four. Colour count and rotation share
    are properties a subject can honestly have (a grey wall is grey), whereas
    one shape being most of the model is the specific thing that makes an
    example worth nothing.
    """
    from . import style

    instances = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) >= 15 and fields[0] == "1":
            try:
                matrix = [float(v) for v in fields[5:14]]
            except ValueError:
                continue
            instances.append(_Instance(fields[14], fields[1], matrix))

    measured = style.measure(instances)
    if not measured or measured["parts"] < style.MIN_PARTS:
        return False
    baseline = style.baseline(measured["parts"])
    if not baseline:
        return False
    return measured["top_share"] > baseline["top_share_p90"]


def _legend(lines):
    """What the part numbers in that source are, most used first.

    The same reasoning as ``refsets._legend``: geometry with no legend is
    unreadable to anything that does not already know the catalogue by heart,
    and a builder that cannot tell a bracket from a mudguard can only paste
    what it is shown rather than adapt it.
    """
    from . import catalog

    counts = {}
    for line in lines:
        match = _PART.match(line.strip())
        if match:
            part_id = match.group(1)
            counts[part_id] = counts.get(part_id, 0) + 1

    out = []
    for part_id, used in sorted(counts.items(), key=lambda kv: -kv[1])[:10]:
        row = catalog.get_part(part_id)
        out.append({"part": part_id, "used": used,
                    "is": (row or {}).get("description") or "not in the catalogue"})
    return out


# -- notes -------------------------------------------------------------------

def remembered(subject, requirements=None, parts=(), limit=MAX_NOTES):
    """Notes worth having in front of this build.

    Two sources, in order. The notes filed against the **parts this build is
    going to use** are the ones that cannot be found any other way at the
    moment they matter — `get_part_details` surfaces them, but only for a part
    the builder already decided to look up, which is after the decision they
    would have informed. Then whatever the index thinks is relevant to the
    subject.
    """
    out, seen = [], set()

    def take(entries):
        for entry in entries:
            text = (entry.get("text") or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            out.append({"about": _about(entry), "note": text})
            if len(out) >= limit:
                return True
        return False

    try:
        for part_id in list(parts)[:8]:
            if take(notes.for_subject("part", str(part_id))):
                return out
    except Exception:
        pass

    query = " ".join(str(s) for s in (subject, requirements) if s).strip()
    if not query:
        return out
    try:
        take(_retrieval().search_notes(query, max_results=limit * 2))
    except Exception:
        pass
    return out


def _about(entry):
    kind = (entry.get("subject_type") or "").strip()
    which = (entry.get("subject_id") or "").strip()
    if kind == "general" or not which:
        return "generally"
    return f"{kind} {which}"


# -- what the builder is shown -----------------------------------------------

def as_text(found=None, remembered_notes=None, subject=None):
    """The block that goes in the task. None when there is nothing to say."""
    found = found or []
    remembered_notes = remembered_notes or []
    if not found and not remembered_notes:
        return None

    parts = [
        "This is **your own** earlier work — models you built and saved, and "
        "things you worked out and wrote down. It is not an official set and "
        "carries none of a set's authority: a set is evidence of how LEGO "
        "solves a problem, and this is evidence of how *you* solved one. Only "
        "models that passed validation are shown.",
        "",
        "Use it the way you would use a set you had just read: take the "
        "technique, not the file. Reproducing one of these line for line is "
        "not building anything, and a model you already have is not an answer "
        "to a request for a new one — but the trunk you got right last time is "
        "a trunk you do not have to work out again.",
    ]

    for digest in found:
        parts.append("")
        head = f"### Your `{digest.get('name')}`"
        facts = [f"{digest['pieces']} parts" if digest.get("pieces") else None,
                 (f"{digest['distinct_shapes']} distinct shapes"
                  if digest.get("distinct_shapes") else None),
                 ", ".join(digest.get("tags") or []) or None]
        facts = [f for f in facts if f]
        parts.append(f"{head} — {' · '.join(facts)}" if facts else head)
        if digest.get("description"):
            parts.append("")
            parts.append(digest["description"])
        for note in digest.get("notes") or []:
            parts.append(f"- *you noted:* {note}")
        if digest.get("legend"):
            parts.append("")
            parts.append("Built out of: " + ", ".join(
                f"`{row['part']}` {row['is']} (x{row['used']})"
                for row in digest["legend"]))
        if digest.get("source"):
            parts.append("")
            parts.append("```ldraw")
            parts.append(digest["source"])
            if digest.get("clipped"):
                parts.append(f"0 // ... clipped at {SOURCE_LINES} of "
                             f"{digest['lines']} lines")
            parts.append("```")

    if remembered_notes:
        parts.append("")
        parts.append("### What you have written down")
        parts.append("")
        parts.append("Facts you worked out while building and recorded. They "
                     "are yours and they were right when you wrote them; check "
                     "anything that names a part against the catalogue before "
                     "you lean on it.")
        parts.append("")
        for entry in remembered_notes:
            parts.append(f"- **{entry['about']}** — {entry['note']}")

    return "\n".join(parts)


def headline(found=None, remembered_notes=None):
    """One line for the log."""
    bits = []
    if found:
        bits.append(", ".join(str(d.get("name")) for d in found))
    if remembered_notes:
        bits.append(f"{len(remembered_notes)} note(s)")
    return "; ".join(bits) or "nothing"
