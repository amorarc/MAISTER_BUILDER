# Retrieval

Local GPU semantic search over two independent vector databases, with no network
calls and no vector-database server.

| Database | Rows | What a row is | Key | |
|---|---|---|---|---|
| `data/vector_db/parts` | 5,878 | one LDraw catalogue part | `part_id` | static |
| `data/vector_db/sets` | 1,801 | one official model (`.mpd`) from the OMR library | `model_file` | static |
| `data/vector_db/creations` | grows | one model the agent built and saved | `creation_id` | appendable |
| `data/vector_db/notes` | grows | one fact the agent wrote down | `note_id` | appendable |

The split between `sets` and `creations` is deliberate and load-bearing. A
human-designed set is evidence of how LEGO solves a problem; an agent creation
is only evidence of what this agent already tried, and is worth reusing only if
it validated. They get separate tools so the agent cannot blur the two.

The static indexes are built from CSV catalogues. The appendable ones are
written a row at a time as the agent works, and are fully derived from
`data/agent_creations/metadata.json` and `data/agent_knowledge/notes.json` -
rebuilding them repairs a lost index without losing anything recorded.

Models, both run locally on the GPU:

- **[Qwen/Qwen3-Embedding-0.6B](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)** - 1024-dim embeddings
- **[Qwen/Qwen3-Reranker-0.6B](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B)** - cross-encoder reranking of the shortlist

## Build

```bash
python -m maister.retrieval.build_indexes             # all four, ~45 s on an RTX 4070
python -m maister.retrieval.build_indexes --only sets
```

Rebuild after editing `documents.py` or re-downloading a catalogue. The float
matrix is never written to disk - quantization happens before saving.

## Appendable stores

An index the agent writes into has no corpus when its first row arrives, which
is a problem: per-dimension calibration fit over two rows is meaningless.
`AppendableStore` handles it in two phases.

**Borrowed.** Below 64 rows it uses the parts index's calibration - same
embedding model, so the per-dimension statistics transfer. Measured on set
vectors, borrowing costs 0.0015 of cosine fidelity (0.99846 against 0.99993 for
own-corpus calibration), far below what changes a ranking. Small stores also
skip the binary coarse filter entirely, so the weaker transfer of bit thresholds
never comes into play.

**Own.** Past 64 rows, and again on every doubling, the store recalibrates on
its own vectors. That needs the original floats, which are not stored - so the
payload always keeps the `document` text and the store re-embeds itself. 80
incremental adds take ~1.2 s including the recalibration pass.

Adding a row whose key already exists **replaces** it, so re-saving an improved
model under the same name updates it rather than leaving the broken draft behind
to be found later.

## The relevance floor

`search_creations` and `search_notes` drop anything below **0.48 cosine** and
return nothing rather than the least-bad row. The static indexes have no floor:
with 5,878 parts the best match is always worth seeing, but a library holding
three creations matches *everything*, and `"spaceship"` returning the pine tree
invites the agent to build from an unrelated reference.

The reranker cannot supply this gate. It orders results correctly but its scores
are not comparable across queries - a correct hit measured 0.0055 on one query
and a wrong hit 0.0054 on another. Cosine is calibrated; measured over good and
bad query/answer pairs:

```
genuine matches   0.517 - 0.679
spurious matches  0.294 - 0.457
```

An exact subject lookup (`search_notes` with a `subject_id` and no query) bypasses
both the floor and the GPU.

## Quantization

Each index stores two quantized copies of the same matrix and no float copy:

| | Layout | Parts index | Role |
|---|---|---|---|
| binary | 1 bit/dim, packed | 0.75 MB | coarse filter, XOR + popcount |
| int8 | 1 byte/dim, per-dim affine | 6.0 MB | rescoring the survivors |
| *(float32 equivalent)* | *4 bytes/dim* | *24.1 MB* | *never stored* |

Two details do the work:

- **Per-dimension calibration**, from the 0.1/99.9 percentiles rather than
  min/max. A normalized 1024-d embedding has components clustered around ±0.03,
  so a single global scale over [−1, 1] would throw away nearly all resolution,
  and raw min/max lets one outlier row compress the scale for every other.
- **Binary thresholds at the per-dimension corpus mean**, not zero. Qwen3
  embeddings carry a clear per-dimension bias; centring keeps each bit near a
  50/50 split so all 1024 bits stay informative.

