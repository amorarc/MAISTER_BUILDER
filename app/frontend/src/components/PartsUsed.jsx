import { useEffect, useMemo, useState } from "react";
import { colourHex, inventory, partName } from "../ldraw";

const LOOKUP_LIMIT = 24; // how many descriptions are worth fetching

/** What the current source actually uses, heaviest part first. */
export default function PartsUsed({ source }) {
  const rows = useMemo(() => inventory(source), [source]);
  const key = rows.map((r) => r.file).join(",");
  const [names, setNames] = useState({});

  useEffect(() => {
    let alive = true;
    const wanted = key ? key.split(",").slice(0, LOOKUP_LIMIT) : [];
    Promise.all(wanted.map(async (file) => [file, await partName(file)])).then((pairs) => {
      if (!alive) return;
      setNames(Object.fromEntries(pairs.filter(([, n]) => n)));
    });
    return () => {
      alive = false;
    };
  }, [key]);

  return (
    <div className="parts-card">
      <div className="eyebrow">PARTS USED</div>
      {rows.length === 0 ? (
        <div className="parts-empty">No parts referenced yet.</div>
      ) : (
        <div className="parts-list">
          {rows.map((r) => (
            <div className="parts-row" key={`${r.colour}|${r.file}`}>
              <span className="parts-sw" style={{ background: colourHex(r.colour) }} />
              <span className="parts-name" title={r.file}>
                {r.id}
                {names[r.file] ? ` ${names[r.file]}` : ""}
              </span>
              <span className="parts-qty">{r.qty}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
