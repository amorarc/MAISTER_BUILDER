/**
 * The physical-brick design language: the four System colours everything is
 * built from, plus the maths used to fake moulded plastic (a darker bevel
 * underneath, a lighter lip on top).
 */

export const BRICK = {
  red: "#E3000B",
  blue: "#0B5FBE",
  yellow: "#F6C700",
  green: "#00963E",
};

/** The rotation used whenever a sequence of things needs to look like bricks. */
export const BRICK_CYCLE = [BRICK.red, BRICK.blue, BRICK.yellow, BRICK.green];

/** Multiply a hex colour by `f` - under 1 darkens (bevel), over 1 lightens. */
export function shade(hex, f) {
  const n = parseInt(hex.slice(1), 16);
  const ch = (shift) => Math.min(255, Math.round(((n >> shift) & 255) * f));
  return `rgb(${ch(16)},${ch(8)},${ch(0)})`;
}

/**
 * The colours an *object* brick can come out, as opposed to the four the app
 * is built from.
 *
 * A subconstruction's name rides on a brick standing on the tool call it
 * belongs to, and that call is already red, green, yellow or blue for its
 * status - so naming objects out of the same four would put a green brick on a
 * green brick often enough to matter. These are real LDraw System colours from
 * outside that set: still unmistakably LEGO, never the colour underneath.
 */
export const OBJECT_BRICKS = [
  "#FE8A18", // 25  Orange
  "#901F76", // 26  Magenta
  "#BBE90B", // 27  Lime
  "#078BC9", // 321 Dark Azure
  "#720E0F", // 320 Dark Red
  "#A0BCAC", // 378 Sand Green
  "#7396C8", // 73  Medium Blue
  "#AA7D55", // 84  Medium Nougat
  "#184632", // 288 Dark Green
  "#FCAC00", // 191 Bright Light Orange
  "#582A12", // 70  Reddish Brown
  "#E4ADC8", // 29  Bright Pink
];

/**
 * Black text or white, by how bright the colour actually is.
 *
 * It used to special-case yellow, which was right while there were four
 * colours and only one of them was pale. The object palette has lime and sand
 * green in it, and both need dark text for the same reason yellow does. The
 * threshold is set so the original four still read exactly as they did.
 */
export function inkOn(hex) {
  const n = parseInt(hex.slice(1), 16);
  const luma =
    0.2126 * ((n >> 16) & 255) +
    0.7152 * ((n >> 8) & 255) +
    0.0722 * (n & 255);
  return luma > 145 ? "#17191E" : "#ffffff";
}

function hash(id) {
  let h = 0;
  for (let i = 0; i < String(id).length; i += 1) {
    h = (h * 31 + String(id).charCodeAt(i)) >>> 0;
  }
  return h;
}

/** Stable per-project colour, so a project keeps its tile between reloads. */
export function colourFor(id) {
  return BRICK_CYCLE[hash(id) % BRICK_CYCLE.length];
}

/**
 * The colour of one object's brick, picked from its name.
 *
 * From the name rather than at random: the tree's brick is the same colour in
 * every row it appears in, and the same colour again tomorrow. A colour that
 * changed per render would be decoration; this one is how you follow a single
 * builder down a log where three of them are interleaved.
 */
export function objectColourFor(name) {
  return OBJECT_BRICKS[hash(name) % OBJECT_BRICKS.length];
}

/** Inline custom properties consumed by `.tile`, `.thumb`, `.mark`… */
export function brickVars(colour) {
  return {
    "--brick": colour,
    "--brick-bevel": shade(colour, 0.58),
    "--brick-lip": shade(colour, 1.06),
    "--brick-ink": inkOn(colour),
  };
}
