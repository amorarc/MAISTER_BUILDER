import * as THREE from "three";
import { LDrawLoader } from "three/examples/jsm/loaders/LDrawLoader.js";
import { LDrawConditionalLineMaterial } from "three/examples/jsm/materials/LDrawConditionalLineMaterial.js";
import { api } from "./api";
import { fillMissingMaterials } from "./ldrawMaterials";

/**
 * Off-screen renders of saved models, for the gallery.
 *
 * A card per model means a canvas per model, and a browser allows only a
 * handful of live WebGL contexts — so instead there is exactly one renderer
 * here, shared by every thumbnail and separate from the viewer's. Models are
 * drawn one at a time into it and read out as PNG data URLs, which are plain
 * images the grid can show as many of as it likes.
 */

const WIDTH = 480;
const HEIGHT = 320;
const BACKGROUND = 0xefede7; // the workbench

let renderer = null;
let loader = null;
let materialsReady = null;
let failed = false;

const cache = new Map(); // cache key -> data URL (or null once known impossible)
let queue = Promise.resolve(); // renders run strictly one after another

function acquire() {
  if (failed) throw new Error("thumbnail renderer unavailable");
  if (!renderer) {
    try {
      renderer = new THREE.WebGLRenderer({
        antialias: true,
        // toDataURL reads the drawing buffer after the frame has been composited
        preserveDrawingBuffer: true,
      });
      renderer.setPixelRatio(1);
      renderer.setSize(WIDTH, HEIGHT, false);
    } catch (e) {
      failed = true;
      throw e;
    }
  }
  return renderer;
}

function acquireLoader() {
  if (!loader) {
    loader = new LDrawLoader();
    loader.setPartsLibraryPath(api.libraryPath());
    loader.setConditionalLineMaterial(LDrawConditionalLineMaterial);
    loader.smoothNormals = true;
    materialsReady = loader
      .preloadMaterials(`${api.libraryPath()}LDConfig.ldr`)
      .catch(() => {
        /* colours fall back to the loader's defaults; geometry still renders */
      });
  }
  return loader;
}

function disposeTree(root) {
  root.traverse((o) => {
    if (o.geometry) o.geometry.dispose();
    const m = o.material;
    if (Array.isArray(m)) m.forEach((x) => x?.dispose?.());
    else m?.dispose?.();
  });
}

/** Three-quarter view, pulled back far enough to hold the whole model. */
function frame(camera, object) {
  const box = new THREE.Box3().setFromObject(object);
  if (box.isEmpty()) return false;

  const size = box.getSize(new THREE.Vector3());
  const centre = box.getCenter(new THREE.Vector3());
  const radius = Math.max(size.length() / 2, 10);

  const fov = THREE.MathUtils.degToRad(camera.fov);
  const distance = (radius / Math.sin(fov / 2)) * 1.05;
  const direction = new THREE.Vector3(0.75, 0.5, 1).normalize();

  camera.position.copy(centre).addScaledVector(direction, distance);
  camera.near = Math.max(distance / 1000, 0.5);
  camera.far = distance * 10;
  camera.lookAt(centre);
  camera.updateProjectionMatrix();
  return true;
}

function drawOne(url) {
  return new Promise((resolve, reject) => {
    const gl = acquire();
    const ldraw = acquireLoader();

    materialsReady.then(() => {
      ldraw.load(
        url,
        (group) => {
          try {
            // a colour the loader could not resolve leaves a null material,
            // which throws in three's render loop and blanks the thumbnail
            fillMissingMaterials(group);

            const scene = new THREE.Scene();
            scene.background = new THREE.Color(BACKGROUND);

            scene.add(new THREE.AmbientLight(0xffffff, 1.6));
            const key = new THREE.DirectionalLight(0xffffff, 2.2);
            key.position.set(1, 2, 1.5);
            scene.add(key);
            const fill = new THREE.DirectionalLight(0xffffff, 0.8);
            fill.position.set(-1, 0.5, -1);
            scene.add(fill);

            // LDraw is -Y up, three.js is +Y up
            group.rotation.x = Math.PI;
            scene.add(group);

            const camera = new THREE.PerspectiveCamera(38, WIDTH / HEIGHT, 1, 100000);
            if (!frame(camera, group)) {
              disposeTree(group);
              resolve(null); // nothing to see — an empty or unresolvable model
              return;
            }

            gl.setSize(WIDTH, HEIGHT, false);
            gl.render(scene, camera);
            const png = gl.domElement.toDataURL("image/png");

            disposeTree(group);
            scene.clear();
            resolve(png);
          } catch (e) {
            reject(e);
          }
        },
        undefined,
        (e) => reject(e instanceof Error ? e : new Error("could not load the model"))
      );
    });
  });
}

/**
 * A PNG data URL for anything LDrawLoader can load, or null if it cannot be
 * drawn. Results are memoised under `key`, and renders are serialised so a
 * gallery of twenty does not try to run twenty at once.
 */
export function thumbnailForUrl(key, url) {
  if (cache.has(key)) return Promise.resolve(cache.get(key));

  const run = queue.then(async () => {
    if (cache.has(key)) return cache.get(key);
    let png = null;
    try {
      png = await drawOne(url);
    } catch {
      png = null; // the card falls back to whatever it shows without a picture
    }
    cache.set(key, png);
    return png;
  });

  queue = run.catch(() => {});
  return run;
}

/** The render of a saved creation. */
export function thumbnailFor(creation) {
  if (creation.missing) return Promise.resolve(null);
  return thumbnailForUrl(
    `${creation.creation_id}@${creation.updated_at || ""}`,
    api.creationModelUrl(creation.creation_id)
  );
}

/** The render of a single catalogue part, in whatever colour is asked for. */
export function thumbnailForPart(partId, colour = 4) {
  return thumbnailForUrl(`part:${partId}:${colour}`, api.partModelUrl(partId, colour));
}

/**
 * The render of an official set.
 *
 * Cached under the set number alone: the corpus is read-only, so a set drawn
 * once is the same set forever, and a shelf of 1,800 that is scrolled twice
 * should only ever pay for the first pass.
 */
export function thumbnailForSet(number) {
  return thumbnailForUrl(`set:${number}`, api.setModelUrl(number));
}
