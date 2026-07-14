from __future__ import annotations

import copy
import random
from dataclasses import dataclass, field
from typing import Any

from wujiang.strategy.models import (
    City,
    EventLogEntry,
    Faction,
    ScheduledConsequence,
    StoryEvent,
    StrategyError,
    WorldState,
)


@dataclass(frozen=True, slots=True)
class StoryChoiceDefinition:
    choice_id: str
    label: str
    preview: str
    outcome: str
    effects: dict[str, int] = field(default_factory=dict)
    delayed_effect_id: str = ""
    delayed_months: int = 0
    delayed_description: str = ""
    ai_score: int = 0
    hidden: bool = False


@dataclass(frozen=True, slots=True)
class StoryTemplateDefinition:
    template_id: str
    title: str
    category: str
    description: str
    default_choice_id: str
    choices: tuple[StoryChoiceDefinition, ...]


STORY_TEMPLATES = (
    StoryTemplateDefinition(
        template_id="border_deserters",
        title="边境逃兵",
        category="border",
        description="一队敌方逃兵越过边境，请求在城中藏身。他们熟悉对岸道路，但身份真假难辨。",
        default_choice_id="turn_away",
        choices=(
            StoryChoiceDefinition(
                "return_deserters", "交还逃兵", "获得 40 金钱，但本城支持度 -2；下月得到一份边境情报。",
                "逃兵被送回对岸，敌方支付了赎金，城内有人对此不满。",
                {"faction_money": 40, "support": -2}, "returned_intel", 1, "交还逃兵换来的边境情报即将送达。", 3,
            ),
            StoryChoiceDefinition(
                "recruit_deserters", "秘密收编", "消耗 60 粮食，兵力 +120、支持度 -3；两个月后可能暴露内应。",
                "逃兵被打散编入守军，短期兵力上升，但城内流言四起。",
                {"city_food": -60, "city_troops": 120, "support": -3}, "deserter_infiltration", 2, "被收编逃兵中的可疑人物开始行动。", 1,
            ),
            StoryChoiceDefinition(
                "shelter_families", "庇护家眷", "消耗 50 金钱，支持度 +6；下月获得向导和少量兵力。",
                "城门向逃兵家眷开放，这一决定赢得了民众认同。",
                {"city_money": -50, "support": 6}, "deserter_guides", 1, "被庇护者愿意为你的军队带路。", 5,
            ),
            StoryChoiceDefinition(
                "turn_away", "拒绝接纳", "支持度 -4；下月边境可能遭到袭扰。",
                "逃兵被赶出城外，边民认为统治者过于冷酷。",
                {"support": -4}, "deserter_raiders", 1, "被拒绝的逃兵在边境重新集结。", -3, True,
            ),
        ),
    ),
    StoryTemplateDefinition(
        template_id="grain_petition",
        title="饥民请愿",
        category="city",
        description="粮价持续上涨，大批市民聚集在政厅外，要求立即开放粮仓。",
        default_choice_id="refuse_petition",
        choices=(
            StoryChoiceDefinition(
                "open_granary", "全面开仓", "消耗 150 粮食，支持度 +10；两个月后人口增长。",
                "粮仓向全城开放，饥民得到救济，市集重新恢复秩序。",
                {"city_food": -150, "support": 10}, "population_recovery", 2, "获救的家庭开始在城中安居。", 6,
            ),
            StoryChoiceDefinition(
                "ration_grain", "限量配给", "消耗 60 粮食，支持度 +4。",
                "有限配给缓解了最紧急的缺粮问题。",
                {"city_food": -60, "support": 4}, ai_score=4,
            ),
            StoryChoiceDefinition(
                "refuse_petition", "拒绝请愿", "支持度 -8；下月不满继续扩大。",
                "卫兵驱散了请愿人群，街巷中的不满迅速蔓延。",
                {"support": -8}, "petition_unrest", 1, "被驱散的请愿者正在秘密串联。", -5, True,
            ),
        ),
    ),
    StoryTemplateDefinition(
        template_id="ether_flare",
        title="以太异象",
        category="world_side",
        description="城市上空出现稳定的以太辉光。学者认为它可能是能源，也可能是一场灾难的前兆。",
        default_choice_id="leave_unstable",
        choices=(
            StoryChoiceDefinition(
                "stabilize_flare", "投入稳定仪式", "消耗势力 30 以太；下月获得 80 城市以太和支持度。",
                "仪式压制了异象的波动，学者开始收集稳定的以太结晶。",
                {"faction_ether": -30}, "stable_ether_harvest", 1, "稳定后的以太结晶即将成熟。", 6,
            ),
            StoryChoiceDefinition(
                "harvest_flare", "立即采集", "城市以太 +50、支持度 -4；下月可能发生以太爆炸。",
                "采集队强行抽取异象能量，仓库迅速充盈，但天空开始出现裂纹。",
                {"city_ether": 50, "support": -4}, "ether_explosion", 1, "过度采集造成的以太裂纹正在扩大。", 2,
            ),
            StoryChoiceDefinition(
                "seal_flare", "封锁现场", "支持度 +2，不获得资源。",
                "军队封锁了异象区域，城市避免了冒险，也错过了眼前收益。",
                {"support": 2}, ai_score=3,
            ),
            StoryChoiceDefinition(
                "leave_unstable", "放任不管", "下月异象失控，城防与支持度下降。",
                "没有人负责处理异象，危险能量继续在城市上空积聚。",
                {}, "ether_explosion", 1, "无人处理的以太异象即将失控。", -6, True,
            ),
        ),
    ),
    StoryTemplateDefinition(
        template_id="guild_dispute",
        title="行会争端",
        category="city",
        description="商人行会与工匠团体因税额和运输权爆发冲突，双方都要求统治者公开表态。",
        default_choice_id="let_strike_spread",
        choices=(
            StoryChoiceDefinition(
                "favor_merchants", "支持商人", "金钱 +100、支持度 -4；下月贸易带来 80 粮食。",
                "商人获得运输特权，税库立即充实，但工匠开始罢工。",
                {"city_money": 100, "support": -4}, "merchant_grain", 1, "商路上的粮队即将抵达。", 3,
            ),
            StoryChoiceDefinition(
                "favor_workers", "支持工匠", "消耗 60 金钱，支持度 +6；两个月后人口 +100。",
                "政厅限制了行会特权，工匠们承诺扩建居住区。",
                {"city_money": -60, "support": 6}, "worker_settlement", 2, "工匠家眷即将迁入城市。", 5,
            ),
            StoryChoiceDefinition(
                "mediate_guilds", "出资调停", "消耗势力 30 金钱，支持度 +3。",
                "双方接受了政厅主持的妥协方案。",
                {"faction_money": -30, "support": 3}, ai_score=4,
            ),
            StoryChoiceDefinition(
                "let_strike_spread", "置之不理", "损失 120 金钱、支持度 -4。",
                "争端演变为全城罢工，税收与秩序同时受损。",
                {"city_money": -120, "support": -4}, ai_score=-5, hidden=True,
            ),
        ),
    ),
)

