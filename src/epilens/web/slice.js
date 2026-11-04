// Cross sections of the 3D view, made with a clip plane
import * as THREE from "three";
import { TARGET } from "./scene.js";
import { axis } from "./plan.js";

const AXES = { axial: [0, 0, 1], coronal: [0, 1, 0], sagittal: [1, 0, 0] };

export function makeSlice({ view, plans }) {
  const modeIn = document.getElementById("slice"),
    atIn = document.getElementById("sliceAt"),
    flipBtn = document.getElementById("sliceFlip");
  let flip = false;
  flipBtn.addEventListener("click", () => (flip = !flip));
  modeIn.addEventListener("change", () => (atIn.value = 0));
  const plane = new THREE.Plane(),
    n = new THREE.Vector3();

  // Recomputed every frame, so it follows the selected electrode and any edits
  view.onFrame(() => {
    const mode = modeIn.value,
      e = plans.current && plans.selectedElectrode,
      on = !!mode && (!!e || mode in AXES);
    atIn.hidden = flipBtn.hidden = !on;
    if (!on) return view.setClip(null);
    const u = e && new THREE.Vector3(...axis(e));
    if (mode === "probe") n.copy(u);
    else if (mode === "along") {
      // the plane contains the track and the vertical axis. For a vertical track it uses the front-back axis instead
      n.crossVectors(u, new THREE.Vector3(0, 0, 1));
      if (n.lengthSq() < 1e-6) n.crossVectors(u, new THREE.Vector3(0, 1, 0));
    } else n.set(...AXES[mode]);
    n.normalize();
    const base = e ? new THREE.Vector3(...e.target.xyz) : TARGET; // through the target, or the middle of the brain if nothing is selected
    plane.setFromNormalAndCoplanarPoint(
      n,
      base.clone().addScaledVector(n, +atIn.value),
    );
    if (flip) plane.negate();
    view.setClip(plane);
  });
}
