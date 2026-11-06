import base64
import http.client
import json
import threading
import time
import urllib.error
import urllib.request

import numpy as np
import pytest

from epilens import cli
from epilens.plans import PlanStore, new_plan
from epilens.patient import FILE_NAMES
from epilens.server import create_server
from legui_fake import make_legui
from test_plans import electrode


@pytest.fixture
def server(tmp_path):
    httpd = create_server(0, PlanStore(tmp_path), idle=None)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", httpd, tmp_path
    httpd.shutdown()
    httpd.server_close()


def call(url, method="GET", body=None, ctype="application/json"):
    data = body if body is None or isinstance(body, bytes) else (
        json.dumps(body).encode() if ctype == "application/json" else body.encode())
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": ctype})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def test_anatomy(server):
    url, *_ = server
    code, _, body = call(f"{url}/api/anatomy")
    a = json.loads(body)
    assert code == 200 and len(base64.b64decode(a["labels"])) == 91 * 109 * 91 and a["shape"] == [91, 109, 91]
    assert "Brain" in [m["name"] for m in a["meshes"]] and len(a["affine"]) == 4
    assert a["regions"][0] == {"id": 2, "name": "Left Frontal Pole", "hemi": "L", "base": "Frontal Pole", "abbrev": "FP",
                               "kind": "cortex"}


def test_t1(server):
    # The template T1 for 2D sections on 1 mm voxels. Tests that white matter brighter than cortex and cortex brighter than CSF
    url, *_ = server
    code, _, body = call(f"{url}/api/t1")
    t = json.loads(body)
    v = np.frombuffer(base64.b64decode(t["data"]), np.uint8).reshape(t["shape"])
    to_voxel = np.linalg.inv(np.array(t["affine"]))
    at = lambda xyz: int(v[tuple(np.rint(to_voxel @ [*xyz, 1])[:3].astype(int))])
    assert code == 200 and np.allclose(np.diag(t["affine"])[:3], 1)
    assert at([28, -20, 30]) > at([55, -20, 10]) > at([0, 0, 0])   # white matter, stg, 3rd ventricle


def test_parse(server):
    url, *_ = server
    code, _, body = call(f"{url}/api/parse?q=RHH&spacing=3.5")
    p = json.loads(body)
    assert code == 200 and p["ok"] and p["name"] == p["entry"]["abbrev"] == "RMTG" and p["entry"]["mode"] == "lateral"


def test_plans_crud(server):
    url, _, root = server
    plan = new_plan("Pt 1")
    code, _, body = call(f"{url}/api/plans/{plan['id']}", "PUT", plan)
    assert code == 200 and json.loads(body)["updated"]
    assert (root / f"{plan['id']}.json").exists()
    code, _, body = call(f"{url}/api/plans")
    assert [p["name"] for p in json.loads(body)] == ["Pt 1"]
    code, _, _ = call(f"{url}/api/plans/{plan['id']}", "DELETE")
    assert code == 200 and not (root / f"{plan['id']}.json").exists()
    code, _, body = call(f"{url}/api/plans/..%2Fx", "PUT", plan)
    assert code >= 400 and not (root.parent / "x.json").exists()


def test_export_import(server):
    url, *_ = server
    _, _, body = call(f"{url}/api/parse?q=STG%20RHH")
    p = json.loads(body)
    plan = new_plan("Pt, 2")
    plan["electrodes"] = [{"id": "e1", "name": p["name"], "custom_name": False, "n_contacts": 4, "spacing_mm": None,
                           "target": p["target"], "entry": p["entry"]}]
    assert call(f"{url}/api/plans/{plan['id']}", "PUT", plan)[0] == 200
    code, headers, body = call(f"{url}/api/export/{plan['id']}")
    text = body.decode()
    assert code == 200 and text.startswith("Electrode,Contact,MNI X") and "attachment" in headers["Content-Disposition"]
    assert ".csv" in headers["Content-Disposition"] and "/" not in headers["Content-Disposition"].split("filename=")[1]
    code, _, body = call(f"{url}/api/import", "POST", text, ctype="text/csv")
    back = json.loads(body)
    assert code == 200 and back["electrodes"][0]["name"] == "RSTG" and back["id"] != plan["id"]
    assert call(f"{url}/api/export/{'0' * 32}")[0] == 404
    code, _, body = call(f"{url}/api/import", "POST", "nope", ctype="text/csv")
    assert code == 400 and "epilens CSV" in json.loads(body)["error"]


