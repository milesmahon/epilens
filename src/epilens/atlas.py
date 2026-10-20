"""MNI atlas geometry

Data is prebuilt by scripts/build_atlas.py
"""

import csv
import functools
from dataclasses import dataclass
from importlib import resources

import numpy as np

SURFACE_MM = 4.0       # a voxel this close to the brain-mask boundary counts as "on the surface"
SURFACE_MIN_VOXELS = 20
MAX_CONTACTS = 16
OUTSIDE, WHITE = "outside brain", "white matter"

# modifier: (axis, which third); axis "absx" = distance from midline
THIRDS = {
    "anterior": ("y", 2), "head": ("y", 2), "body": ("y", 1), "posterior": ("y", 0), "tail": ("y", 0),
    "superior": ("z", 2), "inferior": ("z", 0), "medial": ("absx", 0), "lateral": ("absx", 2),
}


@dataclass(frozen=True)
class Region:
    id: int
    name: str     # "Right Amygdala"
    hemi: str     # "L" | "R"
    base: str     # "Amygdala"
    abbrev: str   # "A" (no hemisphere letter)
    kind: str     # "cortex" | "deep"


class Atlas:
    def __init__(self, labels: np.ndarray, depth: np.ndarray, affine: np.ndarray, regions: list[Region]):
        self.labels, self.depth, self.affine = labels, depth, affine
        self.inv = np.linalg.inv(affine)
        self.regions = regions
        self._by_id = {r.id: r for r in regions}
        self._by_base = {(r.base.lower(), r.hemi): r for r in regions}
        self._vox = {}

    @classmethod
    @functools.cache
    def load(cls) -> "Atlas":
        data = resources.files("epilens") / "data"
        with resources.as_file(data / "atlas.npz") as p:
            z = np.load(p)
            labels, depth, affine = z["labels"], z["depth"], z["affine"]
        rows = csv.DictReader((data / "regions.csv").read_text().splitlines())
        regions = [Region(int(r["id"]), r["name"], r["hemi"], r["base"], r["abbrev"], r["kind"]) for r in rows]
        return cls(labels, depth, affine, regions)

    # ---------- lookup ----------
    def region(self, base: str, hemi: str) -> Region:
        return self._by_base[(base.lower(), hemi)]

    def _ijk(self, xyz) -> tuple[int, int, int] | None:
        ijk = np.rint(self.inv[:3, :3] @ np.asarray(xyz, float) + self.inv[:3, 3]).astype(int)
        if np.any(ijk < 0) or np.any(ijk >= self.labels.shape):
            return None
        return tuple(ijk)

    def label_at(self, xyz) -> str:
        ijk = self._ijk(xyz)
        v = 0 if ijk is None else int(self.labels[ijk])
        return OUTSIDE if v == 0 else WHITE if v == 1 else self._by_id[v].name

    def depth_at(self, xyz) -> float:
        ijk = self._ijk(xyz)
        return 0.0 if ijk is None else float(self.depth[ijk])

    def surface_region(self, xyz, radius: int = 3) -> Region | None:
        # Nearest cortical region to a point within ``radius`` voxels
        c = self.inv[:3, :3] @ np.asarray(xyz, float) + self.inv[:3, 3]
        ijk0 = np.rint(c).astype(int)
        A, best, best_d = self.affine, None, float("inf")
        p = [float(v) for v in xyz]
        for di in range(-radius, radius + 1):
            for dj in range(-radius, radius + 1):
                for dk in range(-radius, radius + 1):
                    i, j, k = int(ijk0[0]) + di, int(ijk0[1]) + dj, int(ijk0[2]) + dk
                    if not (0 <= i < self.labels.shape[0] and 0 <= j < self.labels.shape[1] and 0 <= k < self.labels.shape[2]):
                        continue
                    r = self._by_id.get(int(self.labels[i, j, k]))
                    if r is None or r.kind != "cortex":
                        continue
                    w = [A[a][0] * i + A[a][1] * j + A[a][2] * k + A[a][3] for a in range(3)]
                    d = (w[0] - p[0]) ** 2 + (w[1] - p[1]) ** 2 + (w[2] - p[2]) ** 2
                    if d < best_d:
                        best, best_d = r, d
        return best

    def snap_entry(self, target, entry) -> np.ndarray:
        # Move the entry point along the line from target to entry until it exits the brain
        t, e = np.asarray(target, float), np.asarray(entry, float)
        s = self.exit_point(t, e - t)
        return s if np.linalg.norm(s - t) >= 1 else e # target outside the brain: leave the entry alone

    def inside(self, xyz) -> bool:
        ijk = self._ijk(xyz)
        return ijk is not None and self.labels[ijk] > 0

    # ---------- region geometry ----------
    def _voxels(self, region: Region) -> tuple[np.ndarray, np.ndarray]:
        # World coordinates (n, 3) and depths (n,) of a region's voxels
        if region.id not in self._vox:
            ijk = np.argwhere(self.labels == region.id)
            self._vox[region.id] = (ijk @ self.affine[:3, :3].T + self.affine[:3, 3], self.depth[tuple(ijk.T)])
        return self._vox[region.id]

    @staticmethod
    def _third(xyz: np.ndarray, modifier: str | None) -> np.ndarray:
        # Boolean mask of voxels in the modifier's third of the region
        if not modifier or modifier not in THIRDS:
            return np.ones(len(xyz), bool)
        axis, k = THIRDS[modifier]
        v = np.abs(xyz[:, 0]) if axis == "absx" else xyz[:, "xyz".index(axis)]
        lo, hi = np.quantile(v, [1 / 3, 2 / 3])
        keep = [v <= lo, (v >= lo) & (v <= hi), v >= hi][k]
        return keep if keep.any() else np.ones(len(xyz), bool)

    @staticmethod
    def _centroid_snapped(pts: np.ndarray) -> np.ndarray:
        c = pts.mean(axis=0)
        return pts[np.argmin(((pts - c) ** 2).sum(axis=1))].astype(float)

    def target_point(self, region: Region, modifier: str | None = None) -> np.ndarray:
        xyz, _ = self._voxels(region)
        return self._centroid_snapped(xyz[self._third(xyz, modifier)])

    def reaches_surface(self, region: Region) -> bool:
        _, d = self._voxels(region)
        return int((d <= SURFACE_MM).sum()) >= SURFACE_MIN_VOXELS

    def entry_point(self, region: Region, modifier: str | None = None) -> np.ndarray:
        xyz, d = self._voxels(region)
        keep = self._third(xyz, modifier)
        surf = keep & (d <= SURFACE_MM)
        return self._centroid_snapped(xyz[surf if surf.any() else keep])

    def exit_point(self, origin, direction) -> np.ndarray:
        o = np.asarray(origin, float)
        u = np.asarray(direction, float)
        n = np.linalg.norm(u)
        if n == 0:
            return o
        u = u / n
        last = o
        for s in np.arange(0.5, 300, 0.5):
            p = o + s * u
            if not self.inside(p):
                break
            last = p
        return last

    def fit_contacts(self, target, entry, spacing: float) -> int:
        t = np.asarray(target, float)
        exit_ = self.exit_point(t, np.asarray(entry, float) - t)
        return int(max(1, min(MAX_CONTACTS, np.floor(np.linalg.norm(exit_ - t) / spacing) + 1)))
