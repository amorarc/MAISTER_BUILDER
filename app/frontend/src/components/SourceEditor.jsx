import { useCallback, useEffect, useMemo, useRef } from "react";
import { highlight, partRef } from "../ldraw";
import { plural } from "../format";

// Beyond this the per-keystroke re-highlight stops being free; plain text is a
// fair trade for staying responsive.
const HIGHLIGHT_LIMIT = 4000;

/**
 * Which lines the last validation found fault with, and why.
 *
 * The report names lines in six different places, one per kind of wrong, and
 * nothing has ever put them together - so the editor could tell you a model
 * failed without telling you where. They fold into one map here: line number
 * to the reasons against it.
 *
 * `unresolved_parts` is the odd one out. It comes back as bare part names with
 * no line numbers at all, so those are matched against the source by name,
 * against the whole reference and against its basename - the checker and the
 * file do not always spell a part the same way.
 */
function faultLines(validation, lines) {
  const marks = new Map();
  if (!validation || validation.passed || validation.error) return marks;

  const add = (line, why) => {
    const n = Number(line);
    if (!Number.isInteger(n) || n < 1 || n > lines.length) return;
    const at = marks.get(n);
    if (!at) marks.set(n, [why]);
    else if (!at.includes(why)) at.push(why);
  };

  for (const m of validation.connectivity?.misaligned_parts || []) {
    add(m.line, `${m.part} is off the stud grid by ${m.gap_ldu} LDU`);
  }

  for (const s of validation.overcrowded_studs || []) {
    add(s.line, `${s.part} shares its four studs with line ${s.covered_by?.[0]?.line ?? "?"}`);
  }

  for (const p of validation.missing_parts || []) {
    for (const line of p.lines || []) add(line, `${p.part} does not exist`);
  }

  // Both ends of an overlap are wrong together, and the fix names which one to
  // move - so it is worth reading from either line.
  for (const c of validation.collision?.overlapping_parts || []) {
    const why = c.fix || "shares solid plastic with another part";
    add(c.a?.line, why);
    add(c.b?.line, why);
  }

  for (const c of validation.circular_references || []) {
    add(c.line, "circular reference - this file includes itself");
  }

  const unresolved = new Set(
    (validation.unresolved_parts || []).flatMap((name) => {
      const file = String(name).toLowerCase().replace(/\\/g, "/");
      return [file, file.split("/").pop()];
    })
  );
  if (unresolved.size) {
    lines.forEach((line, i) => {
      const fields = line.trim().split(/\s+/);
      if (fields[0] !== "1" || fields.length < 15) return;
      const file = fields.slice(14).join(" ");
      const key = file.toLowerCase().replace(/\\/g, "/");
      if (unresolved.has(key) || unresolved.has(key.split("/").pop())) {
        add(i + 1, `${file} has no geometry the checker could resolve`);
      }
    });
  }

  return marks;
}

/**
 * Which lines hold a part the stud checker could not vouch for.
 *
 * Kept apart from `faultLines` rather than folded into it, and the reason is
 * the whole point of the distinction: these are **not faults**. No stud
 * connection could be established and nothing was near enough to call it a near
 * miss, which means either the part is genuinely adrift or it is held by a
 * joint the checker does not model - a clip, a bar, a Technic pin, a hinge, a
 * bracket, a minifigure's hand. All of those are correct building.
 *
 * So this runs on a model that *passed*, where `faultLines` returns nothing at
 * all. A build can validate perfectly and still have a dozen parts the checker
 * has no opinion on, and those were invisible here until now.
 *
 * The report lists at most 25 of them, so on a model with more the marks are a
 * sample. `connectivity.unverified` is the real total, which is what the foot
 * counts from.
 */
function unverifiedLines(validation, lines) {
  const marks = new Map();
  if (!validation || validation.error) return marks;

  for (const u of validation.connectivity?.unverified_parts || []) {
    const n = Number(u.line);
    if (!Number.isInteger(n) || n < 1 || n > lines.length) continue;
    // A list, the same shape `faultLines` returns, so a caller can hold the
    // two side by side without knowing which it has.
    marks.set(n, [
      `${u.part} sits on no stud the checker can find - either it is adrift, ` +
        `or a clip, pin, hinge or hand is holding it`,
    ]);
  }

  return marks;
}

