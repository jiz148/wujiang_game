# Hero Rule Index

Use this as a compact implementation index, not the full rule source. For exact wording, read `docs/武将说明.md` and `docs/通用技能和特性说明.md`.

Update this file whenever a hero, skill, trait, or durable gameplay rule changes.

## Durable Default Rules

- If an active skill has no cost text, it costs 0 mana; if it has no written per-turn/per-battle/cooldown limit, it has no use-count limit.
- If a damaging skill/area has no fixed `伤 n` and no `没有伤害`, it uses the caster's current attack.
- If no `破魔` text is written, the damage/effect does not pierce shields.
- If no ally/enemy qualifier is written, the effect applies to both sides.
- `单位` includes heroes, summons, and clones.
- Reductions to attack, defense, speed, and range floor at 1 unless explicitly stated otherwise.
- Modifying the `mana` stat changes both max mana and current mana, then clamps current mana to the new cap.

## Implemented Heroes

### 艾莉 (`ellie`)

- Stats: level 8, 法师, 暗, 人类, 攻2 守2 速1 范1 魔5.
- File: `src/wujiang/heroes/first_five.py`, class `Ellie`.
- Skills:
  - `magic_wall` / `MagicWallSkill`: passive chain speed 2; pay 1 mana per selected threatened ally; each gets 1 temporary shield until chain end.
  - `drain_mana` / `DrainManaSkill`: once per turn; enemy in range loses up to 1 mana and Ellie gains that amount.
  - `mana_pull` / `ManaPullSkill`: once per turn; range target ally/enemy; move target 1-3 cells in chosen direction; enemy target cannot normal move on next action.
  - `curse` / `CurseSkill`: once per battle; Ellie pays 0.5 hp; target gets turn-start half-current-hp damage over time.
  - `medusa` / `MedusaSkill`: once per battle; summon Medusa with attack 3, infinite defense, range 1, four attacks per turn; summon cannot act on entry turn.
  - `experiment` / `ExperimentSkill`: once per battle; ally gains all stats +2 and +2 mana, then dies after delay.
  - `crystal_ball` / `CrystalBallSkill`: once per battle; for 4 rounds, Ellie can attack and target skills globally.
- Traits:
  - `EllieWardTrait`: units that have ended an active skill this turn cannot damage Ellie.

### E。暗人 (`dark_human`)

- Stats: level 5, 刺客, 雷, 人类, 攻3 守4 速4 范1 魔4.
- File: `src/wujiang/heroes/first_five.py`, class `DarkHuman`.
- Skills:
  - `fly_leap` / `DashMoveSkill`: pay 1 mana; once per turn; straight movement exactly 3 cells; can pass through units.
  - `protection` / `PassiveProtectionSkill`: passive chain speed 2; pay 1 mana; only self; gain 2 shields until turn end.
  - `evasion` / `PassiveEvasionSkill`: passive chain speed 2; pay 0.5 mana; twice per turn; straight move exactly 1 cell; disabled in sandstorm because evasion distance is reduced by 1.
  - `stealth` / `StealthSkill`: pay 1.5 mana; self becomes stealth; enemies cannot direct-target, point-cell skills can still hit; breaks on first basic attack or skill; cannot be used in sandstorm.
  - `paralyzing_glove` / `ParalyzingGloveSkill`: once per battle; pierces shield; fixed damage 4; target cannot move for 3 rounds.
  - `fate_kick` / `FateKickSkill`: cooldown 2 rounds; straight dash up to 4, then coin-flip banish effect along direction.
  - `into_darkness` / `IntoDarknessSkill`: cooldown 4 rounds; stealth plus cannot heal for 2 rounds; when breaking stealth with basic attack, next attack damage +1 and pierces shield.
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
  - `judgment_fire` / `JudgmentFireSkill`: once per battle; only usable when attack is 1; damages all except lowest-stat units for attack 6; ignores magic immunity; cannot evade; applies no-heal for 3 rounds.
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
  - `backstep_shot` / `BackstepShotSkill`: passive chain speed 2; pay 0.5 mana; twice per turn; when affected by enemy attack/skill, straight pass-through retreat exactly 2 cells, then may choose whether to counter only the chain source.
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
  - `complete_burn` / `CompleteBurnSkill`: once per turn; remote `4*4` area; default current-attack damage; applies Complete Burn, target loses 1 mana at each own turn start for 5 triggers; non-damage effect pierces shield and does not stack with same-name effect.
  - `blizzard` / `BlizzardSkill`: once per turn; remote `3*3` area; default current-attack damage; applies no normal movement for 3 rounds; non-damage effect pierces shield and does not stack with same-name effect.
  - `thunder_god` / `ThunderGodSkill`: once per battle; summon Thunder God in range; summon is 攻4 守5 速4 范3 魔0, 1x1, no skills/traits, 5-round duration; resets if destroyed by enemy basic attack or skill damage.
  - `water_wave` / `WaterWaveSkill`: cooldown 4 rounds; self only; all stats and max mana +1 for 2 rounds; does not refill current mana.
  - `earth_walker` / `EarthWalkerSkill`: no mana; once per turn; create a clone in range; caster cannot continue acting; clone can act this turn but cannot attack or use skills; caster randomly swaps with a newly created clone; clones expire before ElementHunter's next own turn.
  - `plant_growth` / `PlantGrowthSkill`: no mana; once per turn; remote `5*5` area; until ElementHunter's next own turn, normal movement step costs 2 if the step starts in the area; flying is affected; skill movement is not.
