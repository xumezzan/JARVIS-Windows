"""Stdlib bootstrap after the PowerShell runtime check; works with Python -I."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from jarvis.installation.setup import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
