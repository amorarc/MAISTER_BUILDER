import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { formatMs, plural, relativeTime } from "../format";

/**
 * What the agent did, as a graph you can walk.
 *
 * A run is not a list of messages, it is a tree: the request at the root, the
 * phases under it, a node per *iteration* inside each phase — every turn of
 * the loop up to the moment the model was checked — and a node per tool call
 * inside each iteration. Laid out left to right, that shape is the run — one
 * glance says whether it planned before it built, which subconstruction ate
 * the attempts, and where it started going in circles.
 *
 * Every node carries what went into it and what came out, whole. Clicking one
 * opens exactly that, which is the question this view exists to answer: not
 * "what happened" — the chat panel says that — but "what was it looking at
 * when it decided that".
 */

const COL = 250;   // horizontal distance between depths
const ROW = 46;    // vertical distance between leaves
const W = 196;     // node width
const H = 34;      // node height

// How often a live run's graph is re-read. It grows on disk as the run goes,
// so this is a running build watched from the outside.
const POLL_MS = 1800;

const KINDS = {
  run: { glyph: "◆", name: "the request" },
  workbench: { glyph: "⌂", name: "what was already built" },
  planning: { glyph: "✎", name: "planning" },
  brief: { glyph: "✧", name: "design brief" },
  sets: { glyph: "▦", name: "reference sets" },
  decompose: { glyph: "⑂", name: "decomposition" },
  reference: { glyph: "▣", name: "reference picture" },
  subbuild: { glyph: "▤", name: "subconstruction" },
  assembly: { glyph: "⧉", name: "assembly" },
  critique: { glyph: "◉", name: "critique" },
  step: { glyph: "›", name: "one iteration of the loop" },
  tool: { glyph: "▸", name: "tool call" },
  error: { glyph: "✕", name: "error" },
  // What has to be true before the build may end, and the check that puts it
  // to the model at the end of every iteration. The check is the node that
  // answers "why is this run still going" — it is the only thing that ends one.
  requirements: { glyph: "☑", name: "requirements to finish" },
  requirements_check: { glyph: "⊘", name: "requirements check" },
};

/* -------------------------------------------------------------- layout -- */

/**
 * A tidy left-to-right tree: depth decides the column, and a parent sits
 * level with the middle of its children. Leaves get consecutive rows, so
 * nothing overlaps however lopsided the run was.
 */
function layout(graph, collapsed) {
  if (!graph) return null;

  const byId = new Map(graph.nodes.map((n) => [n.id, n]));
  const kids = new Map();
  const parent = new Map();
  for (const edge of graph.edges) {
    if (edge.kind !== "in") continue;
    if (!kids.has(edge.from)) kids.set(edge.from, []);
    kids.get(edge.from).push(edge.to);
    parent.set(edge.to, edge.from);
  }

  const placed = new Map();
  let row = 0;

  const visit = (id, depth) => {
    const node = byId.get(id);
    if (!node) return null;
    const children = collapsed.has(id) ? [] : kids.get(id) || [];
    let y;
    if (!children.length) {
      y = row * ROW;
      row += 1;
    } else {
      const ys = children.map((c) => visit(c, depth + 1)).filter((v) => v != null);
      y = ys.length ? (Math.min(...ys) + Math.max(...ys)) / 2 : (row++) * ROW;
    }
    placed.set(id, { node, x: depth * COL, y, depth, hidden: 0 });
    return y;
  };

  const roots = graph.nodes.filter((n) => !parent.has(n.id));
  roots.forEach((r) => {
    visit(r.id, 0);
    row += 0.5; // a gutter between disconnected roots, if a trace ever has two
  });

  // How much a collapsed node is hiding, so the badge can say so.
  const countBelow = (id) => {
    const children = kids.get(id) || [];
    return children.reduce((sum, c) => sum + 1 + countBelow(c), 0);
  };
  for (const id of collapsed) {
    const at = placed.get(id);
    if (at) at.hidden = countBelow(id);
  }
  for (const at of placed.values()) {
    at.children = (kids.get(at.node.id) || []).length;
  }

  const links = [];
  for (const edge of graph.edges) {
    const from = placed.get(edge.from);
    const to = placed.get(edge.to);
    if (!from || !to) continue; // one end is inside something collapsed
    links.push({ ...edge, from, to });
  }

  const nodes = [...placed.values()];
  const maxX = Math.max(0, ...nodes.map((n) => n.x)) + W;
  const maxY = Math.max(0, ...nodes.map((n) => n.y)) + H;
  return { nodes, links, byId, parent, kids, width: maxX, height: maxY };
}

