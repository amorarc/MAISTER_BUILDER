"""Paths and settings for the LDraw model builder agent."""

import os
from pathlib import Path

# maister/agent/config.py -> maister/agent -> maister -> <project root>
PROJECT_ROOT = Path(__file__).resolve().parents[2]

DATA_DIR = PROJECT_ROOT / "data"
PROMPTS_DIR = DATA_DIR / "agent_prompts"
SKILLS_DIR = PROMPTS_DIR / "skills"
SYSTEM_PROMPT_FILE = PROMPTS_DIR / "system_prompt.md"
KNOWLEDGE_FILE = PROMPTS_DIR / "ldraw_knowledge.md"
# Turns a short request into a construction brief before the builder starts.
#
# Off by default: the plan_construction tool does the same job better - it is
# grounded in the catalogue and in real sets, and the agent calls it only when a
# build actually needs planning. Running both meant planning every build twice,
# which is slower and gives the builder two plans to reconcile. Set
# LDRAW_AGENT_PLANNER=1 to bring the pre-pass back.
PLANNER_PROMPT_FILE = PROMPTS_DIR / "planner_prompt.md"
PLANNER_ENABLED = os.environ.get("LDRAW_AGENT_PLANNER", "0") == "1"
# The plan_construction tool: a full construction plan, drawn up on demand and
# grounded in the parts catalogue and the official sets.
BLUEPRINT_PROMPT_FILE = PROMPTS_DIR / "blueprint_prompt.md"
# The first thing a request meets: split into atomic subconstructions, one per
# free-standing object, before anything is planned or built.
DECOMPOSE_PROMPT_FILE = PROMPTS_DIR / "decompose_prompt.md"
# The design brief: what this object should *look like*, decided before anything
# works out where a brick goes. See brief.py for why it is a call of its own.
BRIEF_PROMPT_FILE = PROMPTS_DIR / "brief_prompt.md"
# The acceptance criteria: what has to be true before a build may end, written
# once per object and then checked at the end of every iteration. See
# requirements.py - this is what replaced the agent deciding for itself that it
# had finished.
REQUIREMENTS_PROMPT_FILE = PROMPTS_DIR / "requirements_prompt.md"
REQUIREMENTS_CHECK_PROMPT_FILE = PROMPTS_DIR / "requirements_check_prompt.md"
# The third way to answer a requirement: not from a picture and not from the
# geometry checker, but from the .ldr file's own contents. Code counts the
# parts and the colours exactly; this prompt is the model's half - deciding
# what "green" or "round bricks" covers. See requirements.inventory.
REQUIREMENTS_SOURCE_PROMPT_FILE = PROMPTS_DIR / "requirements_source_prompt.md"
# Off switches, so a run can fall back to the old generic gate if the checker
# is unreachable or the behaviour ever needs isolating.
REQUIREMENTS_ENABLED = os.environ.get(
    "LDRAW_AGENT_REQUIREMENTS", "1") not in ("0", "false", "no")
BRIEF_ENABLED = os.environ.get("LDRAW_AGENT_BRIEF", "1") not in ("0", "false", "no")
# Prompt blocks assembled into the system prompt, in filename order.
CONTEXT_DIR = PROMPTS_DIR / "context"

PARTS_CATALOG = DATA_DIR / "parts" / "parts_catalog.csv"
PART_SET_USAGE = DATA_DIR / "parts" / "part_set_usage.csv"
# How often real sets place each part turned rather than square. Mined from the
# corpus by build_technique_notes.py; read per part by catalog.turn_share.
PART_ROTATION = DATA_DIR / "parts" / "part_rotation.csv"
# What a minifigure is seen holding, and where in the hand it goes.
# Built by maister/database_creation/build_minifig_grips.py.
HELD_PARTS = DATA_DIR / "parts" / "minifig_held.csv"
PARTS_DIR = DATA_DIR / "lego_pieces"

# Official Model Repository sets: one .mpd per model plus a metadata.csv
OMR_SETS_DIR = DATA_DIR / "ldraw_omr_sets"
SETS_METADATA = OMR_SETS_DIR / "metadata.csv"

