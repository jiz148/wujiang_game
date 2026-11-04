"""Legacy import and pickle facade for the original next-five hero module.

Battlefield behavior belongs to the hero and shared mechanics modules. New
runtime code should import those modules directly.
"""

from __future__ import annotations

import math
from wujiang.tactical.heroes.mechanics.doomlight_dragon import (
    ApocalypseSkill,
    DoomLightRetaliationTrait,
    DoomLightSkill,
    DoomLightStatus,
    DoomlightDragon,
)

from wujiang.tactical.heroes.mechanics.rock_god import (
    RockAbsorbFootprintStatus,
    RockAbsorbLinkStatus,
    RockAbsorbSkill,
    RockAbsorbStatStatus,
    RockCannonSkill,
    RockGod,
    RockGodSandstormTrait,
)

from wujiang.tactical.heroes.mechanics.undead_king_lina import (
    AttackLockTrait,
    LinaDestroyRewardTrait,
    LinaSandstormRecoveryTrait,
    NoEnemyHealAuraTrait,
    UndeadKingLina,
)

from wujiang.tactical.heroes.mechanics.element_hunter import (
    AllStatsPlusStatus,
    BlizzardSkill,
    BurningManaStatus,
    CompleteBurnSkill,
    EarthWalkerCleanupStatus,
    EarthWalkerSkill,
    ElementHunter,
    ElementHunterClone,
    ElementalEffectTrait,
    PlantGrowthFieldEffect,
    PlantGrowthSkill,
    RemoteAreaDamageSkill,
    RendingSkill,
    SummonLifetimeStatus,
    ThunderGodSkill,
    ThunderGodSummon,
    ThunderResetWatcherStatus,
    WaterWaveSkill,
    blizzard_status_factory,
    complete_burn_status_factory,
)

from wujiang.tactical.heroes.mechanics.sandstorm import (
    CrazySandSkill,
    RockGodSandstormAura,
    SandstormWeatherEffect,
    WindSandSkill,
)

from wujiang.tactical.heroes.mechanics.dragon_rider import (
    DragonAreaAttackTrait,
    DragonAttackDebuffStatus,
    DragonDamageResistanceTrait,
    DragonMountCooldownStatus,
    DragonMountSummon,
    DragonMountedStartTrait,
    DragonRideableMountTrait,
    DragonRider,
    DragonRiderTurnTrait,
    DragonSlashSkill,
    DragonSlashSlowStatus,
    DragonSmokeFieldEffect,
    DragonSmokeRestrictionStatus,
    DragonSmokeSkill,
    DragonSummonSkill,
    alive_owned_dragon_mount,
    apply_dragon_piercing_status,
    replace_source_status,
)

from wujiang.tactical.heroes.mechanics.blood_eater import (
    BloodArtSkill,
    BloodDanceLockStatus,
    BloodDanceSkill,
    BloodEater,
    BloodGuardSkill,
    BloodManaPointTrait,
    BloodRiteAttackStatus,
    BloodSkillDamageGuardTrait,
    MagicShieldSkill,
    RecoverManaSkill,
    SacrificeRitualSkill,
)

from wujiang.tactical.heroes.mechanics.n import (
    MagneticWaveSkill,
    N,
    NAttackCountTrait,
    NAttackManaPointTrait,
    NManaGuardTrait,
    NSkill,
    UnlimitedManaTrait,
)

from wujiang.tactical.heroes.mechanics.jade import (
    CommonMissileSkill,
    DelayedCooldownMultiTargetWallSkill,
    IonShieldSkill,
    Jade,
    JadeMachineGunSkill,
    JadeReactiveOverclockTrait,
    LaserSkill,
    MechEnhancementSkill,
    MissileAreaMixin,
    MissileSkill,
    PlasmaThrusterSkill,
    QuantumShieldSkill,
    StanceFieldEffect,
    StanceSkill,
    maybe_queue_jade_reactive_bonus,
)

from wujiang.tactical.heroes.mechanics.mana_point_cost import ManaPointCostSkill

from wujiang.tactical.heroes.mechanics.declared_area import DeclaredAreaSkillMixin

from wujiang.tactical.heroes.mechanics.area_helpers import (
    area_patterns_preview,
    combined_remote_rectangle_patterns,
    resolve_area_damage,
)

from wujiang.tactical.heroes.mechanics.dragon_breath import DragonBreathSkill, RemoteDragonBreathSkill

from wujiang.tactical.heroes.mechanics.half_pierce import HalfPierceAttackTrait

from wujiang.tactical.heroes.mechanics.next_five_effects import (
    apply_piercing_status_effect,
    damage_followup_effect_applies,
    remote_rectangle_patterns,
)

