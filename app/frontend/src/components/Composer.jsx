import { useCallback, useEffect, useRef, useState } from "react";
import ReferenceChip from "./ReferenceChip";
import { api } from "../api";

/**
 * The one box you talk to the agent through: `[+] [what to build] [Send]`.
 *
 * It floats over the workbench rather than sitting in a panel down the side,
 * and it shows none of the agent's working-out. That is deliberate — the trace
 * is a better account of a run than a scrolling list of tool calls ever was,
 * and two views of the same thing means reading neither. What is left here is
 * the parts of a conversation that are not narration: what you asked for, the
 * pictures you attached, whether it is still going, and the reply.
 *
 * It stays mounted while the run polls even when it is not on screen — a build
 * started here and then watched from the Model view must not stop being
 * watched because the box that started it was hidden.
 */

// The model streams, so this is how often the run is asked what it is doing.
const POLL_MS = 400;

// No step budget. A run ends when the agent finishes, gives up, or you stop
// it — see DEFAULT_MAX_STEPS in config.py. 0 is what says "no limit".
const MAX_STEPS = 0;

// How many reference pictures a project takes. The backend enforces it —
// reference.MAX_IMAGES, which is where the reasoning lives — and this is here
// so the box can say so before the upload rather than after it.
const MAX_REFERENCES = 4;

// What the agent is doing while a tool call is in flight.
//
// Every tool the agent can be given has a line here, and that is a rule rather
// than a list that grew: a tool with no label showed the raw function name at
// best and nothing at worst, and the one it happened to be missing was
// `build_ops` — the most-called tool there is. Half the run reported itself as
// silence because of one absent key. If a tool is added to tools.py it gets a
// line here in the same commit.
const TOOL_LABELS = {
  plan_construction: "Drawing up the construction plan",
  search_parts: "Searching the parts catalogue",
  search_reference: "Looking through real sets and past work",
  get_part_details: "Reading part geometry",
  get_set_details: "Reading a real set",
  read_model: "Reading LDraw source",
  build_ops: "Laying parts on the grid",
  edit_model: "Writing the model file",
  copy_from_set: "Lifting an assembly out of a real set",
  validate_model: "Checking the grid and looking at it",
  ask_about_image: "Looking at your reference picture",
  move_submodel: "Moving a component into place",
  rotate_submodel: "Turning a component",
  assemble_model: "Composing the scene",
  finish: "Finishing up",
};

/**
 * What the agent is doing when it is NOT inside a tool call.
 *
 * A run is not a flat sequence of tool calls. Before the first brick it splits
 * the request into objects, decides what each should look like and writes the
 * acceptance checklist; after the last one it composes the scene and looks at
 * it. The backend announces every one of those — see `_emit` in
 * orchestrator.py — and until now this box read four event types out of about
 * twenty-five and showed nothing for the rest. That is the silence: not the
 * agent going quiet, but a status line that was not listening.
 *
 * Returns the line to show, or null for an event that says nothing new. The
 * phase persists until something replaces it, because a phase is a state the
 * run is in rather than an instant — between two tool calls of a subbuild the
 * honest answer is still "building the tree".
 */
function phaseOf(e) {
  switch (e.type) {
    case "planning":
      return "Planning the build";
    case "decomposing":
      return "Reading the request";
    case "decomposed":
      return e.summary ? `Building: ${e.summary}` : "Working out what to build";
    case "workbench":
      return e.empty ? "Starting from an empty baseplate"
                     : "Looking at what is already on the workbench";
    case "editing":
      return e.changes?.length ? `Changing ${e.changes.join(", ")}`
                               : "Changing what is on the workbench";
    case "reference":
      return "Reading your reference picture";
    case "reference_sets":
      return "Finding real sets to work from";
    case "recalled":
      return "Looking up what it built before";
    case "design_brief":
      return e.replanned ? "Deciding on a different look"
                         : "Deciding how it should look";
    // The one phase that visibly undoes work: the model came back looking like
    // the wrong thing, so the look is being decided again and it is built
    // again from that. Said plainly, because a build restarting with no
    // explanation reads as the run having gone wrong.
    case "replanning":
      return e.reads_as ? `It came out looking like ${e.reads_as} — trying a different design`
                        : "It came out as the wrong thing — trying a different design";
    case "replan_discarded":
      return "Keeping the earlier version";
    case "requirements":
      return "Writing the acceptance checklist";
    case "requirements_checked":
      return e.passed ? "Every requirement met"
                      : "Checking it against the requirements";
    case "requirements_skipped":
      return "Checking the build";
    case "critique_holds":
      return "Fixing what the critique found";
    case "tool_retry":
      return `Retrying ${TOOL_LABELS[e.tool]?.toLowerCase() || e.tool}`;
    case "assembling":
      return e.components?.length
        ? `Composing the scene from ${e.components.length} component(s)`
        : "Composing the scene";
    case "assembled":
      return "Scene composed";
    case "scene_seen":
      return "Looking at the finished scene";
    default:
      return null;
  }
}

