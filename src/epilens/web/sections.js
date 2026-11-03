// 2D sections beside the 3D view via the '2D' button
import * as THREE from "three";
import { TARGET } from "./scene.js";
import { axis, contacts, dist } from "./plan.js";
import { h } from "./dom.js";

const V = (x, y, z) => new THREE.Vector3(x, y, z);
const Z = V(0, 0, 1);
// [look, up] for each plane: we look along `look` with `up` at the top, so screen right = look x up.
// Axial and coronal orientations are radiological (patient's right on the left); sagittal has anterior on the left
const PLANES = {
  coronal: () => [V(0, -1, 0), Z],
  axial: () => [V(0, 0, 1), V(0, 1, 0)],
  sagittal: () => [V(1, 0, 0), Z],
  // down the track from the entry, superior at the top (anterior for a vertical track)
  probe: (u) => {
    const up = Math.abs(u.dot(Z)) > 0.95 ? V(0, 1, 0) : Z.clone();
    return [u.clone().negate(), up.addScaledVector(u, -up.dot(u)).normalize()];
  },
  // the plane holding the track and the vertical (as slice.js), seen like the nearest of coronal and sagittal
  along: (u) => {
    const n = u.clone().cross(Z);
    if (n.lengthSq() < 1e-6) n.crossVectors(u, V(0, 1, 0));
    n.normalize();
    if (Math.abs(n.y) > Math.abs(n.x) ? n.y > 0 : n.x < 0) n.negate();
    return [n, Z.clone().addScaledVector(n, -n.z).normalize()];
  },
};
const NAMES = {
  coronal: "coronal",
  axial: "axial",
  sagittal: "sagittal",
  probe: "probe's eye",
  along: "view along",
};
const FOV = 190; // mm shown across the shorter side
const SLAB = 3; // mm: electrodes this close to the plane are drawn as in it
const SEL = "#7aa2f7",
  NORMAL = "#d5dbe6",
  OUTSIDE = "#f87171",
  ENTRY = "#e5383b",
  CURSOR = "#e0b45a";
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

// T1-like colours per voxel: white matter bright, cortex grey, deep structures tinted with their 3D colour
function colourVolume(atlas, anatomy) {
  const mesh = Object.fromEntries(anatomy.meshes.map((m) => [m.name, m.color]));
  const lut = new Uint8Array(256 * 3).fill(0);
  const set = (id, c) => lut.set(c, id * 3);
  set(0, [10, 12, 16]);
  set(1, [214, 214, 208]);
  for (const r of Object.values(atlas.regions)) {
    if (r.kind === "cortex") set(r.id, [118, 118, 122]);
    else {
      const c = new THREE.Color(mesh[r.name] || "#8a86a0");
      set(
        r.id,
        [c.r, c.g, c.b].map((v) => Math.round(v * 200)),
      );
    }
  }
  const rgb = new Uint8Array(atlas.labels.length * 3);
  atlas.labels.forEach((v, i) =>
    rgb.set(lut.subarray(v * 3, v * 3 + 3), i * 3),
  );
  return rgb;
}

export function makeSections({ view, plans, atlas, anatomy }) {
  const rgb = colourVolume(atlas, anatomy),
    [nx, ny, nz] = atlas.shape;
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
  box.append(h("div", { class: "secBar" }, "cursor", at, info));
  const panels = ["coronal", "axial"].map((mode, i) => {
    const pick = h(
      "select",
      { class: "secPlane", title: "Section plane" },
      ...Object.entries(NAMES).map(([k, t]) => h("option", { value: k }, t)),
    );
    const canvas = h("canvas"),
      hover = h("div", { class: "secHover" });
    const el = h("div", { class: "sec", id: `sec${i}` }, canvas, pick, hover);
    pick.value = mode;
    box.append(el);
    const p = { el, pick, canvas, hover, frame: null };
    canvas.addEventListener("wheel", (ev) => {
      ev.preventDefault();
      at.value = +at.value + Math.sign(-ev.deltaY) * (ev.shiftKey ? 5 : 1);
    });
    canvas.addEventListener("mousemove", (ev) => {
      const w = p.frame && p.frame.world(ev.offsetX, ev.offsetY);
      hover.textContent = w ? atlas.labelAt(w.toArray()) : "";
    });
    canvas.addEventListener("mouseleave", () => (hover.textContent = ""));
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
    info.textContent = `${at.value} mm from the target · nearest contact ${near + 1} · ${atlas.labelAt(P.toArray())}`;
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
    ctx.fillStyle = "#0b0e14";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    p.frame = null;
    if (!sel || w < 2 || hgt < 2) return;
    const [look, up] = PLANES[p.pick.value](sel.u);
    const right = look.clone().cross(up);
    // centred on the middle of the brain, as seen in this plane
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

    // Atlas image with one sample per CSS pixel (trilinear)
    const img = new ImageData(w, hgt),
      px = img.data;
    const o = world(0.5, 0.5).applyMatrix4(atlas.toVoxel),
      dx = world(1.5, 0.5).applyMatrix4(atlas.toVoxel).sub(o),
      dy = world(0.5, 1.5).applyMatrix4(atlas.toVoxel).sub(o);
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
        ) {
          px[q] = 10;
          px[q + 1] = 12;
          px[q + 2] = 16;
          continue;
        }
        const fx = vx - i,
          fy = vy - j,
          fz = vz - k;
        for (let c = 0; c < 3; c++) {
          const at = (a, b, d) =>
            rgb[(((i + a) * ny + j + b) * nz + k + d) * 3 + c];
          const c00 = at(0, 0, 0) * (1 - fz) + at(0, 0, 1) * fz,
            c01 = at(0, 1, 0) * (1 - fz) + at(0, 1, 1) * fz,
            c10 = at(1, 0, 0) * (1 - fz) + at(1, 0, 1) * fz,
            c11 = at(1, 1, 0) * (1 - fz) + at(1, 1, 1) * fz;
          px[q + c] =
            (c00 * (1 - fy) + c01 * fy) * (1 - fx) +
            (c10 * (1 - fy) + c11 * fy) * fx;
        }
      }
    const off = new OffscreenCanvas(w, hgt);
    off.getContext("2d").putImageData(img, 0, 0);
    ctx.drawImage(off, 0, 0, canvas.width, canvas.height);
    ctx.scale(dpr, dpr);

    // show selected electrode in full and contacts of others if they're within the plane
    const st = plans.current.settings;
    ctx.font = "12px system-ui, sans-serif";
    ctx.lineCap = "round";
    for (const e of plans.current.electrodes) {
      const isSel = e.id === sel.e.id,
        col = isSel ? SEL : NORMAL;
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
        ctx.arc(q[0], q[1], 3, 0, 2 * Math.PI);
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
        ctx.fillText(e.name, lx + 6, ly - 6);
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
    ctx.fillStyle = "#8592a6";
    ctx.font = "600 13px system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(letter(up), w / 2, 40);
    ctx.fillText(letter(up.clone().negate()), w / 2, hgt - 12);
    ctx.fillText(letter(right.clone().negate()), 12, hgt / 2);
    ctx.fillText(letter(right), w - 12, hgt / 2);
  }

  return { panels, at };
}

// [t0, t1] of a segment whose distance from the plane goes d0 -> d1, where |d| <= SLAB
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
