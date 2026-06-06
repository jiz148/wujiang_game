# Hero Rule Index

Use this as a compact implementation index, not the full rule source. For exact wording, read `docs/武将说明.md` and `docs/通用技能和特性说明.md`.

Update this file whenever a hero, skill, trait, or durable gameplay rule changes.

## Durable Default Rules

- If an active skill has no cost text, it costs 0 mana; if any skill has no written per-turn/per-battle/cooldown limit, it has no use-count limit.
- For future heroes with magic-point mechanics, if no explicit initial magic-point value is written, initial magic points equal base mana.
- Rules may be 2-player or n-v-n. One player may control multiple heroes, but only one hero acts in a turn.
- `回合` belongs to a single acting hero. `己方回合` means that hero's own turn. One `轮` for that hero runs from its own turn start to its next own turn start.
- If a rule says an integer `持续 N 轮` for a status or local effect, it counts on that affected unit's or owner's own round boundary, not on every global hero turn. Half-round effects such as `1.5轮` / `2.5轮` still follow their explicit per-skill handling.
- Online rooms use `2~6` seats but still only two teams; seat ownership is separate from battle-side membership.
- A seat actively controls only its own heroes, but same-team seats may still use support reactions and instant skills to help allied heroes owned by other seats.
- In multi-seat team rooms, merge each team's heroes into one sorted list and alternate by team, not by seat order.
- `random` stays `n vs n` at the team level; if a team has multiple seats, the host sets a per-seat quota whose sum equals that team's `n`.
- AI uses only legal viewer information, never hidden information, and has three difficulty tiers: `easy / standard / aggressive`.
- AI attack / hostile-skill candidates assume the opponent does not chain; a candidate is ignored unless it can damage, consume shield/resources, or apply an effective effect to at least one enemy. Consuming a normal shield or leftover temporary shield from Protection-like effects counts as effective impact. Multi-target candidates only need one effectively affected enemy.
- Current room AI can already play all currently implemented heroes in active turns, chain reactions, respawn prompts, and instant-skill windows via the shared room AI layer.
- Every finished battle should save a replay under `replays/`; replay opens in legal viewer perspective and may switch to omniscient only after battle end.
- If no currently present hero belongs to a human seat, the room auto-enters AI simulation / spectator mode; the host may pause, resume, step, scrub, and change replay speed while the replay log keeps growing live.
- In every mode, if the battle still has no winner after `20 * initial hero count` completed hero turns, the engine immediately picks a random winning side. `Initial hero count` means only the opening non-summon heroes.
- `classic` mode is the fixed-spawn two-player multi-hero mode. Each side may pick duplicate heroes, there is no hard roster cap, and summons / clones / mounts do not enter the independent turn ring.
- `random` mode is also a two-player multi-hero mode. The host sets `n`; when the room starts, each side receives `n` random heroes with no duplicates anywhere in that battle.
- In `classic`, each side's roster is sorted once at battle start by `速 desc -> 等级 desc -> 攻 desc -> 守 desc -> 范 desc -> 魔 asc -> 随机`, then the two side lists are interleaved in alternating order for the entire battle.
- In `random`, battle-start sorting and interleaving use the exact same rule as `classic`.
- In `classic`, destroyed heroes keep their fixed slot and are skipped when that slot comes up. Temporarily disappeared / banished heroes do not lose their slot; when their slot comes, resolve their return first and then continue that hero's turn if they can reappear.
- In `classic`, spawn slots are assigned by the precomputed turn order, not by lobby pick order. Board size and fixed spawn layout depend on both roster size and opening footprint sizes.
- In `random`, board size still depends on both roster size and opening footprint sizes, but spawn positions are randomized inside fair per-side regions instead of using fixed symmetric slots.
- If a damaging skill/area has no fixed `伤 n` and no `没有伤害`, it uses the caster's current attack.
- If no `破魔` text is written, the damage/effect does not pierce shields.
- If no ally/enemy qualifier is written, the effect applies to both sides.
- Excel or sheet text `炎` maps to the existing `火` attribute.
- Non-square area text such as `3*6` without `身前` is a remote range-based rectangle and can be placed in either orientation.
- `攻击带有以下破魔效果` and `带有破魔的 X 效果` make only the listed / named follow-up effect piercing; they do not make the whole attack or skill damage piercing.
- If a hero writes a known common skill or trait and adds no extra rule text, implement it exactly as the common version.
- Only skills whose text starts with / explicitly says `被动技能` are passive chain skills. Other skills are not passive unless they say `随时使用` or another timing rule.
- Skills prefixed with `￥` or `¥` are big moves / ultimates; by default they are once per battle unless the text writes another use count, cooldown, or reset.
- Passive skills without extra support/protection/other trigger wording can only chain when their owner is about to be affected by an enemy action.
- `随时使用` skills are formal `instant` skills: they can be used normally during the owner's own turn, and can also be used as chain-speed-3 skills during an opposing hero's current turn. They do not count as `主动技能` use for traits that key on active-skill usage.
- `单位` includes heroes, summons, and clones.
- `武将` means a non-summon non-clone hero body only; summons and clones do not count.
- `周围` without extra text means the 8 cells at distance 1 around a unit's occupied cells, excluding its own occupied cells.
- `攻击` without extra text means basic attack / 普攻, not skills.
- Skills that directly select one or more units must select targets both within the caster's `范` (or the skill's own written point-unit range, such as `范 5`) and along a horizontal, vertical, or diagonal line from any caster occupied cell to any target occupied cell, unless the skill explicitly overrides this. Magic Wall, Light Wall, and Stone Wall follow this direct-unit targeting rule.
- `魔免` prevents enemy skill damage and enemy skill follow-up effects; it does not prevent basic attacks, weather, or field effects.
- Passive/reaction skills written as `每回合最多 X 次` reset separately in each opposing hero turn, not only once per whole round. If the passive costs mana, each use still pays its cost.
- Each hero turn has a start phase. Resolve start-of-turn effects first, then in that same start phase ask whether to use start-phase toggle skills. Toggle skills alternate open/close on each use and become unavailable after normal movement, attacks, or other active actions.
- `不能行动` means the unit cannot normal move, cannot basic attack, cannot use active skills, and cannot use passive chain skills.
- `没有移动次数限制` means the unit may split normal movement into any number of normal moves, but total normal movement distance in that turn still cannot exceed current speed.
- `每回合移动次数 +1` grants one extra normal-move action per turn; each normal move is still separately capped by current speed.
- If a summon omits written stats, its omitted defaults are attack 1, defense 1, speed 1, range 1, mana 0.
- For Knight heroes, the first summon written in their skills is the mount unless the text explicitly says otherwise. If that mount has no explicit footprint, it defaults to a vertical `1*2` footprint and has the default `可乘骑` trait.
- Knight heroes enter battle as if they had already used their mount-summon skill and were already mounted, unless the hero text explicitly overrides that.
- A Knight hero can have at most one own mount on the field at a time. If that mount is destroyed, the rider must wait through one of their own turns before summoning that mount again.
- `可乘骑`: if mounted unit `a` is being ridden by unit `b`, only `a` receives damage and skill effects; when `a` is threatened, `b` may still chain for `a`, and support reactions such as Protection or Knockback resolve on `a`. The mounted state ends when `a` takes a normal move or a movement skill that leaves `b`.
- `可乘骑` also redirects direct target selection: even if a player clicks rider `b`, damage and skill effects still resolve on mounted unit `a`.
- Mounted movement rule: if mounted unit `a` moves, rider `b` is carried with it. Rider `b` may also move independently; if `b` leaves `a`'s occupied cells, the mounted state ends immediately.
- Reductions to attack, defense, speed, and range floor at 1 unless explicitly stated otherwise.
- Modifying the `mana` stat changes both max mana and current mana, then clamps current mana to the new cap.
- Same-name weather does not stack. Multiple local weather effects and local plus global weather with the same weather name apply damage and restrictions once.
- Sandstorm suppresses every active stealth status in its covered cells. It removes only the stealth part; non-stealth effects from the same skill, such as Into Darkness' next-basic-attack buff, remain.
- When multiple units can chain to the same action, the reaction order is current speed descending, then level descending, then random among exact ties.
- Each skill effect is judged independently for reactions: if that effect would affect an opposing unit, that unit can potentially chain. Multi-effect damage/effect skills open a separate reaction window for each hostile effect segment; later effect segments wait for earlier segment chains and resolution. A single effect may contain multiple resolution results, and those results share one reaction check. Effect boundaries are semantic: Paralyzing Glove, Complete Burn, Blizzard, Judgment Fire, and Mana Pull are currently single-effect composites, not split effects. Wind Sand, Backstep Shot, and Fate Kick are multi-effect skills, but their weather/backstep/dash first stages usually do not affect opposing units for now; future speed-3 reactions can chain to a first effect if it does affect an opposing unit. Code should model explicit stages with `skill_effect` actions when an action truly has multiple effects.

