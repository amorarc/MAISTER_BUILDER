"""The harness around the builder: one petition in, one finished scene out.

A request is not a build. "A house with a tree and a car" is three builds and
an arrangement, and the difference between treating it as three and treating it
as one is the difference between three models that work and one pile that
does not. So a run has four beats:

1. **Split.** The petition becomes atomic subconstructions — one per
   free-standing object (see ``decompose``). A request for one object splits
   into one, and the rest of this is a straight line.
2. **Build each one, alone.** Every subconstruction gets its own agent with its
   own fresh conversation, its own file, and its own gate: written, validated,
   rendered, looked at. A builder working on the tree is never also holding the
   house in its head, which is what kept going wrong.
3. **Assemble.** The finished subbuilds are composed into one MPD by
   ``assembly``, placed from measured bounding boxes rather than guesses.
4. **Look at the whole thing.** The scene is validated and rendered like
   anything else, and what the vision model says about it is what the user is
   told.

Every beat reports as it happens. A run that dies in beat 2 still leaves two
finished subbuilds and a picture of each, because the alternative — nothing at
all until everything works — is the failure mode this whole design is against.
"""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import decompose as decomposer
from . import (assembly, brief, palette, planner, recall, reference, refsets,
               render, requirements, resume, runstate, survey, trace)
from .agent import LDrawAgent
from .agent import _issue_text as _issue_line
from .config import (BRIEF_ENABLED, DEFAULT_MAX_STEPS, OUT_DIR,
                     REQUIREMENTS_ENABLED)
from .llm import LLM
from .prompts import build_task_context, buildable_reference
from .runstate import RunState
from .tools import ASSEMBLY_EDIT_BUDGET, agent_tools, call_tool
from .validation import validate

# No step budget, for the subbuilds or for the assembly pass. See
# DEFAULT_MAX_STEPS in config.py: 0 is a loop that runs until the agent calls
# `finish`, gives up, errors, or is stopped.
#
# These were 24 and 10, on the reasoning that a builder that cannot place one
# object in twenty-four turns will not place it in fifty. That was wrong in the
# case that matters: a build that searches sets, grafts from two of them,
# validates, is looked at and then repairs what the critic saw spends its
# twenty-four turns doing exactly what it was asked to, and the budget cut it
# off holding an unfinished model with the finish gate never run. Reaching the
# limit was the single most common way a run ended badly.
#
# What replaces it is the stop button, which is immediate and now leaves a
# resume snapshot behind.
SUBBUILD_STEPS = DEFAULT_MAX_STEPS
ASSEMBLY_STEPS = DEFAULT_MAX_STEPS

# Tools a subbuild has no business calling. It builds one object into one file,
# and assembling the scene out of those files is the harness's job. (Saving to
# the gallery used to be listed here too; there is no tool for it any more —
# the user presses a button.)
SUBBUILD_EXCLUDED = frozenset(("assemble_model", "move_submodel",
                               "rotate_submodel"))
# The assembly pass arranges finished objects. Two ways to move a whole one,
# and — since the objects do not always come apart cleanly at the joins —
# `edit_model` for the work that moving cannot do: taking out the two bricks
# where one object passes through another, adding the plate that ties them
# together, dropping a duplicate placement.
#
# It was withheld before, because handing it over invited the pass to go into
# a finished tree and rearrange bricks — which is how a correct subconstruction
# gets taken apart to fix a spacing problem. That risk is real and it is now
# answered where it can actually be enforced rather than in the prompt: see
# `_assembly_guard` in tools.py, which counts the part lines an edit moves and
# refuses anything the size of a redesign. The tool is here; the licence to
# rebuild a component is not.
#
# `assemble_model` is deliberately not in this list, though it used to be. The
# harness has already called it by the time this agent runs — that is what
# produced the scene it is repairing — so the only thing calling it again could
# do is compose the components afresh and throw the repair away. Across 41 runs
# it was never called once, which is the agent agreeing: it was a tool that
# could only do harm, occupying a slot in a ten-step budget.
ASSEMBLY_ONLY = frozenset((
    "move_submodel", "rotate_submodel", "edit_model",
    "read_model", "validate_model", "finish",
))


# How many subconstructions are built at the same time. Each is an independent
# object in its own file, so a scene of five spends most of its wall clock
# waiting on five conversations that have nothing to say to each other.
#
# Not unbounded, for two reasons that are not about this code: every builder is
# a stream of requests to one endpoint, and a dozen at once is how a rate limit
# is found; and each holds a rendered contact sheet in memory while the vision
# model looks at it.
#
# Six rather than three, because the thing being built changed shape. Three was
# chosen when a scene was at most six objects and each was a house or a car; a
# word is now up to twelve objects and every one of them is a letter four
# bricks wide. At three, "MAISTER in big letters" is three waves of the slowest
# letter, and the wall clock is triple what the work is.
PARALLEL_SUBBUILDS = max(1, int(os.environ.get("LDRAW_PARALLEL_SUBBUILDS", "6")))


def _tools(exclude=None, only=None):
    # From what an agent may currently be shown, not from the full catalogue:
    # `only` is a whitelist, and a whitelist applied to TOOL_SCHEMAS would hand
    # the assembly pass back a tool the settings had switched off.
    schemas = agent_tools()
    names = {t["function"]["name"] for t in schemas}
    keep = (only or names) - (exclude or set())
    return [t for t in schemas if t["function"]["name"] in keep]


