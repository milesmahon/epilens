// Move electrode ends: drag handles in 3D, nudge buttons
import { axis, dist } from "./plan.js";

const MIN_LENGTH = 1; // mm

export function makeAdjust({ plans, atlas, layer, view }) {
  const { THREE, camera, controls, renderer } = view;

  const endText = (r, xyz) => (r ? `${r.hemi} ${r.base}` : atlas.labelAt(xyz));

  function surfaceEntry(e, target, p) {
    const d = p.map((v, i) => v - target[i]),
      n = Math.hypot(...d);
    const s =
      n > 0
        ? atlas.exitPoint(
            target,
            d.map((v) => v / n),
          )
        : p;
    const xyz = dist(s, target) >= MIN_LENGTH ? s : p; // target outside the brain: leave the entry alone
    const r = atlas.surfaceRegion(xyz);
    return {
      ...e.entry,
      xyz,
      text: endText(r, xyz),
      abbrev: r ? r.hemi + r.abbrev : "",
      region: r ? r.name : null,
      mode: "moved",
    };
  }
  function targetAt(e, xyz) {
    const r = atlas.regionAt(xyz);
    return {
      ...e.target,
      xyz,
      text: endText(r, xyz),
      region: r ? r.name : null,
      mode: "moved",
    };
  }

  function moveEnd(eid, which, xyz) {
    const e = plans.current.electrodes.find((x) => x.id === eid);
    if (!e) return;
    const other = which === "target" ? e.entry.xyz : e.target.xyz;
    let p = xyz.map(Number);
    if (!(dist(p, other) >= MIN_LENGTH)) {
      // too short: keep the direction, MIN_LENGTH apart
      const u = axis(e),
        sign = which === "target" ? -1 : 1;
      p = other.map((v, i) => v + sign * MIN_LENGTH * u[i]);
    }
    plans.update(
      eid,
      which === "entry"
        ? { entry: surfaceEntry(e, e.target.xyz, p) }
        : { target: targetAt(e, p) },
    );
  }

  // k: "x+", "y-", ... moves `end` 1 mm; "deeper"/"shallower" slides the electrode along its track
  function nudge(e, k, end) {
    if (k === "deeper" || k === "shallower") {
      const u = axis(e),
        s = k === "deeper" ? -1 : 1;
      const t = e.target.xyz.map((v, i) => v + s * u[i]);
      if (dist(t, e.entry.xyz) >= MIN_LENGTH) moveEnd(e.id, "target", t);
      return;
    }
    const p = e[end].xyz.slice();
    p["xyz".indexOf(k[0])] += k[1] === "+" ? 1 : -1;
    moveEnd(e.id, end, p);
  }

  // drag the handle and update electrode on release
  const canvas = renderer.domElement,
    plane = new THREE.Plane(),
    hitPt = new THREE.Vector3();
  let drag = null;
  canvas.addEventListener(
    "pointerdown",
    (ev) => {
      const hit = layer.pick(ev);
      if (!hit || !hit.handle) return;
      drag = { eid: hit.eid, which: hit.handle };
      const e = plans.current.electrodes.find((x) => x.id === hit.eid);
      plane.setFromNormalAndCoplanarPoint(
        camera.getWorldDirection(new THREE.Vector3()),
        new THREE.Vector3(...e[hit.handle].xyz),
      );
      controls.enabled = false; // stop the camera rotating while dragging
      canvas.setPointerCapture(ev.pointerId);
    },
    { capture: true },
  );
  canvas.addEventListener("pointermove", (ev) => {
    if (!drag || !view.rayAt(ev).ray.intersectPlane(plane, hitPt)) return;
    drag.to = hitPt.toArray();
    layer.previewEnd(
      plans.current.electrodes.find((x) => x.id === drag.eid),
      drag.which,
      drag.to,
    );
  });
  const endDrag = () => {
    if (!drag) return;
    const { eid, which, to } = drag;
    drag = null;
    controls.enabled = true;
    if (to) moveEnd(eid, which, to);
  };
  canvas.addEventListener("pointerup", endDrag);
  canvas.addEventListener("pointercancel", endDrag);

  return { moveEnd, nudge };
}
