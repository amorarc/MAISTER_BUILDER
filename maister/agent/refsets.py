"""Real sets, handed to the builder before it starts — with their construction.

The corpus holds 1,801 official models. A builder was told to go and find them
(`search_reference(kind="sets")`, then `get_set_details`, then `read_model`,
then `copy_from_set`) and often did not: four tool calls stand between "build a
car" and seeing how LEGO built one, and a model that thinks it knows what a car
looks like will spend those calls placing bricks instead. So the sets arrived in
runs as a *possibility*, and what the builder actually used was its own idea of
the shape.

This turns that around. Before a subconstruction is built, the harness finds the
sets that match it and puts them **in the task**, already opened:

* which sets they are — number, name, theme, year, size;
* **what each is assembled out of** — the named blocks and their part counts,
  because that is the unit `copy_from_set` grafts and the unit a real designer
  thinks in;
* **the actual LDraw of the assembly most worth copying** — real coordinates,
  real rotations, real stacking. This is the part that was missing: a list of
  the parts a set uses is a shopping list, and a shopping list does not tell you
  that the bonnet is two wedge slopes at 24 LDU meeting a windscreen laid back
  on a hinge. The geometry does.
* the exact `copy_from_set` call that grafts it.

No LLM call and no network: the search is the local vector index, the rest is
reading a file off disk. It costs a few hundred milliseconds and it is the
difference between a builder that has seen a car and one that is guessing.
"""

import re

from . import sets

# How many sets are handed over per subconstruction. Two is deliberate: one is a
# single opinion about what the subject looks like, and four is more LDraw than
# anyone reads before starting. Two lets the builder mix — which is the whole
# point of grafting from several.
MAX_SETS = 2

# Lines of real source shown per set. Enough to hold a small model whole, or the
# recognisable half of a bigger assembly.
SOURCE_LINES = 60

# The assembly worth showing, in parts. Below the floor it is a hinge or a pair
# of tiles rather than a construction. Above GOOD_BLOCK_FULL there is no more
# credit for being bigger — the excerpt is clipped long before that.
GOOD_BLOCK_MIN = 12
GOOD_BLOCK_FULL = 120

# Sets outside this range are poor references whatever they score: a 2,000-piece
# Technic supercar is not how to build a 40-part car, and a 4-part promo is not
# how to build anything.
MIN_PIECES = 12
MAX_PIECES = 300

# An assembly whose parts are mostly NOT on the stud lattice is not a lesson in
# stud building — it is a flexible hose approximated in forty segments, a rubber
# band, a chain. Copying one teaches the builder to write fractional
# coordinates, which is the exact fault the whole harness exists to prevent.
MIN_STUD_SHARE = 0.55

# How related a set has to be before it is worth putting in front of a builder.
#
# A semantic search always answers: ask it for "a quantum chromodynamics
# lecture" and it hands back an Ice Hockey Goal, because something has to come
# first. Handing that over is worse than handing over nothing — a weak
# reference pulls the design towards the wrong subject, and the builder has been
# told to start from what it is given.
#
# The reranker separates these cleanly. "a red racing car" scores 0.99, 0.97,
# 0.97; "a pine tree" scores 0.48 for the Christmas tree and 0.10 for a
# waterfall base that happens to have foliage on it; nonsense scores 0.04. The
# floor sits in the gap. Without a reranker the vector score is all there is,
# and it compresses — 0.71 for the car against 0.39 for the nonsense — so the
# fallback floor is set against that spread instead.
MIN_RERANK = 0.15
MIN_VECTOR = 0.50

# Blocks a real designer never assembled by hand: generated hose paths, tread
# runs, string. They are large, which is precisely why they win a naive
# "biggest assembly" contest.
_GENERATED = re.compile(
    r"hose|flex|string|rubber|chain|tread|band|belt|lq-?\d|-\d+\.ldr$", re.I)

_PART = re.compile(r"^1\s+\S+\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s")

