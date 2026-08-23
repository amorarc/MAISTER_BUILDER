"""A small on-disk vector database of quantized embeddings.

One directory per database, under ``data/vector_db/``:

```
manifest.json    model name, dimension, row count, build time
calibration.npz  per-dimension int8 scale/offset and binary thresholds
binary.npy       uint8 (n, dim/8)   coarse filter
int8.npy         int8  (n, dim)     rescoring
payload.json     the metadata row behind each vector
```

Search is a two-stage cascade: Hamming over the packed bits narrows the corpus
to a few hundred candidates, then int8 dot products order those precisely. At
these corpus sizes (~6k parts, ~1.8k sets) that is well under a millisecond and
the ranking is indistinguishable from a float brute-force scan.

Two flavours:

* :class:`VectorStore` - built once from a complete corpus (parts, human sets).
* :class:`AppendableStore` - grows a row at a time as the agent saves a model or
  writes down something it learned. See its docstring for how calibration works
  when the corpus does not exist yet.
"""

import json
import time
from pathlib import Path

import numpy as np

from . import quantization as quant


class VectorStore:
    def __init__(self, path):
        self.path = Path(path)
        self.manifest = {}
        self.payload = []
        self.binary = None
        self.codes = None
        self.int8_params = {}
        self.binary_params = {}
        self._by_key = {}

    # -- building ---------------------------------------------------------
    @classmethod
    def build(cls, path, vectors, payload, model, key_field=None,
              template_version=None):
        """Quantize ``vectors`` and write a complete database to ``path``."""
        vectors = quant.normalize(vectors)
        if len(vectors) != len(payload):
            raise ValueError(f"{len(vectors)} vectors but {len(payload)} payload rows")

        store = cls(path)
        store.int8_params = quant.fit_int8(vectors)
        store.binary_params = quant.fit_binary(vectors)
        store.codes = quant.quantize_int8(vectors, store.int8_params)
        store.binary = quant.quantize_binary(vectors, store.binary_params)
        store.payload = list(payload)
        store.manifest = {
            "model": model,
            "dim": int(vectors.shape[1]),
            "count": int(vectors.shape[0]),
            "key_field": key_field,
            # bump in documents.py when a template changes, so a stale index is
            # identifiable rather than silently mismatched
            "template_version": template_version,
            "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "bytes_int8": int(store.codes.nbytes),
            "bytes_binary": int(store.binary.nbytes),
            "bytes_float32_equivalent": int(vectors.nbytes),
        }
        store._index_keys()
        store.save()
        return store

    def save(self):
        self.path.mkdir(parents=True, exist_ok=True)
        np.save(self.path / "binary.npy", self.binary)
        np.save(self.path / "int8.npy", self.codes)
        np.savez(
            self.path / "calibration.npz",
            offset=self.int8_params["offset"],
            scale=self.int8_params["scale"],
            threshold=self.binary_params["threshold"],
        )
        (self.path / "payload.json").write_text(
            json.dumps(self.payload, ensure_ascii=False), encoding="utf-8")
        (self.path / "manifest.json").write_text(
            json.dumps(self.manifest, indent=2), encoding="utf-8")

    # -- loading ----------------------------------------------------------
    @classmethod
    def load(cls, path, mmap=True):
        store = cls(path)
        manifest = store.path / "manifest.json"
        if not manifest.is_file():
            raise FileNotFoundError(
                f"no vector database at {store.path}. Build it with:\n"
                f"    python -m maister.retrieval.build_indexes")
        store.manifest = json.loads(manifest.read_text(encoding="utf-8"))
        store.payload = json.loads((store.path / "payload.json").read_text(encoding="utf-8"))
        # mmap: the int8 matrix is only touched for the handful of candidate
        # rows that survive the coarse filter. Appendable stores load it for
        # real, because they rewrite it.
        store.codes = np.load(store.path / "int8.npy",
                              mmap_mode="r" if mmap else None)
        store.binary = np.load(store.path / "binary.npy")
        cal = np.load(store.path / "calibration.npz")
        store.int8_params = {"offset": cal["offset"], "scale": cal["scale"]}
        store.binary_params = {"threshold": cal["threshold"]}
        store._index_keys()
        return store

    def _index_keys(self):
        field = self.manifest.get("key_field")
        if not field:
            return
        self._by_key = {
            str(row.get(field)).lower(): i
            for i, row in enumerate(self.payload)
            if row.get(field) is not None
        }

    def __len__(self):
        return len(self.payload)

    def row_of(self, key):
        """Index of the row whose key field equals ``key``, or None."""
        return self._by_key.get(str(key).strip().lower())

    def vector_of(self, index):
        """Approximate float vector for a stored row.

        Dequantized int8, renormalized. Close enough to the original to be used
        as a query for "more like this" - which is why no float copy is kept.
        """
        vec = quant.dequantize_int8(np.asarray(self.codes[index]), self.int8_params)
        return quant.normalize(vec[None, :])[0]

    # -- search -----------------------------------------------------------
    def search(self, query, top_k=10, coarse_k=None, allowed=None):
        """Cascade search. Returns ``[(index, score), ...]`` best first.

        ``allowed`` restricts the search to a set/sequence of row indices,
        which is how the hard filters (category, theme, piece count) are
        applied before any similarity is computed.
        """
        query = quant.normalize(np.asarray(query, dtype=np.float32)[None, :])[0]
        # 512 measures at recall@10 = 1.000 against a full int8 scan of the parts
        # corpus, and costs nothing: the int8 rescore is not the bottleneck.
        coarse_k = coarse_k or max(top_k * 40, 512)

        if allowed is None:
            candidates = None
            binary = self.binary
        else:
            candidates = np.asarray(sorted(allowed), dtype=np.int64)
            if candidates.size == 0:
                return []
            binary = self.binary[candidates]

        if binary.shape[0] > coarse_k:
            query_bits = quant.quantize_binary(query[None, :], self.binary_params)
            coarse = quant.hamming_scores(binary, query_bits)
            keep = np.argpartition(-coarse, coarse_k - 1)[:coarse_k]
        else:
            keep = np.arange(binary.shape[0])

        rows = keep if candidates is None else candidates[keep]
        scores = quant.int8_scores(np.asarray(self.codes[rows]), query, self.int8_params)

        order = np.argsort(-scores)[:top_k]
        return [(int(rows[i]), float(scores[i])) for i in order]

    def similar(self, index, top_k=10, allowed=None):
        """Nearest neighbours of a stored row, excluding the row itself."""
        hits = self.search(self.vector_of(index), top_k=top_k + 1, allowed=allowed)
        return [(i, s) for i, s in hits if i != index][:top_k]


