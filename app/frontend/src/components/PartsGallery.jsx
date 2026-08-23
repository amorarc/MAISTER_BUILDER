import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import BrickLogo from "./BrickLogo";
import { api } from "../api";
import { plural } from "../format";
import { thumbnailForPart } from "../thumbnail";

/**
 * The parts bin: the catalogue the agent builds *out of*, next door to the
 * gallery of things it has built.
 *
 * Every card is a real render of the part rather than a photograph or an icon,
 * drawn from the same LDraw library the viewer uses — so what you are looking
 * at is the geometry the checker measures and the builder places, not an
 * artist's impression of it. They are produced one at a time off-screen (see
 * thumbnail.js) and arrive as images.
 */

const PAGE = 48;

/** The one colour a part is shown in. LDraw 4 is the red everyone pictures. */
const SWATCH = 4;

const KINDS = [
  { id: "", label: "Anything" },
  { id: "brick", label: "Bricks" },
  { id: "plate", label: "Plates" },
  { id: "other", label: "Other" },
];

const SORTS = [
  { id: "relevance", label: "Best match" },
  { id: "popular", label: "Most used" },
  { id: "size", label: "Biggest" },
  { id: "name", label: "Name" },
  { id: "id", label: "Part number" },
];

/** The render if it arrives, the part number until then. */
function PartArt({ partId }) {
  const [png, setPng] = useState(null);

  useEffect(() => {
    let alive = true;
    setPng(null);
    thumbnailForPart(partId, SWATCH).then((url) => {
      if (alive) setPng(url);
    });
    return () => {
      alive = false;
    };
  }, [partId]);

  if (png) return <img className="card-photo" src={png} alt="" />;
  return (
    <div className="part-art-wait" aria-hidden="true">
      {partId}
    </div>
  );
}

function studs(part) {
  if (!part.width_studs || !part.depth_studs) return null;
  return `${part.width_studs} × ${part.depth_studs} studs`;
}

function millimetres(size) {
  const bits = [size?.width, size?.depth, size?.height].filter((v) => v != null);
  return bits.length === 3 ? `${bits.map((v) => v.toFixed(1)).join(" × ")} mm` : null;
}

const ROLE_WORDS = {
  male: "offers the plug",
  female: "offers the socket",
};

/** How a part joins to anything else — the half of a description that decides
 *  whether a placement is even possible. */
