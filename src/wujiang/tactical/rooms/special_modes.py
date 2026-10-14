"""BP special modes transcribed from data/特殊规则.xlsx (Sheet1)."""

from __future__ import annotations

import secrets
from typing import Any, Sequence


SPECIAL_MODES: tuple[tuple[str, str, str], ...] = (
    ("fast", "来把快速", "所有单位变为速10，行动顺序变成相反"),
    ("sympathy", "同情弱者", "开局时，一些武将获得特性:\n攻最高的武将-无法使用所有主动型直接伤害技能\n守最高的武将-无法作为被动技能的影响对象\n速最高的武将-无法使用能造成位移的技能范最高的武将-的技能只能范1发动"),
    ("slow", "慢速模式", "每回合每个武将只能使用一个技能"),
    ("duel", "有种来单挑", "各随机一个武将来单挑，单挑获胜就是胜利"),
    ("real_men", "真男人模式", "所有单位获得魔免；普攻和普通移动仍可使用，技能仅可使用主动技能"),
    ("general", "大将模式", "等级最高的武将不能参战"),
    ("healer_attack", "不会打伤害的辅助不是好刺客", "每次回复都随机对对方一个武将造成回复量相同的伤害，如果是魔，则费相同的魔，无法连锁这个伤害"),
    ("no_recovery", "老一套对我没用", "禁止回复"),
    ("sudden_death", "骤死模式", "被破坏任意一个武将的一方输"),
    ("soft_persimmon", "软柿子", "每当一个武将被破坏时，该武将会被重制一切设定并重新生成在原地----但是己方行动顺序下一位仍存活的武将会代替其被破坏；己方没有其他存活武将时正常被破坏\n* 所有召唤物、技能效果会消失，就像被破坏过一样。重新生成也是强制的;\n* 但是是完全重制，就像刚出现的新武将一样，所以那种被破坏后才能发动的效果不会生效。\n*并且对方破坏单位后的效果也能激活"),
    ("mages", "我们都是法师", "每回合结束后魔变满，隐身状态时不能变满"),
    ("arena", "擂台赛", "随机决定打擂台顺序\n不打擂台的武将处于无敌并不进入回合，且不能行动，不受效果影响\n场上只有行动顺序最高的武将可以打擂台"),
    ("double_down", "Double Down", "同时抽两个特殊效果"),
    ("chain", "不是，这怎么有根铁链?", "每个武将移动时，其他队友会被迫跟着移动。开局的初始站位决定了武将间的最大距离。\n被迫移动的逻辑:\n\t武将A因各种原因位移时，行动顺序下一位的武将B如果离A超出了最大距离，就尽量通过移动较少的格子回到和A的最大距离内"),
    ("eternal", "永恒轮回战场", "场地中间生成5x5的区域\n当每一轮次结束时，如果该区域只有某一方的武将，则该方所有单位某一属性+1\n*该效果可以不断叠加"),
    ("true_turn", "真回合制", "所有单位范变为周围无限*无限"),
    ("face_tank", "刚正面！", "只能对对方守最高的武将造成伤害"),
    ("none", "无", "没有特殊规则"),
)

SPECIAL_MODE_BY_CODE = {code: {"code": code, "name": name, "description": description}
                        for code, name, description in SPECIAL_MODES}


def resolve_special_mode(code: str) -> tuple[str, ...]:
    """一条规则展开成实际生效的代码。Double Down 会再抽两条不同的效果。"""
    if code not in SPECIAL_MODE_BY_CODE:
        raise ValueError(code)
    if code != "double_down":
        return (code,)
    choices = [item[0] for item in SPECIAL_MODES if item[0] not in {"double_down", "none"}]
    return ("double_down", *secrets.SystemRandom().sample(choices, 2))


def choose_bp_special_modes(pool: Sequence[str] | None = None) -> tuple[str, ...]:
    """从规则表抽一条。不传名单时沿用整张表；传入名单时只在名单里抽。"""
    if pool is None:
        code = secrets.choice(SPECIAL_MODES)[0]
    else:
        choices = [str(code) for code in pool if str(code) in SPECIAL_MODE_BY_CODE]
        if not choices:
            raise ValueError("empty special mode pool")
        code = secrets.choice(choices)
    return resolve_special_mode(code)


def special_mode_catalog() -> list[dict[str, str]]:
    return [{"code": code, "name": name, "description": description} for code, name, description in SPECIAL_MODES]


def normalize_bp_rule_mode(value: Any) -> str:
    text = str(value or "").strip().lower()
    aliases = {
        "fixed": "fixed",
        "固定": "fixed",
        "固定规则": "fixed",
        "random": "random",
        "随机": "random",
        "随机规则": "random",
    }
    mapped = aliases.get(text)
    if mapped is None:
        raise ValueError("规则方式只能是固定规则或随机规则。")
    return mapped


def normalize_bp_rule_code(value: Any) -> str:
    text = str(value or "").strip()
    if text in SPECIAL_MODE_BY_CODE:
        return text
    for code, name, _description in SPECIAL_MODES:
        if text == name:
            return code
    raise ValueError("未知的规则。")


def normalize_bp_rule_pool(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        value = [part for part in value.replace("，", ",").split(",") if part.strip()]
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError("请至少勾选一条可随机的规则。")
    codes: list[str] = []
    for item in value:
        code = normalize_bp_rule_code(item)
        if code not in codes:
            codes.append(code)
    return tuple(codes)


def public_special_modes(codes: Sequence[str]) -> list[dict[str, Any]]:
    return [dict(SPECIAL_MODE_BY_CODE[code]) for code in codes if code in SPECIAL_MODE_BY_CODE]
