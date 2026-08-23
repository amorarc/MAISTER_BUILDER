/**
 * The bloom, run against a scene graph shaped like the one LDrawLoader builds.
 *
 *     node src/bloom.test.js
 *
 * No browser and no WebGL: everything in bloom.js is scene-graph arithmetic and
 * material bookkeeping, which is exactly the part that cannot be checked by
 * looking at the screen. Two versions of it shipped broken — a rim that never
 * moved, and a ghost the same colour as the plastic under it — and both looked
 * identical to "nothing happened". This is what tells the difference.
 */

import * as THREE from "three";
import { breathe, breatheGlow, clearBloom, clearGlow, findPiece, makeBloom,
         makeGlow, pieceColour } from "./bloom.js";

let failures = 0;

function check(label, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) failures += 1;
  console.log(`  ${ok ? "ok  " : "FAIL"} ${label}: ${JSON.stringify(got)}`
    + (ok ? "" : `  (wanted ${JSON.stringify(want)})`));
}

function near(label, got, want, tolerance = 1e-6) {
  check(label, Math.abs(got - want) < tolerance, true);
}

/**
 * What LDrawLoader leaves in the scene, as far as this code is concerned:
 * modelRoot > the model file's group > one Group per type-1 line, each named
 * after its part file, positioned from the line's matrix, holding a mesh and
 * the part's edge lines. The model group carries the −Y flip the viewer sets.
 */
function loadedModel(lines) {
  const modelRoot = new THREE.Group();
  const model = new THREE.Group();
  model.name = "model.ldr";
  model.rotation.x = Math.PI; // LDraw is -Y up; three is +Y up
  modelRoot.add(model);

  // One material per colour, shared — which is the whole reason the bloom
  // clones before it tints. If it did not, lighting one brick would light
  // every brick of that colour in the model.
  const shared = new Map();
  for (const { part, colour, at } of lines) {
    if (!shared.has(colour)) {
      shared.set(colour, new THREE.MeshStandardMaterial({ color: colour }));
    }
    const group = new THREE.Group();
    group.name = part;
    group.position.set(...at);
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(40, 24, 40),
                                shared.get(colour));
    group.add(mesh);
    group.add(new THREE.LineSegments(
      new THREE.BufferGeometry(), new THREE.LineBasicMaterial()));
    model.add(group);
  }
  return { modelRoot, model, shared };
}

const LINES = [
  { part: "3001.dat", colour: 0xc91a09, at: [0, 0, 0] },
  { part: "3001.dat", colour: 0xc91a09, at: [80, 0, 0] },
  { part: "3039.dat", colour: 0xf2cd37, at: [0, -24, 0] },
];

console.log("finding the piece a line placed");
{
  const { modelRoot, model } = loadedModel(LINES);
  const first = findPiece(modelRoot, { file: "3001.dat", x: 0, y: 0, z: 0 });
  const second = findPiece(modelRoot, { file: "3001.dat", x: 80, y: 0, z: 0 });
  const slope = findPiece(modelRoot, { file: "3039.dat", x: 0, y: -24, z: 0 });
  check("the first of two identical parts", first === model.children[0], true);
  check("the second, told apart by position", second === model.children[1], true);
  check("a different part at the same x", slope === model.children[2], true);
  check("case and path do not matter",
        findPiece(modelRoot, { file: "PARTS\\3039.DAT", x: 0, y: -24, z: 0 })
          === model.children[2], true);
  check("a part this render does not have",
        findPiece(modelRoot, { file: "9999.dat", x: 0, y: 0, z: 0 }), null);
  check("the right part at the wrong place",
        findPiece(modelRoot, { file: "3001.dat", x: 500, y: 0, z: 0 }), null);
  check("no reference at all", findPiece(modelRoot, null), null);
  check("nothing loaded", findPiece(new THREE.Group(),
        { file: "3001.dat", x: 0, y: 0, z: 0 }), null);
}