## Excel Roster Layer

- File: `src/wujiang/heroes/excel_roster.py`; generated data: `src/wujiang/heroes/excel_roster_data.py`.
- Registers all 370 answered source-Excel heroes that do not yet have handwritten hero classes. Codes are stable row-based `excel_r###`, for example `excel_r020` is source row 20.
- Current coverage is intentionally limited to source stats/raw text, deterministic footprint basics, and exact pure-common skill/trait fragments. Special fragments with extra text, bracketed extensions, questionnaire answers, or hero-specific timing/targeting are kept in raw text and must be implemented in later batches instead of being silently mapped to ordinary common behavior.
- Pure common skill mappings currently include Light/Magic/Stone Wall, Protection, Evasion, Shensu, Fly Leap, Harden, Pierce, Remote Pierce, Knockback, Machine Gun, Drain Mana, Recover Mana, Magic Shield, Split, Chain Pull, Dragon Breath, Remote Dragon Breath, Defend Twice, Heal, Baptism, Chant, Stealth, Backstep Shot, and pure-name Missile/Ion Shield/Laser.
- Pure common trait mappings currently include Flying, Pass-through Movement, Physical Immunity, permanent Magic Immunity, Attack Lifesteal, Attack Mana Drain, Arc Attack, Block/Counter, natural/stationary recovery variants, half-pierce and full-pierce basic attacks, and fixed attack-count traits.
- Completed special Excel rules:
  - `excel_r026` / 最后的守护者: `guardian_finale` is a once-per-battle ultimate. It applies permanent `终结`: own turn-end hp -1/4 without shield consumption, attack +3, speed +3, active skills cost 0 mana, basic attacks pierce shields and lifesteal 1/4 after damage, and non-damage effects including allied buffs/healing are blocked.
  - `excel_r023` / 芙蕾: `frey_quick_flash` is twice per turn, range 5, teleports to a legal cell around a selected unit, then deals surrounding current-attack skill damage. `frey_god_stab` is a once-per-battle up-to-4-cell line skill. `frey_lion_spear` is once per turn and damages all diagonal rays up to 4 cells. `FreySkillPierceTrait` makes all Frey skills pierce shields. `FreyDamageCapTrait` implements R023-Q001: every damage instance, including attack, skill, weather, field, and fixed hp loss, is capped at 1/4 hp loss.
  - `excel_r036` / 制裁者: `punisher_heal` costs 1 mana and heals 1/4 plus restores 1 mana per R036-Q001. `SkySanctuaryAuraTrait` creates a local `天空圣域` weather aura over the surrounding `11*11`. `sanctuary_banish` is cooldown 3, affects enemies in `天空圣域`, pierces shields, and blocks basic attacks plus active skills until target next own turn end. `sanctuary_judgment` is a once-per-battle ultimate; every enemy in `天空圣域` takes 5 skill-damage instances, each with attack power equal to that target's current attack.
  - `excel_r047` / 次郎坊: `hundred_bird_burial` is cooldown 2, remote `3*6` / `6*3`, damage `attack + 2` without piercing, and applies a piercing 2-owner-turn movement-skill lock. R047-Q001 defines movement skills as any skill that moves self, targets, summons, or swaps positions. `JiroboAfterAttackTrait` grants defense +1 through next own turn end after each basic attack and exposes one current-turn `jirobo_follow_step` move of up to 2 cells.
  - `excel_r056` / 蕾米: `remi_chaos` is a once-per-battle exact-3-cell move followed by surrounding `3*3` current-attack skill damage without piercing. `summon_remi_bat` summons a flying `remi_bat` in a surrounding legal cell with stats attack 3 / defense 1 / speed 3 / range 1 and turn-ready on entry. `RemiUndyingTrait` implements R056-Q001: each lethal damage instance can leave Remi at 1/4 hp after spending 1 mana; if that spend leaves mana at 0, she is destroyed. Her `吸血` and `普攻吸魔` fragments map to basic-attack lifesteal and mana drain.
  - `excel_r070` / 天蟹: `heaven_punishment` is cooldown 2, remote `5*5`, and applies a piercing no-duration `SkillDisabledStatus` to one enemy in the area. R070-Q001 restricts choices to currently public active skills.
  - `excel_r071` / 雪巨人: `snow_avalanche` is cooldown 2, remote `2*6` / `6*2`, current-attack damage, and applies one `雪崩` cannot-act status to hit surviving units through their next own turn end. `big_avalanche` is a once-per-battle global weather marker `大雪崩` with duration 5 using the existing field-effect weather countdown.
  - `excel_r093` / 魔神凯撒: `large_pierce` implements `穿刺（大）` as ordinary Pierce enlarged from a 2-cell to a 3-cell straight line, with unchanged cost/use/damage rules. `kaiser_fist` is cooldown 2 rounds, range 6, damage `attack + 1`, non-piercing, and grants the caster +2 mana if it ultimately deals no damage.
  - `excel_r094` / 杂音: `interference` is cooldown 2, remote `10*10`, destroys clones/copies, and changes summon control to the caster side per R094-Q002 answer `a` without rebuilding turn slots. `noise_wave` costs 1 mana, once per turn, remote `3*3`, no damage, piercing, speed -1 and movement-skill lock until target next own turn end.
  - `excel_r113` / 诚实天使欧内斯特: `purify_mana` is cooldown 5, direct enemy target, non-piercing, and reduces target mana by up to 5 if defenses do not cancel it. `sacred_duel` is cooldown 5, piercing, and applies 5 owner-turn-end rounds of no movement plus active-skill lock.
  - `excel_r118` / 零崎: `zero_dash` is once per turn, direction-declared, exact 8-cell straight skill movement that can pass through units. `ZeroPassThroughTrait` implements R118-Q001: each unit crossed by the movement path takes one current-attack damage instance, and Zerozaki gains 0.5 mana for each crossed unit.
  - `excel_r123` / 风魔: `fuma_pursuit` is cooldown 3, direction-declared, pierces shields, damages the first 4 cells, and moves to the legal 5th cell. `fuma_trap` costs 0.5 mana once per turn and creates a persistent trap field; every enemy turn end, the trap cell plus surrounding `3*3` takes attack-power-3 piercing field damage until Fuma leaves. `fuma_shuriken` is once per turn, range 3, and damages a chosen 3-cell straight line. `FumaSkillManaTrait` implements R123-Q001 with public backend randomness: each active skill use has a 1/2 chance to restore 1 mana.
  - `excel_r136` / 一刀斩。胧月: `true_blade_air_slash` costs 1.5 mana, once per turn, moves exactly 5 straight cells to a legal landing, then deals target-defense+1 piercing skill damage to a selected enemy; if not cancelled, the caster gains mana equal to the target's current mana without draining it. `oboro_meditate` is cooldown 3 and grants +1.5 current mana.
  - `excel_r137` / 不死小子: `undead_boy_devour` is cooldown 2, piercing, halves target current hp as direct skill damage, then heals the caster by its current hp if not cancelled. `UndyingQuarterTrait` implements R137-Q001 answer `a`: each lethal damage instance can leave the unit alive at 1/4 hp if it started that instance at at least 1/2 hp.
  - `excel_r139` / 丰收之神。索拉: `holy_wall` is Light Wall under the source name. `illumination_light` is cooldown 2, affects enemy hero bodies in Sola's surrounding `11*11`, uses damage value 4, and pierces only dark-attribute targets. `SolaHarvestAuraTrait` creates a field aura so allied units in the surrounding `11*11` heal 1/4 and gain 1 mana at their own turn start.
  - `excel_r158` / 魔战士: `martial_god_seal` is cooldown 2, self attack/defense/speed/range/max mana +2, current mana +2, heal 1/2, and lasts until the next enemy hero turn ends per R158-Q001 answer `a`. `hell_slash` is a once-per-battle straight line up to 10 cells that deals current-attack non-piercing skill damage.
  - `excel_r166` / E。电击人: `electric_wind` is cooldown 2, front `2*3` in four orthogonal facings, and applies 2 owner-turn-end rounds of no skill use plus speed -1. `AutoElectricWindTrait` implements R166-Q001 answer `a`: at own turn start, apply Electric Wind to units in the surrounding `5*5` when possible, otherwise skip.
  - `excel_r187` / 集团恶魔首领: `pandemonium` is a once-per-battle global weather marker `万魔殿` with no written duration. `PandemoniumSpeedTrait` synchronizes a +3 speed status while that weather exists.
  - `excel_r188` / 光道的守护者。洁拉: `sky_sanctuary` is a once-per-battle weather ultimate with no written duration. `vitality_blast` is usable only in `天空的圣域`, cooldown 2, line up to 5 cells, and deals current-attack non-piercing skill damage.
  - `excel_r326` / 蝴蝶刺客。弗伦萨: `vain_giant_shadow` is once per turn, any unit target, piercing/cannot-evade, attack +2 and cannot basic attack for 4 owner-turn-end rounds. `FlorenzaAttackFollowupTrait` adds a piercing basic-attack follow-up: drain up to 1 mana and apply attack/defense/speed -1 until target next own turn end.
  - `excel_r337` / 湿地之主。缇娜: `回复` maps to common Heal, and `wetland_grassland` is a once-per-battle global weather marker `湿地草原` with no written duration.
  - `excel_r352` / 水忍: `WaterNinjaCloneAfterAttackTrait` triggers after each basic attack finishes, regardless of damage, and summons one standard clone in the first legal surrounding cell; if none exists, it logs and skips without interrupting the attack flow.
  - `excel_r379` / 妖仙。菊: `sun_slash` is a once-per-battle shield-piercing direct enemy skill that deals current-attack skill damage and applies a 3-owner-turn passive-skill lock when the follow-up can resolve. `KikuAfterDeathTrait` implements R379-Q001 by granting current allied units `菊之遗击` when Kiku is destroyed; it exposes a separate extra basic-attack action with attack power fixed at 4 while keeping normal basic-attack counts separate.