# Models the agent built and chose to keep, kept apart from the human-designed
# OMR sets so the two can never be confused for one another
AGENT_CREATIONS_DIR = DATA_DIR / "agent_creations"
CREATIONS_METADATA = AGENT_CREATIONS_DIR / "metadata.json"
CREATIONS_MODELS_DIR = AGENT_CREATIONS_DIR / "models"

# Things the agent worked out and wrote down: "part X is good for trees"
AGENT_KNOWLEDGE_DIR = DATA_DIR / "agent_knowledge"
NOTES_FILE = AGENT_KNOWLEDGE_DIR / "notes.json"

# Four independent quantized vector databases, built by maister/retrieval.
# parts and sets are static; creations and notes grow as the agent works.
VECTOR_DB_DIR = DATA_DIR / "vector_db"
PARTS_INDEX_DIR = VECTOR_DB_DIR / "parts"
SETS_INDEX_DIR = VECTOR_DB_DIR / "sets"
CREATIONS_INDEX_DIR = VECTOR_DB_DIR / "creations"
NOTES_INDEX_DIR = VECTOR_DB_DIR / "notes"

# Local GPU retrieval models
EMBEDDING_MODEL = os.environ.get("LDRAW_EMBED_MODEL", "Qwen/Qwen3-Embedding-0.6B")
RERANKER_MODEL = os.environ.get("LDRAW_RERANK_MODEL", "Qwen/Qwen3-Reranker-0.6B")
# "cuda", "cpu", or "auto"
RETRIEVAL_DEVICE = os.environ.get("LDRAW_RETRIEVAL_DEVICE", "auto")
# Reranking costs a forward pass per candidate; off by default for the tools
# that are called in a tight loop, on for the ones the agent calls rarely.
RERANK_ENABLED = os.environ.get("LDRAW_RERANK", "1") not in ("0", "false", "no")

OUT_DIR = PROJECT_ROOT / "out"
# parts/ and p/ symlinks into PARTS_DIR, built on demand by library.py
LIBRARY_ROOT = OUT_DIR / ".ldraw_lib"

# --------------------------------------------------------------------------
# Visual feedback
#
# LeoCAD renders a model to PNG with no display at all and in about a third of
# a second, which is cheap enough to do on every write. The pictures are what
# the user looks at; they are also the only way the agent can find out that a
# model which validates perfectly does not look like a car.
#
# The builder is a text model, so it cannot read them itself. A vision model is
# asked to instead, and hands back a critique in words - that is the whole of
# the visual feedback loop.
RENDERS_DIR = OUT_DIR / "renders"
# LeoCAD preset viewpoints: front, back, left, right, top, bottom, home.
# "home" is the 3/4 view that shows the shape; the axis views are what catch a
# roof that overhangs or a wheel that floats.
# Four 3/4 views a quarter turn apart, plus two axis views.
#
# It used to be one 3/4 view and three axis views, and the one was the problem:
# everything on the far side of the model was rendered only edge-on or from
# directly above, so anything standing behind anything else was never actually
# seen. In the build that prompted this, a minifigure's axe was hidden behind
# the tree trunk in `home` and plainly visible from the opposite corner - the
# critic could not report what it was never shown.
#
# Orbiting the camera fixes it: between four corners 90° apart there is nowhere
# left for a part to hide. `front` and `top` stay because an axis view is what
# reads a level or a footprint honestly, where a 3/4 view foreshortens both.
# `right` goes: the orbit covers it, and each extra tile costs the vision model
# resolution on all the others.
RENDER_VIEWS = ("home", "orbit90", "orbit180", "orbit270", "front", "top")

# Views LeoCAD has no preset for, as (latitude, longitude) in degrees.
#
# `home` is itself latitude 30, longitude 45 - checked by rendering both and
# comparing - so the orbit starts a quarter turn on from it and goes round.
# Named for what the camera did rather than for a compass point: which face of
# a model is its "front" is the model's business, but "the same model turned 90
# degrees" is true of every one of them.
RENDER_ANGLES = {
    "orbit90": (30, 135),
    "orbit180": (30, 225),
    "orbit270": (30, 315),
}
RENDER_SIZE = (512, 512)
RENDER_TIMEOUT = 60

