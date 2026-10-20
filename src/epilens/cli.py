import argparse
import json
import logging
import urllib.request

from .plans import DEFAULT_ROOT, PlanStore
from .server import create_server

DEFAULT_PORT = 8737
log = logging.getLogger("epilens")


def _running(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=1) as r:
            return json.loads(r.read()).get("app") == "epilens"
    except (OSError, ValueError):
        return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="epilens", description="sEEG implantation planner on a 3D brain")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--idle", type=float, default=120, help="stop this many seconds after the page closes (0 = never)")
    ap.add_argument("--plans", default=str(DEFAULT_ROOT), help="folder where plans are saved")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.port and _running(args.port):
        url = f"http://localhost:{args.port}/"
        print(f"epilens is already running: {url}", flush=True)
        return 0
    try:
        httpd = create_server(args.port, PlanStore(args.plans), idle=args.idle or None)
    except OSError:  # port taken by something else
        httpd = create_server(0, PlanStore(args.plans), idle=args.idle or None)
    url = f"http://localhost:{httpd.server_address[1]}/"
    print(f"epilens: {url}  (press Ctrl-C or Quit in the menu to stop)", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
