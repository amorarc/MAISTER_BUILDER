"""Which model the agent runs on, and where it runs.

The HuggingFace router takes both halves in one string: ``org/model:provider``,
where the provider suffix is optional and, when absent, lets the router pick.
That is a single opaque id to everything downstream, so it is stored split in
two here - a model and a provider are separate decisions to the person making
them, even though they travel as one field.

Kept in ``out/settings.json`` rather than in the browser: the agent runs on the
backend, so a setting that only existed in localStorage would be a preference
the thing it configures never sees.
"""

import json
import threading

from maister.agent.config import (COPY_FROM_SET_ENABLED, DEFAULT_MODEL,
                                  OUT_DIR, VISION_MODEL)

SETTINGS_FILE = OUT_DIR / "settings.json"

# Routing policies, not providers: the router reads them as "choose for me, on
# this criterion". Listed first because they are what most people want.
POLICIES = ["cheapest", "fastest"]

# Providers that can be named directly. Not exhaustive and not enforced - the
# router gains providers faster than this list will be updated, so an unknown
# value is passed through and the router gets to reject it.
PROVIDERS = [
    "hf-inference",
    "together",
    "fireworks-ai",
    "novita",
    "nebius",
    "hyperbolic",
    "sambanova",
    "groq",
    "cerebras",
    "nscale",
]

# Suggestions for the model field, which is free text. Every one of these is a
# guess about what a given HF account can reach; the field accepts any id.
SUGGESTED_MODELS = [
    # the one build the agent sends reasoning arguments to; see
    # maister/agent/config.py REASONING_MODELS
    "deepseek-ai/DeepSeek-V4-Flash-0731",
    "deepseek-ai/DeepSeek-V4-Flash",
    "deepseek-ai/DeepSeek-V3.2-Exp",
    "Qwen/Qwen3-Coder-480B-A35B-Instruct",
    "moonshotai/Kimi-K2-Instruct",
    "zai-org/GLM-4.6",
    "openai/gpt-oss-120b",
    "meta-llama/Llama-3.3-70B-Instruct",
]

# The second model: the one that looks at the renders and says whether the
# build resembles what was asked for. It must be multimodal - a text-only id
# here means every critique fails and the agent builds blind.
#
# The first three were checked against this project's contact sheets and
# answered in the structured shape the builder can act on.
SUGGESTED_VISION_MODELS = [
    "Qwen/Qwen3.6-35B-A3B",
    "Qwen/Qwen3-VL-235B-A22B-Instruct",
    "Qwen/Qwen2.5-VL-72B-Instruct",
    "meta-llama/Llama-4-Scout-17B-16E-Instruct",
    "google/gemma-3-27b-it",
]

_lock = threading.Lock()


def split_model(model_id):
    """``org/model:provider`` -> ``("org/model", "provider")``.

    A bare id keeps the whole string as the model: only the part after the last
    colon is ever a provider, and a model id without one has no provider at all.
    """
    base, sep, provider = (model_id or "").rpartition(":")
    if not sep:
        return (model_id or "").strip(), ""
    return base.strip(), provider.strip()


def join_model(model, provider):
    """The two halves back into the one string the router expects."""
    model = (model or "").strip()
    provider = (provider or "").strip()
    return f"{model}:{provider}" if provider else model


DEFAULT_MODEL_NAME, DEFAULT_PROVIDER = split_model(DEFAULT_MODEL)
DEFAULT_VISION_MODEL_NAME, DEFAULT_VISION_PROVIDER = split_model(VISION_MODEL)

# Every field this file stores, and where its default comes from. Kept as one
# table so adding a third model later is one line rather than four edits.
FIELDS = {
    "model": lambda: DEFAULT_MODEL_NAME,
    "provider": lambda: DEFAULT_PROVIDER,
    "vision_model": lambda: DEFAULT_VISION_MODEL_NAME,
    "vision_provider": lambda: DEFAULT_VISION_PROVIDER,
}
# Fields that must not be blanked to empty; a provider legitimately can be.
_REQUIRED = ("model", "vision_model")

# The same table for the switches. Separate from FIELDS because the reading is
# different in kind: an empty string is a meaningful provider and a missing
# boolean is not a value at all, so the two cannot share one "if it is a str"
# clause without one of them getting the wrong answer.
BOOL_FIELDS = {
    # Whether the agent may lift assemblies out of released sets. Off is the
    # setting that makes a build the agent's own work - see
    # maister/agent/config.py COPY_FROM_SET_ENABLED.
    "copy_from_set": lambda: COPY_FROM_SET_ENABLED,
}


def _defaults():
    return {**{name: default() for name, default in FIELDS.items()},
            **{name: default() for name, default in BOOL_FIELDS.items()}}


def load():
    """The stored settings, falling back to the configured default per field.

    A file that has been hand-edited into nonsense is treated as absent rather
    than fatal: the agent should still start. A file written before the vision
    model was configurable simply has no entry for it, and gets the default -
    which is why each field is read on its own rather than the whole document
    being accepted or rejected together.
    """
    values = _defaults()
    try:
        stored = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return values
    if not isinstance(stored, dict):
        return values
    for name in FIELDS:
        value = stored.get(name)
        if not isinstance(value, str):
            continue
        if name in _REQUIRED and not value.strip():
            continue
        values[name] = value.strip()
    for name in BOOL_FIELDS:
        value = stored.get(name)
        if isinstance(value, bool):
            values[name] = value
    return values


def save(**changes):
    """Write any subset of the fields through. Returns the settings as they now are."""
    with _lock:
        values = load()
        for name, value in changes.items():
            if value is None:
                continue
            if name in FIELDS:
                values[name] = value.strip()
            elif name in BOOL_FIELDS:
                values[name] = bool(value)
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(values, indent=2) + "\n", encoding="utf-8")
    return values


def copy_from_set():
    """Whether grafting from released sets is allowed, as stored."""
    return bool(load()["copy_from_set"])


def effective_model():
    """The id to hand the router for the builder."""
    values = load()
    return join_model(values["model"], values["provider"])


def effective_vision_model():
    """The id to hand the router for the critic that looks at the renders."""
    values = load()
    return join_model(values["vision_model"], values["vision_provider"])