- Current completion count: 23 fully special-implemented Excel heroes; 347 Excel heroes remain to complete.
- Tests: `tests/test_battle.py` has coverage for all generated registrations, source stat/common mapping samples, generated footprint sizing, and remote-pierce remote line selection.

## Implemented Heroes

### 艾莉 (`ellie`)

- Stats: level 8, 法师, 暗, 人类, 攻2 守2 速1 范1 魔5.
- File: `src/wujiang/heroes/first_five.py`, class `Ellie`.
- Skills:
  - `magic_wall` / `MagicWallSkill`: passive chain speed 2; pay 1 mana per selected threatened ally; each gets 1 temporary shield until chain end.
  - `drain_mana` / `DrainManaSkill`: once per turn; enemy in range loses up to 1 mana and Ellie gains that amount.
  - `mana_pull` / `ManaPullSkill`: once per turn; range target ally/enemy; move target 1-3 cells in chosen direction; enemy target cannot normal move on next action; single-effect composite for reactions.
  - `curse` / `CurseSkill`: once per battle; Ellie pays 0.5 hp; target gets turn-start half-current-hp damage over time.
  - `medusa` / `MedusaSkill`: once per battle; summon Medusa with attack 3, infinite defense, range 1, four attacks per turn; summon cannot act on entry turn.
- `experiment` / `ExperimentSkill`: once per battle; ally gains all stats +2 and +2 mana, then dies after that target's own 3 rounds, not after 3 global turn ends.
  - `crystal_ball` / `CrystalBallSkill`: once per battle; for 4 of Ellie's own rounds, Ellie can attack and target skills globally.
- Traits:
  - `EllieWardTrait`: units that have ended an active skill this turn cannot damage Ellie.

### E。暗人 (`dark_human`)

- Stats: level 5, 刺客, 雷, 人类, 攻3 守4 速4 范1 魔4.
- File: `src/wujiang/heroes/first_five.py`, class `DarkHuman`.
- Skills:
  - `fly_leap` / `DashMoveSkill`: pay 1 mana; once per turn; straight movement exactly 3 cells; can pass through units.
  - `protection` / `PassiveProtectionSkill`: passive chain speed 2; pay 1 mana; only self; gain 2 shields until turn end.
  - `evasion` / `PassiveEvasionSkill`: passive chain speed 2; pay 0.5 mana; twice per turn; straight move exactly 1 cell; disabled in sandstorm because evasion distance is reduced by 1.
  - `stealth` / `StealthSkill`: pay 1.5 mana; self becomes stealth; enemies cannot direct-target, point-cell skills can still hit; stealth can still chain and breaks on attack/skill/reaction skill; cannot be used in sandstorm.
  - `paralyzing_glove` / `ParalyzingGloveSkill`: once per battle; pierces shield; fixed damage 4; target cannot normal move for 3 rounds but movement skills still work; single-effect composite for reactions.
  - `fate_kick` / `FateKickSkill`: cooldown 2 rounds; straight dash up to 4, then coin-flip banish effect along direction; multi-effect, but dash currently usually does not open its own reaction window because it does not affect an opposing unit.
  - `into_darkness` / `IntoDarknessSkill`: cooldown 4 rounds; stealth plus cannot heal for 2 rounds; first basic attack before the buff is spent gets +1 damage and pierces shield. Sandstorm removes only the stealth part.
