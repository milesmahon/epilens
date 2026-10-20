"""Builds the atlas data in src/epilens/data

    uv run python scripts/build_atlas.py

Writes atlas.npz, meshes.npz and regions.csv on the Harvard-Oxford 2 mm MNI grid:
  labels  uint8: 0 = outside brain, 1 = brain without a named region (white matter), >= 2 = region id
  depth   uint8: mm from the voxel to the brain-mask boundary (0 outside)
"""

import csv
from pathlib import Path
from typing import Any

import numpy as np
from nilearn import datasets, image
from scipy import ndimage
from skimage import measure

OUT = Path(__file__).parents[1] / "src" / "epilens" / "data"

# HO cortical label -> (base name, abbreviation); matcher splits on commas
# HO's anterior/posterior divisions are merged, except cingulate and parahippocampal
CORTEX = {
    "Frontal Pole": ("Frontal Pole", "FP"),
    "Insular Cortex": ("Insular Cortex", "INS"),
    "Superior Frontal Gyrus": ("Superior Frontal Gyrus", "SFG"),
    "Middle Frontal Gyrus": ("Middle Frontal Gyrus", "MFG"),
    "Inferior Frontal Gyrus, pars triangularis": ("Inferior Frontal Gyrus pars triangularis", "IFGt"),
    "Inferior Frontal Gyrus, pars opercularis": ("Inferior Frontal Gyrus pars opercularis", "IFGo"),
    "Precentral Gyrus": ("Precentral Gyrus", "PreCG"),
    "Temporal Pole": ("Temporal Pole", "TP"),
    "Superior Temporal Gyrus, anterior division": ("Superior Temporal Gyrus", "STG"),
    "Superior Temporal Gyrus, posterior division": ("Superior Temporal Gyrus", "STG"),
    "Middle Temporal Gyrus, anterior division": ("Middle Temporal Gyrus", "MTG"),
    "Middle Temporal Gyrus, posterior division": ("Middle Temporal Gyrus", "MTG"),
    "Middle Temporal Gyrus, temporooccipital part": ("Temporooccipital Middle Temporal Gyrus", "MTGto"),
    "Inferior Temporal Gyrus, anterior division": ("Inferior Temporal Gyrus", "ITG"),
    "Inferior Temporal Gyrus, posterior division": ("Inferior Temporal Gyrus", "ITG"),
    "Inferior Temporal Gyrus, temporooccipital part": ("Temporooccipital Inferior Temporal Gyrus", "ITGto"),
    "Postcentral Gyrus": ("Postcentral Gyrus", "PostCG"),
    "Superior Parietal Lobule": ("Superior Parietal Lobule", "SPL"),
    "Supramarginal Gyrus, anterior division": ("Supramarginal Gyrus", "SMG"),
    "Supramarginal Gyrus, posterior division": ("Supramarginal Gyrus", "SMG"),
    "Angular Gyrus": ("Angular Gyrus", "AG"),
    "Lateral Occipital Cortex, superior division": ("Superior Lateral Occipital Cortex", "LOCs"),
    "Lateral Occipital Cortex, inferior division": ("Inferior Lateral Occipital Cortex", "LOCi"),
    "Intracalcarine Cortex": ("Intracalcarine Cortex", "CALC"),
    "Frontal Medial Cortex": ("Frontal Medial Cortex", "FMC"),
    "Juxtapositional Lobule Cortex (formerly Supplementary Motor Cortex)": ("Supplementary Motor Cortex", "SMA"),
    "Subcallosal Cortex": ("Subcallosal Cortex", "SCC"),
    "Paracingulate Gyrus": ("Paracingulate Gyrus", "PaCG"),
    "Cingulate Gyrus, anterior division": ("Anterior Cingulate Gyrus", "ACC"),
    "Cingulate Gyrus, posterior division": ("Posterior Cingulate Gyrus", "PCC"),
    "Precuneous Cortex": ("Precuneus", "PCUN"),
    "Cuneal Cortex": ("Cuneus", "CUN"),
    "Frontal Orbital Cortex": ("Frontal Orbital Cortex", "OFC"),
    "Parahippocampal Gyrus, anterior division": ("Anterior Parahippocampal Gyrus", "PHG"),
    "Parahippocampal Gyrus, posterior division": ("Posterior Parahippocampal Gyrus", "PHG"),
    "Lingual Gyrus": ("Lingual Gyrus", "LING"),
    "Temporal Fusiform Cortex, anterior division": ("Temporal Fusiform Cortex", "FUS"),
    "Temporal Fusiform Cortex, posterior division": ("Temporal Fusiform Cortex", "FUS"),
    "Temporal Occipital Fusiform Cortex": ("Temporal Occipital Fusiform Cortex", "TOF"),
    "Occipital Fusiform Gyrus": ("Occipital Fusiform Gyrus", "OFus"),
    "Frontal Opercular Cortex": ("Frontal Opercular Cortex", "FO"),
    "Central Opercular Cortex": ("Central Opercular Cortex", "CO"),
    "Parietal Opercular Cortex": ("Parietal Opercular Cortex", "PO"),
    "Planum Polare": ("Planum Polare", "PP"),
    "Heschl's Gyrus (includes H1 and H2)": ("Heschl's Gyrus", "HG"),
    "Planum Temporale": ("Planum Temporale", "PT"),
    "Supracalcarine Cortex": ("Supracalcarine Cortex", "SCAL"),
    "Occipital Pole": ("Occipital Pole", "OP"),
}
DEEP = {"Thalamus": "T", "Caudate": "CAU", "Putamen": "PUT", "Pallidum": "GP", "Hippocampus": "H",
        "Amygdala": "A", "Accumbens": "NAC"}
