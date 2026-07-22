from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

BATCH_ROOT = ROOT / "var" / "phase0-playtests"
BATCH_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
SAMPLE_ID_PATTERN = re.compile(r"^(desktop|mobile)-[0-9]{2,3}$")
TUTORIAL_STEPS = (
    "select_unit",
    "move",
    "basic_attack",
    "pierce",
    "end_turn",
    "chain_response",
    "win_objective",
    "completed",
    "none",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_batch_id(batch_id: str) -> str:
    normalized = str(batch_id or "").strip().lower()
    if not BATCH_ID_PATTERN.fullmatch(normalized):
        raise ValueError("批次编号只能使用 1～48 位小写字母、数字、下划线或连字符。")
    return normalized


def batch_paths(batch_id: str, *, batch_root: Path = BATCH_ROOT) -> dict[str, Path]:
    normalized = validate_batch_id(batch_id)
    directory = batch_root / normalized
    return {
        "directory": directory,
        "manifest": directory / "manifest.json",
        "observations": directory / "observations.json",
        "analytics": directory / "analytics.sqlite3",
        "accounts": directory / "accounts.sqlite3",
        "report": directory / "report.md",
    }


def prepare_batch(batch_id: str, *, resume: bool = False, batch_root: Path = BATCH_ROOT) -> dict[str, Path]:
    normalized = validate_batch_id(batch_id)
    paths = batch_paths(normalized, batch_root=batch_root)
    directory = paths["directory"]
    manifest_exists = paths["manifest"].exists()
    if manifest_exists and not resume:
        raise ValueError(f"批次 {normalized} 已存在；继续收集请增加 --resume。")
    if directory.exists() and not manifest_exists and any(directory.iterdir()):
        raise ValueError(f"批次目录 {directory} 已有未知内容，已拒绝覆盖。")
    directory.mkdir(parents=True, exist_ok=True)
    if not manifest_exists:
        paths["manifest"].write_text(
            json.dumps(
                {
                    "batch_id": normalized,
                    "created_at": utc_now(),
                    "purpose": "Phase 0 real-player validation",
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    if not paths["observations"].exists():
        paths["observations"].write_text("[]\n", encoding="utf-8")
    return paths


def _load_observations(paths: dict[str, Path]) -> list[dict[str, Any]]:
    if not paths["observations"].exists():
        return []
    raw = json.loads(paths["observations"].read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("observations.json 格式无效，应为数组。")
    return [dict(item) for item in raw if isinstance(item, dict)]


def _clean_note(value: str, *, field: str, max_length: int) -> str:
    cleaned = " ".join(str(value or "").strip().split())
    if len(cleaned) > max_length:
        raise ValueError(f"{field} 不能超过 {max_length} 个字符。")
    return cleaned


def record_observation(
    batch_id: str,
    *,
    sample_id: str,
    device: str,
    browser: str,
    completed: bool,
    stuck_step: str,
    external_rules: bool,
    restart: bool,
    feedback: str = "",
    first_action_seconds: float | None = None,
    replace: bool = False,
    batch_root: Path = BATCH_ROOT,
) -> dict[str, Any]:
    paths = batch_paths(batch_id, batch_root=batch_root)
    if not paths["manifest"].exists():
        raise ValueError("批次不存在，请先运行 start。")
    normalized_sample = str(sample_id or "").strip().lower()
    match = SAMPLE_ID_PATTERN.fullmatch(normalized_sample)
    if match is None:
        raise ValueError("匿名样本编号必须形如 desktop-01 或 mobile-01。")
    normalized_device = str(device or "").strip().lower()
    if normalized_device not in {"desktop", "mobile"} or match.group(1) != normalized_device:
        raise ValueError("样本编号前缀必须与 device 一致。")
    normalized_step = str(stuck_step or "").strip().lower()
    if normalized_step not in TUTORIAL_STEPS:
        raise ValueError("stuck-step 必须是固定教学步骤、completed 或 none。")
    if completed and normalized_step not in {"completed", "none"}:
        raise ValueError("已完成样本的 stuck-step 应为 completed 或 none。")
    if not completed and normalized_step == "completed":
        raise ValueError("未完成样本不能把 stuck-step 记为 completed。")
    if first_action_seconds is not None and not 0 <= first_action_seconds <= 86_400:
        raise ValueError("first-action-seconds 必须在 0～86400 之间。")
    observations = _load_observations(paths)
    existing_index = next(
        (index for index, item in enumerate(observations) if item.get("sample_id") == normalized_sample),
        None,
    )
    if existing_index is not None and not replace:
        raise ValueError(f"样本 {normalized_sample} 已存在；修正记录请增加 --replace。")
    observation: dict[str, Any] = {
        "sample_id": normalized_sample,
        "device": normalized_device,
        "browser": _clean_note(browser, field="browser", max_length=120),
        "completed": bool(completed),
        "stuck_step": normalized_step,
        "external_rules": bool(external_rules),
        "restart": bool(restart),
        "feedback": _clean_note(feedback, field="feedback", max_length=240),
        "recorded_at": utc_now(),
    }
    if first_action_seconds is not None:
        observation["first_action_seconds"] = round(float(first_action_seconds), 3)
    if existing_index is None:
        observations.append(observation)
    else:
        observations[existing_index] = observation
    observations.sort(key=lambda item: str(item.get("sample_id") or ""))
    paths["observations"].write_text(
        json.dumps(observations, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return observation


def _empty_funnel() -> dict[str, Any]:
    return {
        "generated_at": None,
        "total_events": 0,
        "unique_sessions": 0,
        "steps": [],
        "metrics": {
            "first_effective_action_median_ms": None,
            "tutorial_completion_rate": None,
            "match_completion_rate": None,
            "invalid_action_rate": None,
            "tutorial_duration_median_ms": None,
            "match_duration_median_ms": None,
            "action_attempts": 0,
            "rematch_within_10m_rate": None,
        },
    }


def build_report(batch_id: str, *, batch_root: Path = BATCH_ROOT) -> dict[str, Any]:
    normalized = validate_batch_id(batch_id)
    paths = batch_paths(normalized, batch_root=batch_root)
    if not paths["manifest"].exists():
        raise ValueError("批次不存在，请先运行 start。")
    observations = _load_observations(paths)
    funnel = _empty_funnel()
    if paths["analytics"].exists():
        from wujiang.web.analytics import AnalyticsStore

        funnel = AnalyticsStore(paths["analytics"]).funnel()
    desktop_count = sum(item.get("device") == "desktop" for item in observations)
    mobile_count = sum(item.get("device") == "mobile" for item in observations)
    completed_without_rules = sum(
        bool(item.get("completed")) and not bool(item.get("external_rules")) for item in observations
    )
    full_paths = sum(
        bool(item.get("completed"))
        and not bool(item.get("external_rules"))
        and bool(item.get("restart"))
        for item in observations
    )
    manual_completion_rate = (
        round(completed_without_rules / len(observations), 4) if observations else None
    )
    observed_first_actions = [
        float(item["first_action_seconds"]) * 1000
        for item in observations
        if isinstance(item.get("first_action_seconds"), (int, float))
    ]
    analytics_first_action = funnel["metrics"].get("first_effective_action_median_ms")
    first_action_median_ms = (
        float(analytics_first_action)
        if isinstance(analytics_first_action, (int, float))
        else (median(observed_first_actions) if observed_first_actions else None)
    )
    coverage_ready = desktop_count >= 5 and mobile_count >= 5
    first_action_ready = first_action_median_ms is not None and first_action_median_ms < 180_000
    completion_ready = (
        coverage_ready and manual_completion_rate is not None and manual_completion_rate >= 0.70
    )
    full_path_ready = full_paths >= 1
    data_ready = coverage_ready and first_action_ready and completion_ready and full_path_ready
    return {
        "batch_id": normalized,
        "generated_at": utc_now(),
        "observations": {
            "total": len(observations),
            "desktop": desktop_count,
            "mobile": mobile_count,
            "completed_without_external_rules": completed_without_rules,
            "manual_completion_rate": manual_completion_rate,
            "full_paths_with_restart": full_paths,
            "observed_first_action_count": len(observed_first_actions),
        },
        "analytics": funnel,
        "gates": {
            "device_sample_coverage": {
                "passed": coverage_ready,
                "detail": f"desktop {desktop_count}/5，mobile {mobile_count}/5",
            },
            "first_effective_action_under_3_minutes": {
                "passed": first_action_ready,
                "value_ms": first_action_median_ms,
            },
            "tutorial_completion_at_least_70_percent": {
                "passed": completion_ready,
                "value": manual_completion_rate,
                "basis": "匿名观察记录中未查看外部规则的完成样本 / 全部有效样本",
            },
            "complete_path_without_rules_and_with_restart": {
                "passed": full_path_ready,
                "count": full_paths,
            },
        },
        "data_ready_for_phase_review": data_ready,
        "owner_confirmation_required": True,
    }


def _format_rate(value: Any) -> str:
    return "—" if not isinstance(value, (int, float)) else f"{float(value) * 100:.1f}%"


def _format_duration(value: Any) -> str:
    return "—" if not isinstance(value, (int, float)) else f"{float(value) / 1000:.1f} 秒"


def _gate_mark(passed: bool) -> str:
    return "通过" if passed else "待达标"


def render_report(report: dict[str, Any]) -> str:
    observations = report["observations"]
    analytics = report["analytics"]
    metrics = analytics["metrics"]
    gates = report["gates"]
    lines = [
        f"# Phase 0 内测批次：{report['batch_id']}",
        "",
        f"生成时间：{report['generated_at']}",
        "",
        "## 样本",
        "",
        f"- 有效人工记录：{observations['total']}（桌面 {observations['desktop']}，手机 {observations['mobile']}）",
        f"- 未查看外部规则并完成：{observations['completed_without_external_rules']}",
        f"- 人工完成率：{_format_rate(observations['manual_completion_rate'])}",
        f"- 完成后找到再次开始：{observations['full_paths_with_restart']}",
        "",
        "## 自动埋点",
        "",
        f"- 匿名会话：{analytics['unique_sessions']}；事件：{analytics['total_events']}",
        f"- 首次有效行动中位时间：{_format_duration(metrics.get('first_effective_action_median_ms'))}",
        f"- 教学完成率：{_format_rate(metrics.get('tutorial_completion_rate'))}",
        f"- 对局完成率：{_format_rate(metrics.get('match_completion_rate'))}",
        f"- 非法操作率：{_format_rate(metrics.get('invalid_action_rate'))}",
        "",
        "## Phase 0 数据门槛",
        "",
        f"- [{_gate_mark(gates['device_sample_coverage']['passed'])}] 设备样本：{gates['device_sample_coverage']['detail']}",
        f"- [{_gate_mark(gates['first_effective_action_under_3_minutes']['passed'])}] 首次有效行动中位时间小于 3 分钟：{_format_duration(gates['first_effective_action_under_3_minutes']['value_ms'])}",
        f"- [{_gate_mark(gates['tutorial_completion_at_least_70_percent']['passed'])}] 无需外部规则的教学完成率至少 70%：{_format_rate(gates['tutorial_completion_at_least_70_percent']['value'])}",
        f"- [{_gate_mark(gates['complete_path_without_rules_and_with_restart']['passed'])}] 至少一条完成教学并找到再次开始的完整路径：{gates['complete_path_without_rules_and_with_restart']['count']}",
        "",
        f"数据是否已可提交 Phase 0 复盘：{'是' if report['data_ready_for_phase_review'] else '否'}",
        "",
        "> 本报告只判断样本数据是否可进入复盘；完整测试结果和项目负责人确认仍需单独核对。",
        "",
    ]
    return "\n".join(lines)


def _yes_no(value: str) -> bool:
    normalized = str(value or "").strip().lower()
    if normalized not in {"yes", "no"}:
        raise argparse.ArgumentTypeError("只能填写 yes 或 no。")
    return normalized == "yes"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run and summarize clean Phase 0 real-player playtest batches.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start", help="Create or resume an isolated batch and start the local server.")
    start.add_argument("batch_id")
    start.add_argument("--resume", action="store_true")
    start.add_argument("--host", default="0.0.0.0")
    start.add_argument("--port", type=int, default=8000)
    start.add_argument("--public-base-url", default="")

    record = subparsers.add_parser("record", help="Record one anonymous facilitator observation.")
    record.add_argument("batch_id")
    record.add_argument("--sample", required=True)
    record.add_argument("--device", choices=("desktop", "mobile"), required=True)
    record.add_argument("--browser", required=True)
    record.add_argument("--completed", type=_yes_no, required=True)
    record.add_argument("--stuck-step", choices=TUTORIAL_STEPS, required=True)
    record.add_argument("--external-rules", type=_yes_no, required=True)
    record.add_argument("--restart", type=_yes_no, required=True)
    record.add_argument("--feedback", default="")
    record.add_argument("--first-action-seconds", type=float)
    record.add_argument("--replace", action="store_true")

    report = subparsers.add_parser("report", help="Generate the aggregate batch review without exposing credentials.")
    report.add_argument("batch_id")
    report.add_argument("--json", action="store_true")
    return parser


def _run_server(args: argparse.Namespace) -> int:
    paths = prepare_batch(args.batch_id, resume=args.resume)
    os.environ["WUJIANG_ANALYTICS_DB"] = str(paths["analytics"])
    os.environ["WUJIANG_AUTH_DB"] = str(paths["accounts"])
    from wujiang.web.server import normalize_public_base_url, run_server

    public_base_url = normalize_public_base_url(args.public_base_url)
    local_host = "127.0.0.1" if args.host in {"0.0.0.0", "::"} else args.host
    print(f"Phase 0 批次：{validate_batch_id(args.batch_id)}")
    print(f"玩家入口：http://{local_host}:{args.port}/")
    print(f"聚合看板：http://{local_host}:{args.port}/analytics.html")
    print(f"本地批次目录：{paths['directory']}")
    try:
        run_server(host=args.host, port=args.port, public_base_url=public_base_url)
    except KeyboardInterrupt:
        print("\n内测服务器已停止。")
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = _parser().parse_args(argv)
    try:
        if args.command == "start":
            return _run_server(args)
        if args.command == "record":
            observation = record_observation(
                args.batch_id,
                sample_id=args.sample,
                device=args.device,
                browser=args.browser,
                completed=args.completed,
                stuck_step=args.stuck_step,
                external_rules=args.external_rules,
                restart=args.restart,
                feedback=args.feedback,
                first_action_seconds=args.first_action_seconds,
                replace=args.replace,
            )
            print(f"已记录匿名样本 {observation['sample_id']}。")
            return 0
        report = build_report(args.batch_id)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            rendered = render_report(report)
            paths = batch_paths(args.batch_id)
            paths["report"].write_text(rendered, encoding="utf-8")
            print(rendered)
            print(f"报告已保存：{paths['report']}")
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
