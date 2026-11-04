"""Battle BattleCombatMixin methods, retained under the Battle public API."""

from __future__ import annotations

from typing import Any, Optional

class BattleCombatMixin:
    """A cohesive Battle responsibility with no independent state."""

    def resolve_damage(self, ctx: DamageContext) -> DamageContext:
        from wujiang.tactical.engine.core import (
            direct_scoring,
            forecasting_active,
            random,
        )
        if direct_scoring() and not hasattr(ctx, "forecast_hit_probability"):
            # Saved reaction checkpoints may predate these scoring-only slots.
            ctx.forecast_hit_probability = 1.0
            ctx.forecast_attack_changes = ctx.forecast_damage_rows = None
            ctx.forecast_kill_probability = ctx.forecast_shield_pierce_probability = 0.0
        ctx.target = self.effect_recipient(ctx.target)
        if ctx.declared_source_attack is None and ctx.source is not None:
            queued = self.resolving_action
            if queued is not None and queued.actor_id == ctx.source.unit_id:
                ctx.declared_source_attack = queued.payload.get("declared_source_attack")
        with self.suppress_logs_for_stealth(ctx.target):
            if ctx.target.banished:
                ctx.cancelled = True
                ctx.reason = "目标不在战场上。"
                self.log_public_event(ctx.reason, source=ctx.source, target=ctx.target)
                return ctx

            def notify_cancelled() -> None:
                for effect in list(self.field_effects):
                    effect.on_damage_cancelled(self, ctx)
                if ctx.source is not None:
                    for component in self.components_for(ctx.source):
                        component.on_damage_cancelled(self, ctx)
                for component in self.components_for(ctx.target):
                    component.on_damage_cancelled(self, ctx)

            def notify_confirmed_destruction() -> None:
                if ctx.target.alive or ctx.source is None:
                    return
                ctx.target._destroying_source_unit_id = ctx.source.unit_id
                for component in self.components_for(ctx.source):
                    hook = getattr(component, "on_confirmed_damage_destruction", None)
                    if callable(hook):
                        hook(self, ctx)

            for effect in list(self.field_effects):
                effect.on_before_damage(self, ctx)
            if ctx.source is not None:
                for component in self.components_for(ctx.source):
                    component.on_before_damage(self, ctx)
            for component in self.components_for(ctx.target):
                component.on_before_damage(self, ctx)
            if not ctx.cancelled and ctx.raw_damage is None and ctx.area_cell_hits > 1:
                ctx.attack_power += max(0, int(ctx.area_cell_hits) - 1)
            for effect in list(self.field_effects):
                effect.on_after_damage_modifiers(self, ctx)
            if ctx.cancelled:
                if ctx.reason:
                    self.log_public_event(ctx.reason, source=ctx.source, target=ctx.target)
                notify_cancelled()
                return ctx
            if (
                ctx.is_skill
                and not ctx.from_field_effect
                and ctx.target.magic_immunity
                and not ctx.ignore_magic_immunity
            ):
                ctx.cancelled = True
                self.emit_defense_visual_event(
                    source=ctx.source,
                    target=ctx.target,
                    action_name=ctx.action_name,
                    defense_reason="magic_immunity",
                )
                ctx.reason = f"{ctx.target.name} 处于魔免状态。"
                self.log_public_event(ctx.reason, source=ctx.source, target=ctx.target)
                notify_cancelled()
                return ctx
            if (
                not ctx.is_skill
                and not ctx.from_field_effect
                and bool(getattr(ctx.target, "physical_immunity", False))
                and not ctx.ignore_physical_immunity
            ):
                ctx.cancelled = True
                self.emit_defense_visual_event(
                    source=ctx.source,
                    target=ctx.target,
                    action_name=ctx.action_name,
                    defense_reason="physical_immunity",
                )
                ctx.reason = f"{ctx.target.name} 处于物免状态。"
                self.log_public_event(ctx.reason, source=ctx.source, target=ctx.target)
                notify_cancelled()
                return ctx
            if (direct_scoring() and ctx.target.total_shields() > 0
                    and not ctx.ignore_shield and ctx.forecast_shield_pierce_probability > 0):
                from wujiang.tactical.direct_expectation import damage_chance, attack_change
                if ctx.half_ignore_shield:
                    if ctx.raw_damage is None:
                        attack_change(ctx, -1, 1 - ctx.forecast_shield_pierce_probability)
                else:
                    damage_chance(ctx, ctx.forecast_shield_pierce_probability)
                ctx.ignore_shield = True
            if ctx.target.total_shields() > 0:
                if ctx.ignore_shield:
                    protection_before = ctx.target.protection_shields
                    ctx.target.consume_one_shield()
                    ctx.shield_consumed = True
                    ctx.protection_broken = protection_before > 0 and ctx.target.protection_shields == 0
                    self.record_shield_break_summary(ctx.source, ctx.target, ctx.action_name)
                    self.emit_defense_visual_event(
                        source=ctx.source,
                        target=ctx.target,
                        action_name=ctx.action_name,
                        defense_reason="shield_break",
                    )
                    self.log_public_event(
                        f"{ctx.target.name} 的 1 层护盾被【{ctx.action_name}】贯穿并打碎。",
                        source=ctx.source,
                        target=ctx.target,
                    )
                elif ctx.half_ignore_shield:
                    protection_before = ctx.target.protection_shields
                    ctx.target.consume_one_shield()
                    ctx.shield_consumed = True
                    ctx.protection_broken = protection_before > 0 and ctx.target.protection_shields == 0
                    self.record_shield_break_summary(ctx.source, ctx.target, ctx.action_name)
                    if ctx.raw_damage is None:
                        ctx.attack_power = max(0.0, ctx.attack_power - 1)
                    self.emit_defense_visual_event(
                        source=ctx.source,
                        target=ctx.target,
                        action_name=ctx.action_name,
                        defense_reason="shield_half_break",
                    )
                    self.log_public_event(
                        f"{ctx.target.name} 的 1 层护盾被【{ctx.action_name}】半破魔打碎。",
                        source=ctx.source,
                        target=ctx.target,
                    )
                else:
                    ctx.target.consume_one_shield()
                    ctx.shield_consumed = True
                    self.record_shield_break_summary(ctx.source, ctx.target, ctx.action_name)
                    ctx.cancelled = True
                    ctx.modifier_receipts.add("shield_blocked_damage")
                    ctx.reason = f"{ctx.target.name} 的护盾挡下了伤害。"
                    self.emit_defense_visual_event(
                        source=ctx.source,
                        target=ctx.target,
                        action_name=ctx.action_name,
                        defense_reason="shield",
                    )
                    self.log_public_event(ctx.reason, source=ctx.source, target=ctx.target)
                    notify_cancelled()
                    return ctx
            if ctx.target.dodge_charges > 0 and not ctx.cannot_evade:
                ctx.target.dodge_charges -= 1
                ctx.cancelled = True
                ctx.reason = f"{ctx.target.name} 闪避了伤害。"
                self.emit_defense_visual_event(
                    source=ctx.source,
                    target=ctx.target,
                    action_name=ctx.action_name,
                    defense_reason="dodge",
                )
                self.log_public_event(ctx.reason, source=ctx.source, target=ctx.target)
                notify_cancelled()
                return ctx
            for component in self.components_for(ctx.target):
                component.on_final_damage(self, ctx)
            if ctx.cancelled:
                if ctx.reason:
                    self.log_public_event(ctx.reason, source=ctx.source, target=ctx.target)
                notify_cancelled()
                return ctx
            if ctx.target.is_clone:
                ctx.destroyed_as_clone = True
                ctx.raw_damage = 0.0
                ctx.target.current_hp = 0.0
                ctx.target.alive = False
                ctx.target._hell_death_from_damage = bool(ctx.is_skill and not ctx.from_field_effect)
                self.log_public_event(
                    f"{ctx.target.name} 是分身，只要受到伤害就会直接破坏。",
                    source=ctx.source,
                    target=ctx.target,
                )
                for effect in list(self.field_effects):
                    effect.on_after_damage(self, ctx)
                if ctx.source is not None:
                    for component in self.components_for(ctx.source):
                        component.on_after_damage(self, ctx)
                for effect in list(self.field_effects):
                    hook = getattr(effect, "on_after_source_damage", None)
                    if callable(hook):
                        hook(self, ctx)
                for component in self.components_for(ctx.target):
                    component.on_after_damage(self, ctx)
                notify_confirmed_destruction()
                self.cleanup_dead_units()
                return ctx
            defense = ctx.defense_override if ctx.defense_override is not None else ctx.target.stat("defense")
            if direct_scoring():
                from wujiang.tactical.direct_expectation import damage_amount as expected_damage_amount
                damage_amount = expected_damage_amount(self, ctx, defense)
            else:
                damage_amount = self.damage_rule.calculate_damage(ctx.attack_power, defense)
            if ctx.raw_damage is not None:
                damage_amount = ctx.raw_damage
            ctx.raw_damage = ctx.target.damage_fraction_after_limits(damage_amount)
            if ctx.raw_damage > 0:
                for component in self.components_for(ctx.target):
                    component.on_before_hp_loss(self, ctx)
            if direct_scoring():
                from wujiang.tactical.direct_expectation import apply_damage_expectation
                apply_damage_expectation(ctx)
            elif ctx.raw_damage > 0 and ctx.complete_block_chance > 0:
                if forecasting_active():
                    if random.random() < ctx.complete_block_chance:
                        ctx.raw_damage = 0.0
                        ctx.modifier_receipts.add("complete_block")
                elif getattr(self, "_ai_probe_active", False):
                    # Forecast an independent random guard by expectation; do not
                    # let AI inspect the next live random result through rollback.
                    ctx.raw_damage = round(ctx.raw_damage * (1 - ctx.complete_block_chance), 4)
                else:
                    blocked = random.random() < ctx.complete_block_chance
                    self.log_public_event(
                        f"{ctx.target.name} 的【{ctx.complete_block_label or '格挡'}】判定：{'完全格挡' if blocked else '未格挡'}。",
                        source=ctx.source, target=ctx.target,
                    )
                    if blocked:
                        ctx.raw_damage = 0.0
                        ctx.modifier_receipts.add("complete_block")
            old_hp = ctx.target.current_hp
            if ctx.raw_damage >= old_hp and old_hp > 0:
                if direct_scoring():
                    retained = []
                    certain_survival = False
                    for component in self.components_for(ctx.target):
                        previous_probability = ctx.forecast_kill_probability
                        value = component.lethal_damage_remaining_hp(self, ctx)
                        if value is not None:
                            retained.append(value)
                            certain_survival |= ctx.forecast_kill_probability == previous_probability
                    if certain_survival:
                        ctx.forecast_kill_probability = 0.0
                else:
                    retained = [value for component in self.components_for(ctx.target)
                                if (value := component.lethal_damage_remaining_hp(self, ctx)) is not None]
                if retained:
                    remaining_hp = min(old_hp, max(0.0, max(retained)))
                    ctx.raw_damage = max(0.0, old_hp - remaining_hp)
            ctx.target.take_damage_fraction(ctx.raw_damage)
            actual_damage = round(max(0.0, old_hp - ctx.target.current_hp), 4)
            ctx.actual_damage = actual_damage
            if direct_scoring():
                from wujiang.tactical.direct_expectation import record_death_probability
                record_death_probability(ctx)
            if actual_damage > 0:
                for observer in list(self.all_units()):
                    if observer.alive and observer.position is not None and not observer.banished:
                        for component in self.components_for(observer):
                            component.on_battle_damage(self, ctx)
            self.record_damage_summary(ctx, actual_damage)
            self.log_public_event(
                f"{ctx.target.name} 受到 {ctx.raw_damage} 点伤害。",
                source=ctx.source,
                target=ctx.target,
            )
            for effect in list(self.field_effects):
                effect.on_after_damage(self, ctx)
            if ctx.source is not None:
                for component in self.components_for(ctx.source):
                    component.on_after_damage(self, ctx)
            for effect in list(self.field_effects):
                hook = getattr(effect, "on_after_source_damage", None)
                if callable(hook):
                    hook(self, ctx)
            if "bp_soft_persimmon" not in ctx.tags:
                for component in self.components_for(ctx.target):
                    component.on_after_damage(self, ctx)
            if ctx.is_skill and not ctx.cancelled and ctx.actual_damage > 0 and ctx.target.alive:
                self.record_skill_effect(ctx.target)
            if not ctx.target.alive:
                ctx.target._hell_death_from_damage = bool(ctx.is_skill and not ctx.from_field_effect)
                self.record_defeat_summary(ctx.source, ctx.target, ctx.action_name)
            notify_confirmed_destruction()
            self.cleanup_dead_units()
            if direct_scoring():
                from wujiang.tactical.direct_expectation import finish_damage_expectation
                finish_damage_expectation(ctx)
            return ctx


    def expire_chain_temporary_statuses(self) -> None:
        for unit in self.all_units():
            unit.temporary_shields = 0
            for status in list(unit.statuses):
                if getattr(status, "expire_with_chain", False):
                    unit.remove_status(status, self)


    def heal(self, ctx: HealContext) -> HealContext:
        ctx.target = self.effect_recipient(ctx.target)
        with self.suppress_logs_for_stealth(ctx.target):
            for effect in list(self.field_effects):
                effect.on_before_heal(self, ctx)
            if ctx.source is not None:
                for component in list(ctx.source.iter_components()):
                    component.on_before_heal(self, ctx)
            for component in list(ctx.target.iter_components()):
                component.on_before_heal(self, ctx)
            if ctx.cancelled:
                return ctx
            if ctx.target.cannot_heal:
                ctx.cancelled = True
                ctx.reason = f"{ctx.target.name} 当前无法回复。"
                return ctx
            action = self.resolving_action
            if (action is not None and action.action_type in {"skill", "reaction_skill", "skill_effect"}
                    and ctx.source is not None and action.actor_id == ctx.source.unit_id and ctx.effect_source is None
                    and not action.payload.get("from_field_effect")
                    and self.destroy_clone_for_skill_effect(ctx.target, source=ctx.source, action_name=ctx.action_name)):
                ctx.cancelled = True
                return ctx
            old_hp = ctx.target.current_hp
            exempt = any(component.permits_immune_heal(ctx) for component in ctx.target.iter_components())
            ctx.target._inside_battle_heal = True
            try:
                ctx.target.heal_fraction(ctx.amount, ignore_effect_immunity=exempt)
            finally:
                ctx.target.__dict__.pop("_inside_battle_heal", None)
            gained = round(ctx.target.current_hp - old_hp, 4)
            ctx.actual_heal = gained
            if (gained > 0 and action is not None
                    and action.action_type in {"skill", "reaction_skill", "skill_effect"}
                    and not action.payload.get("from_field_effect")):
                self.record_skill_effect(ctx.target)
            self.record_heal_summary(ctx, gained)
            self.log_public_event(
                f"{ctx.target.name} 回复了 {gained} 点生命。",
                source=ctx.source,
                target=ctx.target,
            )
            for effect in list(self.field_effects):
                effect.on_after_heal(self, ctx)
            if ctx.source is not None:
                for component in list(ctx.source.iter_components()):
                    component.on_after_heal(self, ctx)
            for component in list(ctx.target.iter_components()):
                component.on_after_heal(self, ctx)
            return ctx


    def basic_attack(self, actor: Unit, target: Unit) -> None:
        return self.basic_attack_with_payload(actor, target, None)


    def basic_attack_with_payload(
        self,
        actor: Unit,
        target: Unit,
        payload: Optional[dict[str, Any]] = None,
    ) -> None:
        from wujiang.tactical.engine.core import (
            ActionError,
        )
        if not actor.can_take_turn_actions(self):
            raise ActionError("这个单位当前不能行动。")
        resolved_payload = self.resolved_basic_attack_payload(actor, payload)
        attack_cost = int(resolved_payload.get("attack_cost", 1))
        ok, reason = self.basic_attack_resource_allowed(actor, resolved_payload)
        if not ok:
            raise ActionError(reason)
        ignore_stealth = self.attack_ignores_stealth(actor, target)
        ok, reason = self.attack_target_allowed(actor, target, ignore_stealth=ignore_stealth, payload=resolved_payload)
        if not ok:
            raise ActionError(reason)
        resolved_payload.setdefault("target_unit_id", target.unit_id)
        actor.attacks_used += attack_cost
        actor.actions_taken_this_turn.append("attack")
        if not resolved_payload.get("queued_resolution"):
            if not all(component.before_basic_attack_resolution(self, actor, resolved_payload) for component in list(actor.iter_components())):
                resolved_payload["basic_attack_cancelled"] = True
                actor.consume_attack_attempt_buffs(self)
                actor.notify_basic_attack_finished(self, resolved_payload, [], missed=True)
                return
        damage_ctx = self.resolve_attack_damage(
            actor,
            target,
            action_name=str(resolved_payload.get("attack_name") or "普攻"),
            payload=resolved_payload,
        )
        actor.notify_basic_attack_finished(
            self,
            resolved_payload,
            [damage_ctx] if damage_ctx is not None else [],
            missed=damage_ctx is None,
        )
        self.check_win_condition()


    def basic_area_attack_with_payload(
        self,
        actor: Unit,
        payload: Optional[dict[str, Any]] = None,
        cells: Optional[list[Position]] = None,
    ) -> None:
        from wujiang.tactical.engine.core import (
            ActionError,
            Position,
        )
        if not actor.can_take_turn_actions(self):
            raise ActionError("这个单位当前不能行动。")
        resolved_payload = self.resolved_basic_attack_payload(actor, payload)
        area_cells = cells if cells is not None else self.basic_attack_area_cells_for_payload(actor, resolved_payload)
        if area_cells is None:
            raise ActionError("这次攻击需要指定攻击区域。")
        attack_cost = int(resolved_payload.get("attack_cost", 1))
        ok, reason = self.basic_attack_resource_allowed(actor, resolved_payload)
        if not ok:
            raise ActionError(reason)
        block_reason = self.shared_stealth_action_block_reason(actor)
        if block_reason:
            raise ActionError(block_reason)
        if actor.cannot_attack:
            raise ActionError(f"{actor.name} 当前不能攻击。")
        for effect in list(self.field_effects):
            ok, reason = effect.can_attack_target(self, actor, actor)
            if not ok:
                raise ActionError(reason)
        attack_payload = dict(resolved_payload)
        attack_payload["attack_cells"] = [cell.to_dict() for cell in area_cells]
        actor.attacks_used += attack_cost
        actor.actions_taken_this_turn.append("attack")
        for component in list(actor.iter_components()):
            if component.resolve_empty_basic_attack(self, actor, attack_payload):
                actor.consume_attack_attempt_buffs(self)
                actor.notify_basic_attack_finished(self, attack_payload, [], missed=True)
                self.check_win_condition()
                return
        affects_allies = all(
            component.basic_attack_area_affects_allies(self, actor, attack_payload)
            for component in actor.iter_components()
        )
        forced_allied_target_id = str(attack_payload.get("forced_allied_attack_target_id") or "")
        impact = None
        raw_impact = attack_payload.get("impact_cell")
        if isinstance(raw_impact, dict) and raw_impact.get("x") is not None and raw_impact.get("y") is not None:
            impact = Position(int(raw_impact["x"]), int(raw_impact["y"]))
        from wujiang.tactical.engine.siege import is_siege_structure, structure_hit_by_impact

        targets = []
        for unit in self.effect_units_at_cells(area_cells):
            if (
                not affects_allies
                and unit.player_id == actor.player_id
                and unit.unit_id != forced_allied_target_id
            ):
                continue
            if attack_payload.get("structures_need_direct_hit") and is_siege_structure(unit):
                if not structure_hit_by_impact(self, unit, impact):
                    continue
            targets.append(unit)
        if not targets:
            actor.consume_attack_attempt_buffs(self)
            attack_name = str(attack_payload.get("attack_name") or "普攻")
            self.log(f"{actor.name} 的【{attack_name}】没有命中有效目标。")
            actor.notify_basic_attack_finished(self, attack_payload, [], missed=True)
            return
        attack_name = str(attack_payload.get("attack_name") or "普攻")
        damage_contexts: list[DamageContext] = []
        for target in targets:
            damage_ctx = self.resolve_attack_damage(
                actor,
                target,
                action_name=attack_name,
                tags={"area_attack"},
                payload=attack_payload,
            )
            if damage_ctx is not None:
                damage_contexts.append(damage_ctx)
        actor.notify_basic_attack_finished(self, attack_payload, damage_contexts, missed=not damage_contexts)
        self.check_win_condition()


    def resolve_attack_damage(
        self,
        actor: Unit,
        target: Unit,
        *,
        action_name: str,
        tags: Optional[set[str]] = None,
        payload: Optional[dict[str, Any]] = None,
    ) -> Optional[DamageContext]:
        from wujiang.tactical.engine.core import (
            DamageContext,
            HealContext,
        )
        with self.suppress_logs_for_stealth(target):
            attack_payload = dict(payload or {})
            if tags and "area_attack" not in tags:
                attack_payload["reaction_attack"] = True
            resolved_payload = self.resolved_basic_attack_payload(actor, attack_payload)
            attack_tags = {"attack"}
            if tags:
                attack_tags.update(tags)
            attack_tags.update(set(resolved_payload.get("attack_tags", [])))
            if target.player_id == actor.player_id:
                supported = False
                for component in actor.iter_components():
                    supported = component.resolve_allied_basic_support(self, actor, target) or supported
                if supported:
                    actor.consume_attack_attempt_buffs(self)
                    return DamageContext(source=actor, target=target, attack_power=0, is_skill=False,
                                         action_name="友方普攻支援", raw_damage=0, cancelled=True, tags=attack_tags)
            healing = self.allied_basic_heal_amount(actor, target)
            if target.player_id == actor.player_id and (healing > 0 or resolved_payload.get("allied_heal_attack")):
                if healing > 0:
                    self.heal(HealContext(source=actor, target=target, amount=healing, action_name="攻击己方加血"))
                else:
                    self.log("原声明的友方治疗因共享资格失效而落空。")
                actor.consume_attack_attempt_buffs(self)
                return DamageContext(source=actor, target=target, attack_power=0, is_skill=False,
                                     action_name="攻击己方加血", raw_damage=0, cancelled=True, tags=attack_tags)
            target_ctx = self.validate_target(
                actor,
                target,
                action_name=action_name,
                is_skill=False,
                is_hostile=True,
                ignore_targeting_restrictions=bool(resolved_payload.get("area_attack")),
                ignore_magic_immunity=bool(resolved_payload.get("ignore_magic_immunity")),
                ignore_physical_immunity=bool(resolved_payload.get("ignore_physical_immunity")),
                resolve_defenses=False,
                cannot_evade=bool(resolved_payload.get("cannot_evade")),
                tags=attack_tags,
            )
            if target_ctx.cancelled:
                self.log_public_event(target_ctx.reason, source=actor, target=target)
                actor.consume_attack_attempt_buffs(self)
                return None
            attack_power = actor.stat("attack") + float(resolved_payload.get("attack_bonus", 0.0) or 0.0)
            if resolved_payload.get("attack_power_override") is not None:
                attack_power = float(resolved_payload["attack_power_override"])
            if resolved_payload.get("siege_shell"):
                attack_tags.add("siege_shell")
            damage_ctx = DamageContext(
                source=actor,
                target=target,
                attack_power=attack_power,
                is_skill=False,
                action_name=action_name,
                ignore_shield=bool(target_ctx.ignore_shield or resolved_payload.get("ignore_shield")),
                half_ignore_shield=bool(target_ctx.half_ignore_shield or resolved_payload.get("half_ignore_shield")),
                ignore_magic_immunity=bool(target_ctx.ignore_magic_immunity or resolved_payload.get("ignore_magic_immunity")),
                ignore_physical_immunity=bool(target_ctx.ignore_physical_immunity or resolved_payload.get("ignore_physical_immunity")),
                siege_shell=bool(resolved_payload.get("siege_shell")),
                cannot_evade=target_ctx.cannot_evade,
                tags=set(target_ctx.tags),
                area_cell_hits=1 if resolved_payload.get("single_hit_per_recipient") else max(1, self.unit_hit_count_for_cells(target, self.payload_positions(resolved_payload, "attack_cells"))),
            )
            self.resolve_damage(damage_ctx)
            return damage_ctx


    def use_skill(self, actor: Unit, skill_code: str, payload: dict[str, Any]) -> None:
        from wujiang.tactical.engine.core import (
            ActionError,
            ActionMiss,
        )
        skill = actor.get_skill(skill_code)
        prepaid = bool(payload.get("resources_prepaid"))
        if not prepaid and not actor.can_take_turn_actions(self):
            raise ActionError("这个单位当前不能行动。")
        if not prepaid:
            ok, reason = skill.can_use(self, actor, payload)
            if not ok:
                raise ActionError(reason)
            self.prepay_skill_resources(skill, actor, payload)
            if not actor.alive:
                self.cleanup_dead_units()
                return
        if (
            payload.get("queued_resolution")
            and self.declared_target_position(payload) is not None
            and payload.get("target_unit_id")
            and not payload.get("resolved_target_unit_id")
            and skill.target_mode in {"ally", "enemy", "unit"}
            and not skill.target_unit_is_anchor
        ):
            self.log(f"【{skill.name}】落在原定格上，没有命中有效目标。")
            skill.on_target_missed(self, actor, payload)
            skill.finalize_use(self, actor)
            actor.actions_taken_this_turn.append(f"skill:{skill.code}")
            self.check_win_condition()
            return
        target_id = None if skill.target_unit_is_anchor else (payload.get("resolved_target_unit_id") or payload.get("target_unit_id"))
        if target_id:
            target = self.get_unit(target_id)
            self.require_selectable_unit(
                target,
                actor=actor,
                action_name=skill.name,
                ignore_stealth=skill.ignores_stealth_for_payload(self, actor, payload),
                queued_resolution=bool(payload.get("queued_resolution")),
            )
            if (skill.target_mode in {"ally", "enemy", "unit"} and skill.requires_direct_unit_target_line
                    and not self.is_declared_resolution(actor, payload, {"skill", "reaction_skill"})):
                self.require_unit_target_in_range_and_line(
                    actor,
                    target,
                    skill.direct_unit_target_range(self, actor, payload),
                    action_name=skill.name,
                )
        execution_turn = self.turn_number
        try:
            skill.execute(self, actor, payload)
        except ActionMiss as exc:
            self.log(str(exc) or f"【{skill.name}】落在原定格上，没有命中有效目标。")
            skill.on_target_missed(self, actor, payload)
        except ActionError as exc:
            if payload.get("queued_resolution") and payload.get("declared_target_x") is not None:
                self.log(f"【{skill.name}】落在原定格上，但没有命中有效目标。")
            else:
                raise exc
        skill.finalize_use(self, actor)
        if self.turn_number == execution_turn:
            actor.actions_taken_this_turn.append(f"skill:{skill.code}")
        self.check_win_condition()