/** One subbuild, named the way a person would say it. */
function buildingLine(builds) {
  const names = [...builds.values()].filter(Boolean);
  if (!names.length) return null;
  if (names.length === 1) return `Building ${names[0]}`;
  if (names.length === 2) return `Building ${names[0]} and ${names[1]}`;
  return `Building ${names.length} objects at once`;
}

// Tool calls that change the model on disk, and so should put the new model on
// screen the moment they land rather than at the end of the run.
// validate_model is here because it repairs as it checks: overlaps that are
// pure arithmetic are slid back onto the grid before the report comes back, so
// the file on disk can differ from what is on screen once it returns.
const MODEL_CHANGED_BY = new Set([
  "edit_model", "assemble_model", "validate_model",
]);

/** A run's live state, folded forward one batch of events at a time. */
function blank() {
  return { running: new Map(), tool: null, phase: null, builds: new Map() };
}

/**
 * Apply the events a poll just returned.
 *
 * Incremental, because ``/api/runs/{id}?since=`` hands back only what is new —
 * and because the only question asked of the stream is "what is in flight",
 * which does not need the events kept. Which step it is on is not one of them:
 * there is no budget to count against, and it was never the thing anyone
 * wanted to know. Nor is what the build is borrowing from — the run's workings
 * belong in the trace, not over the model.
 */
function advance(state, events) {
  const next = {
    ...state,
    running: new Map(state.running),
    builds: new Map(state.builds),
  };
  for (const e of events) {
    if (e.type === "tool_start") next.running.set(e.call_id ?? e.i, e.tool);
    else if (e.type === "tool_end") next.running.delete(e.call_id ?? e.i);
    else if (e.type === "subbuild_start") {
      // Several of these run at once — a scene of five objects is five
      // conversations in parallel — so they are counted rather than replaced.
      next.builds.set(e.name, e.subject || e.name);
    } else if (e.type === "subbuild_end" || e.type === "subbuild_interrupted") {
      next.builds.delete(e.name);
      next.phase = e.type === "subbuild_interrupted"
        ? `${e.name} stopped short`
        : `Finished ${e.name}`;
    } else if (e.type === "plan") {
      next.phase = "Plan ready";
    } else {
      const said = phaseOf(e);
      if (said) next.phase = said;
    }
  }
  const inFlight = [...next.running.values()];
  next.tool = inFlight.length ? inFlight[inFlight.length - 1] : null;
  return next;
}

