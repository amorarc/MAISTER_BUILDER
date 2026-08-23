# Maister Builder

An LLM agent that designs LEGO models and writes them as LDraw files that can be
built out of real bricks — not files that merely open in a viewer.

This document is the map: what the system is, how it is put together, and — the
half that is harder to recover from the code — **what has been learned by
running it**, with the evidence behind each finding. Most of the architecture
here exists because something was measured and found wanting. The findings are
in [What has been learned](#what-has-been-learned).

---

## 1. The shape of the problem

Text-to-3D is a solved-ish problem for meshes. LEGO is a different problem, and
the differences are what the whole design answers to:

- **The primitive set is fixed and enormous.** 5,878 catalogued parts. You
  cannot extrude a shape; you can only choose parts that exist and place them.
- **Everything lands on a lattice.** 20 LDU per stud, 24 LDU per brick, 8 per
  plate. A placement that is not on the grid is not a placement.
- **The lattice has a phase, and nothing states it.** A part's studs sit at a
  fixed offset from its origin that depends on the part. A 6×6 plate at
  x = −180 and a 1×1 plate at x = 140 are both on multiples of 20 and are still
  **half a stud apart and unable to connect**. This is computable and, before
  `lattice.py`, nothing computed it.
- **Correct is not the same as good.** A tower of ninety identical 2×2 bricks
  passes every physical check and is not a model of anything.

The last point is the one the project has spent the most time on and the one it
has least solved. See finding 9.

---

## 2. Architecture

### 2.1 The run, end to end

`maister/agent/orchestrator.py` runs one petition in four beats:

```
petition
   │
   ├─ 0. survey ......... read the model file as it stands, render it, look at it
   │                      (survey.py) — "what is already on the bench"
   │
   ├─ 1. split .......... one subconstruction per free-standing object
   │                      (decompose.py) — a request for one splits into one
   │
   ├─ 2. build each ..... per object, in parallel, up to 6 at a time:
   │        ├─ brief ..... what it should LOOK like        (brief.py, temp 1.0)
   │        ├─ checklist . what would make it FINISHED     (requirements.py)
   │        ├─ refsets ... real sets that already built it (refsets.py)
   │        ├─ recall .... what THIS agent built before    (recall.py)
   │        └─ agent ..... its own conversation, own file  (agent.py)
   │
   ├─ 3. assemble ....... compose the finished objects into one MPD
   │                      (assembly.py) — measured bounding boxes, not guesses
   │
   └─ 4. look ........... validate and render the whole scene
```

Every beat reports as it happens. A run that dies in beat 2 still leaves two
finished subbuilds and a picture of each — the alternative, nothing at all until
everything works, is the failure mode the whole design is against.

### 2.2 Why the passes are separate calls

Each pre-pass exists because one call was doing two jobs badly:

| pass | question | temperature | why separate |
|---|---|---|---|
| `decompose` | how many objects? | — | one builder holding a house *and* a tree produces one pile with an overlap |
| `brief` | what should it look like? | **1.0** | creativity |
| `blueprint` | where does each brick go? | **0.3** | arithmetic — a plan that gets creative does not add up |
| `requirements` | what would make it finished? | — | the brief is direction, not a contract with the user |

`brief` and `blueprint` were one call. The arithmetic was winning, and every
model came out a box.

### 2.3 The builder's action space

The agent has ~15 tools. Three matter most:

**`build_ops`** — the executable build sequence (`buildir.py`). The builder says
*what* it wants placed and the compiler works out the numbers:

```json
{"op": "row", "part": "3941", "colour": 2, "at": [40, -216, 0], "count": 5}
```

The spacing is **never an input**. A 2×2 round brick is 40 LDU across; written
by hand as a row at a 20 LDU pitch, every pair shares a full stud of plastic and
the model cannot be built. That bug is not expressible here, because there is
nowhere to type the wrong number.

The op vocabulary, in three tiers:

- **placement**: `place`, `row`, `grid`, `stack`, `ring`, `mirror`
- **course-work** (chooses bonded bricks): `wall`, `box`, `fill`
- **composition** (places nothing; transforms the ops inside): `repeat`,
  `reflect`, `define`, `call`

`build_ops` runs the full validation over the model *as it would be* before
writing anything. If the parts would overlap or land off-grid, **nothing is
written at all** and it names the clash. The file on disk is untouched, so
there is nothing to undo.

**`edit_model`** — line surgery against the line numbers the reports quote.
Moving one brick, recolouring a line, deleting a duplicate.

**`copy_from_set`** — grafts a real assembly out of one of 1,820 official sets,
re-anchored, rotated, recoloured, and credited with a comment. The corpus is the
record of how designers solved shapes with real parts; before this existed the
builder read those sets, admired them, and derived the shape from scratch anyway.

**`validate_model`** — both feedback channels in one call, deliberately with no
way to ask for only one:

| | the grid | the eyes |
|---|---|---|
| asks | is it **buildable**? | does it **look right**? |
| catches | off-grid parts, overlaps, invented part numbers | wrong shape, wrong proportions, floating parts |
| blind to | whether it resembles anything | whether the studs line up |

### 2.4 The checkers

`maister/environment_feedback/` — three independent checkers, none of which
involves a model:

- **`ldr_validator.py`** — part numbers resolve, syntax, structure.
- **`ldr_connectivity_checker.py`** — every part on a real stud; what is
  connected to what; how many separate clumps the model falls into over stud
  connections only (`subassemblies`, a build may not finish above 3).
- **`ldr_collision_checker.py`** — swept-volume overlap measured off the parts'
  real shapes, not bounding boxes. A stud in a tube, a bar in a clip, a dish
  nested in a dish all read as zero shared plastic; anything else is a fault
  with a `suggested_move` already on the grid.

Plus `lattice.py`, which answers the phase question in §1 and reports *the
cause* — "68 parts on the wrong lattice, move each z +10" — rather than 68
copies of the symptom.

And `style.py`, which is the only check about the model being **good** rather
than correct. It measures four things against the 1,812-model corpus, bucketed
by piece count, and **never fails a model** — a check that invents problems is
worse than no check, because the builder goes and "fixes" them.

### 2.5 The data

| store | size | what it is |
|---|---|---|
| `data/lego_pieces/` | 570 MB | the LDraw parts library |
| `data/ldraw_omr_sets/` | 363 MB, 1,820 models | official sets, as LDraw source |
| `data/parts/` + catalogue | 5,878 parts | measured geometry: footprint, stud grid, side studs, stacking height, connection families, `used_with` |
| `data/vector_db/` | 12 MB | four indexes: parts, sets, creations, notes |
| `data/agent_creations/` | 15 models | what this agent built and saved |
| `data/agent_knowledge/` | 29 notes | what it worked out while building |

Retrieval is hybrid — exact keyword fused with semantic vector search, then a
reranker (`Qwen3-Embedding-0.6B` / `Qwen3-Reranker-0.6B`, local). Critically it
has a **relevance floor**: a semantic search always answers, and handing over
the least-bad row is worse than handing over nothing, because the builder has
been told to start from what it is given.

### 2.6 The app

`app/` is a FastAPI backend (50 endpoints) and a React/three.js frontend: chat,
a live 3D viewer that re-renders on every write, the LDraw source view, a parts
gallery, project history, and a stop button that leaves a resume snapshot.

---

## 3. What has been learned

These are the findings the project has actually paid for. Each one changed the
code.

### 1. Decisiveness beats deliberation

**Finding:** the failure mode is not a wrong brick — it is spending the whole
run turning a right one over.

The prompt is now emphatic: the first workable answer is the answer; a tool's
answer is the answer; never repeat a call with the same arguments; never re-open
a finished subtask. Repeated lookups are replayed from the stored answer with a
nudge, so a reasoning loop costs a moment rather than a step.

**And write early.** Every subtask is written to disk as it finishes, not at the
end. Three things depend on it: the user is watching a live viewer, a crash
keeps whatever was written, and validation only ever describes the file — so an
unwritten model is an unchecked one.

### 2. Critique rounds are worthless — a met checklist ends the run

**Measured** across every trace on disk: the critic held a finished build 11
times in 7 runs. Of the four that took a second round, **0 improved, 3 came back
with the same issues, 1 got worse**. Two of the seven ended unbuildable.

The reason is visible in the repeats: *"the hull is a rectangular box, not a
boat"* is a true remark with no line-level fix. `CRITIQUE_ROUNDS` is 0. The
critique still reaches the user and still reaches the builder on every earlier
iteration, where it can be acted on. What it no longer does is reopen a build
the checklist has passed.

**The 2026-08-22 addendum:** that measured a critique used as a *repair list*.
The same signal sent to the **planner** — regenerate the brief and rebuild — is
a different mechanism, and the published APT ablation puts it at +12.8%. That
path now exists (`orchestrator._replan`), gated so it can only touch a build the
checklist already rejected. Untested live so far.

### 3. Requirements settle by three routes, and a parts list can never answer a positional claim

`requirements.check` tries, cheapest first: **the parts list** (exact counts and
colours, no model call at all), **the source** (the numbered file, for claims
about named sections), then **the pictures**.

The rule that matters: *"the model uses exactly 2 colours"* is arithmetic and is
settled for free. *"the tiles sit on top of the wall"* is **not answerable from
a parts list at any price** and must fall through to the renders — otherwise the
gate passes a heap of correctly-coloured bricks as a finished build.

### 4. Subtract the height of the piece going on, not the one underneath

Three prompts taught the inverse, and it caused overlaps. Every level is *the
previous level minus the height of the piece being placed*:

```
baseplate (plate, 8)   y = 0
course 1  (brick, 24)  y = 0   - 24 = -24
roof      (plate, 8)   y = -72 -  8 = -80
```

The mistake survives because the two numbers are identical for brick-on-brick.
It only shows up where the heights differ — baseplate to first course, last
course to roof — and those are in every build. Getting it wrong sinks the brick
12 LDU into the plate, and the model comes apart into pieces that each validate
as on-grid.

### 5. Part-specific rules are the domain, not a hack

Curated per-part tables — minifig grip geometry, connection families, the brick
ladders `wall` uses — were resisted as inelegant. They are not. LEGO *is* a
catalogue of specific parts with specific behaviours, and a rule that covers one
part correctly beats a generalisation that covers all of them approximately.
The `wall`/`box`/`fill` ladders are hardcoded rather than searched precisely so
that a wall's parts do not depend on how a search ranked that day.

### 6. Real sets must be pushed, not offered

Four tool calls stood between "build a car" and seeing how LEGO built one — and
a model that believes it knows what a car looks like spends those calls placing
bricks. So `refsets.py` finds them before the build starts and puts them **in
the task**, already opened, with real coordinates and the exact `copy_from_set`
call under each.

It also had to hand over **geometry, not a parts list**. A car is not four tyres
and a windscreen; it is a car base with the wheels recessed, a raked windscreen
on a hinge, and a bonnet of two wedge slopes meeting at 24 LDU. Planned from the
shopping list, every vehicle came out a box on wheels.

*This generalises, and that took until 2026-08-22 to act on: the creations
library and the notes were pull-only for the same reason and with the same
result. `recall.py` applies the identical push.*

### 7. Temperature does not buy diversity; conditioning does

The first idea a model returns is not a sampling accident — it is the *typical*
answer, because preference data was written by annotators who preferred familiar
text. Turning the temperature up samples the same sharpened peak more noisily.

What works is asking for a **distribution** rather than an answer:
`brief.compose` asks for five briefs with the probability each would have been
given if one were asked for, then takes one according to the request's licence
to invent. Reported at 1.6–2.1× the diversity of direct prompting, one call
either way.

**Still unmeasured:** `tools/brief_diversity.py` exists to put a number on this
for *this* project and the baseline has never been run.

### 8. Real sets use all three size classes; this agent picks one

Measured over 1,797 OMR models: **15% structural, 42% medium, 43% detail**, and
**98.6% use all three**.

This agent's own 84 models pool to a similar-looking 21/35/44 — and that hides
the whole fault. Its *per-model* distribution is bimodal: 10.7% of its models
are 90–100% structural and 52% are under 10%, against a corpus where 97% sit
between 0 and 30%. **It is not building the wrong mix, it is not mixing** — it
picks one size of part and builds the whole object out of it.

### 9. The builds are far duller than real sets, and prompt did not fix it

Measured 2026-08-22 over 137 models. Of the 93 with 12+ parts, medians against
real sets of the same size:

| | this agent | corpus | past the threshold |
|---|---|---|---|
| distinct shapes | **6** | 23 | — |
| rotated placements | **1%** | 70% | 85/93 below p10 |
| colours | **3** | 7 | 52/93 below p10 |
| commonest shape's share | **52%** | 12% | 83/93 above p90 |

Vocabulary: **197 shapes ever placed, of 5,878** (3.4%). `3003`, `6141`,
`54200`, `3001` are **55.5%** of every placement.

And `build_ops` was being used as a typewriter: of 1,938 stored ops, **81.6%
were `place`**; `wall` had never been called once.

**The part that matters:** splitting by date, models written after 15 August sat
at 84% `place` with *identical* style numbers to those before. The context is
30 K tokens, it already says all of this correctly, and it had been read past
twelve times. **More prompt was not the lever.**

### 10. Capability is the lever — but only for a call the builder was already making

The 2026-08-22 changes added composition ops (`repeat`/`reflect`/`define`/`call`)
and a volume op (`fill`), on the reasoning that MC-Bench-style systems produce
varied builds with *no validator at all* because their model writes a **program**
— loops, functions, free symmetry — rather than a list.

One live run, cut off by timeout at 16 `build_ops` calls, split the result:

- **`fill` was reached for unprompted** and returned 18 plates in two shapes,
  bonded, for a 9×9 deck a `grid` would have laid as one shape repeated.
- **`repeat` and `reflect` got zero uptake** — and the same run wrote "front
  lower / front upper / back lower / back upper" as four separate `row` ops,
  four separate times. That is one `repeat` and one `reflect`.
- `place` share fell 81.6% → 32%, but most of that is `row` and `box`, which
  already existed.

**The distinction:** `fill` *replaced* a call the builder was already making, and
was adopted immediately. `repeat`/`reflect` ask it to **restructure what it
writes** — and that failed the same way the prompt failed in finding 9. Adding a
capability is not enough if using it requires the model to change its shape of
thought.

*Next move if uptake stays at zero: make `plan_construction` emit the groups so
the builder runs them rather than having to compose them.*

### 11. Operational

- **Do not edit anything under `maister/` while a run is in flight.** The dev
  backend hot-reloads and kills builds mid-way. (The CLI does not — it imports
  once at start.)
- **Keep API testing cheap.** `python3 -m maister.agent.run_agent --self-test`
  exercises the whole toolchain offline with no token, including an
  undefined-name sweep across all 65 modules. One tiny probe after it is enough;
  full end-to-end runs are for verifying behaviour, not for catching typos.
- **LeoCAD headless has no ground plane.** Renders come back transparent and
  gridless; the grid the user sees is the three.js one in the viewer.
- **`validate_model` calibration was 27.5% → 55.6%** on the reference sets.
  Re-measure after any checker change — a checker that gets stricter looks
  identical to a builder that got worse.

---

## 4. Open questions

- **Brief diversity has never been baselined** (finding 7). The tool exists.
- **The re-plan path is untested live** (finding 2 addendum).
- **The style report is still advisory**, and with a met checklist ending the
  run it is read at the exact moment the run is about to end. The unbuilt
  proposal is one hard floor on rotation — 1% against a corpus median of 70% is
  not a matter of taste.
- **Parts are not resolved by role at plan time.** The blueprint already emits
  `parts[].role` ("the wheel arches, curved over the tyre"); nothing looks those
  up and hands the candidates over. Same shape of fix as findings 6 and 10.

---

## 5. Running it

```bash
python3 -m maister.agent.run_agent --self-test          # offline, no token
python3 -m maister.agent.run_agent "a red pickup truck" --out my_build
python3 -m maister.agent.run_agent --validate path.ldr
app/start.sh                                            # the web app
```

Needs a HuggingFace token (`huggingface-cli login`) and, for renders, LeoCAD —
see `simulator/README.md`.
