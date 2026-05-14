# wujiang_game Project Map

Update this file after every new gameplay requirement or code change in `wujiang_game`. Keep entries concise and durable; do not paste full docs.

## Source Of Truth

- User clarifications in the current task override older docs and implementation.
- This skill is always-on for this workspace/conversation; use it for every request, even when the request is not obviously gameplay-related.
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
- `src/wujiang/heroes/next_five.py`: next hero batch. Current implemented heroes include `ElementHunter`, `UndeadKingLina`, `RockGod`, `DoomlightDragon`, `Masamune`, `Jade`, and `N`.
- `src/wujiang/heroes/registry.py`: hero registry, battle creation, classic multi-hero spawn/board sizing, random mode, start order.
- `src/wujiang/web/multiplayer.py` and `src/wujiang/web/server.py`: room flow, host mode changes, random roster-size configuration, `2~6` seat ownership/team assignment, seat persistence, room/battle serialization, AI-room simulation control, replay serialization, and replay HTTP endpoints.
- `src/wujiang/web/ai.py`: room-side AI policy layer. It builds legal move/attack/skill/reaction/instant payloads from battle previews, keeps hidden-info legality by consuming only viewer-legal battle state, and provides per-hero / per-skill scoring hints on top of a generic candidate framework.
- `src/wujiang/web/replay.py`: room replay recorder. It stores spectator, per-seat, and omniscient battle snapshots by step, serves viewer-legal replay state, and persists finished battles under `replays/`.
- `static/app.js`: client state, action selection, board rendering, targeting previews, multi-cell click handling.
- `static/styles.css` and `static/index.html`: UI layout and presentation.
- `tests/test_battle.py`: battle-engine and hero regression coverage, including turn scheduler and spawn-order assertions.
- `tests/test_multiplayer.py`: multiplayer/session regression coverage.
- `tests/test_behavior.py`: behavior-driven scenarios for public lobby discovery, spectator room visibility, room-to-battle flows, classic and random multi-hero room rules, AI-only room simulation, replay HTTP/control flow, player-facing combat rules, and `quickjs` frontend smoke checks for room-directory rendering/join wiring, random-room roster-size controls, board-overlay reflow, replay toolbar rendering, and battle VFX event consumption in `static/app.js`. Use this layer for future end-to-end-like regressions before adding a full browser automation dependency.
- `.codex/skills/wujiang-game-rules/references/hero-rule-index.md`: compact index of implemented heroes, skills, traits, summons, and key implementation constraints.

## Current Engine Patterns

