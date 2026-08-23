"""What a run has actually done, and whether it is allowed to stop.

The old loop ended when the model happened to emit a turn with no tool call in
it. That is not a decision, it is a coincidence: it ends a run that wrote
nothing exactly as readily as one that finished, and it gave the user no way to
tell the two apart.

So ending is a tool now — ``finish`` — and this is the ledger it is checked
against. Every write, validation and render is recorded here as it happens, and
``gate`` reads the ledger back to answer one question: has this run done the
work, or is it just tired?

The gate is deliberately about *sequence*, not just presence. A model validated
before the last write is a model nobody has checked; a render from two writes
ago is a picture of something that no longer exists. Both are recorded with the
write they belong to, so staleness is visible rather than assumed away.
"""

import time

from . import requirements as requirements_module

# How many times one run may question the reference picture. Per run, which is
# per subconstruction: each object of a scene is built by its own agent with its
# own ledger, so a scene of four has four of these allowances and not one shared
# between them.
MAX_ASKS = 10

# How many separate stud-connected pieces one object may be left in.
#
# `subassemblies` counts a model's components over the stud graph, and until now
# it was reported and never enforced. The reasoning was sound as far as it went:
# a clip, a hinge, a bracket or a Technic pin joins two parts without a stud, so
# a correct build routinely reads as more than one piece there, and failing a
# model on it would fail real sets. `objects_in_pieces` was the fault worth
# having instead, because it unions the stud graph with *contact*.
#
# What that leaves open is the case this cap is for. Contact is generous — a part
# merely resting against the build counts as joined — so a model can be a loose
# heap held together by nothing but proximity and still pass every check on the
# page. The stud graph is the one that says what real bricks actually clutch.
#
# So: a ceiling rather than a fault, low, and applied at the gate rather than in
# the checker. Measured over the 28 subbuilds this project has on disk, of the 18
# that pass validation 17 already sit at three or fewer. The one that does not is
# a minifigure whose accessories are held by grips no stud checker can see — the
# case `validate` excuses everywhere else, and the price of this being a count
# rather than a judgement.
#
# Only a builder is held to it. See `RunState.objects`: an assembled scene is
# *meant* to come apart into one piece per object, and a cap there would be a
# demand to glue the tree to the car.
MAX_SUBASSEMBLIES = 3


def too_many_pieces(report, objects="whole"):
    """Whether a validation report shows more pieces than a build may end with.

    One definition, three readers: the gate below refuses to let a run end on
    it, `validate_model` warns about it while there is still time to act, and
    the orchestrator declines to call such a build finished when its agent gave
    up rather than fixed it. Three copies of `> 3` would have been three places
    to forget.

    Answers False for anything but a single object, and for a report with no
    count in it — an unvalidated model is refused by the gate for being
    unvalidated, which is the more useful thing to say about it.
    """
    if objects != "whole":
        return False
    count = ((report or {}).get("connectivity") or {}).get("subassemblies")
    return count is not None and count > MAX_SUBASSEMBLIES


