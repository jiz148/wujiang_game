---
name: wujiang-game-rules
description: Project skill for the wujiang_game browser tactics game. Use when working in this repo on hero implementation, hero rules, skill/trait mechanics, battle-engine behavior, documents, tests, frontend targeting/preview behavior, or any user request that changes gameplay requirements. Also use when a new durable rule clarification arrives, because this skill must be updated after every new requirement or code change in this project.
---

# Wujiang Game Rules

Use this skill as the project onboarding and maintenance workflow for `C:\Users\jiz14\TeamGH\wujiang_game`.

## Mandatory Workflow

1. Read `references/project-map.md` at the start of any hero, rule, battle-engine, or gameplay UI task.
2. Read `references/hero-rule-index.md` when the task involves a specific hero, skill, trait, or current implementation lookup.
3. Consult the repo docs before coding. Prefer `docs/通用规则.md`, `docs/通用技能和特性说明.md`, `docs/武将说明.md`, and `docs/武将游戏规则.md`.
4. For new heroes, finish or update documentation before coding. Implement heroes one at a time. If a hero rule is ambiguous and cannot be safely inferred from docs/code, ask the user before implementing that hero.
5. Keep rule docs, engine code, frontend previews, and tests synchronized. Do not change only code when the user clarified a durable rule.
6. After every new gameplay requirement or code change in this repo, update this skill in the same turn. Prefer concise updates to `references/project-map.md` and `references/hero-rule-index.md`; update `SKILL.md` only when the workflow itself changes.
7. Validate with targeted tests first, then the broader suite when feasible: `python -m unittest discover -s tests`.

## Implementation Priorities

- Treat user clarifications as authoritative game rules.
- Preserve existing user changes in the working tree; do not revert unrelated files.
- Use `apply_patch` for file edits.
- Prefer reusable engine hooks for general mechanics before adding hero-local special cases.
- For grid/range/targeting changes, check backend legality, frontend preview/click selection, and reaction-window target discovery together.

## Reference Loading

- Read `references/project-map.md` for file ownership, mechanics already known to be fragile, and update policy.
- Read `references/hero-rule-index.md` for a compact per-hero and per-skill implementation index.
- Read the existing docs directly for rule details instead of duplicating large rule text into this skill.
