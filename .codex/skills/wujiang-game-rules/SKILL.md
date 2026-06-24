---
name: wujiang-game-rules
description: Always use this skill for any user request in the wujiang_game workspace or conversation. It is the project skill for the wujiang_game browser tactics game, including hero implementation, hero rules, skill/trait mechanics, battle-engine behavior, documents, tests, frontend targeting/preview behavior, and every durable gameplay clarification.
---

# Wujiang Game Rules

Use this skill as the always-on project onboarding and maintenance workflow for `C:\Users\jiz14\TeamGH\wujiang_game`.

## Mandatory Workflow

1. Read `references/project-map.md` at the start of any hero, rule, battle-engine, or gameplay UI task.
2. Read `references/hero-rule-index.md` when the task involves a specific hero, skill, trait, or current implementation lookup.
3. Consult the repo docs before coding. Prefer `docs/通用规则.md`, `docs/通用技能和特性说明.md`, `docs/武将说明.md`, and `docs/武将游戏规则.md`.
4. For new heroes, finish or update documentation before coding.
5. For unimplemented heroes from the source Excel, do not start coding individual heroes from ad hoc chat clarification. First generate or update `docs/武将实现问题清单.xlsx` as a questionnaire covering every unimplemented Excel hero. The questionnaire must have one row per question and include at least these columns: hero name, module/skill/trait/front-end area, question id, question text, options, user answer, and notes/source context. The user will answer in the workbook, usually with an option letter such as `a`, `b`, or `c`, but free-form answers are allowed.
6. The questionnaire must preserve ambiguity quality and quantity. Do not reduce the number of ambiguity questions just because the format is Excel. Include all rule, timing, targeting, effect-order, stacking, summon/clone, resource, AI, frontend selection/preview, and player-facing UI questions needed so that after the user answers the workbook, each listed hero has no remaining ambiguity before implementation. Do not include questions that merely ask whether durable/default/common rules apply; those rules apply automatically. Do not include base-stat confirmation, common skill confirmation, common trait confirmation, generic skill/trait split confirmation, or generic team-stat adjustment confirmation unless the source row contains hero-specific extra text that creates implementation ambiguity. Do not generate questionnaire rows from broad keyword templates such as "resource", "duration", "summon", or "frontend"; each question must be tied to a specific hero text ambiguity and should match the style of the prior one-hero clarification passes, with concrete rule alternatives rather than repeated generic implementation categories.
7. After the user finishes answering `docs/武将实现问题清单.xlsx`, implement all answered unimplemented heroes directly from the workbook plus existing durable rules, keeping docs, code, frontend previews, and tests synchronized.
8. Keep rule docs, engine code, frontend previews, and tests synchronized. Do not change only code when the user clarified a durable rule.
9. After every new gameplay requirement or code change in this repo, update this skill in the same turn. Prefer concise updates to `references/project-map.md` and `references/hero-rule-index.md`; update `SKILL.md` only when the workflow itself changes.
10. After every gameplay, room-flow, or player-visible UI change, add or update behavior-driven tests for the changed feature in the same turn. Prefer `tests/test_behavior.py` for scenario-style coverage and keep it synchronized with the feature that changed.
11. After every hero implementation, and during comprehensive AI debugging of already implemented heroes, run the full per-hero AI debug workflow for each target hero:
    - Read the implemented hero's source/doc text and questionnaire answers if the answer workbook or saved answers exist. For older heroes whose questionnaire answers were not preserved, explicitly note that the questionnaire is unavailable and rely on the existing docs, source text, and durable chat clarifications.
    - Run 10 AI-vs-AI simulations using `tools/per_hero_ai_debug.py` for each target hero. Each match pairs the target hero with one randomly chosen implemented teammate against two randomly chosen implemented opponents. Vary teammates, opponents, and seeds; avoid duplicates where feasible. Use `tools/simulate_match.py` only for one-off follow-up reproduction.
    - For each simulation, compare the battle report, structured findings, hero text, and questionnaire answers/source clarifications. Identify both hero-rule implementation defects and AI defects, including general AI policy issues and hero-specific AI targeting/scoring/payload issues.
    - Record every suspected defect in a traceable report under `reports/`, including target hero, seed, teams, report path, observed behavior, expected rule/AI behavior, source evidence, severity, and proposed fix. Do not treat audit findings as automatic truth; verify against the hero text/questionnaire first.
    - Fix code from the recorded defects, then rerun targeted tests and enough simulations to verify the repair.
12. Validate in three layers when feasible: changed-rule targeted tests first, then the relevant behavior scenarios and AI audit, then the broader suite with `python -m unittest discover -s tests`.
13. After each completed fix batch, report the concrete repair outcome to the user: what was fixed, what audit/test command was run, whether high-signal findings remain, and what remains next.

## Implementation Priorities

- Treat user clarifications as authoritative game rules.
- Preserve existing user changes in the working tree; do not revert unrelated files.
- Use `apply_patch` for file edits.
- Prefer reusable engine hooks for general mechanics before adding hero-local special cases.
- For grid/range/targeting changes, check backend legality, frontend preview/click selection, and reaction-window target discovery together.
- Treat AI behavior as part of the implementation surface. When fixing a hero, also inspect and update general AI payload generation/scoring and any hero-specific AI logic needed for that hero to use, target, chain, and avoid wasting its mechanics correctly.

## Reference Loading

- Read `references/project-map.md` for file ownership, mechanics already known to be fragile, and update policy.
- Read `references/hero-rule-index.md` for a compact per-hero and per-skill implementation index.
- Read the existing docs directly for rule details instead of duplicating large rule text into this skill.