- Durable default conventions: do not ask again when these apply. If an active skill has no cost text, it costs 0 mana; if it has no use limit/cooldown text, it has no per-turn limit; if a damaging skill/area has no fixed `伤 n` and no `没有伤害`, use current attack; if no `破魔` text, it does not pierce shields; if no ally/enemy qualifier, it affects both sides; `单位` includes heroes, summons, and clones; stat reductions to attack/defense/speed/range floor at 1; modifying the `mana` stat means both max mana and current mana are modified then clamped.
- Durable n-v-n timing rule: one turn belongs to one hero, not one player. One player may control multiple heroes, but only one hero acts in a turn. A hero's `己方回合` means that hero's own turn, and one `轮` for that hero runs from its own turn start to its next own turn start.
- Durable round-duration rule: if docs/rules say an integer `持续 N 轮` for a unit status or integer-round local effect, count it on the affected unit's or owner's own round boundary, not on every global hero turn. Half-round effects such as `1.5轮` / `2.5轮` still need explicit per-skill handling.
- Durable passive-count rule for n-v-n: passive/reaction skills written as `每回合最多 X 次` reset separately in each opposing hero turn, not once per whole round. Mana-costed passives still pay cost on each use. Free passives such as Ion Shield follow the same per-opponent-turn reset; Quantum Shield additionally keeps its own round-based lockout rule.
- Durable classic-room rule: `classic` is the fixed-spawn two-player multi-hero mode. Each side may pick duplicate heroes and an uncapped roster size; summons, clones, and mounts never enter the global turn ring.
- Durable random-room rule: `random` is also a two-seat multi-hero mode. The host sets `n`; when the battle starts, each side receives `n` random heroes sampled without repetition across the whole battle, so the same hero cannot appear twice on the field in one random match.
- Durable multi-seat room rule: online rooms use `2~6` seats but still only two teams. The host manually assigns each seat to red or blue; seat states are `open / human / AI`; a room cannot start while any seat remains open or if there is no human seat.
- Durable seat-ownership rule: a seat actively controls only its own heroes, but same-team seats may still legally use support reactions, walls, protection, knockback, and instant skills to help allied heroes owned by other seats.
- Durable multi-seat team-order rule: in multi-seat team rooms, ignore seat order when building the battle turn ring. Merge all red-team heroes into one sorted list and all blue-team heroes into one sorted list, then alternate `red1 -> blue1 -> red2 -> blue2 ...`; if one side runs out, the other side finishes its remaining fixed slots.
- Durable multi-seat random rule: `random` keeps `n vs n` at the team level. If a team has multiple seats, the host must configure a per-seat random-hero quota whose sum equals that team's `n`.
- Durable AI rule: AI must use only legal viewer information and must not peek hidden information such as stealth, clone truth, or unrevealed statuses. Difficulty has three levels: `easy / standard / aggressive`, with a room default plus optional per-seat overrides.
- Durable replay rule: every completed battle should auto-save a replay under a repo-local `replays/` directory until manually deleted. Replay defaults to legal viewer perspective and may switch to omniscient only after the battle ends. Required controls are pause/resume, speed change, step forward/backward, and timeline scrubbing.
- Durable AI-spectator rule: if no currently present hero belongs to a human seat, the room auto-enters AI simulation / spectator mode. This supports rooms where a human host stays as a pure spectator seat with zero heroes while AI seats fight for both teams.
- Durable timeout-win rule: in every mode, if the battle still has no winner after `20 * initial hero count` completed hero turns, immediately choose a random winning side. `Initial hero count` means the opening non-summon heroes only; summons, clones, and mounts do not increase the cap.
- Implementation compatibility rule: room / roster classic mode uses the multi-hero alternating turn ring, but direct `create_battle("hero1", "hero2")` calls still keep the legacy fixed `8x8` duel path and player-turn bundle semantics used by older engine tests and local helpers.
- Durable classic turn-order rule: at battle start, sort each side's heroes once by `speed desc -> level desc -> attack desc -> defense desc -> range desc -> mana asc -> random`. Compare each side's first hero to decide which side starts, then interleave the two side lists in alternating order for the rest of the battle. Destroyed heroes keep their slot and are skipped; temporary disappear/banish does not remove the slot and must attempt return first.
- Durable classic spawn rule: classic board size and fixed spawn slots depend on both roster size and opening footprint sizes, including mounted-entry or future oversized heroes. Do not hard-code classic mode to 8x8 or assume only 1x1 / 1x2 / 2x2 entries. Spawn slots are assigned by the precomputed classic turn order, not by lobby pick order.
- Durable random spawn rule: `random` reuses the same board-size calculation and interleaved turn-order rules as `classic`, including opening footprint sizing for mounted-entry or future oversized heroes, but replaces fixed symmetric spawns with random per-side spawns inside fair left/right bands.
- Summon default stats: omitted summon stats default to attack 1, defense 1, speed 1, range 1, mana 0.
- Knight durable rule: the first summon in a Knight hero's skill text is the mount unless explicitly overridden. Default mount footprint is vertical `1*2`, and mounts default to the `可乘骑` trait.
- Knight durable rule: Knight heroes enter battle already having summoned their mount and already mounted unless the hero text overrides that. A rider can have only one own mount on the field at once; when that mount is destroyed, the rider must wait through one own turn before summoning it again.
- `可乘骑` durable rule: if mounted unit `a` is ridden by unit `b`, only `a` receives damage and skill effects; when `a` is threatened, `b` can still chain for `a`, and support reactions such as Protection or Knockback resolve on `a`. Mounted state ends when `a` uses a normal move or movement skill to leave `b`.
- `可乘骑` also redirects point targeting: clicking rider `b` still causes the damage or skill effect to resolve on mounted unit `a`.
- Mounted movement durable rule: when mounted unit `a` moves, rider `b` is carried along. Rider `b` may still move independently; if `b` leaves `a`'s occupied cells, mounted state ends immediately.
- Current implementation files for Knight / mount behavior:
- `src/wujiang/engine/core.py`: mounted overlap rules, effect-recipient redirection, rider proxy reactions, payload-aware basic attacks.
  - `src/wujiang/heroes/next_five.py`: `Masamune`, `MotorHorseSummon`, mount cooldown, direction-declared arc attack, free mount `Shensu`, mounted free `Leap`, and `Jade` local-field / use-window mechanics.
  - `src/wujiang/heroes/registry.py`: random-mode spawn uses `entry_footprint_*` so mounted-entry heroes reserve mount space.
  - `static/app.js`: board targeting for attack variants, attack direction choice UI, mounted overlap-aware movement highlighting, mounted rendering order with rider above mount, and sidebar panels that must remain manually expandable even while waiting on the opponent or a chain.
