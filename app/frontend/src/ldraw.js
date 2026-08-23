import { api } from "./api";

/**
 * Just enough LDraw to render the source editor and the parts list. The real
 * parsing lives in the backend; this only has to be good enough to colour a
 * line and count what the model uses.
 */

// The LDraw System palette, trimmed to the codes that actually turn up in
// generated models. Anything unknown falls back to plastic grey.
const COLOURS = {
  0: "#05131D", 1: "#0055BF", 2: "#237841", 3: "#008F9B", 4: "#C91A09",
  5: "#C870A0", 6: "#583927", 7: "#9BA19D", 8: "#6D6E5C", 9: "#B4D2E3",
  10: "#4B9F4A", 11: "#55A5AF", 12: "#F2705E", 13: "#FC97AC", 14: "#F2CD37",
  15: "#FFFFFF", 16: "#A0A5A9", 17: "#C2DAB8", 18: "#FBE696", 19: "#E4CD9E",
  20: "#C9CAE2", 22: "#81007B", 23: "#2032B0", 25: "#FE8A18", 26: "#923978",
  27: "#BBE90B", 28: "#958A73", 29: "#E4ADC8", 30: "#AC78BA", 31: "#E1D5ED",
  68: "#F3CF9B", 69: "#CD6298", 70: "#582A12", 71: "#A0A5A9", 72: "#6C6E68",
  73: "#5A93DB", 74: "#73DCA1", 77: "#FECCCF", 78: "#F6D7B3", 84: "#CC702A",
  85: "#3F3691", 86: "#7C503A", 89: "#4C61DB", 92: "#D09168", 100: "#FEBABD",
  110: "#4354A3", 112: "#6874CA", 115: "#C7D23C", 118: "#B3D7D1", 120: "#D9E4A7",
  125: "#F9BA61", 151: "#E6E3DA", 191: "#F8BB3D", 212: "#86C1E1", 216: "#B31004",
  226: "#FFF03A", 232: "#7DBFDD", 272: "#0A3463", 288: "#184632", 308: "#352100",
  320: "#720E0F", 321: "#469BC3", 322: "#68C3E2", 323: "#D3F2EA", 326: "#E2F99A",
  335: "#D67572", 351: "#F785B1", 366: "#FA9C1C", 373: "#75657D", 378: "#708E7C",
  379: "#70819A", 462: "#FFA70B", 484: "#91501C", 503: "#BCB4A5",
};

export function colourHex(code) {
  return COLOURS[Number(code)] || "#9BA19D";
}

// --------------------------------------------------------------------------
// Syntax highlighting
// --------------------------------------------------------------------------

/**
 * Split one line into `{ text, tone }` runs. Whitespace is kept verbatim so the
 * highlighted copy stays glued to the textarea it sits under.
 */
export function highlight(line) {
  if (!line) return [];

  const kind = line.trimStart()[0];

  if (kind === "0") {
    const meta = /^\s*0\s+(STEP|FILE|NOFILE|!\w+)/.test(line);
    return [{ text: line, tone: meta ? "meta" : "comment" }];
  }

  if (kind !== "1") {
    // type 2-5: edges and surfaces, rare in generated models
    return [{ text: line, tone: "comment" }];
  }

  // 1 <colour> x y z  a b c  d e f  g h i  part.dat
  const runs = [];
  let field = 0;
  for (const chunk of line.split(/(\s+)/)) {
    if (!chunk) continue;
    if (/^\s+$/.test(chunk)) {
      runs.push({ text: chunk, tone: "plain" });
      continue;
    }
    if (field === 0) runs.push({ text: chunk, tone: "type" });
    else if (field === 1) runs.push({ text: chunk, tone: "colour" });
    else if (field <= 13) runs.push({ text: chunk, tone: "number" });
    else runs.push({ text: chunk, tone: "part" });
    field += 1;
  }
  return runs;
}

// --------------------------------------------------------------------------
// One line, as a piece
// --------------------------------------------------------------------------

/**
 * The part a line places, or null if the line places nothing.
 *
 * `1 <colour> x y z  a b c  d e f  g h i  part.dat`. Only the colour, the
 * position and the file are read: the nine numbers of the rotation say how the
 * piece is turned, not *which* piece it is, and finding it in the render only
 * needs to know where it was put.
 */
export function partRef(line) {
  const f = String(line || "").trim().split(/\s+/);
  if (f[0] !== "1" || f.length < 15) return null;
  const x = Number(f[2]);
  const y = Number(f[3]);
  const z = Number(f[4]);
  if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(z)) {
    return null;
  }
  return { file: f.slice(14).join(" "), colour: Number(f[1]), x, y, z };
}

// --------------------------------------------------------------------------
// Parts inventory
// --------------------------------------------------------------------------

/**
 * Count the type-1 references in a model, heaviest first. Sub-model references
 * (anything not ending in `.dat`) are skipped — they are not real parts.
 */
export function inventory(source) {
  const counts = new Map();

  for (const line of (source || "").split("\n")) {
    const t = line.trim();
    if (!t.startsWith("1 ")) continue;
    const f = t.split(/\s+/);
    if (f.length < 15) continue;

    const file = f.slice(14).join(" ");
    if (!/\.dat$/i.test(file)) continue;

    const key = `${f[1]}|${file.toLowerCase()}`;
    const row = counts.get(key);
    if (row) row.qty += 1;
    else counts.set(key, { file, colour: f[1], id: file.replace(/\.dat$/i, ""), qty: 1 });
  }

  return [...counts.values()].sort((a, b) => b.qty - a.qty || a.id.localeCompare(b.id));
}

// A part file's first line is its description ("0 Brick  2 x  4"). Worth one
// small fetch per distinct part, cached for the life of the page.
const names = new Map();

export async function partName(file) {
  const key = file.toLowerCase();
  if (names.has(key)) return names.get(key);

  const pending = (async () => {
    try {
      const res = await fetch(`${api.libraryPath()}parts/${encodeURIComponent(file)}`);
      if (!res.ok) return null;
      const first = (await res.text()).split("\n", 1)[0];
      const desc = first.replace(/^\s*0\s+/, "").replace(/\s+/g, " ").trim();
      return desc || null;
    } catch {
      return null;
    }
  })();

  names.set(key, pending);
  return pending;
}
