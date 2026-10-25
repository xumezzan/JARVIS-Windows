"""Repair recurring macOS hidden flags only in this checkout's isolated environment."""

import site
import subprocess
import sys
from pathlib import Path

STARTUP = """# Jarvis local venv metadata repair; no global Python or application policy changes.
import os
import site
import stat
import sys
from pathlib import Path

if sys.platform == "darwin":
    _root = Path(__file__).resolve().parent
    _pth = _root / "_editable_impl_jarvis_windows.pth"
    _paths = [_pth, *(_root / "PySide6/Qt/plugins/platforms").glob("*.dylib")]
    for _path in _paths:
        if _path.is_symlink() or not _path.is_file():
            continue
        _flags = _path.stat().st_flags
        if _flags & stat.UF_HIDDEN:
            os.chflags(_path, _flags & ~stat.UF_HIDDEN)
            if _path == _pth:
                # site processes .pth before sitecustomize, so replay only this repaired
                # trusted editable-install file; never add cwd, env or user-site paths.
                site.addpackage(str(_root), _pth.name, None)
"""


def main() -> None:
    expected = Path(__file__).resolve().parents[1] / ".venv"
    if sys.platform != "darwin" or Path(sys.prefix).resolve() != expected:
        raise SystemExit("Run with this checkout's .venv/bin/python on macOS.")
    package_root = next(
        Path(path) for path in site.getsitepackages() if Path(path).is_relative_to(expected)
    )
    target = package_root / "sitecustomize.py"
    if target.exists() and target.read_text() != STARTUP:
        raise SystemExit("Existing sitecustomize.py preserved; refusing to overwrite it.")
    target.write_text(STARTUP)
    subprocess.run([sys.executable, "-I", "-c", "import jarvis"], check=True)
    print("Installed local venv metadata repair; editable import and Qt plugins stay discoverable.")


if __name__ == "__main__":
    main()
