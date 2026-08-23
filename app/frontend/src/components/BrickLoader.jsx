import { BRICK } from "../brick";

/**
 * The waiting state for the viewer: four System bricks flying in from the
 * corners and clicking together into a 2×2 stack, on repeat.
 *
 * It covers the canvas outright rather than sitting beside it. The viewer keeps
 * the previous project's model on screen until the new one finishes parsing, and
 * a stale build that looks like the real thing is worse than an honest wait — so
 * this hides it until there is something true to show.
 */

// grid order — top-left, top-right, bottom-left, bottom-right — each with the
// corner it flies in from. The order they arrive in is the CSS's business
// (.brick-loader-stack i:nth-child), not this list's.
const BRICKS = [
  { colour: BRICK.red, from: "-96px, -64px" },
  { colour: BRICK.yellow, from: "96px, -64px" },
  { colour: BRICK.green, from: "-96px, 64px" },
  { colour: BRICK.blue, from: "96px, 64px" },
];

export default function BrickLoader({ label = "Snapping the bricks together…" }) {
  return (
    <div className="brick-loader" role="status" aria-live="polite">
      <div className="brick-loader-stack" aria-hidden="true">
        {BRICKS.map((b) => (
          <i key={b.colour} style={{ background: b.colour, "--from": b.from }} />
        ))}
      </div>
      <p className="brick-loader-label">{label}</p>
    </div>
  );
}
