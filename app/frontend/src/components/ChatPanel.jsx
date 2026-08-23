import { useCallback, useEffect, useRef, useState } from "react";
import BrickStream from "./BrickStream";
import Markdown from "./Markdown";
import ReferenceChip from "./ReferenceChip";
import Studs from "./Studs";
import { api } from "../api";
import { inkOn, objectColourFor, shade } from "../brick";
import { formatMs, plural } from "../format";

// The model streams, so this is the refresh rate of the reply appearing on
// screen, not just a status check — it wants to be quick.
const POLL_MS = 280;
// Most tools return in tens of milliseconds, so start and end land in the same
// poll. Keep a row visibly "running" this long so the call is perceptible; the
// duration reported on the row is still the real one.
const HOLD_MS = 550;

// No step budget. A run goes until it is finished or until you stop it — see
// DEFAULT_MAX_STEPS in config.py, and the stop button, which is now the only
// thing that ends a run early.
const MAX_STEPS = 0;

// What the agent is doing while a tool call is in flight.
// STALE, and this file is not mounted by anything (see app/README.md).
// The live status labels are in Composer.jsx — that map covers every
// tool and every run phase, and this one does not. Do not copy from it.
const TOOL_LABELS = {
  plan_construction: "Drawing up the construction plan",
  search_parts: "Searching the parts catalogue",
  search_reference: "Looking through real sets and past work",
  get_part_details: "Reading part geometry",
  get_set_details: "Reading a real set",
  read_model: "Reading LDraw source",
  edit_model: "Writing the model file",
  validate_model: "Checking the grid and looking at it",
  ask_about_image: "Looking at your reference picture",
  assemble_model: "Composing the scene",
};

// Tool calls that change the model on disk, and so should put the new model on
// screen the moment they land rather than at the end of the run.
// validate_model is here because it repairs as it checks: overlaps that are
// pure arithmetic are slid back onto the grid before the report comes back, so
// the file on disk can differ from what is on screen once it returns.
const MODEL_CHANGED_BY = new Set([
  "edit_model", "assemble_model", "validate_model",
]);

const PROMPTS = [
  "Build a 4×4 tower six bricks tall with a red top",
  "Make me a little house with a door",
  "Turn the roof yellow",
];

// --------------------------------------------------------------------------
// Trace
// --------------------------------------------------------------------------

// Turn the raw event stream into rows: one row per tool call, with its
// tool_end folded back into the row that started it. `seen` (call_id -> the
// time this browser first saw the call) drives the HOLD_MS grace period; pass
// nothing for a finished run, where every row is shown in its final state.
function foldEvents(events, seen) {
  const items = [];
  const byCall = new Map();
  let step = 0;
  let held = false;
  let planning = false;

  for (const e of events) {
    if (e.type === "step") {
      step = e.step;
    } else if (e.type === "planning") {
      planning = true;
    } else if (e.type === "plan" && e.text) {
      planning = false;
      items.push({ kind: "plan", key: `p${e.i}`, text: e.text });
    } else if (e.type === "text" && e.text) {
      items.push({ kind: "text", key: `x${e.i}`, step: e.step, text: e.text,
                   lane: e.subconstruction || e.phase || null });
    } else if (e.type === "tool_start") {
      if (seen && !seen.has(e.call_id)) seen.set(e.call_id, Date.now());
      const item = {
        kind: "tool",
        key: `c${e.call_id ?? e.i}`,
        step: e.step,
        tool: e.tool,
        detail: e.detail,
        // which subconstruction ran it. Objects are built at the same time
        // now, so without this the rows of three builders arrive shuffled
        // together with nothing saying which is which.
        lane: e.subconstruction || e.phase || null,
        status: "running",
      };
      byCall.set(e.call_id, item);
      items.push(item);
    } else if (e.type === "tool_end") {
      const item = byCall.get(e.call_id);
      const done = { status: e.ok ? "ok" : "fail", summary: e.summary, ms: e.ms };
      if (!item) {
        items.push({ kind: "tool", key: `e${e.i}`, step: e.step, tool: e.tool,
                     lane: e.subconstruction || e.phase || null, ...done });
      } else if (seen && Date.now() - seen.get(e.call_id) < HOLD_MS) {
        held = true; // finished, but keep it on screen a beat longer
      } else {
        Object.assign(item, done);
      }
    } else if (e.type === "reference_sets" && e.summary) {
      // Real sets found for an object before it is built. Shown as it happens,
      // because "which sets is it building out of" is the question the trace
      // could only answer afterwards.
      items.push({ kind: "sets", key: `s${e.i}`, text: e.summary,
                   lane: e.name || null });
    } else if (e.type === "recalled" && e.summary) {
      // The same, for what this builder made earlier. Shown in the same lane
      // and the same way: "what is it working from" is one question, and the
      // answer is now two things.
      items.push({ kind: "sets", key: `r${e.i}`, text: `from its own work: ${e.summary}`,
                   lane: e.name || null });
    } else if (e.type === "renamed" && e.text) {
      items.push({ kind: "renamed", key: `n${e.i}`, text: e.text });
    } else if (e.type === "error") {
      items.push({ kind: "error", key: `err${e.i}`, text: e.text });
    }
  }

  const active = items.find((i) => i.kind === "tool" && i.status === "running");
  return { items, step, active, held, planning };
}

