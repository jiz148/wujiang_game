from __future__ import annotations

import copy
from http import HTTPStatus
from typing import Any

from wujiang.strategy.errors import StrategyError


CURRENT_STRATEGY_SAVE_VERSION = 1


def strategy_save_version(raw: dict[str, Any]) -> int:
    value = raw.get("save_format_version", 0)
    if isinstance(value, bool):
        raise StrategyError("战略存档版本无效。", status=HTTPStatus.CONFLICT)
    try:
        version = int(value)
    except (TypeError, ValueError) as exc:
        raise StrategyError("战略存档版本无效。", status=HTTPStatus.CONFLICT) from exc
    if version < 0:
        raise StrategyError("战略存档版本不能为负数。", status=HTTPStatus.CONFLICT)
    return version


def migrate_world_payload(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise StrategyError("战略存档必须是对象。", status=HTTPStatus.CONFLICT)
    migrated = copy.deepcopy(raw)
    version = strategy_save_version(migrated)
    if version > CURRENT_STRATEGY_SAVE_VERSION:
        raise StrategyError(
            f"战略存档版本 {version} 高于当前支持版本 {CURRENT_STRATEGY_SAVE_VERSION}，请升级服务后再读取。",
            status=HTTPStatus.CONFLICT,
        )
    while version < CURRENT_STRATEGY_SAVE_VERSION:
        if version == 0:
            migrated["save_format_version"] = 1
            version = 1
            continue
        raise StrategyError(
            f"缺少从战略存档版本 {version} 开始的迁移步骤。",
            status=HTTPStatus.CONFLICT,
        )
    return migrated