/**
 * The raw .ldr, with a highlighted copy sitting under a transparent textarea.
 * Both share the exact same font metrics and padding, so the two layers stay
 * glued together; the textarea owns the scrolling and drives the other two.
 */
export default function SourceEditor({ name, value, onChange, onSave, dirty,
                                       validation, files = [], file = "model.ldr",
                                       onFile, onCursor }) {
  const inputRef = useRef(null);
  const gutterRef = useRef(null);
  const layerRef = useRef(null);

  const lines = useMemo(() => value.split("\n"), [value]);
  const coloured = lines.length <= HIGHLIGHT_LIMIT;

  const sync = () => {
    const el = inputRef.current;
    if (!el) return;
    if (gutterRef.current) gutterRef.current.style.transform = `translateY(${-el.scrollTop}px)`;
    if (layerRef.current) {
      layerRef.current.style.transform = `translate(${-el.scrollLeft}px, ${-el.scrollTop}px)`;
    }
  };

  // A rebuild or a project switch replaces the text under a scrolled viewport.
  useEffect(sync, [value]);

  /**
   * Say which piece the caret is sitting on, so the viewer can light it up.
   *
   * Read off the textarea rather than tracked as state: the caret moves on
   * every click, every arrow key and every character typed, and a re-render
   * per keystroke to hold a number the editor itself does not draw would cost
   * the typing. `null` for a line that places nothing - a comment, a step
   * marker, the blank line between two of them.
   *
   * It is NOT cleared when the box loses focus. Losing focus is what happens
   * when you go and turn the model round to look at the brick this just lit
   * up, and that is the whole point of it.
   */
  const report = useCallback(() => {
    const el = inputRef.current;
    if (!el || !onCursor) return;
    const rows = el.value.split("\n");
    const at = el.value.slice(0, el.selectionStart).split("\n").length;
    const ref = partRef(rows[at - 1]);
    if (!ref) {
      onCursor(null);
      return;
    }
    // Which part this is, counting from the top of the file. The viewer finds
    // the piece by name and position first; this is what it falls back to.
    let index = 0;
    for (let i = 0; i < at - 1; i += 1) {
      if (partRef(rows[i])) index += 1;
    }
    onCursor({
      ...ref,
      line: at,
      index,
      // What the parent compares against to know whether anything changed:
      // moving along a line is not moving to another piece.
      key: `${ref.file}|${ref.x}|${ref.y}|${ref.z}|${at}`,
    });
  }, [onCursor]);

  // The file on screen changed under the caret - a component opened, a build
  // written. Whatever was lit up is a line of a file nobody is reading now.
  useEffect(() => {
    onCursor?.(null);
  }, [file, onCursor]);

  // A subconstruction the agent is building, rather than the scene itself.
  const component = file !== "model.ldr";

  // The report describes the file on disk. A component is a different file
  // altogether, so its lines are never marked from it.
  const marks = useMemo(
    () => (component ? new Map() : faultLines(validation, lines)),
    [component, validation, lines]
  );

  const warns = useMemo(
    () => (component ? new Map() : unverifiedLines(validation, lines)),
    [component, validation, lines]
  );

  // Red wins where a line is both. A part can be unverified *and* overlapping
  // something, and of the two only one is worth acting on - a line painted the
  // colour of "probably fine" while it shares plastic with its neighbour is the
  // editor talking the reader out of the fault.
  const tone = (n) =>
    marks.has(n) ? "is-bad" : warns.has(n) ? "is-warn" : undefined;
  // Both reasons all the same, when there are two: the band can only be one
  // colour, the tooltip has room for the whole story.
  const why = (n) =>
    [...(marks.get(n) || []), ...(warns.get(n) || [])].join(" · ") || undefined;

  // Counted off the marks rather than off two of the report's fields, which is
  // what this used to do - a model failing only on overlaps read "parses
  // clean" here while the rest of the app said FAIL.
  const problems = marks.size;
  // The report's own total, not the length of the list it sent: it lists 25 at
  // most, and a foot that said "8 unverified" over a model with 40 of them
  // would be counting the sample.
  const unverified = validation?.connectivity?.unverified ?? 0;
  const failed = !!validation && !validation.passed && !validation.error;

  return (
    <div
      className={`editor ${dirty && (problems || warns.size) ? "is-stale" : ""}`}
    >
      <div className="editor-head">
        <div className="eyebrow">{name.toUpperCase()}.LDR</div>
        {dirty && <span className="dirty-dot" title="Unsaved changes" />}

        {/* A scene is built as several files, and while it is being built the
            model file is still empty - everything that is happening is in the
            components. They appear here as they are written. */}
        {files.length > 1 && (
          <div className="editor-files" role="group" aria-label="File">
            {files.map((f) => (
              <button
                key={f.file}
                className={`editor-file ${f.file === file ? "is-on" : ""}`}
                onClick={() => onFile?.(f.file)}
                title={`${f.file} · ${plural(f.parts, "part")}`}
              >
                {f.is_scene ? "scene" : f.name}
                <em>{f.parts}</em>
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="editor-body">
        <div className="editor-gutter" ref={gutterRef} aria-hidden="true">
          {lines.map((_, i) => (
            <div key={i} className={tone(i + 1)}>
              {i + 1}
            </div>
          ))}
        </div>

        <div className="editor-hl" aria-hidden="true">
          <div ref={layerRef}>
            {lines.map((line, i) => (
              <div
                key={i}
                className={tone(i + 1)}
                // The band is drawn here, under the transparent textarea, so
                // nothing about a line's metrics changes and the caret stays
                // exactly where the character it is next to is drawn.
                title={why(i + 1)}
              >
                {coloured ? (
                  highlight(line).map((run, j) => (
                    <span key={j} className={`tok-${run.tone}`}>
                      {run.text}
                    </span>
                  ))
                ) : (
                  <span className="tok-plain">{line}</span>
                )}
                {/* zero-width space: keeps a blank line one row tall */}
                {line === "" ? "​" : null}
              </div>
            ))}
          </div>
        </div>

        <textarea
          ref={inputRef}
          className="editor-input"
          value={value}
          spellCheck={false}
          wrap="off"
          aria-label="LDraw source"
          onChange={(e) => {
            onChange(e.target.value);
            report(); // the line under the caret can be a different one now
          }}
          onScroll={sync}
          // Every way a caret moves. `select` covers clicks, drags and the
          // arrow keys; `keyUp` catches the ones that move it without changing
          // the selection, which not every browser reports as a select.
          onSelect={report}
          onKeyUp={report}
          onClick={report}
          onKeyDown={(e) => {
            if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
              e.preventDefault();
              if (dirty) onSave();
            }
          }}
        />
      </div>

      <div className="editor-foot">
        {/* The validation belongs to the scene file. Reporting it under a
            component would have it say "no part references found" over a
            component full of them - it is describing the other file. */}
        {component ? (
          <span>component of the scene - checked when it is assembled</span>
        ) : validation?.error ? (
          <span className="bad">✕ {validation.error}</span>
        ) : validation ? (
          <span className={failed ? "bad" : "ok"} title={validation.verdict}>
            {problems
              ? `✕ ${plural(problems, "line")} to fix`
              : failed
                ? `✕ ${validation.verdict || "does not validate"}`
                : "✓ parses clean"}
          </span>
        ) : (
          <span>not checked yet</span>
        )}
        {/* Said beside the verdict rather than folded into it, because it is
            not a fault and must not read as one: a model can be clean and have
            these. The yellow bands are meaningless without a line saying what
            yellow is. */}
        {!component && unverified > 0 && (
          <span
            className="warn"
            title={
              `${plural(unverified, "part")} the stud checker could not vouch ` +
              `for - held by a clip, pin, hinge or hand, or else adrift. ` +
              `Not a fault` +
              (warns.size < unverified
                ? `. The report marks the first ${warns.size} of them`
                : "")
            }
          >
            ⚠ {plural(unverified, "part")} unverified
          </span>
        )}
        {/* The marks are line numbers from the last save; unsaved edits can
            have moved the lines out from under them. */}
        {dirty && (problems > 0 || warns.size > 0) && (
          <span className="bad">marks are from the last save</span>
        )}
        <span>{plural(lines.length, "line")}</span>
        <span className="spacer" />
        {component ? (
          <span>read-only here</span>
        ) : (
          <span className={dirty ? "bad" : ""}>
            {dirty ? "unsaved - nothing is written until you save" : "in sync with the model"}
          </span>
        )}
        <button
          className="btn btn--primary editor-save"
          onClick={onSave}
          disabled={!dirty || component}
        >
          {dirty ? "Save ⌘S" : "Saved"}
        </button>
      </div>
    </div>
  );
}