# Below this many rows, a store's own percentile calibration is meaningless -
# with two rows every dimension's range is whatever those two happen to span.
MIN_CALIBRATION_ROWS = 64


class AppendableStore(VectorStore):
    """A vector database that grows one row at a time.

    The static indexes fit their quantization calibration over a finished
    corpus. A store the agent writes into has no corpus when the first row
    arrives, so calibration is handled in two phases:

    * **Borrowed.** Until :data:`MIN_CALIBRATION_ROWS` rows exist, the store uses
      a donor index's calibration - the parts index, built with the same
      embedding model. Measured on set vectors, borrowing costs about 0.0015 of
      cosine fidelity (0.99846 against 0.99993 for own-corpus calibration),
      which is far below what changes a ranking. Small stores also skip the
      binary coarse filter entirely, so the weaker transfer of bit thresholds
      never comes into play.
    * **Own.** Once the store is large enough, and again whenever it has doubled
      since the last fit, it recalibrates on its own vectors.

    Recalibrating needs the original floats, which are not stored - so the
    payload always keeps the ``document`` text each vector came from and the
    store re-embeds itself. That is also what makes these indexes rebuildable
    from their JSON source of truth if one is ever lost.
    """

    @classmethod
    def open(cls, path, model, dim, key_field, donor=None, template_version=None):
        """Load the store at ``path``, creating an empty one if absent."""
        path = Path(path)
        if (path / "manifest.json").is_file():
            store = cls.load(path, mmap=False)
            # A full rebuild through build_indexes writes a static manifest, but
            # fits calibration over every row it wrote - so the store is exactly
            # as well calibrated as if it had recalibrated itself.
            store.manifest.setdefault("appendable", True)
            if "calibrated_at_count" not in store.manifest:
                store.manifest["calibrated_at_count"] = store.manifest.get("count", 0)
                store.manifest["calibration_source"] = "own"
            return store

        store = cls(path)
        store.manifest = {
            "model": model,
            "dim": int(dim),
            "count": 0,
            "key_field": key_field,
            "template_version": template_version,
            "appendable": True,
            "calibrated_at_count": 0,
            "calibration_source": None,
            "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "bytes_int8": 0,
            "bytes_binary": 0,
            "bytes_float32_equivalent": 0,
        }
        store.payload = []
        store.codes = np.zeros((0, dim), dtype=np.int8)
        store.binary = np.zeros((0, (dim + 7) // 8), dtype=np.uint8)
        store._adopt_calibration(donor)
        store.save()
        return store

    def _adopt_calibration(self, donor):
        """Take a donor's calibration, or fall back to a plain symmetric range."""
        if donor is not None and donor.int8_params:
            self.int8_params = {"offset": np.array(donor.int8_params["offset"]),
                                "scale": np.array(donor.int8_params["scale"])}
            self.binary_params = {"threshold": np.array(donor.binary_params["threshold"])}
            self.manifest["calibration_source"] = str(donor.path.name)
            return

        # No donor: a normalized d-dimensional vector has components around
        # 1/sqrt(d), so cover a few times that rather than the useless [-1, 1].
        dim = self.manifest["dim"]
        span = 8.0 / np.sqrt(dim)
        self.int8_params = {
            "offset": np.full(dim, -span, dtype=np.float32),
            "scale": np.full(dim, 2 * span / 255.0, dtype=np.float32),
        }
        self.binary_params = {"threshold": np.zeros(dim, dtype=np.float32)}
        self.manifest["calibration_source"] = "default"

    def _refresh_sizes(self):
        self.manifest["count"] = len(self.payload)
        self.manifest["bytes_int8"] = int(self.codes.nbytes)
        self.manifest["bytes_binary"] = int(self.binary.nbytes)
        self.manifest["bytes_float32_equivalent"] = int(
            len(self.payload) * self.manifest["dim"] * 4)
        self.manifest["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")

    def add(self, vectors, rows, encoder=None):
        """Append rows. Returns the indices they landed at.

        Rows whose key already exists replace the old row rather than
        duplicating it, so re-saving a model under the same name updates it.
        """
        vectors = quant.normalize(np.atleast_2d(np.asarray(vectors, dtype=np.float32)))
        if len(vectors) != len(rows):
            raise ValueError(f"{len(vectors)} vectors but {len(rows)} rows")
        if not rows:
            return []

        field = self.manifest.get("key_field")
        replaced = {}
        if field:
            for i, row in enumerate(rows):
                existing = self.row_of(row.get(field))
                if existing is not None:
                    replaced[i] = existing

        codes = quant.quantize_int8(vectors, self.int8_params)
        binary = quant.quantize_binary(vectors, self.binary_params)

        placed = []
        fresh_codes, fresh_binary, fresh_rows = [], [], []
        for i, row in enumerate(rows):
            if i in replaced:
                at = replaced[i]
                self.codes[at] = codes[i]
                self.binary[at] = binary[i]
                self.payload[at] = row
                placed.append(at)
            else:
                fresh_codes.append(codes[i])
                fresh_binary.append(binary[i])
                fresh_rows.append(row)
                placed.append(len(self.payload) + len(fresh_rows) - 1)

        if fresh_rows:
            self.codes = np.vstack([self.codes, np.asarray(fresh_codes, dtype=np.int8)])
            self.binary = np.vstack([self.binary, np.asarray(fresh_binary, dtype=np.uint8)])
            self.payload.extend(fresh_rows)

        self._index_keys()
        if encoder is not None and self._needs_recalibration():
            self.recalibrate(encoder)
        self._refresh_sizes()
        self.save()
        return placed

    def remove(self, key):
        """Drop the row with this key. Returns True if something was removed."""
        index = self.row_of(key)
        if index is None:
            return False
        keep = [i for i in range(len(self.payload)) if i != index]
        self.payload = [self.payload[i] for i in keep]
        self.codes = self.codes[keep]
        self.binary = self.binary[keep]
        self._index_keys()
        self._refresh_sizes()
        self.save()
        return True

    def _needs_recalibration(self):
        count = len(self.payload)
        if count < MIN_CALIBRATION_ROWS:
            return False
        # first real fit, then again on every doubling
        return count >= max(MIN_CALIBRATION_ROWS,
                            2 * self.manifest.get("calibrated_at_count", 0))

    def recalibrate(self, encoder):
        """Refit calibration on this store's own vectors, re-embedding to do it.

        Requantizing the existing codes would compound the error already in
        them, so the documents are embedded again from scratch.
        """
        documents = [row.get("document") or "" for row in self.payload]
        if not documents:
            return
        vectors = quant.normalize(encoder.encode(documents, batch_size=16))
        self.int8_params = quant.fit_int8(vectors)
        self.binary_params = quant.fit_binary(vectors)
        self.codes = quant.quantize_int8(vectors, self.int8_params)
        self.binary = quant.quantize_binary(vectors, self.binary_params)
        self.manifest["calibrated_at_count"] = len(self.payload)
        self.manifest["calibration_source"] = "own"
