import { initScene } from "./scene.js";
import { makeAtlas } from "./atlasjs.js";
import { Plans, contacts, brainKey } from "./plan.js";
import { makeElectrodeLayer } from "./draw.js";
import { makeUI, rememberTabs } from "./ui.js";
import { makeAdjust } from "./adjust.js";
import { makeMenu } from "./menu.js";
import { makeSlice } from "./slice.js";
import { makeSections } from "./sections.js";

const plans = new Plans();

// Only the newest browser tab can make changes
const channel = new BroadcastChannel("epilens");
channel.onmessage = async (ev) => {
  // an older tab just saved edits this tab loaded too early to see
  if (ev.data === "saved") return plans.edited || location.reload();
  if (ev.data !== "took-over" || plans.paused) return;
  // send this tab's waiting edits before stopping
  const waiting = plans.list.filter(
    (p) => p.id in plans._timers || plans.unsaved.has(p.id),
  );
  const saved = await Promise.all(waiting.map((p) => plans.saveNow(p)));
  plans.paused = true;
  for (const t of Object.values(plans._timers)) clearTimeout(t);
  if (saved.some(Boolean)) channel.postMessage("saved");
  const btn = document.createElement("button");
  btn.textContent = "Use this tab instead";
  btn.onclick = () => location.reload();
  const box = document.createElement("div");
  box.className = "stopped paused";
  box.append("epilens is open in another tab", btn);
  document.body.append(box);
};
channel.postMessage("took-over");

await plans.load();
const loadedKey = brainKey(plans.current);
const anatomy = await fetch(`api/anatomy?plan=${plans.current.id}`).then((r) =>
  r.json(),
);
const view = initScene(document.getElementById("stage"), anatomy);
const isPatient = anatomy.space === "patient";
const brainNote = document.getElementById("brainNote");
brainNote.textContent = isPatient
  ? "Brain reconstructed from imaging"
  : "Template brain (MNI152)";
brainNote.classList.toggle("template", !isPatient);
const atlas = makeAtlas(anatomy);
const layer = makeElectrodeLayer(view, atlas);
showLandmarks(anatomy.landmarks || {});
const adjust = makeAdjust({
  plans,
  atlas,
  layer,
  view,
  frozen: !!anatomy.missing,
});
makeSlice({ view, plans });
const sections = makeSections({ view, plans, atlas, anatomy });
const ui = makeUI({ plans, atlas, layer, view, adjust, anatomy });
const menu = makeMenu({ plans, anatomy });

plans.onChange(() => {
  layer.draw(plans.current, plans.selected);
  ui.render();
  menu.render();
});
plans._emit(); // plans loaded before these listeners existed

// The patient's AC and PC
function showLandmarks(landmarks) {
  const { THREE } = view;
  const marks = Object.entries(landmarks).map(([name, xyz]) => {
    const ball = new THREE.Mesh(
      new THREE.SphereGeometry(1.5, 16, 12),
      new THREE.MeshBasicMaterial({ color: 0xf5d90a, depthTest: false }),
    );
    ball.position.set(...xyz);
    ball.renderOrder = 7;
    view.scene.add(ball);
    const el = document.createElement("div");
    el.className = "elLabel landmark";
    el.textContent = name;
    document.getElementById("stage").append(el); // not in #labels, which the electrode layer redraws
    return { el, at: xyz.map((v, i) => v + (i === 2 ? 4 : 0)) };
  });
  if (marks.length)
    view.onFrame(() => {
      for (const m of marks) {
        const p = view.toScreen(m.at);
        m.el.style.display = p ? "block" : "none";
        if (p)
          m.el.style.transform = `translate(${p[0]}px, ${p[1]}px) translate(-50%, -50%)`;
      }
    });
}

// new page on new plan
async function reloadOnto(next) {
  const gone = !plans.list.includes(plans.current); // the plan being closed has nothing to save
  const ok =
    (gone || (await plans.saveNow(plans.current))) &&
    (await plans.saveNow(next));
  if (!ok) return; // couldn't save, so stay here (the banner says why)
  try {
    localStorage.setItem("epilens.currentPlan", next.id);
  } catch (e) {
    /* storage blocked */
  }
  rememberTabs(next.id);
  location.reload();
}
plans.beforeSwitch = (next) =>
  brainKey(next) === loadedKey ? false : reloadOnto(next);

// Ping the server so it stays running. Show a banner if it can't be reached or a save fails, and retry unsaved plans
const missing = document.getElementById("missing");
missing.hidden = !anatomy.missing;
const banner = document.getElementById("offline");
let online = true;
function showStatus() {
  const msg = !online
    ? "Can't reach epilens, so changes are not being saved. Start it again (uv run epilens); this page reconnects by itself."
    : plans.saveError
      ? `Couldn't save this plan (${plans.saveError}) — changes are not being saved. Try Export CSV to keep a copy.`
      : "";
  banner.textContent = msg;
  banner.hidden = !msg;
}
plans.onSaveStatus = showStatus;
async function ping() {
  const ok = await fetch("api/ping").then(
    (r) => r.ok,
    () => false,
  );
  if (ok && plans.unsaved.size) plans.flush();
  online = ok;
  showStatus();
  setTimeout(ping, ok ? 5000 : 1500);
}
ping();

window.epilens = {
  view,
  atlas,
  anatomy,
  plans,
  contacts,
  ui,
  layer,
  sections,
  moveEnd: adjust.moveEnd,
  attachPatient: menu.attachPatient,
  ready: true,
};