- Mana stat is both spawn mana and mana cap. Current mana clamps to `Unit.max_mana()`.
- `Skill.timing == "instant"` is now live. Instant skills can be used as normal skills during the owner's own turn, and can also be used by the waiting side during an opposing hero's turn. They keep chain speed `3` and do not count as `performed_active_skill`.
- `StatusEffect.tick_scope` supports `owner_turn_start`, `owner_turn_end`, and `any_turn_end`. Use `owner_turn_start` for integer-round statuses that should expire at the owner's next-round boundary instead of after every global turn.
- Viewer battle state may expose `active_units` to the waiting player when that player has available instant skills, and `GameRoom.perform_action(...)` allows those instant-skill payloads to bypass the normal `input_player` gate after seat and skill legality checks.
- `Battle.peek_next_turn_unit()` computes the next alive non-summon hero in the fixed turn ring, and `Battle.to_public_dict()` exposes `next_turn_unit_id`, `next_turn_unit_name`, and `next_turn_player_id` for frontend turn-preview UI.
- `BattleFieldEffect.on_turn_start(...)` is available for start-of-owner-round cleanup or triggers on local fields that should not use global `duration` countdowns.
- Multi-seat implementation note: keep battle `player_id` team-based when possible, but model room seats and seat-owned hero control separately in the room layer. Active-turn permissions should be seat-owned, while same-team support reactions remain allowed across seats.
- Current room implementation note: human seats now own only their own heroes even inside the same battle-side team. Multi-seat room start builds battle heroes with `owner_seat_id`, filters `active_units` by seat, and keeps hidden-info visibility team-based.
- Current room implementation note: AI seats are live in battle now. `GameRoom._resolve_ai_until_human_input()` uses `src/wujiang/web/ai.py` to play active turns, reactions, respawn choices, and instant-skill interrupts for seat-owned units. When no human-owned hero remains present, `GameRoom` auto-advances the room as a paced AI simulation, records replay steps through `src/wujiang/web/replay.py`, exposes `/api/rooms/replay` and `/api/rooms/simulation-control`, and lets the host pause / step / scrub live AI-only battles from the frontend replay toolbar.
- Replay implementation note: prefer event log plus periodic snapshots so rewind/scrub stay responsive without storing a full state for every frame.
- `DamageContext.preserve_followup_effects` is the engine flag for “this hit's damage was prevented, but the rest of the same effect should still apply”.
- `Unit.allow_unbounded_mana` lets a hero's current mana and displayed mana cap grow beyond base mana.
- `Unit.footprint_offsets` supports rectangular and irregular multi-cell units. Use `battle.unit_cells(unit)`, `battle.can_place_unit(...)`, `battle.distance_between_units(...)`, and `battle.unit_distance_to_cell(...)` instead of direct `unit.position` comparisons when a rule may interact with multi-cell units.
- `Unit.set_footprint_cells(...)` and `Unit.set_footprint_offsets(...)` keep dynamic body shapes on the same anchor; a unit's `position` is an anchor, not necessarily an occupied cell after body-shape skills.
- `Unit.allow_overheal` allows specific heroes to heal above `max_health`; default healing still caps at `max_health` for everyone else.
- Direct attacks and point-target skills against multi-cell units must declare an actual occupied target cell, not blindly use the unit anchor.
- Multi-cell movement must validate the destination footprint, not only the anchor/top-left cell. Non-stealthed units cannot overlap other non-stealthed units after movement.
- Range damage against multi-cell units should set `DamageContext.area_cell_hits=battle.unit_hit_count_for_cells(unit, cells)` so each extra occupied cell hit adds +1 attack power.
- Full pierce uses `ignore_shield`; half pierce uses `half_ignore_shield`.
- Shields block chain options unless the queued action has `ignore_shield` or `half_ignore_shield`.
- Reaction windows order all units that can currently chain by current speed descending, then level descending, then random order among exact ties.
- Each skill effect is judged independently for reactions: if that effect would affect an opposing unit, that unit can potentially chain. Multi-effect damage/effect skills must open a separate reaction window for each hostile effect segment. A single effect may contain multiple resolution results, and those results share one reaction check. Effect boundaries are semantic: Paralyzing Glove, Complete Burn, Blizzard, Judgment Fire, and Mana Pull are currently single-effect composites, not split effects. Wind Sand, Backstep Shot, and Fate Kick are multi-effect skills, but their weather/backstep/dash first stages usually do not affect opposing units for now; future speed-3 reactions can chain to a first effect if it does affect an opposing unit.
- Code structure: `QueuedAction` supports `skill_effect` actions with optional `effect_resolver`; use `build_skill_effect_action(...)`, `queue_skill_effect_action(...)`, or `queue_area_damage_effect(...)` for explicit effect stages. Wind Sand weather, Backstep Shot retreat/counter, Fate Kick dash/banish, and Rock Cannon impact damage are represented as explicit effect stages; the first three currently preserve the old UX by resolving their non-hostile or direct-reaction stages without opening extra windows.
- Weather is represented as a `BattleFieldEffect` with `weather_name`; use `battle.unit_in_weather(name, unit)` for unit-specific effects because weather may be local, and `battle.has_weather(name)` only means some weather effect of that name exists. Full-board weather effects set `global_weather = True`; local weather effects must return concrete `affected_cells`.
- Same-name weather does not stack. If a unit/cell is covered by multiple weather effects with the same `weather_name`, including local plus global weather, damage and restrictions are applied once.
- Some skills now use a first-use-started shared use window instead of plain cooldown or per-turn count. `WindowChargeSkill` in `src/wujiang/heroes/common.py` tracks a total use pool over N rounds; leftover uses expire when the window ends, and UI labels should read from `window_*` public fields instead of only `max_uses_per_turn`.
- Multi-target wall reactions validate target count before resource prepayment. This matters for free multi-target wall variants such as `离子盾` and `量子盾`, and also avoids post-prepay validation failures on older wall skills.
- When a hostile queued action goes through a reaction window, the original queued payload now records whether the opponent reacted at all via `payload["enemy_reacted"]`. Hero-local traits can use that during final skill resolution without re-inspecting the chain window.
- Stealth hides logs and prevents direct hostile targeting, but point-cell skills can still hit if their cells overlap the stealthed unit. Stealthed units can still chain against enemy actions that affect them. Sandstorm suppresses all stealth statuses in its covered cells and removes only the stealth part, not non-stealth buffs from the same skill.

