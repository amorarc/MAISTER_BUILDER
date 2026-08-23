import { useEffect, useState } from "react";
import { api } from "../api";
import Studs, { TILE_PITCH } from "./Studs";

// --------------------------------------------------------------------------
// Moulding the brick out of the picture
//
// A stud in the frame's grey is a stud belonging to some other brick that
// happens to be holding this picture. A stud takes the colour of the plastic
// it is moulded out of, and the plastic here is the photograph — so each one
// is painted the average of the pixels it is sitting directly above.
//
// Both edges are sampled, because a brick has two of them. The top band paints
// the studs: those are the pixels nearest them, and a red roof filling the top
// of a picture should give red studs even when the rest of it is green. The
// bottom band paints the lip under the brick and the caption below it, which is
// the same argument the other way up.
//
// The measuring itself happens **on the backend**, once, when the picture is
// attached: `edges` arrives with the record and this file only decides what to
// paint with it. A picture's edge colours cannot change after the file is
// written, so re-deriving them in every browser on every page load was work
// done again to get the same answer — and work that could fail, which is how a
// brick ended up grey after a reload. See `edge_colours` in
// maister/agent/reference.py.
//
// The canvas path below stays for a chip shown from a bare `src` with no record
// behind it, which has nothing to read the colours from.
// --------------------------------------------------------------------------

/** Columns sampled across each edge. More than there are ever studs. */
const SAMPLES = 24;

/** How far into the picture still counts as "at the edge". */
const BAND = 0.16;

// url -> Promise<{top, bottom} | null>. One sample per picture however many
// chips show it: the same reference appears in the composer and again in every
// turn of the conversation that carried it.
const sampled = new Map();

/** Decode one image, by URL. */
function load(src) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error(`could not decode ${src}`));
    img.src = src;
  });
}

/**
 * Read the two edge bands out of the picture.
 *
 * Fetched as bytes and decoded from a `blob:` URL rather than pointed at the
 * backend directly, because the backend is a different origin (:8000 to the
 * dev server's :5173) and a canvas that has drawn a cross-origin image refuses
 * to hand its pixels back. `crossOrigin="anonymous"` is the usual answer and
 * is not a reliable one here: the visible <img> asks for the same URL without
 * it, the two requests differ only in CORS mode, and whichever lands in the
 * cache first can be served to the other — after which the read throws and
 * every brick comes out grey. A blob is same-origin by construction, so this
 * cannot happen at all.
 */
async function readEdges(url) {
  const response = await fetch(url, { credentials: "omit" });
  if (!response.ok) return null;

  const blobUrl = URL.createObjectURL(await response.blob());
  try {
    const img = await load(blobUrl);

    // The visible <img> is `object-fit: cover` inside a square, so it is
    // centre-cropped to its short edge. Sample the same crop, or the brick is
    // coloured from pixels the user cannot see.
    const side = Math.min(img.naturalWidth, img.naturalHeight);
    const sx = (img.naturalWidth - side) / 2;
    const sy = (img.naturalHeight - side) / 2;
    const band = Math.max(1, side * BAND);

    const canvas = document.createElement("canvas");
    canvas.width = SAMPLES;
    canvas.height = 2;
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    ctx.imageSmoothingQuality = "high";
    // Squashing a band to a single row IS the averaging: each pixel that comes
    // out is the mean of the strip of picture behind it. Row 0 is the top of
    // the picture, row 1 the bottom.
    ctx.drawImage(img, sx, sy, side, band, 0, 0, SAMPLES, 1);
    ctx.drawImage(img, sx, sy + side - band, side, band, 0, 1, SAMPLES, 1);

    const { data } = ctx.getImageData(0, 0, SAMPLES, 2);
    const row = (n) => {
      const out = [];
      const base = n * SAMPLES * 4;
      for (let i = 0; i < SAMPLES; i += 1) {
        out.push([data[base + i * 4], data[base + i * 4 + 1],
                  data[base + i * 4 + 2]]);
      }
      return out;
    };
    return { top: row(0), bottom: row(1) };
  } finally {
    // The picture is on screen from its own URL; this copy existed only to be
    // measured, and holding it would keep the bytes alive for the session.
    URL.revokeObjectURL(blobUrl);
  }
}

function sampleEdges(url) {
  if (!sampled.has(url)) {
    // A picture that cannot be read is not an error worth showing — the brick
    // falls back to the frame's grey, which is what it always used to be.
    sampled.set(url, readEdges(url).catch(() => null));
  }
  return sampled.get(url);
}

/** Two rows of `[r, g, b]`, or null for anything that is not that. */
function asEdges(value) {
  if (!value || !Array.isArray(value.top) || !Array.isArray(value.bottom)) {
    return null;
  }
  return value.top.length && value.bottom.length ? value : null;
}

/**
 * The picture's edge colours — recorded if the backend measured them, read off
 * the picture if it did not.
 *
 * A record that carries them costs nothing at all: no request, no canvas, and
 * no state to settle, so the brick is moulded on its first paint rather than
 * flashing grey and then filling in.
 */
