import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { formatBytes, plural } from "../format";
import { BRICK, colourFor } from "../brick";

const ACCEPTED = /\.(ldr|mpd|dat)$/i;
const MAX_BYTES = 8_000_000; // the backend refuses anything larger
const MAX_LABEL = "8 MB";

/** Rough part count without parsing: every type-1 line is one reference. */
function countParts(text) {
  let n = 0;
  for (const line of text.split("\n")) if (line.trimStart().startsWith("1 ")) n += 1;
  return n;
}

function describe(file, text) {
  if (!ACCEPTED.test(file.name)) return { error: "not an .ldr, .mpd or .dat file" };
  if (file.size > MAX_BYTES) return { error: `too large — ${MAX_LABEL} max` };
  const parts = countParts(text);
  if (!parts) return { error: "no part references found" };
  return { parts };
}

export default function ImportDialog({ open, onClose, onImported }) {
  const [queue, setQueue] = useState([]);
  const [busy, setBusy] = useState(false);
  const [over, setOver] = useState(false);
  const inputRef = useRef(null);
  const cardRef = useRef(null);

  useEffect(() => {
    if (!open) return;
    setQueue([]);
    setBusy(false);
    setOver(false);
    const onKey = (e) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    cardRef.current?.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const accept = async (files) => {
    const rows = await Promise.all(
      [...files].map(async (file) => {
        let text = "";
        try {
          text = await file.text();
        } catch {
          return { file, error: "could not be read" };
        }
        return { file, text, ...describe(file, text) };
      })
    );
    setQueue((q) => [...q, ...rows]);
  };

  const ready = queue.filter((r) => !r.error);

  const importAll = async () => {
    setBusy(true);
    let last = null;
    const failed = [];
    for (const row of ready) {
      try {
        last = await api.uploadProject(row.file);
      } catch (e) {
        failed.push({ ...row, error: e.message });
      }
    }
    setBusy(false);
    if (failed.length) {
      setQueue(failed);
      return;
    }
    onImported(last);
  };

  return (
    <div
      className="modal"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="modal-card"
        role="dialog"
        aria-modal="true"
        aria-label="Bring in a model"
        tabIndex={-1}
        ref={cardRef}
      >
        <div className="modal-head">
          <div className="modal-mark" />
          <div style={{ flex: 1 }}>
            <h2 className="modal-title">Bring in a model</h2>
            <p className="modal-sub">
              Drop an <code>.ldr</code> or <code>.mpd</code> file. I&apos;ll read the parts and
              you can keep building from there.
            </p>
          </div>
          <button className="modal-close" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>

        <div
          className={`dropzone ${over ? "is-over" : ""}`}
          role="button"
          tabIndex={0}
          onClick={() => inputRef.current?.click()}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              inputRef.current?.click();
            }
          }}
          onDragOver={(e) => {
            e.preventDefault();
            setOver(true);
          }}
          onDragLeave={() => setOver(false)}
          onDrop={(e) => {
            e.preventDefault();
            setOver(false);
            accept(e.dataTransfer.files);
          }}
        >
          <div className="drop-bricks" aria-hidden="true">
            <i />
            <i />
            <i />
          </div>
          <div className="drop-title">{over ? "Let go" : "Drop it right here"}</div>
          <div className="drop-sub">
            or <u>browse your files</u> · up to {MAX_LABEL}
          </div>
          <input
            ref={inputRef}
            type="file"
            accept=".ldr,.mpd,.dat"
            multiple
            hidden
            onChange={(e) => {
              accept(e.target.files);
              e.target.value = "";
            }}
          />
        </div>

        {queue.length > 0 && (
          <div className="file-list">
            {queue.map((row, i) => (
              <div className="file-row" key={i}>
                <div
                  className="file-mark"
                  style={{ "--brick": row.error ? BRICK.red : colourFor(row.file.name) }}
                />
                <div className="file-body">
                  <div className="file-name">{row.file.name}</div>
                  {busy && !row.error ? (
                    <div className="file-bar">
                      <div>
                        <i />
                      </div>
                      <span>reading…</span>
                    </div>
                  ) : (
                    <div className="file-meta">
                      {formatBytes(row.file.size)}
                      {row.error ? ` · ${row.error}` : ` · ${plural(row.parts, "part")} found`}
                    </div>
                  )}
                </div>
                {!busy && (
                  <div className={`file-tag ${row.error ? "file-tag--err" : ""}`}>
                    {row.error ? "Skipped" : "Ready"}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}

        <div className="modal-foot">
          <p>Sub-models are kept as separate steps.</p>
          <button className="btn btn--quiet" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button
            className="btn btn--primary btn--big"
            onClick={importAll}
            disabled={busy || ready.length === 0}
          >
            {busy ? "Opening…" : "Open on grid"}
          </button>
        </div>
      </div>
    </div>
  );
}
