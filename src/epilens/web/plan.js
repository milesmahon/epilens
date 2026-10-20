export const DEFAULT_SETTINGS = {
  spacing_mm: 5.0,
  contact_len_mm: 2.41,
  diameter_mm: 0.86,
};
const hex = (n) =>
  Array.from(crypto.getRandomValues(new Uint8Array(n / 2)), (b) =>
    b.toString(16).padStart(2, "0"),
  ).join("");
const now = () => new Date().toISOString().slice(0, 19);

export const spacingOf = (e, settings) => e.spacing_mm || settings.spacing_mm;
export const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);

// unit vector from the target toward the entry
export function axis(e) {
  const d = e.entry.xyz.map((v, i) => v - e.target.xyz[i]),
    n = Math.hypot(...d);
  return n > 0 ? d.map((v) => v / n) : [1, 0, 0];
}

// Contact 1 (the deepest) sits on the target. Must match plans.contacts()
export function contacts(e, settings) {
  const u = axis(e),
    s = spacingOf(e, settings),
    t = e.target.xyz;
  return Array.from({ length: e.n_contacts }, (_, k) =>
    t.map((v, i) => v + k * s * u[i]),
  );
}

export function newPlan(name) {
  return {
    id: hex(32),
    name,
    created: now(),
    updated: now(),
    settings: { ...DEFAULT_SETTINGS },
    electrodes: [],
  };
}

export function autoName(e, plan) {
  const base = e.entry.abbrev || e.target.abbrev || "Electrode"; // where it enters, e.g. RSTG
  const taken = new Set(
    plan.electrodes.filter((o) => o.id !== e.id).map((o) => o.name),
  );
  if (!taken.has(base)) return base;
  for (let k = 2; ; k++) if (!taken.has(`${base} ${k}`)) return `${base} ${k}`;
}

const end = (x) => ({
  xyz: x.xyz,
  text: x.text,
  abbrev: x.abbrev,
  region: x.region,
  mode: x.mode,
});

export function electrodeFrom(parse) {
  return {
    id: hex(12),
    name: "",
    custom_name: false,
    n_contacts: parse.n_contacts || parse.default_contacts,
    spacing_mm: null,
    target: end(parse.target),
    entry: end(parse.entry),
  };
}

export class Plans {
  constructor() {
    this.list = [];
    this.current = null;
    this.selected = null;
    this._cbs = [];
    this._timers = {};
    this.unsaved = new Set();
    this.saveError = null;
    this.onSaveStatus = () => {};
    this.paused = false; // another browser tab took over
  }

  onChange(cb) {
    this._cbs.push(cb);
  }
  _emit() {
    for (const cb of this._cbs) cb(this);
  }

  async load() {
    this.list = await fetch("api/plans").then((r) => r.json());
    if (!this.list.length) return this.add("Plan 1");
    let last = null;
    try {
      last = localStorage.getItem("epilens.currentPlan");
    } catch (e) {
      /* storage blocked */
    }
    this.current =
      this.list.find((p) => p.id === last) || this.list[this.list.length - 1];
    this._emit();
  }

  _save(plan, wait = 500) {
    if (this.paused) return;
    clearTimeout(this._timers[plan.id]);
    this._timers[plan.id] = setTimeout(() => this.saveNow(plan), wait);
  }

  // Resolves true once the server has this version
  async saveNow(plan) {
    clearTimeout(this._timers[plan.id]);
    delete this._timers[plan.id];
    if (this.paused || !this.list.includes(plan)) return false;
    plan.updated = now();
    try {
      const r = await fetch(`api/plans/${plan.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(plan),
      });
      if (!r.ok)
        throw new Error(
          (await r.json().catch(() => ({}))).error || `HTTP ${r.status}`,
        );
      this.unsaved.delete(plan.id);
      this.saveError = this.unsaved.size ? this.saveError : null;
      return true;
    } catch (err) {
      this.unsaved.add(plan.id);
      this.saveError = String(err.message || err);
      return false;
    } finally {
      this.onSaveStatus();
    }
  }

  flush() {
    for (const id of this.unsaved) {
      const p = this.list.find((x) => x.id === id);
      if (p) this._save(p, 0);
    }
  }

  _touch(plan = this.current) {
    this._save(plan);
    this._emit();
  }

  switchTo(id) {
    this.current = this.list.find((p) => p.id === id) || this.current;
    this.selected = null;
    try {
      localStorage.setItem("epilens.currentPlan", this.current.id);
    } catch (e) {
      /* storage blocked */
    }
    this._emit();
  }

  add(name, plan = null) {
    plan = plan || newPlan(name);
    this.list.push(plan);
    this._save(plan, 0);
    this.switchTo(plan.id);
    return plan;
  }

  async close(id) {
    const i = this.list.findIndex((p) => p.id === id);
    if (i < 0) return;
    clearTimeout(this._timers[id]);
    const [plan] = this.list.splice(i, 1);
    if (this.paused) return;
    await fetch(`api/plans/${plan.id}`, { method: "DELETE" });
    if (!this.list.length) return this.add("Plan 1");
    if (this.current === plan) this.switchTo(this.list[Math.max(0, i - 1)].id);
    else this._emit();
  }

  rename(id, name) {
    const plan = this.list.find((p) => p.id === id);
    if (plan && name.trim()) {
      plan.name = name.trim();
      this._touch(plan);
    }
  }

  get selectedElectrode() {
    return this.current.electrodes.find((e) => e.id === this.selected) || null;
  }

  select(eid) {
    this.selected = eid;
    this._emit();
  }

  addElectrode(e) {
    this.current.electrodes.push(e);
    e.name = autoName(e, this.current);
    this.selected = e.id;
    this._touch();
    return e;
  }

  update(eid, patch) {
    const e = this.current.electrodes.find((x) => x.id === eid);
    if (!e) return;
    Object.assign(e, patch);
    if (!e.custom_name) e.name = autoName(e, this.current);
    this._touch();
  }

  remove(eid) {
    this.current.electrodes = this.current.electrodes.filter(
      (e) => e.id !== eid,
    );
    if (this.selected === eid) this.selected = null;
    this._touch();
  }

  updateSettings(patch) {
    Object.assign(this.current.settings, patch);
    this._touch();
  }
}
