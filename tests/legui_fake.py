"""Fake LeGUI/SPM12 data for tests."""
import io
from importlib import resources

import nibabel as nib
import numpy as np
import scipy.io

from epilens.atlas import Atlas
from epilens.warp import Warp


def affine_warp(T: np.ndarray) -> Warp:
    """A Warp whose field is the 4x4 affine T applied to each voxel centre of the template grid."""
    atlas = Atlas.load()
    shape = atlas.labels.shape
    ijk = np.stack(np.meshgrid(*[np.arange(n) for n in shape], indexing="ij"), -1)
    mni = ijk @ atlas.affine[:3, :3].T + atlas.affine[:3, 3]
    field = mni @ T[:3, :3].T + T[:3, 3]
    return Warp(field.astype(np.float32), atlas.affine)


def _nifti(data: np.ndarray, affine: np.ndarray) -> bytes:
    return nib.Nifti1Image(data, affine).to_bytes()


def make_legui(T: np.ndarray, *, surface_shift=(0, 0, 0), with_ct=True) -> dict[str, bytes]:
    """Fake LeGUI `Registered/` files (keyed like `patient.file_key`) for a patient whose brain is the template moved by T.

    Volumes are on 2 mm grids for faster tests.
    """
    atlas = Atlas.load()
    # fake patient grid LAS voxel order like real imaging covering the template brain moved by T plus 10 mm
    brain = np.argwhere(atlas.labels > 0)
    world = (brain @ atlas.affine[:3, :3].T + atlas.affine[:3, 3]) @ T[:3, :3].T + T[:3, 3]
    lo, hi = world.min(axis=0) - 10, world.max(axis=0) + 10
    shape = tuple(int(n) for n in np.ceil((hi - lo) / 2) + 1)
    affine = np.diag([-2.0, 2.0, 2.0, 1.0])
    affine[:3, 3] = [hi[0], lo[1], lo[2]]

    # Pull each patient voxel back to the template labels
    ijk = np.stack(np.meshgrid(*[np.arange(n) for n in shape], indexing="ij"), -1).reshape(-1, 3)
    pat = ijk @ affine[:3, :3].T + affine[:3, 3]
    mni = (pat - T[:3, 3]) @ np.linalg.inv(T[:3, :3]).T
    tijk = np.rint((mni - atlas.affine[:3, 3]) @ atlas.inv[:3, :3].T).astype(int)
    ok = np.all((tijk >= 0) & (tijk < atlas.labels.shape), axis=1)
    labels = np.zeros(len(ijk), np.uint8)
    labels[ok] = atlas.labels[tuple(tijk[ok].T)]
    labels = labels.reshape(shape)
    gray, white = (labels >= 2).astype(np.float64), (labels == 1).astype(np.float64)
    mr = np.where(labels == 1, 200, np.where(labels >= 2, 120, 0)).astype(np.float32)

    z = np.load(resources.files("epilens") / "data" / "meshes.npz")
    v, f = z["Brain/v"].astype(float), z["Brain/f"]
    v = v @ T[:3, :3].T + T[:3, 3] + np.asarray(surface_shift, float)
    normals = np.zeros_like(v)
    face_n = np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]])
    for k in range(3):
        np.add.at(normals, f[:, k], face_n)
    normals /= np.linalg.norm(normals, axis=1, keepdims=True)
    surfaces = io.BytesIO()
    scipy.io.savemat(surfaces, {"BrainSurfRaw": {"vertices": v, "faces": f.astype(np.int32) + 1},
                                "ProjSurfRaw": {"vertices": v + 3 * normals, "faces": f.astype(np.int32) + 1}})
    acpc = io.BytesIO()
    scipy.io.savemat(acpc, {"ACPCmm": np.array([[0, 12.65, 0, 1], [0, -12.65, 0, 1], [0, -1.06, 80.26, 1]])})

    warp = affine_warp(T)
    files = {
        "mr": _nifti(mr, affine),
        "y_mr": _nifti(warp.field.reshape(*warp.field.shape[:3], 1, 3), warp.affine),
        "mrgray": _nifti(gray, affine),
        "mrwhite": _nifti(white, affine),
        "surfaces": surfaces.getvalue(),
        "acpcmm": acpc.getvalue(),
    }
    if with_ct:
        files["ct"] = _nifti(np.where(labels > 0, 40, 0).astype(np.float32), affine)
    return files
