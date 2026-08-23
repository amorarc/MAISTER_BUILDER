"""High-level search over the two vector databases.

Parts search is **hybrid**. The lexical scorer in :mod:`maister.agent.catalog` is
excellent when the agent already knows what it wants ("brick 2 x 4", "3001") and
useless otherwise, because it requires every query term to appear verbatim -
"something curved for a car roof" matches nothing. Semantic search is the
opposite. Fusing the two rankings keeps the precision of the first and adds the
recall of the second.

Set search is purely semantic, filtered by theme, year and piece count, and is
what lets the agent pull up a real model to work from instead of inventing a
shape from scratch.

Everything degrades to lexical-only if the databases have not been built yet, so
the agent keeps working before the first ``build_indexes`` run.
"""

import re
import sys
import threading
from functools import lru_cache

from ..agent import catalog, creations, notes, sets
from ..agent.config import (
    CREATIONS_INDEX_DIR,
    EMBEDDING_MODEL,
    NOTES_INDEX_DIR,
    PARTS_INDEX_DIR,
    RERANK_ENABLED,
    SETS_INDEX_DIR,
)
from . import documents
from . import encoder as encoder_module
from . import reranker as reranker_module
from .store import AppendableStore, VectorStore

# Rank-fusion constant. 60 is the value from the original RRF paper and is
# deliberately large: it flattens the head of both rankings so that agreement
# between them matters more than either one's exact ordering.
RRF_K = 60

EMBEDDING_DIM = 1024  # Qwen3-Embedding-0.6B; only used to create an empty store

# Minimum cosine similarity for a hit in the agent's own small databases.
#
# The static indexes never need this: with 5,878 parts the best match is always
# worth seeing. A library holding three creations is different - every query
# matches *something*, and "spaceship" returning the pine tree invites the agent
# to build from a reference that has nothing to do with the task.
#
# The reranker cannot supply this gate. Its scores order results correctly but
# are not comparable across queries: a correct hit measured 0.0055 on one query
# and a wrong hit 0.0054 on another. Cosine is calibrated - measured over good
# and bad query/answer pairs, genuine matches ran 0.517-0.679 and spurious ones
# 0.294-0.457, so the floor sits in the gap.
RELEVANCE_FLOOR = 0.48

_stores = {}
_load_errors = {}
_store_lock = threading.Lock()


def _store(name, path):
    """Load a database once, remembering failures so we retry at most... never.

    Once, even when several builders ask at the same moment - subconstructions
    run in parallel and each searches. The fast path stays outside the lock,
    because after the first call this is read every time and contended never.
    """
    if name in _stores:
        return _stores[name]
    if name in _load_errors:
        return None
    with _store_lock:
        # Another thread may have loaded it while this one waited.
        if name in _stores:
            return _stores[name]
        if name in _load_errors:
            return None
        try:
            _stores[name] = VectorStore.load(path)
        except Exception as exc:
            _load_errors[name] = str(exc)
            return None
    return _stores[name]


def parts_store():
    return _store("parts", PARTS_INDEX_DIR)


def sets_store():
    return _store("sets", SETS_INDEX_DIR)


def _writable_store(name, path, key_field):
    """An appendable database, created empty on first use.

    Unlike the static indexes these are never "missing": the agent's first
    saved model or note brings the store into existence.
    """
    if name in _stores:
        return _stores[name]
    donor = parts_store()  # same embedding model, so its calibration transfers
    dim = donor.manifest["dim"] if donor else EMBEDDING_DIM
    _stores[name] = AppendableStore.open(
        path, model=EMBEDDING_MODEL, dim=dim, key_field=key_field, donor=donor,
        template_version=documents.TEMPLATE_VERSION)
    return _stores[name]


def creations_store():
    return _writable_store("creations", CREATIONS_INDEX_DIR, "creation_id")


def notes_store():
    return _writable_store("notes", NOTES_INDEX_DIR, "note_id")


