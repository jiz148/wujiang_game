from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


REPLAYS_DIR = Path("replays")


@dataclass(slots=True)
class ReplayStep:
    index: int
    reason: str
    timestamp: float
    omniscient_battle: dict[str, Any]
    spectator_battle: dict[str, Any]
    seat_views: dict[str, dict[str, Any]]

    def to_manifest_entry(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "reason": self.reason,
            "timestamp": self.timestamp,
            "active_turn_unit_id": self.omniscient_battle.get("active_turn_unit_id"),
            "active_turn_unit_name": self.omniscient_battle.get("active_turn_unit_name"),
            "input_player": self.omniscient_battle.get("input_player"),
            "winner": self.omniscient_battle.get("winner"),
            "round_number": self.omniscient_battle.get("round_number"),
        }


@dataclass(slots=True)
class ReplayRecorder:
    room_id: str
    mode: str
    created_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    saved_path: Optional[str] = None
    steps: list[ReplayStep] = field(default_factory=list)

    def append_step(
        self,
        *,
        reason: str,
        omniscient_battle: dict[str, Any],
        spectator_battle: dict[str, Any],
        seat_views: dict[str, dict[str, Any]],
    ) -> int:
        step = ReplayStep(
            index=len(self.steps),
            reason=str(reason or "state"),
            timestamp=time.time(),
            omniscient_battle=omniscient_battle,
            spectator_battle=spectator_battle,
            seat_views=seat_views,
        )
        self.steps.append(step)
        return step.index

    @property
    def step_count(self) -> int:
        return len(self.steps)

    @property
    def last_index(self) -> int:
        return max(self.step_count - 1, 0)

    def metadata(self) -> dict[str, Any]:
        return {
            "room_id": self.room_id,
            "mode": self.mode,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "saved_path": self.saved_path,
            "step_count": self.step_count,
            "last_step_index": self.last_index,
            "steps": [step.to_manifest_entry() for step in self.steps],
        }

    def battle_for_step(
        self,
        step_index: int,
        *,
        seat_id: Optional[int],
        omniscient: bool,
    ) -> dict[str, Any]:
        if not self.steps:
            raise IndexError("Replay has no steps.")
        normalized = max(0, min(int(step_index), self.last_index))
        step = self.steps[normalized]
        if omniscient or seat_id is None:
            return step.omniscient_battle if omniscient else step.spectator_battle
        return step.seat_views.get(str(int(seat_id))) or step.spectator_battle

    def finish_and_save(
        self,
        *,
        room_summary: dict[str, Any],
    ) -> str:
        if self.saved_path:
            return self.saved_path
        self.finished_at = time.time()
        REPLAYS_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(self.finished_at))
        path = REPLAYS_DIR / f"{self.room_id}-{timestamp}.json"
        payload = {
            "room_id": self.room_id,
            "mode": self.mode,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "room_summary": room_summary,
            "metadata": self.metadata(),
            "steps": [
                {
                    "index": step.index,
                    "reason": step.reason,
                    "timestamp": step.timestamp,
                    "omniscient_battle": step.omniscient_battle,
                    "spectator_battle": step.spectator_battle,
                    "seat_views": step.seat_views,
                }
                for step in self.steps
            ],
        }
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        self.saved_path = str(path.as_posix())
        return self.saved_path
