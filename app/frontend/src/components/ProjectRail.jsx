import { useEffect, useRef, useState } from "react";
import { BRICK, colourFor } from "../brick";
import { CHECKS } from "../buildCheck";
import { plural, relativeTime } from "../format";

// The System colours, plus the greys a model is often mostly made of.
const SWATCHES = [
  BRICK.red, BRICK.blue, BRICK.yellow, BRICK.green,
  "#FE8A18", "#81007B", "#A0A5A9", "#6C6E68",
];

const HEX = /^#[0-9a-fA-F]{6}$/;

/**
 * Any colour at all: the System eight as one-click shortcuts, a full picker,
 * and a hex field for an exact value. `auto` drops back to the colour derived
 * from the project id.
 */
function ColourPicker({ current, resolved, onPick, onClose }) {
  const ref = useRef(null);
  const [custom, setCustom] = useState(resolved);
  const [typed, setTyped] = useState(resolved);

  useEffect(() => {
    const away = (e) => {
      if (!ref.current?.contains(e.target)) onClose();
    };
    const esc = (e) => e.key === "Escape" && onClose();
    // defer, or the click that opened this closes it again
    const id = setTimeout(() => document.addEventListener("mousedown", away));
    document.addEventListener("keydown", esc);
    return () => {
      clearTimeout(id);
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", esc);
    };
  }, [onClose]);

  // The native picker streams changes while a colour is being dragged; settle
  // before writing, so one drag is one save rather than a hundred.
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    const id = setTimeout(() => onPick(custom, { keepOpen: true }), 350);
    return () => clearTimeout(id);
  }, [custom, onPick]);

  const applyTyped = (value) => {
    const hex = value.startsWith("#") ? value : `#${value}`;
    setTyped(hex);
    if (HEX.test(hex)) setCustom(hex.toLowerCase());
  };

  return (
    <div className="swatches" ref={ref}>
      {SWATCHES.map((c) => (
        <button
          key={c}
          type="button"
          className={`swatch ${(current || "").toLowerCase() === c.toLowerCase() ? "is-on" : ""}`}
          style={{ background: c }}
          title={c}
          onClick={() => onPick(c)}
        />
      ))}

      <div className="swatch-custom">
        <input
          type="color"
          value={custom}
          aria-label="Custom colour"
          onChange={(e) => {
            setCustom(e.target.value);
            setTyped(e.target.value);
          }}
        />
        <input
          className="swatch-hex"
          value={typed}
          spellCheck={false}
          aria-label="Hex colour"
          maxLength={7}
          onChange={(e) => applyTyped(e.target.value)}
        />
      </div>

      <button type="button" className="swatch swatch--auto" title="Derive from the project" onClick={() => onPick("")}>
        auto
      </button>
    </div>
  );
}

/**
 * Left rail: every project as a coloured brick, and - at the foot - how the
 * open build currently stands on the stud grid.
 */
