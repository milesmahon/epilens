"""Build the epilens server as a standalone folder for the desktop app.

    uv run python scripts/build_sidecar.py

PyInstaller writes src-tauri/binaries/epilens-server/ (the epilens-server program plus its _internal/ folder).
Tauri ships that folder as a resource and main.rs starts the program from it
"""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BINARIES_DIR = ROOT / "src-tauri" / "binaries"
NAME = "epilens-server"


def main():
    out = BINARIES_DIR / NAME
    shutil.rmtree(out, ignore_errors=True)  # also clears the dev stand-in

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--name",
        NAME,
        "--distpath",
        str(BINARIES_DIR),
        "--collect-data",
        "epilens",
        "--collect-submodules",
        "epilens",
        "--collect-submodules",
        "scipy",
        "--collect-submodules",
        "nibabel",
        "--collect-submodules",
        "h5py",
        "--collect-submodules",
        "openpyxl",
        "--hidden-import",
        "scipy.special",
        "--hidden-import",
        "scipy.spatial.transform.rotation",
        "--hidden-import",
        "skimage.measure",
        "--hidden-import",
        "flask",
        "--hidden-import",
        "werkzeug",
        str(ROOT / "src" / "epilens" / "__main__.py"),
    ]
    print("==> Running PyInstaller...")
    subprocess.check_call(cmd, cwd=str(ROOT))

    # remove PyInstaller's work files
    shutil.rmtree(ROOT / "build", ignore_errors=True)
    (ROOT / f"{NAME}.spec").unlink(missing_ok=True)

    print(f"\n[OK] Built server folder: {out}")


if __name__ == "__main__":
    main()
