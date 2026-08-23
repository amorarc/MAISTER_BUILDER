/** Short, glanceable units for the mono metadata lines. */

export function relativeTime(iso) {
  if (!iso) return "";
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return "";

  const secs = Math.max(0, (Date.now() - then) / 1000);
  if (secs < 45) return "now";
  const mins = secs / 60;
  if (mins < 60) return `${Math.round(mins)} min`;
  const hours = mins / 60;
  if (hours < 24) return `${Math.round(hours)} h`;
  const days = hours / 24;
  if (days < 7) return `${Math.round(days)} d`;
  return new Date(then).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/**
 * A duration in the largest unit that still says something.
 *
 * A subconstruction takes minutes, and "335.6 s" is a number you have to do
 * arithmetic on before it means anything. Past a minute it is minutes, and
 * past an hour it is hours - the seconds are still there, just not leading.
 */
export function formatMs(ms) {
  if (ms == null) return null;
  if (ms < 1000) return `${Math.round(ms)} ms`;

  const secs = ms / 1000;
  // Rounded before the comparison, or 59.99 s prints as "60.0 s" - a minute
  // written as seconds, which is the thing this exists to stop.
  const whole = Math.round(secs);
  if (whole < 60) return `${secs.toFixed(1)} s`;

  const hours = Math.floor(whole / 3600);
  const mins = Math.floor((whole % 3600) / 60);
  const rest = whole % 60;
  if (hours) return `${hours}h ${mins}m`;
  return rest ? `${mins}m ${rest}s` : `${mins}m`;
}

export function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function plural(n, word) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}