## Current Hero Rules To Preserve

- Ellie:
  - Experiment uses the target's own round cadence for both the +2 all-stats buff and the death countdown. It does not tick down on every global turn end.
  - Crystal Ball lasts 4 of Ellie's own rounds, not 4 global hero turns.
- ElementHunter:
  - Complete Burn and Blizzard use remote rectangle selection that can truncate at board edges.
  - Plant Growth affects normal movement only, including flying units; extra cost triggers when the step starts inside the area.
  - Plant Growth lasts until ElementHunter's next own turn starts; it does not expire after two unrelated global turns.
  - EarthWalker costs 0 mana; clones cannot attack or use skills.
  - Great Fire Funeral fields do not stack overlapping area damage.
- UndeadKingLina:
  - Occupies `2*2`; attacks and range checks can originate from any occupied cell.
  - Rending is a one-battle skill that targets one cell by range, deals current attack damage, and pierces shields.
  - Wind Sand selects a remote `2*4` or `4*2` area and creates one-round Sandstorm weather if the damage area contains a unit.
  - Sandstorm: earth units/summons are immune to weather damage; flying non-earth units take `1/8`; other non-earth units take `1/16`; stealth cannot be used and active stealth is suppressed; evasion has no valid one-step move because its distance is reduced by 1.
  - Crazy Sand damages a straight line of 5 cells and teleports the caster to the 6th cell; invalid directions must not be selectable when the 6th anchor is out of bounds or occupied.
  - Lina basic attacks have half pierce, can attack twice, and lock the declared attack target until that target is destroyed.
  - Lina's destroy reward triggers once per turn only when her own attack or skill destroys a hero or a unit with current defense at least 4; it resets move/attack and gains the target's remaining current mana.
  - Lina prevents enemy healing within a `7*7` area around her and gains natural recovery in Sandstorm while not stealthed; natural mana recovery gains 1 mana.