// Every tool call is a brick, and the colour of the brick is what happened to
// it: yellow while it runs, green when it worked, red when it did not, blue
// while the model is still writing it. The mark repeats it in a glyph, because
// a status told only in colour is a status some people cannot read.
//
// Which object the call belongs to rides on a smaller brick stacked on top of
// that one — objects are built at the same time now, so the rows of three
// builders arrive shuffled together and each needs to say whose it is.
const STATUS = {
  running: { mark: "▸", label: "running" },
  ok: { mark: "✓", label: "done" },
  fail: { mark: "✕", label: "failed" },
};

/** The moulded-plastic variables for one object's brick, from its name. */
function laneVars(name) {
  const colour = objectColourFor(name);
  return {
    "--lane": colour,
    "--lane-bevel": shade(colour, 0.58),
    "--lane-lip": shade(colour, 1.08),
    "--lane-ink": inkOn(colour),
  };
}

function ToolRow({ item }) {
  const running = item.status === "running";
  const state = STATUS[item.status] || STATUS.running;
  return (
    <div className={`tool tool--${item.status} ${item.lane ? "tool--named" : ""}`}>
      <Studs />
      {/* whose object this is, as a small brick standing on this one */}
      {item.lane && (
        <span className="tool-lane" style={laneVars(item.lane)}>
          <i aria-hidden="true" />
          {item.lane}
        </span>
      )}
      <div className="tool-body">
        <div className="tool-head">
          <span className="tool-name">{item.tool}</span>
          {item.detail && <span className="tool-detail">{item.detail}</span>}
          {!running && <span className="tool-ms">{formatMs(item.ms)}</span>}
          <span className="tool-mark" title={state.label} aria-label={state.label}>
            {state.mark}
          </span>
        </div>
        <div className="tool-line">
          {running ? `${TOOL_LABELS[item.tool] || "Running"}…` : item.summary}
        </div>
      </div>
    </div>
  );
}

function Trace({ items, animate, onAdvance }) {
  if (!items.length) return null;
  return (
    <div className="trace">
      {items.map((it) =>
        it.kind === "tool" ? (
          <ToolRow key={it.key} item={it} />
        ) : it.kind === "plan" ? (
          <details key={it.key} className="plan-box" open>
            <summary>Construction plan</summary>
            <Markdown text={it.text} />
          </details>
        ) : it.kind === "sets" ? (
          <div key={it.key} className="trace-sets">
            {it.lane && (
              <span className="trace-lane" style={laneVars(it.lane)}>
                {it.lane}
              </span>
            )}
            building out of <b>{it.text}</b>
          </div>
        ) : it.kind === "renamed" ? (
          <div key={it.key} className="trace-renamed">
            named this project <b>{it.text}</b>
          </div>
        ) : it.kind === "error" ? (
          <div key={it.key} className="trace-error">
            {it.text}
          </div>
        ) : (
          <div key={it.key} className="trace-text">
            {it.lane && (
              <span className="trace-lane" style={laneVars(it.lane)}>
                {it.lane}
              </span>
            )}
            {animate ? (
              <BrickStream text={it.text} animate onAdvance={onAdvance} />
            ) : (
              it.text
            )}
          </div>
        )
      )}
    </div>
  );
}

/**
 * The agent's working-out, kept with the reply it produced: what it reasoned
 * between calls and every tool it ran. Open by default — this is the part that
 * explains why the model came out the way it did.
 */
