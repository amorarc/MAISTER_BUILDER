# Maister Builder

An LLM agent that designs LEGO models and writes them as **LDraw files you can
actually build out of real bricks** — not files that merely open in a viewer.

It plans a build, chooses parts from a catalogue of 5,878 real moulds, places
them on the stud lattice, then checks its own work three ways: a swept-volume
collision check, a stud-connectivity check, and a vision model looking at
rendered views of what it just made. What fails goes back to the builder with
the line number and the fix.

```bash
python -m maister.agent.run_agent "a red pickup truck" --out my_build
```

There is also a web app — chat on one side, a live three.js viewer that
re-renders on every write on the other.

**[PROJECT.md](PROJECT.md)** is the deep dive: the architecture, and the
findings that shaped it (what was measured, what it changed).

---

## Requirements

| | | |
|---|---|---|
| **Python 3.11+** | required | developed on 3.13 |
| **A HuggingFace token** | required | with the *"Make calls to Inference Providers"* permission — this is how the agent reaches an LLM |
| **The LDraw parts library** | required | ~570 MB, fetched by the setup script |
| **Node 18+ / npm** | for the web app | not needed for the CLI |
| **LeoCAD** | strongly recommended | without it there are no renders, and no renders means no vision feedback — half the checking |
| **The OMR set corpus** | optional | ~363 MB. Without it the agent still builds; it just cannot study how real sets solved a shape, and `copy_from_set` has nothing to copy |
| **An NVIDIA GPU** | optional | retrieval falls back to CPU automatically — slower to build indexes and search, not blocked |

Disk: about **1 GB** once the data is fetched, plus whatever `out/` grows to.

---

## Install

### 1. The code

```bash
git clone <your-fork-url> MAISTER_BUILDER
cd MAISTER_BUILDER

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

> **On `torch`:** the default wheel pulls CUDA libraries and is ~2.5 GB. If you
> have no NVIDIA GPU, install the CPU build first and everything still works:
> ```bash
> pip install torch --index-url https://download.pytorch.org/whl/cpu
> pip install -r requirements.txt
> ```

### 2. The token

Any one of these; the first hit wins:

```bash
huggingface-cli login                       # easiest, and shared across projects
# or
export HF_TOKEN=hf_...
# or
cp .env.example .env && $EDITOR .env        # .env is gitignored
```

Check it:

```bash
python -m maister.agent.run_agent --check-token
```

### 3. The data

```bash
./scripts/fetch_data.sh          # all of it; re-running skips what is done
./scripts/fetch_data.sh --force  # redo everything
SKIP_OMR=1 ./scripts/fetch_data.sh   # skip the slow optional corpus
```

The repository is 11 MB. Everything large is fetched or built here.

**What the repo already carries** — you do not need to build any of this:

| | size | what it is |
|---|---|---|
| `data/agent_prompts/` | 308 KB | the system prompt, in labelled blocks. This *is* the design |
| `data/parts/` | 7.4 MB | the measured part catalogue: footprints, stud grids, stacking heights, connection families, co-occurrence |
| `data/agent_creations/` | 84 KB | 15 models the agent built and saved |
| `data/agent_knowledge/` | 16 KB | 29 notes it worked out while building |

**What the script fetches:**

**1. The LDraw parts library → `data/lego_pieces/` (~570 MB, required)**
Downloads the official `complete.zip` (~145 MB) from
`library.ldraw.org/library/updates/complete.zip` and extracts every part,
subpart and primitive into one flat folder. Delegated to the project's own
`bootstrap_from_zip`, which leaves a `_bootstrap.done` marker so a re-run costs
nothing. **Nothing works without this** — it is where all part geometry comes
from, so no measuring, rendering or validating can happen until it is there.

**2. The OMR set corpus → `data/ldraw_omr_sets/` (~363 MB, optional)**
Scrapes `library.ldraw.org/omr` for 1,800 official sets as LDraw source, plus a
`metadata.csv` of theme, year and piece counts. **Slow** — it walks the index
page by page. Skip it with `SKIP_OMR=1`.
Without it the agent still builds; what it loses is the ability to study how
real sets solved a shape, and `copy_from_set` has nothing to graft from.

**3. The search indexes → `data/vector_db/` (~12 MB, built locally)**
Runs `python -m maister.retrieval.build_indexes`, which embeds the catalogues
already in the repo into four quantized indexes (parts, sets, creations, notes).
On first run this downloads the embedding and reranker models
(`Qwen3-Embedding-0.6B`, `Qwen3-Reranker-0.6B`, ~1.2 GB, cached by HuggingFace
under `~/.cache/huggingface`). Uses a GPU if there is one and CPU otherwise.

> **Doing it by hand**, if you would rather not run the script:
> ```bash
> python -m maister.database_creation.download_ldraw_omr   # step 2
> python -m maister.retrieval.build_indexes                # step 3
> python -m maister.database_creation.build_part_catalog   # rebuild the CSVs
> ```
> Step 1 has no standalone entry point; `build_part_catalog` seeds the library
> as its first action, so running that alone will also fetch it.

Total on disk once fetched: **about 1 GB**, plus whatever `out/` grows to.

### 4. LeoCAD (for renders)

The agent looks at its own work by rendering views and asking a vision model
what it sees. Without LeoCAD on `PATH` that channel is skipped and only the
geometric checks run.

```bash
# Debian/Ubuntu
sudo apt install leocad
# or download from https://www.leocad.org/download.html
leocad --version        # must be on PATH
```

`simulator/README.md` documents the AppImage setup this was developed against,
including the LPub3D instruction-booklet generator. The AppImages themselves are
not redistributed here — get them from upstream.

### 5. The web app (optional)

```bash
cd app/frontend && npm install && cd ../..
./app/start.sh          # backend on :8000, frontend on :5173
```

---

## Check it worked

```bash
python -m maister.agent.run_agent --self-test
```

It exercises the catalogue, geometry, the three checkers, writing and
validation, the retrieval indexes, the minifigure rules, and sweeps every
module for undefined names. If it passes, the install is sound.

Run it **after** `fetch_data.sh` — it needs the parts library, and it will say
so plainly if that is missing rather than failing somewhere deep:

```
This checkout is missing data it needs:

  the LDraw parts library   .../data/lego_pieces
      Nothing can be measured, rendered or validated without it.