class RunState:
    """Per-run bookkeeping. One of these per agent run, handed to the tools."""

    def __init__(self, subject=None, requirements=None, project=None,
                 target=None, require_render=True, require_vision=True,
                 objects=None):
        # What this run is for, which is what the vision critic is told to
        # judge the renders against.
        self.subject = subject
        self.requirements = requirements
        # Names the renders directory, so one project's pictures never land on
        # another's.
        self.project = project
        # The model file this run is judged on. Set by the harness when it
        # knows (a subbuild has exactly one); left None for a free-form run, in
        # which case the last file written is the one that counts.
        self.target = target

        self.require_render = require_render
        self.require_vision = require_vision

        # What free-standing object this run's file holds, which is what lets
        # `validate_model` check that the thing being built is one connected
        # build rather than a handful of clumps sharing a file.
        #
        # "whole" for a builder: it was given one object and one file, so every
        # part in it belongs to that object and has to be joined to the rest.
        # "blocks" for the assembly pass, where one block is one object and the
        # objects are *meant* not to touch — a tree beside a car is a scene, and
        # joining them would be the fault. None asks nothing.
        #
        # Declared here rather than worked out from the file because no reading
        # of the geometry can tell a block that means "an object" from a block
        # that means "what you add at this step". See validation._disconnected.
        self.objects = objects

        # The design brief for this build — what it should look like, settled
        # before the run started. See brief.py. It rides on the ledger so that
        # `plan_construction` is written against it without the builder having
        # to remember to pass it in.
        self.brief = None

        # The acceptance criteria this build is judged against, written before
        # it started and stored on disk — see requirements.py. The builder does
        # not decide it has finished any more; this list does, and it is put to
        # the model at the end of every iteration.
        self.requirements = None
        # path -> {"seq": n, "result": {...}} — the last answer to that list.
        self.requirement_checks = {}

        # The real sets found for this object before the build started, opened
        # up — see refsets.py. Here for the same reason the brief is: the plan
        # has to be written against the sets the builder is holding, not
        # against a second search that may return different ones.
        self.reference_sets = None

        # This agent's own earlier work on this subject, found and opened
        # before the build started — see recall.py. Same reasoning as the line
        # above it, applied to the two stores that were only ever pulled: the
        # creations library and the notes were reachable and were not reached,
        # so the harness reaches instead and puts what it found on the ledger,
        # where the plan and the builder both see the same thing.
        self.recalled = None

        # What was already on the workbench when this run began, read and
        # looked at before anything was split or planned — see survey.py. Here
        # for the third time for the same reason as the two above: a plan for a
        # change to an existing model was being written from the raw LDraw
        # source and nothing else, so it knew the file had 200 type-1 lines in
        # it and not that they were a house. The survey is the only pass that
        # answers the second question, and the planner could not see it.
        self.workbench = None

        # path -> sequence number of the write. A monotonic counter rather than
        # a clock: two writes inside the same millisecond are still ordered.
        self._clock = 0
        self.writes = {}
        self.validations = {}   # path -> {"seq": n, "report": {...}}
        self.renders = {}       # path -> {"seq": n, "images": [...], ...}
        self.critiques = {}     # path -> {"seq": n, "critique": {...}}
        # path -> {"seq": n, "check": {...}} — the renders judged against the
        # reference picture the user attached, when there is one.
        self.reference_checks = {}
        # Set once describe_image has run, so the description travels with the
        # run rather than being fetched again.
        self.reference_description = None
        # True when this project has a reference image. A run that is supposed
        # to match a picture is not finished until it does.
        self.has_reference = False
        # Whether that picture shows what *this* run has to produce. False for
        # one object of a scene: the picture has the whole scene in it, so a
        # builder given the tree can never make its renders match a photograph
        # of a lumberjack beside a tree. It still gets the description — the
        # colours and proportions in it are the specification for its object
        # too — but it is not held to a comparison it has no way to pass.
        self.reference_is_this_build = True

        # `ask_vision_model`: how many times this run may put questions to the
        # reference picture.
        #
        # It used to be one set of questions, restored by each write. That was
        # too tight in the case it mattered: a builder that has genuinely
        # understood it needs three things from the picture had to spend a
        # write between each of them, and a builder halfway through a detail
        # could not go back and check. A picture is the specification, and
        # rationing questions about the specification to one per write buys
        # nothing when the questions are real.
        #
        # So it is a flat allowance for the whole run instead. The cap is still
        # there, because the failure it was built against is real — a builder
        # allowed to ask without limit asks one question at a time, waits, asks
        # the next, and spends the run interviewing a photograph instead of
        # placing bricks, at one vision call each. Ten is far more than a build
        # that is working needs, and a hard stop for one that has started
        # substituting questions for bricks.
        self.max_asks = MAX_ASKS
        self.asks = []  # [{"seq": n, "questions": [...], "answers": {...}}]

        # What this run is allowed to change with `edit_model`.
        #
        # None is a builder: it owns the file it is writing and may rewrite it
        # as often as it likes. "assembly" is the pass that composes finished
        # objects into a scene — it may reach into the file for the small
        # joining work that moving whole objects cannot do, and it may not use
        # that to redesign a component. See `_assembly_guard` in tools.py,
        # which is where the difference is actually decided.
        self.edit_scope = None
        # Part lines this run has added or removed under that scope, which is
        # the budget the guard spends.
        self.parts_edited = 0

        self.finished = None    # the accepted finish payload
        self.rejections = 0     # how many times the gate has said no

    # -- recording ---------------------------------------------------------

    def _tick(self):
        self._clock += 1
        return self._clock

    def record_write(self, path, lines=0, parts=0):
        self.writes[path] = {"seq": self._tick(), "lines": lines,
                             "parts": parts, "at": time.time()}
        if self.target is None:
            self.target = path

    # -- the question budget -----------------------------------------------

    def may_ask(self):
        """True while this run still has questions left to put to the picture."""
        return len(self.asks) < self.max_asks

    def asks_left(self):
        return max(0, self.max_asks - len(self.asks))

    def record_ask(self, questions, answers):
        """Spend one of the allowance. Nothing brings a spent one back."""
        self.asks.append({"seq": self._clock, "questions": list(questions),
                          "answers": answers})

    def asked_questions(self):
        """Every question this run has already put to the vision model."""
        return [q for entry in self.asks for q in entry["questions"]]

    def record_validation(self, path, report):
        self.validations[path] = {"seq": self._clock, "report": report}

    def record_render(self, path, images, sheet=None):
        self.renders[path] = {"seq": self._clock, "images": list(images),
                              "sheet": sheet}

    def record_critique(self, path, critique):
        self.critiques[path] = {"seq": self._clock, "critique": critique}

    def record_reference_check(self, path, check):
        self.reference_checks[path] = {"seq": self._clock, "check": check}

    def record_requirements_check(self, path, result):
        self.requirement_checks[path] = {"seq": self._clock, "result": result}

    def requirements_result(self, path=None):
        """The latest answer to the checklist for this model, or None."""
        entry = self.requirement_checks.get(path or self.current())
        return entry["result"] if entry else None

    def requirements_met(self, path=None):
        """True only when the checklist was put and every item came back true.

        Never optimistic about silence: a build nobody has checked is not one
        that passed, which is the whole reason the check exists.
        """
        result = self.requirements_result(path)
        return bool(result and result.get("passed"))

    def reference_verdict(self, path=None):
        """The latest reference comparison for the current model, or None."""
        entry = self.reference_checks.get(path or self.current())
        return entry["check"] if entry else None

    # -- reading back ------------------------------------------------------

    def current(self):
        """The model file this run is judged on."""
        if self.target:
            return self.target
        if not self.writes:
            return None
        return max(self.writes, key=lambda p: self.writes[p]["seq"])

    def _fresh(self, store, path):
        """True when ``store`` holds an entry made at or after the last write."""
        entry, write = store.get(path), self.writes.get(path)
        if entry is None or write is None:
            return False
        return entry["seq"] >= write["seq"]

    def validation_of(self, path):
        entry = self.validations.get(path)
        return entry["report"] if entry else None

    def passed(self, path):
        report = self.validation_of(path) or {}
        return bool(report.get("passed"))

    def misaligned(self, path=None):
        """Parts off the stud grid in the latest validation of ``path``.

        Kept apart from the other faults because it is the one no run may end
        holding. A missing part can genuinely defeat a build — the element does
        not exist and no coordinate saves it. A part off the grid is never that:
        it is a number that wants rounding to the lattice, the report says which
        line and by how much, and a model carrying one cannot be built out of
        real bricks. So this is the fault that `give_up` does not excuse.
        """
        report = self.validation_of(path or self.current()) or {}
        return list((report.get("connectivity") or {}).get("misaligned_parts") or [])

    def subassemblies(self, path=None):
        """Separate stud-connected pieces in the latest validation of ``path``.

        None when nothing has been checked, which is a different fact from one
        piece and must not read as it: the gate refuses an unvalidated model for
        being unvalidated rather than for a count nobody has taken.
        """
        report = self.validation_of(path or self.current()) or {}
        return (report.get("connectivity") or {}).get("subassemblies")

    def loose_pieces(self, path=None):
        """The clumps that are not the main body, from that same check."""
        report = self.validation_of(path or self.current()) or {}
        return list((report.get("connectivity") or {}).get("loose_pieces") or [])

    def snapshot(self):
        """What the run has done, for a summary or an event."""
        path = self.current()
        return {
            "target": path,
            "writes": len(self.writes),
            "validated": self._fresh(self.validations, path) if path else False,
            "passed": self.passed(path) if path else False,
            # Said here as well as in the report, because this is the line the
            # builder reads before it decides to call `finish` — and finding out
            # about the ceiling from a rejection costs a step that this saves.
            "subassemblies": self.subassemblies(path) if path else None,
            "may_end_with": (MAX_SUBASSEMBLIES
                             if self.objects == "whole" else None),
            # What actually ends this run. Shown every step so the builder can
            # see the list shrinking rather than discovering it at the end.
            "requirements": (len(requirements_module.items(self.requirements))
                             or None) if self.requirements else None,
            "requirements_met": (
                len((self.requirements_result(path) or {}).get("met") or [])
                if path and self.requirements else None),
            "rendered": self._fresh(self.renders, path) if path else False,
            "critiqued": self._fresh(self.critiques, path) if path else False,
            "has_reference": self.has_reference,
            "may_ask_vision": self.may_ask() if self.has_reference else None,
            "asks_left": self.asks_left() if self.has_reference else None,
            "matches_reference": (
                (self.reference_verdict(path) or {}).get("matches")
                if path and self.has_reference else None),
        }

    # -- the gate ----------------------------------------------------------

    def gate(self, path=None):
        """May this run end? Returns ``(ok, problems, next_step)``.

        ``problems`` is what is missing, in the order it should be dealt with,
        and ``next_step`` is the single tool call that would clear the first of
        them — a rejection that does not say what to do next is how a model
        gets stuck calling ``finish`` over and over.
        """
        path = path or self.current()

        if path is None:
            return False, ["nothing has been written yet — this run has "
                           "produced no model at all"], \
                   ("edit_model with the model you have, even if it is rough: "
                    "insert it at line 1")

        problems, next_step = [], None

        if not self._fresh(self.validations, path):
            problems.append(
                f"`{path}` has been written but not validated since — you do "
                f"not know whether it is buildable")
            next_step = next_step or f"validate_model on `{path}`"
        elif not self.passed(path):
            report = self.validation_of(path) or {}
            problems.append(
                f"`{path}` does not pass validation: "
                f"{report.get('verdict') or 'it has faults'}")
            next_step = next_step or (
                "fix every fault the last validate_model reported, write the "
                "model again, and validate it again")

        # However cleanly it validates, a build the studs read as a heap of
        # separate clumps is not one build. See MAX_SUBASSEMBLIES: this is a
        # ceiling on the *stud* graph, which is stricter than the fault checker
        # on purpose, and it is the one thing here that a passing model can
        # still be refused for.
        if self._fresh(self.validations, path) and too_many_pieces(
                self.validation_of(path), self.objects):
            pieces = self.subassemblies(path)
            problems.append(
                f"`{path}` is one object and it comes apart into {pieces} "
                f"separate pieces on the stud grid. A build may end with at "
                f"most {MAX_SUBASSEMBLIES}: past that it is not a model, it is "
                f"a handful of clumps standing near each other, and picking it "
                f"up in real bricks would leave {pieces - 1} of them behind. "
                f"Note that this is stricter than the faults above — parts "
                f"merely touching count as joined there, and only studs count "
                f"here.")
            loose = self.loose_pieces(path)
            if loose:
                problems.append(
                    "The clumps that are not the main body:\n" + "\n".join(
                        f"- {row.get('parts')} part(s), starting at line "
                        f"{row.get('line')} (`{row.get('part')}`), "
                        f"{row.get('gap_ldu')} LDU from the main body"
                        for row in loose[:6]))
            next_step = next_step or (
                "seat those clumps on the build. For each one, move it until "
                "its parts sit on real studs of the main body — x and z on "
                "multiples of 20 LDU, y on the level the part beneath puts it "
                "at — or bridge the gap with a plate long enough to reach "
                "both. Then validate_model and finish again.")

        if self.require_render and not self._fresh(self.renders, path):
            problems.append(
                f"`{path}` has not been rendered since it was last written — "
                f"nobody has seen what it looks like")
            next_step = next_step or (
                f"validate_model on `{path}` — it renders and looks at the "
                f"model as well as checking the grid")
        elif (self.require_render and self.require_vision
                and not self._fresh(self.critiques, path)):
            problems.append(
                f"`{path}` has been rendered but not looked at — a model can "
                f"pass every check and still not look like what was asked for")
            next_step = next_step or (
                f"validate_model on `{path}` — it always renders and looks, so "
                f"one call is all this needs")

        # The checklist, which is what actually ends a run now. It is checked
        # by the harness at the end of each iteration rather than by anything
        # the builder calls — this clause only catches a `finish` arriving
        # before the check has been put, or while it is still failing. See
        # requirements.py and LDrawAgent._requirements_gate.
        wanted = requirements_module.items(self.requirements)
        if wanted:
            result = self.requirements_result(path)
            if result is None:
                problems.append(
                    f"the {len(wanted)} requirement(s) this build is judged "
                    f"against have not been checked against the model yet")
                next_step = next_step or (
                    f"validate_model on `{path}` — the requirements are put to "
                    f"the model every time an iteration ends, and that is what "
                    f"finishes the run")
            elif not result.get("passed"):
                unmet = result.get("unmet") or []
                problems.append(
                    f"{len(unmet)} of {len(wanted)} requirement(s) are not met: "
                    + "; ".join(f"{r['id']} {r['text']}" for r in unmet[:4])
                    + (f", +{len(unmet) - 4} more" if len(unmet) > 4 else ""))
                next_step = next_step or (
                    "build what those requirements ask for, then "
                    "validate_model — the list is checked again each time")

        # The picture the user attached is the specification, so a model that
        # does not read as it is not finished — however cleanly it validates.
        # Only a checked-and-failed comparison blocks: a reference that could
        # not be compared at all (no vision model, no renderer) must not strand
        # the run in a check it has no way to pass.
        if (self.has_reference and self.reference_is_this_build
                and self._fresh(self.renders, path)):
            check = self.reference_checks.get(path)
            if check is None and self.require_vision:
                problems.append(
                    "this build has a reference picture and has not been "
                    "compared against it — you do not know whether you built "
                    "the right thing")
                next_step = next_step or f"validate_model on `{path}`"
            elif check is not None and check["check"].get("matches") is False:
                verdict = check["check"].get("verdict") or "it does not match"
                problems.append(
                    f"the model does not look like the reference picture: "
                    f"{verdict}")
                next_step = next_step or (
                    "fix the differences marked fatal and major — composition "
                    "and colour first — then write the model and "
                    "validate_model it again")

        if problems:
            return False, problems, next_step
        return True, [], None

    def refuse_give_up(self):
        """Why this run may not give up yet, or None.

        Giving up is a good answer to a build that cannot be made. It is not an
        answer to arithmetic. Every part named here is off the stud grid by a
        stated number of LDU, on a stated line, and moving it onto the lattice
        is the whole of the fix — so a run that stops here has not hit a wall,
        it has stopped short of the last edit.
        """
        rows = self.misaligned()
        if not rows:
            return None

        listed = "\n".join(
            f"- line {row.get('line')}: `{row.get('part')}` at "
            f"{row.get('position')}, {row.get('gap_ldu')} LDU off the grid"
            for row in rows[:8])
        more = (f"\n- …and {len(rows) - 8} more" if len(rows) > 8 else "")

        return {
            "finished": False,
            "why": (f"{len(rows)} part(s) are off the stud grid, and a model "
                    f"that is off the grid cannot be built out of real bricks. "
                    f"This is not something to give up on — every one of them "
                    f"is a coordinate that needs rounding onto the lattice."),
            "misaligned_parts": rows[:8],
            "problems": [f"{listed}{more}"],
            "do_next": ("`edit_model`, replacing each of those lines with the "
                        "same part at the nearest grid position — x and z on "
                        "multiples of 20 (10 for a jumper's half stud), y on "
                        "the level the part below it puts it at. Then "
                        "`validate_model` and `finish` again."),
            "note": ("give_up is for a build that genuinely cannot be made — a "
                     "part that does not exist, geometry that will not resolve. "
                     "It does not cover parts that are merely in the wrong "
                     "place."),
        }

    def reject(self, problems, next_step):
        """Record a refused finish and phrase it for the model."""
        self.rejections += 1
        return {
            "finished": False,
            "why": "this run is not finished yet",
            "problems": problems,
            "do_next": next_step,
            "note": ("Fix these and call finish again. If you genuinely cannot "
                     "— a part does not exist, the geometry will not resolve — "
                     "call finish with give_up=true and blocked_by set to what "
                     "stopped you. That is an honest answer and it is accepted; "
                     "claiming success is not."),
        }

    def accept(self, summary, gave_up=False, blocked_by=None):
        """Record an accepted finish."""
        payload = {
            "finished": True,
            "summary": summary,
            "gave_up": bool(gave_up),
            **self.snapshot(),
        }
        if blocked_by:
            payload["blocked_by"] = blocked_by
        self.finished = payload
        return payload
