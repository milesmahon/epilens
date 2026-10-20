"""Save plans as JSON and CSV/excel import and export

Plan JSON format, shared with web/plan.js:
  {id, name, created, updated, settings: {spacing_mm, contact_len_mm, diameter_mm},
   electrodes: [{id, name, custom_name, n_contacts, spacing_mm (override or None),
                 target: {xyz, text, abbrev}, entry: {xyz, text, abbrev}}]}
"""

import csv
import io
import json
import logging
import os
import re
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

import numpy as np
import openpyxl

from .atlas import Atlas

log = logging.getLogger("epilens")
DEFAULT_ROOT = Path.home() / "Documents" / "epilens" / "plans"
DEFAULT_SETTINGS = {"spacing_mm": 5.0, "contact_len_mm": 2.41, "diameter_mm": 0.86}
COLUMNS = ["Electrode", "Contact", "MNI X", "MNI Y", "MNI Z", "Region", "Hemisphere", "Entry", "Target",
           "Entry X", "Entry Y", "Entry Z", "Target X", "Target Y", "Target Z", "N contacts", "Spacing (mm)"]
ID_RE = re.compile(r"[0-9a-f]{32}")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def new_plan(name: str) -> dict:
    return {"id": uuid.uuid4().hex, "name": name, "created": _now(), "updated": _now(),
            "settings": dict(DEFAULT_SETTINGS), "electrodes": []}


def spacing_of(e: dict, settings: dict) -> float:
    return float(e.get("spacing_mm") or settings["spacing_mm"])


def contacts(e: dict, settings: dict) -> list[list[float]]:
    # Contact positions: contact 1 on the target, then stepping toward the entry
    t, en = np.asarray(e["target"]["xyz"], float), np.asarray(e["entry"]["xyz"], float)
    d = en - t
    n = np.linalg.norm(d)
    u = d / n if n > 0 else np.array([1.0, 0, 0])
    s = spacing_of(e, settings)
    return [list(map(float, t + k * s * u)) for k in range(int(e["n_contacts"]))]


# ---------- CSV ----------
def to_csv(plan: dict, atlas: Atlas) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(COLUMNS)
    r1 = "{:.1f}".format
    for e in plan["electrodes"]:
        tx, ex = e["target"]["xyz"], e["entry"]["xyz"]
        for k, c in enumerate(contacts(e, plan["settings"]), start=1):
            w.writerow([e["name"], k, *map(r1, c), atlas.label_at(c), "L" if c[0] < 0 else "R",
                        e["entry"]["text"], e["target"]["text"], *map(r1, ex), *map(r1, tx),
                        e["n_contacts"], f"{spacing_of(e, plan['settings']):g}"])
    return buf.getvalue()


NAME_COLS = ("electrode", "name", "label", "electrode_name", "lead")
CONTACT_COLS = ("contact", "contact_number", "contact_no", "index")


def _key(col: str) -> str:
    # Column name to key: "Entry X (mm)" -> "entry_x"
    k = re.sub(r"[^a-z0-9]+", "_", col.strip().lower()).strip("_")
    return re.sub(r"_mm$", "", k)


def _num(r: dict, k: str, what: str) -> float:
    try:
        return float(r[k])
    except (KeyError, TypeError, ValueError):
        raise ValueError(f"Bad or missing {k!r} for electrode {what!r}") from None


def _mni(xyz) -> str:
    return f"MNI ({', '.join(f'{v:g}' for v in np.round(xyz, 1))})"


def _target(xyz, atlas: Atlas | None, text: str = "") -> dict:
    # Target from coordinates, labelled "MNI (x, y, z) · region"
    xyz = [float(v) for v in xyz]
    region = atlas.label_at(xyz) if atlas else None
    text = text or _mni(xyz) + (f" · {region}" if region else "")
    return {"xyz": xyz, "text": text, "abbrev": "", "region": region, "mode": "coords"}