STORY_TEMPLATES_BY_ID = {template.template_id: template for template in STORY_TEMPLATES}
CONSEQUENCE_EFFECTS: dict[str, tuple[dict[str, int], str]] = {
    "returned_intel": ({"defense": 1}, "边境情报帮助守军修补了防线，城防 +1。"),
    "deserter_infiltration": ({"city_troops": -50, "support": -2}, "内应暴露，守军损失 50，支持度下降。"),
    "deserter_guides": ({"city_troops": 80}, "熟悉边境的向导带来志愿者，兵力 +80。"),
    "deserter_raiders": ({"city_troops": -40, "support": -2}, "逃兵化为边境匪徒，守军与民心受损。"),
    "population_recovery": ({"city_population": 120, "support": 2}, "获救家庭安居，人口 +120，支持度上升。"),
    "petition_unrest": ({"support": -5}, "请愿者的秘密串联扩大，支持度再次下降。"),
    "stable_ether_harvest": ({"city_ether": 80, "support": 2}, "稳定结晶成熟，以太 +80，支持度上升。"),
    "ether_explosion": ({"defense": -1, "support": -4}, "以太异象爆炸，城防 -1，支持度下降。"),
    "merchant_grain": ({"city_food": 80}, "商路粮队抵达，粮食 +80。"),
    "worker_settlement": ({"city_population": 100, "support": 2}, "工匠家眷迁入，人口 +100，支持度上升。"),
}


