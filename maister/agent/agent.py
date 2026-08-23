"""The agent loop: prompt -> tool calls -> validated LDraw file."""

import itertools
import json
import os
import time

from . import retry
from .config import DEFAULT_MAX_STEPS
from .llm import LLM
from .prompts import build_system_prompt
from .tools import agent_tools, call_tool

# Tools whose answer cannot change during a run: a lookup, a search, a plan.
# Repeating one of these verbatim is the shape a reasoning loop takes here - the
# model re-reads what it already has instead of writing the model. The stored
# answer is handed back with a nudge, so the loop costs a moment rather than a
# step. Everything else (write, validate, read back a file) is left alone, since
# calling it twice with the same arguments is how a repair round legitimately
# works.
REPLAYABLE = frozenset((
    "plan_construction", "search_parts", "search_reference",
    "get_part_details", "get_set_details",
))

# `read_model` is deliberately not in that set, though reading a set twice
# would be safe to replay. It also reads the model being built, and that changes
# under it - a replayed answer there would hand back the model as it was two
# writes ago, which is worse than the call it saves.

# The answer given for a tool call that was never made, so that a stopped turn
# still reads as a complete exchange.
_NOT_RUN = json.dumps({"error": "not run: the user stopped the run"})

# How many times a turn that called no tool at all is pushed back before the
# run is allowed to end anyway. The push-back is what makes `finish` the only
# way out; the cap is what stops a model that has decided to write prose from
# being asked for a tool call until the step limit runs out.
MAX_NUDGES = 3

# How many extra rounds a critique with faults in it may hold a build that has
# already satisfied every requirement. **Zero: a met checklist ends the run.**
#
# This was two, on the reasoning that one round is enough for a real fault to be
# fixed. The reasoning was sound and the measurement does not support it. Across
# every trace on disk, the critic held a finished build 11 times in 7 runs, and
# of the four builds that went on to take a second round:
#
#     0 improved      3 came back with the same issues      1 got worse
#
# The same complaint, repeated. That is the shape of a critique the builder
# cannot act on - "the hull is a rectangular box, not a boat" is a true remark
# about the design and not a fault with a fix, so the extra round re-renders,
# re-reads and re-reports it. Two of the seven ended unbuildable, and in one the
# model degraded under the extra rounds: a tree whose trunk was "a solid block"
# in round one was "a flat, wide base plate" by round two.
#
# So the critique stops being a veto and goes back to being what it is: a remark
# on a finished model. It still reaches the user - `_from_state` puts it in the
# run's result either way - and it still reaches the builder on every earlier
# iteration, which is where it can actually be acted on. What it no longer does
# is reopen a build the checklist has passed.
#
# Set above zero to bring the holding rounds back for one session.
CRITIQUE_ROUNDS = int(os.environ.get("LDRAW_CRITIQUE_ROUNDS", "0"))


def _issue_text(issue):
    """One line of a critique, whether it came back as a string or a record."""
    if isinstance(issue, dict):
        said = issue.get("issue") or issue.get("problem") or issue.get("what")
        fix = issue.get("fix") or issue.get("change")
        return " - ".join(str(p) for p in (said, fix) if p) or str(issue)
    return str(issue)

_NUDGE = (
    "That turn called no tool, which does not end the run - and neither does "
    "saying you are done. The run ends by itself when your requirements are "
    "all met, and they are checked every time you call `validate_model`. So "
    "if you believe the model is finished, call `validate_model` and let the "
    "check say so. If it is not, keep working: the next tool call is what "
    "moves this forward. If something genuinely stops you, call `finish` with "
    "give_up=true and say what it was."
)


