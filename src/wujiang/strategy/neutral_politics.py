from __future__ import annotations

from typing import Any

from wujiang.strategy.models import City, Faction, WorldState
from wujiang.strategy.monthly_cycle import forecast_city_month


def _relation_band(score: int) -> tuple[str, str, str]:
    if score <= -60:
        return "hostile", "敌视", "拒绝接近"
    if score <= -20:
        return "wary", "戒备", "谨慎防备"
    if score < 20:
        return "neutral", "中立", "公事往来"
    if score < 60:
        return "friendly", "友好", "愿意交涉"
    return "trusted", "信赖", "优先合作"


def _city_for_neutral(world: WorldState, faction: Faction) -> City | None:
    return next((city for city in world.cities if city.owner_faction_id == faction.faction_id), None)


def _adjacent_major_strengths(world: WorldState, city: City) -> list[tuple[Faction, int]]:
    node = next((item for item in world.nodes if item.node_id == city.node_id), None)
    adjacent_nodes = set(node.connected_node_ids if node else [])
    faction_by_id = {faction.faction_id: faction for faction in world.factions}
    strengths: dict[str, int] = {}
    for adjacent in world.cities:
        if adjacent.node_id not in adjacent_nodes:
            continue
        owner = faction_by_id.get(adjacent.owner_faction_id)
        if owner is None or owner.is_neutral_city_state:
            continue
        strengths[owner.faction_id] = strengths.get(owner.faction_id, 0) + adjacent.resources.troops + adjacent.defense * 40
    return sorted(
        ((faction_by_id[faction_id], strength) for faction_id, strength in strengths.items()),
        key=lambda item: (-item[1], item[0].faction_id),
    )


