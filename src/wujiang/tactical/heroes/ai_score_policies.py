"""Hero-owned scoring for shared skill classes with different tactical roles."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from wujiang.tactical.rooms.ai_probe import ai_probe_rollback

if TYPE_CHECKING:
    from wujiang.tactical.engine.core import Battle, Position, Unit

from wujiang.tactical.heroes.ai_policy import (
    AISkillScoreContext, BarrierShieldAIPolicy, CompositeReactionAIPolicy, CounterReactionAIPolicy,
    HeroAIPolicy,
)


class RemiPiercePolicy(HeroAIPolicy):
    ai_mana_risk = True
    contextual_skill_codes = frozenset({"pierce"})

    def contextual_skill_score(self, context: AISkillScoreContext) -> float | None:
        return context.r13_effect_score(context.skill("pierce"))

    def early_attack_score(self, battle: Battle, actor: Unit, target: Unit | None,
                              payload: dict, profile: Any, shared: Any) -> float:
        return shared.r13_attack_value(battle, actor, payload)


class KikuPiercePolicy(HeroAIPolicy):
    handles_kiku_movement = True

    def position_move_adjustment(self, battle: Battle, actor: Unit,
                                 destination: Position, profile: Any, shared: Any) -> float:
        with ai_probe_rollback(battle):
            actor.position = destination
            return (0.7 * shared.r23_attack_plan_value(battle, actor)
                    - 2.0 * shared.natsume_unit_danger(battle, actor))

    contextual_skill_codes = frozenset({"pierce"})

    def contextual_skill_score(self, context: AISkillScoreContext) -> float | None:
        return context.paid_skill_value()

    def early_attack_score(self, battle: Battle, actor: Unit, target: Unit | None,
                              payload: dict, profile: Any, shared: Any) -> float:
        return shared.r23_attack_value(battle, actor, payload)


class FusionPiercePolicy(HeroAIPolicy):
    late_position_adjustment = True

    def position_move_adjustment(self, battle: Battle, actor: Unit,
                                 destination: Position, profile: Any, shared: Any) -> float:
        with ai_probe_rollback(battle):
            actor.position = destination
            return (shared.fusion_attack_plan_value(battle, actor, allow_move=False)
                    - shared.fusion_position_risk(battle, actor))

    contextual_skill_codes = frozenset({"pierce"})

    def contextual_skill_score(self, context: AISkillScoreContext) -> float | None:
        return context.shared.fusion_pierce_score(context)

    def late_attack_score(self, battle: Battle, actor: Unit, target: Unit | None,
                              payload: dict, profile: Any, shared: Any) -> float:
        return shared.fusion_attack_value(battle, actor, payload)


class MessengerPiercePolicy(HeroAIPolicy):
    ai_messenger_mana = True
    contextual_skill_codes = frozenset({"pierce"})

    def contextual_skill_score(self, context: AISkillScoreContext) -> float | None:
        return context.r18_effect_score(context.skill("pierce"))


class LeopardPiercePolicy(MessengerPiercePolicy):
    ai_messenger_mana = False

    def position_move_adjustment(self, battle: Battle, actor: Unit,
                                 destination: Position, profile: Any, shared: Any) -> float:
        immediate = shared.r21_winged_contact_value(battle, actor, allow_leap=False, allow_move=False)
        with ai_probe_rollback(battle):
            moved = destination != actor.position
            actor.position = destination
            if moved:
                actor.normal_move_actions_used = actor.normal_move_actions_per_turn()
                actor.normal_move_steps_used = actor.normal_move_distance()
            return 0.7 * max(0.0, shared.r21_winged_contact_value(battle, actor) - immediate)

    def target_attack_score(self, battle: Battle, actor: Unit, target: Unit | None,
                              payload: dict, profile: Any, shared: Any) -> float | None:
        return shared.r21_attack_value(battle, actor, target, payload) if target is not None else None


class FlorenzaPiercePolicy(CounterReactionAIPolicy):
    contextual_skill_codes = frozenset({"remote_pierce"})

    def contextual_skill_score(self, context: AISkillScoreContext) -> float | None:
        return context.r18_effect_score(context.skill("remote_pierce"))

    def target_attack_score(self, battle: Battle, actor: Unit, target: Unit | None,
                              payload: dict, profile: Any, shared: Any) -> float | None:
        return shared.r21_attack_value(battle, actor, target, payload) if target is not None else None


class WetlandHealPolicy(CompositeReactionAIPolicy):
    contextual_skill_codes = frozenset({"heal"})

    def contextual_skill_score(self, context: AISkillScoreContext) -> float | None:
        return context.shared.wetland_heal_score(context)

    def target_attack_score(self, battle: Battle, actor: Unit, target: Unit | None,
                              payload: dict, profile: Any, shared: Any) -> float | None:
        return shared.r22_attack_value(battle, actor, target, payload) if target is not None else None


class GazerAttackPolicy(CounterReactionAIPolicy):
    def position_move_adjustment(self, battle: Battle, actor: Unit,
                                 destination: Position, profile: Any, shared: Any) -> float:
        with ai_probe_rollback(battle):
            actor.position = destination
            return (0.8 * shared.r22_contact_value(battle, actor, allow_move=False)
                    - shared.r22_gazer_window_risk(battle, actor))

    def target_attack_score(self, battle: Battle, actor: Unit, target: Unit | None,
                              payload: dict, profile: Any, shared: Any) -> float | None:
        return shared.r22_attack_value(battle, actor, target, payload) if target is not None else None

    def incoming_attack_score_override(self, battle: Battle, actor: Unit, target: Unit,
                                       payload: dict, profile: Any, shared: Any) -> float:
        return shared.r22_attack_value(battle, actor, target, payload)


class WaterNinjaAttackPolicy(CompositeReactionAIPolicy):
    def position_move_adjustment(self, battle: Battle, actor: Unit,
                                 destination: Position, profile: Any, shared: Any) -> float:
        with ai_probe_rollback(battle):
            actor.position = destination
            return 0.8 * shared.r22_contact_value(battle, actor, allow_move=False)

    def target_attack_score(self, battle: Battle, actor: Unit, target: Unit | None,
                              payload: dict, profile: Any, shared: Any) -> float | None:
        return shared.r22_attack_value(battle, actor, target, payload) if target is not None else None


class DawnBarrierAttackPolicy(BarrierShieldAIPolicy):
    def position_move_adjustment(self, battle: Battle, actor: Unit,
                                 destination: Position, profile: Any, shared: Any) -> float:
        with ai_probe_rollback(battle):
            actor.position = destination
            return shared.r21_barrier_position_value(battle, actor)

    def target_attack_score(self, battle: Battle, actor: Unit, target: Unit | None,
                              payload: dict, profile: Any, shared: Any) -> float | None:
        return shared.r21_attack_value(battle, actor, target, payload) if target is not None else None


class RedAttackPolicy(HeroAIPolicy):
    def late_attack_score(self, battle: Battle, actor: Unit, target: Unit | None,
                              payload: dict, profile: Any, shared: Any) -> float:
        return shared.red_attack_value(battle, actor, payload)


class FeiwangAttackPolicy(HeroAIPolicy):
    is_feiwang = True

    def single_attack_score(self, battle: Battle, actor: Unit, target: Unit | None,
                              payload: dict, profile: Any, shared: Any) -> float | None:
        return (shared.feiwang_attack_value(battle, actor, target, payload)
                if target is not None and target.player_id != actor.player_id else None)


class AlexanderAttackPolicy(HeroAIPolicy):
    def final_attack_bonus(self, battle: Battle, actor: Unit, target: Unit,
                           payload: dict, shared: Any) -> float:
        return shared.alexander_expected_swap_value(battle, actor)


class LineWindowMovePolicy(HeroAIPolicy):
    def position_move_adjustment(self, battle: Battle, actor: Unit,
                                 destination: Position, profile: Any, shared: Any) -> float:
        before = shared.r20_line_window_value(battle, actor, profile)
        with ai_probe_rollback(battle):
            actor.position = destination
            return 0.75 * (shared.r20_line_window_value(battle, actor, profile) - before)


class DemonWeatherAttackPolicy(HeroAIPolicy):
    def target_attack_score(self, battle: Battle, actor: Unit, target: Unit | None,
                            payload: dict, profile: Any, shared: Any) -> float | None:
        if target is None or target.player_id == actor.player_id:
            return None
        power = battle.basic_attack_preview_power(actor, payload)
        impact = shared.probe_attack_damage_impact(battle, actor, target, payload, attack_power=power)
        value = min(target.current_hp, impact.damage) * 100.0
        killed = shared.scored_kill_probability(impact.damage, target.current_hp - 1e-9)
        if killed > 0:
            value += 90.0 * killed
        elif impact.changed_target and impact.damage <= 0:
            value += 20.0
        from wujiang.tactical.heroes.mechanics.racial_weather import weather_race_eligible
        if (value > 0 and not actor.direct_effects_blocked()
                and weather_race_eligible(actor, "恶魔") and battle.unit_in_weather("万魔殿", actor)):
            value += 0.5 * min(max(0.0, target.current_hp - impact.damage), impact.damage) * 100.0
        return value - 4.0


class DemonLineWindowPolicy(DemonWeatherAttackPolicy, LineWindowMovePolicy):
    pass


class NatsumeMovePolicy(HeroAIPolicy):
    late_position_adjustment = True

    def position_move_adjustment(self, battle: Battle, actor: Unit,
                                 destination: Position, profile: Any, shared: Any) -> float:
        with ai_probe_rollback(battle):
            actor.position = destination
            return (shared.natsume_position_plan_value(battle, actor)
                    - shared.natsume_unit_danger(battle, actor))


class ZeroSplitPolicy(HeroAIPolicy):
    """Use clones only when Zero has no valuable attack, dash, or pass-through move."""

    def extra_normal_move_candidates(self, battle: Battle, actor: Unit,
                                     role: str, profile: Any, builder: Any) -> list:
        return builder.build_zero_crossing_move_candidates(battle, actor, role, profile)

    def normal_move_candidate_score(self, battle: Battle, actor: Unit,
                                    destination: Position, profile: Any,
                                    score: float, shared: Any) -> float:
        path = battle.find_path(actor, destination,
                                max_distance=actor.remaining_normal_move_distance(battle),
                                use_movement_cost=True)
        return score + shared.zero_path_effect_score(battle, actor, path)

    def summon_skill_score(self, context: AISkillScoreContext) -> float | None:
        if context.code != "split":
            return None
        if context.shared.best_attack_score(context.battle, context.actor,
                                            context.profile) > context.profile.action_threshold:
            return -1000.0
        if context.shared.profitable_zero_dash(context):
            return -1000.0
        if context.shared.profitable_zero_crossing_move(context):
            return -1000.0
        return None


class BlindMovePolicy(HeroAIPolicy):
    def normal_move_candidate_score(self, battle: Battle, actor: Unit,
                                    destination: Position, profile: Any,
                                    score: float, shared: Any) -> float:
        return shared.blind_paid_move_score(battle, actor, destination, profile)

class OboroSplitPolicy(HeroAIPolicy):
    """Do not split ahead of an available sword, pierce, or meditation line."""

    def summon_skill_score(self, context: AISkillScoreContext) -> float | None:
        if context.code != "split":
            return None
        threshold = context.profile.action_threshold
        if context.shared.best_attack_score(context.battle, context.actor, context.profile) > threshold:
            return -1000.0
        if context.shared.profitable_alternative_skill(
            context, frozenset({"true_blade_air_slash", "pierce", "oboro_meditate"}), threshold):
            return -1000.0
        return None


class ErnestSplitPolicy(HeroAIPolicy):
    """Keep the turn for a useful active skill before making clones."""

    def summon_skill_score(self, context: AISkillScoreContext) -> float | None:
        if context.code != "split":
            return None
        if context.shared.best_attack_score(context.battle, context.actor, context.profile) > 0:
            return -1000.0
        if context.shared.profitable_alternative_skill(
            context, frozenset({"purify_mana", "sacred_duel", "dragon_breath", "drain_mana"}),
            context.profile.action_threshold):
            return -1000.0
        return None
