"""A patient's brain from LeGUI/SPM12 `Registered/` files. Stays in memory (imaging is not written to disk)

The patient atlas is a 1 mm grid in the patient's AC-PC mm, with the same label encoding and regions as
atlas.npz. Each brain voxel takes the template label it maps to through the SPM warp.
"""

import base64
import contextlib
import functools
import gzip
import io
import json
from dataclasses import dataclass
from importlib import resources

import h5py
import nibabel as nib
import numpy as np
import scipy.io
from scipy import ndimage
from scipy.spatial import cKDTree

from . import __version__
from .atlas import Atlas
from .warp import Warp

REQUIRED = ("mr", "y_mr", "surfaces", "mrgray", "mrwhite")
OPTIONAL = ("acpcmm", "ct")
FILE_NAMES = {"mr": "MR.nii", "y_mr": "y_MR.nii", "surfaces": "Surfaces.mat", "mrgray": "MRGray.nii",
              "mrwhite": "MRWhite.nii", "acpcmm": "ACPCmm.mat", "ct": "CT.nii"}
MNI_CENTER = (0, -18, 8)   # where the view looks first, in MNI mm
MARGIN_MM = 5              # kept around the brain mask's bounding box
NEAR_MM = 3                # for the checks, points this close to the brain mask count as matching it
COARSE = 4                 # voxels between the points where the warp is inverted exactly
MATCH = 0.9                # fraction of Brain vertices (or warped template brain voxels) that must match


class PatientError(ValueError):
    pass


@dataclass
class Mesh:
    name: str
    color: str
    v: np.ndarray            # (n, 3) mm
    f: np.ndarray            # (m, 3) 0-based
    s: np.ndarray | None = None   # sulcal depth per vertex (Brain only), for shading


@dataclass
class Patient:
    atlas: Atlas
    warp: Warp
    payload: bytes           # the anatomy JSON, like the template's
    warnings: list[str]


def file_key(name: str) -> str | None:
    """'MR.nii' or 'mr.nii.gz' -> 'mr'. None for a file we don't need."""
    stem = name.lower()
    for ext in (".nii.gz", ".nii", ".mat"):
        if stem.endswith(ext):
            stem = stem[: -len(ext)]
            break
    return stem if stem in REQUIRED + OPTIONAL else None


# Payload
def _b64(a: np.ndarray) -> str:
    return base64.b64encode(np.ascontiguousarray(a).tobytes()).decode()


@functools.cache
def template_meshes() -> tuple[Mesh, ...]:
    with resources.as_file(resources.files("epilens") / "data" / "meshes.npz") as p:
        z = np.load(p)
        return tuple(Mesh(n, str(z[f"{n}/c"]), z[f"{n}/v"], z[f"{n}/f"], z[f"{n}/s"] if f"{n}/s" in z else None)
                     for n in z["names"].tolist())


def anatomy_json(meshes, atlas: Atlas, **extra) -> bytes:
    out = []
    for m in meshes:
        faces = m.f.astype(np.uint16) if len(m.v) < 65536 else m.f.astype(np.uint32)
        d = {"name": m.name, "color": m.color, "kind": "hull" if m.name == "Brain" else "deep",
             "v": _b64(m.v.astype(np.float32)), "f": _b64(faces), "f32": faces.dtype == np.uint32}
        if m.s is not None:
            d["s"] = _b64(m.s.astype(np.uint8))
        out.append(d)
    body = {"meshes": out, "labels": _b64(atlas.labels.astype(np.uint8)), "shape": list(atlas.labels.shape),
            "affine": atlas.affine.tolist(),
            "regions": [{"id": r.id, "name": r.name, "hemi": r.hemi, "base": r.base, "abbrev": r.abbrev, "kind": r.kind}
                        for r in atlas.regions],
            "version": __version__, **extra}
    return json.dumps(body, separators=(",", ":")).encode()


# Reading files
@contextlib.contextmanager
def _reading(key: str):
    """Turn anything that goes wrong while reading a file into a PatientError that names the file."""
    try:
        yield
    except PatientError:
        raise
    except KeyError as e:
        raise PatientError(f"{FILE_NAMES[key]}: missing {e}") from e
    except Exception as e:
        raise PatientError(f"{FILE_NAMES[key]}: can't read ({e})") from e


