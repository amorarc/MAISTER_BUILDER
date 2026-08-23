"""Local GPU retrieval: Qwen3 embeddings + reranking over quantized indexes.

Four independent databases live under ``data/vector_db``:

* ``parts``     - one vector per LDraw catalogue part      (static)
* ``sets``      - one vector per official OMR model        (static)
* ``creations`` - one vector per model the agent saved     (grows)
* ``notes``     - one vector per fact the agent wrote down (grows)

The split between ``sets`` and ``creations`` is deliberate and load-bearing:
a human-designed set is evidence of how LEGO solves a problem, an agent
creation is only evidence of what this agent already tried. Searching them
through one tool would quietly blur that distinction.

All four are built by :mod:`maister.retrieval.build_indexes` and queried through
:mod:`maister.retrieval.search`, which is what the agent tools call.
"""

from .search import (  # noqa: F401
    find_similar_sets,
    forget_stores,
    index_creation,
    index_note,
    notes_for,
    search_creations,
    search_notes,
    search_parts,
    search_sets,
    status,
)