- Traits:
  - `ElementalEffectTrait`: all non-damage skill effects pierce shields and same-name effects do not stack.

### 不死王利娜 (`undead_king_lina`)

- Stats: level 8, 刺客, 土, 灵体, 攻4 守4 速4 范3 魔5; occupies `2*2` and should render as one footprint-spanning board piece.
- File: `src/wujiang/heroes/next_five.py`, class `UndeadKingLina`.
- Skills:
  - `stealth` / `StealthSkill`: pay 1.5 mana; self stealth; cannot be used in sandstorm.
  - `harden` / `HardenSkill`: pay 1 mana; once per turn; defense +1 for 2 rounds.
  - `rending` / `RendingSkill`: once per battle; point one range cell; default current-attack damage; pierces shield.
  - `wind_sand` / `WindSandSkill`: once per turn; remote `2*4` or `4*2` area; default current-attack damage; if area contains any unit, weather becomes Sandstorm for one round.
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
  - `rock_absorb` / `RockAbsorbSkill`: once per turn; shield-piercing effect; choose attack/defense/speed/range/mana; all units in RockGod's local Sandstorm except RockGod get chosen stat -1 for one round; RockGod gains chosen stat by affected unit count and grows by that many legal selected cells where possible. Mana modifies both max and current mana.
  - `rock_cannon` / `RockCannonSkill`: no mana and no use-count limit; choose one or more body cells plus direction; after firing at least 1 body cell must remain and remaining body must stay orthogonally connected; invalid if remaining body blocks a fired cell's ray; each fired cell independently impacts a unit or boundary and deals separate surrounding `3*3` damage for `3 + fired_cell_count`, not pierce, to both sides.
- Traits:
  - `NaturalManaRecoveryTrait`: own turn start mana +1/4 to cap.
  - `RockGodSandstormTrait`: maintains a local Sandstorm aura over the union of each occupied cell's surrounding `9*9`; use `battle.unit_in_weather("沙尘", unit)` for local-aware checks.

## Summons And Clones

- `medusa` summon / class `Medusa`: 攻3, 守 infinite, 范1, four attacks per turn; skill is teleport; cannot act on entry turn.
- `thunder_god_summon` / class `ThunderGodSummon`: 攻4 守5 速4 范3 魔0; no skills/traits; 5-round duration.
- `element_hunter_clone` / class `ElementHunterClone`: copies ElementHunter's current stats, hp, mana, and mana points when created by EarthWalker; clone cannot attack or use skills; any clone is destroyed immediately by damage without damage calculation.

## Reusable Skill Index

- `magic_wall` / `MagicWallSkill`: passive multi-target ally shield; 1 mana per target; temporary shield until chain end.
- `light_wall` / `LightWallSkill`: passive multi-target ally shield; same cost and duration rules as Magic Wall.
- `protection` / `PassiveProtectionSkill`: passive self-only shield; 1 mana; 2 shields until turn end.
- `evasion` / `PassiveEvasionSkill`: passive exact 1-cell straight move; 0.5 mana; twice per turn; no valid move in Sandstorm.
- `backstep_shot` / `BackstepShotSkill`: passive exact 2-cell straight pass-through retreat; 0.5 mana; twice per turn; optional counter only against chain source.
- `knockback` / `KnockbackSkill`: passive shield plus outward push.
- `shensu` / `ShensuSkill`: next normal movement this turn +3 cells.
- `harden` / `HardenSkill`: defense +1 for 2 rounds.
- `stealth` / `StealthSkill`: self stealth; direct targeting blocked for enemies; point-cell overlap can hit; breaks on attack/skill; blocked in Sandstorm.
- `pierce` / `PierceSkill`: contiguous straight line 2 cells touching caster; full area selection required unless board edge truncates it.
- `machine_gun` / `MachineGunSkill`: contiguous straight line 3 cells touching caster; enemy-only damage.
- `defend_twice` / `DefendTwiceSkill`: ally/self defense +1; same caster does not stack.
- `heal` / `HealSkill`: ally/self heals 1/4 hp.
- `baptism` / `BaptismSkill`: human ally gains magic immunity; field effects still apply.
- `chant` / `ChantSkill`: target in range gains mana points, not mana.
- `complete_burn`, `blizzard`, `wind_sand`, and `plant_growth`: remote area skills; area selection can truncate at board edges and must include at least one caster-range cell.
- `rending`: one-cell range damage with shield pierce.
- `crazy_sand`: line damage plus teleport, with destination validity baked into selectable patterns.
- `dragon_breath`: nearby edge-truncated `2*2` range damage, must touch caster orthogonally or diagonally.
- `rock_absorb`: custom `stat_cells` frontend selection; chosen stat plus selected growth cells.
- `rock_cannon`: custom `body_direction` frontend selection; selected body cells plus direction.
