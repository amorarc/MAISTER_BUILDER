"""Maister Builder sources.

    maister.agent                  the LDraw agent: tools, prompts, HF client
    maister.retrieval              local GPU embeddings + reranking over quantized indexes
    maister.environment_feedback   collision and connectivity checkers
    maister.database_creation      scrapers building the parts catalogue and set library

This package was called ``code`` until it started importing torch. The project
root is always on ``sys.path`` - the backend runs as
``python -m uvicorn app.backend.main:app`` from here - so the old name shadowed
the standard library's ``code`` module, which ``pdb``, and therefore torch,
imports. Do not rename it back.
"""
