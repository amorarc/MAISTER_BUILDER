import { useCallback, useEffect, useState } from "react";
import BrickLogo from "./BrickLogo";
import { api } from "../api";
import { plural } from "../format";
import { thumbnailForSet } from "../thumbnail";

/**
 * The shelf: 1,800 official LEGO models, browsable.
 *
 * These are the best material in the project - real designs with real
 * coordinates, which is what the builder learns from and grafts out of. Until
 * now the only way to see one was to ask the agent, which meant you could not
 * find anything you did not already know the name of.
 *
 * Every card is a live render of the set's own LDraw source, drawn by the same
 * off-screen renderer that draws the part swatches (see thumbnail.js) - so a
 * card is the model, not a photograph of the box. They are queued one at a
 * time and cached by set number, because the corpus never changes.
 */

const PAGE = 48;

const SORTS = [
  { id: "name", label: "Name" },
  { id: "pieces", label: "Biggest" },
  { id: "year", label: "Newest" },
  { id: "theme", label: "Theme" },
  { id: "number", label: "Set number" },
];

const SIZES = [
  { id: "", label: "Any size", min: null, max: null },
  { id: "small", label: "Under 100 parts", min: null, max: 99 },
  { id: "medium", label: "100 – 500", min: 100, max: 500 },
  { id: "large", label: "Over 500", min: 501, max: null },
];

/** The render if it arrives, the set number until then. */
function SetArt({ number }) {
  const [png, setPng] = useState(null);

  useEffect(() => {
    let alive = true;
    setPng(null);
    thumbnailForSet(number).then((url) => {
      if (alive) setPng(url);
    });
    return () => {
      alive = false;
    };
  }, [number]);

  if (png) return <img className="card-photo" src={png} alt="" />;
  return (
    <div className="part-art-wait" aria-hidden="true">
      {number}
    </div>
  );
}