def test_import_xlsx(server):
    from xlsx_util import contact_sheet, make_xlsx
    url, *_ = server
    code, _, body = call(f"{url}/api/import?name=pt", "POST", make_xlsx(contact_sheet()), ctype="application/octet-stream")
    back = json.loads(body)
    assert code == 200 and back["name"] == "pt" and len(back["electrodes"]) == 3 and len(back["notes"]) == 3


def test_no_path_traversal(server):
    url, httpd, _ = server
    conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1])
    conn.request("GET", "/../pyproject.toml")
    assert conn.getresponse().status == 404


def test_second_launch_reuses_server(server):
    url, httpd, _ = server
    t0 = time.time()
    assert cli.main(["--port", str(httpd.server_address[1])]) == 0
    assert time.time() - t0 < 5
    code, _, body = call(f"{url}/api/ping")
    assert code == 200 and json.loads(body)["app"] == "epilens"


def test_idle_shutdown(tmp_path):
    httpd = create_server(0, PlanStore(tmp_path), idle=1)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    call(f"http://127.0.0.1:{httpd.server_address[1]}/api/ping")
    t.join(timeout=4)
    assert not t.is_alive()
    httpd.server_close()


def test_quit(tmp_path):
    httpd = create_server(0, PlanStore(tmp_path), idle=None)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    code, _, _ = call(f"http://127.0.0.1:{httpd.server_address[1]}/api/quit", "POST", {})
    t.join(timeout=3)
    assert code == 200 and not t.is_alive()
    httpd.server_close()


def test_idle_survives_wall_clock_jump(tmp_path, monkeypatch):
    # When a laptop sleeps the wall clock jumps forward. The server should not count that time as idle
    import epilens.server as srv
    httpd = create_server(0, PlanStore(tmp_path), idle=5)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    call(f"http://127.0.0.1:{httpd.server_address[1]}/api/ping")
    real = time.time
    monkeypatch.setattr(srv.time, "time", lambda: real() + 10_000)
    t.join(timeout=1.5)
    alive = t.is_alive()
    httpd.shutdown()
    httpd.server_close()
    assert alive


def raw(port, method, path, headers, body=b""):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request(method, path, body=body, headers=headers)
    r = conn.getresponse()
    return r.status, r.read()


def test_other_websites_are_refused(server):
    url, httpd, _ = server
    port = httpd.server_address[1]
    # Guard against DNS rebinding. A request with another Host name can't read plans
    assert raw(port, "GET", "/api/plans", {"Host": f"evil.example:{port}"})[0] == 403
    # a page on another site can't stop epilens or add a plan
    assert raw(port, "POST", "/api/quit", {"Host": f"127.0.0.1:{port}", "Origin": "https://evil.example",
                                           "Content-Type": "text/plain"}, b"{}")[0] == 403
    assert raw(port, "POST", "/api/import", {"Host": f"localhost:{port}", "Origin": "null",
                                             "Content-Type": "text/plain"}, b"x")[0] == 403
    # our own page is fine
    assert raw(port, "GET", "/api/ping", {"Host": f"localhost:{port}"})[0] == 200
    plan = new_plan("ok")
    status, _ = raw(port, "PUT", f"/api/plans/{plan['id']}", {"Host": f"localhost:{port}",
                    "Origin": f"http://localhost:{port}", "Content-Type": "application/json"}, json.dumps(plan).encode())
    assert status == 200


# Patient brain
def _T():
    a = np.radians(5)
    T = np.eye(4)
    T[:3, :3] = 0.95 * np.array([[np.cos(a), -np.sin(a), 0], [np.sin(a), np.cos(a), 0], [0, 0, 1]])
    T[:3, 3] = [2, 12, -4]
    return T


@pytest.fixture(scope="module")
def legui():
    return make_legui(_T())


def _mni_plan(url):
    from epilens.atlas import Atlas
    plan = new_plan("Pt")
    plan["electrodes"] = [electrode(Atlas.load(), "STG RHH", 6)]
    assert call(f"{url}/api/plans/{plan['id']}", "PUT", plan)[0] == 200
    return plan


def _upload(url, plan_id, files, skip=()):
    for k, data in files.items():
        if k not in skip:
            code, _, body = call(f"{url}/api/plans/{plan_id}/patient/files/{FILE_NAMES[k]}", "POST", data,
                                 ctype="application/octet-stream")
            assert code == 200 and json.loads(body) == {"received": k}


