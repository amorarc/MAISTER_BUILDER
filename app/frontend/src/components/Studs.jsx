import { useLayoutEffect, useRef, useState } from "react";

/**
 * A stud every this many pixels of brick. A real brick is 20 LDU between studs
 * on a face 24 LDU tall, so the pitch is a little under the height of one
 * course - this is that ratio at the size a tool-call brick is drawn.
 */
export const STUD_PITCH = 34;

/** The same ratio for the small word bricks a reply is built from. */
export const TILE_PITCH = 17;

// One observer for every brick on the page rather than one each: a long reply
// is a few hundred word bricks, and a few hundred ResizeObservers created and
// torn down as they land is a cost for nothing.
let observer = null;
const watchers = new Map();

function watch(node, measure) {
  if (typeof ResizeObserver === "undefined") return undefined;
  if (observer === null) {
    observer = new ResizeObserver((entries) => {
      for (const entry of entries) watchers.get(entry.target)?.();
    });
  }
  watchers.set(node, measure);
  observer.observe(node);
  return () => {
    watchers.delete(node);
    observer.unobserve(node);
  };
}

/**
 * The studs along the top of a brick, counted from how wide the brick actually
 * is: a wide brick carries a row of them, a narrow one carries a single stud,
 * and it is recounted as the brick resizes - drag the chat panel narrower and
 * the bricks lose studs the way shorter bricks have fewer.
 *
 * The element measures itself rather than its parent: it is stretched between
 * the two insets, so its own width is exactly the run the studs have to fill.
 * Measured before paint, so a brick is never briefly drawn with the wrong
 * number of them.
 */
export default function Studs({
  pitch = STUD_PITCH,
  inset = 13,
  className = "tool-studs",
  tint,
}) {
  const ref = useRef(null);
  const [count, setCount] = useState(1);

  useLayoutEffect(() => {
    const node = ref.current;
    if (!node) return undefined;

    const measure = () => {
      const width = node.clientWidth;
      if (width) setCount(Math.max(1, Math.floor(width / pitch)));
    };
    measure();
    return watch(node, measure);
  }, [pitch]);

  return (
    <span
      ref={ref}
      className={`${className} ${count === 1 ? `${className}--one` : ""}`}
      style={{ "--stud-inset": `${inset}px` }}
      aria-hidden="true"
    >
      {Array.from({ length: count }, (_, i) => {
        // `tint` only ever knows which stud this is once the count is settled,
        // which is why it is a function and not a list: the count is measured
        // here and nowhere else. Returning nothing leaves the stud on the
        // brick's own `--face`, which is what every brick but a picture wants.
        const colour = tint?.(i, count);
        return <i key={i} style={colour ? { background: colour } : undefined} />;
      })}
    </span>
  );
}
