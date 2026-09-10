"""Compact, lossless indexes over saved audit evidence; never simulate a battle."""
from __future__ import annotations

import json
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def collect_changes(root: Path, destination: Path) -> dict[str, Any]:
    """Keep index and worktree diffs separate, including mutually cancelling edits."""
    destination.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {
        "basis": "HEAD -> index; index -> working tree. Not the last accepted audit.",
        "untracked_exclusions": ["Git-ignored files", "outputs/", "reports/"],
        "errors": [],
    }
    commands = {
        "head": ["rev-parse", "HEAD"],
        "staged_files": ["diff", "--cached", "--name-only", "-z", "--no-renames"],
        "unstaged_files": ["diff", "--name-only", "-z", "--no-renames"],
        "untracked_files": ["ls-files", "--others", "--exclude-standard", "-z", "--", ".",
                            ":(exclude)outputs/**", ":(exclude)reports/**"],
        "staged.patch": ["diff", "--cached", "--binary", "--no-ext-diff", "--no-textconv"],
        "unstaged.patch": ["diff", "--binary", "--no-ext-diff", "--no-textconv"],
    }
    for key, args in commands.items():
        try:
            completed = subprocess.run(
                ["git", "-c", "core.quotepath=false", *args], cwd=root,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            if completed.returncode:
                raise OSError(completed.stderr.decode("utf-8", errors="replace").strip())
            if key.endswith(".patch"):
                (destination / key).write_bytes(completed.stdout)
                result[key] = key
            elif key.endswith("_files"):
                result[key] = completed.stdout.decode("utf-8", errors="replace").rstrip("\0").split("\0") if completed.stdout else []
            else:
                result[key] = completed.stdout.decode("utf-8", errors="replace").strip()
        except OSError as exc:
            result["errors"].append(f"{key}: {exc}")
    return result


def write_review_packet(run_dir: Path, *, root: Path, existing_results: bool = False) -> Path:
    # Require completed aggregate files; summarizing must never fall through to running tests.
    summary = load_json(run_dir / "summary.json")
    regression = load_json(run_dir / "regression_tests.json")
    heroes = load_json(run_dir / "hero_metrics.json")["heroes"]
    issues: list[str] = []
    failures: list[dict[str, Any]] = []
    regression_log = run_dir / "regression_tests.log"
    if regression_log.exists():
        with regression_log.open(encoding="utf-8", errors="replace") as stream:
            for line_number, line in enumerate(stream, 1):
                if line.startswith(("FAIL: ", "ERROR: ")):
                    failures.append({"line": line_number, "description": line.strip()})
    else:
        issues.append("缺少 regression_tests.log；无法列出全部失败详情。")
    if not regression.get("passed") and not failures:
        issues.append("常规测试未通过，但没有解析到失败标题；必须检查原始输出和启动错误。")

    findings_path = run_dir / "findings.jsonl"
    finding_categories: Counter[str] = Counter()
    finding_lines = 0
    if findings_path.exists():
        with findings_path.open(encoding="utf-8") as stream:
            for number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                finding_lines += 1
                try:
                    finding_categories[str(json.loads(line).get("category", "unknown"))] += 1
                except (ValueError, AttributeError):
                    issues.append(f"findings.jsonl 第 {number} 行无法解析，需查看原文。")
    else:
        issues.append("缺少 findings.jsonl；不能把缺失报告当作零异常。")
    if finding_lines != summary.get("finding_count"):
        issues.append(f"findings 条数 {finding_lines} 与 summary 记录 {summary.get('finding_count')} 不一致。")

    evidence = []
    for match in summary.get("matches", []):
        folder = Path(match["output_dir"])
        folder = folder if folder.is_absolute() else root / folder
        trace = folder / "trace.jsonl"
        count = 0
        if trace.exists():
            with trace.open(encoding="utf-8") as stream:
                for number, line in enumerate(stream, 1):
                    if not line.strip():
                        continue
                    count += 1
                    try:
                        json.loads(line)
                    except ValueError:
                        issues.append(f"{trace} 第 {number} 行损坏。")
            if not count:
                issues.append(f"空对局记录：{trace}")
        else:
            issues.append(f"缺少对局记录：{trace}")
        evidence.append({"target": match.get("target"), "seed": match.get("seed"),
                         "trace": str(trace), "event_count": count, "exists": trace.exists()})
    if len(evidence) != summary.get("match_count"):
        issues.append("实际对局清单数量与 summary 记录不一致。")

    changes = collect_changes(root, run_dir / "code_changes")
    packet = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "existing_results_only": existing_results,
        "regression": regression, "failures": failures,
        "finding_count": finding_lines, "finding_categories": dict(finding_categories),
        "evidence": evidence, "integrity_issues": issues, "code_changes": changes,
    }
    (run_dir / "review_packet.json").write_text(json.dumps(packet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# 武将精简复核入口", "",
             "旧结果整理：本命令未执行测试，不能证明当前代码已通过。" if existing_results else
             "本次审计的汇总入口；若用了 --resume，可能包含复用的旧对局。",
             "代码清单记录生成摘要时的工作区，参照 Git HEAD/暂存区，不是上次验收版本；未验证测试期间代码是否发生变化。",
             "摘要不自动判定设计符合性。零告警、技能用过、日志可解析均不等于规则正确。", "",
             "## 常规测试：全部失败标题", "",
             f"{regression.get('test_count')} 项；{regression.get('unittest_result') or '结果未知'}。完整堆栈见 regression_tests.log。", ""]
    lines.extend(f"- L{item['line']}: {item['description']}" for item in failures)
    lines.extend(["", "## 全量证据索引", "",
                  f"对局 {len(evidence)} 场，trace 记录 {sum(item['event_count'] for item in evidence)} 条；findings {finding_lines} 条（包含普通观察）。",
                  "全部对局路径见 review_packet.json 的 evidence；全部发现保留在 findings.jsonl，未按严重程度删除。", ""])
    lines.extend(f"- {key}: {value}" for key, value in sorted(finding_categories.items()))
    lines.extend(["", "## 逐将观察与覆盖缺口", ""])
    for hero in heroes:
        lines.append(f"- {hero['name']} ({hero['code']})：{hero['match_count']} 场；{hero['priority']}；设计基线{'有' if hero['design_baseline_available'] else '缺失'}。")
        lines.extend(f"  - {note}" for note in hero.get("observation_notes", []))
        lines.append(f"  - 完整设计对照、动作统计和逐场入口：hero_reviews/{hero['code']}.md")
    lines.extend(["", "## 代码改动清单", "",
                  "完整已跟踪差异（含二进制补丁）见 code_changes/staged.patch 与 unstaged.patch；新增文件按下列路径读取。",
                  "新增文件清单不包含 Git 忽略项及 outputs/、reports/ 生成物；提交历史不在本清单内。共享引擎、AI、前端改动需扩大复核范围，不能仅凭武将代码搜索判断影响。", ""])
    for key, label in (("staged_files", "已暂存"), ("unstaged_files", "未暂存"), ("untracked_files", "新增未跟踪")):
        lines.extend(f"- {label}：{path}" for path in changes.get(key, []))
    lines.extend(["", "## 证据缺失或收集错误", ""])
    lines.extend(f"- {issue}" for issue in [*issues, *changes["errors"]])
    if not issues and not changes["errors"]:
        lines.append("清单核对未发现缺失；这不表示设计与实现不存在差异。")
    lines.extend(["", "复核顺序：全部失败与 findings → 设计覆盖缺口 → 改动涉及的规则/AI/测试 → 关键决策与正常代表对局。",
                  "已有锁定基线的武将复用基线；首次审查的武将仍需逐条完整对照。", ""])
    path = run_dir / "review_packet.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