function useEdgeColours(url, record) {
  const recorded = asEdges(record);
  const [edges, setEdges] = useState(null);
  useEffect(() => {
    setEdges(null);
    if (recorded || !url) return undefined;
    let alive = true;
    sampleEdges(url).then((e) => alive && setEdges(e));
    return () => {
      alive = false;
    };
    // The colours themselves are not a dependency — only whether there are any,
    // since a record arriving is what calls off the measurement.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url, Boolean(recorded)]);
  return recorded || edges;
}

const rgb = ([r, g, b]) => `rgb(${r}, ${g}, ${b})`;

/** The mean of a row of samples. */
function mean(columns) {
  const total = columns.reduce(
    (acc, c) => [acc[0] + c[0], acc[1] + c[1], acc[2] + c[2]], [0, 0, 0]);
  return total.map((v) => Math.round(v / columns.length));
}

/** Multiply a sampled colour — under 1 darkens it into a bevel. */
const scale = (c, f) => c.map((v) => Math.min(255, Math.round(v * f)));

/**
 * The same colour, lifted until it can be read as text on the dark chrome.
 *
 * A photograph of a night sky averages to almost black, and a caption in
 * almost-black under a dark brick is a caption nobody can see. The hue is what
 * carries the meaning here, so it is kept and only the brightness is moved.
 */
function legible(c, floor = 150) {
  const luma = 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
  if (luma >= floor) return c;
  // Brightening is a multiply, and nothing multiplies black into grey. A band
  // that is exactly black — a letterboxed screenshot, a black bar — has no hue
  // to preserve, so it goes to neutral at the floor rather than staying black.
  if (luma < 1) return [floor, floor, floor];
  return scale(c, floor / luma);
}

/**
 * The bottom edge across the brick's width, as a lip you can see.
 *
 * Barely darkened, and floored. A real lip is the shaded underside of the
 * plastic, but the point of this one is that you can tell what colour it came
 * from — and it sits against dark chrome rather than against the picture, so a
 * photograph with a night sky along the bottom would otherwise paint a black
 * strip indistinguishable from no colour at all. The floor is low enough that
 * a dark picture still reads as dark; it only stops it reading as absent.
 */
function lipGradient(columns) {
  const stops = columns.map((c) => rgb(scale(legible(c, 62), 0.92))).join(", ");
  return `linear-gradient(to right, ${stops})`;
}

/** The same edge again, darker, for the bevel the brick stands on. */
function bevelColour(columns) {
  return rgb(scale(legible(mean(columns), 62), 0.55));
}

/**
 * The colour for stud `i` of `n`, as the mean of the columns around it.
 *
 * The studs are spread across the brick's width, so stud i sits at roughly
 * `(i + 0.5) / n` of the way along — near enough, since the point is that a
 * stud over the red part of a picture comes out red. Three columns rather than
 * one so a single bright pixel cannot decide it.
 */
function colourAt(columns, i, n) {
  const at = Math.round(((i + 0.5) / n) * (columns.length - 1));
  let r = 0;
  let g = 0;
  let b = 0;
  let seen = 0;
  for (let j = at - 1; j <= at + 1; j += 1) {
    const c = columns[j];
    if (!c) continue;
    r += c[0];
    g += c[1];
    b += c[2];
    seen += 1;
  }
  if (!seen) return undefined;
  return `rgb(${Math.round(r / seen)}, ${Math.round(g / seen)}, ${Math.round(b / seen)})`;
}


/**
 * A reference image, shown as a LEGO brick.
 *
 * The picture sits inside a brick rather than being dropped into the chat as a
 * bare thumbnail: it is a thing the project now owns, and it should look like
 * it belongs on the workbench.
 *
 * The studs are the same ones a tool call wears — the shared `Studs`, counted
 * from the chip's own width, sitting proud of the top edge. Reused rather than
 * restyled so a reference reads as the same kind of object as everything else
 * on the page: the wide chip comes out a four-stud brick and the small one a
 * two-stud brick, which is what a brick that size would be. Each stud is
 * coloured from the picture beneath it, and the lip under the brick and the
 * caption below it are coloured from the picture above them (see above), so
 * the brick looks moulded out of the reference rather than merely holding it.
 *
 * The square is the point. A photograph is any shape at all, and letting it
 * set the size of the chip makes a column of them ragged — so the frame is
 * fixed and the image is covered into it, centre-cropped, never squashed.
 * `object-fit: cover` is what keeps the format: it fills the square by
 * overflowing the long edge instead of distorting the short one.
 */
export default function ReferenceChip({
  projectId,
  image,
  src,
  onRemove,
  pending = false,
  size = "md",
}) {
  const url = src || (projectId && image ? api.referenceUrl(projectId, image.image_id) : null);
  const label = image?.original_name || "reference image";

  // Null when the picture could not be measured: an unsampled brick falls back
  // to the frame's grey rather than to nothing.
  const edges = useEdgeColours(url, image?.edges);
  const tint = edges ? (i, n) => colourAt(edges.top, i, n) : undefined;

  // The lower half of the brick, moulded out of the lower edge of the picture:
  // the lip across the bottom keeps the picture's own left-to-right colours,
  // and the caption takes their mean, lifted until it can be read.
  const below = edges
    ? {
        "--lip": lipGradient(edges.bottom),
        "--lip-bevel": bevelColour(edges.bottom),
        "--cap-ink": rgb(legible(mean(edges.bottom))),
      }
    : undefined;

  return (
    <figure
      className={
        `refchip refchip--${size}` +
        (pending ? " is-pending" : "") +
        (below ? " is-moulded" : "")
      }
      style={below}
    >
      <div className="refchip-brick">
        <Studs pitch={TILE_PITCH} inset={10} tint={tint} />
        {url ? (
          <img className="refchip-img" src={url} alt={label} loading="lazy" />
        ) : (
          <span className="refchip-empty" aria-hidden="true" />
        )}
        {pending && <span className="refchip-spinner" aria-hidden="true" />}
        {/* the brick's bottom lip, in the colours of the picture standing on it */}
        <span className="refchip-lip" aria-hidden="true" />
        {onRemove && !pending && (
          <button
            type="button"
            className="refchip-x"
            onClick={onRemove}
            aria-label="Remove this reference image"
            title="Remove"
          >
            ✕
          </button>
        )}
      </div>
      <figcaption className="refchip-cap">{pending ? "attaching…" : "reference"}</figcaption>
    </figure>
  );
}
