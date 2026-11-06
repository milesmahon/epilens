"""Local web server. serves the page, the anatomy and the plans, parses text, and imports and exports files."""

import base64
import dataclasses
import functools
import json
import logging
import mimetypes
import re
import threading
import time
from datetime import date
from importlib import resources
from types import SimpleNamespace

import numpy as np
from flask import Flask, abort, jsonify, request
from werkzeug.exceptions import HTTPException
from werkzeug.serving import ThreadedWSGIServer

from . import __version__
from .atlas import Atlas
from .matcher import parse
from .patient import MNI_CENTER, Patient, PatientError, anatomy_json, file_key, load_patient, template_meshes
from .plans import PlanStore, convert_plan, import_file, space_of, to_csv

log = logging.getLogger("epilens")
mimetypes.add_type("text/javascript", ".js")  # Windows can report .js as text/plain, which breaks ES modules
mimetypes.add_type("font/woff2", ".woff2")


@functools.cache
def anatomy_payload() -> bytes:
    return anatomy_json(template_meshes(), Atlas.load(), space="mni", center=list(MNI_CENTER), volumes={}, landmarks={})


@functools.cache
def t1_payload() -> bytes:
    """The template T1 for the 2D sections is separate from the anatomy so it is only sent when sections are opened"""
    with resources.as_file(resources.files("epilens") / "data" / "t1.npz") as p:
        z = np.load(p)
        t1, affine = z["t1"], z["affine"]
    body = {"shape": list(t1.shape), "affine": affine.tolist(), "data": base64.b64encode(np.ascontiguousarray(t1).tobytes()).decode()}
    return json.dumps(body, separators=(",", ":")).encode()


