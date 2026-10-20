// UI elements: text input box, electrode list, tabs, 3D model controls
import { contacts, electrodeFrom, spacingOf } from "./plan.js";
import { $, esc, h } from "./dom.js";

const fmt = (xyz) => xyz.map((v) => v.toFixed(1)).join(" ");
const NUDGES = [
  ["x-", "◀ L"],
  ["x+", "R ▶"],
  ["y+", "A ▲"],
  ["y-", "P ▼"],
  ["z+", "S ⤒"],
  ["z-", "I ⤓"],
  ["deeper", "deeper"],
  ["shallower", "shallower"],
];

export function makeUI({ plans, atlas, layer, view, adjust }) {
  const cmd = $("cmd"),
    readAs = $("readAs"),
    pickEl = $("pick"),
    list = $("list"),
    tabs = $("tabs");
  const spacing = () => plans.current.settings.spacing_mm;

  async function parse(text) {
    try {
      const r = await fetch(
        `api/parse?q=${encodeURIComponent(text)}&spacing=${spacing()}`,
      );
      return await r.json();
    } catch (err) {
      return {
        ok: false,
        read_as: "Can't reach epilens. Start it again with: uv run epilens",
        options: [],
        warnings: [],
      };
    }
  }

  function showReadAs(p) {
    if (!p || (!p.ok && !p.read_as && !p.question)) {
      readAs.innerHTML = "";
      return;
    }
    if (p.ok) {
      const n = p.n_contacts
        ? `${p.n_contacts} contacts`
        : `${p.default_contacts} contacts (fits)`;
      readAs.innerHTML =
        `Read as → entry: <b>${esc(p.entry.text)}</b> · target: <b>${esc(p.target.text)}</b> · <b>${n}</b>` +
        p.warnings.map((w) => ` <span class="w">⚠ ${esc(w)}</span>`).join("");
    } else if (p.question) {
      readAs.innerHTML = `<span class="w">${esc(p.question)} — press Enter to choose</span>`;
    } else {
      readAs.innerHTML = `<span class="bad">${esc(p.read_as)}</span>`;
    }
  }

  let pickState = null;
  function hidePick() {
    pickEl.hidden = true;
    pickState = null;
  }
  function showPick(p, onChoose) {
    pickState = { options: p.options, cur: 0, onChoose };
    pickEl.replaceChildren(
      h("div", { class: "q" }, p.question || p.read_as),
      ...p.options.map((o, i) =>
        h(
          "div",
          {
            class: "o" + (i === 0 ? " cur" : ""),
            onclick: () => choosePick(i),
          },
          o.label || o.text,
          o.ok ? h("span", { class: "m" }, `  ${o.target.text}`) : null,
        ),
      ),
    );
    pickEl.hidden = false;
  }
  function choosePick(i) {
    const s = pickState;
    if (!s) return;
    hidePick();
    s.onChoose(s.options[i]);
  }
  function movePick(d) {
    const s = pickState,
      n = s.options.length;
    s.cur = (s.cur + d + n) % n;
    [...pickEl.querySelectorAll(".o")].forEach((el, i) =>
      el.classList.toggle("cur", i === s.cur),
    );
  }

  // resolve text to a region
  async function resolve(text, onOk, { echo = false } = {}) {
    const p = await parse(text);
    if (p.ok) return onOk(p);
    if (p.options.length) {
      showReadAs(p);
      showPick(p, (o) => {
        if (o.ok) return onOk(o);
        if (echo) cmd.value = o.text;
        resolve(o.text, onOk, { echo });
      });
    } else showReadAs(p);
  }

  let debounce = null;
  cmd.addEventListener("input", () => {
    hidePick();
    clearTimeout(debounce);
    const text = cmd.value;
    debounce = setTimeout(async () => {
      if (!text.trim()) return showReadAs(null);
      const p = await parse(text);
      if (cmd.value === text) showReadAs(p);
    }, 120);
  });
  cmd.addEventListener("keydown", (e) => {
    if (pickState && ["ArrowDown", "ArrowUp"].includes(e.key)) {
      e.preventDefault();
      movePick(e.key === "ArrowDown" ? 1 : -1);
      return;
    }
    if (e.key === "Escape") {
      hidePick();
      return;
    }
    if (e.key !== "Enter") return;
    e.preventDefault();
    if (pickState) return choosePick(pickState.cur);
    if (!cmd.value.trim()) return;
    clearTimeout(debounce);
    resolve(
      cmd.value,
      (p) => {
        plans.addElectrode(electrodeFrom(p));
        cmd.value = "";
        showReadAs(null);
        hidePick();
      },
      { echo: true },
    );
  });

  const outsideContacts = (e) =>
    contacts(e, plans.current.settings)
      .map((c, k) => (atlas.inside(c) ? null : k + 1))
      .filter(Boolean);
  const range = (ks) =>
    ks.length > 1 ? `${ks[0]}–${ks[ks.length - 1]}` : `${ks[0]}`;

  function field(name, value, onCommit, attrs = {}) {
    const inp = h("input", { name, ...attrs });
    inp.value = value;
    let orig = value;
    const commit = () => {
      if (inp.value !== orig) {
        orig = inp.value;
        onCommit(inp.value);
      }
    };
    inp.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        commit();
        inp.blur();
      } else if (e.key === "Escape") {
        inp.value = orig;
        inp.blur();
      }
    });
    inp.addEventListener("blur", commit);
    return inp;
  }

  function editTarget(e, text) {
    const phrase =
      e.entry.mode === "lateral"
        ? text
        : `entry ${fmt(e.entry.xyz)}, ${text} target`;
    resolve(phrase, (p) =>
      plans.update(
        e.id,
        e.entry.mode === "lateral"
          ? { target: electrodeFrom(p).target, entry: electrodeFrom(p).entry }
          : { target: electrodeFrom(p).target },
      ),
    );
  }
  function editEntry(e, text) {
    resolve(`target ${fmt(e.target.xyz)}, ${text} entry`, (p) =>
      plans.update(e.id, { entry: electrodeFrom(p).entry }),
    );
  }

  let adjustOpen = false,
    nudgeEnd = "target",
    focusName = null;
  const nudgePanel = (e) =>
    h(
      "div",
      { class: "nudge" },
      h(
        "div",
        {},
        "move ",
        h(
          "span",
          { class: "seg" },
          ...["target", "entry"].map((w) =>
            h(
              "button",
              {
                class: w === nudgeEnd ? "on" : "",
                "data-end": w,
                onclick: () => {
                  nudgeEnd = w;
                  renderList();
                },
              },
              w,
            ),
          ),
        ),
        " by 1 mm",
      ),
      h(
        "div",
        { class: "grid" },
        ...NUDGES.map(([k, label]) =>
          h(
            "button",
            {
              "data-nudge": k,
              onclick: () => adjust.nudge(e, k, nudgeEnd),
              title:
                k === "deeper" || k === "shallower"
                  ? `slide the contacts ${k} by 1 mm`
                  : `move the ${nudgeEnd} 1 mm`,
            },
            label,
          ),
        ),
      ),
      h(
        "div",
        { class: "sub hint" },
        "Or drag the ringed ends in the 3D view.",
      ),
    );
  const rename = (e, v) =>
    plans.update(
      e.id,
      v.trim() ? { name: v.trim(), custom_name: true } : { custom_name: false },
    );
  function renderList() {
    const plan = plans.current,
      st = plan.settings;
    if (!plan.electrodes.length) {
      list.replaceChildren(
        h(
          "div",
          { class: "empty" },
          'No electrodes yet. Type one in the bar below, e.g. "STG RHH".',
        ),
      );
      return;
    }
    list.replaceChildren(
      ...plan.electrodes.map((e) => {
        const sel = e.id === plans.selected,
          out = outsideContacts(e),
          s = spacingOf(e, st);
        const row = h(
          "div",
          { class: "el" + (sel ? " sel" : ""), "data-id": e.id },
          h(
            "div",
            { class: "head" },
            sel
              ? field("name", e.name, (v) => rename(e, v), {
                  class: "name",
                  size: Math.max(5, e.name.length + 1),
                  title: "Electrode name (clear it for the automatic name)",
                  "aria-label": "Electrode name",
                })
              : h(
                  "span",
                  {
                    class: "name",
                    title: "Double-click to rename",
                    ondblclick: () => {
                      focusName = e.id;
                      plans.select(e.id);
                    },
                  },
                  e.name,
                ),
            h(
              "span",
              { class: "n", title: `${e.n_contacts} contacts` },
              String(e.n_contacts),
            ),
            h("span", { class: "tgt", title: e.target.text }, e.target.text),
            out.length
              ? h(
                  "span",
                  { class: "warn", title: "contacts outside the brain" },
                  "⚠",
                )
              : null,
            h(
              "button",
              {
                class: "rm",
                title: "Remove electrode",
                onclick: (ev) => {
                  ev.stopPropagation();
                  plans.remove(e.id);
                },
              },
              "✕",
            ),
          ),
        );
        if (!sel) {
          row.addEventListener("click", () => plans.select(e.id));
          return row;
        }
        const length = ((e.n_contacts - 1) * s + st.contact_len_mm).toFixed(1);
        row.append(
          h(
            "div",
            { class: "edit" },
            h("span", {}, "entry"),
            field("entry", e.entry.text, (v) => editEntry(e, v)),
            h("span", {}, "target"),
            field("target", e.target.text, (v) => editTarget(e, v)),
            h("span", {}, "contacts"),
            h(
              "div",
              { class: "pair" },
              field(
                "n",
                String(e.n_contacts),
                (v) => {
                  const n = Math.max(
                    1,
                    Math.min(32, parseInt(v, 10) || e.n_contacts),
                  );
                  plans.update(e.id, { n_contacts: n });
                },
                { type: "number", min: 1, max: 32 },
              ),
            ),
            h("div", { class: "sub" }, `${s} mm spacing · ${length} mm long`),
          ),
        );
        if (out.length)
          row.append(
            h(
              "div",
              { class: "warnings" },
              `⚠ contact${out.length > 1 ? "s" : ""} ${range(out)} outside the brain`,
            ),
          );
        const more = h(
          "details",
          { class: "adjust" },
          h("summary", {}, "Adjust position"),
          nudgePanel(e),
        );
        more.open = adjustOpen;
        more.addEventListener("toggle", () => {
          adjustOpen = more.open;
        });
        row.append(more);
        return row;
      }),
    );
    if (focusName) {
      const box = list.querySelector(".el.sel input[name=name]");
      focusName = null;
      if (box) {
        box.focus();
        box.select();
      }
    }
  }

  let renaming = null; // id of the plan whose tab shows a name box
  function nameBox(p) {
    const inp = h("input", {
      class: "rename",
      name: "planName",
      "aria-label": "Plan name",
      size: Math.max(8, p.name.length),
      onclick: (e) => e.stopPropagation(),
      ondblclick: (e) => e.stopPropagation(),
    });
    inp.value = p.name;
    let done = false;
    const finish = (save) => {
      if (done) return;
      done = true;
      renaming = null;
      if (save && inp.value.trim() && inp.value.trim() !== p.name)
        plans.rename(p.id, inp.value);
      else renderTabs();
    };
    inp.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        finish(true);
      } else if (e.key === "Escape") finish(false);
    });
    inp.addEventListener("blur", () => finish(true));
    return inp;
  }
  function freeName() {
    const taken = new Set(plans.list.map((p) => p.name));
    for (let k = plans.list.length + 1; ; k++)
      if (!taken.has(`Plan ${k}`)) return `Plan ${k}`;
  }
  function renderTabs() {
    if (renaming && tabs.contains(document.activeElement)) return; // don't wipe a name being typed
    tabs.replaceChildren(
      ...plans.list.map((p) =>
        h(
          "div",
          {
            class: "tab" + (p === plans.current ? " cur" : ""),
            title: "Double-click to rename",
            onclick: () => p !== plans.current && plans.switchTo(p.id),
            ondblclick: () => {
              renaming = p.id;
              renderTabs();
            },
          },
          p.id === renaming ? nameBox(p) : h("span", {}, p.name),
          h(
            "button",
            {
              class: "x",
              title: "Close plan",
              onclick: (ev) => {
                ev.stopPropagation();
                if (
                  confirm(
                    `Close "${p.name}"? Its saved copy will be deleted (export a CSV first if you need it).`,
                  )
                )
                  plans.close(p.id);
              },
            },
            "×",
          ),
        ),
      ),
      h(
        "button",
        {
          class: "add",
          title: "New plan",
          onclick: () => {
            renaming = plans.add(freeName()).id; // name it right away in the new tab
            renderTabs();
          },
        },
        "+",
      ),
    );
    const box = tabs.querySelector("input.rename");
    if (box) {
      box.focus();
      box.select();
    }
  }

  const canvas = view.renderer.domElement,
    tip = $("tip");
  let downAt = null;
  canvas.addEventListener("pointerdown", (e) => {
    downAt = [e.clientX, e.clientY];
  });
  canvas.addEventListener("pointerup", (e) => {
    if (!downAt || Math.hypot(e.clientX - downAt[0], e.clientY - downAt[1]) > 4)
      return; // a drag, not a click
    const hit = layer.pick(e);
    if (hit && hit.eid) plans.select(hit.eid);
  });
  canvas.addEventListener("pointermove", (e) => {
    const hit = layer.pick(e);
    if (!hit || !hit.k) {
      tip.style.display = "none";
      return;
    }
    const el = plans.current.electrodes.find((x) => x.id === hit.eid);
    tip.innerHTML =
      `<b>${esc(el.name)}</b> · contact ${hit.k}<br>${esc(atlas.labelAt(hit.xyz))}` +
      `<br><span class="m">MNI ${hit.xyz.map((v) => v.toFixed(1)).join(", ")}</span>`;
    tip.style.display = "block";
    tip.style.left = e.offsetX + 14 + "px";
    tip.style.top = e.offsetY + 14 + "px";
  });

  function render() {
    renderTabs();
    renderList();
  }
  return { render };
}
