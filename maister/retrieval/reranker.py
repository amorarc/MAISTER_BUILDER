"""Qwen3-Reranker-0.6B, run locally on the GPU.

The reranker is not an embedding model: it is a small causal LM asked a yes/no
question about one (query, document) pair, and the score is the probability it
assigns to "yes" at the final position. That cross-attention between query and
document catches distinctions a single vector cannot - "slope *inverted* 2 x 2"
versus "slope 2 x 2" embed almost identically but rerank far apart.

It costs a forward pass per candidate, so it only ever sees the shortlist the
vector store already narrowed down.
"""

import threading

from ..agent.config import RERANKER_MODEL
from .encoder import resolve_device

MAX_TOKENS = 1024

PREFIX = (
    "<|im_start|>system\nJudge whether the Document meets the requirements based "
    "on the Query and the Instruct provided. Note that the answer can only be "
    '"yes" or "no".<|im_end|>\n<|im_start|>user\n'
)
SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"

PART_INSTRUCTION = (
    "Decide whether this LDraw catalogue part is the LEGO brick the query asks for."
)
SET_INSTRUCTION = (
    "Decide whether this LEGO set is a useful building reference for the query."
)
CREATION_INSTRUCTION = (
    "Decide whether this previously built model is a useful starting point for "
    "the query."
)
NOTE_INSTRUCTION = (
    "Decide whether this note contains information that helps answer the query."
)

_reranker = None
_reranker_lock = threading.Lock()


class Reranker:
    def __init__(self, model_name=RERANKER_MODEL, device=None):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self.model_name = model_name
        self.device = resolve_device() if device is None else device
        self.tokenizer = AutoTokenizer.from_pretrained(model_name, padding_side="left")
        self.model = AutoModelForCausalLM.from_pretrained(
            model_name,
            dtype=torch.float16 if self.device.startswith("cuda") else torch.float32,
        ).to(self.device).eval()

        self.yes_id = self.tokenizer.convert_tokens_to_ids("yes")
        self.no_id = self.tokenizer.convert_tokens_to_ids("no")
        # tokenized once; every pair reuses them
        self.prefix_ids = self.tokenizer.encode(PREFIX, add_special_tokens=False)
        self.suffix_ids = self.tokenizer.encode(SUFFIX, add_special_tokens=False)

    def _pair_ids(self, instruction, query, document):
        body = f"<Instruct>: {instruction}\n<Query>: {query}\n<Document>: {document}"
        budget = MAX_TOKENS - len(self.prefix_ids) - len(self.suffix_ids)
        middle = self.tokenizer.encode(body, add_special_tokens=False)[:budget]
        return self.prefix_ids + middle + self.suffix_ids

    def score(self, query, documents, instruction, batch_size=8):
        """P(yes) for each document, in the order given."""
        import torch

        if not documents:
            return []

        encoded = [self._pair_ids(instruction, query, d) for d in documents]
        scores = []
        for start in range(0, len(encoded), batch_size):
            chunk = encoded[start:start + batch_size]
            batch = self.tokenizer.pad({"input_ids": chunk}, padding=True,
                                       return_tensors="pt").to(self.device)
            with torch.inference_mode():
                logits = self.model(**batch).logits[:, -1, :]
                pair = torch.stack([logits[:, self.no_id], logits[:, self.yes_id]], dim=1)
                probs = torch.nn.functional.log_softmax(pair.float(), dim=1).exp()
            scores.extend(probs[:, 1].cpu().tolist())
        return scores

    def rerank(self, query, documents, instruction, top_k=None):
        """``[(original index, score), ...]`` best first."""
        scores = self.score(query, documents, instruction)
        order = sorted(range(len(scores)), key=lambda i: -scores[i])
        return [(i, scores[i]) for i in (order[:top_k] if top_k else order)]


def get_reranker():
    """The one reranker, built on first use - locked for the same reason as the
    encoder: parallel builders must not each load their own copy of it."""
    global _reranker
    if _reranker is None:
        with _reranker_lock:
            if _reranker is None:
                _reranker = Reranker()
    return _reranker
