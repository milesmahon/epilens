import logging

import numpy as np
import pytest

from epilens.matcher import parse
from epilens.plans import PlanStore, contacts, from_csv, new_plan, to_csv


def electrode(atlas, text, n=None):
    p = parse(text, atlas)
    return {"id": text.replace(" ", "").lower(), "name": p.name, "custom_name": False,
            "n_contacts": n or p.default_contacts, "spacing_mm": None,
            "target": {"xyz": p.target.xyz, "text": p.target.text, "abbrev": p.target.abbrev},
            "entry": {"xyz": p.entry.xyz, "text": p.entry.text, "abbrev": p.entry.abbrev}}


@pytest.fixture
def plan(atlas):
    p = new_plan("Pt 1")
    p["electrodes"] = [electrode(atlas, "STG RHH", 8), electrode(atlas, "LPUL")]
    return p


def test_contacts_start_at_target(plan):
    e = plan["electrodes"][0]
    c = np.array(contacts(e, plan["settings"]))
    assert len(c) == 8 and np.allclose(c[0], e["target"]["xyz"])
    assert np.allclose(np.linalg.norm(np.diff(c, axis=0), axis=1), 5.0)
    e["spacing_mm"] = 3.5   # per-electrode override
    assert np.isclose(np.linalg.norm(np.subtract(*contacts(e, plan["settings"])[:2])), 3.5)


def test_csv_layout(plan, atlas):
    text = to_csv(plan, atlas)
    lines = text.splitlines()
    assert lines[0].split(",")[:6] == ["Electrode", "Contact", "MNI X", "MNI Y", "MNI Z", "Region"]
    first = lines[1].split(",")
    assert first[0] == "RSTG" and first[1] == "1" and first[5] == "Right Hippocampus" and first[6] == "R"
    assert len(lines) == 1 + 8 + plan["electrodes"][1]["n_contacts"]


def test_csv_roundtrip(plan, atlas):
    back = from_csv(to_csv(plan, atlas), atlas, name="Pt 1")
    assert back["id"] != plan["id"] and back["name"] == "Pt 1"
    for a, b in zip(plan["electrodes"], back["electrodes"]):
        assert a["name"] == b["name"] and a["n_contacts"] == b["n_contacts"]
        assert a["custom_name"] == b["custom_name"]   # automatic names stay automatic
        assert np.allclose(a["target"]["xyz"], b["target"]["xyz"], atol=0.05)
        assert np.allclose(a["entry"]["xyz"], b["entry"]["xyz"], atol=0.05)
        assert a["target"]["text"] == b["target"]["text"]


def test_csv_from_excel(plan, atlas):
    text = "﻿" + to_csv(plan, atlas).replace("\n", "\r\n") + "\r\n,,,,\r\n\r\n"
    back = from_csv(text)
    assert [e["name"] for e in back["electrodes"]] == [e["name"] for e in plan["electrodes"]]


def test_csv_simple_entry_target_list(atlas):
    text = ("Electrode Name,entry x,entry y,entry z,target x,target y,target z,n_contacts\n"
            "RHip,60,-20,-10,26,-22,-12,8\nLA,-60,0,-20,-24,-4,-20,\n")
    plan = from_csv(text, atlas, name="case 3")
    assert plan["name"] == "case 3"
    rhip, la = plan["electrodes"]
    assert rhip["n_contacts"] == 8 and rhip["custom_name"] and rhip["target"]["region"] == "Right Hippocampus"
    assert rhip["target"]["text"].startswith("MNI (26, -22, -12)")
    entry = rhip["entry"]   # moved out to the surface and named after the cortex there
    assert entry["xyz"] == list(atlas.snap_entry([26, -22, -12], [60, -20, -10])) and entry["xyz"][0] > 60
    assert entry["abbrev"] == rhip["entry"]["abbrev"] and entry["abbrev"].startswith("R") and "white" not in entry["text"]
    assert la["n_contacts"] == atlas.fit_contacts([-24, -4, -20], [-60, 0, -20], 5.0) and la["name"] == "LA"


def test_csv_per_contact_list(atlas):
    rows = [f"LH,{k},{-24 - 3.5 * (k - 1)},-20,-12" for k in (3, 1, 2, 4)]   # out of order
    plan = from_csv("label,contact,x,y,z\n" + "\n".join(rows) + "\nsolo,1,30,0,0\n", atlas)
    lh, solo = plan["electrodes"]
    assert lh["n_contacts"] == 4 and lh["spacing_mm"] == 3.5
    assert lh["target"]["xyz"] == [-24, -20, -12] and lh["entry"]["xyz"][0] < -60   # on the line through the last contacts, at the surface
    assert contacts(lh, plan["settings"])[3] == pytest.approx([-34.5, -20, -12])
    assert solo["n_contacts"] == 1 and solo["entry"]["xyz"][0] > 30


