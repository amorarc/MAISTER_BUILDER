import { useState } from "react";
import BrickLogo from "./BrickLogo";
import { plural, relativeTime } from "../format";

/** Click the title to rename. Enter commits, Escape backs out. */
function DocName({ project, onRename }) {
  const [draft, setDraft] = useState(null);

  if (draft === null) {
    return (
      <button
        className="doc-name"
        title="Click to rename"
        onClick={() => setDraft(project.name)}
      >
        {project.name}.ldr
      </button>
    );
  }

  const commit = () => {
    const name = draft.trim();
    setDraft(null);
    if (name && name !== project.name) onRename(project.id, name);
  };

  return (
    <input
      className="doc-rename"
      value={draft}
      autoFocus
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") commit();
        if (e.key === "Escape") setDraft(null);
      }}
    />
  );
}

/**
 * Brand, the open document, the Model/Source/Trace switch, and the actions.
 *
 * The actions are grouped by what they are *for* rather than strung out in one
 * row, because a row of seven buttons is read left to right every time you
 * want one of them. Three groups, each divided from the next:
 *
 *   FILE     what this project is - start one, bring one in, take one away
 *   LIBRARY  the shelves either side of it - what it built, what it builds from
 *   MODEL    what to do with the thing on the baseplate right now
 *
 * and the cog on its own at the end, since settings belong to the app rather
 * than to any of the three.
 */
export default function TopBar({
  project,
  modified,
  view,
  onView,
  onNew,
  onUpload,
  onExport,
  onInstructions,
  building,
  onSaveToGallery,
  saving,
  onSave,
  onGallery,
  onParts,
  onSets,
  onSettings,
  onRename,
  screen,
  dirty,
  lines,
  health,
}) {
  const gallery = screen === "gallery";
  const parts = screen === "parts";
  const sets = screen === "sets";
  const open = !!project && screen === "build";
  const source = view === "source";
  const back = project ? "Back to project" : "Back to build";

  return (
    <header className="topbar">
      <div className="brand">
        <BrickLogo />
        <div className="wordmark">
          MAISTER<span>BUILDER</span>
        </div>
      </div>

      {open && (
        <>
          <div className="topbar-rule" />
          <div className="doc">
            <DocName project={project} onRename={onRename} />
            <div className={`doc-meta ${source && dirty ? "doc-meta--dirty" : ""}`}>
              {source
                ? `${plural(lines, "line")} · ${dirty ? "unsaved changes" : "saved"}`
                : `edited ${relativeTime(modified) || "just now"}`}
            </div>
          </div>

          <div className="segmented" role="group" aria-label="View">
            <button aria-pressed={view === "model"} onClick={() => onView("model")}>
              Model
            </button>
            <button aria-pressed={source} onClick={() => onView("source")}>
              Source
            </button>
            {/* the model, its text, and how the agent arrived at both */}
            <button aria-pressed={view === "trace"} onClick={() => onView("trace")}>
              Trace
            </button>
          </div>
        </>
      )}

      <div className="topbar-spacer" />

      {health && !health.token_configured && (
        <span className="badge badge--warn" title="Set HF_TOKEN so the agent can run">
          No HF token
        </span>
      )}

      {/* The grid verdict used to sit here too. It is already in three other
          places - the rail foot breaks it down, the editor foot counts the
          problems, and anything that needs a fix is named along the bottom
          edge - so up here it was the same sentence for the fourth time. */}

      <div className="topbar-actions">
        {/* FILE - where a project comes from */}
        <div className="topbar-group" role="group" aria-label="File">
          <button
            className={`btn ${open || gallery || parts ? "" : "btn--primary"}`}
            onClick={onNew}
          >
            New project
          </button>
          <button className="btn" onClick={onUpload}>
            Upload .ldr
          </button>
        </div>

        {/* LIBRARY - the two shelves. Each button goes both directions: onto
            its shelf and back off it. */}
        <div className="topbar-group" role="group" aria-label="Library">
          <button className="btn" aria-pressed={gallery} onClick={onGallery}>
            {gallery ? back : "Gallery"}
          </button>
          {/* what it builds out of, next to what it has built */}
          <button className="btn" aria-pressed={parts} onClick={onParts}>
            {parts ? back : "Parts"}
          </button>
          {/* and what it learns from: 1,800 real models */}
          <button className="btn" aria-pressed={sets} onClick={onSets}>
            {sets ? back : "Sets"}
          </button>
        </div>

        {/* MODEL - what to do with what is on the baseplate */}
        {open && (
          <div className="topbar-group" role="group" aria-label="This model">
            {source ? (
              <button className="btn btn--primary" onClick={onSave} disabled={!dirty}>
                {dirty ? "Save" : "Saved"}
              </button>
            ) : (
              <>
                {/* the shelf is the user's, so putting something on it is a
                    button rather than something the agent decides */}
                <button className="btn" onClick={onSaveToGallery} disabled={saving}>
                  {saving ? "Saving…" : "Save to gallery"}
                </button>
                {/* the model as a booklet - only meaningful next to the model
                    itself */}
                <button className="btn" onClick={onInstructions} disabled={building}>
                  {building ? "Building…" : "Instructions"}
                </button>
                {/* named for what lands in the downloads folder, since the
                    button beside it also exports, and as a PDF */}
                <button className="btn btn--primary" onClick={onExport}>
                  Export .ldr
                </button>
              </>
            )}
          </div>
        )}

        {/* the app itself, which belongs to none of the three above */}
        <button
          className="btn btn--icon topbar-cog"
          onClick={onSettings}
          title="Settings - model, provider, all projects"
          aria-label="Settings"
        >
          ⚙
        </button>
      </div>
    </header>
  );
}