# A block whose name says it is the thing being built beats a bigger one that
# does not. A rainforest diorama holds a WaterfallBase of 15 parts and a TreeBig
# of 13, and asked for a tree the harness handed over the waterfall — bigger, on
# the lattice, and about the wrong object. Designers name their submodels after
# what they are, which is free relevance nobody was reading.
NAME_MATCH_BONUS = 3.0
_WORDS = re.compile(r"[a-z]{3,}")
_STOPWORDS = frozenset((
    "the", "and", "with", "for", "its", "it", "that", "this", "some", "into",
    "from", "made", "out", "one", "two", "small", "large", "big", "little",
    "lego", "brick", "bricks", "model", "build", "built", "piece", "pieces",
))

MAX_ASSEMBLIES = 8
# Parts named in the legend under an assembly's source.
MAX_LEGEND = 12


def _retrieval():
    from ..retrieval import search

    return search


def find(subject, requirements=None, limit=MAX_SETS, max_pieces=MAX_PIECES):
    """The best real sets for a subject, opened up. Never raises.

    Returns a list of digests — see ``as_text`` for what a builder is shown.
    An empty list when the index is not built, nothing matches, or anything at
    all goes wrong: a reference is a gift to the build, never a precondition.
    """
    query = " ".join(str(s) for s in (subject, requirements) if s).strip()
    if not query:
        return []

    try:
        hits = _retrieval().search_sets(
            query, min_pieces=MIN_PIECES, max_pieces=max_pieces,
            max_results=max(4, limit * 3))
    except Exception:
        return []

    digests, seen = [], set()
    for hit in hits:
        number = hit.get("set_number")
        if not number or number in seen or not _relevant(hit):
            continue
        seen.add(number)
        digest = _open(hit, _keywords(query))
        if digest:
            digests.append(digest)
        if len(digests) >= limit:
            break
    return digests


def _relevant(hit):
    """Is this hit about the subject, or merely the closest thing in the box?"""
    reranked = hit.get("rerank_score")
    if reranked is not None:
        return reranked >= MIN_RERANK
    return (hit.get("vector_score") or 0) >= MIN_VECTOR


def _open(hit, keywords=()):
    """One search hit, with its assemblies and the source of the best one."""
    rows = sets.resolve(hit.get("set_number"))
    if not rows:
        return None
    row = rows[0]
    path = sets.model_path(row)
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None

    blocks = _blocks(lines)
    chosen = _worth_copying(blocks, lines, keywords)
    # A set whose best assembly is not stud-built is not a reference for a
    # stud-built model, however well it scored on the description. Skipped, so
    # the next hit gets the slot.
    if chosen and chosen["studs"] < MIN_STUD_SHARE:
        return None
    digest = {
        "set_number": row.get("set_number"),
        "set_name": row.get("set_name"),
        "theme": row.get("theme"),
        "year": row.get("year"),
        "pieces": hit.get("total_pieces") or row.get("total_pieces"),
        "assemblies": [{"name": b["name"], "parts": b["parts"]}
                       for b in blocks[:MAX_ASSEMBLIES] if b["parts"]],
    }
    shown = lines[chosen["start"] - 1:chosen["end"]] if chosen else lines
    if chosen:
        digest["shows"] = chosen["name"]
        digest["shows_parts"] = chosen["parts"]
    digest["source"] = "\n".join(_worth_reading(shown))
    digest["legend"] = _legend(shown)
    return digest


def _legend(lines):
    """What the part numbers in the source actually are.

    Without it the geometry is unreadable to anyone who does not already know
    the catalogue by heart: `2436a` is a bracket and `3788` is a mudguard, and a
    builder that cannot tell them apart cannot adapt what it is copying — it can
    only paste it. Most used first, because that is the part the assembly is
    made of.
    """
    counts = {}
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("1 "):
            continue
        name = _target(stripped)
        if name:
            counts[name] = counts.get(name, 0) + 1
    if not counts:
        return []

    try:
        from . import catalog

        described = {row["part_id"]: row.get("description")
                     for row in catalog.load_catalog()}
    except Exception:
        described = {}

    out = []
    for name, count in sorted(counts.items(), key=lambda kv: -kv[1])[:MAX_LEGEND]:
        part_id = name[:-4] if name.lower().endswith(".dat") else name
        what = described.get(part_id)
        if what:
            out.append({"part": part_id, "count": count,
                        "what": _plain(what)})
    return out


