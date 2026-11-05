// Draws electrodes in 3D (track, contacts, entry spot, labels and drag handles) and finds what the mouse is over
import { axis, contacts, dist } from "./plan.js";

const HANDLE_R = 2.6;
const SEL = () =>
  getComputedStyle(document.documentElement)
    .getPropertyValue("--accent")
    .trim();
const PLATINUM = 0x5f6c76,
  SHAFT = 0xaab6be,
  OUTSIDE = 0xc62828,
  ENTRY = 0xe5383b;
const ENTRY_R = 2.2; // mm
const FISSURE_MM = 2.5; // mm. If the track meets the cortex deeper than this, the entry is down a sulcus
const VISIBLE_SCALE = 2; // draw contacts wider than real so they're visible

export function makeElectrodeLayer(view, atlas) {
  const { THREE, scene, camera } = view;
  const root = new THREE.Group();
  scene.add(root);
  const labelsEl = document.getElementById("labels");
  let labels = [],
    pickables = [],
    rings = [],
    handles = {},
    preview = null;
  const Y = new THREE.Vector3(0, 1, 0),
    Z = new THREE.Vector3(0, 0, 1);

  // Where the track meets the cortex mesh. Cached because raycasting is slow
  const surfaceCache = new Map(),
    surfRay = new THREE.Raycaster();
  function surfaceHit(e, u) {
    const key = [...e.target.xyz, ...e.entry.xyz]
      .map((v) => v.toFixed(2))
      .join(",");
    if (!surfaceCache.has(key)) {
      if (surfaceCache.size > 500) surfaceCache.clear();
      const from = e.entry.xyz.map((v, i) => v + 25 * u[i]);
      surfRay.set(new THREE.Vector3(...from), new THREE.Vector3(...u).negate());
      surfRay.far = 25 + dist(e.entry.xyz, e.target.xyz);
      const sides = view.hull.map((m) => m.material.side);
      view.hull.forEach((m) => {
        m.material.side = THREE.DoubleSide;
      }); // hit both sides of the faces
      const hit = surfRay.intersectObjects(view.hull, false)[0];
      view.hull.forEach((m, i) => {
        m.material.side = sides[i];
      });
      // down a sulcus there is no spot on the surface to paint, so draw a disc instead
      const ok = hit && dist(hit.point.toArray(), e.entry.xyz) <= FISSURE_MM;
      surfaceCache.set(key, ok ? { p: hit.point.clone() } : null);
    }
    return surfaceCache.get(key);
  }

  function clear() {
    for (const o of [...root.children]) {
      root.remove(o);
      o.traverse((x) => {
        x.geometry?.dispose();
        x.material?.dispose();
      });
    }
    labelsEl.replaceChildren();
    labels = [];
    pickables = [];
    rings = [];
    handles = {};
    preview = null;
  }

  let spots = [];
  function draw(plan, selectedId) {
    clear();
    spots = [];
    const st = plan.settings;
    for (const e of plan.electrodes) {
      const sel = e.id === selectedId;
      const u = axis(e),
        cs = contacts(e, st);
      const g = new THREE.Group();
      g.userData = { eid: e.id };
      // the track runs from the target to just past whichever is furthest out of the last contact, the entry and the brain surface
      const far = [
        atlas.exitPoint(e.target.xyz, u),
        e.entry.xyz,
        cs[cs.length - 1],
      ].reduce((a, b) =>
        dist(b, e.target.xyz) > dist(a, e.target.xyz) ? b : a,
      );
      const outer = far.map((v, i) => v + 6 * u[i]); // a little past the surface
      const r = (st.diameter_mm / 2) * VISIBLE_SCALE;
      const quat = new THREE.Quaternion().setFromUnitVectors(
        Y,
        new THREE.Vector3(...u),
      );
      const len = dist(e.target.xyz, outer);
      const track = new THREE.Mesh(
        new THREE.CylinderGeometry(r * 0.62, r * 0.62, len, 12, 1),
        new THREE.MeshStandardMaterial({
          color: sel ? SEL() : SHAFT,
          roughness: 0.6,
          transparent: true,
          opacity: sel ? 0.55 : 0.5,
          depthWrite: false,
        }),
      );
      track.position.set(...e.target.xyz.map((v, i) => (v + outer[i]) / 2));
      track.quaternion.copy(quat);
      track.userData = { eid: e.id };
      g.add(track);
      cs.forEach((c, k) => {
        const inside = atlas.inside(c);
        const m = new THREE.Mesh(
          new THREE.CylinderGeometry(r, r, st.contact_len_mm, 16),
          new THREE.MeshStandardMaterial({
            color: !inside ? OUTSIDE : sel ? SEL() : PLATINUM,
            metalness: 0.35,
            roughness: 0.32,
          }),
        );
        m.position.set(...c);
        m.quaternion.copy(quat);
        m.userData = { eid: e.id, k: k + 1, xyz: c };
        g.add(m);
        pickables.push(m);
      });
      // The entry is a red spot painted on the cortex by scene.js, or a red disc if it is down a sulcus
      const s = surfaceHit(e, u);
      if (s) spots.push({ eid: e.id, at: s.p.toArray(), dir: u });
      else {
        const dot = new THREE.Mesh(
          new THREE.CircleGeometry(ENTRY_R, 28),
          new THREE.MeshBasicMaterial({ color: ENTRY, side: THREE.DoubleSide }),
        );
        dot.position.set(...e.entry.xyz);
        dot.quaternion.setFromUnitVectors(Z, new THREE.Vector3(...u));
        dot.renderOrder = 4;
        dot.userData = { eid: e.id };
        g.add(dot);
        if (sel) handles.dot = dot;
      }
      if (sel) {
        for (const which of ["target", "entry"]) {
          const p = e[which].xyz;
          const ball = new THREE.Mesh(
            new THREE.SphereGeometry(HANDLE_R, 16, 12),
            new THREE.MeshBasicMaterial({
              color: SEL(),
              transparent: true,
              opacity: 0.18,
              depthTest: false,
            }),
          );
          ball.position.set(...p);
          ball.renderOrder = 5;
          ball.userData = { eid: e.id, handle: which };
          const ring = new THREE.Mesh(
            new THREE.TorusGeometry(HANDLE_R, 0.35, 8, 32),
            new THREE.MeshBasicMaterial({ color: SEL(), depthTest: false }),
          );
          ring.position.set(...p);
          ring.renderOrder = 6;
          if (which === "entry")
            ((ring.material.opacity = 0.75),
              (ring.material.transparent = true));
          g.add(ball, ring);
          rings.push(ring);
          handles[which] = [ball, ring];
        }
      }
      root.add(g);
      const el = document.createElement("div");
      el.className = "elLabel" + (sel ? " sel" : "");
      el.textContent = e.name;
      labelsEl.append(el);
      // move the selected label out so it does not cover the entry handle
      const off = sel ? 16 : 3;
      if (sel) {
        const leader = new THREE.Line(
          new THREE.BufferGeometry().setFromPoints([
            new THREE.Vector3(...outer),
            new THREE.Vector3(...outer.map((v, i) => v + (off - 3) * u[i])),
          ]),
          new THREE.LineBasicMaterial({
            color: SEL(),
            transparent: true,
            opacity: 0.8,
          }),
        );
        g.add(leader);
      }
      labels.push({ el, at: outer.map((v, i) => v + off * u[i]) });
    }
    view.setEntries(spots);
  }

  view.onFrame(() => {
    for (const r of rings) r.quaternion.copy(camera.quaternion); // rings face the viewer
    for (const l of labels) {
      const p = view.toScreen(l.at);
      l.el.style.display = p ? "block" : "none";
      if (p)
        l.el.style.transform = `translate(${p[0]}px, ${p[1]}px) translate(-50%, -50%)`;
    }
  });

  function pick(ev) {
    const hits = view
      .rayAt(ev)
      .intersectObjects(root.children, true)
      .filter((x) => x.object.userData.eid);
    const handle = hits.find((x) => x.object.userData.handle); // prefer handles, because they are drawn on top
    const hit = handle || hits[0];
    return hit ? hit.object.userData : null;
  }
  // During a drag, move the handle and draw a line to the other end
  function previewEnd(e, which, xyz) {
    for (const m of handles[which] || []) m.position.set(...xyz);
    if (which === "entry") {
      // hide the old entry spot until release
      if (handles.dot) handles.dot.visible = false;
      view.setEntries(spots.filter((s) => s.eid !== e.id));
    }
    if (!preview) {
      preview = new THREE.Line(
        new THREE.BufferGeometry(),
        new THREE.LineBasicMaterial({ color: SEL(), depthTest: false }),
      );
      preview.renderOrder = 6;
      root.add(preview);
    }
    const fixed = which === "entry" ? e.target.xyz : e.entry.xyz;
    preview.geometry.setFromPoints([
      new THREE.Vector3(...fixed),
      new THREE.Vector3(...xyz),
    ]);
  }
  return { draw, pick, previewEnd, root };
}
