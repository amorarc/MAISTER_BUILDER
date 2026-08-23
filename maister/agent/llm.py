"""HuggingFace router client (OpenAI-compatible)."""

import os
import time
from pathlib import Path
from types import SimpleNamespace

from openai import OpenAI

from .config import (DEFAULT_MODEL, DEFAULT_TASK, DEFAULT_TEMPERATURE,
                     HF_BASE_URL, LLM_BACKOFF, LLM_BACKOFF_MAX, LLM_RETRIES,
                     LLM_TIMEOUT, PROJECT_ROOT, REASONING_EFFORT_OVERRIDE,
                     REASONING_MODELS, REASONING_PROFILES,
                     THINKING_MODE_OVERRIDE)

ENV_FILE = PROJECT_ROOT / ".env"
HF_CLI_TOKEN = Path.home() / ".cache" / "huggingface" / "token"


class MissingToken(RuntimeError):
    pass


def _from_env_file():
    """Read HF_TOKEN from a .env at the project root (KEY=value, # comments)."""
    if not ENV_FILE.is_file():
        return None
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() in ("HF_TOKEN", "HUGGINGFACE_TOKEN"):
            return value.strip().strip("'\"") or None
    return None


def _from_hf_cli():
    """Token cached by `huggingface-cli login`."""
    if not HF_CLI_TOKEN.is_file():
        return None
    return HF_CLI_TOKEN.read_text(encoding="utf-8").strip() or None


def resolve_token(api_key=None):
    """First hit wins. Returns (token, source) or (None, None)."""
    for value, source in (
        (api_key, "argument"),
        (os.environ.get("HF_TOKEN"), "HF_TOKEN environment variable"),
        (os.environ.get("HUGGINGFACE_TOKEN"), "HUGGINGFACE_TOKEN environment variable"),
        (_from_env_file(), f"{ENV_FILE}"),
        (_from_hf_cli(), f"{HF_CLI_TOKEN} (huggingface-cli login)"),
    ):
        if value:
            return value, source
    return None, None


def make_client(api_key=None):
    key, _ = resolve_token(api_key)
    if not key:
        raise MissingToken(
            "No HuggingFace token found. Create one at "
            "https://huggingface.co/settings/tokens (needs the "
            "'Make calls to Inference Providers' permission), then use any of:\n"
            "  1. export HF_TOKEN=hf_...                  (current shell)\n"
            f"  2. echo 'HF_TOKEN=hf_...' > {ENV_FILE}    (this project)\n"
            "  3. huggingface-cli login                   (all projects)"
        )
    # `timeout` because a deliberating model on a large context takes minutes
    # and the SDK default gives up well inside that. `max_retries` covers the
    # request that never got off the ground; a stream that dies half way is not
    # covered by it and is handled in `complete` below.
    return OpenAI(base_url=HF_BASE_URL, api_key=key, timeout=LLM_TIMEOUT)


def reasoning_args(model, task):
    """The reasoning arguments this model takes for this kind of call.

    Empty for a model that is not in REASONING_MODELS: sending another
    provider's knobs is at best ignored and at worst a 400.
    """
    name = (model or "").split(":")[0].strip().lower()
    if name not in REASONING_MODELS:
        return {}

    args = dict(REASONING_PROFILES.get(task) or REASONING_PROFILES[DEFAULT_TASK])
    if THINKING_MODE_OVERRIDE:
        args["thinking_mode"] = THINKING_MODE_OVERRIDE
    if REASONING_EFFORT_OVERRIDE:
        args["reasoning_effort"] = REASONING_EFFORT_OVERRIDE
    # reasoning_effort only means anything once the model is deliberating at
    # all, and a stack that reads one and not the other should not be told to
    # think hard in chat mode.
    if args.get("thinking_mode") != "thinking":
        args.pop("reasoning_effort", None)
    return args