export default function Composer({
  projectId,
  visible = true,
  onModelChanged,
  onValidation,
  onActivity,
  onRun,
}) {
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [live, setLive] = useState(blank);
  // The last thing the agent said, and anything that went wrong. The trace
  // holds the whole of both; this is the one line worth reading without going
  // looking for it.
  const [reply, setReply] = useState(null);

  // Reference images attached to this project. They outlive the message that
  // carried them, so they are loaded from the backend rather than kept here.
  const [references, setReferences] = useState([]);
  // How many pictures are still going up, so each one waiting gets a brick of
  // its own rather than the row growing by one however many were dropped in.
  const [attaching, setAttaching] = useState(0);
  const [attachError, setAttachError] = useState(null);

  const pollRef = useRef(null);
  const fileRef = useRef(null);
  const runRef = useRef(null); // the run in flight, so Stop knows what to stop

  const stopPolling = () => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = null;
  };

  // Switching project parks whatever was going on rather than carrying it over
  // — and then asks whether the project being switched *to* has a build of its
  // own still going, because it very well might. A run belongs to the backend,
  // not to this component: it survives a reload, a project switch and a second
  // tab, and until this asked, all three left a build running with the Stop
  // button gone and the read-out dead. See `watch`.
  useEffect(() => {
    stopPolling();
    setLive(blank());
    setBusy(false);
    setStopping(false);
    setReply(null);
    runRef.current = null;
    if (!projectId) return;

    let stale = false;
    api
      .activeRun(projectId)
      .then(({ run_id }) => {
        // `stale` guards the switch that happened while this was in the air:
        // re-attaching then would watch the previous project's run.
        if (stale || !run_id) return;
        watch(run_id);
      })
      .catch(() => {
        /* nothing in flight, or the backend is down — the composer is idle,
           which is what it already shows */
      });
    return () => {
      stale = true;
    };
  }, [projectId]);

  useEffect(() => stopPolling, []);

  // -- reference images ---------------------------------------------------

  const refreshReferences = useCallback(() => {
    if (!projectId) {
      setReferences([]);
      return;
    }
    api
      .listReferences(projectId)
      .then(setReferences)
      .catch(() => setReferences([]));
  }, [projectId]);

  useEffect(() => {
    refreshReferences();
  }, [refreshReferences]);

  /**
   * Attach pictures — one, or everything that was picked or pasted at once.
   *
   * Several are allowed because several is often what the reference actually
   * is: the front of the thing and the side of it say between them what one
   * photograph cannot. They are read together as one specification (see
   * reference.py), so the order they go up in is the order they are shown in,
   * and they are uploaded one at a time rather than in parallel to keep it.
   */
  const attach = useCallback(
    async (files) => {
      const picked = [...(files || [])].filter(Boolean);
      if (!picked.length || !projectId || attaching) return;

      const images = picked.filter((f) => f.type?.startsWith("image/"));
      if (!images.length) {
        setAttachError(picked.length > 1
          ? "none of those files are images"
          : "that file is not an image");
        return;
      }

      // Counted here as well as on the backend, so a fifth picture comes back
      // as a sentence about the limit rather than as a failed upload.
      const room = MAX_REFERENCES - references.length;
      if (room <= 0) {
        setAttachError(`${MAX_REFERENCES} reference pictures is the limit — `
                       + "remove one to attach another");
        return;
      }

      const taking = images.slice(0, room);
      setAttaching(taking.length);
      setAttachError(taking.length < images.length
        ? `only ${taking.length} of those fit — a project takes `
          + `${MAX_REFERENCES} reference pictures`
        : null);
      try {
        for (const file of taking) {
          const added = await api.addReference(projectId, file);
          setReferences((r) => [...r, added]);
          setAttaching((n) => Math.max(0, n - 1));
        }
      } catch (e) {
        setAttachError(e.message);
      } finally {
        setAttaching(0);
      }
    },
    [projectId, attaching, references.length]
  );

  const removeReference = async (imageId) => {
    try {
      await api.deleteReference(projectId, imageId);
      setReferences((r) => r.filter((x) => x.image_id !== imageId));
    } catch (e) {
      setAttachError(e.message);
    }
  };

  // Ctrl+V anywhere in the box. A pasted screenshot arrives as a file on the
  // clipboard event with no name, which is why attach() takes Files/Blobs
  // rather than asking for names.
  const onPaste = useCallback(
    (event) => {
      const files = Array.from(event.clipboardData?.items || [])
        .filter((i) => i.type?.startsWith("image/"))
        .map((i) => i.getAsFile())
        .filter(Boolean);
      if (!files.length) return; // a normal text paste: leave it alone
      event.preventDefault();
      attach(files);
    },
    [attach]
  );

  // -- running ------------------------------------------------------------

  const send = async () => {
    const text = input.trim();
    if (!text || busy || !projectId) return;

    setInput("");
    setLive(blank());
    setReply(null);
    setBusy(true);

    try {
      const { run_id } = await api.chat(projectId, text, MAX_STEPS);
      watch(run_id);
    } catch (e) {
      setBusy(false);
      setReply({ kind: "error", text: e.message });
    }
  };

  /**
   * Follow a run to its end: poll it, render what it writes, and keep Stop on
   * screen for as long as it is going.
   *
   * Separate from `send` because a run is not only ever watched by the page
   * that started it. A reload, a switch to another project and back, or a
   * second tab all leave a build running on the backend with nothing watching
   * it — and, worse, with no Stop button, because Stop needs the run id and
   * that lived in this component's state. Re-attaching is the same loop, from
   * `since = 0`, so the events already recorded replay into the read-out and
   * the page catches up rather than starting blank.
   */
  const watch = (runId) => {
    stopPolling();
    runRef.current = runId;
    setBusy(true);
    // The Trace view watches this: the run is already recording, so naming
    // it here is what lets that view follow a build as it happens.
    onRun?.(runId);

    let since = 0;
    let inFlight = false;
    pollRef.current = setInterval(async () => {
      if (inFlight) return; // a slow poll must not stack up
      inFlight = true;
      try {
        const run = await api.run(runId, since);
        since = run.next_since ?? since;
        if (run.events?.length) {
          setLive((state) => advance(state, run.events));
          // Render every write as it happens rather than at the end of the
          // run: a model that is wrong, or half-finished, is still the thing
          // the user asked to see, and waiting shows them nothing at all.
          if (run.events.some((e) => e.type === "tool_end" && MODEL_CHANGED_BY.has(e.tool))) {
            onModelChanged?.();
          }
        }
        if (run.status === "running") return;
        stopPolling();
        setBusy(false);
        setStopping(false);
        runRef.current = null;
        finish(run);
      } catch (e) {
        stopPolling();
        setBusy(false);
        setStopping(false);
        runRef.current = null;
        setReply({ kind: "error", text: e.message });
      } finally {
        inFlight = false;
      }
    }, POLL_MS);
  };

  const finish = (run) => {
    setLive(blank());
    // Whatever the run did to itself, the file on disk is what it is — show it.
    if (run.validation) onValidation?.(run.validation);
    if (run.model_changed) onModelChanged?.();
    // Asked again at the end, so a run that started with none and attached one
    // itself does not leave the row empty.
    refreshReferences();

    if (run.status === "error") {
      setReply({ kind: "error", text: run.error });
    } else {
      setReply({
        kind: run.warning ? "warn" : "answer",
        text: run.answer || "(no reply)",
        note: run.warning,
      });
    }
  };

  const stopRun = async () => {
    if (!busy || !runRef.current || stopping) return;
    setStopping(true);
    try {
      await api.stopRun(runRef.current);
    } catch {
      /* the run may have finished between the click and the request */
    }
  };

  const newConversation = async () => {
    if (busy || !projectId) return;
    if (!window.confirm("Start a new conversation? The agent forgets what was said."))
      return;
    setReply(null);
    try {
      await api.resetChat(projectId);
    } catch (e) {
      setReply({ kind: "error", text: e.message });
    }
  };

  // What the agent is doing, most specific first: the tool actually in flight,
  // then which object is being built, then whatever phase the run last
  // announced. Only the last of those is ever a guess about the present, and
  // it is a phase the backend really did report rather than filler — the rule
  // this box was written with still holds. It says nothing about *thinking*,
  // because "thinking it through" stands where a real answer goes. It now says
  // plenty about doing, because the run was always saying it and this was not
  // listening.
  const note = busy
    ? `${live.tool
        ? TOOL_LABELS[live.tool] || live.tool
        : buildingLine(live.builds) || live.phase || "Working"}…`
    : "";
  // Only *whether* a run is in flight leaves this box. What it is doing is
  // said here, on the line under the text you type, and nowhere else: the
  // model view used to carry a second copy of the same sentence.
  useEffect(() => {
    onActivity?.(busy);
  }, [busy, onActivity]);

  // Mounted but out of the way: the run keeps polling, the box is not drawn.
  if (!visible || !projectId) return null;

  const hint = busy
    ? stopping
      ? "stopping…"
      : note
    : "⇧⏎ new line · ⌘V to paste a picture";

  return (
    <div className="composer-float" onPaste={onPaste}>
      {/* What the build is borrowing from used to be announced here — "building
          tree out of 561412-1 Christmas Tree…". It is in the trace, where a
          run's workings belong; over the model it was a set number and a
          filename in the way of the thing being built. */}
      {reply && (
        <div className={`composer-reply composer-reply--${reply.kind}`}>
          <span className="eyebrow">
            {reply.kind === "error" ? "FAILED" : "MAISTER"}
          </span>
          <p>{reply.text}</p>
          {reply.note && <p className="composer-reply-note">{reply.note}</p>}
          <button
            className="btn btn--icon composer-reply-close"
            onClick={() => setReply(null)}
            aria-label="Dismiss"
          >
            ×
          </button>
        </div>
      )}

      {/* Its own box, above the one you type in. The pictures belong to the
          project rather than to the message — they outlive whatever is being
          typed — and a composer that grows a row taller the moment one is
          attached moves the thing you were aiming at. */}
      {(references.length > 0 || attaching > 0 || attachError) && (
        <div className="composer-attached">
          {(references.length > 0 || attaching > 0) && (
            <div className="composer-refs">
              {references.map((image) => (
                <ReferenceChip
                  key={image.image_id}
                  projectId={projectId}
                  image={image}
                  size="sm"
                  onRemove={busy ? undefined : () => removeReference(image.image_id)}
                />
              ))}
              {/* one grey brick per picture still going up, so a row of four
                  dropped in at once looks like four from the first frame */}
              {Array.from({ length: attaching }, (_, i) => (
                <ReferenceChip key={`pending-${i}`} size="sm" pending />
              ))}
            </div>
          )}
          {attachError && <div className="composer-error">{attachError}</div>}
        </div>
      )}

      {/* `is-writing` is what takes it out of the translucent state over the
          model. Focus alone is not enough: the box has to stay solid while a
          half-written request is sitting in it and the mouse has gone off to
          spin the model round. */}
      <div className={`composer-box ${input ? "is-writing" : ""}`}>
        <div className="composer-row">
          <input
            ref={fileRef}
            type="file"
            accept="image/*"
            multiple
            hidden
            onChange={(e) => {
              const files = [...(e.target.files || [])];
              e.target.value = ""; // so the same file can be picked twice
              if (files.length) attach(files);
            }}
          />
          <button
            type="button"
            className="btn btn--attach"
            onClick={() => fileRef.current?.click()}
            disabled={busy || attaching > 0 || references.length >= MAX_REFERENCES}
            title={
              references.length >= MAX_REFERENCES
                ? `${MAX_REFERENCES} reference pictures is the limit — remove one to attach another`
                : `Attach reference pictures — up to ${MAX_REFERENCES}, or just paste them with ⌘/Ctrl+V`
            }
            aria-label="Attach reference pictures"
          >
            +
          </button>

          <textarea
            value={input}
            placeholder={
              busy ? "Waiting for the agent…" : "Describe what to build or change…"
            }
            disabled={busy}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
            rows={1}
          />

          {busy ? (
            <button className="btn btn--stop" onClick={stopRun} disabled={stopping}>
              {stopping ? "Stopping…" : "Stop"}
            </button>
          ) : (
            <button
              className="btn btn--primary"
              onClick={send}
              disabled={!input.trim()}
            >
              Send
            </button>
          )}
        </div>

        <div className="composer-foot">
          <span className={`composer-hint ${busy ? "is-live" : ""}`}>
            {busy && <span className="dot-pulse" aria-hidden="true" />}
            {hint}
          </span>
          <button
            className="btn btn--quiet composer-reset"
            onClick={newConversation}
            disabled={busy}
            title="Start a new conversation — the agent forgets what was said"
          >
            New chat
          </button>
        </div>
      </div>

    </div>
  );
}