- Traits:
  - `AttackCountTrait(3)`: up to 3 basic attacks per turn.
  - `FlyingTrait`: movement ignores unit blockers.
  - Stealth use resets `paralyzing_glove` usage.

### 火葬者 (`fire_funeral`)

- Stats: level 5, 勇者, 火, 恶魔, 攻4 守3 速2 范2 魔4.
- File: `src/wujiang/heroes/first_five.py`, class `FireFuneral`.
- Skills:
  - `shensu` / `ShensuSkill`: pay 1 mana; once per turn; this turn's next normal move range +3.
  - `harden` / `HardenSkill`: pay 1 mana; once per turn; defense +1 for 2 rounds.
  - `pierce` / `PierceSkill`: pay 1.5 mana; twice per turn; select a contiguous straight 2-cell line touching the caster; edge-truncated lines allowed.
  - `knockback` / `KnockbackSkill`: passive chain speed 2; pay 1 mana; self gains 1 shield and pushes adjacent units outward 1 cell if possible.
  - `great_funeral` / `GreatFireFuneralSkill`: cooldown 2 rounds; cross-shaped row/column damage 5; self attack -1; creates persistent fire field; fire field area damage does not stack on overlap.
  - `judgment_fire` / `JudgmentFireSkill`: once per battle; only usable when attack is 1; damages all except lowest-stat units for attack 6; ignores magic immunity; cannot evade; applies no-heal for 3 rounds; single-effect composite for reactions.
- Traits:
  - `MagicImmuneWhenAttackOneTrait`: magic immune when current attack <= 1.
  - `BlockCounterTrait`: can use block and counter reaction actions.

### 精兵 (`elite_soldier`)

- Stats: level 4, 弓箭, 土, 人类, 攻3 守2 速2 范14 魔3.
- File: `src/wujiang/heroes/first_five.py`, class `EliteSoldier`.
- Skills:
  - `machine_gun` / `MachineGunSkill`: once per turn; select a contiguous straight 3-cell line touching caster; edge-truncated lines allowed; damages enemies in the selected line.
  - `shensu` / `ShensuSkill`: pay 1 mana; once per turn; this turn's next normal move range +3.
  - `headshot` / `HeadshotSkill`: once per turn; this turn loses square-range basic attack trait; this turn's next basic attack damage +2 and pierces shield; does not affect skills or next turn.
  - `backstep_shot` / `BackstepShotSkill`: passive chain speed 2; pay 0.5 mana; twice per turn; when affected by enemy attack/skill, straight pass-through retreat exactly 2 cells, then may choose whether to counter only the chain source; multi-effect, but retreat currently usually does not open its own hostile reaction window.
- Traits:
  - `PrecisionTrainingTrait`: basic attack range is the surrounding square `(范*2+1)*(范*2+1)` unless headshot disables it; basic attacks have a 1/3 shield-piercing slow proc, next action speed -2 to minimum 1.

### 吟游诗人 (`bard`)

- Stats: level 3, 贤者, 木, 人类, 攻2 守4 速2 范4 魔5.
- File: `src/wujiang/heroes/first_five.py`, class `Bard`.
- Skills:
  - `defend_twice` / `DefendTwiceSkill`: pay 1 mana; once per turn; ally or self in range gains defense +1; same caster's effect does not stack.
  - `heal` / `HealSkill`: pay 1 mana; once per turn; ally or self in range heals 1/4 hp.
  - `protection` / `PassiveProtectionSkill`: passive chain speed 2; pay 1 mana; only self; gain 2 shields until turn end.
  - `baptism` / `BaptismSkill`: pay 2 mana; target human ally; grants magic immunity from skill damage/effects, not field effects.
  - `great_holy_light` / `GreatHolyLightSkill`: once per battle; 2.5-round dynamic field centered on Bard; enemy normal movement ending inside range takes 4 damage; allied unit in range gains defense +1 until next own turn start.
  - `chant` / `ChantSkill`: no mana; once per turn; point a target in range; target gains 2 mana points, not mana.
- Traits:
  - `StationaryRecoveryTrait`: if Bard did not move this turn, turn-end mana +1 and hp +1/4.

### 元素猎人 (`element_hunter`)

- Stats: level 7, 法师, 木, 精灵, 攻3 守3 速2 范2 魔5.
- File: `src/wujiang/heroes/next_five.py`, class `ElementHunter`.
- Skills:
  - `light_wall` / `LightWallSkill`: passive chain speed 2; pay 1 mana per threatened ally; each gains 1 temporary shield until chain end.
  - `shensu` / `ShensuSkill`: pay 1 mana; once per turn; this turn's next normal move range +3.
  - `complete_burn` / `CompleteBurnSkill`: once per turn; remote `4*4` area; default current-attack damage; applies Complete Burn, target loses 1 mana at each own turn start for 5 triggers; non-damage effect pierces shield and does not stack with same-name effect; single-effect composite for reactions.
  - `blizzard` / `BlizzardSkill`: once per turn; remote `3*3` area; default current-attack damage; applies no normal movement for 3 rounds; non-damage effect pierces shield and does not stack with same-name effect; single-effect composite for reactions.
  - `thunder_god` / `ThunderGodSkill`: once per battle; summon Thunder God in range; summon is 攻4 守5 速4 范3 魔0, 1x1, no skills/traits, 5-round duration; resets if destroyed by enemy basic attack or skill damage.
  - `water_wave` / `WaterWaveSkill`: cooldown 4 rounds; self only; all stats and max mana +1 for 2 rounds; does not refill current mana.
  - `earth_walker` / `EarthWalkerSkill`: no mana; once per turn; create a clone in range; caster cannot continue acting; clone can act this turn but cannot attack or use skills; caster randomly swaps with a newly created clone; clones expire before ElementHunter's next own turn.
  - `plant_growth` / `PlantGrowthSkill`: no mana; once per turn; remote `5*5` area; until ElementHunter's next own turn starts, normal movement step costs 2 if the step starts in the area; flying is affected; skill movement is not.
- Traits:
  - `ElementalEffectTrait`: all non-damage skill effects pierce shields and same-name effects do not stack.

### 不死王利娜 (`undead_king_lina`)