from wujiang.tactical.heroes.mechanics.soul_wraith import (
    AttackManaDrainTrait,
    BasicAttackImmunityTrait,
    NearbyEnemyHeroMagicImmunityTrait,
    PassThroughMovementTrait,
    SoulWraith,
    SoulWraithFailedAttackGrowthTrait,
    SoulWraithGrowthStatus,
)

from wujiang.tactical.heroes.mechanics.chanter import (
    CardTranspositionSkill,
    Chanter,
    ChanterCardCleanupTrait,
    ChanterCardFieldEffect,
    ChanterCardSkill,
    FormShiftSkill,
    FormShiftStatus,
    MagicClawLockStatus,
    MagicClawSkill,
    card_effect_area,
    cell_in_cells,
    consume_pierced_shield_for_effect,
    unit_in_cells,
)

from wujiang.tactical.heroes.mechanics.li import (
    AntiSpeedReductionTrait,
    AttackLifeStealTrait,
    ChainPullSkill,
    EssenceAttackStatus,
    EssenceSkill,
    ForesightBlockStatus,
    ForesightRewardStatus,
    ForesightSkill,
    Li,
    RedHeatSkill,
    RedHeatStatus,
    SplitMovementTrait,
    StillnessSkill,
    StillnessStatus,
    WhirlwindAttackSkill,
)

from wujiang.tactical.heroes.mechanics.mana_recovery import NaturalManaRecoveryTrait

from wujiang.tactical.heroes.mechanics.next_five_geometry import (
    ALL_DIRECTIONS,
    ORTHOGONAL_DIRECTIONS,
    impact_area,
    nearby_rectangle_patterns,
    position_key,
    positions_connected,
    square_around_cells,
)

from wujiang.tactical.heroes.mechanics.erasure_apostle import (
    DescentMomentAttackStatus,
    DescentMomentSkill,
    ErasureApostle,
    ErasureApostleDestroyRewardTrait,
    ErasureCounterStatus,
    ErasureSkill,
    ExtraStealthSkill,
    PrematureBurialSkill,
    ShadowCounterSkill,
    erasure_counter_count,
)

from wujiang.tactical.heroes.mechanics.masamune import (
    ARC_DIRECTION_CHOICES,
    ArcAttackTrait,
    FreeShensuSkill,
    HealMountSkill,
    Masamune,
    MasamuneArcAttackTrait,
    MasamuneMountedStartTrait,
    MotorHorseCooldownStatus,
    MotorHorseSkill,
    MotorHorseSummon,
    MountedFreeLeapTrait,
    MountedLeapSkill,
    RideableMountTrait,
    SixBladeStyleSkill,
    SixBladeStyleStatus,
    TripleStrikeAttackTrait,
    UnmountedCombatTrait,
    alive_owned_motor_horse,
    arc_direction_cells,
)

from wujiang.tactical.heroes.mechanics.clone_split import SplitSkill, StandardCloneSummon

from wujiang.tactical.heroes.mechanics.status_replacement import replace_status_by_name

from wujiang.tactical.prediction import random, direct_scoring
from typing import Any, Callable, Iterable, Optional

from wujiang.tactical.engine.core import (
    ActionError,
    Battle,
    BattleFieldEffect,
    DamageContext,
    HealContext,
    HeroUnit,
    Position,
    QueuedAction,
    Skill,
    Stats,
    StatusEffect,
    TargetContext,
    Trait,
)
from wujiang.tactical.engine.army import is_army_soldier
from wujiang.tactical.engine.geometry import rectangle_pattern_coordinates
from wujiang.tactical.heroes.base import AbstractHero
from wujiang.tactical.heroes.common import (
    AttackCountTrait,
    DashMoveSkill,
    DrainManaSkill,
    FlagStatus,
    FlyingTrait,
    HardenSkill,
    InvincibleUntilActionStatus,
    KnockbackSkill,
    LightWallSkill,
    MagicImmunityStatus,
    MachineGunSkill,
    MultiTargetChainShieldSkill,
    PassiveProtectionSkill,
    OverhealTrait,
    PierceSkill,
    ShensuSkill,
    SourcedDefenseStatus,
    StatModifierStatus,
    StoneWallSkill,
    StealthSkill,
    WindowChargeSkill,
    dedupe_positions,
    ensure_distance,
    ensure_ally,
    is_mana_drain_immune,
    match_payload_pattern,
    pattern_selection_preview,
    pattern_signature,
    payload_cells,
    payload_position,
    payload_target_unit,
    positions_to_dict,
)
