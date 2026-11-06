import numpy as np
import pytest

from legui_fake import affine_warp


def make_T():
    a = np.radians(5)
    T = np.eye(4)
    T[:3, :3] = 0.95 * np.array([[np.cos(a), -np.sin(a), 0], [np.sin(a), np.cos(a), 0], [0, 0, 1]])
    T[:3, 3] = [2, 12, -4]
    return T


@pytest.fixture(scope="module")
def setup():
    T = make_T()
    rng = np.random.default_rng(0)
    pts = rng.uniform([-40, -60, -20], [40, 40, 50], size=(50, 3))
    return affine_warp(T), T, pts


def test_to_patient_matches_affine(setup):
    warp, T, pts = setup
    expected = pts @ T[:3, :3].T + T[:3, 3]
    assert np.abs(warp.to_patient(pts) - expected).max() < 1e-3


def test_to_mni_inverts(setup):
    warp, T, pts = setup
    pat = pts @ T[:3, :3].T + T[:3, 3]
    mni, res = warp.invert(pat)
    assert np.abs(mni - pts).max() < 0.01
    assert res.max() < 0.01


def test_shapes(setup):
    warp, T, pts = setup
    assert warp.to_patient(pts[0]).shape == (3,)
    assert warp.to_patient(pts).shape == (50, 3)
    assert warp.to_mni(pts[0]).shape == (3,)
    assert warp.to_mni(pts).shape == (50, 3)