class LDrawAgent:
    def __init__(self, llm=None, max_steps=DEFAULT_MAX_STEPS,
                 include_knowledge=False, verbose=True, on_event=None,
                 state=None):
        # "build": this is the loop that writes the .ldr file, the one place
        # worth paying for deliberation. See config.REASONING_PROFILES.
        self.llm = llm or LLM(task="build")
        self.max_steps = max_steps
        self.verbose = verbose
        self.on_event = on_event
        # What this agent is building, where the caller said - it decides which
        # standing context blocks are worth carrying. See prompts._droppable.
        self.system_prompt = build_system_prompt(
            include_knowledge,
            subject=getattr(state, "subject", None) if state else None)
        self.messages = [{"role": "system", "content": self.system_prompt}]
        self.transcript = []
        self._last_tools = []
        # The run's ledger: what has been written, validated and looked at.
        # Handed to the tools that record into it, and read by `finish` to
        # decide whether this run is allowed to end. None means no gate - a
        # bare chat turn ends the run as it always did.
        self.state = state
        # (tool, arguments) -> the result it produced, for REPLAYABLE tools
        self._answered = {}
        # (tool, failed arguments) -> the arguments that were tried instead.
        # A repair costs a model call the first time and nothing after it.
        self._repairs = {}
        # Which tools this agent may see. Narrowed per run for anything that
        # changes the user's workspace rather than the model being built - a
        # tool the model is never shown is one it cannot decide to call. Read
        # at construction rather than at import, so an agent built after the
        # settings dialog was touched sees the setting.
        self.tools = agent_tools()
        # Set by the caller to a predicate that goes True when the user asks
        # the run to stop. Checked between steps, between tool calls, and on
        # every streamed chunk.
        self.should_stop = None
        # How many times a critique has held a build whose requirements were
        # already met. Bounded by CRITIQUE_ROUNDS.
        self._critique_rounds = 0

    # -- logging ----------------------------------------------------------
    def _log(self, *a):
        if self.verbose:
            print(*a, flush=True)

    def _emit(self, type_, **fields):
        """Report progress to a watcher as it happens (the web UI polls these).

        A listener that raises must not take the run down with it.
        """
        if self.on_event is None:
            return
        try:
            self.on_event({"type": type_, **fields})
        except Exception:
            pass

    def _stopped_result(self, text, steps):
        """The run ended because the user asked it to, not because it finished.

        Whatever was written to the model file stays written - a half-finished
        build is still on disk, and pretending otherwise would be worse than
        saying so.
        """
        self._close_conversation(text)
        self._emit("answer", step=steps, text=text)
        self._log(f"\n=== STOPPED by request after {steps} step(s) ===")
        return {"answer": text, "steps": steps, "transcript": self.transcript,
                "stopped": True,
                "warning": "stopped on request - the model may be half-built"}

    def _requirements_gate(self, step):
        """Put the acceptance criteria to what was just built. Every iteration.

        Runs at the end of an iteration - which is where `validate_model` was
        called, since that is what an iteration *is* (see trace.ITERATION_ENDS).
        Returns a finished result when the build is done, and None to carry on,
        having told the builder what is still outstanding.

        **It is not allowed to quietly not happen.** It used to skip on three
        conditions and say nothing about any of them, and measured across the
        recent runs on disk that came to 38% of iterations ending with nobody
        having asked whether the model was finished. The worst of the three was
        the grid check failing, because `validate_model` then skipped the
        render and there was no contact sheet to judge - so precisely the
        iterations that needed the checklist most were the ones that never met
        it. Every remaining reason to skip is now announced, to the log and to
        the trace, so "it did not run" can never again look like "it ran and
        was happy".

        Failing the grid no longer skips the check either. It cannot *end* a
        run - that is enforced below, where the accepting happens - but the
        builder is still told which requirements its half-repaired model does
        and does not meet, which is the information that stops it repairing
        geometry it is about to throw away.
        """
        from . import requirements as acceptance
        from .config import REQUIREMENTS_ENABLED

        state = self.state
        if not REQUIREMENTS_ENABLED or state is None:
            return None
        # Only where an iteration actually ended. A step that searched for a
        # part has not produced anything new to judge.
        if not any(name == "validate_model" for name, _ in self._last_tools):
            return None

        def skipped(reason):
            """An iteration that ended without being judged, said out loud."""
            self._log(f"[{step}] REQUIREMENTS NOT CHECKED: {reason}")
            self._emit("requirements_skipped", step=step, reason=reason)
            return None

        record = getattr(state, "requirements", None)
        if not acceptance.items(record):
            # No checklist was ever written for this object, so there is
            # nothing to hold it to and the run will end on the generic gate
            # instead - which is the weak one this whole pass exists to
            # replace. Worth a line every iteration: it is the difference
            # between a judged build and an unjudged one.
            return skipped("no acceptance criteria were written for this build")

        path = state.current()
        entry = state.renders.get(path) if path else None
        if not entry or not entry.get("sheet"):
            return skipped(f"nothing has been rendered for `{path}` to judge")

        report = state.validation_of(path)
        try:
            result = acceptance.check(
                record, entry["sheet"], report=report, subject=state.subject)
        except Exception as exc:
            # Best effort, like every other reading pass. An unreachable
            # checker must not strand a build that is finished.
            return skipped(f"the checker could not be reached: {exc}")

        state.record_requirements_check(path, result)
        met, unmet = len(result.get("met") or []), len(result.get("unmet") or [])
        self._log(f"[{step}] requirements: {met} met, {unmet} not")
        self._emit("requirements_checked", step=step, passed=result["passed"],
                   met=result.get("met"), unmet=result.get("unmet"),
                   summary=result.get("summary"))

        if not result["passed"]:
            # Handed back as the work remaining, and as a user turn rather than
            # a tool result: no tool was called, so there is no call to answer.
            self.messages.append({
                "role": "user",
                "content": acceptance.outstanding(result) + (
                    "\n\nKeep building. This is the only list that ends the "
                    "run, and it is checked again after each validate_model.")})
            return None

        # The checklist is satisfied. Two things can still stop this being the
        # end, and both of them are the harness holding its own ground rather
        # than the builder's.

        # A model that does not sit on the stud grid cannot end a run, however
        # well it photographs. This used to be enforced by accident - the
        # render was skipped while the grid was failing, so there was never a
        # sheet to judge - and now that the check runs anyway it has to be said.
        if isinstance(report, dict) and not report.get("passed"):
            self._log(f"[{step}] requirements all met, but the grid check has not passed")
            self.messages.append({
                "role": "user",
                "content": (
                    "Every requirement is met, and this build still cannot "
                    "end: the model does not pass the stud-grid check. "
                    f"`{report.get('verdict')}` Fix the faults `validate_model` "
                    "listed - they are the only thing left - and validate "
                    "again. A model that cannot be built out of real bricks is "
                    "not finished whatever the checklist says.")})
            return None

        # And the eyes - which no longer hold the build. The checklist is a
        # list of things that must be true and not a list of everything that
        # could be wrong, so a critic looking at a passing model often still
        # has something to say. It used to buy another round. Measured over
        # every trace on disk, that round bought nothing: 0 of 4 improved, 3
        # repeated themselves, 1 got worse. See CRITIQUE_ROUNDS.
        #
        # A met checklist is the end of the run. The critique goes out with the
        # result as a remark on a finished model, which is what it is.
        critique = (state.critiques.get(path) or {}).get("critique") or {}
        issues = critique.get("issues") or []
        if issues and self._critique_rounds < CRITIQUE_ROUNDS:
            self._critique_rounds += 1
            self._log(f"[{step}] requirements all met, but the critic still "
                      f"lists {len(issues)} issue(s) - round "
                      f"{self._critique_rounds}/{CRITIQUE_ROUNDS}")
            self._emit("critique_holds", step=step, issues=issues,
                       round=self._critique_rounds, of=CRITIQUE_ROUNDS)
            self.messages.append({
                "role": "user",
                "content": (
                    "Every requirement is met, so the checklist is done with "
                    "you - but the last render was looked at and these are "
                    "still wrong:\n\n"
                    + "\n".join(f"- {_issue_text(i)}" for i in issues[:8])
                    + "\n\nThe checklist is a floor, not a ceiling: it says "
                      "what the model must have, not that everything about it "
                      "is right. Fix these, then validate_model again. If you "
                      "believe one of them is not actually a fault, say which "
                      "and why in your next message and fix the rest.")})
            return None

        answer = (result.get("summary") or "").strip()
        state.accept(answer or "every requirement met")
        self._emit("answer", step=step, text=answer)
        self._log(f"\n=== FINISHED after {step} step(s): every requirement met ===")
        return {"answer": answer, "steps": step, "transcript": self.transcript,
                "finished": True, "gave_up": False,
                "requirements": result, **_from_state(state)}

    def _prune_history(self):
        """Shorten the tool results the run has moved on from.

        Walks back through the conversation and collapses every tool result
        past the most recent ``KEEP_TOOL_RESULTS``. Rewrites the content of the
        message rather than removing it: a tool message answers a tool call by
        id, and a call left unanswered is a conversation the API rejects.
        """
        seen = 0
        for message in reversed(self.messages):
            if message.get("role") != "tool":
                continue
            seen += 1
            if seen <= KEEP_TOOL_RESULTS or message.get("name") in NEVER_PRUNED:
                continue
            shorter = _condense(message.get("name"), message.get("content"))
            if shorter is not None:
                message["content"] = shorter

    def _close_conversation(self, text):
        """Leave the history in a state the next message can be sent from.

        A stop lands mid-turn, and this agent outlives the run: the same
        conversation is reused for whatever the user types next. Two gaps can
        be left behind - an assistant turn holding tool calls that never ran,
        which the API rejects outright, and a user message with no reply at
        all, which invites the model to answer the abandoned request instead of
        the new one. Both are closed here, so Stop costs the run and not the
        conversation.
        """
        for index in range(len(self.messages) - 1, -1, -1):
            message = self.messages[index]
            if message.get("role") != "assistant":
                continue
            answered = {m.get("tool_call_id") for m in self.messages[index + 1:]}
            for call in message.get("tool_calls") or []:
                if call["id"] not in answered:
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "name": call["function"]["name"],
                        "content": _NOT_RUN,
                    })
            break

        if self.messages and self.messages[-1].get("role") in ("user", "tool"):
            self.messages.append({
                "role": "assistant",
                "content": (text or "").strip() or "(stopped by the user)",
            })

    def _call(self, name, arguments, should_stop=None):
        """Run a tool, replaying the answer if this exact call was already made."""
        key = (name, _canonical(arguments)) if name in REPLAYABLE else None
        if key is not None and key in self._answered:
            self._log(f"     (repeat call to {name} - replaying the first answer)")
            return json.dumps({
                "repeat_call": True,
                "note": "You already made this exact call. This is the answer "
                        "you got. Use it and move on - if it was not what you "
                        "needed, change the arguments or write the model with "
                        "what you have.",
                "result": json.loads(self._answered[key]),
            }, ensure_ascii=False)

        result = self._call_with_retries(name, arguments, should_stop)
        if key is not None and _tool_ok(result):
            self._answered[key] = result
        return result

    def _call_with_retries(self, name, arguments, should_stop=None):
        """Run a tool, correcting the call and trying again when it errors.

        A tool that fails on a bad path or an argument it does not take is a
        typo, not a decision, and handing that back to the builder costs it a
        whole reasoning turn to work out what the error already said. So the
        call is repaired here and retried - see retry.py, which is also where
        the tools that must *never* be retried are listed and why.
        """
        result = call_tool(name, arguments, should_stop=should_stop,
                           state=self.state)
        if not retry.should_retry(name, result):
            return result

        original = dict(arguments) if isinstance(arguments, dict) else arguments
        if isinstance(original, str):
            try:
                original = json.loads(original or "{}")
            except ValueError:
                original = {}

        current = dict(original)
        for attempt in range(2, retry.MAX_ATTEMPTS + 1):
            if should_stop is not None and should_stop():
                return result

            # A repair worked out once is remembered: the same bad call later
            # in the run is corrected without paying for the model again.
            key = (name, _canonical(current))
            fixed = self._repairs.get(key)
            if fixed is None:
                fixed = retry.repair(name, current, result, state=self.state,
                                     llm=self.llm, tools=self.tools)
                if fixed is None:
                    return result
                self._repairs[key] = fixed

            self._log(f"     (retry {attempt}/{retry.MAX_ATTEMPTS}: {name} "
                      f"failed, calling it again as {_brief(fixed)})")
            self._emit("tool_retry", tool=name, attempt=attempt,
                       error=_brief(result, 200), arguments=fixed)

            current = fixed
            result = call_tool(name, current, should_stop=should_stop,
                               state=self.state)
            if not retry.should_retry(name, result):
                # Say that it was corrected even when it worked, so the builder
                # writes the next call the way that succeeded.
                if _tool_ok(result) and current != original:
                    return retry.note(result, attempt, original, current)
                return result

        return result

    def _turn_task(self):
        """Whether the turn about to run is worth the model's thinking mode.

        Two turns in a build decide geometry: the one after ``plan_construction``
        returns, which writes the file, and the one after a ``validate_model``
        that found faults, which repairs it. Those get "build" - thinking mode,
        low effort.

        Everything else runs as "chat". Catalogue lookups, re-validation of a
        model that already passed and the closing summary do not place a brick,
        and thinking costs three to four times the tokens and the latency of a
        turn. On a model outside config.REASONING_MODELS this decides nothing:
        neither task sends any reasoning argument at all.
        """
        for name, result in self._last_tools:
            if name == "plan_construction" and _tool_ok(result):
                return "build"
            # Both halves of validate_model buy a repair turn: a grid it
            # failed, and a critique that found something. The fix for either
            # has to be worked out in coordinates the model cannot see.
            if name == "validate_model" and (not _validation_passed(result)
                                             or _critique_found_issues(result)):
                return "build"
        return "chat"

    # -- main loop --------------------------------------------------------
    def run(self, task):
        self.messages.append({"role": "user", "content": task})
        self._log(f"\n=== TASK ===\n{task}\n")

        # Everything this agent is working from, once, before it starts. The
        # standing prompt does not change during a run and the task is the
        # other half of it, so the two together are the whole of the input -
        # which is what makes the outputs below readable afterwards.
        self._emit("context", task=task, system=self.system_prompt,
                   max_steps=self.max_steps,
                   tools=[t["function"]["name"] for t in self.tools])

        last_text = ""
        # (tool, result) from the step just finished, which is what says whether
        # the next turn is a geometry turn
        self._last_tools = []
        nudges = 0

        def stopped():
            return bool(self.should_stop and self.should_stop())

        # Unbounded unless somebody asked for a bound. `max_steps` of 0 (the
        # default) is a loop that ends when the run does - see
        # DEFAULT_MAX_STEPS. The stop button is checked at the top of every
        # turn and inside the model call, so "no limit" never means "cannot be
        # interrupted".
        steps = (itertools.count(1) if not self.max_steps
                 else range(1, self.max_steps + 1))
        step = 0
        for step in steps:
            if stopped():
                return self._stopped_result(last_text, step - 1)
            self._emit("step", step=step)
            turn_task = self._turn_task()
            msg = self.llm.complete(
                self.messages,
                tools=self.tools,
                task=turn_task,
                # the reply as it is written, for anyone watching the run
                on_delta=lambda piece, s=step: self._emit("delta", step=s, text=piece),
                # and the tool call as it is composed, before it can be run
                on_tool=lambda i, name, args, s=step: self._emit(
                    "tool_stream", step=s, index=i, tool=name, arguments=args),
                # A dropped connection is retried inside the client. Said out
                # loud so a run that pauses for half a minute is explained
                # rather than mysterious.
                on_retry=lambda attempt, exc, s=step: self._emit(
                    "llm_retry", step=s, attempt=attempt,
                    error=f"{type(exc).__name__}: {exc}"),
                should_stop=self.should_stop,
            )

            if stopped():
                return self._stopped_result(
                    (msg.content or "").strip() or last_text, step)

            tool_calls = getattr(msg, "tool_calls", None) or []
            content = (msg.content or "").strip()

            entry = {"role": "assistant", "content": msg.content or ""}
            if tool_calls:
                entry["tool_calls"] = [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.function.name,
                                  "arguments": tc.function.arguments}}
                    for tc in tool_calls
                ]
            self.messages.append(entry)

            if content:
                last_text = content
                # Only intermediate thinking is a "text" event; the closing
                # message is emitted below as "answer" instead of twice.
                if tool_calls:
                    self._emit("text", step=step, text=content)
                self._log(f"[{step}] {content[:1500]}")

            if not tool_calls:
                # With a gate in place, a turn of prose is not an ending. Push
                # it back and let the model either finish properly or carry on
                # - but only so many times, since a model that will not call a
                # tool will not start because it was asked a fourth time.
                if self.state is not None and nudges < MAX_NUDGES:
                    nudges += 1
                    self._log(f"[{step}] (no tool call - nudged {nudges}/{MAX_NUDGES})")
                    self.messages.append({"role": "user", "content": _NUDGE})
                    self._last_tools = []
                    continue

                self._emit("answer", step=step, text=last_text)
                self._log(f"\n=== DONE after {step} step(s) ===")
                result = {"answer": last_text, "steps": step,
                          "transcript": self.transcript}
                if self.state is not None and self.state.finished is None:
                    result["warning"] = (
                        "the run ended without calling finish; the model may "
                        "be unfinished")
                    result.update(_from_state(self.state))
                return result

            allowed = {t["function"]["name"] for t in self.tools}
            step_tools = []
            for tc in tool_calls:
                name = tc.function.name
                args = tc.function.arguments

                # Stopped part-way through a batch of calls: run no more of
                # them, but answer every one, since a call left without a
                # result would break the next message the user sends.
                if stopped():
                    self.messages.append({"role": "tool", "tool_call_id": tc.id,
                                          "name": name, "content": _NOT_RUN})
                    continue

                # It was never offered, so a call to it is the model inventing
                # one. Refuse rather than run it.
                if name not in allowed:
                    refusal = json.dumps({"error": f"{name} is not available in this run"})
                    self._log(f"[{step}] -> {name} REFUSED (not offered)")
                    self.messages.append({"role": "tool", "tool_call_id": tc.id,
                                          "name": name, "content": refusal})
                    continue

                self._log(f"[{step}] -> {name}({_brief(args)})")
                self._emit("tool_start", step=step, call_id=tc.id,
                           tool=name, arguments=args)

                started = time.monotonic()
                result = self._call(name, args, should_stop=self.should_stop)
                elapsed_ms = int((time.monotonic() - started) * 1000)
                result, images = _take_images(result)
                self.transcript.append({"step": step, "tool": name,
                                        "arguments": args, "result": result})
                self._emit("tool_end", step=step, call_id=tc.id, tool=name,
                           result=result, ms=elapsed_ms, ok=_tool_ok(result),
                           images=images)
                self._log(f"[{step}] <- {_brief(result, 600)}")

                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": name,
                    "content": result,
                })
                step_tools.append((name, result))

            self._last_tools = step_tools
            # The results this step produced are the fresh ones now; whatever
            # they pushed out of the window is collapsed. See _prune_history.
            self._prune_history()

            # The end of an iteration, and therefore the moment the acceptance
            # criteria are put to the model that was just built. This is what
            # replaced the builder deciding for itself that it was done: it
            # builds and validates, and the harness says whether that was
            # enough. See requirements.py.
            gate = self._requirements_gate(step)
            if gate is not None:
                return gate

            # An accepted `finish` is the intended ending, and the only one
            # that means the work is done. A refused one is just another tool
            # result the model has to answer.
            done = _finished(step_tools)
            if done is not None:
                answer = (done.get("summary") or last_text or "").strip()
                self._emit("answer", step=step, text=answer)
                self._log(f"\n=== FINISHED after {step} step(s) ===")
                result = {"answer": answer, "steps": step,
                          "transcript": self.transcript, "finished": True,
                          "gave_up": bool(done.get("gave_up"))}
                if done.get("gave_up"):
                    result["warning"] = (
                        f"the agent stopped without finishing: "
                        f"{done.get('blocked_by') or 'no reason given'}")
                if self.state is not None:
                    result.update(_from_state(self.state))
                return result

            if stopped():
                return self._stopped_result(last_text, step)

        # Only reachable with a bound set: the unbounded loop leaves through
        # `finish`, a give-up, an error or a stop.
        self._emit("answer", step=step, text=last_text)
        self._log(f"\n=== STOPPED at the {self.max_steps}-step limit ===")
        result = {"answer": last_text, "steps": step,
                  "transcript": self.transcript,
                  "warning": "step limit reached; the model may be unfinished"}
        if self.state is not None:
            result.update(_from_state(self.state))
        return result


