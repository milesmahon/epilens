import numpy as np
import pytest

from epilens.matcher import parse


def region_of(atlas, end):
    return atlas.label_at(end.xyz)


def test_full_sentence(atlas):
    p = parse("right temporal pole entry, anterior amygdala target, 8 contacts", atlas)
    assert p.ok and p.n_contacts == 8 and p.name == "RTP"
    assert region_of(atlas, p.target) == "Right Amygdala" and p.entry.region == "Right Temporal Pole"
    centroid = atlas.target_point(atlas.region("Amygdala", "R"), None)
    assert p.target.xyz[1] > centroid[1]
    assert "amygdala" in p.read_as.lower() and "8 contacts" in p.read_as


def test_missing_side_asks(atlas):
    p = parse("temporal pole entry, anterior amygdala target", atlas)
    assert not p.ok and "which side" in p.question
    assert [o.label for o in p.options] == ["Right", "Left"] and all(o.ok for o in p.options)
    assert p.options[1].target.xyz[0] < 0


@pytest.mark.parametrize("text, entry, target, name", [
    ("STG RHH", "Right Superior Temporal Gyrus", "Right Hippocampus", "RSTG"),
    ("OFC LHB", "Left Frontal Orbital Cortex", "Left Hippocampus", "LOFC"),
    ("from left MTG to the left hippocampal head", "Left Middle Temporal Gyrus", "Left Hippocampus", "LMTG"),
])
def test_shorthand(atlas, text, entry, target, name):
    p = parse(text, atlas)
    assert p.ok, p.read_as
    assert p.entry.region.startswith(entry) and region_of(atlas, p.target) == target and p.name == name


def test_hippocampus_head_is_anterior(atlas):
    hh = parse("STG RHH", atlas).target.xyz[1]
    hb = parse("STG RHB", atlas).target.xyz[1]
    assert hh > hb


@pytest.mark.parametrize("text, target", [
    ("RPCC", "Right Posterior Cingulate Gyrus"),
    ("RACC", "Right Anterior Cingulate Gyrus"),
    ("RPUL", "Right Pulvinar"),
    ("RANT", "Right Anterior Thalamic Nucleus"),
    ("lant", "Left Anterior Thalamic Nucleus"),
    ("left anterior nucleus of the thalamus", "Left Anterior Thalamic Nucleus"),
    ("right anterior thalamus", "Right Anterior Thalamic Nucleus"),
    ("right anterior cingulate", "Right Anterior Cingulate Gyrus"),
])
def test_single_region_is_target_with_lateral_entry(atlas, text, target):
    p = parse(text, atlas)
    assert p.ok, p.read_as
    assert region_of(atlas, p.target) == target and p.entry.mode == "lateral"
    assert "default" in p.entry.text


def test_lateral_entry_points_outward(atlas):
    p = parse("RHH", atlas)
    assert p.entry.xyz[0] > p.target.xyz[0] + 20
    assert atlas.depth_at(p.entry.xyz) <= 4
    assert p.name == p.entry.abbrev == "R" + atlas.surface_region(p.entry.xyz).abbrev   # named by where it enters


@pytest.mark.parametrize("text", ["RHH", "LA", "RPCC", "RPUL", "STG RHH", "ACC entry, RHH", "LOFC",
                                  "target -25 -20 -15 entry -70 -20 -15"])
def test_entry_on_surface_in_cortex(atlas, text):
    # Entry is where the track leaves the brain, named by the closest cortex
    p = parse(text, atlas)
    t, e = np.array(p.target.xyz), np.array(p.entry.xyz)
    assert np.allclose(atlas.exit_point(t, e - t), e)
    assert p.entry.region and atlas.surface_region(e).name == p.entry.region or p.entry.mode in ("region", "through")
    assert "white matter" not in p.entry.text and p.name[0] in "LR"


def test_typo_and_count(atlas):
    p = parse("left amygdla, 6c", atlas)
    assert p.ok and region_of(atlas, p.target) == "Left Amygdala" and p.n_contacts == 6


def test_deep_entry_goes_through(atlas):
    p = parse("ACC entry, RHH", atlas)
    assert p.ok and p.entry.mode == "through" and "through" in p.entry.text and p.name == "RACC"
    assert atlas.depth_at(p.entry.xyz) <= 4


def test_coordinates(atlas):
    p = parse("target -25 -20 -15 entry lateral 6 contacts", atlas)
    assert p.ok and p.target.xyz == [-25.0, -20.0, -15.0] and p.entry.mode == "lateral" and p.n_contacts == 6
    assert p.entry.xyz[0] < -40


