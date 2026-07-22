from __future__ import annotations

from typing import Any


BEGINNER_HEROES: tuple[dict[str, Any], ...] = (
    {"code": "fire_funeral", "position": "近中程爆发", "difficulty": "简单", "mechanics": "直线技能、强化移动", "teammates": ["bard", "soul_wraith"], "summary": "先靠神速接近，再用穿刺和普攻制造稳定压力。"},
    {"code": "elite_soldier", "position": "远程输出", "difficulty": "简单", "mechanics": "方形普攻、直线射击", "teammates": ["excel_r139", "masamune"], "summary": "保持距离，用大范围普攻和机枪覆盖敌人。"},
    {"code": "bard", "position": "治疗辅助", "difficulty": "简单", "mechanics": "治疗、加防、保护", "teammates": ["masamune", "fire_funeral"], "summary": "站在队友身后，用治疗和防御强化延长前线时间。"},
    {"code": "masamune", "position": "机动前排", "difficulty": "标准", "mechanics": "乘骑、格挡、多段攻击", "teammates": ["bard", "elite_soldier"], "summary": "骑乘时快速切入，下马后用格挡和反击守住阵地。"},
    {"code": "soul_wraith", "position": "高速刺客", "difficulty": "标准", "mechanics": "穿人、弧形攻击、物免", "teammates": ["fire_funeral", "excel_r337"], "summary": "利用高速度穿过人群，贴近目标发动弧形普攻。"},
    {"code": "dragon_rider", "position": "召唤前排", "difficulty": "标准", "mechanics": "骑龙、范围普攻、烟雾控制", "teammates": ["bard", "excel_r139"], "summary": "让巨龙占住关键区域，再用烟雾限制敌方出手。"},
    {"code": "excel_r113", "position": "机动控制", "difficulty": "标准", "mechanics": "飞行、净化、决斗", "teammates": ["elite_soldier", "excel_r337"], "summary": "先用机动技能找角度，再用神圣决斗锁住关键敌人。"},
    {"code": "excel_r139", "position": "范围辅助", "difficulty": "简单", "mechanics": "光环回复、远程穿刺", "teammates": ["elite_soldier", "dragon_rider"], "summary": "保持在队伍中心，用光环续航并从远处补伤害。"},
    {"code": "excel_r337", "position": "入门治疗", "difficulty": "简单", "mechanics": "回复、光墙、天气", "teammates": ["soul_wraith", "excel_r113"], "summary": "用回复照顾受伤队友，用光墙替前排挡住危险。"},
    {"code": "dark_human", "position": "隐身刺客", "difficulty": "进阶", "mechanics": "隐身、回避、位移", "teammates": ["bard", "excel_r139"], "summary": "先隐身接近脆弱目标，再选择合适时机现身爆发。"},
)

RECOMMENDED_ROSTERS: tuple[dict[str, Any], ...] = (
    {"code": "steady_front", "name": "稳健前线", "hero_codes": ["masamune", "bard"], "summary": "政宗承担前线压力，吟游诗人负责治疗和加防。"},
    {"code": "ranged_pressure", "name": "远程压制", "hero_codes": ["elite_soldier", "excel_r139"], "summary": "精兵覆盖远处目标，索拉提供续航和范围支援。"},
    {"code": "mobile_assault", "name": "机动突击", "hero_codes": ["fire_funeral", "soul_wraith"], "summary": "火葬者打出正面压力，销魂的死灵从侧翼快速切入。"},
)

QUICK_AI_MATCH: dict[str, Any] = {
    "player_roster_code": "steady_front",
    "player_roster_name": "稳健前线",
    "player_hero_codes": ["masamune", "bard"],
    "opponent_roster_code": "ranged_pressure",
    "opponent_roster_name": "远程压制",
    "opponent_hero_codes": ["elite_soldier", "excel_r139"],
    "opponent_name": "入门 AI",
    "ai_difficulty": "easy",
}


def _estimated_difficulty(hero: dict[str, Any]) -> str:
    mechanism_length = len(str(hero.get("raw_skill_text") or "")) + len(str(hero.get("raw_trait_text") or ""))
    if mechanism_length <= 90:
        return "简单"
    if mechanism_length <= 200:
        return "标准"
    return "进阶"


def hero_discovery_payload(heroes: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    beginner_by_code = {str(item["code"]): item for item in BEGINNER_HEROES}
    discovery = []
    for hero in heroes:
        code = str(hero.get("code") or "")
        beginner = beginner_by_code.get(code)
        discovery.append(
            {
                "code": code,
                "position": str(beginner["position"] if beginner else hero.get("role") or "未分类"),
                "role": str(hero.get("role") or "未分类"),
                "difficulty": str(beginner["difficulty"] if beginner else _estimated_difficulty(hero)),
                "difficulty_source": "curated" if beginner else "estimated",
            }
        )
    return discovery


def onboarding_payload(heroes: list[dict[str, Any]] | tuple[dict[str, Any], ...] = ()) -> dict[str, Any]:
    return {
        "beginner_heroes": [dict(item) for item in BEGINNER_HEROES],
        "recommended_rosters": [dict(item) for item in RECOMMENDED_ROSTERS],
        "hero_discovery": hero_discovery_payload(heroes),
    }


def recommended_roster_hero_codes(roster_code: str) -> list[str] | None:
    normalized = str(roster_code or "").strip()
    roster = next((item for item in RECOMMENDED_ROSTERS if item["code"] == normalized), None)
    return list(roster["hero_codes"]) if roster is not None else None


def quick_ai_match_payload() -> dict[str, Any]:
    return {
        **QUICK_AI_MATCH,
        "player_hero_codes": list(QUICK_AI_MATCH["player_hero_codes"]),
        "opponent_hero_codes": list(QUICK_AI_MATCH["opponent_hero_codes"]),
    }