# How many tool results stay in the conversation in full. Everything older is
# collapsed to the part of it that was still worth having.
#
# A run has no step limit (see DEFAULT_MAX_STEPS) and nothing ever left the
# conversation, so the context grew without a ceiling: measured on a 27-step
# build of a *tiny house*, tool output alone came to 30,110 tokens and the last
# turn cost about 64,000 - 30,412 of standing prompt, 3,795 of task, and the
# rest the run talking to itself. Fifteen `build_ops` results accounted for
# 11,960 of it, each one largely the model's own placements read back to it.
#
# Six is chosen to cover a repair round: a validate, the edit that answers it,
# and the validate after that, with room to spare. What is dropped is dropped
# because the *file on disk* is the state of the build - a stale search result
# or a superseded write is a description of a model that no longer exists, and
# `read_model` re-reads the real one for nothing.
KEEP_TOOL_RESULTS = 6

# The plan is not a stale result. It is what the build is following, it is
# referred to at every step, and it is one call however long the run - so it is
# kept in full wherever it falls.
NEVER_PRUNED = frozenset(("plan_construction",))

# A value longer than this is the bulk: a parts list, a connectivity report, a
# set's source. Shorter ones are the verdicts and counts worth remembering.
_PRUNE_OVER = 160


def _condense(name, content):
    """An old tool result reduced to what is still worth carrying, or None.

    None means leave it alone - it is already small, or it is not JSON, and
    rewriting it would cost more than it saves.
    """
    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("pruned"):
        return None

    kept, dropped = {}, []
    for key, value in payload.items():
        if len(json.dumps(value, default=str)) <= _PRUNE_OVER:
            kept[key] = value
        else:
            dropped.append(key)
    if not dropped:
        return None

    # The one thing that must survive a search: the numbers. Everything a
    # builder does with a search result afterwards is place one of these, and
    # the descriptions that make the entry big are not needed to do it.
    if name in ("search_parts", "search_reference"):
        found = [row.get("part_id") for row in (payload.get("results") or [])
                 if isinstance(row, dict) and row.get("part_id")]
        if found:
            kept["part_ids"] = found

    kept["pruned"] = (
        f"This is an older `{name}` result, shortened to keep the conversation "
        f"readable - {', '.join(dropped)} were dropped. The model file on disk "
        f"is the state of the build; read_model gives you any of it again.")
    return json.dumps(kept, ensure_ascii=False, default=str)