def _entry(xyz, target: dict, atlas: Atlas | None, text: str = "") -> dict:
    # Entry moved out to the brain surface along the track and named by the cortex there.
    # Entries from an epilens export (they have text) keep their position and text
    if atlas is None:
        return {"xyz": [float(v) for v in xyz], "text": text or _mni(xyz), "abbrev": "", "region": None, "mode": "coords"}
    xyz = [float(v) for v in (xyz if text else atlas.snap_entry(target["xyz"], xyz))]
    r = atlas.surface_region(xyz)
    text = text or _mni(xyz) + (f" · {r.hemi} {r.base}" if r else "")
    return {"xyz": xyz, "text": text, "abbrev": r.hemi + r.abbrev if r else "",
            "region": r.name if r else None, "mode": "coords"}


def from_csv(text: str, atlas: Atlas | None = None, name: str | None = None) -> dict:
    # Reads one of:
    #   - an epilens export, or any sheet with entry_x/y/z and target_x/y/z columns
    #   - a contact list: electrode name + x, y, z (+ contact number), or labels like "LfHH1"
    return _from_rows(list(csv.reader(io.StringIO(text.lstrip("\ufeff")))), atlas, name)[0]


def import_file(data: bytes, atlas: Atlas | None = None, name: str | None = None) -> tuple[dict, list[str]]:
    if not data.startswith(b"PK"):
        text = data.decode("utf-8", errors="replace").lstrip("\ufeff")
        return _from_rows(list(csv.reader(io.StringIO(text))), atlas, name)
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except (zipfile.BadZipFile, KeyError, OSError):
        raise ValueError("This file isn't a readable .xlsx workbook.") from None
    errors = []
    for ws in wb.worksheets:
        try:
            return _from_rows([["" if v is None else str(v) for v in r] for r in ws.iter_rows(values_only=True)], atlas, name)
        except ValueError as e:
            errors.append(e)
    raise next((e for e in errors if "empty" not in str(e)), errors[0] if errors else ValueError("The workbook is empty."))


def _coord_cols(header: list[str]) -> list[str] | None:
    mni = [c for c in header if re.search(r"(^|_)mni(_|$)", c)]
    for pick in ([next((c for c in mni if re.search(rf"_{a}$", c)), None) for a in "xyz"],
                 [next((c for c in mni if c.endswith(f"_{i}")), None) for i in (1, 2, 3)],
                 [a if a in header else None for a in "xyz"]):
        if all(pick) and len(set(pick)) == 3:
            return pick
    return None


def _split_labels(recs: list[dict], col: str) -> bool:
    # Split labels like "LfHH1" into electrode "LfHH" and contact 1
    parts = [re.fullmatch(r"(.*?[A-Za-z].*?)[\s_\-]*(\d+)", r.get(col, "")) for r in recs if r.get(col)]
    if not parts or not all(parts) or len({m[1] for m in parts}) == len(parts):
        return False
    for r in recs:
        m = re.fullmatch(r"(.*?[A-Za-z].*?)[\s_\-]*(\d+)", r.get(col, ""))
        r["_name"], r["_contact"] = (m[1].strip(), m[2]) if m else ("", "")
    return True


