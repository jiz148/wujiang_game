"""Train the spectator-replay imitation picker and report a held-out score."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from wujiang.platform.http.runtime import PROJECT_ROOT
from wujiang.tactical.rooms.imitation.dataset import LABELS, iter_spectator_rows
from wujiang.tactical.rooms.imitation.model import save_model, train_model


def main() -> None:
    parser = argparse.ArgumentParser(description="Train an imitation picker from spectator replays.")
    parser.add_argument("--replays", type=Path, default=PROJECT_ROOT / "replays")
    parser.add_argument("--limit", type=int, default=40, help="How many of the largest replays to read.")
    parser.add_argument("--epochs", type=int, default=40)
    parser.add_argument("--out", type=Path, default=PROJECT_ROOT / "var" / "imitation" / "weights.npz")
    args = parser.parse_args()
    paths = sorted(args.replays.glob("*.json"), key=lambda path: path.stat().st_size, reverse=True)[: max(args.limit, 2)]
    rows = []
    for path in paths:
        try:
            rows.extend(iter_spectator_rows(path))
        except (OSError, json.JSONDecodeError, UnicodeError):
            continue
    if len(rows) < 20:
        raise SystemExit(f"只有 {len(rows)} 条观战样本，不够训练。")
    sources = sorted({row["source"] for row in rows})
    split = max(1, len(sources) // 5)
    test_sources = set(sources[-split:])
    train_rows = [row for row in rows if row["source"] not in test_sources]
    test_rows = [row for row in rows if row["source"] in test_sources]
    train_x, train_y = _matrix(train_rows)
    test_x, test_y = _matrix(test_rows)
    model = train_model(train_x, train_y, epochs=args.epochs)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    save_model(model, args.out)
    train_acc = float((model.predict(train_x) == train_y).mean())
    test_acc = float((model.predict(test_x) == test_y).mean()) if len(test_y) else 0.0
    majority = int(np.bincount(train_y, minlength=len(LABELS)).argmax())
    majority_acc = float((test_y == majority).mean()) if len(test_y) else 0.0
    counts = {LABELS[index]: int((train_y == index).sum()) for index in range(len(LABELS))}
    print(json.dumps({
        "replays": len(paths),
        "train_rows": len(train_rows),
        "test_rows": len(test_rows),
        "train_accuracy": round(train_acc, 3),
        "test_accuracy": round(test_acc, 3),
        "majority_baseline": round(majority_acc, 3),
        "majority_label": LABELS[majority],
        "train_counts": counts,
        "weights": str(args.out),
    }, ensure_ascii=False, indent=2))


def _matrix(rows: list[dict]) -> tuple[np.ndarray, np.ndarray]:
    features = np.array([row["features"] for row in rows], dtype=np.float64)
    labels = np.array([LABELS.index(row["label"]) for row in rows], dtype=np.int64)
    return features, labels


if __name__ == "__main__":
    main()