- Stats: level 8, 刺客, 土, 灵体, 攻4 守4 速4 范3 魔5; occupies `2*2` and should render as one footprint-spanning board piece.
- File: `src/wujiang/heroes/next_five.py`, class `UndeadKingLina`.
- Skills:
  - `stealth` / `StealthSkill`: pay 1.5 mana; self stealth; cannot be used in sandstorm.
  - `harden` / `HardenSkill`: pay 1 mana; once per turn; defense +1 for 2 rounds.
  - `rending` / `RendingSkill`: once per battle; point one range cell; default current-attack damage; pierces shield.
  - `wind_sand` / `WindSandSkill`: once per turn; remote `2*4` or `4*2` area; default current-attack damage; if area contains any unit, weather becomes Sandstorm for one round; multi-effect, but weather change currently usually does not open its own hostile reaction window.
  - `knockback` / `KnockbackSkill`: passive chain speed 2; pay 1 mana; self gains 1 shield and pushes adjacent units outward 1 cell if possible.
  - `crazy_sand` / `CrazySandSkill`: cooldown 2 rounds; choose valid direction; damage straight 5 cells and teleport caster to 6th anchor cell; invalid if 6th anchor is out of bounds or occupied.
- Traits:
  - `AttackCountTrait(2)`: up to 2 basic attacks per turn.
  - `HalfPierceAttackTrait`: Lina's basic attacks half-pierce shields.
  - `AttackLockTrait`: basic-attack declaration locks target until that target is destroyed.
  - `LinaDestroyRewardTrait`: once per turn; if Lina's own basic attack/skill destroys a hero or a unit with current defense >= 4, reset move/attacks and gain target's remaining current mana.
  - `NoEnemyHealAuraTrait`: enemies within Lina's surrounding `7*7` cannot heal.
  - `LinaSandstormRecoveryTrait`: in Sandstorm and not stealthed, Lina gets natural recovery at own turn start.

### 岩神 (`rock_god`)

- Stats: level 4, 狂战, 土, 石人, 攻3 守5 速2 范1 魔3; base `2*2`, dynamic irregular footprint allowed.
- File: `src/wujiang/heroes/next_five.py`, class `RockGod`.
- Skills:
  - `harden` / `HardenSkill`: pay 1 mana; once per turn; defense +1 for 2 rounds.
  - `knockback` / `KnockbackSkill`: passive chain speed 2; pay 1 mana; self gains 1 shield and pushes adjacent units outward 1 cell if possible.
  - `dragon_breath` / `DragonBreathSkill`: pay 2 mana; twice per turn; nearby `2*2` area touching the caster orthogonally or diagonally; edge truncation allowed; default current-attack damage.
  - `rock_absorb` / `RockAbsorbSkill`: once per turn; choose attack/defense/speed/range/mana; all units in RockGod's local Sandstorm except RockGod get chosen stat -1 for one round unless shielded; RockGod gains chosen stat by successfully affected unit count and grows by that many legal selected cells where possible. Mana modifies both max and current mana. Shield reactions can block Rock Absorb per target. When the footprint status ends, base `2*2` cells occupied by other units or out of bounds are skipped instead of moving RockGod.
  - `rock_cannon` / `RockCannonSkill`: no mana and no use-count limit; choose one or more body cells plus direction; after firing at least 1 body cell must remain and remaining body must stay orthogonally connected; invalid if remaining body blocks a fired cell's ray; each fired cell independently impacts a unit or boundary and deals a separate chainable surrounding `3*3` effect for `3 + fired_cell_count` damage, not pierce, to both sides. Frontend must visibly highlight selectable and selected body cells.
- Traits:
  - `NaturalManaRecoveryTrait`: own turn start mana +1 to cap.
  - `RockGodSandstormTrait`: maintains local same-name Sandstorm over the union of each occupied cell's surrounding `9*9`; multiple RockGod auras combine for coverage and do not stack damage with each other or global Sandstorm. Use `battle.unit_in_weather("沙尘", unit)` for local-aware checks.

### 神龙。末日光 (`doomlight_dragon`)

- Stats: level 4, 法师, 光, 古龙, 攻3 守4 速3 范3 魔5; occupies `2*2`, has flying, and can heal above max hp.
- File: `src/wujiang/heroes/next_five.py`, class `DoomlightDragon`.
- Skills:
  - `stone_wall` / `StoneWallSkill`: passive chain speed 2; same rules as Light Wall; pay 1 mana per selected threatened ally; each gains 1 temporary shield until chain end.
  - `shensu` / `ShensuSkill`: pay 1 mana; once per turn; this turn's next normal move range +3.
  - `harden` / `HardenSkill`: pay 1 mana; once per turn; defense +1 for 2 rounds.
  - `remote_dragon_breath` / `RemoteDragonBreathSkill`: pay 2 mana; twice per turn; remote edge-truncated `2*2` area by range; default current-attack damage.
  - `doom_light` / `DoomLightSkill`: once per battle; remote `7*7` area; applies a 4-round non-stacking Doom Light effect that prevents healing and deals half-current-hp damage at each affected unit's own turn start; the effect pierces shields.
  - `apocalypse` / `ApocalypseSkill`: once per turn; player chooses `n`, where `n` is a positive integer strictly below current hp, pays `n` hp, then hits a remote edge-truncated `n*n` area for current attack `+ n` with shield pierce. Example: current hp `1.25` still allows `n = 1`. The chosen `n` must remain explicit in payload/UI because edge-truncated patterns alone are ambiguous.
- Traits:
  - `FlyingTrait`: movement ignores unit blockers.
  - `DoomLightRetaliationTrait`: units that attack Doomlight Dragon or damage it receive Doom Light; units damaged by Doomlight Dragon also receive Doom Light, but Doom Light tick damage must not recursively reapply Doom Light.
  - `OverhealTrait`: healing can raise current hp above max hp.

### 天位骑士。政宗 (`masamune`)

- Stats: level 4, 骑士, 土, 人类, 攻4 守3 速3 范1 魔3.
- File: `src/wujiang/heroes/next_five.py`, class `Masamune`.
- Skills:
  - `motor_horse` / `MotorHorseSkill`: mount summon skill; own mount only; default 0 mana; cannot summon if own mount is already on field or still in one-own-turn remount lockout. Knight start rule means Masamune enters battle already mounted on a summoned Motor Horse.
  - `protection` / `PassiveProtectionSkill`: standard self-only chain shield.
  - `six_blade_style` / `SixBladeStyleSkill`: once per turn; only while not mounted and before any attack this turn; this turn attack -1 and attack cap becomes 6.
  - `heal_mount` / `HealMountSkill`: once per turn; only while mounted; heal the currently ridden Motor Horse by 1/2 hp.
- Traits:
  - `ArcAttackTrait`: basic attack first chooses one of 8 forward directions; orthogonal directions attack the outer 3-cell row, diagonal directions attack the corresponding 3-cell corner arc.
  - `MountedFreeLeapTrait`: while mounted, once per turn may use Leap for free.
  - `TripleStrikeAttackTrait`: may choose one basic attack to consume 3 attack counts; that attack gets +3 damage and half pierce.
  - `UnmountedCombatTrait`: while not mounted, gains block, counter, and attack lifesteal (+1/4 hp after dealing basic-attack damage).
- Implementation touchpoints:
  - `src/wujiang/engine/core.py`: generalized basic attacks to support payload variants, mount target redirection, and rider proxy reactions.
  - `static/app.js`: attack actions are no longer hard-coded to `attack`; Masamune uses attack `choice_pattern` to declare direction first, then click the highlighted target.

### 摩托马 (`motor_horse`)

