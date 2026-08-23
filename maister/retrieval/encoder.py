"""Qwen3-Embedding-0.6B, run locally on the GPU.

Qwen3 embedding models are decoder-only, so the sentence vector is the hidden
state of the **last** token. That requires left padding: with right padding the
final position of a short sequence is a pad token and the vector is garbage.

Queries are wrapped in an instruction ("Instruct: ...\\nQuery: ..."), documents
are embedded bare. That asymmetry is how the model was trained and is worth
about a point or two of retrieval quality, so both index building and querying
go through this module rather than calling the tokenizer directly.
"""

import threading

import numpy as np

from ..agent.config import EMBEDDING_MODEL, RETRIEVAL_DEVICE

MAX_TOKENS = 512  # part and set documents are short; the model allows 32k

# What the query is being matched against, one per database.
PART_INSTRUCTION = (
    "Given a description of a LEGO brick, retrieve the LDraw parts catalogue "
    "entry for the part that matches it."
)
SET_INSTRUCTION = (
    "Given a description of something to build out of LEGO, retrieve official "
    "LEGO sets that are a good reference for building it."
)
CREATION_INSTRUCTION = (
    "Given a description of something to build out of LEGO, retrieve models "
    "this agent has already built and saved that are similar to it."
)
NOTE_INSTRUCTION = (
    "Given a question about building with LEGO, retrieve notes this agent "
    "previously wrote down that help answer it."
)

_encoder = None
_encoder_lock = threading.Lock()


def resolve_device(preference=RETRIEVAL_DEVICE):
    import torch

    if preference and preference != "auto":
        return preference
    return "cuda" if torch.cuda.is_available() else "cpu"


class Encoder:
    """Lazily loaded embedding model. Use :func:`get_encoder` for the shared one."""

    def __init__(self, model_name=EMBEDDING_MODEL, device=None):
        import torch
        from transformers import AutoModel, AutoTokenizer

        self.model_name = model_name
        self.device = resolve_device() if device is None else device
        self.tokenizer = AutoTokenizer.from_pretrained(model_name, padding_side="left")
        self.model = AutoModel.from_pretrained(
            model_name,
            dtype=torch.float16 if self.device.startswith("cuda") else torch.float32,
        ).to(self.device).eval()
        self.dim = int(self.model.config.hidden_size)

    @staticmethod
    def _pool(hidden, attention_mask):
        """Last non-padding token of each sequence."""
        import torch

        # left padding puts the real last token at position -1 for every row
        if bool((attention_mask[:, -1] == 1).all()):
            return hidden[:, -1]
        lengths = attention_mask.sum(dim=1) - 1
        return hidden[torch.arange(hidden.shape[0], device=hidden.device), lengths]

    def encode(self, texts, batch_size=16, instruction=None, progress=None):
        """Embed a list of strings into an L2-normalized float32 array."""
        import torch

        if isinstance(texts, str):
            texts = [texts]
        if instruction:
            texts = [f"Instruct: {instruction}\nQuery: {t}" for t in texts]

        out = np.empty((len(texts), self.dim), dtype=np.float32)
        for start in range(0, len(texts), batch_size):
            chunk = texts[start:start + batch_size]
            batch = self.tokenizer(chunk, padding=True, truncation=True,
                                   max_length=MAX_TOKENS, return_tensors="pt").to(self.device)
            with torch.inference_mode():
                hidden = self.model(**batch).last_hidden_state
                vectors = self._pool(hidden, batch["attention_mask"])
                vectors = torch.nn.functional.normalize(vectors.float(), p=2, dim=-1)
            out[start:start + len(chunk)] = vectors.cpu().numpy()
            if progress:
                progress(min(start + len(chunk), len(texts)), len(texts))
        return out

    def encode_query(self, text, instruction):
        return self.encode([text], instruction=instruction)[0]


def get_encoder():
    """The one encoder, built on first use.

    Locked because subconstructions are built in parallel and each searches for
    parts. Without it, three builders reaching a cold encoder together each see
    `None` and each load a transformer — three copies of the same weights, three
    times the load, and on a GPU that is where the run runs out of memory.
    """
    global _encoder
    if _encoder is None:
        with _encoder_lock:
            if _encoder is None:
                _encoder = Encoder()
    return _encoder