# Any multimodal model the router serves. This is not the builder: it looks at
# pictures and answers in words, and it is called a handful of times per build.
#
# All four below were checked against this project's renders. Qwen3-VL leads
# because it was the one that read a small red car as a small red car and
# listed no faults - the others each invented one. A vision critic that reports
# problems a model does not have is worse than none, since the builder will go
# and "fix" them.
VISION_MODEL = os.environ.get("LDRAW_VISION_MODEL",
                              "Qwen/Qwen3.6-35B-A3B:cheapest")
# Tried in order when the configured model cannot be reached, so a provider
# having a bad minute costs the critique a second rather than the build its
# eyes. Skipped entirely when LDRAW_VISION_MODEL was set by hand: that is
# someone choosing a model, not asking for a search.
VISION_FALLBACKS = (
    "Qwen/Qwen2.5-VL-72B-Instruct",
    "meta-llama/Llama-4-Scout-17B-16E-Instruct",
    "google/gemma-3-27b-it",
)
VISION_MODEL_PINNED = bool(os.environ.get("LDRAW_VISION_MODEL"))
# Set to 0 to render without ever asking a vision model - the pictures are
# still written for the user, the agent just gets no critique.
VISION_ENABLED = os.environ.get("LDRAW_VISION", "1") not in ("0", "false", "no")
# A critique is a paragraph, not an essay, and a wandering one costs the build
# a step for nothing.
VISION_TEMPERATURE = float(os.environ.get("LDRAW_VISION_TEMPERATURE", "0.2"))
# A critique is a paragraph of JSON. This is deliberately not generous: with
# thinking switched off (below) the answer is a few hundred tokens, so a reply
# that runs past this is a model that started deliberating anyway - and failing
# fast on that is cheaper than paying for pages of monologue and then throwing
# them away.
VISION_MAX_TOKENS = int(os.environ.get("LDRAW_VISION_MAX_TOKENS", "2000"))
# Describing the reference picture is the exception, and it gets its own
# budget. It is asked for the whole object, then every major part, then every
# detail on every part, then every marking and finish on those details - four
# passes over the picture in one answer, plus a walk round all six faces, where
# a critique is a paragraph. Held to the critique's limit it runs out mid-JSON,
# and a description that will not parse is worth nothing at all: the builder
# falls back to prose it cannot act on, or to inventing what was cut off.
# Spent once per picture, and the answer is stored, so this is not a per-step
# cost.
#
# 9000 rather than the 5000 it was, because the passes above went from three to
# four and every part now carries its faces, its angles and its openings. The
# risk this number guards against is asymmetric: spending tokens on a
# description nobody needed costs one call, and truncating one costs the project
# its specification - a cut-off answer is deliberately never cached, so the next
# run pays again and is cut off in the same place.
VISION_DESCRIBE_MAX_TOKENS = int(
    os.environ.get("LDRAW_VISION_DESCRIBE_MAX_TOKENS", "9000"))

# The critic must answer, not deliberate. Its reasoning is worse than useless
# here: it arrives as `reasoning_content` rather than `content`, it is prose
# instead of the JSON the builder can act on, and it eats the whole token
# budget before the model ever writes its conclusion.
#
# There is no single way to ask for this - every stack spells it differently -
# so all of these go in the same chat_template_kwargs and whichever one the
# serving stack understands wins. The rest are ignored, which costs nothing.
#
#   enable_thinking  Qwen3 and Qwen3.6
#   thinking_mode    DeepSeek-V4
#   thinking         several vLLM builds
VISION_NO_THINKING = os.environ.get("LDRAW_VISION_THINKING", "0") in ("0", "false", "no")
VISION_TEMPLATE_KWARGS = {
    "enable_thinking": False,
    "thinking_mode": "chat",
    "thinking": False,
}

