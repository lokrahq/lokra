"""Build a standalone lokra for this machine, one that runs without Python installed.

The result is dist/lokra-<version>-<os>-<arch>.tar.gz (a .zip on Windows) holding a lokra folder with the
lokra program and everything it loads. The release workflow runs this on each platform; Homebrew and
lokra.dev/install.sh install these files.
"""
from __future__ import annotations

import platform
import shutil
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "build" / "standalone"
DIST = ROOT / "dist"
# Modules that are imported by name at run time, which PyInstaller cannot see by reading the code.
COLLECT = ["--collect-data", "lokra", "--collect-submodules", "sqlglot", "--collect-submodules", "uvicorn",
           "--collect-submodules", "anyio", "--collect-submodules", "psycopg", "--collect-all", "psycopg_binary",
           "--copy-metadata", "mcp"]


def target() -> str:
    system = {"darwin": "macos", "linux": "linux", "win32": "windows"}[sys.platform]
    arch = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x86_64", "amd64": "x86_64"}[platform.machine().lower()]
    return f"{system}-{arch}"


def main() -> int:
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True)
    entry = WORK / "lokra_main.py"
    entry.write_text("from lokra.cli import main\n\nraise SystemExit(main())\n", encoding="utf-8")
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--onedir", "--name", "lokra",
                    "--distpath", str(WORK / "dist"), "--workpath", str(WORK / "work"), "--specpath", str(WORK),
                    *COLLECT, str(entry)], check=True)
    folder = WORK / "dist" / "lokra"
    DIST.mkdir(exist_ok=True)
    name = f"lokra-{version}-{target()}"
    if sys.platform == "win32":
        archive = DIST / f"{name}.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
            for path in sorted(folder.rglob("*")):
                z.write(path, Path("lokra") / path.relative_to(folder))
    else:
        archive = DIST / f"{name}.tar.gz"
        with tarfile.open(archive, "w:gz") as t:
            t.add(folder, arcname="lokra")
    print(archive)
    return 0


if __name__ == "__main__":
    sys.exit(main())
