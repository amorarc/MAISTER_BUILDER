import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { LDrawLoader } from "three/examples/jsm/loaders/LDrawLoader.js";
import { LDrawConditionalLineMaterial } from "three/examples/jsm/materials/LDrawConditionalLineMaterial.js";
import BrickLoader from "./BrickLoader";
import { api } from "../api";
import { breathe, breatheGlow, clearBloom, clearGlow, findPiece, makeBloom,
         makeGlow } from "../bloom";
import { checkGroups } from "../buildCheck";
import { fillMissingMaterials } from "../ldrawMaterials";
import { plural } from "../format";
import { acquireRenderer } from "../renderer";

export default function Viewer3D({ projectId, version, file, busy, built,
                                   highlight, check, validation, children }) {
  const mountRef = useRef(null);
  const stateRef = useRef(null);
  const [status, setStatus] = useState("idle");
  const [error, setError] = useState(null);
  const [webglError, setWebglError] = useState(null);
  const [partCount, setPartCount] = useState(0);
  // Which project the scene currently holds. Opening a different one is a swap
  // (nothing on screen is true any more); a new version of the one already
  // shown is a refresh, and the build stays up while it loads.
  const shownRef = useRef(null);
  const [swapping, setSwapping] = useState(false);

  // --- one-time scene setup ------------------------------------------------
  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;

    let renderer;
    try {
      renderer = acquireRenderer();
    } catch (e) {
      setWebglError(e?.message || String(e));
      return;
    }
    setWebglError(null);

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0xefede7); // the workbench

    const camera = new THREE.PerspectiveCamera(
      40,
      mount.clientWidth / mount.clientHeight,
      1,
      100000
    );
    camera.position.set(400, 300, 400);

    renderer.setSize(mount.clientWidth, mount.clientHeight);
    // `replaceChildren` rather than `appendChild`: this node holds the one
    // canvas and nothing else, so anything already in it is a leftover - a
    // canvas from a mount whose cleanup did not run, which in development is
    // one saved file away. A leftover on top of the live one is a frozen
    // picture of the model that ignores the mouse.
    mount.replaceChildren(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;

    scene.add(new THREE.AmbientLight(0xffffff, 1.6));
    const key = new THREE.DirectionalLight(0xffffff, 2.2);
    key.position.set(1, 2, 1.5);
    scene.add(key);
    const fill = new THREE.DirectionalLight(0xffffff, 0.8);
    fill.position.set(-1, 0.5, -1);
    scene.add(fill);

    // Rebuilt rather than scaled whenever the framing changes, so one cell is
    // always exactly one stud however far out the camera sits.
    const gridHolder = { current: makeGrid(squareAround(960)) };
    scene.add(gridHolder.current);

    const modelRoot = new THREE.Group();
    scene.add(modelRoot);

    let raf;
    const animate = () => {
      raf = requestAnimationFrame(animate);
      // Guarded, and the guard is the point: the highlight is a nicety and the
      // view is the product. Anything thrown between here and `render` below
      // stops the frame being drawn, and a viewer that has stopped drawing
      // looks exactly like one that has frozen - you cannot even turn the
      // model to see what went wrong. So a bloom that misbehaves is dropped
      // and the frame goes on.
      try {
        breathe(stateRef.current?.bloom);
        breatheGlow(stateRef.current?.glow);
      } catch {
        dropBloom(stateRef.current);
        dropGlow(stateRef.current);
      }
      controls.update();
      renderer.render(scene, camera);
    };
    animate();

    const onResize = () => {
      if (!mount.clientWidth) return;
      camera.aspect = mount.clientWidth / mount.clientHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(mount.clientWidth, mount.clientHeight);
    };
    const observer = new ResizeObserver(onResize);
    observer.observe(mount);

    stateRef.current = { scene, camera, renderer, controls, modelRoot, gridHolder };

    return () => {
      cancelAnimationFrame(raf);
      observer.disconnect();
      controls.dispose();
      dropBloom(stateRef.current);
      dropGlow(stateRef.current);
      disposeTree(modelRoot);
      disposeTree(gridHolder.current);

      // The renderer is shared and deliberately NOT disposed - disposing it
      // would drop the one context we reuse. Only detach its canvas.
      if (renderer.domElement.parentNode === mount) {
        mount.removeChild(renderer.domElement);
      }
      stateRef.current = null;
    };
  }, []);

  // --- (re)load the model whenever it changes ------------------------------
  useEffect(() => {
    const st = stateRef.current;
    if (!st || !projectId) return;

    let cancelled = false;
    setStatus("loading");
    setError(null);

    const loader = new LDrawLoader();
    loader.setPartsLibraryPath(api.libraryPath());
    // required by LDrawLoader for type-5 conditional edges under WebGLRenderer
    loader.setConditionalLineMaterial(LDrawConditionalLineMaterial);
    loader.smoothNormals = true;

    const clear = () => {
      // Before the tree is disposed: the bloom borrows the geometry of the
      // piece it is lighting up, and taking it down afterwards would be
      // holding materials against a model that no longer exists. Same for the
      // check's glow, which holds the loader's own materials to give back.
      dropBloom(st);
      dropGlow(st);
      disposeTree(st.modelRoot);
      st.modelRoot.clear();
    };

    // Drop the outgoing project before the first byte of the new one is
    // requested, so the wait is never spent looking at someone else's build.
    const showing = `${projectId}::${file || 'model.ldr'}`;
    if (shownRef.current !== showing) {
      clear();
      setPartCount(0);
      frameEmpty(st);
      setSwapping(true);
    }

    (async () => {
      try {
        await loader.preloadMaterials(`${api.libraryPath()}LDConfig.ldr`);
      } catch {
        // Colours fall back to the loader's defaults; geometry still renders.
      }
      if (cancelled) return;

      try {
        loader.load(
          // The scene, or whichever component the Source view is showing -
          // both panes look at the same file, so switching moves both.
          file && file !== "model.ldr"
            ? api.fileUrl(projectId, file, version)
            : api.modelUrl(projectId, version),
          (group) => {
            if (cancelled || !stateRef.current) return;
            clear();

            // Before anything is added to the scene: one material the loader
            // could not resolve throws inside three's render loop, every frame,
            // and takes the whole model down with it.
            fillMissingMaterials(group);

            // LDraw is right-handed with -Y up; three.js is +Y up.
            group.rotation.x = Math.PI;

            let count = 0;
            group.traverse((o) => {
              if (o.isMesh) count += 1;
            });
            setPartCount(count);

            st.modelRoot.add(group);
            if (count) frameObject(st, group);
            else frameEmpty(st);
            setStatus(count ? "ready" : "empty");
            shownRef.current = showing;
            setSwapping(false);
          },
          undefined,
          (err) => {
            if (cancelled) return;
            clear();
            setStatus("error");
            setError(err?.message || "could not load the model");
            // the error pill has to be readable, so the loader comes down even
            // though there is nothing to show behind it
            shownRef.current = showing;
            setSwapping(false);
          }
        );
      } catch (e) {
        if (cancelled) return;
        clear();
        setStatus("error");
        setError(e?.message || String(e));
        shownRef.current = showing;
        setSwapping(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [projectId, version, file]);

  // --- the piece the caret is on -------------------------------------------
  //
  // Rebuilt whenever the highlight changes and whenever the model is reloaded
  // under it - `partCount` is what says a new one has landed, and the piece
  // this was pointing at belongs to the tree that has just been thrown away.
  useEffect(() => {
    const st = stateRef.current;
    if (!st) return undefined;
    dropBloom(st);
    if (!highlight || status !== "ready") return undefined;

    const piece = findPiece(st.modelRoot, highlight);
    if (!piece) return undefined; // the line places something not in this file
    st.bloom = makeBloom(st.scene, piece);

    return () => dropBloom(stateRef.current);
  }, [highlight, status, partCount]);

  // --- the check the mouse is over ------------------------------------------
  //
  // Hovering a count in the rail paints the parts behind it. Rebuilt on the
  // same terms as the caret's bloom, and for the same reason: `partCount` is
  // what says a new model has landed under it, and the objects this was
  // holding belong to the tree that has just been thrown away.
  useEffect(() => {
    const st = stateRef.current;
    if (!st) return undefined;
    dropGlow(st);
    if (!check || status !== "ready") return undefined;

    const groups = checkGroups(validation, check);
    if (groups.length) st.glow = makeGlow(st.modelRoot, groups);

    return () => dropGlow(stateRef.current);
  }, [check, validation, status, partCount]);

  if (webglError) {
    return (
      <div className="viewer viewer-fallback">
        <h3>WebGL is unavailable</h3>
        <p>{webglError}</p>
        <p>
          This is usually too many live WebGL contexts. Reload the page to
          reclaim them. If it persists, check <code>chrome://gpu</code> for
          hardware acceleration.
        </p>
        <button className="btn btn--primary btn--big" onClick={() => window.location.reload()}>
          Reload
        </button>
      </div>
    );
  }

  return (
    <div className="viewer">
      <div ref={mountRef} className="viewer-canvas" />

      {swapping && <BrickLoader />}

      <div className="viewer-overlay">
        {status === "loading" && !swapping && <span className="pill">Loading model…</span>}
        {status === "empty" && <span className="pill">Empty baseplate</span>}
        {status === "ready" && (
          <span className="pill">{plural(partCount, "part")} on the grid</span>
        )}
        {status === "error" && <span className="pill pill--error">{error}</span>}

        {busy ? (
          <span className="pill pill--building">Building…</span>
        ) : status === "ready" && built ? (
          <span className="pill pill--built">Built</span>
        ) : null}
      </div>

      <div className="viewer-tools">
        <button
          className="btn btn--light"
          onClick={() => {
            const st = stateRef.current;
            if (st && st.modelRoot.children[0]) frameObject(st, st.modelRoot.children[0]);
          }}
        >
          Reset view
        </button>
      </div>

      {/* What the agent is doing right now is NOT reported here. It used to
          be, in a read-out docked at the foot of the workbench - and the
          composer sitting directly under it was already saying the same
          sentence. Two copies of one line means reading neither. */}
      {children}
    </div>
  );
}

/** Take down whatever the caret was lighting up, if anything. See bloom.js. */
function dropBloom(st) {
  if (!st?.bloom) return;
  clearBloom(st.bloom);
  st.bloom = null;
}

/** The same, for the parts a hovered build check was lighting up. */
function dropGlow(st) {
  if (!st?.glow) return;
  clearGlow(st.glow);
  st.glow = null;
}

/** Free GPU memory held by everything under an object. */
function disposeTree(root) {
  root.traverse((o) => {
    if (o.geometry) o.geometry.dispose();
    const m = o.material;
    if (Array.isArray(m)) m.forEach((x) => x?.dispose?.());
    else m?.dispose?.();
  });
}

const STUD = 20; // LDU
const MAX_LINES = 240; // past this the cell grows instead
const GRID_Y = -0.1; // a hair under the floor, so coplanar lines cannot fight

// How much floor there is around the build. Enough that the model never reads
// as standing at the edge of the world, not so much that a six-stud tree sits
// in the middle of a car park.
const MIN_MARGIN = 3 * STUD;
const MAX_MARGIN = 12 * STUD;
const MARGIN_SHARE = 0.3;

/** One set of lines, in one grey. */
function gridLines(points, colour) {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(points, 3));
  return new THREE.LineSegments(
    geometry,
    new THREE.LineBasicMaterial({ color: colour })
  );
}

/**
 * A stud grid under a footprint, in the workbench's own greys.
 *
 * The floor follows the build rather than the origin. A model is written
 * wherever its author put it - a scene's second object can stand two hundred
 * studs off - and a fixed square centred on (0,0) left those builds hanging
 * over the edge of it, or clear of it altogether, which reads as floating
 * rather than as standing on anything. So the extent is taken from where the
 * parts actually are, in x and z independently: a long car gets a long floor,
 * and there is grid under every part of it and a margin all the way round.
 *
 * Cells stay a whole number of studs, and past a few hundred lines the cell
 * grows instead of the count, so a baseplate-sized model does not turn the
 * floor into a solid block.
 */
function makeGrid(box) {
  const spanX = Math.max(box.maxX - box.minX, STUD);
  const spanZ = Math.max(box.maxZ - box.minZ, STUD);
  const margin = Math.min(
    MAX_MARGIN,
    Math.max(MIN_MARGIN, Math.max(spanX, spanZ) * MARGIN_SHARE)
  );

  const cell =
    STUD *
    Math.max(
      1,
      Math.ceil(Math.max(spanX + 2 * margin, spanZ + 2 * margin) / STUD / MAX_LINES)
    );

  // Snapped outwards onto the lattice the bricks themselves sit on, so a line
  // falls where a stud does.
  const x0 = Math.floor((box.minX - margin) / cell) * cell;
  const x1 = Math.ceil((box.maxX + margin) / cell) * cell;
  const z0 = Math.floor((box.minZ - margin) / cell) * cell;
  const z1 = Math.ceil((box.maxZ + margin) / cell) * cell;

  // Every fifth line darker - something to count studs against, and what the
  // old grid used its two centre lines for.
  const minor = [];
  const major = [];
  for (let x = x0; x <= x1 + 1e-6; x += cell) {
    (Math.round(x / cell) % 5 === 0 ? major : minor).push(x, 0, z0, x, 0, z1);
  }
  for (let z = z0; z <= z1 + 1e-6; z += cell) {
    (Math.round(z / cell) % 5 === 0 ? major : minor).push(x0, 0, z, x1, 0, z);
  }

  const group = new THREE.Group();
  group.add(gridLines(minor, 0xdcd8ce));
  group.add(gridLines(major, 0xc3bfb2));
  group.position.y = GRID_Y;
  return group;
}

/** A footprint centred on the origin, `extent` LDU across. */
function squareAround(extent) {
  const half = extent / 2;
  return { minX: -half, maxX: half, minZ: -half, maxZ: half };
}

function setGrid(st, box) {
  const next = makeGrid(box);
  st.scene.remove(st.gridHolder.current);
  disposeTree(st.gridHolder.current);
  st.gridHolder.current = next;
  st.scene.add(next);
}

const VIEW_DIR = new THREE.Vector3(0.75, 0.55, 1).normalize();

/**
 * Nothing to frame: sit over a baseplate-sized patch of grid. At the full
 * extent an empty scene reads as a horizon rather than a floor.
 */
function frameEmpty(st) {
  setGrid(st, squareAround(480));
  st.camera.position.copy(VIEW_DIR).multiplyScalar(700);
  st.camera.near = 1;
  st.camera.far = 8000;
  st.camera.updateProjectionMatrix();
  st.controls.target.set(0, 0, 0);
  st.controls.update();
}

/** Point the camera at the object and pull back far enough to see all of it. */
function frameObject(st, object) {
  const box = new THREE.Box3().setFromObject(object);
  if (box.isEmpty()) return;

  const size = box.getSize(new THREE.Vector3());
  const center = box.getCenter(new THREE.Vector3());
  const radius = Math.max(size.length() / 2, 20);

  // sit the build on the grid: its lowest point becomes the floor, whatever
  // height it was authored at
  object.position.y -= box.min.y;
  box.setFromObject(object);
  box.getCenter(center);

  // and put the floor under it, wherever "under it" happens to be
  setGrid(st, {
    minX: box.min.x, maxX: box.max.x,
    minZ: box.min.z, maxZ: box.max.z,
  });

  const fov = THREE.MathUtils.degToRad(st.camera.fov);
  const distance = (radius / Math.sin(fov / 2)) * 1.15;

  st.camera.position.copy(center).addScaledVector(VIEW_DIR, distance);
  st.camera.near = Math.max(distance / 1000, 0.5);
  st.camera.far = distance * 10;
  st.camera.updateProjectionMatrix();
  st.controls.target.copy(center);
  st.controls.update();
}
