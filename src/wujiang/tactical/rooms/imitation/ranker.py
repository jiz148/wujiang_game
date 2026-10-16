"""Score each already-legal action and learn which one was actually taken.

The input is the situation plus one candidate, such as move or skill:pierce.
Training uses the decision records under var/nn-lab. Weights stay there too.
The live battle still supplies the legal list; this network only ranks it.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from wujiang.tactical.rooms.imitation.visible import INPUT_DIM, pair_features

SITUATION_DIM = INPUT_DIM
HIDDEN = 64


class LegalRanker:
    def __init__(self) -> None:
        rng = np.random.default_rng(11)
        self.w1 = rng.normal(0, 0.12, size=(INPUT_DIM, HIDDEN)).astype(np.float32)
        self.b1 = np.zeros(HIDDEN, dtype=np.float32)
        self.w2 = rng.normal(0, 0.12, size=(HIDDEN, 1)).astype(np.float32)
        self.b2 = np.zeros(1, dtype=np.float32)

    def score_candidates(self, situation: dict[str, Any], candidates: list[dict[str, Any]]) -> np.ndarray:
        if not candidates:
            return np.zeros(0, dtype=np.float32)
        rows = np.stack([pair_features(situation, candidate) for candidate in candidates])
        return self._logits(rows)[:, 0]

    def fit(
        self,
        groups: list[tuple[dict[str, Any], list[dict[str, Any]], int]],
        *,
        epochs: int = 80,
        lr: float = 0.05,
        skill_weight: float = 4.0,
    ) -> list[float]:
        losses: list[float] = []
        for _ in range(epochs):
            grad_w1 = np.zeros_like(self.w1)
            grad_b1 = np.zeros_like(self.b1)
            grad_w2 = np.zeros_like(self.w2)
            grad_b2 = np.zeros_like(self.b2)
            total = 0.0
            weight_sum = 0.0
            for situation, candidates, label in groups:
                rows = np.stack([pair_features(situation, candidate) for candidate in candidates]).astype(np.float32)
                hidden_in = rows @ self.w1 + self.b1
                hidden = np.maximum(0.0, hidden_in)
                logits = (hidden @ self.w2 + self.b2)[:, 0]
                shifted = logits - float(logits.max())
                exp = np.exp(shifted)
                probs = exp / exp.sum()
                chosen = str(candidates[label].get("verb") or "")
                weight = skill_weight if chosen.startswith("skill:") else 1.5 if chosen == "attack" else 1.0
                total += weight * (-np.log(max(float(probs[label]), 1e-8)))
                weight_sum += weight
                dlogits = weight * probs
                dlogits[label] -= weight
                grad_hidden = dlogits[:, None] * self.w2.T
                grad_hidden[hidden_in <= 0] = 0
                grad_w2 += hidden.T @ dlogits[:, None]
                grad_b2 += dlogits.sum(keepdims=True)
                grad_w1 += rows.T @ grad_hidden
                grad_b1 += grad_hidden.sum(axis=0)
            scale = max(weight_sum, 1.0)
            self.w1 -= lr * (grad_w1 / scale).astype(np.float32)
            self.b1 -= lr * (grad_b1 / scale).astype(np.float32)
            self.w2 -= lr * (grad_w2 / scale).astype(np.float32)
            self.b2 -= lr * (grad_b2 / scale).astype(np.float32)
            losses.append(total / max(weight_sum, 1.0))
        return losses

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez(path, w1=self.w1, b1=self.b1, w2=self.w2, b2=self.b2)

    @classmethod
    def load(cls, path: Path) -> "LegalRanker":
        blob = np.load(path)
        model = cls()
        model.w1 = blob["w1"]
        model.b1 = blob["b1"]
        model.w2 = blob["w2"]
        model.b2 = blob["b2"]
        if tuple(model.w1.shape) != (INPUT_DIM, HIDDEN) or tuple(model.w2.shape) != (HIDDEN, 1):
            raise ValueError("排序权重和当前的可见局面输入不一致。")
        return model

    def _logits(self, rows: np.ndarray) -> np.ndarray:
        hidden = np.maximum(0.0, rows @ self.w1 + self.b1)
        return hidden @ self.w2 + self.b2


def load_groups(path: Path) -> list[tuple[str, dict[str, Any], list[dict[str, Any]], int]]:
    groups: list[tuple[str, dict[str, Any], list[dict[str, Any]], int]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        verb = str(row.get("verb") or "")
        candidates = [item for item in (row.get("candidates") or []) if isinstance(item, dict) and item.get("verb")]
        verbs = [str(item.get("verb") or "") for item in candidates]
        if verb not in verbs:
            continue
        situation = row.get("situation") if isinstance(row.get("situation"), dict) else {}
        if not isinstance(situation.get("actor"), dict):
            continue
        groups.append((str(row.get("replay") or ""), situation, candidates, verbs.index(verb)))
    return groups


def train_ranker(
    records: Path,
    out: Path,
    *,
    epochs: int = 80,
    report_file: Path | None = None,
    min_rows: int = 20,
) -> dict[str, Any]:
    groups = load_groups(records)
    if len(groups) < min_rows:
        raise ValueError(f"只有 {len(groups)} 条带合法清单的决策，不够训练。")
    replays = sorted({replay for replay, _situation, _legal, _label in groups})
    holdout = max(1, int(len(replays) * 0.2))
    test_replays = set(replays[-holdout:]) if len(replays) >= 5 else set()
    train_groups = [group[1:] for group in groups if group[0] not in test_replays]
    test_groups = [group[1:] for group in groups if group[0] in test_replays]
    if not train_groups or not test_groups:
        cut = max(1, int(len(groups) * 0.2))
        test_groups = [group[1:] for group in groups[:cut]]
        train_groups = [group[1:] for group in groups[cut:]]
    model = LegalRanker()
    losses = model.fit(train_groups, epochs=epochs)
    model.save(out)
    frequency = _frequency(train_groups)
    report = {
        "weights": str(out),
        "train_rows": len(train_groups),
        "test_rows": len(test_groups),
        "test_replays": len(test_replays),
        "test_accuracy": _accuracy(model, test_groups),
        "frequency_baseline": _frequency_accuracy(test_groups, frequency),
        "skill_accuracy": _accuracy(model, [group for group in test_groups if str(group[1][group[2]].get("verb") or "").startswith("skill:")]),
        "skill_frequency_baseline": _frequency_accuracy(
            [group for group in test_groups if str(group[1][group[2]].get("verb") or "").startswith("skill:")],
            frequency,
        ),
        "choice_accuracy": _accuracy(model, [group for group in test_groups if len(group[1]) >= 2]),
        "choice_frequency_baseline": _frequency_accuracy(
            [group for group in test_groups if len(group[1]) >= 2],
            frequency,
        ),
        "final_loss": round(losses[-1], 4) if losses else None,
    }
    destination = report_file or out.with_name("ranker-report.json")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _accuracy(model: LegalRanker, groups: list[tuple[dict[str, Any], list[dict[str, Any]], int]]) -> float | None:
    if not groups:
        return None
    correct = 0
    for situation, candidates, label in groups:
        chosen = int(model.score_candidates(situation, candidates).argmax())
        correct += int(chosen == label)
    return round(correct / len(groups), 3)


def _frequency(groups: list[tuple[dict[str, Any], list[dict[str, Any]], int]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for _situation, candidates, label in groups:
        verb = str(candidates[label].get("verb") or "")
        counts[verb] = counts.get(verb, 0) + 1
    return counts


def _frequency_accuracy(
    groups: list[tuple[dict[str, Any], list[dict[str, Any]], int]],
    frequency: dict[str, int],
) -> float | None:
    if not groups:
        return None
    correct = 0
    for _situation, candidates, label in groups:
        picked = max(range(len(candidates)), key=lambda index: (frequency.get(str(candidates[index].get("verb") or ""), 0), index))
        correct += int(picked == label)
    return round(correct / len(groups), 3)


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="用一步一条的决策样本训练合法清单排序。")
    parser.add_argument("--records", type=Path, default=Path("var/nn-lab/visible-decisions.jsonl"))
    parser.add_argument("--out", type=Path, default=Path("var/nn-lab/ranker.npz"))
    parser.add_argument("--epochs", type=int, default=80)
    args = parser.parse_args()
    print(json.dumps(train_ranker(args.records, args.out, epochs=args.epochs), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