class LLM:
    def __init__(self, client=None, model=DEFAULT_MODEL,
                 temperature=DEFAULT_TEMPERATURE,
                 task=DEFAULT_TASK,
                 stream=True):
        self.client = client or make_client()
        self.model = model
        self.temperature = temperature
        # What this client is for — "plan", "build" or "chat". It decides how
        # much deliberation the model is asked for; see REASONING_PROFILES.
        self.task = task
        self.reasoning = reasoning_args(model, task)
        # Cleared for good if the endpoint rejects the reasoning arguments, so
        # one refusal does not cost every later turn a retry.
        self.supports_reasoning = True
        self.supports_tools = True
        # Streamed so a caller can watch the reply being written. Turned off
        # for the rest of the run if a provider refuses.
        self.stream = stream

    def complete(self, messages, tools=None, on_delta=None, on_tool=None,
                 should_stop=None, task=None, on_retry=None):
        """One assistant turn.

        ``task`` overrides this client's own for a single call, which is how the
        builder pays for deliberation on the turns that decide geometry and not
        on the ones that only look something up.

        ``on_delta(text)`` is called with each piece of content as it arrives
        and ``on_tool(index, name, arguments)`` each time a tool call grows, so
        a caller can show the reply — and the call being composed — instead of
        waiting for the whole turn. The return value is the same either way: a
        message with ``.content`` and ``.tool_calls``.

        ``on_retry(attempt, exc)`` is called when the call died in transit and
        is about to be made again. See ``_transient``.
        """
        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }
        if tools and self.supports_tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        reasoning = {}
        if self.supports_reasoning:
            reasoning = (self.reasoning if task is None
                         else reasoning_args(self.model, task))
        kwargs.update(_reasoning_kwargs(reasoning))

        try:
            return self._attempt(kwargs, on_delta, on_tool, should_stop, on_retry)
        except Exception as e:
            # Checked before the tools branch below: both look like "invalid
            # parameter", and blaming the tools for a template complaint would
            # silently cost the agent every one of them.
            if reasoning and _looks_like_template_rejection(e):
                self.supports_reasoning = False
                kwargs.pop("extra_body", None)
                kwargs.pop("reasoning_effort", None)
                kwargs.pop("top_p", None)
                return self._attempt(kwargs, on_delta, on_tool, should_stop,
                                     on_retry)
            # Some router-hosted models reject the tools parameter. Retry once
            # without it so the run degrades to plain chat rather than dying.
            if tools and self.supports_tools and _looks_like_tool_rejection(e):
                self.supports_tools = False
                kwargs.pop("tools", None)
                kwargs.pop("tool_choice", None)
                return self._attempt(kwargs, on_delta, on_tool, should_stop,
                                     on_retry)
            # A provider that will not stream still answers in one piece.
            if self.stream and _looks_like_stream_rejection(e):
                self.stream = False
                return self._attempt(kwargs, on_delta, on_tool, should_stop,
                                     on_retry)
            raise

    def _attempt(self, kwargs, on_delta, on_tool=None, should_stop=None,
                 on_retry=None):
        """One turn, asked again if the connection drops under it.

        Safe to repeat: at the moment a request dies nothing it asked for has
        run — tool calls are executed by the caller after this returns — so a
        second attempt costs tokens and nothing else. What it saves is the whole
        run, which is what a dropped stream used to cost.

        The retry is not for the model saying something unhelpful, and never for
        a refusal, a bad parameter or a 400: those come back the same however
        many times they are asked. Only for transport — see ``_transient``.
        """
        delay = LLM_BACKOFF
        for attempt in range(1, LLM_RETRIES + 2):
            try:
                return self._request(kwargs, on_delta, on_tool, should_stop)
            except Exception as exc:
                last = attempt > LLM_RETRIES
                if last or not _transient(exc) or (should_stop and should_stop()):
                    raise
                if on_retry:
                    try:
                        on_retry(attempt, exc)
                    except Exception:
                        pass
                # Slept in slices so a stop pressed during the wait is honoured
                # rather than sat through.
                waited = 0.0
                while waited < delay:
                    if should_stop and should_stop():
                        raise
                    time.sleep(min(0.25, delay - waited))
                    waited += 0.25
                delay = min(delay * 2, LLM_BACKOFF_MAX)

    def _request(self, kwargs, on_delta, on_tool=None, should_stop=None):
        if not self.stream:
            resp = self.client.chat.completions.create(**kwargs)
            message = resp.choices[0].message
            if on_delta and message.content:
                on_delta(message.content)
            if on_tool:
                for i, tc in enumerate(message.tool_calls or []):
                    on_tool(i, tc.function.name, tc.function.arguments)
            return message
        return _consume(
            self.client.chat.completions.create(**kwargs, stream=True),
            on_delta, on_tool, should_stop)


