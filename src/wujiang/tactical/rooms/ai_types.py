"""AI value objects and difficulty configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

@dataclass(slots=True)
class DifficultyProfile:
    action_threshold: float
    reaction_threshold: float
    instant_threshold: float
    aggressive_bonus: float
    support_bonus: float
    once_per_battle_threshold: float


@dataclass(slots=True)
class AICandidate:
    payload: dict[str, Any]
    score: float
    summary: str


def difficulty_profile(name: str) -> DifficultyProfile:
    normalized = str(name or "standard").strip().lower()
    if normalized == "easy":
        return DifficultyProfile(
            action_threshold=25.0,
            reaction_threshold=55.0,
            instant_threshold=90.0,
            aggressive_bonus=0.0,
            support_bonus=8.0,
            once_per_battle_threshold=95.0,
        )
    if normalized == "aggressive":
        return DifficultyProfile(
            action_threshold=12.0,
            reaction_threshold=35.0,
            instant_threshold=45.0,
            aggressive_bonus=18.0,
            support_bonus=0.0,
            once_per_battle_threshold=30.0,
        )
    return DifficultyProfile(
        action_threshold=18.0,
        reaction_threshold=42.0,
        instant_threshold=60.0,
        aggressive_bonus=10.0,
        support_bonus=4.0,
        once_per_battle_threshold=55.0,
    )