_MOVED = re.compile(r"~?moved to (\S+)", re.I)


def _plain(description):
    """A catalogue description as a person would read it.

    LDraw marks aliases and superseded parts in the description itself — a
    leading `~`, `=` or `_`, or the whole thing replaced by "Moved to 3023b".
    A set legitimately uses the old number and it still resolves, so the number
    stays; the marker does not, and a redirect is spelled out rather than shown
    as a shrug.
    """
    text = " ".join(str(description or "").split())
    moved = _MOVED.match(text)
    if moved:
        return (f"an older number for {moved.group(1)} — it still resolves, "
                f"and {moved.group(1)} is the current one")
    return text.lstrip("~=_ ").strip()


def _target(part_line):
    """What a type-1 line places: a part file, or another block of this MPD.

    Split on a fixed field count rather than on the last token, because a
    submodel reference is a *file name* and file names in an MPD have spaces in
    them — `1 16 0 -48 20 … 41590 - 1.ldr`. Taking the last word gave "1.ldr",
    which matches no block, so every reference counted as a real part and a
    five-line table of contents looked like a five-part assembly.
    """
    fields = part_line.split(None, 14)
    return fields[14].strip().lower() if len(fields) > 14 else ""


def _blocks(lines):
    """The named assemblies of an MPD, in build order.

    ``parts`` counts every type-1 line; ``own`` counts only the ones that place
    a real part rather than another block of the same file. The difference is
    what separates an assembly from a wrapper: `41590 - Iron Man.ldr` is five
    lines long and every one of them is a reference, so it looks like a small
    tidy assembly and holds no geometry at all. Copying it copies nothing.
    """
    blocks = []
    for number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped[:7].upper().startswith("0 FILE "):
            if blocks:
                blocks[-1]["end"] = number - 1
            blocks.append({"name": stripped[7:].strip(), "start": number,
                           "end": len(lines), "parts": 0, "own": 0})
        elif blocks and stripped.startswith("1 "):
            blocks[-1]["parts"] += 1

    names = {b["name"].strip().lower() for b in blocks}
    for block in blocks:
        for line in lines[block["start"] - 1:block["end"]]:
            stripped = line.strip()
            if stripped.startswith("1 ") and _target(stripped) not in names:
                block["own"] += 1
    return blocks


def _keywords(subject):
    """The words of a request that a submodel might be named after."""
    return {w for w in _WORDS.findall((subject or "").lower())
            if w not in _STOPWORDS}


def _worth_copying(blocks, lines, keywords=()):
    """The assembly to show the source of, scored rather than sized.

    "The biggest block" is the obvious rule and it is wrong. The biggest block
    in a Technic set is the generated hose path — forty segments at fractional
    coordinates and rotations to eleven decimal places, which is the least
    instructive thing in the file and the most misleading thing to copy. So a
    block is judged on whether it is *stud-built* first, then on being a
    readable size, and generated runs are discounted by name as well.

    Never a printed-element definition (`…pz0.dat`): a single part wearing a
    submodel's clothes.
    """
    best, best_score = None, -1.0
    for block in blocks:
        # `own` rather than `parts`: a block of nothing but references to other
        # blocks is a table of contents, and there is nothing in it to copy.
        if not block["own"] or block["name"].lower().endswith(".dat"):
            continue
        studs = _stud_share(lines[block["start"] - 1:block["end"]])
        score = studs * _size_weight(block["own"])
        # The name bonus is for choosing between real assemblies, never for
        # promoting a small one: "a fire truck" matches `4208 - firemen.ldr`,
        # which is the crew standing beside the truck.
        name = block["name"].lower()
        if block["own"] >= GOOD_BLOCK_MIN and any(w in name for w in keywords):
            score *= NAME_MATCH_BONUS
        if _GENERATED.search(block["name"]):
            score *= 0.2
        if score > best_score:
            best, best_score = {**block, "studs": studs}, score
    return best