/** One set, opened: what it is, what it is made of, and the way in. */
function SetSheet({ number, onClose, onStart }) {
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState(null);
  const [starting, setStarting] = useState(false);

  useEffect(() => {
    let alive = true;
    setDetail(null);
    setError(null);
    api
      .setDetails(number)
      .then((d) => alive && setDetail(d))
      .catch((e) => alive && setError(String(e.message || e)));
    return () => {
      alive = false;
    };
  }, [number]);

  const start = async () => {
    setStarting(true);
    try {
      await onStart(detail || { set_number: number });
    } finally {
      setStarting(false);
    }
  };

  // The assemblies worth showing. A real set is a handful of named blocks plus
  // a tail of one-part definitions for printed elements, and the tail is noise
  // to anyone deciding what to build from.
  const assemblies = (detail?.submodels || [])
    .filter((b) => b.parts >= 2 && !/\.dat$/i.test(b.name))
    .slice(0, 12);

  return (
    <div className="modal" onClick={onClose} role="presentation">
      <div
        className="modal-card part-sheet"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label={detail?.set_name || number}
      >
        <div className="modal-head">
          <div style={{ flex: 1, minWidth: 0 }}>
            <h2 className="modal-title">{detail?.set_name || number}</h2>
            <p className="modal-sub">
              <code>{detail?.set_number || number}</code>
              {detail?.theme ? ` · ${detail.theme}` : ""}
              {detail?.year ? ` · ${detail.year}` : ""}
            </p>
          </div>
          <button className="modal-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>

        <div className="part-sheet-body">
          <div className="part-sheet-main">
            <div className="part-sheet-art">
              <SetArt number={number} />
            </div>

            {error && <div className="gallery-error">{error}</div>}

            <div className="part-facts">
              {[
                ["Pieces", detail?.total_pieces?.toLocaleString()],
                ["Distinct parts", detail?.unique_pieces?.toLocaleString()],
                ["Theme", detail?.theme],
                ["Year", detail?.year],
                ["Assemblies", assemblies.length || null],
              ]
                .filter(([, v]) => v != null && v !== "")
                .map(([label, value]) => (
                  <div key={label} className="part-fact">
                    <span className="eyebrow">{label.toUpperCase()}</span>
                    <span>{value}</span>
                  </div>
                ))}
            </div>

            <div className="part-sheet-actions">
              <button className="btn btn--primary" onClick={start} disabled={starting}>
                {starting ? "Opening…" : "Start a project from this set"}
              </button>
              {detail?.source_url && (
                <a
                  className="btn btn--quiet"
                  href={detail.source_url}
                  target="_blank"
                  rel="noreferrer"
                >
                  Source on LDraw.org
                </a>
              )}
            </div>
            <p className="part-company-note">
              A copy opens as a new project. The set on the shelf is never
              changed - take it apart, recolour it, build onto it.
            </p>
          </div>

          <aside className="part-sheet-side">
            {assemblies.length > 0 && (
              <>
                <span className="eyebrow">BUILT OUT OF</span>
                <ul className="part-company">
                  {assemblies.map((b) => (
                    <li key={b.name}>
                      <span className="part-company-name">{b.name}</span>
                      <span className="mono part-company-id">
                        {plural(b.parts, "part")}
                      </span>
                    </li>
                  ))}
                </ul>
                <p className="part-company-note">
                  These are the assemblies the agent can graft from - a wing, a
                  cab, a head - with <code>copy_from_set</code>.
                </p>
              </>
            )}

            {(detail?.top_parts || []).length > 0 && (
              <>
                <span className="eyebrow">PARTS IT USES MOST</span>
                <ul className="part-company">
                  {detail.top_parts.slice(0, 10).map((p) => (
                    <li key={p.part_id}>
                      <span className="part-company-name">
                        {p.description || p.part_id}
                      </span>
                      <span className="mono part-company-id">×{p.quantity}</span>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </aside>
        </div>
      </div>
    </div>
  );
}

export default function SetsGallery({ onOpenProject }) {
  const [query, setQuery] = useState("");
  const [theme, setTheme] = useState("");
  const [size, setSize] = useState("");
  const [sort, setSort] = useState("name");

  const [themes, setThemes] = useState([]);
  const [page, setPage] = useState({ total: 0, sets: [] });
  const [shown, setShown] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [open, setOpen] = useState(null);

  useEffect(() => {
    api
      .setThemes()
      .then((d) => setThemes(d.themes || []))
      .catch(() => setThemes([]));
  }, []);

  const load = useCallback(
    async (offset = 0) => {
      setLoading(true);
      setError(null);
      const chosen = SIZES.find((s) => s.id === size) || SIZES[0];
      try {
        const data = await api.browseSets({
          query,
          theme,
          sort,
          limit: PAGE,
          offset,
          min_pieces: chosen.min,
          max_pieces: chosen.max,
        });
        setPage(data);
        setShown((prev) => (offset === 0 ? data.sets : [...prev, ...data.sets]));
      } catch (e) {
        setError(String(e.message || e));
      } finally {
        setLoading(false);
      }
    },
    [query, theme, size, sort]
  );

  // Typing re-runs the search, but not on every keystroke.
  useEffect(() => {
    const timer = setTimeout(() => load(0), 200);
    return () => clearTimeout(timer);
  }, [load]);

  const start = async (set) => {
    const project = await api.projectFromSet(set.set_number);
    setOpen(null);
    if (onOpenProject) onOpenProject(project.id);
  };

  const more = shown.length < (page.total || 0);

  return (
    <div className="gallery">
      <div className="gallery-head">
        <div>
          <h2 className="gallery-title">The set shelf</h2>
          <p className="gallery-sub">
            {page.total.toLocaleString()} official {plural(page.total, "model")} in
            LDraw - real designs, real coordinates. Open one to start a project
            from it, or leave them here for the builder to graft from.
          </p>
        </div>
        <div className="gallery-tools">
          <div className="rail-search gallery-search">
            <input
              className="input"
              placeholder="Search sets…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>
          <select className="select" value={sort} onChange={(e) => setSort(e.target.value)}>
            {SORTS.map((s) => (
              <option key={s.id} value={s.id}>
                {s.label}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="part-filters">
        <select className="select select--wide" value={theme} onChange={(e) => setTheme(e.target.value)}>
          <option value="">Every theme</option>
          {themes.map((t) => (
            <option key={t.theme} value={t.theme}>
              {t.theme} ({t.sets})
            </option>
          ))}
        </select>

        <select className="select" value={size} onChange={(e) => setSize(e.target.value)}>
          {SIZES.map((s) => (
            <option key={s.id} value={s.id}>
              {s.label}
            </option>
          ))}
        </select>

        {(query || theme || size) && (
          <button
            className="btn btn--icon part-clear"
            onClick={() => {
              setQuery("");
              setTheme("");
              setSize("");
            }}
            aria-label="Clear the filters"
          >
            ×
          </button>
        )}
      </div>

      {error && <div className="gallery-error">{error}</div>}

      {!loading && shown.length === 0 && !error && (
        <div className="gallery-empty">
          <BrickLogo />
          <p>Nothing on the shelf matches that.</p>
        </div>
      )}

      <div className="gallery-grid">
        {shown.map((set) => (
          <article
            key={set.set_number}
            className="card"
            role="button"
            tabIndex={0}
            onClick={() => setOpen(set.set_number)}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                setOpen(set.set_number);
              }
            }}
          >
            <div className="card-art">
              <SetArt number={set.set_number} />
              <span className="card-flag">{set.set_number}</span>
            </div>

            <div className="card-body">
              <div className="card-head">
                <h3 className="card-name">{set.set_name}</h3>
              </div>
              <div className="card-tags">
                {set.theme && <span className="chip">{set.theme}</span>}
                {set.year && <span className="chip">{set.year}</span>}
              </div>
              <div className="card-meta">
                <span>{plural(set.total_pieces || 0, "piece")}</span>
                {set.unique_pieces > 0 && <span>{set.unique_pieces} distinct</span>}
              </div>
            </div>
          </article>
        ))}
      </div>

      {more && (
        <div className="part-more">
          <button className="btn" onClick={() => load(shown.length)} disabled={loading}>
            {loading ? "Fetching…" : `Show more (${page.total - shown.length} left)`}
          </button>
        </div>
      )}

      {open && (
        <SetSheet number={open} onClose={() => setOpen(null)} onStart={start} />
      )}
    </div>
  );
}
