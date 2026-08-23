import * as THREE from "three";

// --------------------------------------------------------------------------
// The piece under the caret
//
// Reading a model file, the hard question is never what a line says — it is
// which brick it is. `1 4 0 -24 40 ... 3001.dat` is a red 2x4 somewhere in a
// hundred of them, and the only way to find it used to be to change the line
// and watch what moved.
//
// So the piece the caret is on breathes, in its own colour. Three things at
// once, because one of them alone is not enough:
//
//   * **The piece itself** brightens and dims, on materials cloned for it.
//     Cloned rather than tinted in place because LDrawLoader hands the same
//     material to every part of that colour, and lighting one red brick would
//     otherwise light all of them.
//   * **A rim** — the piece a hair larger, drawn inside-out, so what survives
//     being covered by the piece is a halo around its outline.
//   * **A ghost** drawn with the depth test off, so a brick buried in the
//     middle of a build — exactly the one that cannot be found by looking —
//     shows through what is in front of it.
//
// It lives in its own file because it is the part of the viewer that cannot be
// checked by looking at it: it is three matrices and a material swap, and two
// versions of it shipped broken in ways that were invisible on screen — a rim
// that never moved because `matrixAutoUpdate = false` stops three.js copying
// `.matrix` into `matrixWorld`, and a ghost laid over the piece in the piece's
// own colour, which changes nothing anyone can see. Out here it is four plain
// functions over a scene graph, and bloom.test.js runs them under real
// three.js with no browser. See that file for what is pinned.
// --------------------------------------------------------------------------

/** One full breath, in milliseconds. Slow: a marker to find, not an alarm. */
export const BLOOM_MS = 2600;
/** How much larger the rim is than the piece it stands around. */
export const BLOOM_GROW = 1.12;
/** How far the pulse lifts a colour towards white at its brightest. */
export const BLOOM_LIFT = 0.62;

const WHITE = new THREE.Color(0xffffff);

/** The colour the piece is actually rendered in, whatever the line asked for. */
export function pieceColour(piece) {
  let found = null;
  piece.traverse((o) => {
    if (found || !o.isMesh) return;
    const material = Array.isArray(o.material) ? o.material[0] : o.material;
    if (material?.color) found = material.color;
  });
  // Nothing to read the colour off — a part that failed to load has no mesh.
  return found ? found.clone() : new THREE.Color(0xf6c700);
}

/**
 * The object a source line placed, somewhere in the loaded model.
 *
 * Matched on the part file *and* where it was put. LDrawLoader gives each
 * type-1 reference a Group of its own, names it after the file, and decomposes
 * the line's matrix into the group's position — so the numbers in the line are
 * the numbers on the object, and neither has to be counted.
 *
 * Null when nothing matches, and null is the right answer: it means the line
 * places something this render does not contain. Lighting up a near-miss
 * instead would point at the wrong brick, which is worse than pointing at
 * none — the whole purpose of this is to answer "which one is it".
 */
export function findPiece(root, ref) {
  const model = root?.children?.[0];
  const wanted = String(ref?.file || "").toLowerCase().replace(/\\/g, "/");
  if (!model || !wanted) return null;
  const bare = wanted.split("/").pop();

  let best = null;
  let closest = Infinity;
  model.traverse((o) => {
    const name = String(o.name || "").toLowerCase().replace(/\\/g, "/");
    if (!name || (name !== wanted && name.split("/").pop() !== bare)) return;
    const gap = Math.abs(o.position.x - ref.x)
      + Math.abs(o.position.y - ref.y)
      + Math.abs(o.position.z - ref.z);
    if (gap < closest) {
      closest = gap;
      best = o;
    }
  });
  // The same numbers that are in the line, so this is an exact match with room
  // for the float. Any further off and it is a different placement of the same
  // part.
  return closest <= 0.5 ? best : null;
}

/**
 * Light a piece up. Returns what `breathe` pulses and `clearBloom` takes down.
 *
 * Everything here shares the piece's geometry rather than rebuilding it —
 * which is why nothing here may ever dispose a geometry.
 */