- Summon / class `MotorHorseSummon`: default mount for Masamune; occupies vertical `1*2`; stats 攻0 守5 速5 范1 魔0.
- Skills:
  - `free_shensu` / `FreeShensuSkill`: once per own turn, free use of Shensu.
- Traits:
  - `可乘骑`

### 翡翠 (`jade`)

- Stats: level 8, 勇者, 钢, 机甲, 攻4 守4 速3 范3 魔0.
- File: `src/wujiang/heroes/next_five.py`, class `Jade`.
- Skills:
  - `machine_gun` / `JadeMachineGunSkill`: standard once-per-turn Machine Gun line damage; if an enemy chains and at least one original enemy target ultimately takes no damage, Jade can permanently gain +1 future use for this skill from next own turn, once per turn.
  - `missile` / `MissileSkill`: remote edge-truncated `2*2` area damage; first use opens a 2-round window counted on Jade's own turn starts, with 3 total uses; leftover uses expire when the window ends.
  - `ion_shield` / `IonShieldSkill`: passive chain speed 2; free wall; up to 2 casts in each opposing hero turn, and one cast may shield multiple currently threatened allies until chain end.
  - `laser` / `LaserSkill`: cooldown 3 rounds; remote edge-truncated `2*10` or `10*2` area damage.
  - `quantum_shield` / `QuantumShieldSkill`: passive chain speed 2; free wall; in a usable round, up to 3 casts in each opposing hero turn, and one cast may shield multiple currently threatened allies until chain end. If used anywhere in that round, the next full round is unavailable and the following round becomes usable again.
  - `mech_enhancement` / `MechEnhancementSkill`: cooldown 3 rounds; self defense +1 for 2 rounds and heal 1/2 hp.
  - `plasma_thruster` / `PlasmaThrusterSkill`: once per turn; straight flying move up to the 5th cell, or to the boundary-truncated last cell if the direction hits the edge first; final cell must be empty.
  - `stance` / `StanceSkill`: cooldown 2 rounds; creates a visible dynamic local field that arms after Jade's current turn ends and prevents damage to other allied units currently inside Jade's surrounding `7*7` until Jade's next own turn starts.
- Traits:
  - `FlyingTrait`: movement ignores unit blockers.
  - `JadeReactiveOverclockTrait`: after an enemy chain, if a damaging skill leaves any original enemy target without damage, that skill gains +1 permanent future use from Jade's next own turn; once per turn per skill.

### N (`n`)

- Stats: level 4, 勇者, 光, 人类, 攻2 守3 速3 范1 魔2.
- File: `src/wujiang/heroes/next_five.py`, class `N`.
- Skills:
  - `protection` / `PassiveProtectionSkill`: standard self-only chain shield.
  - `pierce` / `PierceSkill`: standard 2-cell touching line pierce skill.
  - `split` / `SplitSkill`: pay 1.5 mana; once per turn; choose 3 legal cells in range, summon 3 standard clones, randomly swap with one of them, and end the caster's remaining actions for the turn. The clones cannot act on entry turn and, as clones, cannot attack or use skills. Enemy-facing visible info for those clones must mirror the real hero so mana or text does not reveal the real body. Preview exposes legal single cells with `required_cells=3` instead of enumerating all 3-cell combinations.
  - `drain_mana` / `DrainManaSkill`: standard range mana drain.
  - `magnetic_wave` / `MagneticWaveSkill`: instant skill / chain speed 3; pay 2 mana points; once per turn; remote edge-truncated `3*3` area for current-attack damage; any currently acting hit unit loses the rest of that turn.
  - `n_skill` / `NSkill`: active self skill; pay 1 mana point; gain 1 mana.
- Traits:
  - `NAttackManaPointTrait`: gain 1 mana point on every basic-attack declaration, even if the attack is blocked or misses later.
  - `NAttackCountTrait`: at own turn start, snapshot attack count as `floor(current_mana) + 1` for that turn only.
  - `UnlimitedManaTrait`: current mana and displayed mana cap are unbounded for this unit.
  - `NManaGuardTrait`: while current mana > 0, each incoming damage instance is cancelled, costs 1 mana, and still allows non-damage follow-up effects from that same skill/effect to apply.

### 噬血 (`blood_eater`)

- Stats: level 4, 贤者, 火, 兽人, 攻3 守2 速3 范3 魔5. Excel `炎` maps to the existing `火` attribute.
- File: `src/wujiang/heroes/next_five.py`, class `BloodEater`.
- Skills:
  - `recover_mana` / `RecoverManaSkill`: once per turn; self current mana +1.
  - `drain_mana` / `DrainManaSkill`: standard range mana drain.
  - `magic_shield` / `MagicShieldSkill`: passive chain speed 2; pay 1 mana when affected by an enemy skill; self gains 1-round magic immunity.
  - `blood_guard` / `BloodGuardSkill`: pay 1 mana; self or allied hero only; defense +1 for 2 owner rounds; same caster's copy cannot stack.
  - `blood_art` / `BloodArtSkill`: pay 1 mana; self or allied hero only; must hit; attack +1 for 2 owner rounds; same caster's copy cannot stack.
  - `blood_dance` / `BloodDanceSkill`: once per turn; allied unit target; target and BloodEater both heal 1/4, gain 1 mana, and cannot move or use movement skills until that BloodEater's next own turn starts.
  - `sacrifice_ritual` / `SacrificeRitualSkill`: pay 4 magic points; choose any destroyed unit and a legal adjacent cell around BloodEater; revive it there with max/current hp 1/4 and full mana.
- Traits:
  - `HalfPierceAttackTrait`: basic attacks half-pierce shields.
  - `BloodManaPointTrait`: starts with magic points equal to base mana; gains 1 magic point, capped at 8, whenever any unit loses at least 1/4 hp from another unit's damage.
  - `BloodSkillDamageGuardTrait`: at 8 magic points, prevents skill damage only; non-damage follow-up effects and healing still apply.

### 李 (`li`)

- Stats: level 9, 勇者, 土, 人类, 攻3 守5 速3 范1 魔5.
- File: `src/wujiang/heroes/next_five.py`, class `Li`.
- Skills:
  - `leap` / `DashMoveSkill`: pay 1 mana; once per turn; straight movement up to 3 cells; can pass through units.
  - `chain_pull` / `ChainPullSkill`: common Lock Chain behavior; pay 0.5 mana; once per turn; choose a straight front 5-cell line; first hit unit is pulled straight to a legal cell around Li.
  - `harden` / `HardenSkill`: standard defense +1 for 2 rounds.
  - `protection` / `PassiveProtectionSkill`: standard self-only chain shield.
  - `whirlwind_attack` / `WhirlwindAttackSkill`: once per battle; immediately resolves one basic attack against every unit around Li, including allies.
  - `red_heat` / `RedHeatSkill`: start-phase toggle; open grants attack +2 and speed +3; Li loses half current hp at Li's own turn end; use again in a later start phase to close.
  - `essence` / `EssenceSkill`: cooldown 2 rounds; this turn gains exactly 2 extra shield-piercing basic attacks; only unused extra charges become current mana at turn end.
  - `foresight` / `ForesightSkill`: passive once per opposing hero turn; blocks one enemy basic attack against Li, then Li's next own turn has attack count +1 and speed +1.
  - `stillness` / `StillnessSkill`: once-per-battle ultimate; heal 1/2, end Red Heat, then for 4 owner rounds Li cannot act, gains defense +2, magic immunity, and natural healing at own turn start.