def forget_stores():
    """Drop cached handles so the next call reloads from disk.

    Rebuilding an index behind a long-lived backend would otherwise keep serving
    the old one for the life of the process.
    """
    _stores.clear()
    _load_errors.clear()
    _buildable_parts.cache_clear()
    _set_sources.cache_clear()


def status():
    """What is available right now - surfaced by the agent's tools on failure."""
    info = {"embedding_model": None, "device": None}
    static = (("parts", parts_store), ("sets", sets_store))
    writable = (("creations", creations_store), ("notes", notes_store))

    for name, getter in static:
        try:
            store = getter()
        except Exception as exc:
            info[name] = {"available": False, "reason": str(exc)}
            continue
        if store is None:
            info[name] = {"available": False, "reason": _load_errors.get(name)}
        else:
            info[name] = {"available": True, "count": store.manifest["count"],
                          "dim": store.manifest["dim"]}
            info["embedding_model"] = store.manifest["model"]

    for name, getter in writable:
        try:
            store = getter()
            info[name] = {"available": True, "count": store.manifest["count"],
                          "calibration": store.manifest.get("calibration_source")}
        except Exception as exc:
            info[name] = {"available": False, "reason": str(exc)}

    try:
        info["device"] = encoder_module.resolve_device()
    except Exception:
        pass
    return info


# -- fusion -----------------------------------------------------------------

def _rrf(rankings, key):
    """Reciprocal-rank fusion of several ranked lists of dicts."""
    scores, items = {}, {}
    for ranked in rankings:
        for rank, item in enumerate(ranked):
            ident = item.get(key)
            if ident is None:
                continue
            scores[ident] = scores.get(ident, 0.0) + 1.0 / (RRF_K + rank + 1)
            items.setdefault(ident, item)
    order = sorted(scores, key=lambda i: -scores[i])
    return [dict(items[i], fused_score=round(scores[i], 5)) for i in order]


def _query_tokens(query):
    return set(re.findall(r"[a-z0-9.]+", (query or "").lower()))


def _should_rerank(override):
    return RERANK_ENABLED if override is None else bool(override)


# -- parts ------------------------------------------------------------------

@lru_cache(maxsize=1)
def _buildable_parts():
    """Rows that name a part the agent could actually place, computed once.

    Every query pays for this scan otherwise, and the answer never changes.
    """
    store = parts_store()
    if store is None:
        return frozenset()
    usable = set()
    for i, row in enumerate(store.payload):
        if (row.get("category") or "") in ("Moved", "Obsolete"):
            continue
        # "~" marks a subpart: internal geometry LDraw uses to assemble a real
        # part, which cannot be placed in a model on its own
        if (row.get("description") or "").startswith("~"):
            continue
        usable.add(i)
    return frozenset(usable)


def _allowed_parts(store, category=None, width_studs=None, depth_studs=None):
    """Row indices passing the hard filters."""
    base = _buildable_parts()
    if category is None and width_studs is None and depth_studs is None:
        return base

    allowed = set()
    for i in base:
        row = store.payload[i]
        if category and category.lower() not in (row.get("category") or "").lower():
            continue
        if width_studs is not None or depth_studs is not None:
            # either orientation: the part can be rotated 90 degrees
            want = {width_studs, depth_studs} - {None}
            have = {row.get("width_studs"), row.get("depth_studs")} - {None}
            if not want <= have:
                continue
        allowed.add(i)
    return allowed


def _semantic_parts(query, category, width_studs, depth_studs, top_k):
    store = parts_store()
    if store is None or not query:
        return []
    vector = encoder_module.get_encoder().encode_query(
        query, encoder_module.PART_INSTRUCTION)
    allowed = _allowed_parts(store, category, width_studs, depth_studs)
    hits = store.search(vector, top_k=top_k, allowed=allowed)
    return [dict(store.payload[i], vector_score=round(score, 4))
            for i, score in hits]


