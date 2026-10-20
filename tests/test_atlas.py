import csv
from pathlib import Path

import numpy as np

DATA = Path(__file__).parents[1] / "src" / "epilens" / "data"


def test_shipped_data():
    z = np.load(DATA / "atlas.npz")
    assert z["labels"].shape == (91, 109, 91) and z["labels"].dtype == np.uint8
    regions = list(csv.DictReader(open(DATA / "regions.csv")))
    names = {r["name"] for r in regions}
    for n in ["Left Hippocampus", "Right Amygdala", "Right Temporal Pole", "Left Pulvinar", "Right Anterior Thalamic Nucleus",
              "Right Anterior Cingulate Gyrus", "Left Temporal Fusiform Cortex", "Left Superior Temporal Gyrus"]:
        assert n in names
    ids = {int(r["id"]) for r in regions}
    assert ids <= set(np.unique(z["labels"])) - {0, 1}  # every region has voxels
    assert z["depth"][z["labels"] == 0].max() == 0
    assert all(r["abbrev"] and r["hemi"] in ("L", "R") and r["kind"] in ("cortex", "deep") for r in regions)


MODIFIERS = [None, "anterior", "posterior", "superior", "inferior", "medial", "lateral"]


def test_point_inside_every_region_and_modifier(atlas):
    for r in atlas.regions:
        for mod in MODIFIERS:
            assert atlas.label_at(atlas.target_point(r, mod)) == r.name, (r.name, mod)


def test_hippocampus_thirds_ordered(atlas):
    h = atlas.region("Hippocampus", "R")
    ys = [atlas.target_point(h, m)[1] for m in ("head", "body", "tail")]
    assert ys[0] > ys[1] > ys[2] and atlas.target_point(h, None)[0] > 0


def test_surface(atlas):
    assert atlas.reaches_surface(atlas.region("Middle Temporal Gyrus", "L"))
    assert not atlas.reaches_surface(atlas.region("Anterior Cingulate Gyrus", "R"))
    assert not atlas.reaches_surface(atlas.region("Hippocampus", "R"))
    e = atlas.exit_point(atlas.target_point(atlas.region("Amygdala", "R"), None), [1, 0, 0])
    assert atlas.label_at(e + [2, 0, 0]) == "outside brain" and 55 < e[0] < 75


def test_entry_point_on_surface(atlas):
    r = atlas.region("Temporal Pole", "L")
    p = atlas.entry_point(r, None)
    assert atlas.label_at(p) == r.name and atlas.depth_at(p) <= 4


def test_fit_contacts(atlas):
    t = atlas.target_point(atlas.region("Amygdala", "R"), None)
    assert 7 <= atlas.fit_contacts(t, t + [10, 0, 0], 5.0) <= 10


def test_label_outside_and_white_matter(atlas):
    assert atlas.label_at([0, 0, 200]) == "outside brain"
    assert atlas.label_at([-26, -10, 30]) == "white matter"