/**
 * Iterations arrive closed.
 *
 * A node per tool call is the detail; the shape of a run is its phases and the
 * iterations under them, which a hundred tool nodes fanned out to the right
 * bury completely. So an iteration shows as one node carrying a count of what
 * is inside it, and clicking it opens that up.
 *
 * `seen` is why this takes a mutable set: the graph is re-read every couple of
 * seconds while a run is going, and closing "every iteration" on each poll
 * would shut the one the user just opened, over and over. Each iteration is
 * closed once, on the poll it first appears in, and after that it is theirs.
 */
function newIterations(graph, seen) {
  const parents = new Set(
    graph.edges.filter((e) => e.kind === "in").map((e) => e.from));
  return graph.nodes
    .filter((n) => n.kind === "step" && parents.has(n.id) && !seen.has(n.id))
    .map((n) => n.id);
}

/** Parent → grandparent → … , for finding the prompt a turn was working from. */
function ancestors(id, parent, byId) {
  const chain = [];
  let at = parent.get(id);
  while (at) {
    const node = byId.get(at);
    if (node) chain.push(node);
    at = parent.get(at);
  }
  return chain;
}

/* --------------------------------------------------------------- pieces -- */

function Payload({ value, empty = "nothing recorded" }) {
  const [all, setAll] = useState(false);

  if (value === null || value === undefined || value === "")
    return <p className="trace-none">{empty}</p>;

  const text =
    typeof value === "string" ? value : JSON.stringify(value, null, 2);
  const long = text.length > 4000;
  const shown = long && !all ? text.slice(0, 4000) : text;

  return (
    <>
      <pre className="trace-payload">{shown}</pre>
      {long && (
        <button className="btn btn--quiet trace-more" onClick={() => setAll(!all)}>
          {all
            ? "Show less"
            : `Show all ${text.length.toLocaleString()} characters`}
        </button>
      )}
    </>
  );
}

/** A block that starts closed, for the 30 KB of standing prompt. */
function Fold({ title, note, children, open: initial = false }) {
  const [open, setOpen] = useState(initial);
  return (
    <div className={`trace-fold ${open ? "is-open" : ""}`}>
      <button className="trace-fold-head" onClick={() => setOpen(!open)}>
        <span className="trace-caret">{open ? "▾" : "▸"}</span>
        {title}
        {note && <em>{note}</em>}
      </button>
      {open && <div className="trace-fold-body">{children}</div>}
    </div>
  );
}

function Section({ label, children }) {
  return (
    <section className="trace-section">
      <div className="eyebrow">{label}</div>
      {children}
    </section>
  );
}

/**
 * The pictures this node produced or was shown.
 *
 * Copies, not the live renders: out/renders is overwritten by the next write,
 * so pointing at it would show today's model beside a decision made three
 * builds ago. These are archived with the run, which is what makes them
 * evidence. Click one for the full size.
 */
/**
 * The acceptance checklist, as a checklist.
 *
 * Serves both nodes. On `requirements` there are no answers yet, so every row
 * is shown plain — it is the list the build is about to be judged against. On
 * `requirements_check` each row carries the answer and the evidence for it,
 * and the unmet ones come first because they are the reason the run is still
 * going.
 */
