"""AI turn, reaction, and prompted-action coordination."""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any, Iterable, Optional, Protocol
from wujiang.tactical.engine.core import ActionError, Battle, DamageContext, Position, Unit, battle_state_rollback
from wujiang.tactical.prediction import direct_scoring, expected_local, record_candidates
from wujiang.tactical.rooms.ai_probe import ai_probe_rollback
from wujiang.tactical.rooms.ai_targets import living_hostile_combatants

if TYPE_CHECKING:
    from wujiang.tactical.rooms.ai import AICandidate, DifficultyProfile

class DecisionServices(Protocol):
    AICandidate: Any
    DifficultyProfile: Any
    ai_enemy_view: Any
    alexander_magic_coverage_value: Any
    best_candidate: Any
    best_unit_candidate: Any
    build_attack_candidates: Any
    build_move_candidates: Any
    build_reaction_candidates: Any
    build_skill_candidates: Any
    clear_follow_anchor_cache: Any
    difficulty_profile: Any
    hero_style: Any
    r23_action_delta: Any
    r23_action_state: Any
    reviewed_r17_position_value: Any
    score_respawn_destination: Any
    scored_death_probability: Any
    with_enemy_view: Any

class AIDecisionCoordinator:
    """Choose from candidate builders and handle constrained action windows."""

    def __init__(self, services: DecisionServices) -> None:
        self.services = services

    def choose_turn_action(self, battle: Battle, actor: Unit, difficulty: str) -> dict[str, Any]:
        self.services.clear_follow_anchor_cache(battle)
        profile = self.services.difficulty_profile(difficulty)
        candidates, move_candidates = self.turn_action_candidates(battle, actor, profile)
        best_non_move = self.services.best_candidate(candidates)
        best_move = self.services.best_candidate(move_candidates)
        if best_non_move is not None and best_non_move.score >= profile.action_threshold:
            return best_non_move.payload
        if best_move is not None and best_move.score >= 0:
            return best_move.payload
        if best_non_move is not None and best_non_move.score > 0:
            return best_non_move.payload
        return {"type": "end_turn"}


    def choose_turn_bundle_action(self, battle: Battle, units: Iterable[Unit], difficulty: str) -> tuple[dict[str, Any], Optional[Unit]]:
        self.services.clear_follow_anchor_cache(battle)
        profile = self.services.difficulty_profile(difficulty)
        non_move_candidates: list[tuple[Unit, self.services.AICandidate]] = []
        move_candidates: list[tuple[Unit, self.services.AICandidate]] = []
        fallback_candidates: list[tuple[Unit, self.services.AICandidate]] = []
        for unit in units:
            if not unit.can_take_turn_actions(battle):
                continue
            actor_candidates, actor_moves = self.turn_action_candidates(battle, unit, profile)
            for candidate in actor_candidates:
                if candidate.score >= profile.action_threshold:
                    non_move_candidates.append((unit, candidate))
                elif candidate.score > 0:
                    fallback_candidates.append((unit, candidate))
            for candidate in actor_moves:
                if candidate.score >= 0:
                    move_candidates.append((unit, candidate))

        chosen = self.services.best_unit_candidate(non_move_candidates)
        if chosen is not None:
            return chosen[1].payload, chosen[0]
        chosen = self.services.best_unit_candidate(move_candidates)
        if chosen is not None:
            return chosen[1].payload, chosen[0]
        chosen = self.services.best_unit_candidate(fallback_candidates)
        if chosen is not None:
            return chosen[1].payload, chosen[0]
        return {"type": "end_turn"}, None


    def turn_action_candidates(self,
        battle: Battle,
        actor: Unit,
        profile: DifficultyProfile,
    ) -> tuple[list[AICandidate], list[AICandidate]]:
        action_snapshot = battle.action_snapshot_for(actor)
        candidates: list[self.services.AICandidate] = []
        move_candidates: list[self.services.AICandidate] = []
        preserve_new_stealth = actor.is_stealthed() and any(
            action in {"skill:stealth", "skill:extra_stealth"}
            for action in actor.actions_taken_this_turn
        )
        for action in action_snapshot.get("actions", []):
            if not action.get("available"):
                continue
            kind = str(action.get("kind") or "")
            if kind == "move":
                move_candidates.extend(self.services.build_move_candidates(battle, actor, action, profile))
            elif preserve_new_stealth:
                continue
            elif kind == "attack":
                candidates.extend(self.services.build_attack_candidates(battle, actor, action, profile))
            elif kind == "skill":
                candidates.extend(self.services.build_skill_candidates(battle, actor, action, profile, instant_only=False))
        if any(candidate.summary in {"skill:stealth", "skill:extra_stealth"} for candidate in candidates):
            can_reposition = any(candidate.score >= 0 for candidate in move_candidates)
            has_followup_action = any(
                candidate.score > 0 and candidate.summary not in {"skill:stealth", "skill:extra_stealth"}
                for candidate in candidates
            )
            if not can_reposition or has_followup_action:
                candidates = [
                    candidate for candidate in candidates
                    if candidate.summary not in {"skill:stealth", "skill:extra_stealth"}
                ]
        record_candidates(actor, candidates, move_candidates)
        return candidates, move_candidates


    def choose_instant_action(self, battle: Battle, units: Iterable[Unit], difficulty: str) -> Optional[dict[str, Any]]:
        self.services.clear_follow_anchor_cache(battle)
        profile = self.services.difficulty_profile(difficulty)
        candidates: list[self.services.AICandidate] = []
        for unit in units:
            snapshot = battle.action_snapshot_for(unit)
            for action in snapshot.get("actions", []):
                if action.get("kind") != "skill" or not action.get("available"):
                    continue
                if str(action.get("timing") or "") != "instant":
                    continue
                candidates.extend(self.services.build_skill_candidates(battle, unit, action, profile, instant_only=True))
        chosen = self.services.best_candidate(candidates)
        if chosen is None or chosen.score < profile.instant_threshold:
            return None
        return chosen.payload


    def choose_chain_reaction(self,
        battle: Battle,
        reactor: Unit,
        options: list[dict[str, Any]],
        difficulty: str,
    ) -> Optional[dict[str, Any]]:
        queued_action = battle.pending_chain.queued_action if battle.pending_chain is not None else None
        if queued_action is None:
            return None
        profile = self.services.difficulty_profile(difficulty)
        candidates: list[self.services.AICandidate] = []
        for option in options:
            candidates.extend(self.services.build_reaction_candidates(battle, reactor, queued_action, option, profile))
        chosen = self.services.best_candidate(candidates)
        if chosen is None or chosen.score < profile.reaction_threshold:
            return None
        return chosen.payload


    def choose_respawn_action(self, battle: Battle, unit: Unit, options: list[Position], difficulty: str) -> Optional[dict[str, Any]]:
        if not options:
            return None
        profile = self.services.difficulty_profile(difficulty)
        role = self.services.hero_style(unit)
        best: tuple[float, Position] | None = None
        for destination in options:
            score = self.services.score_respawn_destination(battle, unit, destination, role, profile)
            if best is None or score > best[0]:
                best = (score, destination)
        if best is None:
            return None
        destination = best[1]
        return {"type": "respawn_select", "unit_id": unit.unit_id, "x": destination.x, "y": destination.y}


    def choose_damage_choice_action(self, battle: Battle) -> dict[str, Any]:
        prompt = battle.pending_damage_choice
        if prompt is None:
            raise ActionError("当前没有待选择的伤害。")
        unit = battle.get_unit(prompt["unit_id"])
        if prompt.get("kind") == "gentleman_guard":
            with self.services.ai_enemy_view(battle, unit.player_id):
                protected = battle.units.get(str(prompt.get("protected_unit_id") or ""))
                damage = float(prompt.get("damage") or 0)
                guard = next((trait for trait in unit.traits if trait.name == "绅士防身"), None)
                remaining = guard.remaining(battle) if guard is not None else 0
                attacker = battle.current_turn_unit()
                followup_attacks = max(0, attacker.attack_actions_per_turn() - attacker.attacks_used) if attacker is not None else 0
                if protected is unit:
                    protect = (damage >= unit.current_hp or damage >= max(0.25, unit.current_hp * 0.4)
                               or remaining >= 2 and followup_attacks <= 0)
                elif protected is not None and protected.is_clone:
                    root_exposed = attacker is not None and attacker.position is not None and unit.position is not None and (
                        battle.distance_between_units(attacker, unit) <= attacker.targeting_range())
                    clone_useful = protected.position is not None and any(
                        enemy.position is not None and battle.distance_between_units(protected, enemy) <= 2
                        for enemy in living_hostile_combatants(battle, unit.player_id))
                    protect = remaining >= 2 and clone_useful and not root_exposed and followup_attacks <= 1
                else:
                    protect = False
                return {"type": "damage_choice", "unit_id": unit.unit_id,
                        "target_unit_id": "protect" if protect else "decline"}
        if prompt.get("kind") == "end_dash":
            with self.services.ai_enemy_view(battle, unit.player_id):
                return self.choose_uesugi_end_dash_action(battle, prompt, unit)
        if prompt.get("kind") == "electronic_teleport":
            with self.services.ai_enemy_view(battle, unit.player_id):
                return self.choose_electronic_teleport_action(battle, prompt, unit)
        if prompt.get("kind") == "hell_sacrifice":
            with self.services.ai_enemy_view(battle, unit.player_id):
                return self.choose_hell_sacrifice_action(battle, prompt, unit)
        if prompt.get("kind") == "hell_death_strike":
            with self.services.ai_enemy_view(battle, unit.player_id):
                return self.choose_hell_death_strike_action(battle, prompt, unit)
        if prompt.get("kind") == "hell_rescue":
            with self.services.ai_enemy_view(battle, unit.player_id):
                return self.choose_hell_rescue_action(battle, prompt, unit)
        if prompt.get("kind") in {"hell_dragon_first", "hell_dragon_second", "hell_dragon_effect"}:
            with self.services.ai_enemy_view(battle, unit.player_id):
                return self.choose_hell_dragon_sacrifice_action(battle, prompt, unit)
        if prompt.get("kind") in {"rotation", "attack_swap", "formation", "optional_swap", "optional_placement"}:
            with self.services.ai_enemy_view(battle, unit.player_id):
                return self.choose_rotation_damage_choice_action(battle, prompt, unit)
        damage = float(prompt["damage"])
        stats = list(prompt["stats"])
        choice = "decline"
        if stats and (damage >= unit.current_hp or damage >= max(0.4, unit.current_hp * 0.45)):
            costs = {"attack": 48, "defense": 52, "speed": 40, "attack_range": 44}
            choice = min(stats, key=lambda name: (costs[name] + (30 if unit.stat(name) <= 2 else 0), name))
        return {"type": "damage_choice", "unit_id": unit.unit_id, "stat_name": choice}


    def choose_hell_sacrifice_action(self, battle: Battle, prompt: dict[str, Any], unit: Unit) -> dict[str, Any]:
        """A free click is still a real allied death; decline unless rebirth repays it."""
        best = (0.0, "decline")
        for unit_id in prompt.get("options", []):
            target = battle.units.get(unit_id)
            if target is None or target.unit_id == unit.unit_id or not target.alive:
                continue
            recipient = battle.effect_recipient(target)
            if (recipient.magic_immunity or recipient.total_shields() > 0 or recipient.dodge_charges > 0
                    or recipient.skill_non_damage_effects_blocked()):
                continue
            if recipient.hero_code not in {"excel_r238", "excel_r240", "excel_r242"}:
                continue
            if not any(enemy.alive for enemy in living_hostile_combatants(battle, unit.player_id)):
                continue
            immediate_loss = (recipient.current_hp * 125.0 + recipient.stat("attack") * 14.0
                              + (35.0 if recipient.turn_ready else 0.0))
            future_gain = (115.0 if recipient.hero_code == "excel_r238" else
                           105.0 if recipient.hero_code == "excel_r240" else 0.0)
            if recipient.current_mana > 0:
                future_gain -= recipient.current_mana * 12.0
            score = future_gain - immediate_loss
            if score > best[0] + 12.0:
                best = (score, unit_id)
        return {"type": "damage_choice", "unit_id": unit.unit_id, "target_unit_id": best[1]}


    def choose_hell_dragon_sacrifice_action(self, battle: Battle, prompt: dict[str, Any], unit: Unit) -> dict[str, Any]:
        kind = str(prompt.get("kind") or "")
        options = [battle.units.get(str(unit_id)) for unit_id in prompt.get("options", [])]

        @expected_local(battle)
        def death_effect_value(target: Unit) -> float:
            code = target.hero_code
            value = 35.0 if code in {"excel_r238", "excel_r239", "excel_r240", "excel_r242",
                                      "excel_r243", "excel_r244", "excel_r245", "excel_r246",
                                      "excel_r247", "excel_r248"} else 0.0
            if code == "excel_r245":
                value += 55.0 * sum(body.player_id == target.player_id and "地狱" in body.name
                                      and not body.alive for body in battle.destroyed_units)
            elif code == "excel_r247":
                value += 28.0 * sum(body.alive and body.summoner_id == target.unit_id
                                      for body in battle.all_units())
            elif code in {"excel_r244", "excel_r246"} and target.position is not None:
                nearby = battle.units_at_cells(battle.neighbors(target.position))
                value += 22.0 * sum(body.alive and body.player_id != target.player_id for body in nearby)
                value -= 30.0 * sum(body.alive and body.player_id == target.player_id
                                      and body.unit_id != target.unit_id for body in nearby)
            return value

        @expected_local(battle)
        def loss(target: Unit) -> float:
            recipient = battle.effect_recipient(target)
            if (recipient is not target or recipient.magic_immunity or recipient.total_shields() > 0
                    or recipient.dodge_charges > 0 or recipient.skill_non_damage_effects_blocked()):
                return 10000.0
            return (target.current_hp * 95.0 + target.stat("attack") * 12.0
                    + (25.0 if target.turn_ready else 0.0) - death_effect_value(target))

        viable = [target for target in options if target is not None and target.alive and loss(target) < 10000]
        choice = "decline"
        if kind == "hell_dragon_effect":
            if viable:
                choice = max(viable, key=lambda target: (death_effect_value(target), -loss(target), target.unit_id)).unit_id
        elif kind == "hell_dragon_second":
            candidate_trait = next((trait for trait in unit.traits
                                    if callable(getattr(trait, "sacrifice_candidates", None))), None)
            offered_ids = {target.unit_id for target in options if target is not None}
            first_id = next((target.unit_id for target in candidate_trait.sacrifice_candidates(battle)
                             if target.unit_id not in offered_ids), "") if candidate_trait is not None else ""
            safe = [target for target in viable
                    if any(hero.unit_id not in {first_id, target.unit_id}
                           for hero in battle.hero_units(unit.player_id)
                           if not bool(getattr(hero, "is_siege_structure", False)))]
            if safe:
                choice = min(safe, key=lambda target: (loss(target), target.unit_id)).unit_id
        else:
            ranked = sorted(viable, key=lambda target: (loss(target), target.unit_id))
            if len(ranked) >= 2:
                enemies = living_hostile_combatants(battle, unit.player_id)
                return_value = 185.0 + unit.stat("attack") * 14.0
                for first in ranked:
                    second = next((target for target in ranked if target.unit_id != first.unit_id
                                   and any(hero.unit_id not in {first.unit_id, target.unit_id}
                                           for hero in battle.hero_units(unit.player_id)
                                           if not bool(getattr(hero, "is_siege_structure", False)))), None)
                    if (second is not None and enemies
                            and return_value > loss(first) + loss(second) + 25.0):
                        choice = first.unit_id
                        break
        return {"type": "damage_choice", "unit_id": unit.unit_id, "target_unit_id": choice}


    def choose_hell_dragon_return_action(self, battle: Battle, unit: Unit) -> Optional[dict[str, Any]]:
        if battle.winner is not None:
            return None
        options = battle.hell_dragon_return_options_for(unit)
        if not options:
            return None
        profile = self.services.difficulty_profile("标准")
        scored = [(self.services.score_respawn_destination(battle, unit, cell, self.services.hero_style(unit), profile), cell) for cell in options]
        best_score, destination = max(scored, key=lambda item: (item[0], -item[1].y, -item[1].x))
        allies = [ally for ally in battle.player_units(unit.player_id) if ally.alive and ally.unit_id != unit.unit_id]
        if allies and best_score < -125.0:
            return None
        return {"type": "hell_dragon_return", "unit_id": unit.unit_id,
                "x": destination.x, "y": destination.y}


    def choose_hell_rescue_action(self, battle: Battle, prompt: dict[str, Any], unit: Unit) -> dict[str, Any]:
        best = (0.0, "decline")
        needs_hero = not any(not bool(getattr(hero, "is_siege_structure", False))
                             for hero in battle.hero_units(unit.player_id))
        for unit_id in prompt.get("options", []):
            target = next((body for body in battle.destroyed_units if body.unit_id == unit_id), None)
            if target is None or target.alive or target.player_id != unit.player_id:
                continue
            if needs_hero and (target.is_summon or target.is_clone
                               or bool(getattr(target, "is_siege_structure", False))):
                continue
            if target.is_summon and target.summoner_id:
                summoner = battle.units.get(target.summoner_id)
                if summoner is None or not summoner.alive:
                    continue
            score = (120.0 + target.stat("attack") * 18.0 + target.stat("defense") * 10.0
                     + target.targeting_range() * 6.0)
            if target.hero_code in {"excel_r238", "excel_r240", "excel_r242", "excel_r243", "excel_r244", "excel_r246"}:
                score += 30.0
            if score > best[0]:
                best = (score, str(unit_id))
        return {"type": "damage_choice", "unit_id": unit.unit_id, "target_unit_id": best[1]}


    def choose_hell_death_strike_action(self, battle: Battle, prompt: dict[str, Any], unit: Unit) -> dict[str, Any]:
        best = (0.0, "decline")
        for unit_id in prompt.get("options", []):
            target = battle.units.get(str(unit_id))
            if target is None or not target.alive:
                continue
            recipient = battle.effect_recipient(target)
            guards = min(2, recipient.total_shields() + recipient.dodge_charges)
            hits = max(0, 2 - guards)
            harm = min(recipient.current_hp, hits * battle.damage_rule.calculate_damage(
                unit.stat("attack"), recipient.stat("defense")))
            score = (harm * 140.0 + guards * 12.0 + (75.0 if harm >= recipient.current_hp and harm > 0 else 0.0))
            if recipient.player_id == unit.player_id:
                score *= -1.4
            if score > best[0]:
                best = (score, unit_id)
        return {"type": "damage_choice", "unit_id": unit.unit_id, "target_unit_id": best[1]}


    def choose_rotation_damage_choice_action(self, battle: Battle, prompt: dict[str, Any], unit: Unit) -> dict[str, Any]:
        units = list(battle.all_units())
        initial = {
            candidate.unit_id: (candidate.current_hp, candidate.current_mana,
                                candidate.total_shields(), candidate.alive,
                                self.services.reviewed_r17_position_value(battle, unit, candidate),
                                self.services.alexander_magic_coverage_value(battle, unit, candidate) if prompt.get("kind") == "attack_swap" else 0.0)
            for candidate in units
        }

        @expected_local(battle)
        def outcome(choice: str) -> float:
            with battle_state_rollback(battle, ai_probe=False):
                # The outer enemy-view context is itself a probe; replay the real
                # damage hooks against that visible view, then restore every state.
                battle._ai_probe_active = False
                battle.pending_damage_choice = None
                battle._damage_choice_active = True
                battle._damage_choice_decisions = [*prompt["decisions"], choice]
                battle._damage_choice_event_index = 0
                battle._damage_choice_probe_default = True
                try:
                    battle._perform_action_steps(deepcopy(prompt["action_payload"]))
                except (ActionError, KeyError, ValueError, TypeError):
                    return -1000.0
                score = 0.0
                for candidate in units:
                    hp, mana, shields, alive, position_value, magic_value = initial[candidate.unit_id]
                    value = (hp - candidate.current_hp) * 100.0
                    value += (mana - candidate.current_mana) * 18.0
                    value += max(0, shields - candidate.total_shields()) * 20.0
                    if alive and self.services.scored_death_probability(candidate) > 0:
                        value += (90.0 * self.services.scored_death_probability(candidate) if direct_scoring() else 90.0)
                    score += -value if candidate.player_id == unit.player_id else value
                    if candidate.position is not None and candidate.alive:
                        position_gain = self.services.reviewed_r17_position_value(battle, unit, candidate) - position_value
                        score += position_gain * (0.8 if candidate.player_id == unit.player_id else -0.8)
                        if mandatory and candidate.player_id == unit.player_id and candidate.unit_id != unit.unit_id:
                            score += self.services.alexander_magic_coverage_value(battle, unit, candidate) - magic_value
                return score

        mandatory = prompt.get("kind") in {"attack_swap", "formation"}
        baseline = outcome("decline") if not mandatory else -1000.0
        best = (baseline + 8.0, "decline")
        for ally_id in prompt["options"]:
            score = outcome(ally_id)
            if mandatory and best[1] == "decline" or score > best[0]:
                best = (score, ally_id)
        return {"type": "damage_choice", "unit_id": unit.unit_id, "target_unit_id": best[1]}


    def choose_uesugi_end_dash_action(self, battle: Battle, prompt: dict[str, Any], unit: Unit) -> dict[str, Any]:
        trait = next((component for component in unit.iter_components() if component.name == "轮末穿行"), None)
        if trait is None:
            return {"type": "damage_choice", "unit_id": unit.unit_id, "target_unit_id": "decline"}
        routes = trait.routes(battle, unit)
        best = (8.0, "decline")
        for choice in prompt.get("options", []):
            path = routes.get(choice)
            if path is None:
                continue
            with ai_probe_rollback(battle):
                before = self.services.r23_action_state(battle)
                position_before = self.services.reviewed_r17_position_value(battle, unit, unit)
                try:
                    battle.move_unit(unit, path[-1], forced=True, ignore_units=True, path=path[1:],
                                     max_distance=6, exact_distance=6, straight_only=True,
                                     tags={"uesugi_end_dash", f"source:{unit.unit_id}"})
                    for target, _ in battle.path_crossing_events(unit, path):
                        if unit.alive and target.alive:
                            battle.resolve_damage(DamageContext(
                                source=unit, target=target, attack_power=max(0.0, unit.stat("attack") - 1),
                                is_skill=False, from_field_effect=True, action_name="轮末穿人伤害",
                                tags={"movement", "pass_through_damage"}))
                except (ActionError, KeyError, TypeError, ValueError):
                    continue
                score = self.services.r23_action_delta(unit, before)
                score += 0.9 * (self.services.reviewed_r17_position_value(battle, unit, unit) - position_before)
                if score > best[0]:
                    best = (score, choice)
        return {"type": "damage_choice", "unit_id": unit.unit_id, "target_unit_id": best[1]}


    def choose_electronic_teleport_action(self, battle: Battle, prompt: dict[str, Any], unit: Unit) -> dict[str, Any]:
        trait = next((component for component in unit.iter_components()
                      if component.name == "电子龙轮末瞬移"), None)
        if trait is None:
            return {"type": "damage_choice", "unit_id": unit.unit_id, "target_unit_id": "decline"}
        routes = trait.routes(battle, unit)
        original_value = self.services.reviewed_r17_position_value(battle, unit, unit)
        original_hp = unit.current_hp
        best = (6.0, "decline")
        for choice in prompt.get("options", []):
            destination = routes.get(choice)
            if destination is None:
                continue
            with ai_probe_rollback(battle):
                try:
                    battle.move_unit(unit, destination, forced=True, allow_anywhere=True,
                                     tags={"electronic_teleport", f"source:{unit.unit_id}"})
                except (ActionError, KeyError, TypeError, ValueError):
                    continue
                score = self.services.reviewed_r17_position_value(battle, unit, unit) - original_value
                score += (unit.current_hp - original_hp) * 100.0
                if score > best[0]:
                    best = (score, choice)
        return {"type": "damage_choice", "unit_id": unit.unit_id, "target_unit_id": best[1]}