# --------------------------------------------------------------------------
# Grafting from real sets
#
# `copy_from_set` lifts an assembly out of a released set and puts it in the
# model being built. It is the single biggest quality lever this project has -
# a real designer's wheel arch beats anything the agent works out from scratch
# - and for exactly that reason it makes one question unanswerable: how much of
# a finished model did the agent actually design?
#
# So it can be switched off. Not because grafting is wrong, but because a run
# with it off is the only run that measures the builder rather than the corpus.
# The sets stay readable either way: reading one teaches how a thing is built,
# and that is a different act from copying it out.
#
# This is the default and the CLI's switch. The app overrides it per-process
# from out/settings.json - see tools.set_copy_from_set - so the choice made in
# the settings dialog outlives the backend that was running when it was made.
COPY_FROM_SET_ENABLED = os.environ.get(
    "LDRAW_COPY_FROM_SET", "1") not in ("0", "false", "no")

CHECKER_DIR = PROJECT_ROOT / "maister" / "environment_feedback"

# HuggingFace router, OpenAI-compatible
HF_BASE_URL = "https://router.huggingface.co/v1"

# How long one model call may take, and how many times a call that dies in
# transit is tried again.
#
# Both exist because of the same failure: a build ran for thirteen minutes and
# ended with `RemoteProtocolError: peer closed connection without sending
# complete message body`. The provider dropped the stream part-way through a
# turn; the exception came out of the agent loop, the subconstruction was marked
# "not built", and thirteen minutes of work was reported as a failure with a
# half-written model left on the workbench.
#
# A dropped stream is not a decision anybody made. It is weather, and the answer
# to weather is to ask again - the turn has executed nothing at the point it
# dies, so re-sending it is safe. The timeout is generous rather than tight for
# the same reason a step limit is gone: a deliberating model on a big context
# genuinely takes minutes, and cutting it off at the default ten produces
# exactly the error this is here to survive.
LLM_TIMEOUT = float(os.environ.get("LDRAW_LLM_TIMEOUT", "1800"))
LLM_RETRIES = max(0, int(os.environ.get("LDRAW_LLM_RETRIES", "4")))
# Seconds before the first retry; doubles each time, capped.
LLM_BACKOFF = 2.0
LLM_BACKOFF_MAX = 30.0
# DeepSeek-V4-Flash-0731 specifically: it is the build listed in
# REASONING_MODELS below, so it is the one that actually receives the
# thinking_mode / reasoning_effort arguments this project tunes. The plain
# DeepSeek-V4-Flash id ignores them silently, which makes every profile here a
# no-op. ":cheapest" is a router routing policy, not part of the model name.
DEFAULT_MODEL = os.environ.get("LDRAW_AGENT_MODEL",
                               "deepseek-ai/DeepSeek-V4-Flash-0731:cheapest")
# The planning passes can run on a different model from the builder - planning
# rewards reasoning, building rewards obedience to a format.
BLUEPRINT_MODEL = os.environ.get("LDRAW_BLUEPRINT_MODEL", DEFAULT_MODEL)
# Well below the builder's 1: a plan is a JSON document with arithmetic in it,
# and the creativity that makes a good reply makes a rambling plan.
BLUEPRINT_TEMPERATURE = float(os.environ.get("LDRAW_BLUEPRINT_TEMPERATURE", "0.3"))

# The design brief runs hot, and it is the only call here that does.
#
# Planning at 0.3 is right for the arithmetic - a rambling plan is a bad plan -
# but those two jobs were sharing one temperature, and the arithmetic was
# winning. "What should this look like" and "where does each brick go" want
# opposite settings, so they are two calls now: this one decides the look at 1.0
# and hands the planner a decision to be careful *about*.
BRIEF_MODEL = os.environ.get("LDRAW_BRIEF_MODEL", DEFAULT_MODEL)
BRIEF_TEMPERATURE = float(os.environ.get("LDRAW_BRIEF_TEMPERATURE", "1.0"))