PULVINAR_AAL = ("Thal_PuI", "Thal_PuM", "Thal_PuA", "Thal_PuL")
ANT_AAL = ("Thal_AV",) # AAL3 only has the anteroventral part of ANT

DEEP_COLORS = {"Hippocampus": "#e8a33d", "Amygdala": "#d1495b", "Thalamus": "#6c8ead", "Caudate": "#8fae6b",
               "Putamen": "#5fa8a0", "Pallidum": "#9d7bb0", "Accumbens": "#c47f9a", "Lateral Ventricle": "#8a8f98"}
HULL_COLOR = "#b8c4d6"


def build_labels():
    cort = datasets.fetch_atlas_harvard_oxford("cort-maxprob-thr25-2mm", verbose=0)
    sub = datasets.fetch_atlas_harvard_oxford("sub-maxprob-thr25-2mm", verbose=0)
    ref = image.load_img(cort.maps)
    affine = ref.affine
    shape = ref.shape
    ijk = np.indices(shape).reshape(3, -1).T
    world_x = (ijk @ affine[:3, :3].T + affine[:3, 3])[:, 0].reshape(shape)
    hemi_of = np.where(world_x < 0, "L", "R")

    regions, labels = [], np.zeros(shape, np.uint8)

    def add(name, hemi, base, abbrev, kind, vox):
        rid = len(regions) + 2
        regions.append(dict(id=rid, name=name, hemi=hemi, base=base, abbrev=abbrev, kind=kind))
        labels[vox] = rid

    cdat = np.asarray(image.load_img(cort.maps).get_fdata(), int)
    ho_ids: dict[tuple[str, str], list[int]] = {}   # merged regions have several HO labels
    for i, lab in enumerate(cort.labels):
        if lab in CORTEX:
            ho_ids.setdefault(CORTEX[lab], []).append(i)
    for (base, ab), ids in ho_ids.items():
        for hemi, side in (("L", "Left"), ("R", "Right")):
            add(f"{side} {base}", hemi, base, ab, "cortex", np.isin(cdat, ids) & (hemi_of == hemi))

    sdat = np.asarray(image.load_img(sub.maps).get_fdata(), int)
    for i, lab in enumerate(sub.labels):
        side, _, struct = lab.partition(" ")
        if struct in DEEP and side in ("Left", "Right"):
            add(lab, side[0], struct, DEEP[struct], "deep", sdat == i)

    aal = datasets.fetch_atlas_aal(version="3v2", verbose=0)
    adat = np.asarray(image.resample_to_img(image.load_img(aal.maps), ref, interpolation="nearest",
                                            force_resample=True, copy_header=True).get_fdata(), int)
    for base, abbrev, parts in (("Pulvinar", "PUL", PULVINAR_AAL), ("Anterior Thalamic Nucleus", "ANT", ANT_AAL)):
        for side, h in (("Left", "L"), ("Right", "R")):
            idx = [int(i) for l, i in zip(aal.labels, aal.indices) if l.startswith(parts) and l.endswith(f"_{h}")]
            add(f"{side} {base}", h, base, abbrev, "deep", np.isin(adat, idx))

    mask_img = image.resample_to_img(datasets.load_mni152_brain_mask(resolution=2), ref, interpolation="nearest",
                                     force_resample=True, copy_header=True)
    mask = (mask_img.get_fdata() > 0) | (labels > 0)
    labels[mask & (labels == 0)] = 1
    labels[~mask] = 0
    depth = np.minimum(np.asarray(ndimage.distance_transform_edt(mask)) * 2.0, 255).astype(np.uint8)
    depth[~mask] = 0
    return labels, depth, affine, regions


