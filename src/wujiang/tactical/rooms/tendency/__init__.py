"""Tendency picker. Intents are the decision; the snapshot supplies the step."""
from wujiang.tactical.rooms.tendency.decide import (
    choose_chain_reaction,
    choose_damage_choice_action,
    choose_instant_action,
    choose_respawn_action,
    choose_turn_bundle_action,
)

__all__ = [
    "choose_chain_reaction",
    "choose_damage_choice_action",
    "choose_instant_action",
    "choose_respawn_action",
    "choose_turn_bundle_action",
]