# Printed, stickered and stamped variants. They embed almost identically to the
# plain part they are based on - "Slope Brick Curved 2 x 2 with Black 'targa'
# Pattern" is the same shape - so similarity alone happily ranks them first.
_DECORATED_RE = re.compile(r"\b(pattern|sticker|print(ed)?|logo)\b", re.IGNORECASE)


def _wants_decoration(query):
    return bool(_DECORATED_RE.search(query or ""))


def _plain_parts_first(items, query):
    """Sink decorated variants below plain parts, keeping order within each group.

    A printed part is specific to the one set it shipped in. Offering it as the
    answer to "something curved for a car roof" sends the agent off to build with
    an element whose decoration it never asked for, when the undecorated mould it
    actually wants is sitting one row below.
    """
    if _wants_decoration(query):
        return items
    plain = [i for i in items if not _DECORATED_RE.search(i.get("description") or "")]
    decorated = [i for i in items if _DECORATED_RE.search(i.get("description") or "")]
    return plain + decorated


def _rerank(query, items, instruction, top_k):
    """Reorder `items` by relevance, or leave them alone if that is not possible.

    Reranking is a refinement on top of a search that already worked: fusion
    has ranked these results and they are usable as they stand. So a reranker
    that cannot run must not take the search down with it - most often it is
    CUDA out of memory, because the reranker is a second 0.6B model and
    something else on the machine (the app's own backend, say) already has the
    GPU. Losing the refinement costs a little ordering; losing the search makes
    the caller believe the part does not exist.
    """
    try:
        scored = reranker_module.get_reranker().rerank(
            query, [i.get("document") or i.get("description") or "" for i in items],
            instruction)
    except Exception as exc:
        print(f"retrieval: reranking skipped ({type(exc).__name__}: "
              f"{str(exc).splitlines()[0][:120]})", file=sys.stderr)
        return items[:top_k]
    ranked = [dict(items[i], rerank_score=round(score, 4)) for i, score in scored]
    return ranked[:top_k]


def search_parts(query="", category=None, width_studs=None, depth_studs=None,
                 max_results=12, rerank=None, semantic_candidates=48):
    """Hybrid part search: lexical + semantic, optionally reranked.

    Returns catalogue summaries in the same shape the lexical scorer produced,
    plus whichever of ``fused_score`` / ``vector_score`` / ``rerank_score``
    applied, so the agent can see why something ranked where it did.
    """
    lexical = catalog.search_parts(query, category, width_studs, depth_studs,
                                   max_results=semantic_candidates)
    semantic = _semantic_parts(query, category, width_studs, depth_studs,
                               semantic_candidates)
    if not semantic:
        return [_part_row(item) for item in lexical[:max_results]]

    fused = _rrf([lexical, semantic], key="part_id")

    # An exact hit is not a ranking signal, it is the answer: "3001" means part
    # 3001, and "brick 2 x 4" means the part actually described that way, not
    # the closest-embedding variant with train wheels bolted to it. Both beat
    # anything fusion or the reranker has to say.
    tokens = _query_tokens(query)
    normalized = catalog.normalize_text(query)
    by_id = {i for i, item in enumerate(fused)
             if (item.get("part_id") or "").lower() in tokens}
    by_description = {
        i for i, item in enumerate(fused)
        if normalized and catalog.normalize_text(item.get("description")) == normalized
    }
    exact = by_id | by_description
    if exact:
        pinned = [fused[i] for i in sorted(exact)]
        # A bare part number is a lookup, not a description - whatever the
        # embedding thinks "3001" is near is noise, so the rest comes from the
        # keyword ranking instead of the fused one.
        tail = lexical if by_id else [f for i, f in enumerate(fused) if i not in exact]
        seen = {p.get("part_id") for p in pinned}
        rest = [item for item in tail if item.get("part_id") not in seen]
        return [_part_row(item) for item in (pinned + rest)[:max_results]]

    # Before reranking, so the candidate window is not spent on twelve printed
    # variants of one mould; and after, so the reranker cannot promote them back.
    fused = _plain_parts_first(fused, query)
    if _should_rerank(rerank):
        fused = _rerank(query, fused[:max(max_results * 2, 16)],
                        reranker_module.PART_INSTRUCTION, max_results)
        fused = _plain_parts_first(fused, query)
    return [_part_row(item) for item in fused[:max_results]]