def _nifti(files: dict[str, bytes], key: str) -> nib.Nifti1Image:
    with _reading(key):
        data = files[key]
        if data[:2] == b"\x1f\x8b":
            data = gzip.decompress(data)
        img = nib.Nifti1Image.from_bytes(data)
    extra = int(np.prod(img.shape[3:]))   # y_MR is (nx, ny, nz, 1, 3); everything else one 3-D volume
    if len(img.shape) < 3 or extra != (3 if key == "y_mr" else 1):
        raise PatientError(f"{FILE_NAMES[key]}: unexpected shape {img.shape}")
    return img


def _same_grid(a: nib.Nifti1Image, b: nib.Nifti1Image) -> bool:
    return a.shape[:3] == b.shape[:3] and np.allclose(a.affine, b.affine, atol=0.01)


def _mat(data: bytes) -> dict:
    """A .mat file as nested dicts of arrays. v7.3 files are HDF5, which MATLAB stores transposed."""
    try:
        return scipy.io.loadmat(io.BytesIO(data), simplify_cells=True)
    except NotImplementedError:
        def read(node):
            if isinstance(node, h5py.Dataset):
                return np.asarray(node).T
            return {k: read(v) for k, v in node.items() if not k.startswith("#")}
        with h5py.File(io.BytesIO(data), "r") as f:
            return read(f)


def _sample(img: nib.Nifti1Image, key: str, shape, affine: np.ndarray, order: int) -> np.ndarray:
    """Resample an image onto the grid (shape, affine), nearest (order 0) or trilinear (order 1). 0 outside the image."""
    m = np.linalg.inv(img.affine) @ affine   # grid voxel -> image voxel
    with _reading(key):
        if not np.allclose(m[:3, :3], np.diag(np.diag(m[:3, :3]))):   # the grids are turned relative to each other
            vol = np.asarray(img.dataobj, dtype=np.float32).reshape(img.shape[:3])
            return ndimage.affine_transform(vol, m[:3, :3], m[:3, 3], output_shape=shape, order=order,
                                            mode="constant", cval=0.0)
        # The usual case: both grids are axis-aligned, so each grid axis picks rows of one image axis.
        # Converting a whole 0.4 mm volume to float is the slow part, so only the voxels used are read and converted
        raw = np.asanyarray(img.dataobj.get_unscaled()).reshape(img.shape[:3])
        slope, inter = img.dataobj.slope, img.dataobj.inter
    pos = [m[a, a] * np.arange(shape[a]) + m[a, 3] for a in range(3)]   # image voxel coordinate along each axis
    inside = [(p >= -0.5 if order == 0 else p >= 0) & (p <= n - 0.5 if order == 0 else p <= n - 1)
              for p, n in zip(pos, raw.shape)]
    if order == 0:
        idx = [np.clip(np.rint(p).astype(int), 0, n - 1) for p, n in zip(pos, raw.shape)]
        out = raw[np.ix_(*idx)].astype(np.float32)
    else:
        lo = [np.clip(np.floor(p).astype(int), 0, n - 2) for p, n in zip(pos, raw.shape)]
        fr = [np.clip(p - i, 0, 1).astype(np.float32) for p, i in zip(pos, lo)]
        out = np.zeros(shape, np.float32)
        for corner in np.ndindex(2, 2, 2):
            wx, wy, wz = (f if c else 1 - f for f, c in zip(fr, corner))
            w = wx[:, None, None] * wy[None, :, None] * wz[None, None, :]
            out += raw[np.ix_(*[i + c for i, c in zip(lo, corner)])].astype(np.float32) * w
    out = out * np.float32(slope if np.isfinite(slope) else 1) + np.float32(inter if np.isfinite(inter) else 0)
    out *= inside[0][:, None, None] & inside[1][None, :, None] & inside[2][None, None, :]   # 0 outside the image
    return out


def _window(vol: np.ndarray, lo: float, hi: float) -> np.ndarray:
    return np.clip((vol - lo) * (255 / max(hi - lo, 1e-6)), 0, 255).astype(np.uint8)


def _world(ijk: np.ndarray, affine: np.ndarray) -> np.ndarray:
    return ijk @ affine[:3, :3].T + affine[:3, 3]


