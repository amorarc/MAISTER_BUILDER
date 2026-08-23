# Maister Builder

A React app for building LEGO models with the LDraw agent: a live 3D view of
the current build on the workbench, and a floating box over the Trace view to
tell the agent what to make of it.

## Run

Two processes. Terminal 1 — backend:

```bash
conda activate hf_env
cd <repo>
python -m uvicorn app.backend.main:app --reload --port 8000
```

Terminal 2 — frontend:

```bash
cd <repo>/app/frontend
npm run dev
```

Then open http://localhost:5173.

Or run both with `./app/start.sh`.

The backend needs a HuggingFace token; it resolves one from `HF_TOKEN`, a `.env`
at the project root, or the `huggingface-cli login` cache. The top bar shows a
"No HF token" badge if none is found — the viewer still works, only chat needs it.

### First-time setup: the vector databases

Part and set search run on local GPU embeddings. Build both indexes once (~45 s
on a laptop RTX 4070, ~9 MB on disk):

```bash
python -m maister.retrieval.build_indexes
```

Without them the agent still finds parts by keyword, but it cannot search by
description and the set-reference tools return nothing. See
[maister/retrieval/README.md](../maister/retrieval/README.md).

## What it does

- **New project** starts from a blank model and opens on the Trace, which is
  where the composer is; **Upload .ldr** opens an import dialog that takes
  dropped or browsed files (`.ldr`, `.mpd`, `.dat`) and reports what it found
  before opening them. Projects live in `out/projects/<id>/model.ldr`.
- **The composer** — `[+] [Describe what to build or change…] [Send]` — floats
  over the Model and Trace views; not over Source, where a box on top of the
  text is in the way of typing. Attached reference pictures stand above it as
  bare bricks, so the thing you aim at does not move when you attach one. Over
  the model the box is frosted while it is being ignored and fills in solid
  once there is something typed in it. It sends the request plus the current
  file contents to the agent, which edits the file with its own tools and
  validates before replying; the viewer refreshes automatically as each write
  lands, whichever view is on screen, and the read-out at the foot of the
  workbench tracks the step and tool in flight. The agent's working-out is not
  repeated here — the Trace is the account of a run.
- The **Model / Source / Trace** switch in the top bar swaps the viewer for the
  raw LDraw text — half the workbench each, text and preview — or for the graph
  of the run. **Rebuild** writes the edits back and revalidates.
- The top bar's actions are grouped by what they are for: the project (new,
  upload), the libraries (gallery, parts), and this model (save to gallery,
  instructions, export), with settings on its own at the end.
- **Export** downloads the current model as a `.ldr` file.
- The **cog** in the top bar opens Settings: which model the agent runs on and
  which HuggingFace provider serves it — the two halves of the router's
  `org/model:provider` id, edited separately. Stored in `out/settings.json`, so
  the choice outlives the process; the running agents are retargeted on save
  without losing their conversations. The same window downloads every project
  as one zip, or erases them all.
- **Copying from real sets** is a switch in the same window. On (the default)
  the agent may graft a finished assembly straight out of a released set with
  `copy_from_set` — a wheel arch, a wing, a torso — which makes better models
  faster and makes it impossible to say how much of one the agent designed. Off
  withdraws the tool entirely: the schema is never shown, the reference-set
  briefing inverts from "copy first, design second" to "read them, do not
  reproduce them", and the system prompt gains a section saying so. The 1,801
  sets stay fully readable either way — reading one is how a technique is
  learned, and that is a different act from lifting it. Expect simpler models;
  that is the point of the setting. `LDRAW_COPY_FROM_SET=0` is the same switch
  for the CLI.
- Stud-grid validation is reported in the rail foot, which breaks it down, and
  in the editor foot as a count of problems. Misaligned parts and fragmented
  submodels are listed along the bottom edge. The top bar deliberately says
  none of it — a fourth copy of the same sentence is not a fourth reader.

## How the pieces fit

```
app/frontend        React + Vite, three.js LDrawLoader for the 3D view
app/frontend/public/fonts   Bricolage Grotesque / DM Sans / JetBrains Mono,
                    self-hosted latin subsets so the UI is right offline
app/backend/main.py FastAPI: projects, chat runs, validation, LDraw library
app/backend/settings.py     the chosen model and provider, kept across restarts
maister/agent          the agent itself (tools, prompts, HF client)
maister/retrieval      local GPU embeddings + reranking over four quantized indexes
maister/environment_feedback   the collision + connectivity checkers
data/lego_pieces    the LDraw parts library, served at /ldraw
data/parts/LDConfig.ldr     colour definitions, served at /ldraw/LDConfig.ldr
data/ldraw_omr_sets 1,801 official LEGO models the agent uses as references
data/agent_creations  models the agent built and chose to keep
data/agent_knowledge  notes the agent wrote down while building
data/vector_db      the four quantized vector databases
```

The agent has a memory that outlives a run: it saves models worth reusing into
`data/agent_creations` and records what it worked out in `data/agent_knowledge`.
Those are searched by their own tools, kept strictly apart from the official
sets — an official set is evidence of good design, an agent creation is only
evidence of what the agent already tried.

Chat runs are executed on a worker thread and polled via `GET /api/runs/{id}`,
so a slow reasoning model never trips an HTTP timeout. Every event of the run is
also written to disk under `out/projects/<id>/traces/`, and the Trace view reads
that back as a graph while it is still growing.

The Trace's nodes are **iterations**, not turns: a new one begins only where the
agent called `validate_model` or `finish`, so one node is one attempt at the
model rather than one round trip to the API. Each node carries what went into it
and what came out — and the pictures. Renders and reference images are copied
into `traces/images/` as thumbnails named by content hash, because `out/renders`
holds only the newest build of each project and a trace has to show the model as
it was when the decision was made. They are served from
`GET /api/projects/{id}/traces/images/{name}` and never reach the agent itself:
the tools put them under an `_images` key that the loop strips out of every tool
result before the model sees it.

## Notes

- Each project keeps its own agent instance in memory, so chat history persists
  for the life of the backend process. Restarting the backend clears history but
  not the model files. **New chat** in the composer clears both on purpose.
- `components/ChatPanel.jsx` is the old docked chat, no longer mounted by
  anything. It is left on disk, with its stylesheet, in case the scrolling
  transcript is ever wanted back.
- `LDrawLoader` probes several paths per part, so the backend log shows 404s
  before each successful fetch. That is the loader's normal search behaviour, not
  a broken library — `setFileMap` would remove the extra requests if it ever
  matters.