export function makeBloom(scene, piece) {
  piece.updateWorldMatrix(true, false);
  const colour = pieceColour(piece);
  const halos = [];
  const lit = [];
  const swapped = [];

  // 1. The piece itself. Its own materials are put aside and replaced with
  //    copies, because the loader hands one material to every part of a given
  //    colour: tinting in place would light up every red brick in the model.
  piece.traverse((o) => {
    if (!o.isMesh || !o.material || Array.isArray(o.material)) return;
    const original = o.material;
    if (!original.color || typeof original.clone !== "function") return;
    const material = original.clone();
    swapped.push([o, original]);
    lit.push({ material, base: original.color.clone() });
    o.material = material;
  });

  // 2. and 3. Two copies of it, hung on the scene rather than beside the piece
  //    and posed with the piece's world matrix. Inside the model they would be
  //    part of the model: `frameObject` measures that tree to decide where the
  //    floor goes and how far back the camera sits, and a copy of one brick
  //    12% too big has no business moving the camera.
  //
  //    Posed by decomposing the matrix into position/rotation/scale rather
  //    than by writing `.matrix` — three.js recomputes `matrixWorld` from the
  //    decomposed values every frame, and from a raw `.matrix` only when it is
  //    told the matrix changed. Told nothing, it draws these at the piece's
  //    own size and position, where neither can be seen.
  const copy = (options, order, matrix) => {
    const clone = piece.clone();
    clone.traverse((o) => {
      if (!o.isMesh) {
        // The part's edge lines. Drawn in black over a coloured ghost they
        // read as the piece being outlined rather than lit.
        o.visible = false;
        return;
      }
      const material = new THREE.MeshBasicMaterial({
        transparent: true, depthWrite: false, toneMapped: false, ...options,
      });
      halos.push(material);
      o.material = material;
      o.renderOrder = order;
    });
    clone.renderOrder = order;
    matrix.decompose(clone.position, clone.quaternion, clone.scale);
    scene.add(clone);
    return clone;
  };

  // The rim: the piece, inside-out and larger, so what survives being covered
  // by the piece itself is a halo around its outline. Grown about its own
  // centre — grown about its origin instead, which for LDraw sits on the top
  // face, it would swell downwards and through the brick below it.
  const centre = new THREE.Box3().setFromObject(piece)
    .getCenter(new THREE.Vector3());
  const grown = new THREE.Matrix4()
    .makeTranslation(centre.x, centre.y, centre.z)
    .multiply(new THREE.Matrix4().makeScale(BLOOM_GROW, BLOOM_GROW, BLOOM_GROW))
    .multiply(new THREE.Matrix4().makeTranslation(-centre.x, -centre.y, -centre.z))
    .multiply(piece.matrixWorld);

  const aura = copy({ side: THREE.BackSide, opacity: 0, color: colour },
                    2, grown);
  // The ghost: the piece over the top of everything, so it can be found when
  // it is inside the build rather than on the outside of it. Lifted towards
  // white, or laid over the piece in the piece's own colour it would be a
  // change nobody can see.
  const ghost = copy(
    { depthTest: false, opacity: 0, color: colour.clone().lerp(WHITE, 0.5) },
    999, piece.matrixWorld);

  return { aura, ghost, halos, lit, swapped,
           started: (globalThis.performance || Date).now() };
}

/** One frame of the pulse. `at` is a timestamp, for tests. */
export function breathe(bloom, at) {
  if (!bloom) return;
  const now = at ?? (globalThis.performance || Date).now();
  const phase = (Math.sin(((now - bloom.started) / BLOOM_MS) * Math.PI * 2) + 1) / 2;

  // The plastic, brightening and dimming in its own colour. This is the part
  // you actually see; the two halos are what find it for you when the piece is
  // small or buried.
  for (const { material, base } of bloom.lit || []) {
    material.color.copy(base).lerp(WHITE, BLOOM_LIFT * phase);
    if (material.emissive) {
      material.emissive.copy(base).multiplyScalar(0.45 * phase);
    }
  }
  for (const material of bloom.halos || []) {
    // The rim carries the pulse; the ghost stays faint, or a piece standing in
    // plain sight would be repainted flat every two seconds.
    material.opacity = material.depthTest ? 0.25 + 0.7 * phase
                                          : 0.08 + 0.3 * phase;
  }
}

// --------------------------------------------------------------------------
// A whole check, lit at once
//
// The caret bloom above lights ONE piece and spends three objects doing it —
// the piece, a rim around it and a ghost through the model in front of it. All
// three are worth it for one brick you are hunting for.
//
// A build check is the other shape of the same question: not "which brick is
// this line" but "which bricks did this count count", and the answer is
// routinely two hundred of them. Cloning a rim and a ghost per part would be
// four hundred extra objects in the scene to say something the colour already
// says, so this lights the plastic and nothing else.
//
// What it does buy is sharing. LDrawLoader hands ONE material to every part of
// a given LDraw colour, so a model of four hundred red bricks is four hundred
// meshes over a single material — and one clone per (group, source material)
// paints all of them. A model that took four hundred clones now takes about
// three, and the pulse is three colour writes a frame rather than four hundred.
// --------------------------------------------------------------------------

/** How much of the check's colour a lit part wears at the bottom of a breath. */
export const GLOW_FLOOR = 0.72;