def _from_rows(rows: list[list[str]], atlas: Atlas | None, name: str | None) -> tuple[dict, list[str]]:
    rows = [[c or "" for c in r] for r in rows]
    rows = [r for r in rows if any(c.strip() for c in r)]
    rows = [r for r in rows if not r[0].lstrip().startswith("#")]   # older exports start with a note line
    if not rows:
        raise ValueError("The file is empty.")
    header = [_key(c) for c in rows[0]]
    recs = [dict(zip(header, (c.strip() for c in r))) for r in rows[1:]]
    cols = set(header)
    name_col = next((c for c in NAME_COLS if c in cols), None)
    contact_col = next((c for c in CONTACT_COLS if c in cols), None)
    ends = {f"{w}_{a}" for w in ("entry", "target") for a in "xyz"}
    xyz_cols = _coord_cols(header)
    notes: list[str] = []
    if name_col and ends <= cols:
        electrodes = _from_ends(recs, name_col, atlas)
    elif xyz_cols and (name_col or contact_col):
        combined = contact_col if not name_col else name_col if not contact_col else None
        if combined and _split_labels(recs, combined):
            name_col, contact_col = "_name", "_contact"
        if not name_col:
            raise ValueError(f"Couldn't tell the electrodes apart: expected an electrode name column, or contact "
                             f"labels like 'LAH1' in the {combined!r} column.")
        electrodes = _from_contacts(recs, name_col, contact_col, xyz_cols, atlas, notes)
    else:
        raise ValueError("This doesn't look like an epilens CSV. Expected columns: an electrode name plus entry_x, entry_y, entry_z, target_x, target_y, "
                         "target_z — or an electrode name plus x, y, z for each contact.")
    if not electrodes:
        raise ValueError("No depth electrodes found in the file." + (" " + " ".join(notes) if notes else ""))
    plan = new_plan(name or "Imported plan")
    plan["electrodes"] = electrodes
    return plan, notes


def _electrode(nm, target, entry, n, spacing, atlas):
    if n is None:
        if atlas is None:
            raise ValueError(f"Electrode {nm!r} has no n_contacts")
        n = atlas.fit_contacts(target["xyz"], entry["xyz"], spacing or DEFAULT_SETTINGS["spacing_mm"])
    auto = entry["abbrev"] or target["abbrev"] or "Electrode"
    return {"id": uuid.uuid4().hex[:12], "name": nm, "custom_name": nm != auto, "n_contacts": max(1, min(32, int(n))),
            "spacing_mm": None if not spacing or spacing == DEFAULT_SETTINGS["spacing_mm"] else spacing,
            "target": target, "entry": entry}


def _from_ends(recs, name_col, atlas):
    seen: dict[str, dict] = {}
    for r in recs:
        nm = r.get(name_col, "")
        if not nm or nm in seen:
            continue
        xyz = lambda p: [_num(r, f"{p}_{a}", nm) for a in "xyz"]
        spacing = _num(r, "spacing", nm) if r.get("spacing") else None
        n = int(_num(r, "n_contacts", nm)) if r.get("n_contacts") else None
        target = _target(xyz("target"), atlas, r.get("target", ""))
        if target["xyz"] == xyz("entry"):
            raise ValueError(f"Electrode {nm!r}: entry and target are the same point")
        entry = _entry(xyz("entry"), target, atlas, r.get("entry", ""))
        seen[nm] = _electrode(nm, target, entry, n, spacing, atlas)
    return list(seen.values())