class Orchestrator:
    """Runs one petition from end to end."""

    def __init__(self, llm=None, model=None, on_event=None, verbose=True,
                 max_steps=DEFAULT_MAX_STEPS, client=None):
        self.model = model
        self.client = client
        self.llm = llm
        self.on_event = on_event
        self.verbose = verbose
        self.max_steps = max_steps
        self.should_stop = None
        # The subconstructions of the run in progress, so a watcher can show
        # the checklist while it is being worked through.
        self.subconstructions = []
        # The conversation so far, handed to every sub-agent (they have none).
        self.history = None
        # The reference pictures in force for this project, if the user
        # attached any. They are the specification, so they reach every
        # subbuild. `reference` is the first of them and carries the
        # description and the answered questions for all of them — see
        # reference.py, where they are written to every record.
        self.references = []
        self.reference = None
        # The petition, the design brief per object, and the snapshot of a
        # previous stopped run — everything a stop needs to write down so the
        # next run can carry on rather than start again. See resume.py.
        self.petition = None
        self.briefs = {}
        self.resumed = None
        self.already_built = set()
        # Objects whose look has already been re-decided once after coming out
        # unrecognisable. One each, and no more — see `_replan`.
        self._replanned = set()
        # What was already on the workbench when this run started — read,
        # measured, checked and looked at before anything is split or built.
        # See survey.py.
        self.workbench = None

    # -- reporting ---------------------------------------------------------

    def _log(self, *a):
        if self.verbose:
            print(*a, flush=True)

    def _emit(self, type_, **fields):
        if self.on_event is None:
            return
        try:
            self.on_event({"type": type_, **fields})
        except Exception:
            pass

    def _forward(self, event, **extra):
        """Re-emit a sub-agent's event as one of this run's own.

        Takes the event as a dict rather than being spread over ``_emit``'s
        signature: a sub-agent's event already carries its own ``type`` key,
        and ``_emit(**event)`` would be a TypeError — swallowed by the caller's
        own try/except, so every event from every subbuild would vanish with no
        sign of why.
        """
        if self.on_event is None:
            return
        try:
            self.on_event({**event, **extra})
        except Exception:
            pass

    def _stopped(self):
        return bool(self.should_stop and self.should_stop())

    def _new_llm(self, task="build"):
        """A fresh LLM for one sub-agent.

        Fresh rather than shared: an LLM carries flags it negotiated with the
        endpoint, and two agents running against one instance would trade them.
        The HTTP client underneath is reused, which is the expensive part.
        """
        if self.llm is not None:
            return LLM(client=self.llm.client, model=self.llm.model, task=task)
        from .llm import make_client

        self.client = self.client or make_client()
        return LLM(client=self.client, model=self.model, task=task)

    # -- the run -----------------------------------------------------------

    def run(self, petition, project_dir, current_model=None, project=None,
            history=None):
        """Build ``petition`` into ``project_dir``. Returns a result dict.

        ``project_dir`` is relative to out/, the same way every path the tools
        take is. The finished scene lands at ``<project_dir>/model.ldr``; each
        subconstruction keeps its own file in ``<project_dir>/parts/``.

        ``history`` is the conversation so far, as text. Sub-agents are built
        fresh for each subconstruction and have no memory of their own, so
        continuity across turns has to be handed to them — this is where it
        comes in.
        """
        project = project or Path(project_dir).name
        main_path = f"{project_dir}/model.ldr"
        self.history = history
        self.petition = petition

        # Where a stopped run got to, if the last one in this project was
        # stopped and was working on this same job. Read before anything else
        # so the split below can be taken from it rather than made again.
        snapshot = resume.load(project)
        if snapshot and not resume.matches(snapshot, petition):
            # A different request supersedes the half-built one. Keeping it
            # would mean silently skipping work the user is now asking for.
            resume.clear(project)
            snapshot = None
        if snapshot:
            self.history = self.history or snapshot.get("history")
            if resume.is_continuation(petition) and snapshot.get("petition"):
                petition = snapshot["petition"]
                self.petition = petition
            self._log(f"--- resuming: {len(resume.finished(snapshot))} "
                      f"object(s) already built")
        self.resumed = snapshot

        # Every picture attached, not the last one. They are read together and
        # the description is written to all of them, so any single record
        # answers "what is this project's reference" — which is why the rest of
        # this file can go on holding one.
        self.references = reference.active(project)
        self.reference = self.references[0] if self.references else None
        if self.references:
            self._log(f"--- {len(self.references)} reference image(s): "
                      f"{', '.join(r.get('file') or '?' for r in self.references)}"
                      f"{' (described)' if reference.described(self.references) else ''}")
            self._emit("reference", image=reference.summarize(self.reference),
                       images=_kept(project, reference_image=reference.paths(
                           self.references, project)))

        # 0 -- read what is already there ---------------------------------
        #
        # Before the request is split, before a brief is written, before a
        # single part is placed: look at the file. It may be empty, it may hold
        # last turn's build, it may hold an official set the user opened as a
        # starting point — and each of those is a different job. Deciding any
        # of it from the request alone is deciding it blind.
        self.workbench = self._survey(main_path, project)

        # 1 -- split ------------------------------------------------------
        #
        # Read the picture *before* splitting, not when a builder gets round to
        # asking. How many things are being built is the first decision the run
        # makes, and making it from the request alone means making it blind:
        # "build this" splits into one object however many are in the picture,
        # and the objects the user can plainly see never get built.
        self._describe_reference(project, petition)

        self._emit("decomposing")
        self._log(f"\n=== PETITION ===\n{petition}\n")

        subs, meta = decomposer.decompose(
            petition, current_model=current_model,
            reference=(self.reference or {}).get("description"),
            workbench=survey.as_text(self.workbench),
            llm=self._new_llm(task="plan"), should_stop=self.should_stop)

        # Objects the stopped run already finished keep their files and are not
        # built again. Matched by name against the split, which is stable for
        # the same petition — and where it is not, the worst case is that an
        # object is built a second time, which is what happened every time
        # before any of this existed.
        self.already_built = (resume.finished(self.resumed)
                              if self.resumed else set())
        # An object built *in place* wrote to the project's own model file, and
        # that file always exists — so it would look finished on every resume
        # and the change would never be made. Only objects with a file of their
        # own can be recognised as already done.
        if self.already_built and self.resumed:
            self.already_built = {
                e.get("name") for e in self.resumed["subconstructions"]
                if e.get("name") in self.already_built
                and e.get("path") and e["path"] != main_path}
        if self.already_built:
            kept = [s for s in subs if s.name in self.already_built]
            for sub in kept:
                sub.status = "done"
                sub.path = next(
                    (e.get("path") for e in self.resumed["subconstructions"]
                     if e.get("name") == sub.name), sub.path)
                sub.note = "built before the run was stopped; kept as it was"
            self._log(f"--- keeping {len(kept)} object(s) from the stopped "
                      f"run: {', '.join(s.name for s in kept)}")

        self.subconstructions = subs

        self._emit("decomposed", summary=meta.get("summary"),
                   scene=meta.get("scene"), source=meta.get("source"),
                   subconstructions=[s.as_dict() for s in subs])
        self._log(f"--- {len(subs)} subconstruction(s): "
                  f"{', '.join(s.name for s in subs)}"
                  + (f"  ({meta['note']})" if meta.get("note") else ""))

        if self._stopped():
            return self._result(petition, meta, main_path,
                                stopped=True, answer="")

        # Is this a change to the model that is already there, rather than a
        # new build? "Add a chimney" must come back as the house it already had
        # with a chimney on its roof — one model. Building the chimney into a
        # file of its own and standing it next to the house is the single most
        # wrong thing this harness could do with that request.
        modifying = planner.is_modification(petition, current_model)

        # A single object is not a scene either: it is built straight into the
        # project's own model file, and there is nothing to assemble.
        single = len(subs) == 1 and subs[0].quantity == 1

        # Everything into the one file, in order, each build seeing what the
        # last one left behind.
        in_place = modifying or single
        if modifying:
            self._log("--- editing the existing model in place "
                      f"({len(subs)} change(s)); nothing will be assembled")
            self._emit("editing", changes=[s.name for s in subs])

        # 2 -- build them --------------------------------------------------
        if in_place:
            self._build_in_turn(subs, main_path, petition, project, modifying)
        else:
            self._build_together(subs, project_dir, petition, project)

        built = [s for s in subs if s.status == "done"]
        if self._stopped():
            return self._result(petition, meta, main_path, stopped=True,
                                answer=self._summary(subs, meta))

        # 3 -- assemble ----------------------------------------------------
        # Nothing to assemble when everything went into the one file: the
        # model is already whole, and running the assembler over it would take
        # the build apart in order to put it back.
        if not in_place:
            if not built:
                return self._result(
                    petition, meta, main_path,
                    answer=("Nothing could be built. " + self._summary(subs, meta)))
            self._assemble(built, main_path, meta, project)

        # 4 -- look at the whole thing ------------------------------------
        return self._result(petition, meta, main_path,
                            answer=self._summary(subs, meta))

    def _survey(self, main_path, project):
        """Read the model file as it stands, before this run changes anything.

        Costs nothing on an empty workbench — the common case, and the one that
        short-circuits before any renderer is started. On a file with parts in
        it, it costs six renders and one vision call, and buys the run the one
        fact nothing else in the pipeline had: *what the thing already is*.

        Best effort, like every other reading pass here. A survey that cannot
        be taken leaves the run exactly where it was before this existed.
        """
        try:
            surveyed = survey.survey(
                main_path, project=project,
                # A stop pressed before the build starts should not spend a
                # vision call on a picture nobody will read.
                look=not self._stopped(), should_stop=self.should_stop)
        except Exception as exc:
            self._log(f"--- the workbench could not be read: {exc}")
            return None

        self._log(f"--- workbench: {survey.headline(surveyed)}")
        self._emit("workbench", empty=bool(surveyed.get("empty")),
                   summary=survey.headline(surveyed),
                   survey={k: v for k, v in surveyed.items()
                           if not k.startswith("_")},
                   images=_kept(project, surveyed.pop("_images", ()),
                                surveyed.pop("_sheet", None)))
        return surveyed

    def _describe_reference(self, project, petition=None):
        """Have the pictures read, if there are any and nobody has read them.

        Costs one vision call at the start of a run and saves the same call
        being made by the first builder to need it — the description is stored
        against the images, so every subbuild after this one gets it for free.
        Best effort: a picture that cannot be described must not stop the build,
        it only leaves the run working from the request alone, as it used to.

        One call however many pictures there are. Describing them one at a time
        would come back with one subject each, and four descriptions of four
        photographs of one car are four cars to everything downstream — the
        decomposer would split the run into four builds and stand them in a row.
        """
        if not self.references or reference.described(self.references):
            return
        if self._stopped():
            return
        pictures = reference.paths(self.references, project)
        if not pictures:
            return
        try:
            described = render.describe(pictures, request=petition)
        except Exception as exc:
            self._log(f"--- the reference could not be described: {exc}")
            return

        reference.set_description(
            project, [r.get("image_id") for r in self.references], described)
        self.references = [dict(r, description=described)
                           for r in self.references]
        self.reference = self.references[0]
        seen = described.get("objects") or []
        self._log(f"--- reference read: {described.get('subject')}"
                  + (f", {len(seen)} object(s)" if seen else ""))
        self._emit("reference", image=reference.summarize(self.reference),
                   images=_kept(project, reference_image=pictures))

    def _reference_spec(self):
        """The reference as the prompt wants it: one record, plus how many.

        The description and the answered questions are the same on every
        record, so the first of them is the whole specification. ``count`` is
        the one thing it cannot carry by itself, and the builder needs it —
        "the picture" and "the four pictures" ask for different reading.
        """
        if not self.reference:
            return None
        return dict(self.reference, count=len(self.references))

    # -- running the builds -------------------------------------------------

    def _build_in_turn(self, subs, main_path, petition, project, modifying):
        """One after another, all into the same file.

        This is the case that cannot be shared out, and the reason the split
        exists. When the request is an edit, or is a single object, every
        subconstruction writes to the *project's own model file* and each one
        is handed that file as the one before it left it — that is what makes
        "add a chimney and paint the door" two changes to one house rather than
        two houses. Run those at the same time and both builders read the same
        starting model, both write the whole file back, and the one that
        finishes second silently erases the other's work.
        """
        for index, sub in enumerate(subs, start=1):
            if self._stopped():
                break
            sub.path = main_path
            # As it stands *now*, not as it stood when the run began.
            existing = self._read(main_path)
            self._build_one(sub, index, len(subs), petition, project,
                            existing, modifying=modifying, whole_build=True)

    def _build_together(self, subs, project_dir, petition, project):
        """All at once, each into a file of its own.

        A scene's subconstructions share nothing: separate agents, separate
        conversations, separate files, and the arrangement between them is not
        decided until they are all finished. So the only thing serialising them
        was the loop, and a scene of four objects spent four times as long as
        it needed waiting on four conversations that never had to agree.
        """
        for sub in subs:
            # An object carried over from a stopped run keeps the file it was
            # built into; reassigning it would point the assembler at a path
            # nothing has written.
            if sub.name not in getattr(self, "already_built", ()):
                sub.path = f"{project_dir}/parts/{sub.name}.ldr"

        total = len(subs)
        workers = min(PARALLEL_SUBBUILDS, total)
        if workers < 2:
            for index, sub in enumerate(subs, start=1):
                if self._stopped():
                    break
                self._build_one(sub, index, total, petition, project, None,
                                whole_build=total == 1)
            return

        self._log(f"--- building {total} subconstruction(s), {workers} at a time")

        def build(index, sub):
            # Checked in the worker as well as before submitting: a stop
            # pressed while the pool is draining should leave the builds that
            # have not started alone rather than run them anyway.
            if self._stopped():
                return
            try:
                self._build_one(sub, index, total, petition, project, None,
                                whole_build=total == 1)
            except Exception as exc:
                # _build_one already handles a builder that raises. This is the
                # backstop for anything else — one subconstruction going down
                # must not take the other three with it, because three objects
                # and a gap is a result and an exception is not.
                sub.status = "failed"
                sub.note = f"{type(exc).__name__}: {exc}"
                self._log(f"--- {sub.name} FAILED outright: {sub.note}")
                self._emit("subbuild_end", name=sub.name, ok=False,
                           note=sub.note)

        with ThreadPoolExecutor(max_workers=workers,
                                thread_name_prefix="subbuild") as pool:
            # Exceptions are handled inside `build`, so the futures are only
            # waited on — which the context manager does on the way out.
            for index, sub in enumerate(subs, start=1):
                pool.submit(build, index, sub)

    # -- one subconstruction ----------------------------------------------

    def _faults(self, path):
        """What is already wrong with the model at ``path``, or None if nothing.

        The report a previous run was given died with that run's conversation,
        so this re-derives it from the file instead of carrying it. Local, no
        API call, and it describes the model as it is now rather than as it was
        when somebody last looked at it. A clean model, a missing file or a
        checker that will not run all come back as None — the brief says
        nothing rather than something reassuring it has not verified.
        """
        # A subconstruction is given its file when the harness decides where it
        # goes, which is after the brief can first be built — so "no path yet"
        # is a normal state here and not a mistake to raise on.
        if not path:
            return None
        target = Path(_abs(path))
        if not target.is_file():
            return None
        try:
            report = validate(target)
        except Exception:
            return None
        if report.get("error") or report.get("passed"):
            return None
        return report

    def _read(self, path):
        """The current contents of a model file under out/, or None."""
        target = Path(_abs(path))
        if not target.is_file():
            return None
        text = target.read_text(encoding="utf-8", errors="replace")
        return text if planner.has_parts(text) else None

    def _object_scope(self, path):
        """What the file this builder writes to holds: one object, or several.

        The builder is always given exactly one object to make. The question is
        only whether the *file* holds only that — because when it does, "every
        part in here is joined to the rest" is a fact worth failing a build
        over, and when it does not, joining everything would be the fault.

        Decided by reading the file rather than from what kind of run this is,
        because the two come apart. A scene's subbuilds each own an empty
        `parts/<name>.ldr`, and an edit inherits whatever was on the workbench —
        but so does a *new* object built straight into a project that already
        holds an assembled scene, and keying this on "is it an edit" would then
        demand the new house be joined to the tree and the car.

        So: a file with more than one block in it is a scene, and a file without
        is one object. That second half is the case that matters, and it is the
        one the orchestrator has always cared about — "add a chimney" must come
        back as one house with a chimney on it, not a house standing beside a
        chimney.

        The inference is only ever made in the direction that cannot do harm.
        "blocks" reports and does not fail (see validation._disconnected), so an
        opened official set — whose blocks are instruction steps rather than
        objects — costs a noisy line in the report and never a rejected build.
        """
        try:
            text = Path(_abs(path)).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return "whole"      # nothing there yet: this builder will fill it
        # A `0 FILE x.dat` block is an embedded part definition, not an object.
        blocks = [name for name, _ in assembly.read_blocks(text, "main")
                  if not name.lower().endswith(".dat")]
        return "blocks" if len(blocks) > 1 else "whole"

    def _build_one(self, sub, index, total, petition, project, current_model,
                   modifying=False, whole_build=True):
        """One agent, one object, its own conversation."""
        # Already finished before the last run was stopped. Its file is on disk
        # and it goes into the scene as it is — rebuilding it would spend the
        # whole point of having stopped.
        if sub.name in getattr(self, "already_built", ()):
            self._log(f"--- [{index}/{total}] {sub.name}: kept from the "
                      f"stopped run ({sub.path})")
            self._emit("subbuild_end", name=sub.name, ok=True, note=sub.note,
                       path=sub.path, passed=True, resumed=True)
            return

        sub.status = "building"
        self._emit("subbuild_start", name=sub.name, subject=sub.subject,
                   index=index, total=total)
        self._log(f"\n--- [{index}/{total}] {sub.name}: {sub.subject}")

        # What the critic is told to judge the renders against. For an edit
        # that is the *whole model* after the change, not the change on its
        # own: asking "does this look like a chimney" of a house with a chimney
        # on it invites the answer "no", and the fix for that answer is to
        # throw the house away.
        subject = sub.subject
        if modifying:
            subject = (f"the complete model, which already existed and has "
                       f"just had this change made to it: {sub.subject}. It "
                       f"must be one connected build with the change attached "
                       f"to it, not two things side by side")

        design_brief = self._brief(sub, project, modifying)
        # What has to be true before this build may end, written before a brick
        # is placed and stored beside the model. Its own step because it is its
        # own decision: the brief says what this should look like, and this says
        # what would make it finished. See requirements.py.
        checklist = self._requirements(sub, project, design_brief)
        real_sets = self._reference_sets(sub, modifying)
        # And this agent's own earlier answers to the same question. See
        # recall.py: the same push that the sets get, applied to the two stores
        # that only ever got pulled.
        mine, remembered = self._recall(sub, modifying)

        result, state, report = self._attempt(
            sub, index, total, petition, project, current_model, subject,
            modifying, whole_build, design_brief, checklist, real_sets,
            mine, remembered)
        if result is None:
            return

        # A build that came back reading as the wrong thing goes back to the
        # design pass, not to the builder. See `_replan`.
        again = self._replan(sub, state, report, design_brief, modifying,
                             project)
        if again is not None:
            design_brief = again
            result, state, report = self._keep_better(
                (result, state, report),
                sub, index, total, petition, project, current_model, subject,
                modifying, whole_build, design_brief, checklist, real_sets,
                mine, remembered)

        self._settle(sub, result, state, report)

    def _attempt(self, sub, index, total, petition, project, current_model,
                 subject, modifying, whole_build, design_brief, checklist,
                 real_sets, mine, remembered):
        """One builder, one conversation, one go at this object.

        Returns ``(result, state, report)``, or ``(None, None, None)`` when the
        run died in a way that leaves nothing to report — the caller returns
        without emitting, exactly as it did when this was inline.
        """
        state = RunState(subject=subject, requirements=sub.requirements,
                         project=project, target=sub.path,
                         require_render=render.available(),
                         objects=self._object_scope(sub.path))
        # Carried on the ledger so `plan_construction` is written against it
        # whether or not the builder thinks to mention it.
        state.brief = design_brief
        # ...and the checklist, which the agent loop puts to the model at the
        # end of every iteration and which is now what ends the run.
        state.requirements = checklist
        # The same, for the sets found for this object. The plan is the one
        # call that decides what gets built, and it used to be shown a parts
        # list — "a car uses four tyres and two grille tiles" — which is a
        # shopping list rather than a construction, and planned from it every
        # vehicle came out a box on wheels. Now it sees the geometry the
        # builder sees, and names the assembly to start from.
        state.reference_sets = real_sets
        # The same again for what this agent already built. On the ledger so
        # `plan_construction` is written against it too: a plan that has not
        # seen the trunk this agent already got right plans another one.
        state.recalled = recall.as_text(mine, remembered, subject=sub.subject)
        # And what is already on the bench, for the same reason: a plan for a
        # change is only as good as its reading of what it is changing.
        state.workbench = survey.as_text(self.workbench)
        # A picture the user attached is the specification for every part of
        # the build, so each subbuild is held to it too.
        state.has_reference = bool(self.reference)
        state.reference_description = (self.reference or {}).get("description")
        # One object of a scene is not the scene. See RunState.
        state.reference_is_this_build = whole_build

        agent = LDrawAgent(llm=self._new_llm(task="build"),
                           max_steps=SUBBUILD_STEPS, verbose=self.verbose,
                           state=state)
        agent.tools = _tools(exclude=SUBBUILD_EXCLUDED)
        agent.should_stop = self.should_stop
        agent.on_event = lambda event: self._forward(
            event, subconstruction=sub.name)

        try:
            result = agent.run(self._subbuild_task(sub, index, total, petition,
                                                   current_model, modifying,
                                                   design_brief, real_sets,
                                                   checklist, state.recalled))
        except Exception as exc:
            # The builder died mid-run — the connection dropped, the provider
            # gave up, something raised. What it had already written is still on
            # disk, and it is the only thing anyone has to show for the time
            # that was spent: a run that reported "not built" while leaving
            # eight parts on the workbench was telling the user something the
            # file contradicts.
            result = self._salvage(sub, exc, state)
            if result is None:
                return None, None, None

        report = state.validation_of(sub.path) or {}
        sub.validation = report
        critique = state.critiques.get(sub.path)
        sub.critique = critique["critique"] if critique else None
        return result, state, report

    def _settle(self, sub, result, state, report):
        """What this object came out as, from what the builder left behind."""
        # A build the studs read as a heap of clumps is not finished, and
        # `give_up` is the one door left open to it: the gate refuses this on
        # the way to `finish`, but a run that walks away instead used to be
        # marked done on the strength of a passing report. Same ceiling, same
        # answer, whichever door it left by. See runstate.MAX_SUBASSEMBLIES.
        in_pieces = runstate.too_many_pieces(report, state.objects)

        if result.get("gave_up") and report.get("passed") and not in_pieces:
            # It stopped short of `finish`, but the object it was given is on
            # the grid and has been looked at. Whatever it could not settle was
            # not this object, and marking the node failed said the opposite:
            # the reason is kept, the verdict is not.
            sub.status = "done"
            sub.note = (result.get("answer")
                        or result.get("warning") or "").strip()
        elif result.get("gave_up"):
            sub.status = "failed"
            sub.note = (self._pieces_note(report) if in_pieces
                        else result.get("warning") or "the builder gave up")
        elif in_pieces and Path(_abs(sub.path)).is_file():
            # Passing the fault checks and still a heap of clumps. Kept and
            # shown, like any other build that came out wrong, and not called
            # finished — the only ways to reach this are a run that died and was
            # salvaged or one that was interrupted, and neither is the gate
            # having agreed.
            sub.status = "done"
            sub.unbuildable = True
            sub.note = self._pieces_note(report)
        elif report.get("passed"):
            sub.status = "done"
            sub.note = result.get("answer") or ""
        elif Path(_abs(sub.path)).is_file():
            # It exists but does not validate. Kept anyway: a subbuild that is
            # wrong is still something to look at and still assembles into a
            # scene, and the user is told which one it was.
            #
            # Kept, but never called finished. A run that ends on the step limit
            # skips `finish` entirely, so the gate that would have refused this
            # never ran — and "done" on a model with parts off the grid is the
            # harness telling the user something the checker just denied.
            sub.status = "done"
            off_grid = len((report.get("connectivity") or {}).get(
                "misaligned_parts") or [])
            sub.unbuildable = bool(off_grid) or not report.get("passed")
            sub.note = (f"kept, but it does not validate: "
                        f"{report.get('verdict') or 'unchecked'}")
            if off_grid:
                sub.note = (f"kept, but {off_grid} part(s) are off the stud "
                            f"grid, so it cannot be built out of real bricks: "
                            f"{report.get('verdict') or ''}").strip()
        else:
            sub.status = "failed"
            sub.note = "no model file was written"

        # Why it stopped, in front of what state it stopped in. A model that is
        # unfinished because the connection dropped is a different thing from
        # one the builder finished badly, and only one of them is worth running
        # again unchanged.
        if result.get("interrupted"):
            sub.unbuildable = sub.unbuildable or not report.get("passed")
            sub.note = f"{result['warning']}. {sub.note}".strip()

        self._emit("subbuild_end", name=sub.name, ok=sub.status == "done",
                   note=sub.note, path=sub.path,
                   passed=bool(report.get("passed")),
                   critique=sub.critique)
        self._log(f"--- {sub.name}: {sub.status} ({sub.note})")

    # -- when the model came out as the wrong thing -------------------------

    def _replan(self, sub, state, report, design_brief, modifying, project):
        """A new design brief, when the last build read as the wrong thing.

        `LDRAW_CRITIQUE_ROUNDS` is 0 and the measurement behind it stands: over
        every trace on disk, handing a critique back to the builder that wrote
        the model bought nothing — 0 of 4 improved, 3 repeated themselves, 1 got
        worse. What that measured, though, is a critique used as a **repair
        list**, and the critiques that kept coming back were of the one kind no
        repair list can carry: *"the hull is a rectangular box, not a boat"*
        names no line, has no edit, and is not a fault in the assembly at all.
        It is a fault in what was aimed at.

        So it goes to the pass that aims. The renders and what was seen in them
        go back to `brief.py`, which chooses a different silhouette, and the
        object is built again from that — rather than the same model being
        pushed at until the same remark comes back a third time.

        Gated hard, and every gate matters:

        * **Only when the checklist did not pass.** A met checklist ends the
          run and nothing here reopens it. That is the finding above, kept
          exactly: this can only touch a build that had already come out badly.
        * **Only on `recognisable: false`.** Not on `character.generic`, which
          is advice on a build that is *right*, and not on a located issue,
          which the builder can and should fix itself.
        * **Once.** A second re-plan is the same loop with more steps in it.
        * **Never on an edit.** "Paint the door blue" is not an invitation to
          redecide what the house looks like.

        Returns the new brief, or None to leave the first attempt alone.
        """
        if modifying or self._stopped() or not BRIEF_ENABLED:
            return None
        if sub.name in self._replanned:
            return None
        # The one thing that must never be reopened: a build the checklist
        # accepted. See above.
        if state.requirements_met(sub.path):
            return None

        critique = sub.critique or {}
        if critique.get("recognisable") is not False:
            return None
        # Nothing was built, so there is no silhouette to have got wrong —
        # whatever went wrong was not the brief.
        if not (report.get("parts") or sub.path in state.writes):
            return None
        # And nothing may be re-planned whose file held something before this
        # run touched it. The second attempt starts from an empty file — that
        # is what makes it an attempt rather than a second model built on top
        # of the first — so a re-plan over a workbench that already had a model
        # on it would clear the user's work. A scene's subbuild owns a fresh
        # `parts/` file and is never in that position; a build straight into
        # the project's own model file can be, and `modifying` does not always
        # catch it (a request with no edit verb, made against a full bench,
        # reads as a new build).
        if not self._is_own_file(sub.path):
            self._log(f"--- {sub.name} came out wrong, but its file held work "
                      f"from before this run; not re-planning over it")
            return None

        seen = critique.get("reads_as") or "something else"
        self._log(f"--- {sub.name} came back reading as \"{seen}\", not "
                  f"{sub.subject} — re-planning the look rather than patching "
                  f"the model")
        self._emit("replanning", name=sub.name, reads_as=seen,
                   was=(design_brief or {}).get("reads_as"))
        self._replanned.add(sub.name)

        wording = sub.requirements
        if len(self.subconstructions) == 1 and self.petition:
            wording = f"{wording} {self.petition}"
        allowed = brief.licence(sub.subject, wording, self.reference)
        try:
            document = brief.compose(
                sub.subject,
                requirements=sub.requirements,
                reference=buildable_reference(
                    (self.reference or {}).get("description")),
                angle=brief.variation(f"{project}:replan", allowed),
                stance=brief.persona(f"{project}:replan", allowed),
                allowed=allowed,
                # A different seed, so a different candidate is taken out of
                # the five. Seeded on the same run rather than at random, so a
                # resumed run reaches the same second brief as the first.
                seed=f"{project}:{sub.name}:replan",
                size_hint=sub.size_hint,
                failed=self._what_was_seen(critique, design_brief),
                should_stop=self.should_stop)
        except Exception as exc:
            self._log(f"--- could not re-plan {sub.name}: {exc}")
            return None

        if not document:
            return None
        self.briefs[sub.name] = document
        self._log(f"--- {sub.name} re-planned as: "
                  f"{document.get('reads_as') or 'written'}")
        self._emit("design_brief", name=sub.name, brief=document, replanned=True)
        return document

    @staticmethod
    def _what_was_seen(critique, design_brief):
        """The last attempt, as the brief writer needs to read it."""
        lines = []
        if (design_brief or {}).get("reads_as"):
            lines.append(f"It was aimed at: {design_brief['reads_as']}")
        if critique.get("reads_as"):
            lines.append(f"What was actually built reads as: "
                         f"{critique['reads_as']}")
        if critique.get("verdict"):
            lines.append(f"The verdict on it: {critique['verdict']}")
        issues = [_issue_line(i) for i in (critique.get("issues") or [])[:6]]
        if issues:
            lines.append("What was wrong with it:\n"
                         + "\n".join(f"- {i}" for i in issues))
        return "\n".join(lines)

    def _is_own_file(self, path):
        """Is this file entirely this run's own work, safe to start again in?

        True for a scene's subbuild, which always gets a fresh `parts/` file of
        its own, and for a single-object build onto a bench the survey found
        empty. False for anything the user already had, which is the case a
        re-plan must never touch.
        """
        if f"/parts/{Path(path).name}" in str(path).replace("\\", "/"):
            return True
        return bool((self.workbench or {}).get("empty"))

    def _keep_better(self, first, sub, *args):
        """Build it again from the new brief, and keep whichever came out best.

        The second attempt starts from an **empty file**. That is what makes it
        a second attempt rather than a second model built on top of the first:
        `build_ops` and `edit_model` append by default, so leaving the first
        attempt in place would give the re-plan a wrong model to grow out of,
        which is the one outcome worse than either attempt alone.

        The first is held in memory and put back if the re-plan came out worse.
        A re-plan follows a build that had already failed and it must not be
        able to turn a bad model into no model — the floor is what was there.
        """
        path = Path(_abs(sub.path))
        try:
            kept = path.read_bytes() if path.is_file() else None
        except OSError:
            kept = None
        if kept is not None:
            self._clear(path)

        result, state, report = self._attempt(sub, *args)
        if result is None:
            # The second builder died. Put the first attempt back and report
            # it, exactly as if the re-plan had never happened.
            return self._rewind(first, sub, path, kept)

        if self._score(result, state, report, sub) >= self._score(*first, sub):
            return result, state, report

        self._log(f"--- the re-planned {sub.name} came out worse; keeping the "
                  f"first attempt")
        self._emit("replan_discarded", name=sub.name)
        return self._rewind(first, sub, path, kept)

    def _rewind(self, first, sub, path, kept):
        """Put the first attempt back — the file *and* what is recorded of it.

        Both halves, because `_attempt` writes its findings onto ``sub`` as it
        goes. Restoring the bytes and leaving ``sub.validation`` and
        ``sub.critique`` holding the second attempt's would describe the model
        on disk with the report of a model that has just been thrown away —
        which is the one failure mode worse than either attempt, since
        everything downstream believes it.
        """
        result, state, report = first
        if kept is not None:
            self._restore(path, kept)
        sub.validation = report
        entry = state.critiques.get(sub.path)
        sub.critique = entry["critique"] if entry else None
        return first

    def _clear(self, path):
        """Empty a file so the next attempt starts from nothing.

        Deleted rather than truncated: the builder's first write to a path that
        does not exist creates it, and an empty-but-present file is a state some
        of the writing paths read as "a model with no parts in it yet" and
        others as a header to append after. Not existing has one meaning.
        """
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            self._log(f"--- could not clear {path} for the second attempt: {exc}")

    def _restore(self, path, content):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        except OSError as exc:
            self._log(f"--- could not put the earlier model back: {exc}")

    @staticmethod
    def _score(result, state, report, sub):
        """How well an attempt came out, for choosing between two of them.

        In the order the project already judges by everywhere else: the
        checklist first, then the stud grid, then whether the thing is
        recognisable, then whether anything was built at all.
        """
        critique = (state.critiques.get(sub.path) or {}).get("critique") or {}
        return (
            bool(state.requirements_met(sub.path)),
            bool(report.get("passed")),
            critique.get("recognisable") is not False,
            not result.get("gave_up"),
            int(report.get("parts") or 0),
        )

    def _requirements(self, sub, project, design_brief):
        """The acceptance criteria for one object, written once and kept.

        Read back from disk when they are already there — a resumed run, or a
        second turn on the same project, must be held to the list the first
        iteration was judged against. A checklist rewritten each time it is
        applied is not a checklist, it is a moving target, and a build could
        satisfy one version and be failed by the next.

        Best effort like every other pre-pass: no list leaves the run on the
        generic gate, which is where it was before this existed.
        """
        if not REQUIREMENTS_ENABLED or self._stopped():
            return None

        kept = requirements.for_object(project, sub.name)
        if requirements.items(kept):
            self._log(f"--- requirements for {sub.name}: "
                      f"{len(requirements.items(kept))} kept from an earlier run")
            self._emit("requirements", name=sub.name, record=kept, reused=True)
            return kept

        try:
            record = requirements.compose(
                sub.subject,
                requirements=sub.requirements,
                # The brief is deliberately not passed. It is direction for the
                # builder, not a contract with the user — a sofa was refused for
                # being red because the brief had said white and the request had
                # said nothing. See requirements.compose.
                # The room taken out, for the same reason the brief gets it
                # that way: a checklist written from a photograph will demand a
                # floor if it is shown the floor.
                reference=buildable_reference(
                    (self.reference or {}).get("description")),
                size_hint=sub.size_hint,
                size_from=sub.size_from,
                project=project, name=sub.name,
                llm=self._new_llm(task="plan"),
                should_stop=self.should_stop)
        except Exception as exc:
            self._log(f"--- no requirements for {sub.name}: {exc}")
            return None

        if not record:
            return None
        found = requirements.items(record)
        dropped = record.get("rejected_as_unmeasurable") or []
        invented = record.get("rejected_as_not_asked_for") or []
        self._log(f"--- requirements for {sub.name}: {len(found)} written"
                  + (f", {len(dropped)} dropped as unmeasurable" if dropped else "")
                  + (f", {len(invented)} dropped as not asked for" if invented else ""))
        self._emit("requirements", name=sub.name, record=record,
                   count=len(found))
        return record

    @staticmethod
    def _pieces_note(report):
        """Why a build that passed its checks is still not finished."""
        pieces = (report.get("connectivity") or {}).get("subassemblies")
        return (f"kept, but it is {pieces} separate pieces on the stud grid "
                f"and a build may end with at most "
                f"{runstate.MAX_SUBASSEMBLIES} — the parts are near each other "
                f"rather than joined to each other, so this comes apart when "
                f"it is picked up")

    def _brief(self, sub, project, modifying):
        """What this object should look like, before anything plans it.

        Skipped for an edit. "Paint the door blue" is not an invitation to
        redecide what the house looks like, and a builder handed a fresh design
        direction for a one-line change is a builder about to rebuild a model
        the user was happy with.

        Best effort in every other way too: no brief is exactly the behaviour
        this project had before the pass existed, so a failure here costs the
        build nothing but the variety it would have added.
        """
        if modifying or not BRIEF_ENABLED or self._stopped():
            return None

        # The look this object was given before the run was stopped. Deciding
        # it again would cost a call and could answer differently, which is how
        # half a scene ends up in one palette and half in another.
        kept = ((self.resumed or {}).get("briefs") or {}).get(sub.name)
        if kept:
            self.briefs[sub.name] = kept
            self._log(f"--- design brief for {sub.name}: kept from the "
                      f"stopped run")
            self._emit("design_brief", name=sub.name, brief=kept)
            return kept

        # How much this request licensed anyone to invent. Read per object and
        # not per project: "a house and something creative next to it" licenses
        # the second and not the first, and a scene-wide reading would let one
        # object's wording redesign another's.
        #
        # The petition is consulted too, but only where the run is building one
        # object — "build me a creative table" is an invitation the decomposer
        # may well have dropped on its way to the subject "a table", and losing
        # it there means the one request that did ask for invention silently
        # gets none. With several objects the whole petition is the wrong scope
        # for exactly the reason above.
        wording = sub.requirements
        if len(self.subconstructions) == 1 and self.petition:
            wording = f"{wording} {self.petition}"
        allowed = brief.licence(sub.subject, wording, self.reference)

        # Seeded on the project, not the object: every object in one scene gets
        # the same angle, so a house and the tree beside it are pushed the same
        # way instead of pulling the scene in two directions. A new project is a
        # new seed, which is what stops the same request twice from producing
        # the same model twice.
        angle = brief.variation(project, allowed)
        # Drawn from the same seed as the angle and from different bits of it,
        # so one scene is written by one designer with one angle — and which
        # designer is not decided by which angle. See brief.PERSONAS.
        stance = brief.persona(project, allowed)
        try:
            document = brief.compose(
                sub.subject,
                requirements=sub.requirements,
                # Without the room: a brief written from a photograph will put
                # the floor in the palette if it is shown the floor.
                reference=buildable_reference(
                    (self.reference or {}).get("description")),
                angle=angle,
                stance=stance,
                allowed=allowed,
                # Per object, not per project: the objects of a scene share the
                # angle and the stance, and each still picks its own way through
                # its own five candidates. Seeded so that a resumed run reaches
                # the same brief rather than quietly redesigning the tree.
                seed=f"{project}:{sub.name}",
                size_hint=sub.size_hint,
                should_stop=self.should_stop)
        except Exception as exc:
            self._log(f"--- no design brief for {sub.name}: {exc}")
            return None

        if not document:
            return None

        # Colours settled here are the scene's colours: the next object built
        # reads them out of the palette rather than picking its own red. See
        # palette.py.
        try:
            palette.record_scheme(project, brief.colours(document))
        except Exception:
            pass

        self.briefs[sub.name] = document
        sampled = document.get("sampling") or {}
        self._log(f"--- design brief for {sub.name} ({allowed}): "
                  f"{document.get('reads_as') or 'written'}"
                  + (f"  (p={sampled['chosen_probability']} of "
                     f"{sampled['candidates']} candidates)"
                     if sampled.get("chosen_probability") is not None else ""))
        self._emit("design_brief", name=sub.name, brief=document)
        return document

    def _salvage(self, sub, exc, state=None):
        """A builder that raised, and what is left of its work.

        Returns a stand-in result for a subbuild that wrote something before it
        died — so the rest of `_build_one` validates it, reports it and lets the
        scene keep it — or None when there is genuinely nothing there, which is
        the old behaviour and the honest answer for a run that never started.
        """
        why = f"{type(exc).__name__}: {exc}"
        written = survey.read(sub.path) if sub.path else {"empty": True}
        parts = 0 if written.get("empty") else written.get("parts", 0)

        if not parts:
            sub.status = "failed"
            sub.note = why
            self._emit("subbuild_end", name=sub.name, ok=False, note=sub.note)
            self._log(f"--- {sub.name} FAILED: {why}")
            return None

        # Checked now, because the builder never got to. Without this the file
        # goes on to be reported as "unchecked", which is the one thing it is
        # cheap not to be.
        if state is not None:
            try:
                state.record_validation(sub.path, validate(_abs(sub.path)))
            except Exception:
                pass

        self._log(f"--- {sub.name}: the builder died ({why}) with {parts} "
                  f"part(s) written; keeping them")
        self._emit("subbuild_interrupted", name=sub.name, error=why, parts=parts)
        return {
            "answer": "",
            "interrupted": why,
            "warning": (f"the builder stopped part-way through: {why}. "
                        f"{parts} part(s) had been written and were kept"),
        }

    def _reference_sets(self, sub, modifying=False):
        """Real sets that already built this object, opened before it starts.

        The builder has always had the tools to go and find these — a search, a
        details call, a read of one submodel — and across run after run it did
        not: it is four calls between the request and the geometry, and a model
        that believes it knows what a car looks like spends them placing bricks
        instead. So the harness does the looking. It costs no API call (the
        index is local) and it means every subconstruction starts on the page
        with real construction in front of it rather than a suggestion that
        some exists. See refsets.py.

        Skipped for an edit: "paint the door blue" does not need two houses of
        LDraw pasted into its context.
        """
        if modifying or self._stopped():
            return None
        try:
            found = refsets.find(sub.subject, requirements=sub.requirements)
        except Exception as exc:
            self._log(f"--- no reference sets for {sub.name}: {exc}")
            return None
        if not found:
            self._log(f"--- no reference sets matched {sub.name}")
            return None

        self._log(f"--- reference sets for {sub.name}: "
                  f"{refsets.headline(found)}")
        self._emit("reference_sets", name=sub.name,
                   summary=refsets.headline(found),
                   sets=[{k: v for k, v in d.items() if k != "source"}
                         for d in found])
        return found

    def _recall(self, sub, modifying=False):
        """This agent's own earlier work on this subject, opened before it starts.

        The same move as `_reference_sets` and for the same measured reason —
        see recall.py. The creations library and the notes have always been
        reachable through `search_reference` and `get_part_details`, and are
        therefore reached only by a builder that decided to reach, which across
        the runs on disk it does not. So the harness looks instead.

        No API call: the vector index is local and the rest is a file read.

        Skipped for an edit, like the sets. "Paint the door blue" does not need
        two of last week's trees pasted into its context.
        """
        if modifying or self._stopped():
            return None, None
        try:
            found = recall.find(sub.subject, requirements=sub.requirements)
            remembered = recall.remembered(sub.subject,
                                           requirements=sub.requirements)
        except Exception as exc:
            self._log(f"--- nothing recalled for {sub.name}: {exc}")
            return None, None
        if not found and not remembered:
            return None, None

        self._log(f"--- recalled for {sub.name}: "
                  f"{recall.headline(found, remembered)}")
        self._emit("recalled", name=sub.name,
                   summary=recall.headline(found, remembered),
                   creations=[{k: v for k, v in d.items() if k != "source"}
                              for d in found],
                   notes=remembered)
        return found, remembered

    def _subbuild_task(self, sub, index, total, petition, current_model,
                       modifying=False, design_brief=None, real_sets=None,
                       checklist=None, recalled=None):
        """The per-run half of the context: this object, and only this one."""
        siblings = [s.name for s in self.subconstructions if s is not sub]

        if modifying:
            closing = (
                f"This is a CHANGE to the model already in `{sub.path}`, not a "
                f"new build. Make it with `edit_model`, against the line "
                f"numbers you have been shown: replace the lines that change, "
                f"insert the ones that are new, delete the ones that go. Every "
                f"part the user did not ask you to touch stays exactly where "
                f"it is — that is what editing gets you and rewriting does "
                f"not — and the new work attaches to the build on real studs. "
                f"Do not start a fresh model, and do not put the change "
                f"somewhere beside the model. validate_model to "
                f"confirm it is still one connected build, then finish.")
        else:
            # Where sets were found, the first move is named as a call rather
            # than as an aspiration. "Consider using the references" is what
            # the prompt used to say, and what came back was a model built from
            # nothing with the references unread.
            start = (
                ("**Start with `copy_from_set`.** Real sets that already built "
                 "this are open in front of you, with the assembly to take and "
                 "the exact call under each one. Graft the closest one in "
                 "first, recolour it, and build the difference — from more "
                 "than one set where more than one has a piece of the answer. "
                 "Only design from nothing what none of them solved.\n\n")
                if real_sets else "")
            closing = (
                f"{start}"
                f"Plan it, write it to `{sub.path}`, validate it, and "
                f"validate_model checks that it looks like {sub.subject} too. "
                f"Everything you build goes in that one file, joined together "
                f"on the stud grid — never several loose pieces sitting apart. "
                f"Then call finish.")

        return build_task_context(
            petition=petition,
            siblings=siblings if total > 1 else None,
            subconstruction=sub.subject,
            index=index, total=total,
            requirements=sub.requirements,
            size_hint=sub.size_hint,
            max_pieces=sub.max_pieces,
            quantity=sub.quantity,
            model_path=sub.path,
            current_model=current_model,
            # What was on the workbench when the run began. Given only to a
            # build that writes into that same file: a subconstruction of a
            # scene has its own empty file, and telling it about a model it is
            # not touching is an invitation to build around one.
            workbench=(survey.as_text(self.workbench)
                       if current_model else None),
            known_faults=self._faults(sub.path),
            history=self.history,
            modifying=modifying,
            reference=self._reference_spec(),
            design_brief=design_brief,
            requirements_record=checklist,
            reference_sets=refsets.as_text(real_sets, subject=sub.subject),
            recalled=recalled,
            closing=closing,
        )

    # -- assembly ----------------------------------------------------------

    def _assemble(self, built, main_path, meta, project):
        """Compose the finished subbuilds into the project's model file.

        Done by the tool directly rather than by an agent: the placement is
        measured arithmetic, and there is nothing here for a language model to
        decide. An agent runs afterwards only if the composed scene fails to
        validate, which it should not.
        """
        self._emit("assembling", components=[s.name for s in built])
        self._log(f"\n--- assembling {len(built)} component(s) into {main_path}")

        components = [{"file": s.path, "name": s.name} for s in built]
        result = _json(call_tool("assemble_model", {
            "path": main_path,
            "components": components,
            "title": meta.get("summary") or "Scene",
        }))

        if "error" in result:
            self._emit("assembled", ok=False, note=result["error"])
            self._log(f"--- assembly FAILED: {result['error']}")
            return result

        report = validate(_abs(main_path))
        self._emit("assembled", ok=bool(report.get("passed")),
                   components=result.get("components"),
                   verdict=report.get("verdict"))
        self._log(f"--- assembled: {report.get('verdict')}")

        # A scene that does not validate after a measured layout means two
        # subbuilds genuinely intersect. Hand it to an agent to nudge apart —
        # it has measure_model and assemble_model and nothing else it could
        # waste the steps on.
        if not report.get("passed"):
            self._repair_scene(main_path, built, report, project)
        return result

    def _repair_scene(self, main_path, built, report, project):
        self._log("--- the assembled scene does not validate; repairing")
        state = RunState(subject="the whole scene", project=project,
                         target=main_path, require_render=render.available(),
                         # One block per finished object, and the objects are
                         # meant to stand apart — so each is checked for being
                         # whole and nothing asks them to touch each other.
                         objects="blocks")
        # What makes `edit_model` safe to offer here: the guard in tools.py
        # reads this and holds the pass to joining work.
        state.edit_scope = "assembly"
        state.record_write(main_path)
        state.record_validation(main_path, report)

        agent = LDrawAgent(llm=self._new_llm(task="build"),
                           max_steps=ASSEMBLY_STEPS, verbose=self.verbose,
                           state=state)
        agent.tools = _tools(only=set(ASSEMBLY_ONLY))
        agent.should_stop = self.should_stop
        agent.on_event = lambda event: self._forward(event, phase="assembly")

        listing = "\n".join(f"- {s.name}: `{s.path}`" for s in built)
        # How the picture said these objects stand together. The assembler
        # places them in a measured row, which never overlaps but is also never
        # what the picture showed — this is the only description of the
        # arrangement that exists, and moving them into it is exactly what the
        # two tools below are for.
        arrangement = (self.reference or {}).get("description") or {}
        wanted = arrangement.get("arrangement") if isinstance(arrangement, dict) else None
        try:
            agent.run(
                f"The scene at `{main_path}` was assembled from these finished "
                f"subconstructions:\n{listing}\n\n"
                f"It does not validate: {report.get('verdict')}\n\n"
                f"The components themselves are correct. They were each built "
                f"and validated on their own — do not rebuild them. What is "
                f"wrong is where they stand relative to each other, and "
                f"moving them is the first thing to reach for: "
                f"`move_submodel` shifts a whole object, `rotate_submodel` "
                f"turns one where it stands, and neither can break anything. "
                f"validate_model names what overlaps what and reports each "
                f"object's size under `size` — read that, then move the "
                f"overlapping objects apart on the stud grid (multiples of "
                f"20 LDU).\n\n"
                f"`edit_model` is here for the join itself, once the objects "
                f"are roughly where they belong and moving them further would "
                f"only spread the scene out: take out the handful of parts "
                f"that physically pass through another object, or add the "
                f"plate or tile that ties two of them together so the scene "
                f"reads as one build. It is limited to that on purpose — a "
                f"few parts per call and {ASSEMBLY_EDIT_BUDGET} across this "
                f"pass — and an edit the size of a rebuild is refused. If a "
                f"component is genuinely wrong, say so in your summary "
                f"instead of trying to fix it here.\n\n"
                f"Validate again, then finish."
                + (f"\n\nThe reference picture shows them arranged like "
                   f"this, and this is what the scene should end up looking "
                   f"like: {wanted}" if wanted else ""))
        except Exception as exc:
            self._log(f"--- scene repair failed: {exc}")

    # -- the answer --------------------------------------------------------

    def _summary(self, subs, meta):
        """What the user is told, assembled from what actually happened."""
        done = [s for s in subs if s.status == "done"]
        failed = [s for s in subs if s.status != "done"]

        lines = []
        if len(subs) > 1:
            lines.append(f"{meta.get('summary') or 'The scene'} — "
                         f"{len(done)} of {len(subs)} built.")

        # Said first, and said plainly. A model with parts off the grid is not
        # a model with a caveat, it is a model that cannot be built, and
        # burying that under a list of what went well is how a user finds out
        # from the bricks instead of from us.
        # Two things set this now — parts off the stud grid, and a build the
        # studs read as loose clumps — so the headline names neither and each
        # object's own note says which it was. Claiming the wrong one is worse
        # than claiming none: a user sent looking for off-grid coordinates in a
        # model whose coordinates are all fine finds nothing and concludes the
        # warning was noise.
        unbuildable = [s for s in subs if getattr(s, "unbuildable", False)]
        if unbuildable:
            lines.append(
                "**This did not come out buildable.** "
                + ", ".join(f"`{s.name}`" for s in unbuildable)
                + (" could not be assembled out of real bricks as it stands, "
                   if len(unbuildable) == 1 else
                   " could not be assembled out of real bricks as they stand, ")
                + "whatever the model on screen looks like. The run stopped "
                  "before that was fixed; the reason is beside each one below.")

        for sub in done:
            note = (f" ({sub.note})" if sub.note
                    and ("does not validate" in sub.note
                         or getattr(sub, "unbuildable", False)) else "")
            lines.append(f"- **{sub.name}**: {sub.subject}{note}")
            seen = (sub.critique or {}).get("verdict")
            if seen:
                lines.append(f"  {seen}")
        for sub in failed:
            lines.append(f"- **{sub.name}**: not built — {sub.note or 'unknown'}")
        return "\n".join(lines)

    def _remember(self, petition, main_path, stopped):
        """Write down where a stopped run got to, or clear a finished one.

        Every ending comes through here, which is why it lives here: a run that
        stops has something worth keeping and a run that finishes has something
        worth throwing away, and doing either anywhere else means finding all
        the places a run can end.

        Silent by design. Nothing is announced, nothing is asked, and a failure
        to write costs the next run a fresh start rather than costing this one
        anything at all.
        """
        project = Path(main_path).parent.name
        if not project:
            return
        try:
            if not stopped:
                resume.clear(project)
                return
            resume.save(
                project, petition,
                subconstructions=[s.as_dict() for s in self.subconstructions],
                briefs=self.briefs,
                history=self.history,
                note="stopped by the user")
        except Exception:
            pass

    def _result(self, petition, meta, main_path, answer="", stopped=False):
        self._remember(self.petition or petition, main_path, stopped)

        target = _abs(main_path)
        report = validate(target) if Path(target).is_file() else None

        result = {
            "petition": petition,
            "summary": meta.get("summary"),
            "scene": bool(meta.get("scene")),
            "subconstructions": [s.as_dict() for s in self.subconstructions],
            "model": main_path,
            "validation": report,
            "answer": answer,
            "stopped": stopped,
        }
        if stopped:
            result["warning"] = "stopped on request — the scene may be unfinished"
        elif report and not report.get("passed"):
            off_grid = len((report.get("connectivity") or {}).get(
                "misaligned_parts") or [])
            result["buildable"] = False
            result["warning"] = (
                f"the finished scene does not validate: "
                f"{report.get('verdict') or 'unknown fault'}"
                + (f" — {off_grid} part(s) are off the stud grid, so this "
                   f"cannot be built out of real bricks" if off_grid else ""))
        elif report:
            result["buildable"] = True

        # One last look at the finished thing, which is what the user asked
        # for in the first place. Best effort: a critique that cannot be had
        # does not cost them the build.
        if report and not stopped:
            try:
                scene = Path(main_path).parent.name
                images, sheet, critique, note = render.look(
                    target, subject=meta.get("summary") or petition,
                    project=scene)
                result["renders"] = [str(p) for _, p in images]
                result["contact_sheet"] = str(sheet) if sheet else None
                result["critique"] = critique
                self._emit("scene_seen", critique=critique,
                           contact_sheet=str(sheet) if sheet else None,
                           # the finished scene as it was actually judged, kept
                           # where the next build cannot overwrite it
                           images=_kept(scene, images, sheet))
            except Exception as exc:
                result["render_note"] = str(exc)

        self._emit("done", **{k: v for k, v in result.items()
                              if k in ("model", "answer", "stopped")})
        return result


def _kept(project, images=(), sheet=None, reference_image=None):
    """Archive pictures beside the run's trace. See ``trace.keep_image``.

    ``reference_image`` is one path or several — a project may have up to four
    reference pictures and the trace should show all of them, not the first.
    """
    kept = []
    for view, image in images or ():
        record = trace.keep_image(project, image, kind="render", view=view)
        if record:
            kept.append(record)
    if sheet:
        record = trace.keep_image(project, sheet, kind="sheet",
                                  label="contact sheet")
        if record:
            kept.append(record)
    for picture in _paths(reference_image):
        record = trace.keep_image(project, picture, kind="reference",
                                  label="the reference picture")
        if record:
            kept.append(record)
    return kept


def _paths(value):
    """One path, several, or none, as a list."""
    if not value:
        return []
    return [value] if isinstance(value, (str, Path)) else list(value)


def _abs(path):
    """A tool-style path (relative to out/) as an absolute one."""
    p = Path(path)
    return p if p.is_absolute() else OUT_DIR / p


def _json(text):
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return {"error": "unreadable tool result"}
