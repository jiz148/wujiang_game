"""Read existing user-run audit evidence only; never import or run the game."""
import json
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

root = Path(__file__).resolve().parents[2]
audit = root / "reports/current-hero-deep-audit/excel_r126--excel_r291--excel_r356"
rows = []
for hero in ("excel_r126", "excel_r291", "excel_r356"):
    for path in sorted((audit / hero).glob("*/trace.jsonl")):
        events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        own = [event for event in events if event.get("actor", {}).get("hero_code") == hero]
        actions = Counter(event["payload"].get("skill_code", event["payload"].get("action_code", event["payload"]["type"])) for event in own)
        row = {"hero": hero, "match": path.parent.name.split("-")[1], "steps": len(events), "winner": events[-1].get("winner"), "actions": dict(actions)}
        selected_logs = []
        for event in events:
            for log in event.get("new_logs", []):
                if any(term in str(log) for term in ("赤之随机破魔", "铁锁锚点", "铁锁追击", "犀牛遗志", "等待回归", "满状态回归")):
                    selected_logs.append({"step": event["step"], "log": log})
        if hero == "excel_r291":
            declarations = [event for event in own if event["payload"]["type"] in {"attack", "skill", "chain_react"}]
            rolls = [entry for entry in selected_logs if "赤之随机破魔" in str(entry["log"])]
            row["declarations_and_rolls"] = [len(declarations), len(rolls)]
            row["missing_roll_steps"] = [event["step"] for event in declarations if not any("赤之随机破魔" in str(log) for log in event.get("new_logs", []))]
        if hero == "excel_r356":
            attacks_by_turn = Counter(event["before"]["turn_number"] for event in own if event["payload"]["type"] == "attack")
            row["attacks_per_own_turn"] = dict(attacks_by_turn)
            row["lifecycle_logs"] = selected_logs
        if hero == "excel_r126":
            casts = []
            for index, event in enumerate(events):
                if event.get("actor", {}).get("hero_code") != hero or event["payload"].get("skill_code") != "solar_judgment":
                    continue
                cast = {"step": event["step"], "deaths": [], "logs": []}
                for following in events[index:]:
                    if following is not event and following["reason"] == "ai_turn":
                        break
                    for unit_id, delta in following.get("state_delta", {}).items():
                        before, after = delta.get("before"), delta.get("after")
                        if before and after and before.get("alive") and not after.get("alive"):
                            cast["deaths"].append({"code": before["hero_code"], "player": before["player_id"]})
                    cast["logs"].extend(following.get("new_logs", []))
                casts.append(cast)
            row["judgment_casts"] = casts
            if len(events) >= 500:
                row["tail"] = [{"step": event["step"], "actor": event.get("actor"), "payload": event["payload"], "logs": event.get("new_logs", [])} for event in events[-6:]]
        rows.append(row)

log = (audit / "regression_tests.log").read_text(encoding="utf-8")
failures = [line for line in log.splitlines() if line.startswith(("FAIL:", "ERROR:"))]
result = {"source": str(audit.relative_to(root)), "matches": rows, "regression_failures": failures}
output = Path(__file__).with_name("saved_trace_review.json")
output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
for row in rows:
    compact = {key: value for key, value in row.items() if key not in {"tail", "judgment_casts"}}
    if "judgment_casts" in row:
        compact["judgment_casts"] = [{"step": cast["step"], "deaths": cast["deaths"]} for cast in row["judgment_casts"]]
    print(json.dumps(compact, ensure_ascii=False))
print(f"Regression findings: {len(failures)}. Full evidence: {output}")
