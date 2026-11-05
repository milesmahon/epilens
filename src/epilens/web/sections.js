// 2D sections shown beside the 3D view
import * as THREE from "three";
import { TARGET, bytes } from "./scene.js";
import { axis, contacts, dist } from "./plan.js";
import { h } from "./dom.js";

const V = (x, y, z) => new THREE.Vector3(x, y, z);
const Z = V(0, 0, 1);
// Each plane gives [look, up]. We look along `look` with `up` at the top of the screen, so screen right = look × up.
// Axial and coronal are shown radiologically, with the patient's right on the left. Sagittal has anterior on the left.
const PLANES = {
  coronal: () => [V(0, -1, 0), Z],
  axial: () => [V(0, 0, 1), V(0, 1, 0)],
  sagittal: () => [V(1, 0, 0), Z],
  // Look down the track from the entry, with superior at the top. For a vertical track, anterior is at the top
  probe: (u) => {
    const up = Math.abs(u.dot(Z)) > 0.95 ? V(0, 1, 0) : Z.clone();
    return [u.clone().negate(), up.addScaledVector(u, -up.dot(u)).normalize()];
  },
  // The plane that contains the track and the vertical axis, the same plane slice.js uses.
  // It is turned to match whichever of coronal or sagittal it is closer to
  along: (u) => {
    const n = u.clone().cross(Z);
    if (n.lengthSq() < 1e-6) n.crossVectors(u, V(0, 1, 0));
    n.normalize();
    if (Math.abs(n.y) > Math.abs(n.x) ? n.y > 0 : n.x < 0) n.negate();
    return [n, Z.clone().addScaledVector(n, -n.z).normalize()];
  },
};
const NAMES = {
  coronal: "Coronal",
  axial: "Axial",
  sagittal: "Sagittal",
  probe: "Probe's eye",
  along: "View along",
};
const FOV = 190; // mm shown across the shorter side
const SLAB = 3; // mm. Electrodes this close to the plane are drawn as if they were in it
const css = (name) =>
  getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const NORMAL = "#d5dbe6",
  OUTSIDE = "#f87171",
  ENTRY = "#e5383b",
  CURSOR = "#e0b45a";
const FONT = `"Source Sans 3", "Segoe UI", "Helvetica Neue", Arial, sans-serif`;
const LETTER = [
  ["R", "L"],
  ["A", "P"],
  ["S", "I"],
];
const letter = (v) => {
  const a = [0, 1, 2].reduce(
    (m, i) =>
      Math.abs(v.getComponent(i)) > Math.abs(v.getComponent(m)) ? i : m,
    0,
  );
  return LETTER[a][v.getComponent(a) > 0 ? 0 : 1];
};