# -- sets -------------------------------------------------------------------

@lru_cache(maxsize=1)
def _set_sources():
    """``{store row index: metadata row}``, joined once rather than per query."""
    store = sets_store()
    if store is None:
        return {}
    by_file = {(r.get("file_name") or "").lower(): r for r in sets.load_sets()}
    return {i: by_file[key]
            for i, row in enumerate(store.payload)
            if (key := (row.get("model_file") or "").lower()) in by_file}


def _allowed_sets(store, theme=None, year_min=None, year_max=None,
                  min_pieces=None, max_pieces=None, exclude=()):
    return {
        i for i, source in _set_sources().items()
        if i not in exclude
        and sets.matches_filters(source, theme, year_min, year_max,
                                 min_pieces, max_pieces)
    }


def search_sets(query, theme=None, year_min=None, year_max=None,
                min_pieces=None, max_pieces=None, max_results=8, rerank=None,
                candidates=32):
    """Find official models matching a natural-language description."""
    store = sets_store()
    if store is None:
        return []

    vector = encoder_module.get_encoder().encode_query(
        query, encoder_module.SET_INSTRUCTION)
    allowed = _allowed_sets(store, theme, year_min, year_max, min_pieces, max_pieces)
    hits = store.search(vector, top_k=candidates, allowed=allowed)
    items = [dict(store.payload[i], vector_score=round(score, 4))
             for i, score in hits]

    if _should_rerank(rerank):
        items = _rerank(query, items, reranker_module.SET_INSTRUCTION, max_results)
    return [_strip(item) for item in items[:max_results]]


def find_similar_sets(identifier, max_results=8, theme=None, min_pieces=None,
                      max_pieces=None):
    """Models closest to a given one, by embedding.

    The query vector is the stored one dequantized, so this needs neither the
    GPU nor the embedding model - it is pure numpy over the quantized index.
    """
    store = sets_store()
    if store is None:
        return []

    rows = sets.resolve(identifier)
    if not rows:
        return []

    # a set number can name several models; treat every one of them as a seed
    seeds = [store.row_of(r["file_name"]) for r in rows]
    seeds = [s for s in seeds if s is not None]
    if not seeds:
        return []

    allowed = _allowed_sets(store, theme=theme, min_pieces=min_pieces,
                            max_pieces=max_pieces, exclude=set(seeds))
    best = {}
    for seed in seeds:
        for index, score in store.similar(seed, top_k=max_results * 2, allowed=allowed):
            if score > best.get(index, -2.0):
                best[index] = score

    order = sorted(best, key=lambda i: -best[i])[:max_results]
    return [_strip(dict(store.payload[i], similarity=round(best[i], 4)))
            for i in order]


# -- the agent's own creations ----------------------------------------------

def index_creation(record):
    """Add or update one creation in the vector index."""
    store = creations_store()
    document = documents.creation_document(record)
    row = dict(creations.summarize(record), document=document)
    encoder = encoder_module.get_encoder()
    store.add(encoder.encode([document]), [row], encoder=encoder)
    return row


def unindex_creation(creation_id):
    return creations_store().remove(creation_id)


