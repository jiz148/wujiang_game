# wujiang_game Project Map

Update this file after every new gameplay requirement or code change in `wujiang_game`. Keep entries concise and durable; do not paste full docs.

## Source Of Truth

- User clarifications in the current task override older docs and implementation.
- Update repo docs before coding when implementing a new hero or durable gameplay rule.
- Use `docs/通用规则.md` for shared battle rules.
- Use `docs/通用技能和特性说明.md` for common skills, traits, and keywords.
- Use `docs/武将说明.md` for per-hero implementation notes.
- Use `docs/武将游戏规则.md` for broad prototype/gameplay rules.
- Use `references/hero-rule-index.md` for a compact per-hero and per-skill implementation index; keep it synchronized with docs and code.

## Code Map

- `src/wujiang/engine/core.py`: generic battle engine, unit state, action queue, chain windows, targeting, shields, damage, healing, field effects, movement, public state.
- `src/wujiang/heroes/common.py`: reusable skills, traits, statuses, selection helpers, line/pattern helpers.
- `src/wujiang/heroes/first_five.py`: first hero batch and their special mechanics.
- `src/wujiang/heroes/next_five.py`: next hero batch. Current implemented heroes include `ElementHunter`, `UndeadKingLina`, and `RockGod`.
- `src/wujiang/heroes/registry.py`: hero registry, battle creation, random mode, spawn logic, start order.
- `static/app.js`: client state, action selection, board rendering, targeting previews, multi-cell click handling.
- `static/styles.css` and `static/index.html`: UI layout and presentation.
- `tests/test_battle.py`: battle-engine and hero regression coverage.
- `tests/test_multiplayer.py`: multiplayer/session regression coverage.
- `.codex/skills/wujiang-game-rules/references/hero-rule-index.md`: compact index of implemented heroes, skills, traits, summons, and key implementation constraints.

## Current Engine Patterns

- Durable default conventions: do not ask again when these apply. If an active skill has no cost text, it costs 0 mana; if it has no use limit/cooldown text, it has no per-turn limit; if a damaging skill/area has no fixed `伤 n` and no `没有伤害`, use current attack; if no `破魔` text, it does not pierce shields; if no ally/enemy qualifier, it affects both sides; `单位` includes heroes, summons, and clones; stat reductions to attack/defense/speed/range floor at 1; modifying the `mana` stat means both max mana and current mana are modified then clamped.
- Mana stat is both spawn mana and mana cap. Current mana clamps to `Unit.max_mana()`.
- `Unit.footprint_offsets` supports rectangular and irregular multi-cell units. Use `battle.unit_cells(unit)`, `battle.can_place_unit(...)`, `battle.distance_between_units(...)`, and `battle.unit_distance_to_cell(...)` instead of direct `unit.position` comparisons when a rule may interact with multi-cell units.
- `Unit.set_footprint_cells(...)` and `Unit.set_footprint_offsets(...)` keep dynamic body shapes on the same anchor; a unit's `position` is an anchor, not necessarily an occupied cell after body-shape skills.
- Multi-cell movement must validate the destination footprint, not only the anchor/top-left cell. Non-stealthed units cannot overlap other non-stealthed units after movement.
- Range damage against multi-cell units should set `DamageContext.area_cell_hits=battle.unit_hit_count_for_cells(unit, cells)` so each extra occupied cell hit adds +1 attack power.
- Full pierce uses `ignore_shield`; half pierce uses `half_ignore_shield`.
- Shields block chain options unless the queued action has `ignore_shield` or `half_ignore_shield`.
- Weather is represented as a `BattleFieldEffect` with `weather_name`; use `battle.unit_in_weather(name, unit)` for unit-specific effects because weather may be local, and `battle.has_weather(name)` only means some weather effect of that name exists. Full-board weather effects set `global_weather = True`; local weather effects must return concrete `affected_cells`.
- Stealth hides logs and prevents direct hostile targeting, but point-cell skills can still hit if their cells overlap the stealthed unit.

## Current Hero Rules To Preserve

