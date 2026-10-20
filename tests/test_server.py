import base64
import http.client
import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from epilens import cli
from epilens.plans import PlanStore, new_plan
from epilens.server import create_server


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
    # Laptop sleep makes the wall clock jump; the server shouldn't count that as idle
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
    # DNS rebinding: a foreign Host name can't read plans
    assert raw(port, "GET", "/api/plans", {"Host": f"evil.example:{port}"})[0] == 403
    # a page on another site can't stop epilens or plant a plan
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