export default function ProjectRail({
  projects,
  projectId,
  validation,
  onOpen,
  onDelete,
  onPatch,
  onCheckHover,
}) {
  const [query, setQuery] = useState("");
  const [picking, setPicking] = useState(null); // project id whose brick is open
  const [renaming, setRenaming] = useState(null); // project id being renamed
  const [draft, setDraft] = useState("");

  const commitRename = (p) => {
    const name = draft.trim();
    setRenaming(null);
    if (name && name !== p.name) onPatch(p.id, { name });
  };

  const needle = query.trim().toLowerCase();
  const shown = needle
    ? projects.filter((p) => p.name.toLowerCase().includes(needle))
    : projects;

  return (
    <nav className="rail">
      <div className="rail-head">
        <div className="eyebrow">PROJECTS</div>
        <div className="rail-search">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter…"
            aria-label="Filter projects"
          />
          <span className="rail-count">{needle ? shown.length : `All ${projects.length}`}</span>
        </div>
      </div>

      <div className="rail-list">
        {shown.length === 0 && (
          <div className="rail-empty">{needle ? "Nothing matches." : "No projects yet."}</div>
        )}

        {shown.map((p) => {
          const active = p.id === projectId;
          return (
            <div key={p.id} className={`project ${active ? "is-active" : ""}`}>
              <button
                type="button"
                className="thumb"
                style={{ "--brick": p.colour || colourFor(p.id) }}
                title="Change colour"
                onClick={() => setPicking(picking === p.id ? null : p.id)}
              />

              {renaming === p.id ? (
                <input
                  className="project-rename"
                  value={draft}
                  autoFocus
                  onChange={(e) => setDraft(e.target.value)}
                  onBlur={() => commitRename(p)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") commitRename(p);
                    if (e.key === "Escape") setRenaming(null);
                  }}
                />
              ) : (
                <button type="button" className="project-open" onClick={() => onOpen(p.id)}>
                  <span className="project-body">
                    <span className="project-name">{p.name}</span>
                    <span className="project-meta">
                      {active && validation?.parts != null
                        ? `${plural(validation.parts, "part")} · ${relativeTime(p.modified)}`
                        : relativeTime(p.modified)}
                    </span>
                  </span>
                </button>
              )}

              <button
                type="button"
                className="btn btn--icon project-act"
                title={`Rename ${p.name}`}
                onClick={() => {
                  setDraft(p.name);
                  setRenaming(p.id);
                }}
              >
                ✎
              </button>
              <button
                type="button"
                className="btn btn--icon project-act"
                title={`Delete ${p.name}`}
                onClick={(e) => onDelete(p.id, e)}
              >
                ×
              </button>

              {picking === p.id && (
                <ColourPicker
                  current={p.colour}
                  resolved={p.colour || colourFor(p.id)}
                  onClose={() => setPicking(null)}
                  onPick={(c, opts) => {
                    // dragging the picker keeps it open; a swatch click closes it
                    if (!opts?.keepOpen) setPicking(null);
                    onPatch(p.id, { colour: c });
                  }}
                />
              )}
            </div>
          );
        })}
      </div>

      {validation?.parts != null && (
        <BuildStats validation={validation} onHover={onCheckHover} />
      )}
    </nav>
  );
}

/**
 * How the open build stands on the stud grid - and, on hover, where.
 *
 * Each row lights its own parts up on the model in the colour of its dot, so
 * a count is somewhere to look rather than a number to go hunting for. Only
 * while there is something to show: a row reading 0 has nothing to point at,
 * and a row that highlights on hover when it is empty teaches you not to trust
 * the ones that do.
 */
function BuildStats({ validation, onHover }) {
  const c = validation.connectivity || {};
  const rows = [
    { kind: "connected", value: c.connected ?? 0, always: true },
    { kind: "misaligned", value: c.misaligned ?? 0 },
    { kind: "unverified", value: c.unverified ?? 0 },
    // Blue, and lit whatever it says: one is a whole model and worth seeing
    // outlined, where zero misaligned parts is nothing to look at.
    { kind: "subassemblies", value: c.subassemblies ?? 0, always: true },
  ];

  const hasIndex = !!c.part_index;

  return (
    <div className="rail-foot" onMouseLeave={() => onHover?.(null)}>
      <div className="eyebrow">BUILD CHECK</div>
      {rows.map((r) => {
        const { label, tone } = CHECKS[r.kind];
        // A tone the row has not earned reads as a verdict it has not given:
        // zero misaligned parts is not a red row.
        const dot = r.always || r.value ? tone : "";
        const live = hasIndex && (r.always || r.value > 0);
        return (
          <button
            key={r.kind}
            type="button"
            className={`stat ${live ? "stat--live" : ""}`}
            disabled={!live}
            onMouseEnter={() => live && onHover?.(r.kind)}
            onFocus={() => live && onHover?.(r.kind)}
            onBlur={() => onHover?.(null)}
          >
            <span className={`stat-dot ${dot ? `stat-dot--${dot}` : ""}`} />
            <span className="stat-label">{label}</span>
            <span className="stat-value">{r.value}</span>
          </button>
        );
      })}
    </div>
  );
}
