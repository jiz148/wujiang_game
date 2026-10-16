"""Split visible decisions by the living actor and train one ranker per hero.

A row belongs to a hero only when that hero is the one who acted. Taking the
action already means they were on the board and alive. Dead bodies and hidden
enemies are not in the visible situation, so they are not used as labels.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from wujiang.tactical.rooms.imitation.ranker import train_ranker
from wujiang.tactical.rooms.imitation.service import lab_hero_dir

MIN_ROWS = 12
PLAIN_SOLDIERS = frozenset({
    "普通步兵",
    "步兵",
    "弓兵",
    "守备兵",
    "strategy_infantry",
    "strategy_archer",
    "strategy_garrison",
})


def uses_soldier_logic(identity: str) -> bool:
    return str(identity or "").strip() in PLAIN_SOLDIERS


def living_actor_key(row: dict[str, Any]) -> str:
    situation = row.get("situation") if isinstance(row.get("situation"), dict) else {}
    actor = situation.get("actor") if isinstance(situation.get("actor"), dict) else {}
    if float(actor.get("hp_ratio") or 0) <= 0:
        return ""
    return str(actor.get("hero_code") or actor.get("name") or "").strip()


def hero_file_stem(identity: str) -> str:
    cleaned = "".join("_" if character in '\\/:*?"<>|' else character for character in identity.strip())
    return cleaned or "unknown"


def shipped_hero_dir() -> Path:
    from wujiang.platform.http.runtime import PROJECT_ROOT
    return PROJECT_ROOT / "data" / "hero-models"


def hero_weight_path(identity: str) -> Path:
    name = f"{hero_file_stem(identity)}.npz"
    shipped = shipped_hero_dir() / name
    if shipped.is_file():
        return shipped
    return lab_hero_dir() / name


def split_records(source: Path, directory: Path | None = None) -> dict[str, Any]:
    destination = directory or lab_hero_dir()
    destination.mkdir(parents=True, exist_ok=True)
    handles: dict[str, Any] = {}
    counts: dict[str, int] = {}
    try:
        for line in source.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            key = living_actor_key(row)
            if not key:
                continue
            if key not in handles:
                path = destination / f"{hero_file_stem(key)}.jsonl"
                handles[key] = path.open("w", encoding="utf-8", newline="\n")
            handles[key].write(json.dumps(row, ensure_ascii=False) + "\n")
            counts[key] = counts.get(key, 0) + 1
    finally:
        for handle in handles.values():
            handle.close()
    catalog = {
        "source": str(source),
        "heroes": len(counts),
        "rows": sum(counts.values()),
        "counts": dict(sorted(counts.items(), key=lambda item: (-item[1], item[0]))),
    }
    (destination / "catalog.json").write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    return catalog


def train_heroes(directory: Path | None = None, *, min_rows: int = MIN_ROWS, epochs: int = 60) -> dict[str, Any]:
    folder = directory or lab_hero_dir()
    catalog_path = folder / "catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8")) if catalog_path.is_file() else {"counts": {}}
    trained: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for identity, count in (catalog.get("counts") or {}).items():
        if uses_soldier_logic(str(identity)):
            skipped.append({"hero": identity, "rows": int(count), "reason": "小兵走原来的军队算法，不训练网络。"})
            continue
        records = folder / f"{hero_file_stem(str(identity))}.jsonl"
        if int(count) < min_rows or not records.is_file():
            skipped.append({"hero": identity, "rows": int(count), "reason": "样本太少，先只整理不训练。"})
            continue
        out = hero_weight_path(str(identity))
        try:
            report = train_ranker(
                records,
                out,
                epochs=epochs if int(count) >= 40 else 40,
                report_file=out.with_suffix(".report.json"),
                min_rows=min_rows,
            )
        except (OSError, ValueError) as exc:
            skipped.append({"hero": identity, "rows": int(count), "reason": str(exc)})
            continue
        trained.append({
            "hero": identity,
            "rows": int(count),
            "test_accuracy": report.get("test_accuracy"),
            "frequency_baseline": report.get("frequency_baseline"),
            "skill_accuracy": report.get("skill_accuracy"),
            "weights": report.get("weights"),
        })
        print(json.dumps(trained[-1], ensure_ascii=False), flush=True)
    summary = {
        "trained": len(trained),
        "skipped": len(skipped),
        "min_rows": min_rows,
        "heroes": trained,
        "skipped_heroes": skipped,
    }
    (folder / "train-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="按武将拆开可见决策，并分别训练。")
    parser.add_argument("command", choices=("split", "train"))
    parser.add_argument("--records", type=Path, default=Path("data/nn-lab/visible-decisions.jsonl"))
    parser.add_argument("--min-rows", type=int, default=MIN_ROWS)
    parser.add_argument("--epochs", type=int, default=60)
    args = parser.parse_args()
    if args.command == "split":
        catalog = split_records(args.records)
        print(json.dumps({"heroes": catalog["heroes"], "rows": catalog["rows"]}, ensure_ascii=False, indent=2))
        return
    summary = train_heroes(min_rows=args.min_rows, epochs=args.epochs)
    print(json.dumps({"trained": summary["trained"], "skipped": summary["skipped"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