console.log("\nlighting one up");
{
  const { modelRoot, model, shared } = loadedModel(LINES);
  const scene = new THREE.Scene();
  scene.add(modelRoot);
  const piece = model.children[0];
  const sharedRed = shared.get(0xc91a09);
  const neighbour = model.children[1].children[0];

  check("the colour comes off the plastic",
        `#${pieceColour(piece).getHexString()}`, "#c91a09");

  const bloom = makeBloom(scene, piece);
  check("the piece is on its own materials now",
        piece.children[0].material !== sharedRed, true);
  check("its neighbour still has the shared one",
        neighbour.material === sharedRed, true);
  check("two copies were hung on the scene",
        scene.children.filter((o) => o === bloom.aura || o === bloom.ghost).length,
        2);

  // The rim has to actually be bigger, and in the same place. This is the one
  // that was broken: `.matrix` was set and three.js never read it.
  scene.updateMatrixWorld(true);
  const box = new THREE.Box3().setFromObject(piece);
  const rim = new THREE.Box3().setFromObject(bloom.aura);
  const size = box.getSize(new THREE.Vector3());
  const rimSize = rim.getSize(new THREE.Vector3());
  near("the rim is 12% wider", rimSize.x / size.x, 1.12, 1e-3);
  near("the rim is 12% taller", rimSize.y / size.y, 1.12, 1e-3);
  check("and concentric with the piece",
        box.getCenter(new THREE.Vector3())
          .distanceTo(rim.getCenter(new THREE.Vector3())) < 1e-6, true);

  const ghostBox = new THREE.Box3().setFromObject(bloom.ghost);
  check("the ghost sits exactly on the piece",
        ghostBox.min.distanceTo(box.min) < 1e-6
        && ghostBox.max.distanceTo(box.max) < 1e-6, true);
  check("the ghost shows through what is in front of it",
        bloom.ghost.children[0].material.depthTest, false);
  check("the edge lines are not painted",
        bloom.aura.children[1].visible, false);

  console.log("\n  the pulse");
  // Trough and peak of one breath, by hand rather than by waiting.
  const at = (fraction) => bloom.started + 2600 * fraction;
  breathe(bloom, at(0.75)); // sin = -1
  const dim = piece.children[0].material.color.getHexString();
  const dimRim = bloom.aura.children[0].material.opacity;
  breathe(bloom, at(0.25)); // sin = +1
  const bright = piece.children[0].material.color.getHexString();
  const brightRim = bloom.aura.children[0].material.opacity;

  check("at the trough the piece is its own colour", `#${dim}`, "#c91a09");
  check("at the peak it is lifted towards white", bright !== dim, true);
  check("and it is still the same hue, not white",
        bright !== "ffffff" && bright > dim, true);
  check("the rim fades", brightRim > dimRim, true);
  check("the shared material was never touched",
        `#${sharedRed.color.getHexString()}`, "#c91a09");
  check("nor was the neighbour it is shared with",
        `#${neighbour.material.color.getHexString()}`, "#c91a09");

  console.log("\n  taking it down");
  clearBloom(bloom);
  check("the piece has the loader's material back",
        piece.children[0].material === sharedRed, true);
  check("its colour is unpainted", `#${sharedRed.color.getHexString()}`, "#c91a09");
  check("both copies are off the scene",
        scene.children.includes(bloom.aura)
        || scene.children.includes(bloom.ghost), false);
  check("the geometry survived — it belongs to the model",
        piece.children[0].geometry.attributes.position.count > 0, true);
}

console.log("\nthe awkward ones");
{
  const scene = new THREE.Scene();
  // A part that loaded no mesh at all: a group with nothing in it.
  const modelRoot = new THREE.Group();
  const model = new THREE.Group();
  modelRoot.add(model);
  const empty = new THREE.Group();
  empty.name = "3001.dat";
  model.add(empty);
  scene.add(modelRoot);

  const bloom = makeBloom(scene, empty);
  check("an empty piece makes a bloom rather than throwing", !!bloom, true);
  breathe(bloom, bloom.started + 650);
  check("and breathing it does nothing", bloom.lit.length, 0);
  clearBloom(bloom);

  // Nothing at all, which is what every frame with no caret on a part gets.
  breathe(null);
  breathe(undefined);
  clearBloom(null);
  check("no bloom is not an error", true, true);
}

