import { initScene } from "./scene.js";
import { makeAtlas } from "./atlasjs.js";
import { Plans, contacts } from "./plan.js";
import { makeElectrodeLayer } from "./draw.js";
import { makeUI } from "./ui.js";
import { makeAdjust } from "./adjust.js";
import { makeMenu } from "./menu.js";
import { makeSlice } from "./slice.js";

const anatomy = await fetch("api/anatomy").then((r) => r.json());
const view = initScene(document.getElementById("stage"), anatomy);
const atlas = makeAtlas(anatomy);
const layer = makeElectrodeLayer(view, atlas);
const plans = new Plans();
const adjust = makeAdjust({ plans, atlas, layer, view });
makeSlice({ view, plans });
const ui = makeUI({ plans, atlas, layer, view, adjust });
const menu = makeMenu({ plans });

plans.onChange(() => {
  layer.draw(plans.current, plans.selected);
  ui.render();
  menu.render();
});
await plans.load();

// Ping the server so it stays up. Show a banner if it's unreachable or a save fails, and retry unsaved plans
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

// Only the newest tab is live
const channel = new BroadcastChannel("epilens");
channel.onmessage = (ev) => {
  if (ev.data !== "took-over" || plans.paused) return;
  plans.paused = true;
  for (const t of Object.values(plans._timers)) clearTimeout(t);
  const btn = document.createElement("button");
  btn.textContent = "Use this tab instead";
  btn.onclick = () => location.reload();
  const box = document.createElement("div");
  box.className = "stopped paused";
  box.append("epilens is open in another tab", btn);
  document.body.append(box);
};
channel.postMessage("took-over");

window.epilens = {
  view,
  atlas,
  anatomy,
  plans,
  contacts,
  ui,
  layer,
  moveEnd: adjust.moveEnd,
  ready: true,
};
