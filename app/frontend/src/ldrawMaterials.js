import * as THREE from "three";

/**
 * Guarantees every renderable in a loaded model has a material.
 *
 * LDrawLoader resolves a colour code against LDConfig.ldr and, when the code is
 * not defined there, leaves the object's material null rather than substituting
 * anything. three.js reads `material.visible` on every object it projects, so a
 * single null throws inside the render loop — and because the throw happens
 * while the render list is being built, *nothing* is drawn. One undefined colour
 * on one internal subpart takes the whole model with it.
 *
 * That is not hypothetical: the wheel assembly 3137c01 reaches u9132.dat, whose
 * contacts are colour 494, and 102 codes used by the parts library are absent
 * from the LDConfig this project ships. A model is far better off with a grey
 * contact than with no model.
 *
 * Materials are made per call and belong to the group they are applied to, so
 * the usual dispose-on-clear pass frees them like any other.
 */
export function fillMissingMaterials(root) {
  let face = null;
  let edge = null;
  let patched = 0;

  const stand_in = (object) => {
    patched += 1;
    if (object.isMesh) {
      face = face || new THREE.MeshStandardMaterial({
        color: 0x9a9a9a, roughness: 0.55, metalness: 0.1,
      });
      return face;
    }
    edge = edge || new THREE.LineBasicMaterial({ color: 0x4a4a4a });
    return edge;
  };

  root.traverse((o) => {
    if (!o.isMesh && !o.isLine && !o.isPoints) return;

    if (Array.isArray(o.material)) {
      if (o.material.every(Boolean)) return;
      o.material = o.material.map((m) => m || stand_in(o));
      return;
    }
    if (!o.material) o.material = stand_in(o);
  });

  return patched;
}
