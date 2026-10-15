"""Spectator-replay imitation picker.

See docs/spectator-imitation.md.
"""
from wujiang.tactical.rooms.imitation.dataset import FEATURE_COUNT, LABELS
from wujiang.tactical.rooms.imitation.service import (
    collect,
    load_picker,
    predict,
    replay_dir,
    summarize,
    train,
    weights_path,
)

__all__ = [
    "FEATURE_COUNT",
    "LABELS",
    "collect",
    "load_picker",
    "predict",
    "replay_dir",
    "summarize",
    "train",
    "weights_path",
]
