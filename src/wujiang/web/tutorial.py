from __future__ import annotations

from typing import Any


TUTORIAL_ID = "first_battle"
TUTORIAL_STEPS: tuple[dict[str, Any], ...] = (
    {"id": "select_unit", "title": "选中你的武将", "instruction": "点击火葬者，先认识血量、魔力和行动。", "allowed": []},
    {"id": "move", "title": "向敌人靠近", "instruction": "选择移动，并走到金色标记的格子。", "allowed": ["move"]},
    {"id": "basic_attack", "title": "进行普通攻击", "instruction": "选择普攻，再点击艾莉。", "allowed": ["attack"]},
    {"id": "active_skill", "title": "使用主动技能", "instruction": "选择穿刺，查看魔力消耗和直线范围后命中艾莉。", "allowed": ["pierce"]},
    {"id": "end_turn", "title": "结束当前回合", "instruction": "本回合练习完成，点击结束回合让艾莉行动。", "allowed": ["end_turn"]},
    {"id": "chain_response", "title": "完成一次连锁响应", "instruction": "艾莉的行动影响火葬者时，选择一个响应，或明确放弃连锁。", "allowed": ["reaction", "chain_skip"]},
    {"id": "win_objective", "title": "独立赢下教学战", "instruction": "现在所有正常行动都已解锁。击败艾莉即可完成教学。", "allowed": ["all"]},
)
STEP_INDEX = {step["id"]: index for index, step in enumerate(TUTORIAL_STEPS)}


def tutorial_step(step_id: str) -> dict[str, Any]:
    return dict(TUTORIAL_STEPS[STEP_INDEX.get(step_id, 0)])


def next_tutorial_step_id(step_id: str) -> str:
    index = STEP_INDEX.get(step_id, 0)
    return TUTORIAL_STEPS[min(index + 1, len(TUTORIAL_STEPS) - 1)]["id"]
