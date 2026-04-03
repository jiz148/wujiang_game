from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wujiang.web.server import normalize_public_base_url, run_server  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Wujiang online test server.")
    parser.add_argument("--host", default="0.0.0.0", help="Host interface to bind. Default: 0.0.0.0")
    parser.add_argument("--port", type=int, default=8000, help="Port to listen on. Default: 8000")
    parser.add_argument(
        "--public-base-url",
        default="",
        help="Public homepage URL to share with friends, e.g. http://203.0.113.10:8000 or https://game.example.com",
    )
    args = parser.parse_args()
    try:
        args.public_base_url = normalize_public_base_url(args.public_base_url)
    except ValueError as exc:
        parser.error(str(exc))
    return args


if __name__ == "__main__":
    args = parse_args()
    run_server(host=args.host, port=args.port, public_base_url=args.public_base_url)