function Requirements({ node }) {
  const out = node.output || {};
  const unmet = out.unmet || [];
  const met = out.met || [];
  const plain = out.requirements || [];
  const answered = node.kind === "requirements_check";
  const rows = answered ? [...unmet, ...met] : plain;
  const rejected = out.rejected_as_unmeasurable || [];

  return (
    <>
      {answered && out.summary && (
        <p className="trace-detail-note">{out.summary}</p>
      )}
      <Section
        label={
          answered
            ? `Checked — ${met.length} met, ${unmet.length} not`
            : `The checklist — ${plain.length} to satisfy`
        }
      >
        {rows.length ? (
          <ul className="req-list">
            {rows.map((r) => (
              <li
                key={r.id}
                className={
                  answered ? (r.met ? "req--met" : "req--unmet") : undefined
                }
              >
                <span className="req-mark">
                  {answered ? (r.met ? "✓" : "✕") : "·"}
                </span>
                <div>
                  <span className="req-id">{r.id}</span> {r.text}
                  <em className="req-how">{r.check}</em>
                  {r.evidence && <div className="req-why">{r.evidence}</div>}
                  {!answered && r.why && <div className="req-why">{r.why}</div>}
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <p className="trace-none">no requirements were written</p>
        )}
      </Section>

      {/* Said out loud rather than dropped: a list that quietly lost three of
          its criteria looks like a list that was never given them. */}
      {[
        ["Dropped as unmeasurable", rejected],
        ["Dropped as not asked for", out.rejected_as_not_asked_for || []],
      ].map(([label, list]) =>
        list.length ? (
          <Section key={label} label={`${label} — ${list.length}`}>
            <ul className="req-list">
              {list.map((text, i) => (
                <li key={i} className="req--dropped">
                  <span className="req-mark">–</span>
                  <div>{text}</div>
                </li>
              ))}
            </ul>
          </Section>
        ) : null
      )}

      <Fold title="Raw" note="the payload as recorded">
        <Payload value={node.output} empty="nothing came back" />
      </Fold>
    </>
  );
}

function Shots({ projectId, images }) {
  if (!images?.length) return null;
  return (
    <Section label={`What it saw · ${plural(images.length, "picture")}`}>
      <div className="trace-shots">
        {images.map((image, i) => {
          const url = api.traceImageUrl(projectId, image.src);
          const caption = image.view || image.label || image.kind || "picture";
          return (
            <a
              key={`${image.src}-${i}`}
              className={`trace-shot trace-shot--${slug(image.kind)}`}
              href={url}
              target="_blank"
              rel="noreferrer"
              title={`${caption} — open full size`}
            >
              <img src={url} alt={caption} loading="lazy" />
              <em>{caption}</em>
            </a>
          );
        })}
      </div>
    </Section>
  );
}

/**
 * Everything about one node. Inputs above outputs, always in that order —
 * the point of the panel is reading down from what it was given to what it
 * decided.
 */
function NodeDetail({ node, view, onGo, projectId }) {
  if (!node) {
    return (
      <div className="trace-detail trace-detail--empty">
        <p>Pick a node.</p>
        <p className="trace-none">
          Every one of them holds what it was given and what it gave back.
        </p>
      </div>
    );
  }

  const kind = KINDS[node.kind] || { glyph: "•", name: node.kind };
  const context =
    node.context ||
    ancestors(node.id, view.parent, view.byId).find((a) => a.context)?.context;
  const stepInput = node.kind === "step" ? node.input || {} : null;

  return (
    <div className="trace-detail">
      <header className="trace-detail-head">
        <span className={`trace-glyph trace-glyph--${node.kind}`}>{kind.glyph}</span>
        <div className="trace-detail-title">
          <h3>{node.label}</h3>
          <div className="trace-detail-sub">
            {kind.name}
            {node.lane && node.lane !== "main" ? ` · ${node.lane}` : ""}
            {/* how many turns of the loop went into this attempt */}
            {node.turns > 1 ? ` · ${plural(node.turns, "turn")}` : ""}
            {node.ms != null ? ` · ${formatMs(node.ms)}` : ""}
          </div>
        </div>
        {node.status && (
          <span className={`trace-status trace-status--${slug(node.status)}`}>
            {node.status}
          </span>
        )}
      </header>

      {node.title && node.title !== node.label && (
        <p className="trace-detail-note">{node.title}</p>
      )}

      {/* An iteration is fed by the one before it, so its input is a set of
          links rather than a payload of its own — following them is the
          point. */}
      {stepInput?.kind === "tool_results" ? (
        <Section label="In — what came back from the last iteration">
          {stepInput.from?.length ? (
            <div className="trace-links">
              {stepInput.from.map((id) => {
                const source = view.byId.get(id);
                if (!source) return null;
                return (
                  <button
                    key={id}
                    className={`trace-link trace-link--${slug(source.status)}`}
                    onClick={() => onGo(id)}
                  >
                    <span>{source.label}</span>
                    <em>{source.status}</em>
                  </button>
                );
              })}
            </div>
          ) : (
            <p className="trace-none">
              nothing — the iteration before it called no tools
            </p>
          )}
        </Section>
      ) : (
        <Section label="In">
          <Payload
            value={stepInput?.kind === "task" ? stepInput.task : node.input}
            empty="nothing was passed in"
          />
        </Section>
      )}

      {/* Before the payload, not after it: a picture answers "what did it
          actually build" faster than four hundred lines of JSON ever will. */}
      <Shots projectId={projectId} images={node.images} />

      {/* A checklist read as JSON is a checklist nobody reads. These two nodes
          are the only ones whose payload is a list of yes/no answers, and the
          whole value of them is scanning it — so they get ticks and crosses
          and the raw object stays available underneath. */}
      {node.kind === "requirements" || node.kind === "requirements_check" ? (
        <Requirements node={node} />
      ) : (
        <Section label="Out">
          <Payload value={node.output} empty="nothing came back" />
        </Section>
      )}

      {/* The agent's own context — the standing prompt and the task it was
          set. Held by the run or the subbuild; a turn inherits it. */}
      {context && (
        <Section label="The context it was working from">
          {context.task && (
            <Fold
              title="Task"
              note={`${context.task.length.toLocaleString()} chars`}
              open={node.kind !== "tool"}
            >
              <Payload value={context.task} />
            </Fold>
          )}
          {context.system && (
            <Fold
              title="System prompt"
              note={`${context.system.length.toLocaleString()} chars`}
            >
              <Payload value={context.system} />
            </Fold>
          )}
          {context.tools?.length > 0 && (
            <Fold title="Tools it was offered" note={`${context.tools.length}`}>
              <div className="trace-chips">
                {context.tools.map((t) => (
                  <span key={t} className="trace-chip">
                    {t}
                  </span>
                ))}
              </div>
            </Fold>
          )}
        </Section>
      )}

      <Section label="How long it took">
        <div className="trace-times">
          <div>
            <em>started</em>
            <b>{node.at ? new Date(node.at * 1000).toLocaleTimeString() : "—"}</b>
          </div>
          <div>
            <em>ended</em>
            <b>{node.ended ? new Date(node.ended * 1000).toLocaleTimeString() : "—"}</b>
          </div>
          <div>
            <em>took</em>
            <b className="trace-took">{node.ms != null ? formatMs(node.ms) : "—"}</b>
          </div>
        </div>
      </Section>

      <footer className="trace-detail-foot">
        <span className="mono">{node.id}</span>
        <span className="mono">{node.kind}</span>
      </footer>
    </div>
  );
}

function slug(value) {
  return String(value || "none").replace(/\s+/g, "-");
}

/* ------------------------------------------------------------ the view -- */

export default function TraceView({ projectId, runId }) {
  const [runs, setRuns] = useState([]);
  const [chosen, setChosen] = useState(null);
  const [graph, setGraph] = useState(null);
  const [error, setError] = useState(null);
  const [selected, setSelected] = useState(null);
  const [collapsed, setCollapsed] = useState(() => new Set());
  const [query, setQuery] = useState("");
  const [camera, setCamera] = useState({ x: 40, y: 30, k: 1 });

  const frame = useRef(null);
  const drag = useRef(null);
  const fitted = useRef(null);
  // Iteration nodes this view has already closed once. See `seed` below.
  const seeded = useRef(new Set());

  const view = useMemo(() => layout(graph, collapsed), [graph, collapsed]);

  /* -- loading ---------------------------------------------------------- */

  const loadRuns = useCallback(async () => {
    if (!projectId) return;
    try {
      const data = await api.traces(projectId);
      setRuns(data.runs || []);
      setError(null);
      return data.runs || [];
    } catch (e) {
      setError(e.message);
      return [];
    }
  }, [projectId]);

  useEffect(() => {
    setChosen(null);
    setGraph(null);
    setSelected(null);
    loadRuns();
  }, [projectId, loadRuns]);

  // A run started from the composer is the one worth watching, so it is opened
  // rather than merely listed — this view is where the build is now read, and
  // leaving the previous run on screen while a new one records would be the
  // wrong half of the answer.
  useEffect(() => {
    if (!runId) return;
    setChosen(runId);
    loadRuns();
  }, [runId, loadRuns]);

  useEffect(() => {
    if (!runs.length) return;
    setChosen((current) =>
      current && runs.some((r) => r.run_id === current) ? current : runs[0].run_id
    );
  }, [runs]);

  const loadGraph = useCallback(async () => {
    if (!projectId || !chosen) return null;
    try {
      const data = await api.traceGraph(projectId, chosen);

      // Which iterations are new, and marking them seen, both happen out here
      // rather than inside the updater below — StrictMode calls an updater
      // twice, and one that had already recorded these as seen would return
      // the set unchanged the second time and leave them open.
      const fresh = newIterations(data, seeded.current);
      for (const id of fresh) seeded.current.add(id);

      // Graph and closed set together, deliberately: they batch into one
      // commit, so the layout is only ever computed with the two agreeing.
      // Closing the iterations in an effect *after* the graph landed left one
      // render of the fully expanded tree in between — and that is the one
      // `fit` measured, scaling the camera for a graph several times the size
      // of the one actually on screen.
      setGraph(data);
      if (fresh.length) {
        setCollapsed((current) => new Set([...current, ...fresh]));
      }
      setError(null);
      return data;
    } catch (e) {
      setError(e.message);
      return null;
    }
  }, [projectId, chosen]);

  useEffect(() => {
    setSelected(null);
    setCollapsed(new Set());
    seeded.current = new Set();
    loadGraph();
  }, [loadGraph]);

  // Keep watching while the run is still going: the trace is appended to disk
  // as it happens, so this is a build being observed rather than a replay.
  const live = graph?.meta?.status === "running";
  useEffect(() => {
    if (!live) return;
    const id = setInterval(() => {
      loadGraph();
      loadRuns();
    }, POLL_MS);
    return () => clearInterval(id);
  }, [live, loadGraph, loadRuns]);

  /* -- camera ----------------------------------------------------------- */

  const fit = useCallback(() => {
    const box = frame.current?.getBoundingClientRect();
    if (!box || !view || !view.nodes.length) return;
    const k = Math.min(1.1, (box.width - 80) / view.width,
                       (box.height - 80) / view.height);
    const scale = Math.max(0.25, k);
    setCamera({
      x: (box.width - view.width * scale) / 2,
      y: (box.height - view.height * scale) / 2,
      k: scale,
    });
  }, [view]);

  // Fit once per run, not on every poll — a graph that re-centres itself
  // under the cursor every two seconds cannot be read.
  useEffect(() => {
    if (!view || fitted.current === chosen) return;
    fitted.current = chosen;
    fit();
  }, [view, chosen, fit]);

  // Non-passive, because zooming has to stop the page from scrolling.
  useEffect(() => {
    const el = frame.current;
    if (!el) return;
    const onWheel = (e) => {
      e.preventDefault();
      const box = el.getBoundingClientRect();
      const px = e.clientX - box.left;
      const py = e.clientY - box.top;
      setCamera((c) => {
        const k = Math.min(2.4, Math.max(0.2, c.k * Math.exp(-e.deltaY * 0.0015)));
        const ratio = k / c.k;
        return { k, x: px - (px - c.x) * ratio, y: py - (py - c.y) * ratio };
      });
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  const onPointerDown = (e) => {
    if (e.target.closest(".trace-node")) return;
    drag.current = { x: e.clientX, y: e.clientY, cam: camera };
    e.currentTarget.setPointerCapture(e.pointerId);
  };

  const onPointerMove = (e) => {
    const from = drag.current;
    if (!from) return;
    setCamera({
      k: from.cam.k,
      x: from.cam.x + (e.clientX - from.x),
      y: from.cam.y + (e.clientY - from.y),
    });
  };

  const endDrag = () => {
    drag.current = null;
  };

  /** Centre a node and select it — how the In/Out links navigate. */
  const goTo = (id) => {
    setSelected(id);
    const at = view?.nodes.find((n) => n.node.id === id);
    const box = frame.current?.getBoundingClientRect();
    if (!at || !box) return;
    setCamera((c) => ({
      k: c.k,
      x: box.width / 2 - (at.x + W / 2) * c.k,
      y: box.height / 2 - (at.y + H / 2) * c.k,
    }));
  };

  const toggle = (id, e) => {
    e?.stopPropagation();
    setCollapsed((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  /* -- rendering -------------------------------------------------------- */

  const needle = query.trim().toLowerCase();
  const matches = useMemo(() => {
    if (!needle || !graph) return null;
    const hit = new Set();
    for (const node of graph.nodes) {
      const hay = [node.label, node.title, node.tool, node.lane,
                   JSON.stringify(node.input), JSON.stringify(node.output)]
        .join(" ")
        .toLowerCase();
      if (hay.includes(needle)) hit.add(node.id);
    }
    return hit;
  }, [needle, graph]);

  const active = view?.byId.get(selected) || null;

  if (!projectId) return null;

  return (
    <div className="trace">
      <div className="trace-runs">
        <span className="eyebrow">RUNS</span>
        {runs.length === 0 && (
          <span className="trace-none">
            {error ? error : "nothing has been run on this project yet"}
          </span>
        )}
        <div className="trace-runs-list">
          {runs.map((run) => (
            <button
              key={run.run_id}
              className={`trace-run ${run.run_id === chosen ? "is-on" : ""}`}
              onClick={() => setChosen(run.run_id)}
              title={run.message}
            >
              <i className={`trace-dot trace-dot--${slug(run.status)}`} />
              <span className="trace-run-msg">{run.message || "(no message)"}</span>
              <em>
                {relativeTime(
                  run.started ? new Date(run.started * 1000).toISOString() : null
                )}
              </em>
            </button>
          ))}
        </div>
        <div className="trace-spacer" />
        <input
          className="trace-search"
          value={query}
          placeholder="Find in this run…"
          onChange={(e) => setQuery(e.target.value)}
        />
        <button className="btn btn--quiet" onClick={fit} disabled={!view}>
          Fit
        </button>
      </div>

      <div className="trace-body">
        <div
          className="trace-canvas"
          ref={frame}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={endDrag}
          onPointerCancel={endDrag}
        >
          {view && view.nodes.length > 0 ? (
            <svg className="trace-svg" width="100%" height="100%">
              <g transform={`translate(${camera.x},${camera.y}) scale(${camera.k})`}>
                {view.links.map((link, i) => (
                  <path
                    key={i}
                    className={`trace-edge trace-edge--${link.kind}`}
                    d={
                      link.kind === "next"
                        ? sequence(link.from, link.to)
                        : elbow(link.from, link.to)
                    }
                  />
                ))}

                {view.nodes.map(({ node, x, y, children, hidden }) => {
                  const dim = matches && !matches.has(node.id);
                  return (
                    <g
                      key={node.id}
                      className={
                        `trace-node trace-node--${node.kind}` +
                        ` is-${slug(node.status)}` +
                        (node.id === selected ? " is-selected" : "") +
                        (hidden ? " is-closed" : "") +
                        (dim ? " is-dim" : "") +
                        (matches?.has(node.id) ? " is-hit" : "")
                      }
                      transform={`translate(${x},${y})`}
                      // One click does both: shows what the node holds in the
                      // panel, and opens what it is hiding on the canvas. A
                      // closed iteration is closed because nobody has asked
                      // about it yet — asking about it is this click. Closing
                      // it again is the ± , so that a second click on a node
                      // you are reading does not fold it away under you.
                      onClick={() => {
                        setSelected(node.id);
                        if (hidden) toggle(node.id);
                      }}
                    >
                      <rect width={W} height={H} rx="7" />
                      <text className="trace-node-glyph" x="13" y={H / 2 + 4}>
                        {(KINDS[node.kind] || {}).glyph || "•"}
                      </text>
                      <text className="trace-node-label" x="30" y={H / 2 + 4}>
                        {clip(node.label, 22)}
                      </text>
                      {node.ms != null && (
                        <text className="trace-node-ms" x={W - 10} y={H / 2 + 4}>
                          {formatMs(node.ms)}
                        </text>
                      )}
                      {children > 0 && (
                        <g
                          className="trace-toggle"
                          transform={`translate(${W + 4},${H / 2})`}
                          onClick={(e) => toggle(node.id, e)}
                        >
                          <circle r="8" />
                          <text y="4">{hidden ? `+` : `−`}</text>
                        </g>
                      )}
                      {hidden > 0 && (
                        <text className="trace-node-hidden" x={W + 18} y={H / 2 + 4}>
                          {hidden}
                        </text>
                      )}
                      {/* there is a picture inside this one — worth saying on
                          the node, since it is the reason to open it */}
                      {node.images?.length > 0 && (
                        <circle className="trace-node-shot" cx={W - 8} cy="8" r="3" />
                      )}
                    </g>
                  );
                })}
              </g>
            </svg>
          ) : (
            <div className="trace-blank">
              <p>{error || "No run selected."}</p>
              <p className="trace-none">
                Ask the agent to build something — every run from now on is
                recorded here, whole.
              </p>
            </div>
          )}

          {graph && (
            <div className="trace-legend">
              {live && <span className="trace-live">running</span>}
              <span>{graph.nodes.length} nodes</span>
              {Object.entries(graph.meta?.counts || {})
                .filter(([kind]) => kind === "tool" || kind === "step")
                .map(([kind, n]) => (
                  <span key={kind}>
                    {n} {kind === "step" ? "iterations" : "tool calls"}
                  </span>
                ))}
              {graph.meta?.timing?.wall_ms > 0 && (
                <span className="trace-wall">{formatMs(graph.meta.timing.wall_ms)}</span>
              )}
              {/* Objects are built at the same time, so the work inside a run
                  adds up to more than the run lasted. That ratio is what the
                  parallelism actually bought. */}
              {graph.meta?.timing?.overlap > 1.05 && (
                <span className="trace-overlap" title="subconstruction time ÷ wall clock">
                  {graph.meta.timing.overlap}× parallel
                </span>
              )}
              {matches && <span>{matches.size} match</span>}
            </div>
          )}
        </div>

        <aside className="trace-side">
          {/* keyed, so the folds and "show all" toggles start closed again on
              the next node rather than inheriting the last one's state */}
          <NodeDetail
            key={selected || "none"}
            node={active}
            view={view}
            onGo={goTo}
            projectId={projectId}
          />
        </aside>
      </div>
    </div>
  );
}

/** Parent's right edge to child's left edge, as a flat S. */
function elbow(from, to) {
  const x1 = from.x + W;
  const y1 = from.y + H / 2;
  const x2 = to.x;
  const y2 = to.y + H / 2;
  const mid = x1 + (x2 - x1) / 2;
  return `M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2},${y2}`;
}

/** One turn to the next: down the left-hand side, out of the way of the tree. */
function sequence(from, to) {
  const x = from.x + 14;
  return `M${x},${from.y + H} L${x},${to.y}`;
}

function clip(text, max) {
  const value = String(text || "");
  return value.length > max ? `${value.slice(0, max - 1)}…` : value;
}