def test_csv_garbage_rejected():
    with pytest.raises(ValueError):
        from_csv("hello,world\n1,2\n")


def test_store_odd_names(tmp_path, plan):
    store = PlanStore(tmp_path)
    plan["name"] = "Pt 3/4 — ü 🧠"
    saved = store.save(plan)
    assert saved["updated"]
    assert [p["name"] for p in store.list()] == ["Pt 3/4 — ü 🧠"]
    assert [f.name for f in tmp_path.iterdir()] == [f"{plan['id']}.json"]
    with pytest.raises(ValueError):
        store.delete("../x")
    with pytest.raises(ValueError):
        store.save({**plan, "id": "../../evil"})
    store.delete(plan["id"])
    assert store.list() == []


def test_store_corrupt_file_skipped(tmp_path, plan, caplog):
    store = PlanStore(tmp_path)
    store.save(plan)
    (tmp_path / ("0" * 32 + ".json")).write_text("{not json")
    with caplog.at_level(logging.WARNING, logger="epilens"):
        assert [p["id"] for p in store.list()] == [plan["id"]]
    assert "skipping" in caplog.text


def test_import_bad_xlsx(atlas):
    from epilens.plans import import_file
    with pytest.raises(ValueError, match="readable .xlsx"):
        import_file(b"PK\x03\x04 not really a zip", atlas)


def test_import_xlsx_contact_list(atlas):
    from epilens.plans import import_file
    from xlsx_util import contact_sheet, make_xlsx
    plan, notes = import_file(make_xlsx(contact_sheet()), atlas, "case 9")
    assert plan["name"] == "case 9"
    by = {e["name"]: e for e in plan["electrodes"]}
    assert list(by) == ["RHH 1-4", "RHH 5-8", "LA"]   # the grid is skipped and the lead with a gap is split in two
    assert any("Grid (6 contacts)" in n for n in notes) and any("RHH has groups" in n for n in notes)
    assert any("contact 1 is the outermost" in n for n in notes)
    deep, shallow, la = by["RHH 5-8"], by["RHH 1-4"], by["LA"]
    assert deep["target"]["xyz"] == [26, -20, -12] and deep["target"]["region"] == "Right Hippocampus"   # uses the MNI columns, not the native ones
    assert shallow["target"]["xyz"] == [26 + 3.5 * 3 + 30, -20, -12]
    assert deep["n_contacts"] == shallow["n_contacts"] == 4 and deep["spacing_mm"] == shallow["spacing_mm"] == 3.5
    assert contacts(deep, plan["settings"])[3] == pytest.approx([36.5, -20, -12])
    assert deep["entry"]["xyz"][0] > 60 and shallow["entry"]["xyz"] == pytest.approx(deep["entry"]["xyz"], abs=0.6)
    assert la["n_contacts"] == 6 and la["target"]["xyz"] == [-24, -4, -20] and la["spacing_mm"] == 4


def test_import_csv_labels_and_mni_columns(atlas):
    # In a CSV the "MNI X" columns win over plain x, y, z. Labels like "LA 1" are split, and contact 1 stays the tip
    from epilens.plans import import_file
    rows = "".join(f"LA {k},{-24 - 4 * (k - 1)},-4,-20,0,0,0\n" for k in range(1, 6))
    plan, notes = import_file(("Contact,MNI X,MNI Y,MNI Z,x,y,z\n" + rows).encode(), atlas)
    (la,) = plan["electrodes"]
    assert la["name"] == "LA" and la["n_contacts"] == 5 and la["target"]["xyz"] == [-24, -4, -20] and notes == []


def test_import_xlsx_without_electrodes(atlas):
    from epilens.plans import import_file
    from xlsx_util import make_xlsx
    with pytest.raises(ValueError, match="Skipped Grid"):
        import_file(make_xlsx({"S": [["contact", "mni x", "mni y", "mni z"]] +
                                    [[f"Grid{k}", -60, 10 * (k % 3), 10 * (k // 3)] for k in range(1, 7)]}), atlas)
    with pytest.raises(ValueError, match="epilens CSV"):
        import_file(make_xlsx({"S": [["hello", "world"], [1, 2]]}), atlas)