- RockGod:
  - Occupies base `2*2`; body can become irregular through Rock Absorb and shrink through Rock Cannon. Remaining body must be orthogonally connected.
  - RockGod has a local Sandstorm aura covering the union of each occupied cell's surrounding `9*9`; local Sandstorm displays on board and only affects units inside those cells. RockGod sandstorm is ordinary same-name Sandstorm for stacking, so multiple RockGod auras and global Sandstorm do not deal duplicate weather damage.
  - Dragon Breath costs 2 mana, twice per turn, and selects a nearby edge-truncated `2*2` area that touches the caster orthogonally or diagonally.
  - Rock Absorb is once per turn; player chooses one stat, all units in RockGod's local Sandstorm except RockGod get that stat -1 for one round unless a shield blocks that unit's effect, and RockGod gains that stat by the successfully affected unit count even if a non-mana stat was already floored at 1. Mana absorption changes max mana and current mana. Shield reactions can block Rock Absorb per target.
  - Rock Absorb growth uses explicit selected cells, allows irregular shapes, requires orthogonal connectivity, and adds only as many legal cells as possible.
  - Rock Absorb footprint restoration attempts the base `2*2` at the current anchor. If a base cell is occupied or out of bounds, skip that cell and do not move RockGod to restore it.
  - Rock Cannon costs 0, has no use-count limit, selects one or more current body cells plus a direction, requires at least one body cell remain and the remainder stay orthogonally connected, rejects directions blocked by remaining own body, then each fired cell independently travels until hitting a unit or exiting at the boundary and resolves its own surrounding `3*3` damage for `3 + fired_cell_count` without pierce. Each impact damage is a separate chainable effect.
- DoomlightDragon:
  - Occupies `2*2`, has flying, and can heal above 1 hp.
  - Stone Wall is rule-identical to Light Wall.
  - Remote Dragon Breath is a range-based edge-truncated `2*2` damage skill, not a near-body pattern.
  - Doom Light is a once-per-battle remote `7*7` pure effect that pierces shields, lasts 4 rounds, blocks healing, and deals half-current-hp damage at each affected unit's own turn start.
  - Doom Light tick damage heals Doomlight Dragon by the same amount.
  - Units that attack Doomlight Dragon, damage it, or are damaged by it receive Doom Light, but Doom Light tick damage must not recursively refresh/reapply Doom Light.
  - Apocalypse is once per turn; the player explicitly chooses `n`, pays `n` hp where `n < current hp`, and then targets a remote edge-truncated `n*n` area for attack `+ n` shield-piercing damage. Example: current hp `1.25` still allows `n = 1`.
- Masamune:
  - Knight start rule is active: he enters battle already mounted on his own Motor Horse.
  - Arc Attack uses player-declared 8-direction facing. Orthogonal directions attack the outer 3-cell row; diagonal directions attack the corresponding 3-cell corner arc.
  - Mounted Free Leap is implemented as a visible 0-mana once-per-turn Leap action while mounted.
- Jade:
  - Missile uses a first-use-started 2-round window with expiring leftover uses.
  - Ion Shield is a free wall-like passive reaction with up to 2 casts per own cycle; one cast may protect multiple threatened allies.
  - Quantum Shield is a free wall-like passive reaction with up to 3 casts in a usable own cycle; if used at all, the next own cycle is unavailable and the following cycle becomes usable again.
  - Laser uses remote edge-truncated `2*10` / `10*2` area selection.
  - Plasma Thruster is a straight flying move to the 5th cell, except boundary truncation allows the last in-bounds cell; occupied destinations remain invalid.
  - Stance is a dynamic visible local field; it does not protect Jade, only blocks damage, and only during the next enemy turn after casting.
  - Reactive Overclock checks damaging skills after enemy chain resolution; if any original enemy target took no damage, that skill gains +1 permanent future use from Jade's next own turn, once per turn per skill.
