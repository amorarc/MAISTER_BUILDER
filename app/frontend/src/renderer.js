import * as THREE from "three";

/**
 * The one WebGL context the page is allowed, and the one place it lives.
 *
 * A browser allows only a handful of live contexts. Creating one per component
 * mount burns through them fast - StrictMode double-invokes effects in dev, and
 * every project switch remounts the viewer - and once the budget is gone,
 * WebGLRenderer throws for good. Sharing a single renderer means the app can
 * never exhaust the budget no matter how much you click around.
 *
 * It is in a file of its own, away from the component that uses it, and that is
 * not tidiness - it is what makes the sharing survive editing.
 *
 * A module-level singleton is only a singleton for as long as its module is.
 * Vite replaces a module when you save it, and the replacement starts with
 * fresh module state: `sharedRenderer` back to null. The component remounts,
 * asks for a renderer, and gets a *second* one with a second canvas, while the
 * first is still in the page - its render loop cancelled by the old cleanup, so
 * what you are looking at is a still image that does not answer the mouse. It
 * looks exactly like the viewer has frozen, and it happens only in development,
 * only after saving this component, which is a miserable thing to debug.
 *
 * Here, editing the viewer does not touch this file, so the module is not
 * replaced and the renderer it holds outlives the edit.
 */

let renderer = null;
let failed = null;

export function acquireRenderer() {
  if (failed) throw new Error(failed);
  if (!renderer) {
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true });
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    } catch (e) {
      failed = e?.message || String(e);
      throw e;
    }
  }
  return renderer;
}
