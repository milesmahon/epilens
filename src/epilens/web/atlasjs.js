import * as THREE from "three";
import { bytes } from "./scene.js";

// Atlas lookups in the browser, matching atlas.py
const rint = (x) => {
  // round half to even, like np.rint
  const f = Math.floor(x),
    d = x - f;
  return d > 0.5 || (d === 0.5 && f % 2 !== 0) ? f + 1 : f;
};

export function makeAtlas(anatomy) {
  const labels = bytes(anatomy.labels);
  const [nx, ny, nz] = anatomy.shape;
  const regions = Object.fromEntries(anatomy.regions.map((r) => [r.id, r]));
  const toWorld = new THREE.Matrix4().set(...anatomy.affine.flat());
  const toVoxel = toWorld.clone().invert();
  const voxel = (p) =>
    new THREE.Vector3(...p).applyMatrix4(toVoxel).toArray().map(rint);
  const at = (i, j, k) =>
    i < 0 || j < 0 || k < 0 || i >= nx || j >= ny || k >= nz
      ? 0
      : labels[(i * ny + j) * nz + k]; // numpy C order
  const value = (p) => at(...voxel(p));
  const labelAt = (p) => {
    const v = value(p);
    return v === 0
      ? "outside brain"
      : v === 1
        ? "white matter"
        : regions[v].name;
  };
  // Nearest cortical region within `radius` voxels. Searches in the same order as atlas.py
  const surfaceRegion = (p, radius = 3) => {
    const [i0, j0, k0] = voxel(p);
    const P = new THREE.Vector3(...p),
      w = new THREE.Vector3();
    let best = null,
      bestD = Infinity;
    for (let i = i0 - radius; i <= i0 + radius; i++)
      for (let j = j0 - radius; j <= j0 + radius; j++)
        for (let k = k0 - radius; k <= k0 + radius; k++) {
          const r = regions[at(i, j, k)];
          if (!r || r.kind !== "cortex") continue;
          const d = w.set(i, j, k).applyMatrix4(toWorld).distanceToSquared(P);
          if (d < bestD) {
            best = r;
            bestD = d;
          }
        }
    return best;
  };
  const inside = (p) => value(p) > 0;
  // Last point inside the brain going from p along u
  const exitPoint = (p, u) => {
    let last = p;
    for (let s = 0.5; s < 300; s += 0.5) {
      const q = p.map((v, i) => v + s * u[i]);
      if (!inside(q)) break;
      last = q;
    }
    return last;
  };
  return {
    labelAt,
    regionAt: (p) => regions[value(p)] || null,
    surfaceRegion,
    inside,
    exitPoint,
  };
}