def _clone_world(world: WorldState) -> WorldState:
    return WorldState.from_dict(copy.deepcopy(world.to_dict()))


def _template(template_id: str) -> StoryTemplateDefinition:
    template = STORY_TEMPLATES_BY_ID.get(str(template_id))
    if template is None:
        raise StrategyError("战略事件模板不存在。")
    return template


def _choice(template: StoryTemplateDefinition, choice_id: str) -> StoryChoiceDefinition:
    choice = next((item for item in template.choices if item.choice_id == str(choice_id)), None)
    if choice is None:
        raise StrategyError("战略事件选项不存在。")
    return choice


def _city(world: WorldState, city_id: str) -> City:
    city = next((item for item in world.cities if item.city_id == str(city_id)), None)
    if city is None:
        raise StrategyError("战略事件城市不存在。")
    return city


def _faction(world: WorldState, faction_id: str) -> Faction:
    faction = next((item for item in world.factions if item.faction_id == str(faction_id)), None)
    if faction is None:
        raise StrategyError("战略事件势力不存在。")
    return faction


def _choice_unavailable_reason(world: WorldState, event: StoryEvent, choice: StoryChoiceDefinition) -> str:
    city = _city(world, event.city_id)
    faction = _faction(world, event.faction_id)
    stores = {
        "city_food": (city.resources.food, "城市粮食"),
        "city_money": (city.resources.money, "城市金钱"),
        "city_ether": (city.resources.ether, "城市以太"),
        "city_troops": (city.resources.troops, "城市兵力"),
        "faction_money": (faction.resources.money, "势力金钱"),
        "faction_ether": (faction.resources.ether, "势力以太"),
    }
    for key, amount in choice.effects.items():
        if amount >= 0 or key not in stores:
            continue
        current, label = stores[key]
        if current < -amount:
            return f"{label}不足，需要 {-amount}。"
    return ""


def _apply_effects(world: WorldState, event: StoryEvent, effects: dict[str, int]) -> None:
    city = _city(world, event.city_id)
    faction = _faction(world, event.faction_id)
    for key, amount in effects.items():
        delta = int(amount)
        if key == "city_food":
            city.resources.food = max(0, city.resources.food + delta)
        elif key == "city_money":
            city.resources.money = max(0, city.resources.money + delta)
        elif key == "city_population":
            city.resources.population = max(0, city.resources.population + delta)
        elif key == "city_ether":
            city.resources.ether = max(0, city.resources.ether + delta)
        elif key == "city_troops":
            city.resources.troops = max(0, city.resources.troops + delta)
        elif key == "faction_money":
            faction.resources.money = max(0, faction.resources.money + delta)
        elif key == "faction_ether":
            faction.resources.ether = max(0, faction.resources.ether + delta)
        elif key == "support":
            city.support_by_faction[event.faction_id] = max(
                0, min(100, city.support_by_faction.get(event.faction_id, 50) + delta)
            )
        elif key == "defense":
            city.defense = max(0, city.defense + delta)


def validate_story_event_choice(
    world: WorldState,
    *,
    faction_id: str,
    event_id: str,
    choice_id: str,
) -> StoryChoiceDefinition:
    event = next((item for item in world.story_events if item.event_id == str(event_id)), None)
    if event is None:
        raise StrategyError("战略事件不存在。")
    if event.faction_id != str(faction_id):
        raise StrategyError("只能处理本势力的战略事件。")
    if event.status != "pending":
        raise StrategyError("这个战略事件已经处理。")
    choice = _choice(_template(event.template_id), choice_id)
    if choice.hidden:
        raise StrategyError("该事件选项不能主动选择。")
    reason = _choice_unavailable_reason(world, event, choice)
    if reason:
        raise StrategyError(reason)
    return choice