def search_creations(query, tag=None, validated_only=False, min_pieces=None,
                     max_pieces=None, max_results=8, rerank=None, candidates=24,
                     floor=RELEVANCE_FLOOR):
    """Search models this agent built - never the human-designed sets.

    Returns nothing rather than the least-bad row when the library holds no
    real match; see :data:`RELEVANCE_FLOOR`.
    """
    store = creations_store()
    if not len(store):
        return []

    by_id = {r["creation_id"]: r for r in creations.load_creations()}
    allowed = {
        i for i, row in enumerate(store.payload)
        if (record := by_id.get(row.get("creation_id"))) is not None
        and creations.matches_filters(record, tag, validated_only,
                                      min_pieces, max_pieces)
    }
    if not allowed:
        return []

    vector = encoder_module.get_encoder().encode_query(
        query, encoder_module.CREATION_INSTRUCTION)
    hits = store.search(vector, top_k=candidates, allowed=allowed)
    items = [dict(store.payload[i], vector_score=round(score, 4))
             for i, score in hits if score >= floor]
    if not items:
        return []

    if _should_rerank(rerank):
        items = _rerank(query, items, reranker_module.CREATION_INSTRUCTION,
                        max_results)
    return [_strip(item) for item in items[:max_results]]


# -- what the agent has learned ---------------------------------------------

def index_note(record):
    store = notes_store()
    document = documents.note_document(record)
    row = dict(notes.summarize(record),
               subject_type=record.get("subject_type"),
               subject_id=record.get("subject_id"),
               document=document)
    encoder = encoder_module.get_encoder()
    store.add(encoder.encode([document]), [row], encoder=encoder)
    return row


def unindex_note(note_id):
    return notes_store().remove(note_id)


def search_notes(query, subject_type=None, subject_id=None, max_results=8,
                 rerank=None, candidates=24, floor=RELEVANCE_FLOOR):
    """Search the agent's own notes.

    With a ``subject_id`` and no query this is an exact lookup: it skips both
    the GPU and the relevance floor, because asking "what do I know about part
    3062b" should return everything filed there regardless of wording.
    """
    if subject_id and not (query or "").strip():
        return [notes.summarize(r) for r in
                notes.for_subject(subject_type or "part", subject_id)][:max_results]

    store = notes_store()
    if not len(store):
        return []

    wanted_type = (subject_type or "").strip().lower() or None
    wanted_id = (subject_id or "").strip().lower() or None
    allowed = {
        i for i, row in enumerate(store.payload)
        if (wanted_type is None or row.get("subject_type") == wanted_type)
        and (wanted_id is None or (row.get("subject_id") or "").lower() == wanted_id)
    }
    if not allowed:
        return []

    vector = encoder_module.get_encoder().encode_query(
        query, encoder_module.NOTE_INSTRUCTION)
    hits = store.search(vector, top_k=candidates, allowed=allowed)
    items = [dict(store.payload[i], vector_score=round(score, 4))
             for i, score in hits if score >= floor]
    if not items:
        return []

    if _should_rerank(rerank):
        items = _rerank(query, items, reranker_module.NOTE_INSTRUCTION, max_results)
    return [_strip(item) for item in items[:max_results]]


def notes_for(subject_type, subject_id):
    """Notes attached to one subject, for folding into a details response."""
    return [notes.summarize(r) for r in notes.for_subject(subject_type, subject_id)]


def _strip(item):
    """Drop the embedded document - it is index-building detail, not an answer."""
    return {k: v for k, v in item.items() if k != "document"}


def _part_row(item):
    """One part hit, with its catalogue fields brought up to date.

    A semantic hit is not a catalogue row: it is the metadata that was frozen
    into the vector index the day the index was built. Anything the catalogue
    has learned since - how a part connects, how many studs it actually has -
    is missing from it, and missing from *only* the hits the semantic side
    found. That is worse than missing everywhere: the agent would see a field
    on some rows and not others, with nothing to say why, and would have to
    treat "no connection listed" as "no connection". So every row is topped up
    from the catalogue on the way out, and the scores, which the catalogue
    knows nothing about, are kept.
    """
    row = _strip(item)
    fresh = catalog.summary_for(row.get("part_id"))
    if fresh:
        row.update(fresh)
    return row