def _attach(url, legui):
    plan = _mni_plan(url)
    _upload(url, plan["id"], legui)
    code, _, body = call(f"{url}/api/plans/{plan['id']}/patient", "POST", b"")
    assert code == 200, body
    return plan, json.loads(body)


def _restart(root):
    httpd = create_server(0, PlanStore(root), idle=None)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{httpd.server_address[1]}", httpd


def test_attach_patient(server, legui):
    url, _, root = server
    plan, out = _attach(url, legui)
    assert out["plan"]["space"] == "patient" and isinstance(out["warnings"], list)
    assert json.loads((root / f"{plan['id']}.json").read_text())["space"] == "patient"
    a = json.loads(call(f"{url}/api/anatomy?plan={plan['id']}")[2])
    assert a["space"] == "patient" and "MR" in a["volumes"] and not a.get("missing")
    assert json.loads(call(f"{url}/api/anatomy")[2])["space"] == "mni"
    # a second attach on a patient plan only loads imaging
    _upload(url, plan["id"], legui)
    code, _, body = call(f"{url}/api/plans/{plan['id']}/patient", "POST", b"")
    assert code == 200 and json.loads(body)["plan"]["electrodes"] == out["plan"]["electrodes"]


def test_attach_missing_file(server, legui):
    url, _, root = server
    plan = _mni_plan(url)
    before = (root / f"{plan['id']}.json").read_text()
    _upload(url, plan["id"], legui, skip=("y_mr",))
    code, _, body = call(f"{url}/api/plans/{plan['id']}/patient", "POST", b"")
    assert code == 400 and "y_MR" in json.loads(body)["error"]
    assert (root / f"{plan['id']}.json").read_text() == before
    assert json.loads(call(f"{url}/api/anatomy?plan={plan['id']}")[2])["space"] == "mni"
    # uploads are dropped after the failed attach: a retry with only y_MR still lacks the rest
    _upload(url, plan["id"], legui, skip=tuple(k for k in legui if k != "y_mr"))
    code, _, body = call(f"{url}/api/plans/{plan['id']}/patient", "POST", b"")
    assert code == 400 and "MR.nii" in json.loads(body)["error"] and "y_MR" not in json.loads(body)["error"]
    assert call(f"{url}/api/plans/{'0' * 32}/patient", "POST", b"")[0] == 404
    assert call(f"{url}/api/plans/{plan['id']}/patient/files/notes.txt", "POST", b"x")[0] == 400


def test_restart_shows_missing(server, legui):
    url, httpd, root = server
    plan, _ = _attach(url, legui)
    url2, httpd2 = _restart(root)
    try:
        a = json.loads(call(f"{url2}/api/anatomy?plan={plan['id']}")[2])
        assert a["space"] == "patient" and a["missing"] is True and a["meshes"] == [] and a["shape"] == [1, 1, 1]
        assert base64.b64decode(a["labels"]) == b"\0" and a["regions"] and a["volumes"] == {}
        code, _, body = call(f"{url2}/api/export/{plan['id']}")
        assert code == 200 and "X (AC-PC)" in body.decode().splitlines()[0]
    finally:
        httpd2.shutdown()
        httpd2.server_close()


def test_parse_uses_plan_atlas(server, legui):
    url, *_ = server
    plan, _ = _attach(url, legui)
    p = json.loads(call(f"{url}/api/parse?q=LHH&plan={plan['id']}")[2])
    t = json.loads(call(f"{url}/api/parse?q=LHH")[2])
    assert p["ok"] and not np.allclose(p["target"]["xyz"], t["target"]["xyz"], atol=1)
    from epilens.patient import load_patient
    from epilens.atlas import Atlas
    from epilens.matcher import parse
    pat = load_patient(legui, Atlas.load())
    assert np.allclose(p["target"]["xyz"], parse("LHH", pat.atlas).target.xyz)


def test_patient_upload_foreign_origin(server):
    url, *_ = server
    req = urllib.request.Request(f"{url}/api/plans/{'0' * 32}/patient/files/MR.nii", data=b"x", method="POST",
                                 headers={"Origin": "http://evil.example"})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req)
    assert e.value.code == 403


def test_no_files_written(server, legui):
    url, _, root = server
    _attach(url, legui)
    assert {p.suffix for p in root.rglob("*") if p.is_file()} == {".json"}
    assert not [p for p in root.rglob("*") if p.is_dir()]
