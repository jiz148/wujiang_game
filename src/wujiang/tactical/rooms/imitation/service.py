"""Collect spectator replays, train the picker, and use a saved model.

Drop new replay JSON files into the replays directory and call these
functions again. The model only learns the action kind visible in a
spectator recording. It does not score a live legal-action list.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from wujiang.platform.http.runtime import PROJECT_ROOT
from wujiang.tactical.rooms.imitation.dataset import FEATURE_COUNT, LABELS, collect_examples
from wujiang.tactical.rooms.imitation.model import ActionPicker

DEFAULT_MAX_BYTES = 20_000_000


def replay_dir() -> Path:
    return PROJECT_ROOT / "replays"


def lab_weights_path() -> Path:
    return PROJECT_ROOT / "data" / "nn-lab" / "weights.npz"


def lab_ranker_path() -> Path:
    return PROJECT_ROOT / "data" / "nn-lab" / "ranker.npz"


def lab_hero_dir() -> Path:
    return PROJECT_ROOT / "data" / "nn-lab" / "heroes"


def weights_path() -> Path:
    return PROJECT_ROOT / "var" / "imitation" / "weights.npz"


def report_path() -> Path:
    return PROJECT_ROOT / "var" / "imitation" / "report.json"


def collect(directory: Path | None = None, *, max_bytes: int = DEFAULT_MAX_BYTES) -> list[tuple[str, list[float], str]]:
    return collect_examples(directory or replay_dir(), max_bytes=max_bytes)


def summarize(rows: list[tuple[str, list[float], str]]) -> dict[str, Any]:
    counts = {label: 0 for label in LABELS}
    sources: set[str] = set()
    for source, features, label in rows:
        if len(features) != FEATURE_COUNT:
            raise ValueError(f"{source} 的特征长度是 {len(features)}，应为 {FEATURE_COUNT}。")
        counts[label] = counts.get(label, 0) + 1
        sources.add(source)
    return {
        "replays": len(sources),
        "rows": len(rows),
        "labels": LABELS,
        "feature_count": FEATURE_COUNT,
        "counts": counts,
    }


def train(
    directory: Path | None = None,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    epochs: int = 40,
    holdout_fraction: float = 0.2,
    out: Path | None = None,
) -> dict[str, Any]:
    rows = collect(directory, max_bytes=max_bytes)
    if len(rows) < 20:
        raise ValueError(f"只有 {len(rows)} 条观战样本，不够训练。把录像放到 {replay_dir()} 后再试。")
    sources = sorted({source for source, _features, _label in rows})
    holdout = max(1, int(len(sources) * holdout_fraction))
    test_sources = set(sources[-holdout:])
    train_rows = [row for row in rows if row[0] not in test_sources]
    test_rows = [row for row in rows if row[0] in test_sources]
    train_x, train_y = _matrix(train_rows)
    test_x, test_y = _matrix(test_rows)
    picker = ActionPicker()
    losses = picker.fit(train_x, train_y, epochs=epochs)
    destination = out or weights_path()
    picker.save(destination)
    majority = int(np.bincount(train_y, minlength=len(LABELS)).argmax())
    report = {
        "weights": str(destination),
        "train_rows": len(train_rows),
        "test_rows": len(test_rows),
        "train_replays": len(sources) - len(test_sources),
        "test_replays": sorted(test_sources),
        "train_accuracy": _accuracy(picker, train_x, train_y),
        "test_accuracy": _accuracy(picker, test_x, test_y),
        "majority_baseline": _majority_accuracy(test_y, majority),
        "majority_label": LABELS[majority],
        "counts": summarize(train_rows)["counts"],
        "final_loss": round(losses[-1], 4) if losses else None,
    }
    report_path().parent.mkdir(parents=True, exist_ok=True)
    report_path().write_text(_json(report), encoding="utf-8")
    return report


def load_picker(path: Path | None = None) -> ActionPicker:
    return ActionPicker.load(path or weights_path())


def predict(features: list[float] | np.ndarray, picker: ActionPicker | None = None) -> dict[str, Any]:
    model = picker or load_picker()
    vector = np.asarray(features, dtype=np.float32).reshape(1, FEATURE_COUNT)
    probabilities = model.scores(vector)[0]
    chosen = int(probabilities.argmax())
    return {
        "label": LABELS[chosen],
        "probabilities": {LABELS[index]: round(float(probabilities[index]), 4) for index in range(len(LABELS))},
    }


def _matrix(rows: list[tuple[str, list[float], str]]) -> tuple[np.ndarray, np.ndarray]:
    features = np.array([features for _source, features, _label in rows], dtype=np.float32)
    labels = np.array([LABELS.index(label) for _source, _features, label in rows], dtype=np.int64)
    return features, labels


def _accuracy(picker: ActionPicker, features: np.ndarray, labels: np.ndarray) -> float:
    if len(labels) == 0:
        return 0.0
    return round(float((picker.predict(features) == labels).mean()), 3)


def _majority_accuracy(labels: np.ndarray, majority: int) -> float:
    if len(labels) == 0:
        return 0.0
    return round(float((labels == majority).mean()), 3)


def _json(payload: dict[str, Any]) -> str:
    import json
    return json.dumps(payload, ensure_ascii=False, indent=2)