def _take_images(result):
    """Split a tool result into what the model reads and what the trace shows.

    A tool that rendered the model, or was shown the reference picture, puts
    what it saw under ``_images`` - filenames in the run's own trace archive.
    The builder is text-only and cannot open any of them, so the key never
    reaches the conversation: it comes out here, goes to the trace, and the
    model is handed the result without it.
    """
    if not isinstance(result, str) or '"_images"' not in result:
        return result, None
    try:
        payload = json.loads(result)
    except (TypeError, ValueError):
        return result, None
    if not isinstance(payload, dict) or "_images" not in payload:
        return result, None
    images = payload.pop("_images") or None
    return json.dumps(payload, ensure_ascii=False, default=str), images


def _finished(step_tools):
    """The payload of an accepted ``finish`` in this step, or None.

    A refused finish comes back ``{"finished": false, ...}``, which is not an
    ending - it is the gate telling the model what is still missing.
    """
    for name, result in step_tools:
        if name != "finish":
            continue
        try:
            payload = json.loads(result)
        except (TypeError, ValueError):
            continue
        if isinstance(payload, dict) and payload.get("finished"):
            return payload
    return None


def _from_state(state):
    """What the ledger says about the model, for the run's result."""
    snapshot = state.snapshot()
    out = {"target": snapshot["target"], "validated": snapshot["validated"],
           "passed": snapshot["passed"], "rendered": snapshot["rendered"]}
    path = snapshot["target"]
    if path and path in state.renders:
        out["renders"] = state.renders[path]["images"]
        out["contact_sheet"] = state.renders[path].get("sheet")
    if path and path in state.critiques:
        out["critique"] = state.critiques[path]["critique"]
    return out