- ElementHunter:
  - Complete Burn and Blizzard use remote rectangle selection that can truncate at board edges.
  - Plant Growth affects normal movement only, including flying units; extra cost triggers when the step starts inside the area.
  - EarthWalker costs 0 mana; clones cannot attack or use skills.
  - Great Fire Funeral fields do not stack overlapping area damage.
- UndeadKingLina:
  - Occupies `2*2`; attacks and range checks can originate from any occupied cell.
  - Rending is a one-battle skill that targets one cell by range, deals current attack damage, and pierces shields.
  - Wind Sand selects a remote `2*4` or `4*2` area and creates one-round Sandstorm weather if the damage area contains a unit.
  - Sandstorm: earth units/summons are immune to weather damage; flying non-earth units take `1/8`; other non-earth units take `1/16`; stealth cannot be used; evasion has no valid one-step move because its distance is reduced by 1.
  - Crazy Sand damages a straight line of 5 cells and teleports the caster to the 6th cell; invalid directions must not be selectable when the 6th anchor is out of bounds or occupied.
  - Lina basic attacks have half pierce, can attack twice, and lock the declared attack target until that target is destroyed.
  - Lina's destroy reward triggers once per turn only when her own attack or skill destroys a hero or a unit with current defense at least 4; it resets move/attack and gains the target's remaining current mana.
  - Lina prevents enemy healing within a `7*7` area around her and gains natural recovery in Sandstorm while not stealthed.
- RockGod:
  - Occupies base `2*2`; body can become irregular through Rock Absorb and shrink through Rock Cannon. Remaining body must be orthogonally connected.
  - RockGod has a local Sandstorm aura covering the union of each occupied cell's surrounding `9*9`; local Sandstorm displays on board and only affects units inside those cells.
  - Dragon Breath costs 2 mana, twice per turn, and selects a nearby edge-truncated `2*2` area that touches the caster orthogonally or diagonally.
  - Rock Absorb is once per turn, shield-piercing effect; player chooses one stat, all units in RockGod's local Sandstorm except RockGod get that stat -1 for one round, and RockGod gains that stat by the affected unit count even if a non-mana stat was already floored at 1. Mana absorption changes max mana and current mana.
  - Rock Absorb growth uses explicit selected cells, allows irregular shapes, requires orthogonal connectivity, and adds only as many legal cells as possible.
  - Rock Cannon costs 0, has no use-count limit, selects one or more current body cells plus a direction, requires at least one body cell remain and the remainder stay orthogonally connected, rejects directions blocked by remaining own body, then each fired cell independently travels until hitting a unit or exiting at the boundary and resolves its own surrounding `3*3` damage for `3 + fired_cell_count` without pierce.

## Frontend Coupling

- If backend target selection uses `occupied_cells`, update `static/app.js` previews/click helpers too.
- `unitOccupiedCells(unit)` is the frontend fallback-compatible helper for multi-cell units.
- Multi-cell units render as one footprint-spanning board piece, not one piece per occupied cell. Irregular pieces use `occupied_cells` to fill only actual body cells within the bounding box. Keep board cells explicitly grid-positioned so overlay pieces do not disturb grid auto-placement.
- Keep action wheel buttons and board alerts at a higher CSS stacking layer than footprint-spanning board pieces; large units can otherwise visually cover skill buttons even when pointer events are disabled.
- Move-path previews should show a distinct final-footprint highlight and translate clicks on any final occupied footprint cell back to the backend anchor payload.
- Frontend move blocking must mirror `battle.can_place_unit(...)`: test the whole footprint, allow overlaps only for stealth exceptions, and reject out-of-bounds footprints.
- Pattern-cell skills must expose target cells through skill previews and `get_target_cells_for_payload` so chain windows show the affected area.
- Custom frontend selection modes currently include `stat_cells` for Rock Absorb and `body_direction` for Rock Cannon; backend remains authoritative for legality.

## Testing Policy

- Add focused tests for every durable gameplay rule change.
- Use direct `DamageContext` tests only for engine-level behavior; prefer `battle.perform_action(...)` for player-facing skill behavior.
- Run targeted tests for the changed rule first.
- Run `python -m unittest discover -s tests` before final response when feasible.
- `git diff --check` may show LF/CRLF warnings on this Windows repo; treat actual whitespace errors separately from those warnings.