def _mesh(mask, affine, sigma, step):
    # Smoothed surface of a binary volume, in MNI mm (from lens/brain.py)
    vol = ndimage.gaussian_filter(mask.astype(np.float32), sigma)
    verts, faces, _, _ = measure.marching_cubes(vol, level=0.5, step_size=step)
    verts = verts @ affine[:3, :3].T + affine[:3, 3]
    return verts.astype(np.float32), faces.astype(np.uint32)


def _cortex():
    # Folded brain surface from MNI152 gray + white matter, and sulcal depth per vertex for shading
    gm, wm = datasets.load_mni152_gm_template(resolution=1), datasets.load_mni152_wm_template(resolution=1)
    mask = datasets.load_mni152_brain_mask(resolution=1).get_fdata() > 0
    tissue = (gm.get_fdata() + wm.get_fdata()) * mask
    vol = ndimage.gaussian_filter(tissue.astype(np.float32), 0.6)
    ijk, faces, _, _ = measure.marching_cubes(vol, level=0.5, step_size=2)
    envelope = ndimage.gaussian_filter((tissue > 0.5).astype(np.float32), 4) > 0.3
    depth = ndimage.map_coordinates(ndimage.distance_transform_edt(envelope), ijk.T, order=1)
    verts = ijk @ gm.affine[:3, :3].T + gm.affine[:3, 3]
    a, b, c = (verts[faces[:, k]] for k in range(3))
    if np.einsum("ij,ij->", a, np.cross(b, c)) < 0:  # faces point inward: flip them
        faces = faces[:, ::-1]
    return verts.astype(np.float32), faces.astype(np.uint32), np.clip(depth, 0, 255).astype(np.uint8)


def build_meshes():
    meshes = {}
    v, f, sulc = _cortex()
    meshes["Brain"] = (v, f, HULL_COLOR, sulc)
    ho = datasets.fetch_atlas_harvard_oxford("sub-maxprob-thr25-1mm", verbose=0)
    maps = image.load_img(ho.maps)
    atlas = np.asarray(maps.get_fdata(), dtype=int)
    for i, label in enumerate(ho.labels):
        struct = label.removeprefix("Left ").removeprefix("Right ")
        if struct in DEEP_COLORS:
            meshes[label] = (*_mesh(atlas == i, maps.affine, sigma=1.0, step=1), DEEP_COLORS[struct])
    return meshes


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    labels, depth, affine, regions = build_labels()
    present = set(np.unique(labels))
    missing = [r["name"] for r in regions if r["id"] not in present]
    assert not missing, f"regions with no voxels: {missing}"
    assert all(r["abbrev"] for r in regions) and len(regions) + 2 <= 255
    np.savez_compressed(OUT / "atlas.npz", labels=labels, depth=depth, affine=affine)
    with open(OUT / "regions.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["id", "name", "hemi", "base", "abbrev", "kind"])
        w.writeheader()
        w.writerows(regions)
    meshes = build_meshes()
    arrays: dict[str, Any] = {"names": np.array(list(meshes))}
    for n, (v, fc, c, *sulc) in meshes.items():
        arrays |= {f"{n}/v": v, f"{n}/f": fc, f"{n}/c": np.array(c)}
        if sulc:
            arrays[f"{n}/s"] = sulc[0]
    np.savez_compressed(OUT / "meshes.npz", **arrays)
    for p in sorted(OUT.iterdir()):
        print(f"{p.name}: {p.stat().st_size / 1e6:.2f} MB")
    print(f"{len(regions)} regions")


if __name__ == "__main__":
    main()
