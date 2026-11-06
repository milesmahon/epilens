import base64
import gzip
import io
import json

import nibabel as nib
import numpy as np
import pytest
import scipy.io

from epilens.matcher import parse
from epilens.patient import PatientError, file_key, load_patient
from legui_fake import affine_warp, make_legui


def make_T(shift=(0, 0, 0)):
    a = np.radians(5)
    T = np.eye(4)
    T[:3, :3] = 0.95 * np.array([[np.cos(a), -np.sin(a), 0], [np.sin(a), np.cos(a), 0], [0, 0, 1]])
    T[:3, 3] = np.array([2, 12, -4]) + shift
    return T


@pytest.fixture(scope="module")
def files():
    f = make_legui(make_T())
    f["mr"] = gzip.compress(f["mr"], compresslevel=1)  # as if dropped as MR.nii.gz
    return f


@pytest.fixture(scope="module")
def patient(files, atlas):
    return load_patient(files, atlas)


def test_file_key():
    assert file_key("MR.nii") == "mr"
    assert file_key("mr.nii.gz") == "mr"
    assert file_key("y_MR.nii.gz") == "y_mr"
    assert file_key("Surfaces.mat") == "surfaces"
    assert file_key("ACPCmm.MAT") == "acpcmm"
    assert file_key("iy_MR.nii") is None
    assert file_key("lwHO_cort.nii") is None


def test_labels_follow_warp(atlas, patient):
    T = make_T()
    ijk = np.argwhere(atlas.labels >= 2)
    rng = np.random.default_rng(0)
    pts = ijk[rng.choice(len(ijk), 200, replace=False)] @ atlas.affine[:3, :3].T + atlas.affine[:3, 3]
    same = [patient.atlas.label_at(T[:3, :3] @ p + T[:3, 3]) == atlas.label_at(p) for p in pts]
    assert np.mean(same) >= 0.9


def test_matcher_on_patient(patient):
    p = parse("LHH", patient.atlas)
    assert p.ok and patient.atlas.label_at(p.target.xyz) == "Left Hippocampus"


def test_payload(patient):
    a = json.loads(patient.payload)
    assert a["space"] == "patient" and set(a["volumes"]) == {"MR", "CT"}
    assert len(base64.b64decode(a["volumes"]["MR"])) == np.prod(a["shape"]) == patient.atlas.labels.size
    assert len(base64.b64decode(a["labels"])) == np.prod(a["shape"])
    brain = next(m for m in a["meshes"] if m["name"] == "Brain")
    n = len(base64.b64decode(brain["v"])) // 12
    faces = np.frombuffer(base64.b64decode(brain["f"]), np.uint32 if brain["f32"] else np.uint16)
    assert "s" in brain and len(base64.b64decode(brain["s"])) == n and faces.max() < n
    assert len(a["meshes"]) > 1 and all(m["kind"] == "deep" for m in a["meshes"] if m["name"] != "Brain")
    assert np.allclose(a["landmarks"]["AC"], (0, 12.65, 0)) and np.allclose(a["landmarks"]["PC"], (0, -12.65, 0))
    assert np.allclose(a["center"], make_T()[:3, :3] @ (0, -18, 8) + make_T()[:3, 3], atol=0.01)
    assert patient.warnings == []


def test_missing_files(files, atlas):
    partial = {k: v for k, v in files.items() if k not in ("y_mr", "mrwhite")}
    with pytest.raises(PatientError, match="Missing patient files: y_MR.nii, MRWhite.nii"):
        load_patient(partial, atlas)


def test_surface_mismatch(atlas):
    with pytest.raises(PatientError, match="surface doesn't match MR"):
        load_patient(make_legui(make_T(), surface_shift=(40, 0, 0), with_ct=False), atlas)


def test_warp_mismatch(files, atlas):
    w = affine_warp(make_T(shift=(40, 0, 0)))  # another subject's warp
    y_mr = nib.Nifti1Image(w.field.reshape(*w.field.shape[:3], 1, 3), w.affine).to_bytes()
    with pytest.raises(PatientError, match="warp doesn't match MR"):
        load_patient({**files, "y_mr": y_mr}, atlas)


def test_no_ct_or_acpc(files, atlas):
    p = load_patient({k: v for k, v in files.items() if k not in ("ct", "acpcmm")}, atlas)
    a = json.loads(p.payload)
    assert list(a["volumes"]) == ["MR"] and a["landmarks"] == {}


@pytest.mark.parametrize("key, name", [("mrgray", "MRGray.nii"), ("y_mr", "y_MR.nii"), ("surfaces", "Surfaces.mat"),
                                       ("acpcmm", "ACPCmm.mat")])
def test_junk_file(files, atlas, key, name):
    with pytest.raises(PatientError, match=f"^{name}: "):
        load_patient({**files, key: b"not a real file"}, atlas)


def test_zero_based_faces(files, atlas):
    s = scipy.io.loadmat(io.BytesIO(files["surfaces"]), simplify_cells=True)
    s["BrainSurfRaw"]["faces"] -= 1
    out = io.BytesIO()
    scipy.io.savemat(out, {k: s[k] for k in ("BrainSurfRaw", "ProjSurfRaw")})
    with pytest.raises(PatientError, match="faces should be vertex numbers from 1"):
        load_patient({**files, "surfaces": out.getvalue()}, atlas)


def _shifted(data: bytes) -> bytes:
    img = nib.Nifti1Image.from_bytes(gzip.decompress(data) if data[:2] == b"\x1f\x8b" else data)
    affine = img.affine.copy()
    affine[0, 3] += 10
    return nib.Nifti1Image(np.asarray(img.dataobj), affine).to_bytes()


def test_mr_mismatch(files, atlas):
    with pytest.raises(PatientError, match="MR.nii doesn't match MRGray.nii/MRWhite.nii"):
        load_patient({**files, "mr": _shifted(files["mr"])}, atlas)


def test_ct_mismatch(files, atlas):
    with pytest.raises(PatientError, match="CT.nii doesn't match MR.nii"):
        load_patient({**files, "ct": _shifted(files["ct"])}, atlas)