Fetch and build all of it with:

    ./scripts/fetch_data.sh
```

Most of it is offline. Two parts are not, and both degrade rather than fail:
if **LeoCAD** is installed it renders the test model, and if a **token** is
present it then asks the vision model what it sees. Set `LDRAW_VISION=0` to
keep the whole run local.

---

## Usage

```bash
# build something
python -m maister.agent.run_agent "a small house with a pitched roof" --out house

# one agent, one conversation, no splitting into separate objects
python -m maister.agent.run_agent "a castle gatehouse" --flat

# check an existing file
python -m maister.agent.run_agent --validate out/house/model.ldr

# see the assembled system prompt
python -m maister.agent.run_agent --show-prompt
```

Output lands in `out/<name>/`: the `.ldr` model, the rendered views, the ops
that built it, and the run's chat history.

Open the result in any LDraw viewer — LeoCAD, LDView, Studio, or Bricklink's
online viewer.

---

## Layout

```
maister/
  agent/                 the agent: tools, the run loop, the orchestrator
    orchestrator.py        split -> build each -> assemble -> look
    agent.py               the loop: prompt -> tool calls -> validated file
    tools.py               every tool the model can call
    buildir.py             the op compiler — where the arithmetic lives
    lattice.py             which stud grid a part stands on (see PROJECT.md §1)
    brief.py               what it should look like, decided before anything
    blueprint.py           the construction planner
    requirements.py        what would make this finished
    style.py               is it any good? measured against 1,812 real sets
  environment_feedback/   the three checkers — no model involved
  retrieval/              hybrid keyword + vector search over parts and sets
  database_creation/      builders for the catalogue and corpora
app/
  backend/               FastAPI, 50 endpoints
  frontend/              React + three.js
data/
  agent_prompts/         the system prompt, in labelled blocks
  parts/                 the measured part catalogue (committed)
tools/                   corpus measurement scripts
scripts/fetch_data.sh    downloads and builds everything else
```

---

## Configuration

Everything is an environment variable with a default in
[`maister/agent/config.py`](maister/agent/config.py). The ones worth knowing:

| variable | default | what it does |
|---|---|---|
| `LDRAW_AGENT_MODEL` | `deepseek-ai/DeepSeek-V4-Flash-0731:cheapest` | the builder's model |
| `LDRAW_VISION` | `1` | `0` turns off render-and-look entirely |
| `LDRAW_PARALLEL_SUBBUILDS` | `6` | objects built at once |
| `LDRAW_MAX_OBJECTS` | `12` | ceiling on how many a request may split into |
| `LDRAW_BRIEF_CANDIDATES` | `5` | briefs sampled per object (see PROJECT.md §7) |
| `LDRAW_CRITIQUE_ROUNDS` | `0` | deliberately zero — see PROJECT.md §2 |

---

## Licence

**[PolyForm Noncommercial License 1.0.0](LICENSE)** — free for any
noncommercial use: personal projects, research, teaching, charities and
non-profits. Commercial use needs a separate licence from the copyright holder.

Two things worth knowing:

- This is **source-available, not open source**. The noncommercial restriction
  is a field-of-use limit, which the Open Source Definition does not permit, so
  GitHub will label the repository "non-standard licence". That is expected.
- PolyForm Noncommercial is written *for software*, which is why it is used
  here rather than CC BY-NC — Creative Commons themselves advise against CC
  licences for code.

### Third-party material

None of the following is covered by the licence above, and none of it is
redistributed in this repository:

| what | where it comes from | when |
|---|---|---|
| **LDraw parts library** | [library.ldraw.org](https://library.ldraw.org) | `scripts/fetch_data.sh`, step 1 |
| **OMR model corpus** (1,800 official sets) | [library.ldraw.org/omr](https://library.ldraw.org/omr) | `scripts/fetch_data.sh`, step 2 |
| **LeoCAD**, **LPub3D** | upstream projects | installed by you |
| **Embedding / reranker models** | HuggingFace | cached on first index build |

Each carries its own terms. Read them before redistributing any of it.

**On `data/parts/`.** Those CSVs *are* committed, and they are measurements
derived from the LDraw library — bounding boxes, stud coordinates, how often
each part appears across the corpus. They are shipped because rebuilding them
needs ~1 GB of downloads and a long parse, and because without them nothing
runs. They are derived data rather than a copy of the library, but if you plan
to relicense or sell anything built on them, check LDraw's terms first.

**No model files are shipped.** The demo fixtures this was developed against
were OMR models by named third-party authors and have been removed rather than
republished. `simulator/run_leocad.sh` and `simulator/make_instructions.sh`
therefore take a model path, or fall back to whatever the corpus download and
your own builds have produced.

LEGO® is a trademark of the LEGO Group, which does not sponsor, authorise or
endorse this project.
