from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wujiang.tools.all_hero_deep_audit import main  # noqa: E402


if __name__ == "__main__":
    os.chdir(ROOT)
    raise SystemExit(main())