Measured on the parts index against an exact float scan:

```
int8 vector fidelity  cosine(original, dequantized)   mean 0.99993  min 0.99909
ranking correlation   float vs int8                        0.99992
cascade recall@10     vs full int8 scan                     1.000
```

`find_similar_sets` dequantizes a stored row and uses it as the query, so
"more like this" needs neither the GPU nor the embedding model.

## Query path

```
query ─▶ embed (28 ms, GPU)
      ─▶ hard filters          category / studs, or theme / year / piece count
      ─▶ binary Hamming        whole corpus ─▶ top 512
      ─▶ int8 dot product      top 512 ─▶ top 32
      ─▶ Qwen3 reranker        top 32 ─▶ top k
```

`coarse_k` defaults to `max(top_k * 40, 512)`; 512 measures at recall@10 = 1.000
on the parts corpus and the int8 rescore is not the bottleneck.

Latency, steady state (models warm):

| | rerank on | rerank off |
|---|---|---|
| `search_parts` | 280 ms | 87 ms |
| `search_sets` | 352 ms | 22 ms |
| `find_similar_sets` | - | 2.4 ms |

Reranking is on by default: it is small against LLM latency and it is what
separates "Slope Brick Curved 2 x 2" from the twelve patterned variants that
embed almost identically. Set `LDRAW_RERANK=0` to disable it globally, or pass
`rerank=False` per call.

## Hybrid parts search

`search_parts` fuses two rankings with reciprocal-rank fusion (k=60):

- the **lexical** scorer in `maister/agent/catalog.py` - requires every query term
  to appear verbatim, so it is precise for `"brick 2 x 4"` and returns nothing
  for `"something curved for a car roof"`
- **semantic** vector search - the opposite failure mode

Two corrections sit on top of the fused ranking, because pure similarity gets
both cases wrong in the same direction - it prefers the elaborate variant over
the plain mould:

- **Exact hits are pinned**, bypassing fusion and the reranker: a query token
  matching a `part_id`, or a query equal to a part's normalized description.
  Without the pin, `"brick 2 x 4"` returns *Brick 2 x 4 with Train Wheels* above
  plain `3001`.
- **Decorated parts sink below plain ones** - anything whose description mentions
  a pattern, sticker, print or logo. These are the same shape as the mould they
  decorate, so they embed almost identically, and `"something curved for a car
  roof"` used to return *Slope Brick Curved 2 x 2 with Black "targa" Pattern*
  ahead of plain `15068`. A printed part is specific to the one set it shipped
  in. The demotion is skipped when the query itself asks for a pattern or logo.

Set search is purely semantic - there is no useful keyword match against a set
name - filtered by theme, year and piece count.

Both degrade to keyword-only if the databases have not been built, so the agent
keeps working before the first build.

## Files

```
quantization.py   int8 affine + binary packing, and the scoring maths for both
store.py          VectorStore (static) and AppendableStore (grows)
encoder.py        Qwen3-Embedding wrapper (last-token pooling, left padding)
reranker.py       Qwen3-Reranker wrapper (P(yes) at the final position)
documents.py      catalogue row -> the text that gets embedded
search.py         hybrid fusion, filters, the API the agent tools call
build_indexes.py  CLI
```

Sources of truth for the appendable stores live outside this package:

```
maister/agent/creations.py   the agent's model library
maister/agent/notes.py       the agent's notes, with subject validation
```

Notes are validated against a real subject on write - a note filed against a
part number that does not exist is worse than no note, because it is a
confident-looking record of something hallucinated that will be retrieved and
believed later. Notes on a part are folded into `get_part_details`
automatically, which is the main way they get used; notes on a set are reached
through `search_reference(kind="notes", subject_id=...)`, since
`get_set_details` now returns the set's LDraw source and nothing else.

## Tuning

Embedding quality is decided in `documents.py`, not in the model. The templates
expand what the CSV encodes tersely (`"Brick  2 x  4"` becomes a sentence naming
its footprint, height and whether anything can attach on top) and add the
vocabulary a *user* would reach for. Changing a template means rebuilding -
`TEMPLATE_VERSION` and the model name are recorded in each `manifest.json`.

Environment variables: `LDRAW_EMBED_MODEL`, `LDRAW_RERANK_MODEL`,
`LDRAW_RETRIEVAL_DEVICE` (`auto` / `cuda` / `cpu`), `LDRAW_RERANK`.
