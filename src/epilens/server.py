"""Local web server for serving the page, anatomy, parsing, plans, import/export"""

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

import numpy as np
from flask import Flask, abort, jsonify, request
from werkzeug.exceptions import HTTPException
from werkzeug.serving import ThreadedWSGIServer

from . import __version__
from .atlas import Atlas
from .matcher import parse
from .plans import PlanStore, import_file, to_csv

log = logging.getLogger("epilens")
mimetypes.add_type("text/javascript", ".js") # Windows can report .js as text/plain, which breaks ES modules


def _b64(a: np.ndarray) -> str:
    return base64.b64encode(np.ascontiguousarray(a).tobytes()).decode()


@functools.cache
def anatomy_payload() -> bytes:
    atlas = Atlas.load()
    meshes = []
    with resources.as_file(resources.files("epilens") / "data" / "meshes.npz") as p:
        z = np.load(p)
        for n in z["names"].tolist():
            v, f = z[f"{n}/v"], z[f"{n}/f"]
            faces = f.astype(np.uint16) if len(v) < 65536 else f.astype(np.uint32)
            m = {"name": n, "color": str(z[f"{n}/c"]), "kind": "hull" if n == "Brain" else "deep",
                 "v": _b64(v.astype(np.float32)), "f": _b64(faces), "f32": faces.dtype == np.uint32}
            if f"{n}/s" in z:  # sulcal depth, for shading the cortex
                m["s"] = _b64(z[f"{n}/s"].astype(np.uint8))
            meshes.append(m)
    body = {"meshes": meshes, "labels": _b64(atlas.labels.astype(np.uint8)), "shape": list(atlas.labels.shape),
            "affine": atlas.affine.tolist(), "regions": [{"id": r.id, "name": r.name, "hemi": r.hemi, "base": r.base, "abbrev": r.abbrev, "kind": r.kind}
                        for r in atlas.regions],
            "version": __version__}
    return json.dumps(body, separators=(",", ":")).encode()


def _safe_filename(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip(" ._") or "plan"
    return f"{s[:60]}_{date.today().isoformat()}.csv"


def create_app(atlas: Atlas, store: PlanStore, on_ping, on_quit) -> Flask:
    app = Flask(__name__, static_folder="web", static_url_path="")

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
        r.headers["Cache-Control"] = "no-store" # always load the current page and scripts
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

    @app.get("/api/anatomy")
    def anatomy():
        return app.response_class(anatomy_payload(), mimetype="application/json")

    @app.get("/api/parse")
    def parse_text():
        return dataclasses.asdict(parse(request.args.get("q", ""), atlas, float(request.args.get("spacing") or 5.0)))

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
        return {"deleted": plan_id}

    @app.get("/api/export/<plan_id>")
    def export(plan_id):
        try:
            plan = store.load(plan_id)
        except FileNotFoundError:
            abort(404)
        return app.response_class(to_csv(plan, atlas), mimetype="text/csv", headers={
            "Content-Disposition": f'attachment; filename="{_safe_filename(plan.get("name", ""))}"'})

    @app.post("/api/import")
    def import_():
        plan, notes = import_file(request.get_data(), atlas, request.args.get("name"))   # CSV or .xlsx
        return {**store.save(plan), "notes": notes}

    @app.post("/api/quit")
    def quit_():
        log.info("Quit from the page.")
        on_quit()
        return {"stopped": True}

    return app


def create_server(port: int, store: PlanStore, idle: float | None = 120, atlas: Atlas | None = None):
    # serve_forever() returns on quit or when idle
    last_ping = None

    def on_ping():
        nonlocal last_ping
        last_ping = time.monotonic() # monotonic, so laptop sleep doesn't count as idle

    def stop_soon():
        threading.Thread(target=server.shutdown, daemon=True).start()

    def watch_idle(idle: float):
        while last_ping is None or time.monotonic() - last_ping <= idle:
            time.sleep(0.25)
        log.info("No browser for %ss; stopping.", idle)
        server.shutdown()

    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    server = ThreadedWSGIServer("127.0.0.1", port, create_app(atlas or Atlas.load(), store, on_ping, stop_soon))
    server.daemon_threads = False   # let Quit's reply finish sending before exit
    if idle:
        threading.Thread(target=watch_idle, args=(idle,), daemon=True).start()
    return server
