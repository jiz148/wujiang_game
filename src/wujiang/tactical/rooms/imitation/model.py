"""A small softmax network over four observed action kinds."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from wujiang.tactical.rooms.imitation.dataset import FEATURE_COUNT, LABELS


class ActionPicker:
    def __init__(self, weights: dict[str, np.ndarray] | None = None) -> None:
        rng = np.random.default_rng(7)
        self.w1 = rng.normal(0, 0.15, size=(FEATURE_COUNT, 32)).astype(np.float32)
        self.b1 = np.zeros(32, dtype=np.float32)
        self.w2 = rng.normal(0, 0.15, size=(32, len(LABELS))).astype(np.float32)
        self.b2 = np.zeros(len(LABELS), dtype=np.float32)
        if weights:
            self.w1 = weights["w1"]
            self.b1 = weights["b1"]
            self.w2 = weights["w2"]
            self.b2 = weights["b2"]

    def scores(self, features: np.ndarray) -> np.ndarray:
        hidden = np.maximum(0.0, features @ self.w1 + self.b1)
        logits = hidden @ self.w2 + self.b2
        shifted = logits - logits.max(axis=1, keepdims=True)
        exp = np.exp(shifted)
        return exp / exp.sum(axis=1, keepdims=True)

    def predict(self, features: np.ndarray) -> np.ndarray:
        return self.scores(features).argmax(axis=1)

    def fit(self, features: np.ndarray, labels: np.ndarray, *, epochs: int = 40, lr: float = 0.08) -> list[float]:
        losses: list[float] = []
        eye = np.eye(len(LABELS), dtype=np.float32)
        targets = eye[labels]
        for _ in range(epochs):
            hidden_in = features @ self.w1 + self.b1
            hidden = np.maximum(0.0, hidden_in)
            logits = hidden @ self.w2 + self.b2
            shifted = logits - logits.max(axis=1, keepdims=True)
            exp = np.exp(shifted)
            probs = exp / exp.sum(axis=1, keepdims=True)
            loss = -np.log(np.clip(probs[np.arange(len(labels)), labels], 1e-8, 1.0)).mean()
            losses.append(float(loss))
            grad_logits = (probs - targets) / len(labels)
            grad_w2 = hidden.T @ grad_logits
            grad_b2 = grad_logits.sum(axis=0)
            grad_hidden = grad_logits @ self.w2.T
            grad_hidden[hidden_in <= 0] = 0
            grad_w1 = features.T @ grad_hidden
            grad_b1 = grad_hidden.sum(axis=0)
            self.w2 -= lr * grad_w2.astype(np.float32)
            self.b2 -= lr * grad_b2.astype(np.float32)
            self.w1 -= lr * grad_w1.astype(np.float32)
            self.b1 -= lr * grad_b1.astype(np.float32)
        return losses

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez(path, w1=self.w1, b1=self.b1, w2=self.w2, b2=self.b2, labels=np.array(LABELS))

    @classmethod
    def load(cls, path: Path) -> "ActionPicker":
        blob = np.load(path)
        return cls({"w1": blob["w1"], "b1": blob["b1"], "w2": blob["w2"], "b2": blob["b2"]})