def _safe_filename(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip(" ._") or "plan"
    return f"{s[:60]}_{date.today().isoformat()}.csv"


def _missing_payload(atlas: Atlas) -> bytes:
    # A patient plan whose imaging has not been loaded since the server started, show an empty one-voxel volume
    stub = SimpleNamespace(labels=np.zeros((1, 1, 1), np.uint8), affine=np.eye(4), regions=atlas.regions)
    return anatomy_json((), stub, space="patient", missing=True, center=[0, 0, 0], volumes={}, landmarks={})


def create_app(atlas: Atlas, store: PlanStore, on_ping, on_quit) -> Flask:
    app = Flask(__name__, static_folder="web", static_url_path="")
    patients: dict[str, Patient] = {}           # plan id -> patient brain, in memory only
    uploads: dict[str, dict[str, bytes]] = {}   # plan id -> files the page handed over (same computer), until attached

    @app.before_request
    def only_our_page():
        # Block requests from other websites
        port = request.environ["SERVER_PORT"]
        ours = {f"localhost:{port}", f"127.0.0.1:{port}"}
        origin = request.headers.get("Origin")
        if request.host not in ours or (request.method != "GET" and origin and origin not in {f"http://{h}" for h in ours}):
            abort(403)

    @app.after_request
    def no_cache(r):
        r.headers["Cache-Control"] = "no-store"  # always load the current page and scripts
        return r

    @app.errorhandler(Exception)
    def error(e: Exception):
        if isinstance(e, HTTPException):
            return jsonify(error=e.description), e.code or 500
        if isinstance(e, (KeyError, ValueError, TypeError)):
            return jsonify(error=str(e)), 400
        log.exception("request failed: %s %s", request.method, request.path)
        return jsonify(error=f"{type(e).__name__}: {e}"), 500

    @app.get("/")
    def index():
        return app.send_static_file("index.html")

    @app.get("/api/ping")
    def ping():
        on_ping()
        return {"app": "epilens", "version": __version__}

    def stored(plan_id: str) -> dict:
        try:
            return store.load(plan_id)
        except FileNotFoundError:
            abort(404)

    @app.get("/api/anatomy")
    def anatomy():
        plan_id = request.args.get("plan")
        if not plan_id or space_of(stored(plan_id)) != "patient":
            return app.response_class(anatomy_payload(), mimetype="application/json")
        p = patients.get(plan_id)
        return app.response_class(p.payload if p else _missing_payload(atlas), mimetype="application/json")

    @app.get("/api/t1")
    def t1():
        return app.response_class(t1_payload(), mimetype="application/json")

    @app.get("/api/parse")
    def parse_text():
        plan_id = request.args.get("plan")
        use, space = atlas, "mni"
        if plan_id and space_of(stored(plan_id)) == "patient":
            if plan_id not in patients:
                raise ValueError("The patient's imaging isn't loaded.")
            use, space = patients[plan_id].atlas, "patient"
        return dataclasses.asdict(parse(request.args.get("q", ""), use, float(request.args.get("spacing") or 5.0), space))

    @app.post("/api/plans/<plan_id>/patient/files/<name>")
    def patient_file(plan_id, name):
        stored(plan_id)
        key = file_key(name)
        if key is None:
            raise ValueError("not a patient file")
        if request.args.get("first"):   # a new attempt: forget files from one that was abandoned
            uploads.pop(plan_id, None)
        uploads.setdefault(plan_id, {})[key] = request.get_data(cache=False)   # kept in memory, never written
        return {"received": key}

    @app.post("/api/plans/<plan_id>/patient")
    def attach_patient(plan_id):
        plan = stored(plan_id)
        files = uploads.pop(plan_id, {})   # released whether or not loading works; the page re-sends every file
        patient = load_patient(files, atlas)   # PatientError -> 400, nothing changed
        warnings = list(patient.warnings)
        if space_of(plan) != "patient":
            plan, more = convert_plan(plan, patient.warp.to_patient, patient.atlas, "patient")
            plan = store.save(plan)
            warnings += more
        patients[plan_id] = patient
        return {"plan": plan, "warnings": warnings}

    @app.get("/api/plans")
    def list_plans():
        return store.list()

    @app.put("/api/plans/<plan_id>")
    def save_plan(plan_id):
        plan = json.loads(request.get_data())
        if plan.get("id") != plan_id:
            raise ValueError("plan id does not match the URL")
        return store.save(plan)

    @app.delete("/api/plans/<plan_id>")
    def delete_plan(plan_id):
        store.delete(plan_id)
        patients.pop(plan_id, None)   # delete the imaging along with the plan
        uploads.pop(plan_id, None)
        return {"deleted": plan_id}

    @app.get("/api/export/<plan_id>")
    def export(plan_id):
        try:
            plan = store.load(plan_id)
        except FileNotFoundError:
            abort(404)
        pat = patients.get(plan_id)
        if space_of(plan) == "patient":
            csv_text = to_csv(plan, pat.atlas if pat else None, pat.warp if pat else None)
        else:
            csv_text = to_csv(plan, atlas)
        return app.response_class(csv_text, mimetype="text/csv", headers={
            "Content-Disposition": f'attachment; filename="{_safe_filename(plan.get("name", ""))}"'})

    @app.post("/api/import")
    def import_():
        plan, notes = import_file(request.get_data(), atlas, request.args.get("name"))   # a CSV or .xlsx file
        return {**store.save(plan), "notes": notes}

    @app.post("/api/quit")
    def quit_():
        log.info("Quit from the page.")
        on_quit()
        return {"stopped": True}

    return app


def create_server(port: int, store: PlanStore, idle: float | None = 120, atlas: Atlas | None = None):
    # serve_forever() returns when the user quits or the server has been idle too long
    last_ping = None

    def on_ping():
        nonlocal last_ping
        last_ping = time.monotonic()  # monotonic, so time asleep on a laptop does not count as idle

    def stop_soon():
        threading.Thread(target=server.shutdown, daemon=True).start()

    def watch_idle(idle: float):
        while last_ping is None or time.monotonic() - last_ping <= idle:
            time.sleep(0.25)
        log.info("No browser for %ss; stopping.", idle)
        server.shutdown()

    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    server = ThreadedWSGIServer("127.0.0.1", port, create_app(atlas or Atlas.load(), store, on_ping, stop_soon))
    server.daemon_threads = False   # let the reply to Quit finish sending before the server exits
    if idle:
        threading.Thread(target=watch_idle, args=(idle,), daemon=True).start()
    return server