def _voxel(xyz: np.ndarray, affine: np.ndarray, shape) -> tuple[np.ndarray, np.ndarray]:
    """Nearest voxel index of each point, and whether it is on the grid."""
    ijk = np.rint((xyz - affine[:3, 3]) @ np.linalg.inv(affine[:3, :3]).T).astype(int)
    ok = np.all((ijk >= 0) & (ijk < shape), axis=1)
    return np.where(ok[:, None], ijk, 0), ok


def _surfaces(files: dict[str, bytes]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """BrainSurfRaw vertices and 0-based faces, and ProjSurfRaw vertices, from Surfaces.mat."""
    with _reading("surfaces"):
        s = _mat(files["surfaces"])
        v = np.asarray(s["BrainSurfRaw"]["vertices"], float)
        f = np.asarray(s["BrainSurfRaw"]["faces"], float)
        proj = np.asarray(s["ProjSurfRaw"]["vertices"], float)
    if not all(a.ndim == 2 and a.shape[1] == 3 and len(a) for a in (v, f, proj)):
        raise PatientError("Surfaces.mat: BrainSurfRaw and ProjSurfRaw should be n x 3 vertices and faces")
    if np.any(f != np.round(f)) or f.min() < 1 or f.max() > len(v):  # MATLAB numbers vertices from 1
        raise PatientError("Surfaces.mat: BrainSurfRaw faces should be vertex numbers from 1 to the number of vertices")
    return v, f.astype(np.int64) - 1, proj


def load_patient(files: dict[str, bytes], template: Atlas) -> Patient:
    """`files` holds the raw bytes of each file, keyed by `file_key`."""
    missing = [FILE_NAMES[k] for k in REQUIRED if k not in files]
    if missing:
        raise PatientError(f"Missing patient files: {', '.join(missing)}")
    warnings = []

    # Open the small files and the volume headers first, so a wrong file fails before the slow steps
    mr, gray, white = (_nifti(files, k) for k in ("mr", "mrgray", "mrwhite"))
    if not (_same_grid(mr, gray) and _same_grid(mr, white)):   # MRGray and MRWhite are SPM's segmentation of MR
        raise PatientError("MR.nii doesn't match MRGray.nii/MRWhite.nii (is it from another patient?)")
    ct = _nifti(files, "ct") if "ct" in files else None
    if ct is not None and not _same_grid(ct, mr):   # LeGUI reslices the CT onto the MR
        raise PatientError("CT.nii doesn't match MR.nii (is it from another patient?)")
    acpc = None
    if "acpcmm" in files:
        with _reading("acpcmm"):
            acpc = np.asarray(_mat(files["acpcmm"])["ACPCmm"], float)
        if acpc.ndim != 2 or acpc.shape[0] < 2 or acpc.shape[1] < 3:
            raise PatientError(f"ACPCmm.mat: expected AC and PC rows, got shape {acpc.shape}")

    # Brain mask on a 1 mm axis-aligned grid over the MR's field of view, then cropped to the brain
    corners = np.array(np.meshgrid(*[[0, n - 1] for n in mr.shape[:3]], indexing="ij")).reshape(3, -1).T
    world = _world(corners, mr.affine)
    lo, hi = np.floor(world.min(axis=0)), np.ceil(world.max(axis=0))
    affine = np.eye(4)
    affine[:3, 3] = lo
    shape = tuple(int(n) for n in hi - lo + 1)
    tissue = _sample(gray, "mrgray", shape, affine, order=0)
    tissue += _sample(white, "mrwhite", shape, affine, order=0)
    mask = tissue > 0.5
    del tissue, gray, white
    if not mask.any():
        raise PatientError("MRGray and MRWhite are empty")
    nz = np.argwhere(mask)
    a, b = np.maximum(nz.min(axis=0) - MARGIN_MM, 0), np.minimum(nz.max(axis=0) + MARGIN_MM + 1, shape)
    mask = mask[a[0]:b[0], a[1]:b[1], a[2]:b[2]]
    affine[:3, 3] += a
    shape = mask.shape

    # verify files match by comparing geometry
    a2 = affine.copy()
    a2[:3, :3] *= 2
    near = ndimage.binary_dilation(mask[::2, ::2, ::2], structure=np.ones((3, 3, 3), bool), iterations=NEAR_MM // 2)

    def near_mask(xyz):
        ijk, ok = _voxel(xyz, a2, near.shape)
        return np.mean(ok & near[tuple(ijk.T)])

    brain_v, faces, proj_v = _surfaces(files)
    if near_mask(brain_v) < MATCH:
        raise PatientError("Surfaces.mat: surface doesn't match MR (is it from another patient?)")
    y_mr = _nifti(files, "y_mr")
    with _reading("y_mr"):
        warp = Warp.from_nifti(y_mr)
    del y_mr
    some = np.argwhere(template.labels > 0)[::8]   # a sample of the template brain is enough to tell
    if near_mask(warp.to_patient(_world(some, template.affine))) < MATCH:
        raise PatientError("y_MR.nii: warp doesn't match MR (is it from another patient?)")

    # coarse grid because labeling is slow otherwise
    coarse = [np.arange(0, n - 1 + COARSE, COARSE) for n in shape]
    cijk = np.stack(np.meshgrid(*coarse, indexing="ij"), axis=-1).reshape(-1, 3)
    cmni, residual = warp.invert(_world(cijk, affine))
    near, _ = _voxel(_world(cijk, affine), affine, shape)
    residual = residual[mask[tuple(near.T)]]   # only where the brain is
    inside = np.argwhere(mask)
    cmni = cmni.reshape(*[len(c) for c in coarse], 3)
    mni = np.stack([ndimage.map_coordinates(cmni[..., c], (inside / COARSE).T, order=1, prefilter=False)
                    for c in range(3)], axis=1)
    if residual.size and np.percentile(residual, 99) > 0.5:
        warnings.append(f"The warp is hard to invert in places (99th percentile error {np.percentile(residual, 99):.1f} mm); "
                        "regions may be mislabelled there.")
    tijk, ok = _voxel(mni, template.affine, template.labels.shape)
    labels = np.zeros(shape, np.uint8)
    labels[tuple(inside.T)] = np.where(ok, np.maximum(template.labels[tuple(tijk.T)], 1), 1)
    # Depth at 2 mm is plenty (it decides what counts as near the surface) and much faster than at 1 mm
    d2 = ndimage.distance_transform_edt(np.pad(mask[::2, ::2, ::2], 1))[1:-1, 1:-1, 1:-1] * 2
    depth = np.repeat(np.repeat(np.repeat(d2, 2, 0), 2, 1), 2, 2)[: shape[0], : shape[1], : shape[2]]
    depth = np.where(mask, np.minimum(depth, 255), 0).astype(np.uint8)
    atlas = Atlas(labels, depth, affine, template.regions)

    # Volumes for the cross sections
    vol = _sample(mr, "mr", shape, affine, order=1)
    volumes = {"MR": _b64(_window(vol, *np.percentile(vol[mask], [1, 99])))}
    del vol
    if ct is not None:
        volumes["CT"] = _b64(_window(_sample(ct, "ct", shape, affine, order=1), 0, 80))

    # Meshes: the patient's own cortex, and the template's deep structures moved through the warp
    t = brain_v[faces]
    if np.einsum("ij,ij->", t[:, 0], np.cross(t[:, 1], t[:, 2])) < 0:  # the faces point inward, so flip them
        faces = faces[:, ::-1]
    sulc = cKDTree(proj_v).query(brain_v)[0]
    meshes = [Mesh("Brain", m.color, brain_v, faces, np.clip(sulc, 0, 255)) if m.name == "Brain"
              else Mesh(m.name, m.color, warp.to_patient(m.v.astype(float)), m.f) for m in template_meshes()]

    landmarks = {}
    if acpc is not None:
        landmarks = {"AC": acpc[0, :3].tolist(), "PC": acpc[1, :3].tolist()}
        mid = (acpc[0, :3] + acpc[1, :3]) / 2
        if np.linalg.norm(mid) > 3:
            warnings.append(f"The AC-PC midpoint is {np.linalg.norm(mid):.0f} mm from the origin; "
                            "the MR may not be in AC-PC space.")

    payload = anatomy_json(meshes, atlas, space="patient", center=warp.to_patient(MNI_CENTER).tolist(),
                           volumes=volumes, landmarks=landmarks)
    return Patient(atlas, warp, payload, warnings)