- N:
  - `磁力波` is the first formal instant skill: a range-based, edge-truncated `3*3` current-attack area skill paid with 2 mana points, usable once in each hero turn, including the opponent's current turn.
  - `攻击魔力点+1` triggers on every basic-attack declaration, even if the later damage is blocked or the declared target cell ends up empty.
  - `每回合开始时决定攻击数=魔+1` snapshots once at own turn start using `floor(current_mana) + 1`; mid-turn mana changes do not alter that turn's attack cap.
  - `魔无上限` means both current mana and displayed mana cap are unbounded for N.
  - `魔大于0时不受到伤害，受到伤害时魔-1` applies per damage instance, blocks damage only, and still lets that same effect's non-damage follow-up apply.

## Frontend Coupling

- If backend target selection uses `occupied_cells`, update `static/app.js` previews/click helpers too.
- `unitOccupiedCells(unit)` is the frontend fallback-compatible helper for multi-cell units.
- Multi-cell units render as one footprint-spanning board piece, not one piece per occupied cell. Irregular pieces use `occupied_cells` to fill only actual body cells within the bounding box. Keep board cells explicitly grid-positioned so overlay pieces do not disturb grid auto-placement.
- Keep action wheel buttons and board alerts at a higher CSS stacking layer than footprint-spanning board pieces; large units can otherwise visually cover skill buttons even when pointer events are disabled.
- Action wheel and board-alert overlays are stage-level absolute layers, so any zoom, board-stage scroll, or window resize path must reschedule overlay positioning after layout settles.
- Action wheel placement must also clamp back inside the visible `board-stage` bounds when the selected unit is near an edge; do not let top or side buttons render outside the scroll viewport.
- Battle VFX are also stage-level overlays driven from backend `visual_events`. Engine changes that alter attack/skill/effect timing or defensive cancellation should keep `visual_events` synchronized, and frontend changes must preserve VFX positioning after board rerenders.
- Move-path previews should show a distinct final-footprint highlight and translate clicks on any final occupied footprint cell back to the backend anchor payload.
- Frontend move blocking must mirror `battle.can_place_unit(...)`: test the whole footprint, allow overlaps only for stealth exceptions, and reject out-of-bounds footprints.
- Pattern-cell skills must expose target cells through skill previews and `get_target_cells_for_payload` so chain windows show the affected area.
- Custom frontend selection modes currently include `stat_cells` for Rock Absorb, `body_direction` for Rock Cannon, and `choice_pattern` for skills that need both an explicit option choice and a shaped area selection such as Apocalypse. Backend remains authoritative for legality. Rock Cannon body-cell selection must visibly highlight selectable and selected occupied body cells, including large/irregular footprint cells.
- Multiplayer room identity is stored in both `localStorage` and `sessionStorage` and auto-loaded from `?room=...`; if a stored token no longer maps to a viewer seat, clear it so the user is not stuck as a spectator with a stale token.
- Current room / online implementation remains two-seat, but classic mode now needs per-seat multi-hero rosters and hero-counter lobby UI. Preserve the per-hero interpretation of `回合`, `己方回合`, and `轮`.
- In running or full rooms, `/api/rooms/join` may reclaim an occupied seat by exact normalized player name and returns the original token/player id. Open lobbies still let a same-name player claim an open second seat. The frontend exposes a recovery button when the current nickname matches an occupied seat but the viewer has no token.
- `/favicon.ico` intentionally returns `204 No Content`; the icon is not gameplay-critical and should not create distracting 404 console noise.

## Testing Policy

- Add focused tests for every durable gameplay rule change.
- After every gameplay, room-flow, or player-visible UI change, add or update at least one behavior scenario that exercises the changed feature from the player's point of view.
- Prefer `tests/test_behavior.py` for scenario-style coverage that crosses room APIs, turn flow, chain windows, targeting, or frontend-facing state payloads.
- Use direct `DamageContext` tests only for engine-level behavior; prefer `battle.perform_action(...)` for player-facing skill behavior.
- Run targeted tests for the changed rule first.
- Run the relevant behavior scenarios after targeted tests.
- Run `python -m unittest discover -s tests` before final response when feasible.
- `git diff --check` may show LF/CRLF warnings on this Windows repo; treat actual whitespace errors separately from those warnings.
