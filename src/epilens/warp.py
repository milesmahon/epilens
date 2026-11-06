"""MNI <-> patient conversion from an SPM `y_MR` deformation field."""
import nibabel as nib
import numpy as np
from scipy.ndimage import map_coordinates


class Warp:
    """`field` is float32 (nx, ny, nz, 3): patient mm at each voxel of the MNI grid given by `affine`."""

    def __init__(self, field: np.ndarray, affine: np.ndarray):
        self.field = np.asarray(field, dtype=np.float32)
        self.affine = np.asarray(affine, dtype=float)
        self.inv_affine = np.linalg.inv(self.affine)
        # Least-squares affine patient -> MNI, used as the starting guess for inversion.
        sub = self.field[::4, ::4, ::4]
        ijk = np.stack(np.meshgrid(*[np.arange(0, n, 4) for n in self.field.shape[:3]], indexing="ij"), -1)
        ok = np.isfinite(sub).all(axis=-1)
        pat = sub[ok].astype(float)
        mni = ijk[ok] @ self.affine[:3, :3].T + self.affine[:3, 3]
        A = np.c_[pat, np.ones(len(pat))]
        self._start = np.linalg.lstsq(A, mni, rcond=None)[0]  # (4, 3)

    @classmethod
    def from_nifti(cls, img: nib.Nifti1Image) -> "Warp":
        data = np.asarray(img.dataobj, dtype=np.float32)
        return cls(data.reshape(*data.shape[:3], 3), img.affine)

    def to_patient(self, xyz) -> np.ndarray:
        pts = np.atleast_2d(np.asarray(xyz, dtype=float))
        ijk = (pts - self.affine[:3, 3]) @ self.inv_affine[:3, :3].T
        out = np.stack(
            [map_coordinates(self.field[..., c], ijk.T, order=1, mode="nearest", prefilter=False) for c in range(3)],
            axis=-1,
        )
        return out[0] if np.ndim(xyz) == 1 else out

    def invert(self, xyz) -> tuple[np.ndarray, np.ndarray]:
        """Returns (mni, residual_mm) for patient points."""
        p = np.atleast_2d(np.asarray(xyz, dtype=float))
        x = np.c_[p, np.ones(len(p))] @ self._start
        err = p - self.to_patient(x)
        for _ in range(6):
            todo = np.flatnonzero(np.linalg.norm(err, axis=-1) > 1e-3)  # points already within 0.001 mm stop here
            if len(todo) == 0:
                break
            x[todo] += err[todo]
            err[todo] = p[todo] - self.to_patient(x[todo])
        residual = np.linalg.norm(err, axis=-1)
        if np.ndim(xyz) == 1:
            return x[0], residual[0]
        return x, residual

    def to_mni(self, xyz) -> np.ndarray:
        return self.invert(xyz)[0]