def _canonical(arguments):
    """Tool arguments as a stable string, so trivial rewordings still match.

    ``{"query": "brick 2 x 4"}`` and ``{ "query":"brick 2 x 4" }`` are the same
    call, and a model that loops rarely loops with byte-identical JSON.
    """
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments or "{}")
        except ValueError:
            return " ".join(arguments.split()).lower()
    if not isinstance(arguments, dict):
        return str(arguments).lower()
    return json.dumps(
        {k: (v.strip().lower() if isinstance(v, str) else v)
         for k, v in sorted(arguments.items()) if v is not None},
        sort_keys=True, default=str,
    )


def _validation_passed(result):
    """True only for a validate_model result that came back clean.

    A malformed or errored result counts as not passing: something is wrong with
    the model either way, and the repair turn is where thinking earns its keep.
    """
    try:
        return bool(json.loads(result).get("passed"))
    except (TypeError, ValueError, AttributeError):
        return False


def _critique_found_issues(result):
    """True when the looking half of validate_model found visual faults.

    A build that came out in loose pieces counts even when the issues list is
    empty: it is the fault validation cannot see - two correct models standing
    a few studs apart pass every geometric check there is - so it has to be the
    thing that buys the repair turn.
    """
    try:
        seen = json.loads(result).get("seen") or {}
    except (TypeError, ValueError, AttributeError):
        return False
    return bool(seen.get("issues")) or seen.get("one_build") is False


def _tool_ok(result):
    try:
        return "error" not in json.loads(result)
    except (TypeError, ValueError):
        return True


def _brief(value, limit=300):
    if not isinstance(value, str):
        value = json.dumps(value, default=str)
    value = " ".join(value.split())
    return value if len(value) <= limit else value[:limit] + " ..."
