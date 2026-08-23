"""Vector quantization for the parts and sets databases.

Float32 embeddings are never stored. Every index keeps two quantized copies of
the same matrix, which together are ~5x smaller than the float original:

* **binary** - one bit per dimension, packed 8 to a byte (1024 dims -> 128 B per
  vector). Used as a coarse filter: a XOR plus a popcount over the whole corpus.
* **int8** - one byte per dimension with a per-dimension affine scale
  (1024 dims -> 1 KB per vector). Used to rescore the survivors of the coarse
  filter, and accurate enough that the ordering matches the float one.

The per-dimension calibration matters: a normalized 1024-d embedding has
components clustered around +-0.03, so a single global scale over [-1, 1] would
throw away almost all of the resolution.
"""

import numpy as np

# popcount of every byte value, so Hamming distance is a table lookup and a sum
_POPCOUNT = np.unpackbits(np.arange(256, dtype=np.uint8)[:, None], axis=1).sum(1).astype(np.uint16)


def normalize(matrix):
    """L2-normalize rows, so a dot product is a cosine similarity."""
    matrix = np.asarray(matrix, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=-1, keepdims=True)
    return matrix / np.maximum(norms, 1e-12)


# -- int8 -------------------------------------------------------------------

def fit_int8(matrix, clip_percentile=99.9):
    """Per-dimension affine calibration: x ~= (q + 128) * scale + offset.

    Ranges come from percentiles rather than min/max so that a single outlier
    dimension in one part does not compress the scale for all 5,000 others.
    """
    matrix = np.asarray(matrix, dtype=np.float32)
    lo = np.percentile(matrix, 100.0 - clip_percentile, axis=0).astype(np.float32)
    hi = np.percentile(matrix, clip_percentile, axis=0).astype(np.float32)
    # a dead dimension (lo == hi) would divide by zero
    span = np.maximum(hi - lo, 1e-8).astype(np.float32)
    return {"offset": lo, "scale": (span / 255.0).astype(np.float32)}


def quantize_int8(matrix, params):
    """Float rows -> int8 rows under a calibration from :func:`fit_int8`."""
    matrix = np.asarray(matrix, dtype=np.float32)
    q = np.rint((matrix - params["offset"]) / params["scale"])
    return np.clip(q - 128.0, -128, 127).astype(np.int8)


def dequantize_int8(codes, params):
    return (codes.astype(np.float32) + 128.0) * params["scale"] + params["offset"]


def int8_dot_terms(query, params):
    """Split ``dot(query, dequant(codes))`` into a codes-side and a constant part.

    ``dot(q, x) = dot(codes, scale * q) + dot(128 * scale + offset, q)``, so a
    query only needs one float vector prepared up front and the per-row work
    stays an int8 matmul.
    """
    query = np.asarray(query, dtype=np.float32)
    weights = params["scale"] * query
    bias = float(np.dot(128.0 * params["scale"] + params["offset"], query))
    return weights, bias


def int8_scores(codes, query, params):
    """Cosine similarity of ``query`` against int8-coded rows."""
    weights, bias = int8_dot_terms(query, params)
    return codes.astype(np.float32) @ weights + bias


# -- binary -----------------------------------------------------------------

def fit_binary(matrix):
    """Threshold each dimension at its corpus mean.

    Thresholding at zero is the usual choice, but Qwen3 embeddings have a clear
    per-dimension bias; centring on the mean keeps each bit close to a 50/50
    split and so keeps all 1024 bits informative.
    """
    matrix = np.asarray(matrix, dtype=np.float32)
    return {"threshold": matrix.mean(axis=0).astype(np.float32)}


def quantize_binary(matrix, params):
    """Float rows -> packed bits, 8 dimensions per byte."""
    matrix = np.asarray(matrix, dtype=np.float32)
    return np.packbits(matrix > params["threshold"], axis=-1)


def hamming_scores(packed, query_packed):
    """Similarity (higher is better) as negated Hamming distance."""
    xor = np.bitwise_xor(packed, query_packed)
    return -_POPCOUNT[xor].sum(axis=1).astype(np.int32)