function TraceBox({ trace }) {
  const tools = trace.filter((t) => t.kind === "tool").length;
  const thoughts = trace.filter((t) => t.kind === "text").length;

  return (
    <details className="trace-box" open>
      <summary>
        {thoughts ? "Thinking" : "Trace"}
        {tools ? ` · ${plural(tools, "tool call")}` : ""}
      </summary>
      <Trace items={trace} animate={false} />
    </details>
  );
}

/**
 * A call the model is still writing. It cannot run until the arguments are
 * complete, so this is the only window in which it would otherwise be
 * invisible — the JSON appears character by character as it is generated.
 */
function ComposingRow({ call }) {
  return (
    <div className="tool tool--composing">
      <Studs />
      <div className="tool-body">
        <div className="tool-head">
          <span className="tool-name">{call.tool || "…"}</span>
          <span className="tool-detail">writing the call…</span>
        </div>
        {call.arguments && (
          <div className="tool-args">
            {call.arguments}
            <span className="tool-caret" />
          </div>
        )}
      </div>
    </div>
  );
}

function Dots() {
  return (
    <span className="dots" aria-hidden="true">
      <i />
      <i />
      <i />
    </span>
  );
}

// --------------------------------------------------------------------------
// Conversation storage
// --------------------------------------------------------------------------

// Where conversations used to live: one blob in this browser holding every
// project at once. Two windows each read it at startup and each wrote the whole
// thing back, so the second to type erased what the first had said; and the
// blob carried a tool row per event, so a few real builds took it past the
// storage quota, after which the write failed silently and nothing survived a
// reload at all. The server keeps the conversation now — this key is read once
// more, to carry anything still in it across, and then dropped.
const STORE_KEY = "maister.threads.v1";

// Shared so an empty conversation keeps a stable identity between renders —
// `|| []` would mint a new array each time and retrigger every effect.
const NO_MESSAGES = [];

/** Whatever the old browser store still holds for one project. */
function strandedThread(projectId) {
  try {
    const all = JSON.parse(localStorage.getItem(STORE_KEY)) || {};
    const mine = all[projectId];
    return Array.isArray(mine) && mine.length ? mine : null;
  } catch {
    return null;
  }
}

/** Forget one project's old copy, once it is safely on the server. */
function dropStrandedThread(projectId) {
  try {
    const all = JSON.parse(localStorage.getItem(STORE_KEY)) || {};
    delete all[projectId];
    if (Object.keys(all).length) localStorage.setItem(STORE_KEY, JSON.stringify(all));
    else localStorage.removeItem(STORE_KEY);
  } catch {
    /* nothing to clean up */
  }
}

/**
 * A stored message as the log renders it. The tool rows are folded from the
 * events the server kept, which is the same fold a live run goes through — so
 * a reply read back tomorrow looks like the one that arrived today.
 */
function restore(message) {
  const items = message.events?.length ? foldEvents(message.events).items : [];
  const text = message.text || "";
  return {
    ...message,
    trace: items.filter((it) => !(it.kind === "text" && it.text === text)),
  };
}

// --------------------------------------------------------------------------

