"""Build the quantized vector databases.

    python -m maister.retrieval.build_indexes            # all four
    python -m maister.retrieval.build_indexes --only sets
    python -m maister.retrieval.build_indexes --batch-size 32

Embeddings are produced once on the GPU and written out quantized; the float
matrix never reaches disk. Rebuild after changing anything in ``documents.py``
or after re-downloading the catalogues.

``parts`` and ``sets`` are built from CSV catalogues. ``creations`` and ``notes``
normally grow a row at a time as the agent works and never need this script -
but they are fully derived from ``data/agent_creations/metadata.json`` and
``data/agent_knowledge/notes.json``, so rebuilding them here repairs a lost or
stale index without losing anything the agent recorded.
"""

import argparse
import sys
import time

from ..agent import catalog, creations, notes, sets
from ..agent.config import (
    CREATIONS_INDEX_DIR,
    NOTES_INDEX_DIR,
    PARTS_INDEX_DIR,
    SETS_INDEX_DIR,
)
from . import documents
from .encoder import get_encoder
from .store import VectorStore


def _progress(label):
    started = time.monotonic()

    def report(done, total):
        elapsed = time.monotonic() - started
        rate = done / elapsed if elapsed else 0
        eta = (total - done) / rate if rate else 0
        sys.stdout.write(
            f"\r  {label}: {done}/{total}  {rate:5.1f}/s  eta {eta:5.0f}s")
        sys.stdout.flush()
        if done >= total:
            sys.stdout.write("\n")

    return report


def build_parts(batch_size):
    rows = catalog.load_catalog()
    if not rows:
        print("No parts catalogue found; skipping the parts index.", file=sys.stderr)
        return None

    themes = sets.part_themes()
    payload, texts = [], []
    for row in rows:
        geometry = catalog.part_geometry(row)
        text = documents.part_document(row, geometry, themes.get(row.get("part_id")))
        texts.append(text)
        entry = catalog.summarize(row)
        entry["document"] = text
        payload.append(entry)

    print(f"Parts: embedding {len(texts)} documents")
    encoder = get_encoder()
    vectors = encoder.encode(texts, batch_size=batch_size, progress=_progress("parts"))
    return VectorStore.build(PARTS_INDEX_DIR, vectors, payload,
                             model=encoder.model_name, key_field="part_id",
                             template_version=documents.TEMPLATE_VERSION)


def build_sets(batch_size):
    rows = sets.load_sets()
    if not rows:
        print("No set metadata found; skipping the sets index.", file=sys.stderr)
        return None

    payload, texts = [], []
    for row in rows:
        text = documents.set_document(row)
        texts.append(text)
        entry = sets.summarize(row)
        entry["set_id"] = row.get("set_id")
        entry["document"] = text
        payload.append(entry)

    print(f"Sets: embedding {len(texts)} documents")
    encoder = get_encoder()
    vectors = encoder.encode(texts, batch_size=batch_size, progress=_progress("sets"))
    return VectorStore.build(SETS_INDEX_DIR, vectors, payload,
                             model=encoder.model_name, key_field="model_file",
                             template_version=documents.TEMPLATE_VERSION)


def build_creations(batch_size):
    records = creations.load_creations()
    if not records:
        print("No saved creations yet; nothing to index.")
        return None

    payload, texts = [], []
    for record in records:
        text = documents.creation_document(record)
        texts.append(text)
        payload.append(dict(creations.summarize(record), document=text))

    print(f"Creations: embedding {len(texts)} documents")
    encoder = get_encoder()
    vectors = encoder.encode(texts, batch_size=batch_size, progress=_progress("creations"))
    return VectorStore.build(CREATIONS_INDEX_DIR, vectors, payload,
                             model=encoder.model_name, key_field="creation_id",
                             template_version=documents.TEMPLATE_VERSION)


def build_notes(batch_size):
    records = notes.load_notes()
    if not records:
        print("No notes yet; nothing to index.")
        return None

    payload, texts = [], []
    for record in records:
        text = documents.note_document(record)
        texts.append(text)
        payload.append(dict(notes.summarize(record),
                            subject_type=record.get("subject_type"),
                            subject_id=record.get("subject_id"),
                            document=text))

    print(f"Notes: embedding {len(texts)} documents")
    encoder = get_encoder()
    vectors = encoder.encode(texts, batch_size=batch_size, progress=_progress("notes"))
    return VectorStore.build(NOTES_INDEX_DIR, vectors, payload,
                             model=encoder.model_name, key_field="note_id",
                             template_version=documents.TEMPLATE_VERSION)


def _report(name, store):
    if store is None:
        return
    manifest = store.manifest
    stored = manifest["bytes_int8"] + manifest["bytes_binary"]
    original = manifest["bytes_float32_equivalent"]
    print(f"  {name}: {manifest['count']} vectors x {manifest['dim']} dims -> "
          f"{stored / 1e6:.2f} MB quantized "
          f"(float32 would be {original / 1e6:.2f} MB, {original / stored:.1f}x) "
          f"at {store.path}")


BUILDERS = {
    "parts": build_parts,
    "sets": build_sets,
    "creations": build_creations,
    "notes": build_notes,
}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--only", choices=tuple(BUILDERS),
                        help="build one database instead of all four")
    parser.add_argument("--batch-size", type=int, default=16)
    args = parser.parse_args(argv)

    started = time.monotonic()
    wanted = [args.only] if args.only else list(BUILDERS)
    built = {name: BUILDERS[name](args.batch_size) for name in wanted}

    print(f"\nDone in {time.monotonic() - started:.0f}s")
    for name, store in built.items():
        _report(name, store)

    # A long-lived backend caches store handles; a rebuild behind it would
    # otherwise keep serving the old index until restart.
    from .search import forget_stores
    forget_stores()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