export function makeSections({ view, plans, atlas, anatomy }) {
  // Template T1 is loaded the first time a section is drawn
  let scan = null,
    loading = false;
  function loadScan() {
    if (loading) return;
    loading = true;
    fetch("api/t1")
      .then((r) => r.json())
      .then((t) => {
        scan = {
          shape: t.shape,
          toVoxel: new THREE.Matrix4().set(...t.affine.flat()).invert(),
          data: bytes(t.data),
        };
        lastKey = ""; // redraw
      })
      .catch(() => (loading = false)); // try again on the next draw
  }
  const box = document.getElementById("sections"),
    toggle = document.getElementById("split");
  const at = h("input", {
    type: "range",
    id: "secAt",
    title: "Move the cursor along the electrode (or scroll over a section)",
    min: 0,
    max: 0,
    step: 1,
    value: 0,
  });
  const info = h("span", { id: "secInfo" });
  const isPatient = anatomy.space === "patient";
  box.append(
    h(
      "div",
      { class: "sourceNote" + (isPatient ? "" : " template") },
      isPatient ? "Patient T1" : "Template T1 (ICBM152)",
    ),
    h("div", { class: "secBar" }, "Along the lead", at, info),
  );
  const panels = ["coronal", "axial"].map((mode, i) => {
    const pick = h(
      "select",
      { class: "secPlane", title: "Section plane" },
      ...Object.entries(NAMES).map(([k, t]) => h("option", { value: k }, t)),
    );
    const canvas = h("canvas");
    const el = h("div", { class: "sec", id: `sec${i}` }, canvas, pick);
    pick.value = mode;
    box.append(el);
    const p = { el, pick, canvas, frame: null };
    canvas.addEventListener("wheel", (ev) => {
      ev.preventDefault();
      at.value = +at.value + Math.sign(-ev.deltaY) * (ev.shiftKey ? 5 : 1);
    });
    return p;
  });

  let on = false;
  try {
    on = localStorage.getItem("epilens.split") === "1";
  } catch (e) {
    /* storage blocked */
  }
  const show = () => {
    box.hidden = !on;
    toggle.classList.toggle("on", on);
  };
  toggle.addEventListener("click", () => {
    on = !on;
    try {
      localStorage.setItem("epilens.split", on ? "1" : "0");
    } catch (e) {
      /* storage blocked */
    }
    show();
  });
  show();

  // The cursor in 3D
  const marker = new THREE.Mesh(
    new THREE.SphereGeometry(2, 16, 12),
    new THREE.MeshBasicMaterial({ color: CURSOR, depthTest: false }),
  );
  marker.renderOrder = 7;
  view.scene.add(marker);

  let lastSel = null,
    lastKey = "";
  view.onFrame(() => {
    const e = on && plans.current ? plans.selectedElectrode : null;
    marker.visible = !!e;
    if (!on) return;
    if ((e && e.id) !== lastSel) {
      lastSel = e && e.id;
      at.value = 0; // new electrode starts at its target
    }
    if (e) at.max = Math.ceil(dist(e.target.xyz, e.entry.xyz));
    at.hidden = !e;
    const key = JSON.stringify([
      panels.map((p) => [
        p.pick.value,
        p.canvas.clientWidth,
        p.canvas.clientHeight,
      ]),
      at.value,
      e && e.id,
      plans.current && plans.current.electrodes,
      plans.current && plans.current.settings,
      devicePixelRatio,
    ]);
    if (key === lastKey) return;
    lastKey = key;
    if (!e) {
      info.textContent = "Select an electrode to see sections through it";
      for (const p of panels) draw(p, null);
      return;
    }
    const u = V(...axis(e)),
      P = V(...e.target.xyz).addScaledVector(u, +at.value);
    marker.position.copy(P);
    const cs = contacts(e, plans.current.settings);
    const near = cs.reduce(
      (b, c, k) => (dist(c, P.toArray()) < dist(cs[b], P.toArray()) ? k : b),
      0,
    );
    info.textContent = `${at.value} mm from the target · nearest contact ${near + 1}`;
    for (const p of panels) draw(p, { e, u, P });
  });

  function draw(p, sel) {
    const { canvas } = p,
      w = canvas.clientWidth,
      hgt = canvas.clientHeight,
      dpr = devicePixelRatio;
    canvas.width = Math.max(1, Math.round(w * dpr));
    canvas.height = Math.max(1, Math.round(hgt * dpr));
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    p.frame = null;
    if (!sel || w < 2 || hgt < 2) return;
    const [look, up] = PLANES[p.pick.value](sel.u);
    const right = look.clone().cross(up);
    // centred on the middle of the brain as seen in this plane
    const C = TARGET.clone().addScaledVector(
      look,
      sel.P.clone().sub(TARGET).dot(look),
    );
    const s = Math.min(w, hgt) / FOV; // CSS px per mm
    const world = (x, y) =>
      C.clone()
        .addScaledVector(right, (x - w / 2) / s)
        .addScaledVector(up, (hgt / 2 - y) / s);
    const screen = (q) => {
      const d = V(...q).sub(C);
      return [w / 2 + d.dot(right) * s, hgt / 2 - d.dot(up) * s, d.dot(look)]; // C is on the plane
    };
    p.frame = { world };

    // Draw the T1, one sample per pixel
    if (!scan) loadScan();
    const img = new ImageData(w, hgt),
      px = img.data;
    const [nx, ny, nz] = scan ? scan.shape : [0, 0, 0],
      vol = scan && scan.data;
    const toVoxel = scan ? scan.toVoxel : new THREE.Matrix4();
    const o = world(0.5, 0.5).applyMatrix4(toVoxel),
      dx = world(1.5, 0.5).applyMatrix4(toVoxel).sub(o),
      dy = world(0.5, 1.5).applyMatrix4(toVoxel).sub(o);
    const at = (i, j, k) => vol[(i * ny + j) * nz + k];
    for (let y = 0; y < hgt; y++)
      for (let x = 0; x < w; x++) {
        const vx = o.x + x * dx.x + y * dy.x,
          vy = o.y + x * dx.y + y * dy.y,
          vz = o.z + x * dx.z + y * dy.z;
        const i = Math.floor(vx),
          j = Math.floor(vy),
          k = Math.floor(vz),
          q = (y * w + x) * 4;
        px[q + 3] = 255;
        if (
          i < 0 ||
          j < 0 ||
          k < 0 ||
          i >= nx - 1 ||
          j >= ny - 1 ||
          k >= nz - 1
        )
          continue; // outside the scan: black
        const fx = vx - i,
          fy = vy - j,
          fz = vz - k;
        const c00 = at(i, j, k) * (1 - fz) + at(i, j, k + 1) * fz,
          c01 = at(i, j + 1, k) * (1 - fz) + at(i, j + 1, k + 1) * fz,
          c10 = at(i + 1, j, k) * (1 - fz) + at(i + 1, j, k + 1) * fz,
          c11 = at(i + 1, j + 1, k) * (1 - fz) + at(i + 1, j + 1, k + 1) * fz;
        px[q] =
          px[q + 1] =
          px[q + 2] =
            (c00 * (1 - fy) + c01 * fy) * (1 - fx) +
            (c10 * (1 - fy) + c11 * fy) * fx;
      }
    const off = new OffscreenCanvas(w, hgt);
    off.getContext("2d").putImageData(img, 0, 0);
    ctx.drawImage(off, 0, 0, canvas.width, canvas.height);
    ctx.scale(dpr, dpr);
    // dark outline on everything drawn over the scan
    ctx.shadowColor = "rgba(0, 0, 0, 0.9)";
    ctx.shadowBlur = 4;

    // Show the selected electrode in full, and other electrodes' contacts only where they are in the plane
    const st = plans.current.settings;
    ctx.font = `14px ${FONT}`;
    ctx.lineCap = "round";
    for (const e of plans.current.electrodes) {
      const isSel = e.id === sel.e.id,
        col = isSel ? css("--film") : NORMAL;
      const cs = contacts(e, st),
        last = cs[cs.length - 1];
      const tip = screen(e.target.xyz),
        tail = screen(
          dist(last, e.target.xyz) > dist(e.entry.xyz, e.target.xyz)
            ? last
            : e.entry.xyz,
        );
      const [t0, t1] = inSlab(tip[2], tail[2]);
      const lerp = (t) => [
        tip[0] + t * (tail[0] - tip[0]),
        tip[1] + t * (tail[1] - tip[1]),
      ];
      ctx.strokeStyle = col;
      if (isSel) {
        ctx.globalAlpha = 0.45;
        ctx.setLineDash([4, 4]);
        line(ctx, tip, tail, 1);
        ctx.setLineDash([]);
        ctx.globalAlpha = 1;
      }
      if (t0 < t1) line(ctx, lerp(t0), lerp(t1), isSel ? 2 : 1.5);
      for (const c of cs) {
        const q = screen(c),
          inside = Math.abs(q[2]) <= SLAB;
        if (!inside && !isSel) continue;
        ctx.fillStyle = ctx.strokeStyle = atlas.inside(c) ? col : OUTSIDE;
        ctx.globalAlpha = inside ? 1 : 0.5;
        ctx.beginPath();
        ctx.arc(q[0], q[1], isSel ? 3.5 : 3, 0, 2 * Math.PI);
        inside ? ctx.fill() : ctx.stroke();
      }
      ctx.globalAlpha = 1;
      if (isSel) {
        const en = screen(e.entry.xyz);
        ctx.fillStyle = ENTRY;
        ctx.beginPath();
        ctx.arc(en[0], en[1], 3.5, 0, 2 * Math.PI);
        ctx.fill();
      }
      if (isSel || t0 < t1) {
        const [lx, ly] = isSel ? tail : lerp((t0 + t1) / 2);
        ctx.fillStyle = col;
        if (isSel) ctx.font = `700 15px ${FONT}`;
        ctx.fillText(e.name, lx + 6, ly - 6);
        if (isSel) ctx.font = `14px ${FONT}`;
      }
    }
    // cursor
    const c = screen(sel.P.toArray());
    ctx.strokeStyle = CURSOR;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(c[0] - 9, c[1]);
    ctx.lineTo(c[0] - 4, c[1]);
    ctx.moveTo(c[0] + 4, c[1]);
    ctx.lineTo(c[0] + 9, c[1]);
    ctx.moveTo(c[0], c[1] - 9);
    ctx.lineTo(c[0], c[1] - 4);
    ctx.moveTo(c[0], c[1] + 4);
    ctx.lineTo(c[0], c[1] + 9);
    ctx.stroke();
    // orientation letters
    ctx.fillStyle = "#c9d1da";
    ctx.font = `600 15px ${FONT}`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(letter(up), w / 2, 40);
    ctx.fillText(letter(up.clone().negate()), w / 2, hgt - 12);
    ctx.fillText(letter(right.clone().negate()), 12, hgt / 2);
    ctx.fillText(letter(right), w - 12, hgt / 2);
  }

  return { panels, at };
}

// A segment's distance from the plane goes from d0 to d1. Return [t0, t1], the part of it where |d| <= SLAB
function inSlab(d0, d1) {
  if (Math.abs(d1 - d0) < 1e-9) return Math.abs(d0) <= SLAB ? [0, 1] : [1, 0];
  const a = (-SLAB - d0) / (d1 - d0),
    b = (SLAB - d0) / (d1 - d0);
  return [Math.max(0, Math.min(a, b)), Math.min(1, Math.max(a, b))];
}

function line(ctx, a, b, width) {
  ctx.lineWidth = width;
  ctx.beginPath();
  ctx.moveTo(a[0], a[1]);
  ctx.lineTo(b[0], b[1]);
  ctx.stroke();
}