def _stud_share(lines):
    """How much of an assembly sits on the stud lattice.

    The x and z of a part placed on studs land on a multiple of 10 LDU — 20 for
    a full stud, 10 for a jumper's half. A block where that is true of most
    parts was built the way this project builds; one where it is not is a hose,
    a chain, or something posed at an angle.
    """
    on, total = 0, 0
    for line in lines:
        found = _PART.match(line.strip())
        if not found:
            continue
        total += 1
        x, z = float(found.group(1)), float(found.group(3))
        if abs(x % 10) < 0.02 and abs(z % 10) < 0.02:
            on += 1
    return on / total if total else 0.0


def _size_weight(parts):
    """How much of a lesson an assembly of this size is.

    Below the floor it falls away squarely, so a three-part block cannot be
    carried by a name match: `41590 - Iron Man.ldr` is three real parts and two
    references, and it matched "iron man" perfectly while holding almost none
    of the figure.

    Above it, bigger is *better*, gently, up to a point. This was a window with
    a penalty for being large, and the penalty kept choosing scenery: asked for
    a fire truck, set 4208 offered its 167-part `car.ldr` and its 20-part
    `tree.ldr`, and the window handed over the tree. The source is clipped to
    sixty lines whatever is chosen, so a large assembly costs nothing to prefer
    — and its first sixty lines are the base and the first courses, which is
    exactly the part worth reading.
    """
    if parts < GOOD_BLOCK_MIN:
        return (parts / GOOD_BLOCK_MIN) ** 2
    return 0.6 + 0.4 * min(parts, GOOD_BLOCK_FULL) / GOOD_BLOCK_FULL


def _worth_reading(lines):
    """Source with the bookkeeping stripped, clipped to what will be read.

    Part lines and step markers only. `0 !LDRAW_ORG`, licence headers and the
    author's name say nothing about how the thing was built, and they are a
    third of a short set's lines.
    """
    kept = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("1 ") or stripped.upper().startswith("0 STEP"):
            kept.append(stripped)
        elif stripped[:7].upper().startswith("0 FILE "):
            kept.append(stripped)
        if len(kept) >= SOURCE_LINES:
            kept.append("… (clipped — read_model gives you all of it)")
            break
    return kept


