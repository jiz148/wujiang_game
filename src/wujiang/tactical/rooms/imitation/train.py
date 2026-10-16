"""Command line for the spectator imitation picker.

    python -m wujiang.tactical.rooms.imitation.train inspect
    python -m wujiang.tactical.rooms.imitation.train train
    python -m wujiang.tactical.rooms.imitation.train predict --features 0.5,0.2,0.3,0.1,0.2,0.1,0.2,0.2,0.4,0,0,0.1,1,0.5
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from wujiang.tactical.rooms.imitation.dataset import FEATURE_COUNT
from wujiang.tactical.rooms.imitation.service import collect, predict, replay_dir, summarize, train, weights_path


def main() -> None:
    parser = argparse.ArgumentParser(description="查看、训练或使用观战模仿模型。")
    parser.add_argument("command", choices=("inspect", "train", "predict"))
    parser.add_argument("--replays", type=Path, default=None)
    parser.add_argument("--max-bytes", type=int, default=20_000_000)
    parser.add_argument("--epochs", type=int, default=40)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--weights", type=Path, default=None)
    parser.add_argument("--features", default="", help="逗号分隔的 14 个局面数字。")
    args = parser.parse_args()
    if args.command == "inspect":
        print(json.dumps(summarize(collect(args.replays, max_bytes=args.max_bytes)), ensure_ascii=False, indent=2))
        return
    if args.command == "train":
        report = train(args.replays, max_bytes=args.max_bytes, epochs=args.epochs, out=args.out)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    values = [float(item) for item in args.features.split(",") if item.strip()]
    if len(values) != FEATURE_COUNT:
        raise SystemExit(f"需要 {FEATURE_COUNT} 个数字，实际收到 {len(values)} 个。")
    from wujiang.tactical.rooms.imitation.service import load_picker
    chosen = predict(values, load_picker(args.weights or weights_path()))
    print(json.dumps(chosen, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