/**
 * Every named object in the loaded model, keyed by part file, with where it
 * sits in LDraw's own coordinates.
 *
 * Measured against the loaded model's frame rather than read off `.position`,
 * which is local to whatever submodel the part was reached through. The
 * checker reports world space after expanding every submodel, so this is the
 * frame both sides can meet in — and it survives the model being stood on the
 * grid by `frameObject`, since that moves the frame and not the parts in it.
 */
export function indexPieces(root) {
  const model = root?.children?.[0];
  if (!model) return null;

  model.updateWorldMatrix(true, true);
  const inverse = new THREE.Matrix4().copy(model.matrixWorld).invert();
  const at = new THREE.Vector3();
  const byName = new Map();

  model.traverse((o) => {
    const name = String(o.name || "").toLowerCase().replace(/\\/g, "/").split("/").pop();
    if (!name) return;
    at.setFromMatrixPosition(o.matrixWorld).applyMatrix4(inverse);
    if (!byName.has(name)) byName.set(name, []);
    byName.get(name).push({ object: o, x: at.x, y: at.y, z: at.z });
  });
  return byName;
}

/** The one object a `{ part, at }` row placed, or null if this render has none. */
function lookup(index, ref) {
  const name = String(ref?.part || "").toLowerCase().replace(/\\/g, "/").split("/").pop();
  const [x, y, z] = ref?.at || [];
  const candidates = index?.get(name);
  if (!candidates || x == null) return null;

  let best = null;
  let closest = Infinity;
  for (const c of candidates) {
    const gap = Math.abs(c.x - x) + Math.abs(c.y - y) + Math.abs(c.z - z);
    if (gap < closest) {
      closest = gap;
      best = c.object;
    }
  }
  // Same tolerance as findPiece, and for the same reason: these are the
  // numbers the checker read out of the file, so a match is exact bar the
  // float. Anything further off is a different placement of the same part.
  return closest <= 0.5 ? best : null;
}

/**
 * Light every part of every group, each group in its own colour.
 *
 * `groups` is what `buildCheck.checkGroups` returns: `[{ colour, refs }]`.
 * Null when nothing matched, which is the same answer as "no glow" and is what
 * the caller checks.
 */
export function makeGlow(root, groups) {
  const index = indexPieces(root);
  if (!index) return null;

  const swapped = [];
  const lit = [];

  for (const group of groups || []) {
    const target = new THREE.Color(group.colour);
    const clones = new Map(); // the loader's material -> this group's copy

    for (const ref of group.refs || []) {
      const piece = lookup(index, ref);
      if (!piece) continue;
      piece.traverse((o) => {
        if (!o.isMesh || !o.material || Array.isArray(o.material)) return;
        const original = o.material;
        if (!original.color || typeof original.clone !== "function") return;

        let material = clones.get(original);
        if (!material) {
          material = original.clone();
          clones.set(original, material);
          lit.push({ material, base: original.color.clone(), target });
        }
        swapped.push([o, original]);
        o.material = material;
      });
    }
  }

  return lit.length ? { swapped, lit, started: (globalThis.performance || Date).now() }
                    : null;
}

/**
 * One frame of the check's pulse. `at` is a timestamp, for tests.
 *
 * The part is painted the check's colour and breathes light rather than
 * breathing colour: a set of parts that faded back to their own plastic every
 * two seconds would spend half of each breath unreadable, and which parts are
 * in the set is the whole of what this is for. A trace of the brick's own
 * colour is left underneath so the model still reads as bricks.
 */
export function breatheGlow(glow, at) {
  if (!glow) return;
  const now = at ?? (globalThis.performance || Date).now();
  const phase = (Math.sin(((now - glow.started) / BLOOM_MS) * Math.PI * 2) + 1) / 2;

  for (const { material, base, target } of glow.lit || []) {
    material.color.copy(base).lerp(target, GLOW_FLOOR + (1 - GLOW_FLOOR) * phase);
    if (material.emissive) {
      material.emissive.copy(target).multiplyScalar(0.14 + 0.5 * phase);
    }
  }
}

/** Take the bloom down. The geometry belongs to the model — leave it alone. */
export function clearBloom(bloom) {
  if (!bloom) return;
  // The piece gets its own materials back before anything else: those are the
  // loader's, shared with every other part of that colour, and losing them
  // would leave this brick painted mid-pulse for good.
  for (const [mesh, original] of bloom.swapped || []) mesh.material = original;
  for (const { material } of bloom.lit || []) material.dispose();
  for (const object of [bloom.aura, bloom.ghost]) object?.parent?.remove(object);
  for (const material of bloom.halos || []) material.dispose();
}

/**
 * Take a check's glow down.
 *
 * A glow is a bloom without the two copies — the same `swapped` to put back
 * and the same `lit` to dispose — so the bloom's own teardown already does the
 * whole job, and the pair of them can never drift apart into one leaking what
 * the other releases.
 */
export const clearGlow = clearBloom;