def as_text(digests, subject=None):
    """The block a builder is shown. None when there is nothing to show.

    Reads the grafting setting rather than taking it as an argument, because
    every caller would have to pass the same answer and one of them would
    eventually not. With grafting off the sets are still worth every line of
    this — what changes is that they are here to be *read* rather than lifted,
    so the `copy_from_set` calls come out and the advice inverts.
    """
    if not digests:
        return None

    from .tools import copy_from_set_enabled

    grafting = copy_from_set_enabled()

    lines = [
        "**Real LEGO sets that already built this.** They were found for you "
        "and opened — you do not have to go looking, and you should not start "
        "from nothing while these are on the page.",
        "",
        "What is below is not a picture and not a parts list. It is **how the "
        "set is actually put together**: which assemblies it comes apart into, "
        "how big each one is, and the real LDraw of "
        + ("the one worth copying" if grafting else "the one worth studying")
        + " — real part numbers, real coordinates, real rotations. That is the "
        "information you cannot derive and they already paid for.",
    ]

    for digest in digests:
        head = f"## {digest['set_number']} — {digest.get('set_name') or 'untitled'}"
        facts = [f for f in (digest.get("theme"), str(digest.get("year") or ""),
                             f"{digest.get('pieces')} pieces"
                             if digest.get("pieces") else None) if f]
        lines += ["", head, f"*{' · '.join(facts)}*"]

        assemblies = digest.get("assemblies") or []
        if assemblies:
            lines.append("")
            lines.append(
                "It comes apart into these assemblies — each one is something "
                "`copy_from_set` can graft whole:" if grafting else
                "It comes apart into these assemblies, which is itself worth "
                "reading: it is how a designer divided this subject up.")
            lines += [f"- `{a['name']}` — {a['parts']} parts"
                      for a in assemblies if a["parts"]]

        if digest.get("shows"):
            lines += ["", f"**`{digest['shows']}` ({digest.get('shows_parts')} "
                          f"parts), as it is really built:**"]
        else:
            lines += ["", "**As it is really built:**"]
        lines += ["", "```", digest.get("source") or "", "```"]

        legend = digest.get("legend") or []
        if legend:
            lines += ["", "The parts it does that with — these are real part "
                          "numbers and they are yours to use:"]
            lines += [f"- `{p['part']}` ×{p['count']} — {p['what']}"
                      for p in legend]

        if grafting:
            graft = (f'copy_from_set(path=…, set_number="{digest["set_number"]}"'
                     + (f', submodel="{digest["shows"]}"'
                        if digest.get("shows") else "")
                     + ", at=[0,0,0], recolour={…})")
            lines += ["", f"Graft it with `{graft}`, then adapt it."]

    if grafting:
        lines += [
            "",
            "### What to do with them",
            "",
            "**Copy first, design second.** Take the assembly that is closest "
            "to what you need — `copy_from_set` — recolour it, then spend your "
            "build on what makes this model different from that one. A model "
            "that could have borrowed and did not is a worse model and a "
            "longer build.",
            "",
            "Grafting from **more than one** of them is normal and good: the "
            "body from one set, the wheels or the roof from another. Every "
            "graft leaves a comment in the file saying where it came from, so "
            "credit takes care of itself.",
            "",
            "Read them even where you do not copy. The stacking, the offsets, "
            "the way a curve is made out of straight bricks, how many plates a "
            "real designer spends on a wheel arch — that is what these are "
            "here for. `read_model('set:<number>', submodel='<name>')` gives "
            "you the whole of any of them.",
        ]
    else:
        lines += [
            "",
            "### What to do with them",
            "",
            "**Read them; do not reproduce them.** `copy_from_set` is switched "
            "off for this build, and copying a set out by hand into "
            "`build_ops` is the same act done slowly. What you are after is "
            "the technique: the stacking, the offsets, the way a curve is made "
            "out of straight bricks, how many plates a real designer spends on "
            "a wheel arch, which single part does a job you were about to "
            "approximate with three.",
            "",
            "The part numbers above are the most portable thing on this page. "
            "A set naming a part you did not know existed is worth more than "
            "its coordinates, because you can use it anywhere and the "
            "coordinates only fit where they were.",
            "",
            "`read_model('set:<number>', submodel='<name>')` gives you the "
            "whole of any of them. Then build your own, in your own "
            "coordinates, and let it be simpler than the set if that is what "
            "it takes to be yours.",
        ]

    lines += [
        "",
        "### When they are the wrong sets",
        "",
        "These were found by searching for the subject, and a search always "
        "answers. Sometimes what comes back is close to what you are building "
        "and sometimes it is a set that happens to share a word with it — a "
        "rainforest diorama for a tree, a mini tractor for a windmill.",
        "",
        ("**Look before you graft.** If the assembly is not a version of the "
         "thing you were asked for, take nothing from it. Build what was asked "
         "for instead, and read them only for technique."
         if grafting else
         "**Check what you are reading.** If the assembly is not a version of "
         "the thing you were asked for, take not even the technique from it — "
         "how a tractor's bonnet is built has nothing to teach a windmill. "
         "Build what was asked for."),
        "",
        "The failure to avoid is putting a set's own features into a model "
        "nobody asked for them in — a minifigure, a sticker, a trailer, the "
        "themed bits a set carries because of what it was. A model with the "
        "wrong set's furniture on it is worse than the plain version of the "
        "right shape, and it is a fault nothing downstream can see: it "
        "validates perfectly, because it is a perfectly buildable model of the "
        "wrong thing.",
    ]
    if subject:
        lines += ["",
                  f"None of this makes them **the** answer: you are building "
                  f"{subject}, not a copy of a set. Where the request and the "
                  f"set disagree, the request wins — and where there is a "
                  f"reference picture, the picture wins over both. Build what "
                  f"is in the picture. Nothing goes into the model because a "
                  f"set had one."]
    return "\n".join(lines)


def headline(digests):
    """One line for the log and the event stream."""
    if not digests:
        return "no matching sets"
    return ", ".join(
        f"{d['set_number']} {d.get('set_name') or ''}".strip()
        + (f" ({d['shows']})" if d.get("shows") else "")
        for d in digests)