export default function ChatPanel({
  projectId,
  projectName,
  onModelChanged,
  onValidation,
  onActivity,
  onRun,
}) {
  // The conversation lives on the server, beside the model and the traces. It
  // survives switching away, reloading, a second window, and the backend
  // restarting. Only "Clear" ends one.
  const [messages, setMessages] = useState(NO_MESSAGES);

  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [events, setEvents] = useState([]);
  const [partial, setPartial] = useState(""); // the reply as it streams in
  const [pending, setPending] = useState([]); // tool calls still being written

  const eventsRef = useRef([]); // mirrors `events`, readable outside render
  const seenRef = useRef(new Map()); // call_id -> when this browser first saw it
  const logRef = useRef(null);
  const bottomRef = useRef(null);
  const stickRef = useRef(true); // false once the user scrolls up to read
  const pollRef = useRef(null);
  const inputRef = useRef(null);
  const runRef = useRef(null); // the run in flight, so Stop knows what to stop
  const [stopping, setStopping] = useState(false);
  // Reference images attached to this project. They outlive the message that
  // carried them, so they are loaded from the backend rather than kept here.
  const [references, setReferences] = useState([]);
  const [attaching, setAttaching] = useState(false);
  const [attachError, setAttachError] = useState(null);
  const fileRef = useRef(null);

  const setLiveEvents = (next) => {
    eventsRef.current = next;
    if (!next.length) seenRef.current = new Map();
    setEvents(next);
  };

  // -- the conversation ---------------------------------------------------

  /**
   * Read the transcript back from the server.
   *
   * `force` replaces whatever is on screen — that is opening a project. Without
   * it the server list is only adopted when it is at least as complete as the
   * one already shown, because a reply is written a moment after the run is
   * marked finished, and a poll that lands in that gap would otherwise wipe the
   * answer the user is reading.
   */
  const refreshMessages = useCallback(
    async (id, { force = false } = {}) => {
      if (!id) return;
      try {
        const { messages: stored } = await api.messages(id);
        let restored = (stored || []).map(restore);

        // One conversation may still be in this browser from before the server
        // kept them. Carry it over, once, then let go of it.
        if (!restored.length) {
          const stranded = strandedThread(id);
          if (stranded) {
            try {
              const { messages: saved } = await api.putMessages(id, stranded);
              restored = (saved || []).map(restore);
              dropStrandedThread(id);
            } catch {
              restored = stranded; // show it even if it could not be saved
            }
          }
        }

        setMessages((local) =>
          force || restored.length >= local.length ? restored : local
        );
      } catch {
        /* the backend is down; whatever is on screen stays on screen */
      }
    },
    []
  );

  useEffect(() => {
    setMessages(NO_MESSAGES);
    refreshMessages(projectId, { force: true });
  }, [projectId, refreshMessages]);

  // A second window is a second reader of the same file. Catching up when this
  // one is looked at again is what keeps the two from showing different
  // conversations.
  useEffect(() => {
    const catchUp = () => {
      if (document.visibilityState === "visible" && !busy) refreshMessages(projectId);
    };
    document.addEventListener("visibilitychange", catchUp);
    window.addEventListener("focus", catchUp);
    return () => {
      document.removeEventListener("visibilitychange", catchUp);
      window.removeEventListener("focus", catchUp);
    };
  }, [projectId, busy, refreshMessages]);

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

  const attach = useCallback(
    async (file) => {
      if (!file || !projectId || attaching) return;
      if (!file.type?.startsWith("image/")) {
        setAttachError("that file is not an image");
        return;
      }
      setAttaching(true);
      setAttachError(null);
      try {
        const added = await api.addReference(projectId, file);
        setReferences((r) => [...r, added]);
      } catch (e) {
        setAttachError(e.message);
      } finally {
        setAttaching(false);
      }
    },
    [projectId, attaching]
  );

  const removeReference = async (imageId) => {
    try {
      await api.deleteReference(projectId, imageId);
      setReferences((r) => r.filter((x) => x.image_id !== imageId));
    } catch (e) {
      setAttachError(e.message);
    }
  };

  // Ctrl+V anywhere in the chat. A pasted screenshot arrives as a file on the
  // clipboard event with no name, which is why attach() takes a File/Blob
  // rather than asking for one.
  const onPaste = useCallback(
    (event) => {
      const items = Array.from(event.clipboardData?.items || []);
      const image = items.find((i) => i.type?.startsWith("image/"));
      if (!image) return; // a normal text paste: leave it alone
      const file = image.getAsFile();
      if (!file) return;
      event.preventDefault();
      attach(file);
    },
    [attach]
  );

  const stopPolling = () => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = null;
  };

  // Switching project parks the current conversation rather than ending it.
  useEffect(() => {
    stopPolling();
    setLiveEvents([]);
    setPartial("");
    setPending([]);
    setBusy(false);
  }, [projectId]);

  const stopRun = async () => {
    if (!busy || !runRef.current || stopping) return;
    setStopping(true);
    // Nothing more will be written to this turn, so stop showing it being
    // written. The run itself is settled by the backend on this request; the
    // next poll picks up its final state and closes the message out.
    setPartial("");
    setPending([]);
    try {
      await api.stopRun(runRef.current);
    } catch {
      /* the run may have finished between the click and the request */
    }
  };

  const newConversation = async () => {
    if (busy) return;
    if (messages.length && !window.confirm("Start a new conversation? This clears the messages here.")) {
      return;
    }
    setMessages([]);
    setLiveEvents([]);
    setPartial("");
    setPending([]);
    try {
      // Clears the record on disk as well as the agent's memory of it — the
      // two are the same conversation, so they end together.
      await api.resetChat(projectId);
    } catch {
      /* the backend is down; the screen is clear, the file is not */
      refreshMessages(projectId, { force: true });
    }
  };

  useEffect(() => stopPolling, []);

  const scrollDown = useCallback((smooth = false) => {
    if (!stickRef.current) return;
    const el = logRef.current;
    if (!el) return;
    if (smooth) bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    else el.scrollTop = el.scrollHeight;
  }, []);

  const onLogScroll = () => {
    const el = logRef.current;
    if (el) stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  };

  useEffect(() => {
    scrollDown(true);
  }, [messages, events, partial, scrollDown]);

  const finish = (run) => {
    const { items } = foldEvents(eventsRef.current);
    setLiveEvents([]);

    // Whatever the run did to itself, the file on disk is what it is — show it.
    if (run.validation) onValidation?.(run.validation);
    if (run.model_changed) onModelChanged?.();

    // Shown straight away from what this window watched happen, then reconciled
    // below against what the server wrote — which is what every other window,
    // and this one tomorrow, will read.
    if (run.status === "error") {
      setMessages((m) => [...m, { role: "error", text: run.error, trace: items }]);
    } else {
      const answer = run.answer || "(no reply)";
      setMessages((m) => [
        ...m,
        {
          role: "assistant",
          text: answer,
          warning: run.warning,
          steps: run.steps,
          // the closing message is not also a trace row
          trace: items.filter((it) => !(it.kind === "text" && it.text === answer)),
          animate: true,
        },
      ]);
    }

    // The reply is written as the run unwinds, a moment after it reports
    // itself finished, so this asks once the dust has settled rather than now.
    setTimeout(() => refreshMessages(projectId), 1200);
  };

  const send = async () => {
    const text = input.trim();
    if (!text || busy || !projectId) return;

    setInput("");
    setMessages((m) => [...m, { role: "user", text, images: references }]);
    setLiveEvents([]);
    setPartial("");
    setPending([]);
    setBusy(true);
    stickRef.current = true;

    let since = 0;
    let inFlight = false;

    try {
      const { run_id } = await api.chat(projectId, text, MAX_STEPS);
      runRef.current = run_id;
      // The Trace view watches this: the run is already recording, so naming
      // it here is what lets that view follow a build as it happens.
      onRun?.(run_id);
      pollRef.current = setInterval(async () => {
        if (inFlight) return; // a slow poll must not stack up
        inFlight = true;
        try {
          const run = await api.run(run_id, since);
          since = run.next_since ?? since;
          if (run.events?.length) {
            setLiveEvents([...eventsRef.current, ...run.events]);
            // Render every write as it happens rather than at the end of the
            // run: a model that is wrong, or half-finished, is still the thing
            // the user asked to see, and waiting shows them nothing at all.
            if (run.events.some((e) => e.type === "tool_end" && MODEL_CHANGED_BY.has(e.tool))) {
              onModelChanged?.();
            }
          }
          setPartial(run.partial?.text || "");
          setPending(Object.values(run.partial?.tools || {}));
          if (run.status === "running") return;
          stopPolling();
          setBusy(false);
          setStopping(false);
          runRef.current = null;
          setPartial("");
          setPending([]);
          finish(run);
        } catch (e) {
          stopPolling();
          setBusy(false);
          setMessages((m) => [...m, { role: "error", text: e.message }]);
        } finally {
          inFlight = false;
        }
      }, POLL_MS);
    } catch (e) {
      setBusy(false);
      setMessages((m) => [...m, { role: "error", text: e.message }]);
    }
  };

  const live = foldEvents(events, seenRef.current);

  // A row inside its grace period needs one more render to be released; polling
  // usually provides it, this covers the gap when it does not.
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!live.held) return;
    const id = setTimeout(() => setTick((n) => n + 1), 120);
    return () => clearTimeout(id);
  });

  // Keep the workbench read-out in step with what the agent is doing.
  const note = live.active
    ? `${TOOL_LABELS[live.active.tool] || live.active.tool}…`
    : "";
  useEffect(() => {
    onActivity?.(busy ? { note } : null);
  }, [busy, note, onActivity]);

  const suggest = (text) => {
    setInput(text);
    inputRef.current?.focus();
  };

  return (
    <aside className="chat">
      <div className="chat-header">
        <span className="chat-flag" aria-hidden="true" />
        <span className="eyebrow">CHAT</span>
        <span className={`chat-tool ${live.active ? "chat-tool--live" : ""}`}>
          {live.active && <span className="dot-pulse" aria-hidden="true" />}
          <span>{live.active ? live.active.tool : projectName ? `${projectName}.ldr` : ""}</span>
        </span>
        <button
          className="btn chat-clear"
          onClick={newConversation}
          disabled={busy || !projectId || messages.length === 0}
          title="Clear this conversation and the agent's memory of it"
        >
          Clear
        </button>
      </div>

      <div className="chat-log" ref={logRef} onScroll={onLogScroll}>
        {messages.length === 0 && !busy && (
          <div className="chat-hint">
            <p>
              {projectId
                ? "Tell me what to build, or ask for a change to what's on the grid."
                : "Open or start a project first, then tell me what to build."}
            </p>
            {PROMPTS.map((p) => (
              <button key={p} type="button" onClick={() => suggest(p)} disabled={!projectId}>
                “{p}”
              </button>
            ))}
          </div>
        )}

        {messages.map((m, i) =>
          m.role === "user" ? (
            <div key={i} className="msg msg--user">
              {m.images?.length > 0 && (
                <div className="msg-refs">
                  {m.images.map((image) => (
                    <ReferenceChip
                      key={image.image_id}
                      projectId={projectId}
                      image={image}
                      size="md"
                    />
                  ))}
                </div>
              )}
              <div className="msg-bubble">{m.text}</div>
            </div>
          ) : (
            <div key={i} className={`msg msg--${m.role}`}>
              <div className="msg-who">
                {m.role === "error" ? "ERROR" : "MAISTER"}
              </div>

              {m.trace?.length > 0 && <TraceBox trace={m.trace} />}

              {m.role === "assistant" ? (
                <BrickStream text={m.text} animate={m.animate} onAdvance={scrollDown} />
              ) : (
                <div className="msg-body">{m.text}</div>
              )}
              {m.warning && <div className="msg-warn">{m.warning}</div>}
            </div>
          )
        )}

        {busy && (
          <div className="msg msg--assistant">
            <div className="msg-who">
              MAISTER
              <span className="msg-note">
                {live.active ? "snapping bricks…" : "working"}
              </span>
            </div>
            <Trace items={live.items} animate={false} />

            {/* what the model is writing right now, brick by brick */}
            {partial && <BrickStream text={partial} live />}

            {pending.map((call, i) => (
              <ComposingRow key={i} call={call} />
            ))}

            {!live.active && !partial && pending.length === 0 && (
              <div className="thinking">
                {live.planning ? "Planning the build" : "Thinking"}
                <Dots />
              </div>
            )}
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <div className="composer" onPaste={onPaste}>
        {(references.length > 0 || attaching) && (
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
            {attaching && <ReferenceChip size="sm" pending />}
            <p className="composer-refs-note">
              Attached to this project — the agent builds to match it, and it
              stays in force for later changes.
            </p>
          </div>
        )}
        {attachError && <div className="composer-error">{attachError}</div>}

        <textarea
          ref={inputRef}
          value={input}
          placeholder={
            busy
              ? "Waiting for the agent…"
              : projectId
                ? "Describe what to build or change…"
                : "Open a project to start building…"
          }
          disabled={busy || !projectId}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
          rows={3}
        />
        <div className="composer-row">
          <input
            ref={fileRef}
            type="file"
            accept="image/*"
            hidden
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = ""; // so the same file can be picked twice
              if (file) attach(file);
            }}
          />
          <button
            type="button"
            className="btn btn--attach"
            onClick={() => fileRef.current?.click()}
            disabled={busy || !projectId || attaching}
            title="Attach a reference image — or just paste one with ⌘/Ctrl+V"
            aria-label="Attach a reference image"
          >
            +
          </button>
          <span className="composer-hint">
            {busy ? (stopping ? "stopping…" : "building…") : "⇧⏎ new line · ⌘V image"}
          </span>
          {busy ? (
            <button className="btn btn--stop" onClick={stopRun} disabled={stopping}>
              {stopping ? "Stopping…" : "Stop"}
            </button>
          ) : (
            <button
              className="btn btn--primary"
              onClick={send}
              disabled={!input.trim() || !projectId}
            >
              Send
            </button>
          )}
        </div>
      </div>
    </aside>
  );
}
