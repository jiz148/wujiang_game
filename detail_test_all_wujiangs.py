from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wujiang.tools.all_hero_deep_audit import main  # noqa: E402


if __name__ == "__main__":
    os.chdir(ROOT)
    # Some terminals cannot encode a hero's Chinese name. Preserve the audit
    # and its file logs even when console output needs escaped characters.
    sys.stdout.reconfigure(errors="backslashreplace")
    sys.stderr.reconfigure(errors="backslashreplace")
    raise SystemExit(main(sys.argv[1:]))
