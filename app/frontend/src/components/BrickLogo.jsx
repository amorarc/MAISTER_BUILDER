import { BRICK } from "../brick";

/** The mark: a 2×2 brick in the four System colours, seen from above. */
export default function BrickLogo({ size = 28, hero = false }) {
  return (
    <div
      className={`brick-logo ${hero ? "brick-logo--hero" : ""}`}
      style={hero ? undefined : { "--size": `${size}px` }}
      aria-hidden="true"
    >
      <i style={{ background: BRICK.red }} />
      <i style={{ background: BRICK.yellow }} />
      <i style={{ background: BRICK.green }} />
      <i style={{ background: BRICK.blue }} />
    </div>
  );
}
