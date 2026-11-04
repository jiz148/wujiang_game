"""Shared recipient resolution for shield-piercing nondamage controls."""

from __future__ import annotations

from wujiang.tactical.engine.core import Battle, Unit

def piercing_nondamage_recipient(battle: Battle, actor: Unit, target: Unit, *,
                                 action_name: str, code: str) -> Unit | None:
    target = battle.effect_recipient(target)
    if target.skill_non_damage_effects_blocked():
        battle.log_public_event(f"{target.name} 未受【{action_name}】的无伤效果影响。", source=actor, target=target)
        return None
    result = battle.validate_target(actor, target, action_name=action_name, is_skill=True,
        is_hostile=target.player_id != actor.player_id, ignore_shield=True,
        resolve_defenses=False, record_effect_on_validation=False,
        tags={"skill", code, "control"})
    target = result.target
    if result.cancelled:
        battle.log_public_event(result.reason or f"{target.name} 未受【{action_name}】影响。", source=actor, target=target)
        return None
    if target.skill_non_damage_effects_blocked():
        battle.log_public_event(f"{target.name} 未受【{action_name}】的无伤效果影响。", source=actor, target=target)
        return None
    if target.player_id != actor.player_id and target.total_shields() > 0:
        target.consume_one_shield()
        battle.log_public_event(f"{target.name} 的一层护盾被【{action_name}】贯穿并打碎。", source=actor, target=target)
    if target.player_id != actor.player_id and target.dodge_charges > 0 and not result.cannot_evade:
        target.dodge_charges -= 1
        battle.log_public_event(f"{target.name} 闪避了【{action_name}】。", source=actor, target=target)
        return None
    if battle.destroy_clone_for_skill_effect(target, source=actor, action_name=action_name):
        return None
    return target