- Traits:
  - `AttackCountTrait(3)`: up to 3 basic attacks per turn.
  - `AttackLifeStealTrait`: basic attacks that deal damage heal Li by 1/4.
  - `SplitMovementTrait`: Li can split normal movement any number of times, capped by current speed in total.
  - `AntiSpeedReductionTrait`: speed reductions from common slow/stat-reduction statuses do not lower Li's speed.

### 咏唱者 (`chanter`)

- Stats: level 3, 法师, 暗, 精灵, 攻1 守2 速3 范4 魔5.
- File: `src/wujiang/heroes/next_five.py`, class `Chanter`.
- Skills:
  - `paralysis_card` / `ChanterCardSkill`: pay 1 mana; place a permanent non-occupying card marker in range. The center cell and surrounding `3*3` block enemy skill use while enemies are inside; piercing.
  - `poison_card` / `ChanterCardSkill`: pay 1 mana; place a permanent non-occupying card marker in range. Affected enemies take attack-power-2 piercing field damage at their own turn end.
  - `drain_card` / `ChanterCardSkill`: pay 1 mana; place a permanent non-occupying card marker in range. Affected enemies are drained for up to 1 mana by Chanter at their own turn end; piercing.
  - `light_wall` / `LightWallSkill`: exactly common Light Wall.
  - `card_transposition` / `CardTranspositionSkill`: passive, pay 0.5 mana, twice per opposing hero turn; if Chanter is affected, swap with one own card. Original action still resolves on original declared cells.
  - `magic_claw` / `MagicClawSkill`: pay 1.5 mana, once per turn; choose one own card and affect only its surrounding cells, excluding the card cell. Enemy units there cannot move or use movement skills until Chanter's next own turn starts; card is not consumed.
  - `form_shift` / `FormShiftSkill`: once per battle; permanent attack 4 / defense 5 / speed 4 / range 1; disables only future `paralysis_card`, `poison_card`, and `drain_card` usage. Existing cards remain active.
- Traits:
  - `NaturalManaRecoveryTrait`: own turn start current mana +1 to cap.
  - `ChanterCardCleanupTrait`: Chanter leaving removes own cards and own Magic Claw locks; Chanter's own turn start clears locks from Magic Claw.

### 抹杀的使徒 (`erasure_apostle`)

- Stats: level 5, 刺客, 暗, 人类, 攻4 守1 速4 范1 魔4.
- File: `src/wujiang/heroes/next_five.py`, class `ErasureApostle`.
- Status:
  - `ErasureCounterStatus`: stackable `抹杀计数点` with a `source_unit_id`; counters persist until removed or the owning unit leaves.
- Skills:
  - `stealth` / `StealthSkill`: common stealth.
  - `split` / `SplitSkill`: common split.
  - `extra_stealth` / `ExtraStealthSkill`: ultimate, once per battle, no mana; big-move stealth.
  - `drain_mana` / `DrainManaSkill`: common drain mana.
  - `premature_burial` / `PrematureBurialSkill`: range 5, once per turn, any unit target; must hit, pierces shields, places one own Erasure Counter; counters stack.
  - `erasure` / `ErasureSkill`: ultimate, once per battle; removes all counters placed by this ErasureApostle only, then each removed counter deals direct `1/4` hp loss with shield pierce, not attack/defense formula damage.
  - `descent_moment` / `DescentMomentSkill`: pay 1 mana, once per turn; target an enemy with any Erasure Counter, choose any legal adjacent landing cell, teleport there, and gain 2 extra basic attacks this turn locked to that target only. Original normal basic attacks can still target others.
  - `shadow_counter` / `ShadowCounterSkill`: passive chain speed 2, pay 0.5 mana, no use-count limit; when self is affected by enemy attack/skill, move exactly 2 normal steps without passing through units and may turn, then place non-piercing own counters on enemies in the original-position surrounding `5*5` excluding the origin. Shields and magic immunity can block this counter placement.
- Traits:
  - `ErasureApostleDestroyRewardTrait`: when this ErasureApostle's own attack/skill destroys a unit, gain that target's remaining current mana; if the destroyed unit is a hero, reset Erasure.

### 龙骑 (`dragon_rider`)

- Stats: level 5, 骑士, 火, 兽人, 攻4 守4 速3 范1 魔5. Excel `炎` maps to existing `火`.
- File: `src/wujiang/heroes/next_five.py`, class `DragonRider`.
- Summon:
  - `dragon_mount` / `DragonMountSummon`: attack 3 / defense 5 / speed 5 / range 4 / mana 0, flying, rideable, `2*2`. DragonRider starts mounted on it; only one own Dragon may exist; if destroyed, DragonRider must wait through one own turn before resummoning.
- Skills:
  - `dragon_breath` / `DragonBreathSkill`: common nearby `2*2` current-attack damage.
  - `summon_dragon` / `DragonSummonSkill`: resummons and mounts own Dragon if no own Dragon is alive and no remount cooldown remains.
  - `protection` / `PassiveProtectionSkill`: standard self-only chain shield.
  - `dragon_slash` / `DragonSlashSkill`: once per turn; front straight 5-cell area; damage value 5 is not piercing; first hit unit receives a piercing chain-pull follow-up and speed -2 until DragonRider's next own turn starts.
  - `chain_pull` / `ChainPullSkill`: common chain pull; pay 0.5 mana; once per turn.
  - `smoke_spray` / `DragonSmokeSkill`: pay 1 mana; once per turn; remote `3*6` / `6*3`; no damage; affects both sides; units inside or later entering cannot attack or use active skills until DragonRider's next own turn starts.
- Traits:
  - `DragonMountedStartTrait`: opening Dragon summon and mounted state.
  - `DragonRiderTurnTrait`: clears own Dragon follow-up debuffs and smoke on own turn start; gains current mana for each fielded allied non-summon non-clone Mage hero.
- Dragon summon traits:
  - `DragonAreaAttackTrait`: basic attack selects a remote `3*3` area. Damage is not piercing; the hit speed -1 / defense -1 follow-up is piercing and lasts until DragonRider's next own turn starts.
  - `DragonDamageResistanceTrait`: formula skill damage against Dragon has attack power -1 to minimum 1 and area-cell bonus capped to one hit cell; raw/fixed hp loss is not reduced.

### 销魂的死灵 (`soul_wraith`)

- Stats: level 1, 剑士, 暗, 灵体, 攻4 守0.5 速5 范1 魔2. Defense minimum is 0.5 for this hero.
- File: `src/wujiang/heroes/next_five.py`, class `SoulWraith`.
- Skills:
  - `pierce` / `PierceSkill`: common Pierce, pay 1.5 mana, twice per turn.