def _consume(stream, on_delta, on_tool=None, should_stop=None):
    """Fold a stream of deltas back into one message.

    Content arrives as plain fragments; tool calls arrive in pieces keyed by
    index, with the name and the JSON arguments each split across any number of
    chunks, so both are concatenated per slot before being handed back.
    """
    content, reasoning = [], []
    calls = {}

    stopped = False
    for chunk in stream:
        # Checked per chunk, so Stop lands mid-sentence rather than after the
        # whole turn has been generated.
        if should_stop and should_stop():
            stopped = True
            stream.close()
            break
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta is None:
            continue

        piece = getattr(delta, "content", None)
        if piece:
            content.append(piece)
            if on_delta:
                on_delta(piece)

        thought = getattr(delta, "reasoning_content", None)
        if thought:
            reasoning.append(thought)
            if on_delta:
                on_delta(thought)

        for part in getattr(delta, "tool_calls", None) or []:
            slot = calls.setdefault(
                part.index, {"id": None, "name": [], "arguments": []})
            if getattr(part, "id", None):
                slot["id"] = part.id
            fn = getattr(part, "function", None)
            grew = False
            if fn is not None:
                if getattr(fn, "name", None):
                    slot["name"].append(fn.name)
                    grew = True
                if getattr(fn, "arguments", None):
                    slot["arguments"].append(fn.arguments)
                    grew = True
            # report the call as it is written, not once it is complete
            if grew and on_tool:
                on_tool(part.index, "".join(slot["name"]), "".join(slot["arguments"]))

    tool_calls = [
        SimpleNamespace(
            id=slot["id"] or f"call_{index}",
            type="function",
            function=SimpleNamespace(
                name="".join(slot["name"]),
                arguments="".join(slot["arguments"]),
            ),
        )
        for index, slot in sorted(calls.items())
    ]

    return SimpleNamespace(
        role="assistant",
        content="".join(content),
        reasoning_content="".join(reasoning) or None,
        # A half-written call cannot be run, so a stopped turn has none.
        tool_calls=None if stopped else (tool_calls or None),
        stopped=stopped,
    )


def _reasoning_kwargs(reasoning):
    """Spread the reasoning profile over the two places a request carries it.

    ``reasoning_effort`` and ``top_p`` are first-class request parameters, so
    they go at the top level. ``thinking_mode`` is an argument to the model's
    own prompt encoder, which OpenAI-compatible endpoints take under
    ``chat_template_kwargs``. A stack that templates client-side — the HF
    router does — ignores the second; sending it costs nothing and means the
    intent travels with the request to one that does not (vLLM, SGLang).
    """
    if not reasoning:
        return {}

    kwargs = {k: v for k, v in reasoning.items() if k != "thinking_mode"}
    mode = reasoning.get("thinking_mode")
    if mode:
        kwargs["extra_body"] = {"chat_template_kwargs": {"thinking_mode": mode}}
    return kwargs


# Failures that are the network rather than the request. Matched on the class
# name and on the message, and down the whole `__cause__` chain: the SDK wraps
# an `httpx.RemoteProtocolError` in an `APIConnectionError` sometimes and lets
# it through raw at others, and a stream that dies mid-body surfaces as
# `httpx.RemoteProtocolError: peer closed connection without sending complete
# message body (incomplete chunked read)` — the one that cost a thirteen-minute
# build.
_TRANSIENT_TYPES = (
    "RemoteProtocolError", "ProtocolError", "IncompleteRead",
    "ChunkedEncodingError", "APIConnectionError", "APITimeoutError",
    "ConnectError", "ConnectTimeout", "ReadError", "ReadTimeout",
    "WriteError", "PoolTimeout", "ConnectionResetError",
    "InternalServerError", "APIStatusError", "RateLimitError",
)
_TRANSIENT_TEXT = (
    "peer closed connection", "incomplete chunked read", "connection reset",
    "connection aborted", "server disconnected", "broken pipe",
    "timed out", "timeout", "temporarily unavailable", "overloaded",
    "bad gateway", "service unavailable", "gateway time-out",
    " 429", " 500", " 502", " 503", " 504",
)


def _transient(exc):
    """Is this the network having a bad moment, or a request that is wrong?"""
    seen = 0
    while exc is not None and seen < 6:
        if type(exc).__name__ in _TRANSIENT_TYPES:
            # An APIStatusError covers every 4xx as well; only the codes that
            # mean "ask again" count.
            status = getattr(exc, "status_code", None)
            if status is None or status == 429 or status >= 500:
                return True
        text = str(exc).lower()
        if any(mark in text for mark in _TRANSIENT_TEXT):
            return True
        exc = exc.__cause__ or exc.__context__
        seen += 1
    return False


def _looks_like_stream_rejection(exc):
    text = str(exc).lower()
    return "stream" in text


def _looks_like_template_rejection(exc):
    text = str(exc).lower()
    return any(s in text for s in ("chat_template", "thinking_mode", "extra_body",
                                   "reasoning_effort", "top_p"))


def _looks_like_tool_rejection(exc):
    text = str(exc).lower()
    return any(s in text for s in
               ("tool", "function", "not supported", "unsupported", "invalid parameter"))
