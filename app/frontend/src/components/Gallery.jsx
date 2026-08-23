import { useEffect, useMemo, useState } from "react";
import BrickLogo from "./BrickLogo";
import { api } from "../api";
import { colourHex } from "../ldraw";
import { plural, relativeTime } from "../format";
import { thumbnailFor } from "../thumbnail";

/**
 * Everything the agent built and chose to keep, each card showing an actual
 * render of the model. The renders are produced one at a time off-screen (see
 * thumbnail.js) and arrive as images, so the card falls back to the model's
 * colour fingerprint until its picture is ready — or permanently, if the model
 * cannot be drawn.
 */
/** The render if it arrives, the colour fingerprint until then. */
function Thumbnail({ creation }) {
  const [png, setPng] = useState(null);

  useEffect(() => {
    let alive = true;
    thumbnailFor(creation).then((url) => {
      if (alive) setPng(url);
    });
    return () => {
      alive = false;
    };
  }, [creation]);

  if (png) {
    return <img className="card-photo" src={png} alt={`Render of ${creation.name}`} />;
  }

  const palette = creation.palette || [];
  return (
    <div className="card-swatches" aria-hidden="true">
      {palette.length > 0 ? (
        palette.map((p, i) => (
          <span
            key={i}
            className="card-brick"
            title={`${plural(p.count, "part")} in colour ${p.colour}`}
            style={{ background: colourHex(p.colour), flexGrow: p.count }}
          />
        ))
      ) : (
        <span className="card-brick card-brick--none" style={{ flexGrow: 1 }} />
      )}
    </div>
  );
}

export default function Gallery({ onOpen }) {
  const [creations, setCreations] = useState(null);
  const [error, setError] = useState(null);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(null);

  const load = () => {
    api
      .listCreations()
      .then(setCreations)
      .catch((e) => setError(e.message));
  };

  useEffect(load, []);

  const needle = query.trim().toLowerCase();
  const shown = useMemo(() => {
    if (!creations) return [];
    if (!needle) return creations;
    return creations.filter(
      (c) =>
        c.name?.toLowerCase().includes(needle) ||
        c.description?.toLowerCase().includes(needle) ||
        (c.tags || []).some((t) => t.toLowerCase().includes(needle))
    );
  }, [creations, needle]);

  const open = async (c) => {
    if (busy) return;
    setBusy(c.creation_id);
    try {
      onOpen(await api.openCreation(c.creation_id));
    } catch (e) {
      setError(e.message);
      setBusy(null);
    }
  };

  const remove = async (c, e) => {
    e.stopPropagation();
    if (!window.confirm(`Delete "${c.name}" from the agent's library? This cannot be undone.`)) {
      return;
    }
    try {
      await api.deleteCreation(c.creation_id);
      load();
    } catch (err) {
      setError(err.message);
    }
  };

  return (
    <div className="gallery">
      <div className="gallery-head">
        <div>
          <h2 className="gallery-title">The agent&apos;s workbench</h2>
          <p className="gallery-sub">
            {creations === null
              ? "Reading the library…"
              : `${plural(creations.length, "model")} Maister Builder kept. Open one to edit a copy.`}
          </p>
        </div>
        <div className="gallery-tools">
          <div className="rail-search gallery-search">
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search name, description, tag…"
              aria-label="Search creations"
            />
          </div>
        </div>
      </div>

      {error && <div className="gallery-error">{error}</div>}

      {creations !== null && shown.length === 0 && (
        <div className="gallery-empty">
          <BrickLogo hero />
          <h3>{needle ? "Nothing matches" : "The shelf is empty"}</h3>
          <p>
            {needle
              ? "Try a different word, or clear the search."
              : "The agent saves a model here when it builds one worth finding again. Ask it to build something, and keep it."}
          </p>
        </div>
      )}

      <div className="gallery-grid">
        {shown.map((c) => (
          <article
            key={c.creation_id}
            className={`card ${busy === c.creation_id ? "is-busy" : ""}`}
            role="button"
            tabIndex={0}
            onClick={() => open(c)}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                open(c);
              }
            }}
          >
            <div className="card-art">
              <Thumbnail creation={c} />
              {c.missing && <span className="card-flag">file missing</span>}
              {busy === c.creation_id && <span className="card-flag">opening…</span>}
            </div>

            <div className="card-body">
              <div className="card-head">
                <h3 className="card-name">{c.name}</h3>
                <span className={`chip ${c.validated ? "chip--done" : "chip--warn"}`}>
                  {c.validated ? "validated" : "unchecked"}
                </span>
              </div>
              {c.description && <p className="card-desc">{c.description}</p>}
              <div className="card-meta">
                <span>{plural(c.total_pieces ?? 0, "piece")}</span>
                <span>{plural(c.unique_pieces ?? 0, "unique part")}</span>
                <span>{relativeTime(c.updated_at || c.created_at)}</span>
              </div>
              {(c.tags || []).length > 0 && (
                <div className="card-tags">
                  {c.tags.map((t) => (
                    <span key={t} className="chip">
                      {t}
                    </span>
                  ))}
                </div>
              )}
            </div>

            <button
              className="btn btn--icon card-del"
              title={`Delete ${c.name}`}
              onClick={(e) => remove(c, e)}
            >
              ×
            </button>
          </article>
        ))}
      </div>
    </div>
  );
}