console.log("\nlighting a whole check up");
{
  const { modelRoot, model, shared } = loadedModel(LINES);
  const sharedRed = shared.get(0xc91a09);
  const sharedYellow = shared.get(0xf2cd37);

  // Two clumps in two colours, the way hovering "Sub-assemblies" arrives: the
  // two red bricks in one, the yellow slope on its own.
  const glow = makeGlow(modelRoot, [
    { colour: "#0b5fbe", refs: [{ part: "3001.dat", at: [0, 0, 0] },
                                { part: "3001.dat", at: [80, 0, 0] }] },
    { colour: "#fb8122", refs: [{ part: "3039.dat", at: [0, -24, 0] }] },
  ]);

  check("every part in the check is off the shared materials",
        model.children.every((g) => g.children[0].material !== sharedRed
                                 && g.children[0].material !== sharedYellow),
        true);
  // The point of the whole design: two hundred red bricks in one group are
  // two hundred meshes over ONE clone, not two hundred of them.
  check("one clone per group per source material, not per mesh",
        glow.lit.length, 2);
  check("both bricks of a group share that one clone",
        model.children[0].children[0].material
          === model.children[1].children[0].material, true);
  check("and the other group has its own",
        model.children[2].children[0].material
          !== model.children[0].children[0].material, true);

  // Painted its group's colour throughout, brightest at the top of a breath.
  breatheGlow(glow, glow.started);          // trough
  const low = model.children[0].children[0].material.emissive.b;
  const trough = `#${model.children[0].children[0].material.color.getHexString()}`;
  breatheGlow(glow, glow.started + 650);    // peak
  const high = model.children[0].children[0].material.emissive.b;
  const peak = `#${model.children[0].children[0].material.color.getHexString()}`;

  check("a red brick in the blue group reads blue at the trough already",
        trough !== "#c91a09", true);
  check("the pulse is light, not colour — it stays blue at the peak",
        peak !== "#c91a09", true);
  check("and it glows harder at the peak than the trough", high > low, true);
  check("the second group glows its own colour, not the first's",
        model.children[2].children[0].material.emissive.r
          > model.children[2].children[0].material.emissive.b, true);

  clearGlow(glow);
  check("every part has the loader's material back",
        model.children[0].children[0].material === sharedRed
          && model.children[1].children[0].material === sharedRed
          && model.children[2].children[0].material === sharedYellow, true);
  check("the geometry survived — it belongs to the model",
        model.children[0].children[0].geometry.attributes.position.count > 0,
        true);
}

console.log("\nthe awkward ones, for a check");
{
  const { modelRoot } = loadedModel(LINES);
  check("a check with nothing behind it makes no glow",
        makeGlow(modelRoot, []), null);
  check("a part this render does not have is skipped, not guessed",
        makeGlow(modelRoot, [{ colour: "#0b5fbe",
                               refs: [{ part: "9999.dat", at: [0, 0, 0] }] }]),
        null);
  check("the right part at the wrong place is a different placement",
        makeGlow(modelRoot, [{ colour: "#0b5fbe",
                               refs: [{ part: "3001.dat", at: [500, 0, 0] }] }]),
        null);
  check("nothing loaded", makeGlow(new THREE.Group(),
        [{ colour: "#0b5fbe", refs: [{ part: "3001.dat", at: [0, 0, 0] }] }]),
        null);
  breatheGlow(null);
  clearGlow(null);
  check("no glow is not an error", true, true);
}

console.log(failures ? `\nFAILED (${failures})` : "\nPASS");
process.exit(failures ? 1 : 0);