function Connections({ part, families }) {
  const joins = part.connections || [];
  if (joins.length === 0) return null;
  const blurb = (id) => families.find((f) => f.id === id)?.description;

  return (
    <div>
      <span className="eyebrow">HOW IT JOINS</span>
      <ul className="part-joins">
        {joins.map((c) => (
          <li key={c.id}>
            <div className="part-join-head">
              <strong>{c.name}</strong>
              {/* what it does once built — "rigid" is every part, so it is
                  only worth saying when the joint actually moves */}
              {c.motion && c.motion !== "rigid" && (
                <span className="chip chip--join">{c.does}</span>
              )}
              {(c.roles || []).map((r) => (
                <span key={r} className="chip">
                  {ROLE_WORDS[r] || r}
                </span>
              ))}
              {c.evidence === "name" && (
                <span className="chip chip--soft" title="Read from the part's catalogue name rather than measured from its geometry">
                  from the name
                </span>
              )}
            </div>
            <p>{blurb(c.id)}</p>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Everything the catalogue holds about one part, beside its render. */
function PartSheet({ part, families, onClose, onCopy, copied, onOpenPart, onBack, busy }) {
  const rows = [
    ["Part number", part.part_id],
    ["Category", part.category],
    ["Footprint", studs(part) || "not a rectangle of studs"],
    // Brick and plate heights are named only for parts that actually stack on
    // studs. An axle happens to be 12 LDU tall too, and calling it "a plate"
    // would say something about it that is not true.
    [
      "Height",
      part.attachment?.seats_on_studs && part.kind === "brick"
        ? "A brick (28 LDU)"
        : part.attachment?.seats_on_studs && part.kind === "plate"
          ? "A plate (12 LDU)"
          : part.size_ldu?.height != null
            ? `${part.size_ldu.height} LDU`
            : null,
    ],
    ["Size", millimetres(part.size_mm)],
    [
      "Size in LDU",
      part.size_ldu?.width != null
        ? `${part.size_ldu.width} × ${part.size_ldu.depth} × ${part.size_ldu.height}`
        : null,
    ],
    ["Stacks at", part.place_height_ldu != null ? `${part.place_height_ldu} LDU` : null],
    // counted from the part's own geometry, so it answers the question it looks
    // like it answers: whether anything can be built on top of this
    [
      "Studs on top",
      part.top_studs == null
        ? null
        : part.top_studs === 0
          ? "None — nothing stacks on this"
          : plural(part.top_studs, "stud"),
    ],
    // what has to already be in the model for this part to have anywhere to go
    ["To attach it", part.attachment?.summary],
    [
      "Used in",
      part.set_count
        ? `${plural(part.set_count, "set")}, ${part.total_uses} times` +
          (part.commonness ? ` — ${part.commonness.band}` : "")
        : null,
    ],
    ["File", part.dat_name],
    ["Author", part.author],
    ["Library", part.ldraw_org],
  ].filter(([, value]) => value != null && value !== "");

  return (
    <div className="modal" onClick={onClose} role="presentation">
      <div
        className="modal-card part-sheet"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label={part.description}
      >
        <div className="modal-head">
          {/* the way back up the chain of parts you followed to get here */}
          {onBack && (
            <button className="part-back" onClick={onBack} aria-label="Back to the previous part">
              ←
            </button>
          )}
          <div style={{ flex: 1, minWidth: 0 }}>
            <h2 className="modal-title">{part.description}</h2>
            <p className="modal-sub">
              <code>{part.part_id}</code> · {part.category}
            </p>
          </div>
          <button className="modal-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>

        {/* The part on the left, everything it connects to on the right. The
            two scroll independently: the panel is a list you work down while
            the part you are working on stays in view. */}
        <div className="part-sheet-body">
          <div className="part-sheet-main">
            <div className="part-sheet-art">
              <PartArt partId={part.part_id} />
            </div>

            <dl className="part-facts">
              {rows.map(([label, value]) => (
                <div key={label} className="part-fact">
                  <dt>{label}</dt>
                  <dd>{value}</dd>
                </div>
              ))}
            </dl>

            {part.keywords && (
              <div>
                <span className="eyebrow">ALSO KNOWN AS</span>
                <p className="part-keywords">{part.keywords}</p>
              </div>
            )}

            <div className="part-sheet-actions">
              <button className="btn btn--quiet" onClick={() => onCopy(part.part_id)}>
                {copied ? "Copied" : "Copy part number"}
              </button>
              {part.source_url && (
                <a
                  className="btn btn--quiet"
                  href={part.source_url}
                  target="_blank"
                  rel="noreferrer"
                >
                  On ldraw.org
                </a>
              )}
            </div>
          </div>

          <aside className="part-sheet-side">
            <Connections part={part} families={families} />
            <Companions part={part} onOpen={onOpenPart} busy={busy} />
            {!(part.connections || []).length && !(part.used_with || []).length && (
              <p className="part-company-note">
                Nothing is recorded about how this one joins to anything else.
              </p>
            )}
          </aside>
        </div>
      </div>
    </div>
  );
}

/** What real sets put beside this part. Each row opens that part. */
function Companions({ part, onOpen, busy }) {
  const rows = part.used_with || [];
  if (rows.length === 0) return null;

  return (
    <div>
      <span className="eyebrow">USUALLY BUILT WITH</span>
      <ul className="part-company">
        {rows.map((c) => (
          <li key={c.part_id}>
            <button
              className={`part-company-row ${busy === c.part_id ? "is-busy" : ""}`}
              onClick={() => onOpen(c.part_id)}
              title={`Open ${c.description || c.part_id}`}
            >
              <span className="part-company-bar" style={{ width: `${c.in_sets_pct}%` }} />
              <span className="part-company-pct">{c.in_sets_pct}%</span>
              <span className="part-company-name">{c.description || c.part_id}</span>
              <span className="mono part-company-id">{c.part_id}</span>
            </button>
          </li>
        ))}
      </ul>
      <p className="part-company-note">
        Of the sets that use this part, how many also use that one. Click one to open it.
      </p>
    </div>
  );
}

export default function PartsGallery() {
  const [query, setQuery] = useState("");
  const [typed, setTyped] = useState("");
  const [category, setCategory] = useState("");
  const [kind, setKind] = useState("");
  const [width, setWidth] = useState("");
  const [depth, setDepth] = useState("");
  const [studsOnly, setStudsOnly] = useState(false);
  const [sort, setSort] = useState("relevance");

  const [connection, setConnection] = useState("");
  const [categories, setCategories] = useState([]);
  const [vocabulary, setVocabulary] = useState({});
  const families = vocabulary.families || [];
  const familyName = (id) => families.find((f) => f.id === id)?.name || id;
  const [page, setPage] = useState({ results: [], total: 0, offset: 0 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  // The parts you have followed to get here, last one showing. Opening a
  // companion pushes; the back arrow pops. Without it, following a rim to its
  // tyre and back means finding the rim in the grid again.
  const [trail, setTrail] = useState([]);
  const [opening, setOpening] = useState(null);
  const [copied, setCopied] = useState(null);
  const open = trail[trail.length - 1] || null;

  // Typing is not a search. Waiting a beat turns a five-letter word into one
  // request instead of five, and the list stops flickering through the
  // half-typed matches on the way.
  useEffect(() => {
    const id = setTimeout(() => setQuery(typed), 220);
    return () => clearTimeout(id);
  }, [typed]);

  useEffect(() => {
    api.partCategories().then(setCategories).catch(() => setCategories([]));
    api.partConnections().then(setVocabulary).catch(() => setVocabulary({}));
  }, []);

  const filters = useMemo(
    () => ({
      query,
      category,
      kind,
      width_studs: width,
      depth_studs: depth,
      has_studs: studsOnly ? true : "",
      connection,
      // "best match" means nothing without something to match against
      sort: !query && sort === "relevance" ? "popular" : sort,
    }),
    [query, category, kind, width, depth, studsOnly, connection, sort]
  );

  // The request in flight, so a slow early page cannot land on top of a fast
  // later one and show results for a search nobody is running any more.
  const latest = useRef(0);

  const load = useCallback(
    async (offset) => {
      const ticket = ++latest.current;
      setLoading(true);
      try {
        const next = await api.browseParts({ ...filters, limit: PAGE, offset });
        if (ticket !== latest.current) return;
        setError(null);
        setPage((current) =>
          offset === 0
            ? next
            : { ...next, results: [...current.results, ...next.results] }
        );
      } catch (e) {
        if (ticket === latest.current) setError(e.message);
      } finally {
        if (ticket === latest.current) setLoading(false);
      }
    },
    [filters]
  );

  useEffect(() => {
    load(0);
  }, [load]);

  /** Follow a companion. The grid row is a summary; the sheet needs the lot. */
  const openPart = async (partId) => {
    if (opening) return;
    setOpening(partId);
    try {
      const full = await api.partDetails(partId);
      setTrail((t) => [...t, full]);
    } catch (e) {
      setError(e.message);
    } finally {
      setOpening(null);
    }
  };

  const copy = (partId) => {
    navigator.clipboard?.writeText(partId).catch(() => {});
    setCopied(partId);
    setTimeout(() => setCopied(null), 1400);
  };

  const shown = page.results;
  const more = shown.length < page.total;
  const narrowed = query || category || kind || width || depth || studsOnly || connection;

  return (
    <div className="gallery">
      <div className="gallery-head">
        <div>
          <h2 className="gallery-title">The parts bin</h2>
          <p className="gallery-sub">
            {loading && shown.length === 0
              ? "Opening the catalogue…"
              : `${plural(page.total, "part")} the agent can build with` +
                (narrowed ? ", matching what you asked for" : "")}
          </p>
        </div>
        <div className="gallery-tools">
          <div className="rail-search gallery-search">
            <input
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              placeholder="brick 2x4, slope, hinge, 3001…"
              aria-label="Search parts"
            />
          </div>
          <select
            className="select"
            value={sort}
            onChange={(e) => setSort(e.target.value)}
            aria-label="Sort parts"
          >
            {SORTS.map((s) => (
              <option key={s.id} value={s.id}>
                {s.label}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="part-filters">
        <div className="segmented" role="group" aria-label="Shape">
          {KINDS.map((k) => (
            <button key={k.id} aria-pressed={kind === k.id} onClick={() => setKind(k.id)}>
              {k.label}
            </button>
          ))}
        </div>

        <select
          className="select"
          value={category}
          onChange={(e) => setCategory(e.target.value)}
          aria-label="Category"
        >
          <option value="">Every category</option>
          {categories.map((c) => (
            <option key={c.name} value={c.name}>
              {c.name} ({c.count})
            </option>
          ))}
        </select>

        {/* Three resolutions in one control, because a builder arrives with
            whichever they have: the system, the behaviour they need, or the
            exact family. Grouped so the middle option is a real category
            rather than "everything that is not a stud". */}
        <select
          className="select select--wide"
          value={connection}
          onChange={(e) => setConnection(e.target.value)}
          aria-label="How it connects"
        >
          <option value="">How it connects…</option>
          {(vocabulary.groups || []).map((g) => (
            <optgroup key={g.id} label={g.name}>
              <option value={g.id}>All — {g.name.toLowerCase()}</option>
              {g.families.map((fid) => (
                <option key={fid} value={fid}>
                  {"  "}
                  {familyName(fid)}
                </option>
              ))}
            </optgroup>
          ))}
          <optgroup label="By what the joint does">
            {(vocabulary.motions || [])
              .filter((m) => m.id !== "rigid")
              .map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name}
                </option>
              ))}
          </optgroup>
        </select>

        <label className="part-size">
          <span>Studs</span>
          <input
            type="number"
            min="1"
            max="48"
            value={width}
            onChange={(e) => setWidth(e.target.value)}
            placeholder="w"
            aria-label="Width in studs"
          />
          <span>×</span>
          <input
            type="number"
            min="1"
            max="48"
            value={depth}
            onChange={(e) => setDepth(e.target.value)}
            placeholder="d"
            aria-label="Depth in studs"
          />
        </label>

        <button
          className={`chip chip--toggle ${studsOnly ? "chip--on" : ""}`}
          aria-pressed={studsOnly}
          onClick={() => setStudsOnly((v) => !v)}
        >
          studs on top
        </button>

        {narrowed && (
          <button
            className="btn btn--icon part-clear"
            title="Clear every filter"
            onClick={() => {
              setTyped("");
              setCategory("");
              setKind("");
              setWidth("");
              setDepth("");
              setStudsOnly(false);
              setConnection("");
            }}
          >
            ×
          </button>
        )}
      </div>

      {error && <div className="gallery-error">{error}</div>}

      {!loading && shown.length === 0 && (
        <div className="gallery-empty">
          <BrickLogo hero />
          <h3>No part like that</h3>
          <p>
            Nothing in the catalogue matches all of that at once. Drop a filter, or try the
            word the LDraw catalogue would use — “slope”, “bracket”, “tile”.
          </p>
        </div>
      )}

      <div className="gallery-grid">
        {shown.map((part) => (
          <article
            key={part.part_id}
            className="card"
            role="button"
            tabIndex={0}
            onClick={() => setTrail([part])}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                setTrail([part]);
              }
            }}
          >
            <div className="card-art">
              <PartArt partId={part.part_id} />
              <span className="card-flag">{part.part_id}</span>
            </div>

            <div className="card-body">
              <div className="card-head">
                <h3 className="card-name">{part.description}</h3>
              </div>
              <div className="card-tags">
                {part.category && <span className="chip">{part.category}</span>}
                {studs(part) && <span className="chip">{studs(part)}</span>}
                {/* Only the connections worth noticing: every brick has studs.
                    A family whose name is already the category — a Turntable in
                    Turntable — is dropped rather than printed twice. */}
                {(part.special_connections || [])
                  .filter((id) => familyName(id) !== part.category)
                  .map((id) => (
                    <span key={id} className="chip chip--join">
                      {familyName(id)}
                    </span>
                  ))}
              </div>
              <div className="card-meta">
                <span>{millimetres(part.size_mm) || "size unknown"}</span>
                {part.total_uses > 0 && <span>{part.total_uses.toLocaleString()} uses</span>}
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
        <PartSheet
          part={open}
          families={families}
          onClose={() => setTrail([])}
          onBack={trail.length > 1 ? () => setTrail((t) => t.slice(0, -1)) : null}
          onOpenPart={openPart}
          busy={opening}
          onCopy={copy}
          copied={copied === open.part_id}
        />
      )}
    </div>
  );
}
