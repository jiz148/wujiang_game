"""Skill-owned preparation scoring plans for the red equipment family."""

from __future__ import annotations


class RedAttackPreparationMixin:
    def ai_red_adjust_attack_plan(self, battle, actor, services, profile, after: float) -> float:
        return after

    def ai_red_success_bonus(self) -> float:
        return 10.0

    def ai_red_preparation_score(self, battle, actor, services, profile) -> float:
        before = services.red_attack_plan_value(battle, actor)
        with services.ai_probe_rollback(battle):
            self.execute(battle, actor, {})
            after = services.red_attack_plan_value(battle, actor)
            after = self.ai_red_adjust_attack_plan(battle, actor, services, profile, after)
        gain = after - before
        if gain <= 4:
            return -20.0
        return after + self.ai_red_success_bonus()


class RedDefensePreparationMixin:
    ai_red_defense_physical = True

    def ai_red_preparation_score(self, battle, actor, services, profile) -> float:
        physical = self.ai_red_defense_physical
        if ((physical and (actor.physical_immunity or actor.has_status("无限铠甲")
                           or actor.has_status("飞龙宝珠")))
                or (not physical and actor.magic_immunity)):
            return -20.0
        if services.forecasting_active() and not services.direct_scoring():
            try:
                with services.ai_probe_rollback(battle):
                    expected = services._red_expected_defense_value(battle, actor, self, self.code)
                if expected is not None:
                    return expected
            except services.UnstableObservation:
                services.note("defense_convolution_unsafe_inspection")

        def exposure() -> float:
            result = 0.0
            for enemy in services.living_hostile_combatants(battle, actor.player_id):
                distance = services.distance_between_units(battle, actor, enemy)
                if physical:
                    if not enemy.cannot_attack and distance <= enemy.targeting_range() + enemy.normal_move_distance():
                        result += services.estimate_attack_damage(
                            battle, enemy, actor, {}, attack_power=enemy.stat("attack")) * enemy.attack_actions_per_turn()
                elif distance <= max(5, enemy.targeting_range()) + enemy.normal_move_distance() and not enemy.cannot_use_skills:
                    for incoming in enemy.skills:
                        if (incoming.code in services.DAMAGING_SKILL_CODES and incoming.cooldown_remaining == 0
                                and (incoming.max_uses_per_battle is None or incoming.uses_this_battle < incoming.max_uses_per_battle)
                                and enemy.current_mana >= incoming.mana_cost):
                            result += services.estimate_skill_damage(
                                battle, enemy, incoming, {}, actor, enemy.stat("attack"), cells=[],
                                ignore_shield=incoming.ignores_shield_for_payload(battle, enemy, {}),
                                half_ignore_shield=False)
            return result

        before = exposure()
        with services.ai_probe_rollback(battle):
            self.execute(battle, actor, {})
            saved = max(0.0, before - exposure())
        return saved * 130.0 + (90.0 if saved >= actor.current_hp and saved > 0 else 0.0) - 35.0