def _resolve_story_event_in_place(
    world: WorldState,
    event: StoryEvent,
    choice: StoryChoiceDefinition,
    *,
    automatic: bool,
) -> None:
    _apply_effects(world, event, choice.effects)
    event.status = "expired" if automatic else "resolved"
    event.choice_id = choice.choice_id
    event.resolved_month = world.current_month
    event.outcome_summary = choice.outcome
    world.memory_tags.append(f"story_choice:{event.event_id}:{choice.choice_id}")
    if choice.delayed_effect_id:
        consequence_id = f"{event.event_id}:{choice.delayed_effect_id}"
        if not any(item.consequence_id == consequence_id for item in world.scheduled_consequences):
            world.scheduled_consequences.append(
                ScheduledConsequence(
                    consequence_id=consequence_id,
                    source_event_id=event.event_id,
                    effect_id=choice.delayed_effect_id,
                    faction_id=event.faction_id,
                    city_id=event.city_id,
                    due_month=world.current_month + max(1, choice.delayed_months),
                    description=choice.delayed_description,
                )
            )
    world.event_log.append(
        EventLogEntry(
            month=world.current_month,
            category="story_event_ignored" if automatic else "story_event_choice",
            message=f"{_template(event.template_id).title}：{choice.outcome}",
            related_ids=[event.event_id, event.faction_id, event.city_id, choice.choice_id],
        )
    )


def resolve_story_event(
    world: WorldState,
    *,
    faction_id: str,
    event_id: str,
    choice_id: str,
) -> WorldState:
    choice = validate_story_event_choice(
        world,
        faction_id=faction_id,
        event_id=event_id,
        choice_id=choice_id,
    )
    next_world = _clone_world(world)
    event = next(item for item in next_world.story_events if item.event_id == str(event_id))
    _resolve_story_event_in_place(next_world, event, choice, automatic=False)
    next_world.validate()
    return next_world


def _border_city(world: WorldState, faction_id: str) -> City | None:
    cities_by_node = {city.node_id: city for city in world.cities}
    node_by_id = {node.node_id: node for node in world.nodes}
    candidates: list[City] = []
    for city in world.cities:
        if city.owner_faction_id != faction_id:
            continue
        node = node_by_id.get(city.node_id)
        if node and any(
            cities_by_node.get(node_id) is not None
            and cities_by_node[node_id].owner_faction_id != faction_id
            for node_id in node.connected_node_ids
        ):
            candidates.append(city)
    return min(candidates, key=lambda city: city.city_id, default=None)


def _event_city_for_template(world: WorldState, faction_id: str, template_id: str) -> City | None:
    owned = [city for city in world.cities if city.owner_faction_id == faction_id]
    if not owned:
        return None
    if template_id == "border_deserters":
        return _border_city(world, faction_id)
    if template_id == "grain_petition":
        return min(owned, key=lambda city: (city.support_by_faction.get(faction_id, 50), city.resources.food, city.city_id))
    if template_id == "ether_flare":
        return max(owned, key=lambda city: (city.resources.ether, city.city_id))
    return max(owned, key=lambda city: (city.resources.population, city.city_id))


def _eligible_templates(world: WorldState, faction_id: str) -> list[StoryTemplateDefinition]:
    recent = {
        event.template_id
        for event in world.story_events
        if event.faction_id == faction_id and event.opened_month >= world.current_month - 2
    }
    candidates = [
        template
        for template in STORY_TEMPLATES
        if template.template_id not in recent and _event_city_for_template(world, faction_id, template.template_id) is not None
    ]
    if candidates:
        return candidates
    return [
        template
        for template in STORY_TEMPLATES
        if _event_city_for_template(world, faction_id, template.template_id) is not None
    ]


