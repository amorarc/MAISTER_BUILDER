/**
 * The build check, as colours and as sets of parts to light up.
 *
 * The rail counts four things about the model on the stud grid. A count on its
 * own is a number you have to go and find on the build - "2 unverified" over a
 * two-hundred-part model is a needle and no haystack. Hovering a count paints
 * the parts it counted, on the model, in the colour of the dot beside it.
 *
 * The report the backend sends carries `connectivity.part_index`: one row per
 * part, each with the verdict it was counted under and which stud-connected
 * clump it belongs to. Complete rather than the capped sample the agent is
 * shown - see validation._part_index for why that distinction matters here.
 */

/**
 * One colour per count, matching the dot in the rail.
 *
 * The three verdicts wear the app's own status colours, which are the LEGO
 * System colours the rest of the UI is built from. Sub-assemblies are blue:
 * the count is not a fault - a scene is *meant* to have a tree standing apart
 * from a car - so it takes the one status colour that does not read as alarm.
 */
export const CHECKS = {
  connected: { label: "Connected", tone: "ok", colour: "#00a34a" },
  misaligned: { label: "Misaligned", tone: "err", colour: "#e3000b" },
  unverified: { label: "Unverified", tone: "warn", colour: "#f6c700" },
  subassemblies: { label: "Sub-assemblies", tone: "info", colour: "#0b5fbe" },
};

/**
 * Twenty colours for twenty clumps, in the order they get handed out.
 *
 * Blue first, so a model in one piece glows the same blue as the dot beside
 * the count and the second clump is the first colour that is *not* blue -
 * which is the moment the count starts saying something.
 *
 * The rest were not chosen by eye. They come out of a farthest-point search
 * over ~1,100 candidates in OKLCH - every one inside sRGB, above a chroma
 * floor so it reads as a colour rather than a grey, and above 2:1 contrast
 * against the workbench so it is visible on the stage - taking at each step
 * whichever candidate sits furthest from every colour already picked, scored
 * on the worse of normal vision and simulated protanopia/deuteranopia.
 *
 * **Twenty distinguishable colours do not exist, and this list does not
 * pretend otherwise.** Measured as OKLab ΔE×100 over every pair, worst case:
 *
 *     first 5      ΔE 26.9 normal / 13.9 colour-blind    unmistakable
 *     first 8      ΔE 15.9 / 7.8                         passes the full check
 *     first 12     ΔE 11.6 / 6.1                         tellable, close
 *     all 20       ΔE  8.4 / 4.3                         colour alone fails
 *
 * Which is survivable *here* and would not be on a chart, because a clump is
 * a contiguous lump of bricks in 3D: past a dozen, what says two parts belong
 * together is that they are touching, and the colour only has to separate a
 * clump from its neighbours rather than from all nineteen others.
 */
export const CLUMP_COLOURS = [
  "#0b5fbe", // blue - the main body, and the dot in the rail
  "#fb8122", // orange
  "#9f0c33", // crimson
  "#d173f4", // orchid
  "#21bb98", // jade
  "#85710e", // olive
  "#db3d7e", // raspberry
  "#972198", // purple
  "#1995c6", // azure
  "#845de7", // violet
  "#138565", // pine
  "#fc8395", // salmon
  "#49b72c", // leaf
  "#944809", // russet
  "#bd7715", // bronze
  "#ec5eb9", // pink
  "#db373d", // red
  "#469efa", // sky
  "#ba39a3", // magenta
  "#21b3d6", // cyan
];

/**
 * What to light up for one check, as `[{ colour, refs }]`.
 *
 * `refs` are `{ part, at }` - the part file and where it ended up in world
 * space - which is what the viewer matches its loaded objects against.
 *
 * Empty for a check with nothing behind it, and empty for a report from a
 * backend that did not send the index, so a stale tab lights nothing rather
 * than lighting the wrong thing.
 */
export function checkGroups(validation, kind) {
  const index = validation?.connectivity?.part_index;
  if (!index || !kind) return [];

  if (kind === "subassemblies") {
    // One group per clump, in the order the checker found them - largest
    // first, so the main body is clump 0 and takes blue. Past twenty the
    // colours repeat; a model in twenty-one pieces has a bigger problem than
    // two of them sharing a blue.
    const clumps = new Map();
    for (const row of index) {
      const n = row.clump ?? 0;
      if (!clumps.has(n)) clumps.set(n, []);
      clumps.get(n).push(row);
    }
    return [...clumps.entries()]
      .sort((a, b) => a[0] - b[0])
      .map(([n, refs]) => ({ colour: CLUMP_COLOURS[n % CLUMP_COLOURS.length], refs }));
  }

  const refs = index.filter((row) => row.state === kind);
  return refs.length ? [{ colour: CHECKS[kind]?.colour || "#0b5fbe", refs }] : [];
}