- Traits:
  - `BasicAttackImmunityTrait`: immune to enemy basic-attack damage and basic-attack follow-up effects; does not block skills.
  - `PassThroughMovementTrait`: normal movement may pass through units but final placement must remain legal.
  - `FlyingTrait`: movement ignores unit blockers.
  - `ArcAttackTrait`: declared 8-direction arc basic attack; resolves against all enemy units in the three declared arc cells.
  - `AttackManaDrainTrait`: each enemy that actually takes SoulWraith basic-attack damage loses up to 1 current mana; SoulWraith gains the same amount.
  - `NearbyEnemyHeroMagicImmunityTrait`: if no enemy non-summon non-clone hero body is in SoulWraith's surrounding 8 cells, enemy skill damage and skill follow-up effects are blocked. Summons/clones do not disable this.
  - `SoulWraithFailedAttackGrowthTrait`: if a SoulWraith basic attack deals no damage because an enemy side reaction skill prevented it, add one stacking `SoulWraithGrowthStatus` layer: attack +1, speed +1, normal-move actions per turn +1. Layers persist across turns and all clear when a SoulWraith basic attack deals damage. Pierce neither triggers nor clears it.

## Summons And Clones

- `medusa` summon / class `Medusa`: 攻3, 守 infinite, 范1, four attacks per turn; skill is teleport; cannot act on entry turn.
- `thunder_god_summon` / class `ThunderGodSummon`: 攻4 守5 速4 范3 魔0; no skills/traits; 5-round duration.
- `element_hunter_clone` / class `ElementHunterClone`: copies ElementHunter's current stats, hp, mana, and mana points when created by EarthWalker; clone cannot attack or use skills; any clone is destroyed immediately by damage without damage calculation.

## Reusable Skill Index

- `magic_wall` / `MagicWallSkill`: passive multi-target ally shield; 1 mana per target; temporary shield until chain end.
- `light_wall` / `LightWallSkill`: passive multi-target ally shield; same cost and duration rules as Magic Wall.
- `stone_wall` / `StoneWallSkill`: passive multi-target ally shield; same rules as Light Wall.
- `protection` / `PassiveProtectionSkill`: passive self-only shield; 1 mana; 2 shields until turn end.
- `evasion` / `PassiveEvasionSkill`: passive exact 1-cell straight move; 0.5 mana; twice per turn; no valid move in Sandstorm.
- `backstep_shot` / `BackstepShotSkill`: passive exact 2-cell straight pass-through retreat; 0.5 mana; twice per turn; optional counter only against chain source; multi-effect, but retreat currently usually does not open its own hostile reaction window.
- `knockback` / `KnockbackSkill`: passive shield plus outward push.
- `shensu` / `ShensuSkill`: next normal movement this turn +3 cells.
- `harden` / `HardenSkill`: defense +1 for 2 rounds.
- `stealth` / `StealthSkill`: self stealth; direct targeting blocked for enemies; point-cell overlap can hit; can chain while stealthed; breaks on attack/skill/reaction skill; blocked and suppressed in Sandstorm.
- `pierce` / `PierceSkill`: contiguous straight line 2 cells touching caster; full area selection required unless board edge truncates it.
- `machine_gun` / `MachineGunSkill`: contiguous straight line 3 cells touching caster; enemy-only damage.
- `split` / `SplitSkill`: standard 1.5-mana once-per-turn clone summon, swap, and self turn-end action cutoff.
- `drain_mana` / `DrainManaSkill`: range target loses up to 1 mana; caster gains the drained amount.
- `magnetic_wave` / `MagneticWaveSkill`: instant remote edge-truncated `3*3` current-attack damage paid with 2 mana points; any currently acting hit unit loses the rest of that turn.
- `n_skill` / `NSkill`: self skill; pay 1 mana point; gain 1 mana.
- `chain_pull` / `ChainPullSkill`: Lock Chain-style skill; pay 0.5 mana; once per turn; choose a straight front 5-cell line; first hit unit is pulled straight to a legal adjacent cell around the caster.
- `extra_stealth`: ErasureApostle ultimate version of stealth; once per battle, no mana.
- `premature_burial`: range-5 must-hit unit target that pierces shields and places an own stackable Erasure Counter.
- `erasure`: ErasureApostle ultimate; removes own Erasure Counters and converts them into direct shield-piercing `1/4` hp loss each.
- `descent_moment`: two-stage target-then-destination skill; frontend/AI payload must include `target_unit_id`, `dest_x`, and `dest_y`.
- `shadow_counter`: speed-2 passive exact-2 normal-step retreat plus non-piercing Erasure Counter placement around the original position.
- `light_wall` / `LightWallSkill` remains the default implementation for any future hero that writes only `光墙`.
- `defend_twice` / `DefendTwiceSkill`: ally/self defense +1; same caster does not stack.
- `heal` / `HealSkill`: ally/self heals 1/4 hp.
- `baptism` / `BaptismSkill`: human ally gains magic immunity; field effects still apply.
- `chant` / `ChantSkill`: target in range gains mana points, not mana.
- `complete_burn`, `blizzard`, `wind_sand`, and `plant_growth`: remote area skills; area selection can truncate at board edges and must include at least one caster-range cell. Complete Burn and Blizzard are single-effect composites for reactions; Wind Sand is multi-effect but its weather stage currently usually has no extra hostile reaction window.
- `rending`: one-cell range damage with shield pierce.
- `crazy_sand`: line damage plus teleport, with destination validity baked into selectable patterns.
- `dragon_breath`: nearby edge-truncated `2*2` range damage, must touch caster orthogonally or diagonally.
- `summon_dragon`: DragonRider mount summon / remount skill; one own Dragon only and one-own-turn remount lockout.
- `dragon_slash`: DragonRider once-per-turn front straight 5-cell damage value 5 plus piercing chain-pull follow-up on the first hit unit.
- `smoke_spray`: DragonRider remote `3*6` / `6*3` no-damage field that blocks attacks and active skills for units in the area until DragonRider's next own turn starts.
- `arc_attack` / `ArcAttackTrait`: basic attack declares one of 8 directions and resolves against all enemy units in the three declared arc cells; multi-cell targets count every occupied hit cell for area-hit attack bonus.
- `物免` / `BasicAttackImmunityTrait`: blocks enemy basic-attack damage and basic-attack follow-up effects, not skills.
- `攻击吸魔` / `AttackManaDrainTrait`: basic attacks that actually deal damage drain up to 1 current mana from each damaged enemy and give the same amount to the attacker.
- `remote_dragon_breath`: remote edge-truncated `2*2` range damage selected by range.
- `rock_absorb`: custom `stat_cells` frontend selection; chosen stat plus selected growth cells; shield reactions can block the effect for their protected target.
- `rock_cannon`: custom `body_direction` frontend selection; selected body cells plus direction, with visible selectable/selected body-cell highlight.
- `missile`: remote edge-truncated `2*2` range damage with a first-use-started 2-own-round / 3-use window; leftover uses expire with the window.
- `ion_shield`: free passive multi-target wall; up to 2 casts in each opposing hero turn, and one cast may shield multiple threatened allies until chain end.
- `laser`: remote edge-truncated `2*10` or `10*2` area damage selected by range.
- `quantum_shield`: free passive multi-target wall; up to 3 casts in a usable round across opposing hero turns, and one cast may shield multiple threatened allies until chain end. If used anywhere in that round, the next full round is unavailable and the following round becomes usable again.
- `plasma_thruster`: straight flying displacement to the fixed 5th cell or to the boundary-truncated last cell in that direction.
- `stance`: dynamic visible local anti-damage field that arms on the caster's turn end and lasts through the next enemy turn only.