def open_monthly_story_events(world: WorldState) -> WorldState:
    next_world = _clone_world(world)
    for faction in sorted(next_world.factions, key=lambda item: item.faction_id):
        if any(event.faction_id == faction.faction_id and event.status == "pending" for event in next_world.story_events):
            continue
        candidates = _eligible_templates(next_world, faction.faction_id)
        if not candidates:
            continue
        rng = random.Random(f"{next_world.seed}:{next_world.current_month}:{faction.faction_id}:story")
        template = candidates[rng.randrange(len(candidates))]
        city = _event_city_for_template(next_world, faction.faction_id, template.template_id)
        if city is None:
            continue
        event_id = f"story_{next_world.current_month}_{faction.faction_id}_{template.template_id}"
        if any(event.event_id == event_id for event in next_world.story_events):
            continue
        next_world.story_events.append(
            StoryEvent(
                event_id=event_id,
                template_id=template.template_id,
                faction_id=faction.faction_id,
                city_id=city.city_id,
                opened_month=next_world.current_month,
            )
        )
        next_world.event_log.append(
            EventLogEntry(
                month=next_world.current_month,
                category="story_event_opened",
                message=f"{faction.name}在{city.name}遇到事件：{template.title}。",
                related_ids=[event_id, faction.faction_id, city.city_id],
            )
        )
    next_world.validate()
    return next_world


def advance_story_events(world: WorldState) -> WorldState:
    next_world = _clone_world(world)
    for event in next_world.story_events:
        if event.status != "pending" or event.opened_month >= next_world.current_month:
            continue
        template = _template(event.template_id)
        default_choice = _choice(template, template.default_choice_id)
        _resolve_story_event_in_place(next_world, event, default_choice, automatic=True)

    for consequence in next_world.scheduled_consequences:
        if consequence.status != "pending" or consequence.due_month > next_world.current_month:
            continue
        event = next((item for item in next_world.story_events if item.event_id == consequence.source_event_id), None)
        effect = CONSEQUENCE_EFFECTS.get(consequence.effect_id)
        if event is None or effect is None:
            consequence.status = "resolved"
            continue
        effects, summary = effect
        _apply_effects(next_world, event, effects)
        consequence.status = "resolved"
        next_world.memory_tags.append(f"story_consequence:{consequence.consequence_id}")
        next_world.event_log.append(
            EventLogEntry(
                month=next_world.current_month,
                category="story_consequence",
                message=summary,
                related_ids=[consequence.consequence_id, consequence.source_event_id, consequence.city_id],
            )
        )
    next_world = open_monthly_story_events(next_world)
    next_world.validate()
    return next_world


def pending_story_event_for_faction(world: WorldState, faction_id: str) -> StoryEvent | None:
    return next(
        (
            event
            for event in world.story_events
            if event.faction_id == str(faction_id) and event.status == "pending"
        ),
        None,
    )


def choose_ai_story_choice(world: WorldState, event: StoryEvent) -> StoryChoiceDefinition | None:
    template = _template(event.template_id)
    available = [
        choice
        for choice in template.choices
        if not choice.hidden and not _choice_unavailable_reason(world, event, choice)
    ]
    return max(available, key=lambda choice: (choice.ai_score, choice.choice_id), default=None)


def story_events_public(world: WorldState) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for event in sorted(world.story_events, key=lambda item: (item.opened_month, item.event_id), reverse=True):
        template = _template(event.template_id)
        item = event.to_dict()
        item.update(
            {
                "title": template.title,
                "category": template.category,
                "description": template.description,
                "deadline_month": event.opened_month,
                "choices": [],
            }
        )
        if event.status == "pending":
            for choice in template.choices:
                if choice.hidden:
                    continue
                reason = _choice_unavailable_reason(world, event, choice)
                item["choices"].append(
                    {
                        "id": choice.choice_id,
                        "label": choice.label,
                        "preview": choice.preview,
                        "enabled": not bool(reason),
                        "disabled_reason": reason,
                        "command_cost": 1,
                    }
                )
        payload.append(item)
    return payload


def scheduled_consequences_public(world: WorldState) -> list[dict[str, Any]]:
    return [
        item.to_dict()
        for item in sorted(world.scheduled_consequences, key=lambda consequence: (consequence.due_month, consequence.consequence_id))
        if item.status == "pending"
    ]
