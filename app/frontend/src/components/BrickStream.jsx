import { useEffect, useState } from "react";
import Markdown from "./Markdown";
import Studs, { TILE_PITCH } from "./Studs";
import { BRICK_CYCLE, brickVars } from "../brick";

/**
 * The reply arrives in one piece; this snaps it into place word by word as
 * coloured tiles. Once the last word lands the bricks come away and the text
 * renders as itself — the model writes markdown, so it is formatted as markdown.
 * Line structure is preserved during the reveal so lists still read as lists.
 */

// One word per frame would be frantic and one per 60 ms would be slow for a
// long answer, so the stride grows instead of the interval.
const TICK_MS = 55;
const TARGET_TICKS = 45;

// Past this the tiles stop being playful and turn into a wall of colour.
const MAX_TILED_WORDS = 220;

/** Lines of words. Blank lines survive as empty arrays and become spacers. */
function toLines(text) {
  return (text || "").split("\n").map((line) => line.split(/\s+/).filter(Boolean));
}

function useReveal(total, animate, onAdvance) {
  const [shown, setShown] = useState(animate ? 0 : total);
  const [seen, setSeen] = useState(total);

  // A new message re-arms the reveal without waiting for an effect to run.
  if (seen !== total) {
    setSeen(total);
    setShown(animate ? 0 : total);
  }

  useEffect(() => {
    if (!animate || shown >= total) return;
    const stride = Math.max(1, Math.ceil(total / TARGET_TICKS));
    const id = setTimeout(() => {
      setShown((n) => Math.min(total, n + stride));
      onAdvance?.();
    }, TICK_MS);
    return () => clearTimeout(id);
  }, [animate, shown, total, onAdvance]);

  return shown;
}

/**
 * `live` is for text still arriving from the model: every word that has landed
 * is already a brick, there is no reveal timer to run, and it never settles —
 * the stream itself provides the pacing.
 */
export default function BrickStream({ text, animate = false, live = false, onAdvance }) {
  const lines = toLines(text);
  const words = lines.reduce((n, l) => n + l.length, 0);
  const tiled = animate && !live && words > 0 && words <= MAX_TILED_WORDS;

  const revealed = useReveal(words, tiled, onAdvance);
  const shown = live ? words : revealed;
  const settled = live ? false : !tiled || shown >= words;

  // Every brick is down: take them away and let the text be text.
  if (settled) return <Markdown text={text} className={tiled ? "md--settling" : ""} />;

  // Where the empty socket sits: right after the newest word, so the sentence
  // visibly grows instead of the socket parking at the end of the paragraph.
  let end = 0;
  const ends = lines.map((l) => (end += l.length));
  const slotLine = lines.findIndex((l, i) => l.length > 0 && ends[i] >= Math.max(shown, 1));

  let index = 0;

  return (
    <div className="stream">
      {lines.map((tokens, li) => {
        // nothing revealed on this line yet — don't reserve space for it
        if (ends[li] - tokens.length >= shown && li !== slotLine) return null;
        if (tokens.length === 0) return <div key={li} className="stream-gap" />;

        return (
          <div key={li} className="stream-line">
            {tokens.map((word, ti) => {
              const i = index++;
              const colour = BRICK_CYCLE[i % BRICK_CYCLE.length];
              const state = i >= shown ? "tile--hidden" : i === shown - 1 ? "tile--pop" : "";
              return (
                <span key={ti} className={`tile ${state}`} style={brickVars(colour)}>
                  <Studs className="tile-studs" pitch={TILE_PITCH} inset={5} />
                  <span className="tile-label">{word}</span>
                </span>
              );
            })}
            {li === slotLine && <span className="stream-slot" aria-hidden="true" />}
          </div>
        );
      })}
    </div>
  );
}