def test_unknown_word(atlas):
    p = parse("banana", atlas)
    assert not p.ok and "banana" in p.read_as


def test_empty(atlas):
    p = parse("  ", atlas)
    assert not p.ok and p.question is None and p.options == []


def test_entry_only_needs_target(atlas):
    p = parse("right temporal pole entry", atlas)
    assert not p.ok and "target" in p.read_as.lower()


@pytest.mark.parametrize("text", ["STG RHH", "RPUL", "right temporal pole entry, anterior amygdala target",
                                  "OFC LHB", "RACC", "left amygdala"])
def test_defaults_sane(atlas, text):
    p = parse(text, atlas)
    assert p.ok and 1 <= p.default_contacts <= 16
    t = np.array(p.target.xyz)
    assert atlas.label_at(t) == atlas.label_at(p.target.xyz) != "outside brain"


def test_target_with_several_parts_asks(atlas):
    p = parse("left cingulate gyrus", atlas)
    assert not p.ok and "which" in p.question
    assert [o.label for o in p.options] == ["Anterior Cingulate Gyrus", "Posterior Cingulate Gyrus"]
    assert all(o.ok and o.target.xyz[0] < 0 for o in p.options)


@pytest.mark.parametrize("text, target", [
    ("left superior LOC", "Left Superior Lateral Occipital Cortex"),     # a modifier in one name picks it
    ("right anterior PHG", "Right Anterior Parahippocampal Gyrus"),
    ("left posterior parahippocampal gyrus", "Left Posterior Parahippocampal Gyrus"),
    ("RIFG pars triangularis", "Right Inferior Frontal Gyrus pars triangularis"),
])
def test_modifier_picks_named_part(atlas, text, target):
    p = parse(text, atlas)
    assert p.ok, p.read_as
    assert region_of(atlas, p.target) == target and "(" not in p.target.text


def test_merged_gyrus_thirds(atlas):
    # STG is one region, so "anterior STG" is its front third
    a, p = parse("RSTG anterior", atlas), parse("RSTG posterior", atlas)
    assert region_of(atlas, a.target) == region_of(atlas, p.target) == "Right Superior Temporal Gyrus"
    assert a.target.xyz[1] > p.target.xyz[1] + 20 and a.target.text == "R Superior Temporal Gyrus (anterior)"


def test_hippocampus_anterior_abbrev(atlas):
    assert parse("LAH", atlas).target.abbrev == "LAH"


def test_unknown_word_suggests_rewrites(atlas):
    p = parse("left frontl pole", atlas)
    assert p.ok and p.target.region == "Left Frontal Pole"
    p = parse("frontal", atlas)
    assert not p.ok and p.options and all(o.text for o in p.options)
    assert any("frontal" in o.text for o in p.options)


def _mods_for(r):
    mods = [None, "anterior", "posterior", "superior", "inferior", "medial", "lateral"]
    return mods + (["head", "body", "tail"] if r.base == "Hippocampus" else [])


def test_displayed_text_reparses_as_target(atlas):
    # Editing a list row re-parses its displayed text, which must give back the same region and part
    from epilens.matcher import _label
    bad = []
    for r in atlas.regions:
        for mod in _mods_for(r):
            t = atlas.target_point(r, mod)
            ex = t + [30 if r.hemi == "R" else -30, 0, 0]
            p = parse(f"entry {ex[0]:.1f} {ex[1]:.1f} {ex[2]:.1f}, {_label(r.hemi, r.base, mod)} target", atlas)
            if not (p.ok and p.target.region == r.name and p.target.xyz == list(t)):
                bad.append((r.name, mod, p.read_as))
    assert bad == []


def test_displayed_text_reparses_as_entry(atlas):
    from epilens.matcher import _label
    bad = []
    for r in atlas.regions:
        if not atlas.reaches_surface(r):
            continue
        for mod in _mods_for(r):
            t = atlas.target_point(atlas.region("Thalamus", r.hemi), None)
            p = parse(f"target {t[0]:.1f} {t[1]:.1f} {t[2]:.1f}, {_label(r.hemi, r.base, mod)} entry", atlas)
            if not (p.ok and p.entry.region == r.name and p.entry.xyz == list(atlas.snap_entry(t, atlas.entry_point(r, mod)))):
                bad.append((r.name, mod, p.read_as))
    assert bad == []