def _from_contacts(recs, name_col, contact_col, xyz_cols, atlas, notes):
    # Contact rows -> electrodes 
    # A lead with a long gap between contact groups becomes one electrode per group
    groups: dict[str, list] = {}
    for i, r in enumerate(recs):
        nm = r.get(name_col, "")
        if not nm:
            continue
        k = _num(r, contact_col, nm) if contact_col and r.get(contact_col) else i
        groups.setdefault(nm, []).append((k, [_num(r, c, nm) for c in xyz_cols]))
    leads, skipped = {}, []
    for nm, pts in groups.items():
        pts.sort(key=lambda kp: kp[0])
        xyz = np.array([p for _, p in pts])
        if len(xyz) >= 3:
            s = np.linalg.svd(xyz - xyz.mean(0), compute_uv=False)
            if np.sqrt((s[1:] ** 2).sum() / len(xyz)) > 2.0:   # mm off the best straight line
                skipped.append(f"{nm} ({len(xyz)} contacts)")
                continue
        if re.search(r"grid|strip", nm, re.I):
            skipped.append(f"{nm} ({len(xyz)} contacts)")
            continue
        leads[nm] = ([k for k, _ in pts], xyz)
    if skipped:
        notes.append(f"Skipped {', '.join(skipped)}: only straight depth electrodes can be shown.")
    if atlas is not None:
        def beyond(p, q):   # mm of brain on the line from p, away from q
            u = (p - q) / np.linalg.norm(p - q)
            return 0.5 * sum(atlas.inside(p + s * u) for s in np.arange(0.5, 200, 0.5))
        diffs = [beyond(x[0], x[-1]) - beyond(x[-1], x[0]) for _, x in leads.values() if len(x) > 1]
        vote = sum(np.sign(d) for d in diffs if abs(d) >= 10)
        if vote < 0:
            leads = {nm: (ks[::-1], x[::-1]) for nm, (ks, x) in leads.items()}
            notes.append("In this file contact 1 is the outermost, so each electrode's highest-numbered contact is its tip.")
    out, gapped = [], []
    for nm, (ks, xyz) in leads.items():
        if len(xyz) == 1:   # a single contact: point it straight out laterally
            target = _target(xyz[0], atlas)
            entry = xyz[0] + [10.0 if xyz[0][0] >= 0 else -10.0, 0, 0]
            out.append(_electrode(nm, target, _entry(entry, target, atlas), 1, None, atlas))
            continue
        gaps = np.linalg.norm(np.diff(xyz, axis=0), axis=1)
        if gaps.min() <= 0:
            raise ValueError(f"Electrode {nm!r}: contacts share the same position")
        med = float(np.median(gaps))
        cuts = [i + 1 for i, g in enumerate(gaps) if g > max(2.5 * med, med + 5)]
        segs = [(a, b) for a, b in zip([0, *cuts], [*cuts, len(xyz)])]
        if len(segs) > 1:
            gapped.append(nm)
        outward = (xyz[-1] - xyz[0]) / np.linalg.norm(xyz[-1] - xyz[0])
        for a, b in sorted(segs, key=lambda ab: min(ks[ab[0]:ab[1]])):
            seg = xyz[a:b]
            label = nm if len(segs) == 1 else f"{nm} {min(ks[a:b]):g}-{max(ks[a:b]):g}"
            spacing = round(float(np.median(gaps[a:b - 1])), 2) if b - a > 1 else None
            target = _target(seg[0], atlas)
            entry = xyz[-1] if b < len(xyz) or b - a > 1 else seg[0] + 10 * outward
            out.append(_electrode(label, target, _entry(entry, target, atlas), b - a, spacing, atlas))
    if gapped:
        names = gapped[0] if len(gapped) == 1 else f"{', '.join(gapped[:-1])} and {gapped[-1]}"
        notes.append(f"{names} {'has' if len(gapped) == 1 else 'each have'} groups of contacts with a gap between "
                     f"them, so each group is shown as its own electrode on the same track.")
    return out


# ---------- storage ----------
class PlanStore:
    def __init__(self, root: Path | str = DEFAULT_ROOT):
        self.root = Path(root)

    def _path(self, plan_id: str) -> Path:
        if not isinstance(plan_id, str) or not ID_RE.fullmatch(plan_id):
            raise ValueError(f"bad plan id: {plan_id!r}")
        return self.root / f"{plan_id}.json"

    def list(self) -> list[dict]:
        if not self.root.exists():
            return []
        plans = []
        for f in sorted(self.root.glob("*.json")):
            try:
                p = json.loads(f.read_text(encoding="utf-8"))
                if ID_RE.fullmatch(str(p.get("id", ""))) and isinstance(p.get("electrodes"), list):
                    plans.append(p)
                    continue
            except (OSError, ValueError):
                pass
            log.warning("skipping unreadable plan file %s", f)
        return sorted(plans, key=lambda p: p.get("created") or p.get("updated") or "")

    def save(self, plan: dict) -> dict:
        path = self._path(plan.get("id"))
        plan = {**plan, "updated": _now()}
        plan.setdefault("created", plan["updated"])
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)
        return plan

    def load(self, plan_id: str) -> dict:
        return json.loads(self._path(plan_id).read_text(encoding="utf-8"))

    def delete(self, plan_id: str) -> None:
        self._path(plan_id).unlink(missing_ok=True)