def neutral_city_state_profile(world: WorldState, neutral_faction_id: str) -> dict[str, Any]:
    faction = next(
        (item for item in world.factions if item.faction_id == neutral_faction_id and item.is_neutral_city_state),
        None,
    )
    if faction is None:
        return {}

    major_factions = sorted(
        (item for item in world.factions if not item.is_neutral_city_state),
        key=lambda item: item.faction_id,
    )
    city = _city_for_neutral(world, faction)
    relationships = []
    for major in major_factions:
        score = max(-100, min(100, int(faction.relations.get(major.faction_id, 0))))
        band, label, governor_view = _relation_band(score)
        relationships.append({
            "faction_id": major.faction_id,
            "faction_name": major.name,
            "score": score,
            "band": band,
            "label": label,
            "governor_view": governor_view,
            "influence": int(faction.influence_by_faction.get(major.faction_id, 0)),
            "local_support": int(city.support_by_faction.get(major.faction_id, 35)) if city is not None else 0,
        })

    if city is None:
        return {
            "city_id": None,
            "posture": {"id": "dispossessed", "label": "失城沉寂", "summary": "已失去唯一城市，当前没有本地行动能力。"},
            "current_need": {"id": "restoration", "label": "恢复城邦", "summary": "城主旧部希望重新取得立足点。"},
            "fear": {"type": "dispossession", "source_id": None, "label": "城邦消亡", "summary": "唯一城市已经易主。"},
            "governor_position": {"id": "exiled", "label": "保存旧部", "summary": "城主优先保存残余影响，而非主动扩张。"},
            "relationships": relationships,
            "factors": {},
        }

    forecast = forecast_city_month(city)
    own_strength = city.resources.troops + city.defense * 40
    adjacent_threats = _adjacent_major_strengths(world, city)
    strongest = adjacent_threats[0] if adjacent_threats else None
    threat_ratio = round(strongest[1] / max(1, own_strength), 2) if strongest else 0.0
    incited = bool(faction.incited_against_faction_id)

    if incited:
        posture = {"id": "incited", "label": "受教唆出兵", "summary": "一次性攻击意图尚未执行，城主仍不会自行选择扩张。"}
        need = {"id": "escape_manipulation", "label": "摆脱操纵", "summary": "当前首要诉求是结束外部教唆造成的战争压力。"}
        fear = {
            "type": "instigator",
            "source_id": faction.incited_by_faction_id,
            "label": next((item.name for item in world.factions if item.faction_id == faction.incited_by_faction_id), "未知教唆者"),
            "summary": "城邦正被外部势力驱使卷入战争。",
        }
        governor_position = {"id": "coerced", "label": "被迫应战", "summary": "城主准备完成一次出兵，之后恢复本地守备。"}
    elif forecast["food_shortage"]:
        posture = {"id": "seeking_aid", "label": "求援", "summary": "粮食缺口压过了其他政治考虑。"}
        need = {"id": "food_relief", "label": "粮食援助", "summary": f"预计下月粮食维护需要 {forecast['food_upkeep']}。"}
        fear = {"type": "shortage", "source_id": city.city_id, "label": "粮食断供", "summary": "缺粮会继续损害支持度并推高叛乱风险。"}
        governor_position = {"id": "pragmatic_aid", "label": "愿以合作换援助", "summary": "城主愿与能解决粮荒的势力交涉。"}
    elif forecast["rebellion_risk"] >= 45:
        posture = {"id": "unrest", "label": "内局不稳", "summary": "城主把城市秩序置于对外行动之前。"}
        need = {"id": "stability", "label": "稳定民心", "summary": f"预计叛乱风险为 {forecast['rebellion_risk']}。"}
        fear = {"type": "unrest", "source_id": city.city_id, "label": "自治派坐大", "summary": "内部支持崩落可能比外敌更早摧毁统治。"}
        governor_position = {"id": "defensive_reform", "label": "先安内政", "summary": "城主暂不愿承担高风险对外承诺。"}
    elif strongest and threat_ratio >= 1.25:
        posture = {"id": "threatened", "label": "边境受压", "summary": "相邻主要势力的兵力与城防优势形成现实压力。"}
        need = {"id": "protection", "label": "防务保障", "summary": "城邦希望获得能够约束强邻的安全承诺。"}
        fear = {"type": "major_faction", "source_id": strongest[0].faction_id, "label": strongest[0].name, "summary": f"相邻威胁强度约为本城 {threat_ratio:.2f} 倍。"}
        governor_position = {"id": "balancing", "label": "寻求制衡", "summary": "城主愿与第三方合作，但不会主动扩张。"}
    else:
        posture = {"id": "guarded_neutral", "label": "中立守备", "summary": "当前没有迫使城邦改变现状的直接危机。"}
        need = {"id": "trade_access", "label": "互市通路", "summary": "城邦希望通过有限往来增加资源余量，同时保留自治。"}
        if strongest:
            fear = {"type": "major_faction", "source_id": strongest[0].faction_id, "label": strongest[0].name, "summary": "强邻尚未构成立即威胁，但仍是主要安全观察对象。"}
        else:
            fear = {"type": "none", "source_id": None, "label": "暂无明确来源", "summary": "当前没有接壤主要势力形成直接压力。"}
        governor_position = {"id": "autonomy_first", "label": "自治优先", "summary": "城主接受互利往来，但拒绝无条件并入任何主要势力。"}

    return {
        "city_id": city.city_id,
        "posture": posture,
        "current_need": need,
        "fear": fear,
        "governor_position": governor_position,
        "relationships": relationships,
        "factors": {
            "food_shortage": bool(forecast["food_shortage"]),
            "rebellion_risk": int(forecast["rebellion_risk"]),
            "own_defense_strength": own_strength,
            "strongest_adjacent_major_strength": strongest[1] if strongest else 0,
            "threat_ratio": threat_ratio,
        },
    }


def neutral_city_state_profiles_public(world: WorldState) -> dict[str, dict[str, Any]]:
    return {
        faction.faction_id: neutral_city_state_profile(world, faction.faction_id)
        for faction in world.factions
        if faction.is_neutral_city_state
    }