# Verbalized sampling: how many briefs the model is asked to write in one reply,
# and which end of its own distribution one is then taken from.
#
# The point is not to get five briefs. It is that a model asked for *one* answer
# returns the most typical one - that is what mode collapse is, and the cause is
# a bias in preference data toward familiar text rather than anything about
# sampling (arXiv:2510.01171). Temperature does not reach it: raising it
# re-words the median answer instead of moving it, which is measured
# (arXiv:2602.20408) and is what this project found by hand - see brief.py.
#
# Asking for a distribution does reach it. The model can name its own less
# likely answers perfectly well when it is asked to enumerate rather than to
# answer, and taking one from the tail is then a choice rather than a
# resampling. Reported at 1.6-2.1x the diversity of direct prompting, with no
# measured cost to quality.
#
# One call either way: the candidates come back in a single reply. Set
# LDRAW_BRIEF_CANDIDATES=1 to go back to asking for one brief.
BRIEF_CANDIDATES = max(1, int(os.environ.get("LDRAW_BRIEF_CANDIDATES", "5")))
# Candidates at or below this probability are the tail worth choosing from.
# 0.2 is "no more likely than an even split of five", so it keeps whatever the
# model itself considered unobvious and drops what it led with. If nothing
# qualifies - a model that spread its five evenly - the least likely is taken,
# which is the same rule at the only place it can still be applied.
BRIEF_TAIL_CEILING = float(os.environ.get("LDRAW_BRIEF_TAIL_CEILING", "0.2"))

# --------------------------------------------------------------------------
# Reasoning controls
#
# DeepSeek-V4 encodes two prompt modes: "thinking" wraps reasoning in
# <think>...</think> before the reply, "chat" closes the block immediately so
# the model answers directly. See the model's encoding/README.md.
#
# DeepSeek-V4-Flash-0731 adds a second knob on top of that one:
#
#   encode_messages(messages, thinking_mode="thinking", reasoning_effort="max")
#
# thinking_mode decides *whether* it deliberates, reasoning_effort ("low",
# "high", "max") *how much* once it does. The card also asks for temperature 1.0
# and top_p 0.95 in agentic scenarios.
# https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-0731
#
# Only models listed here are sent these arguments; anything else is left with
# whatever defaults its own serving stack applies. Compared case-insensitively
# against the model id with any ":provider" routing suffix removed, so
# "deepseek-ai/DeepSeek-V4-Flash-0731:cheapest" still matches.
REASONING_MODELS = ("deepseek-ai/deepseek-v4-flash-0731",)

# What each kind of call is worth in deliberation.
#
# Thinking is bought in one place only: the builder turns that write the .ldr
# file, at effort "low" - enough to check an arithmetic step without turning
# every turn into an essay. Everything else runs in chat mode.
#
# Planning used to think too, and it does not any more. A plan is a long
# structured JSON document, and a model that deliberates before writing one
# spends its output budget on the reasoning and then runs out mid-document -
# the call never finishes and there is no plan to show for the wait. Chat mode
# starts writing the plan immediately, which is what this call needs.
REASONING_PROFILES = {
    # plan_construction, and the optional planner pre-pass
    "plan": {"thinking_mode": "chat"},
    # the builder loop: the turns that write the .ldr file
    "build": {"thinking_mode": "thinking", "reasoning_effort": "low",
              "top_p": 0.95},
    # titles, chat, anything that does not place a brick
    "chat": {"thinking_mode": "chat"},
}
DEFAULT_TASK = "chat"

# Escape hatches, for pinning a run to one setting from the shell. Unset means
# "let the task decide"; setting either one overrides every profile above.
THINKING_MODE_OVERRIDE = os.environ.get("LDRAW_AGENT_THINKING_MODE", "").strip() or None
REASONING_EFFORT_OVERRIDE = os.environ.get("LDRAW_AGENT_REASONING_EFFORT", "").strip() or None

# How many turns of the loop an agent gets. **0 means no limit**, which is the
# default: a run goes until it calls `finish`, gives up, or is stopped.
#
# There used to be a budget everywhere - 50 here, 24 per subconstruction, 10 for
# the assembly pass - and every one of them ended runs that were still working.
# A build that spends four turns grafting from a set and four repairing what the
# critic saw has not gone wrong; it has done the thing it was asked to do, and
# cutting it off at twenty-four leaves an unfinished model on the workbench with
# `finish` never called and its gate never run. The step limit was, in practice,
# the most common way for a build to fail.
#
# What ends a run now: `finish`, `give_up`, an unrecoverable error, or the stop
# button - which also writes a resume snapshot, so stopping is cheap. Set
# LDRAW_MAX_STEPS to put a ceiling back for one session.
DEFAULT_MAX_STEPS = max(0, int(os.environ.get("LDRAW_MAX_STEPS", "0") or 0))
DEFAULT_TEMPERATURE = 1
