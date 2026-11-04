from __future__ import annotations

import re
import sys
from wujiang.tactical.prediction import random, forecasting_active, direct_scoring
from copy import copy
from contextlib import contextmanager
from itertools import permutations
from typing import Any, Callable

from wujiang.tactical.engine.army import is_army_soldier
from wujiang.tactical.engine.core import (
    ActionError,
    ActionMiss,
    Battle,
    BattleComponent,
    BattleFieldEffect,
    DamageContext,
    HealContext,
    HeroUnit,
    MoveContext,
    Position,
    Skill,
    Stats,
    StatusEffect,
    TargetContext,
    TemporaryDefenseStatus,
    Trait,
    Unit,
)
from wujiang.tactical.heroes.base import AbstractHero
from wujiang.tactical.heroes.hero_kit import hero_kit_for
from wujiang.tactical.heroes.common import (
    AttackCountTrait,
    BackstepShotSkill,
    BaptismSkill,
    BlockCounterTrait,
    ChantSkill,
    DashMoveSkill,
    DefendTwiceSkill,
    DrainManaSkill,
    FlagStatus,
    FlyingTrait,
    HardenSkill,
    HealSkill,
    KnockbackSkill,
    LightWallSkill,
    InvincibleUntilActionStatus,
    MachineGunSkill,
    MagicWallSkill,
    MagicImmunityStatus,
    PassiveEvasionSkill,
    PassiveProtectionSkill,
    PierceSkill,
    NextNormalMoveBoostStatus,
    ShensuSkill,
    StationaryRecoveryTrait,
    StatModifierStatus,
    StealthSkill,
    StoneWallSkill,
    ensure_ally,
    ensure_distance,
    ensure_enemy,
    is_mana_drain_immune,
    line_patterns,
    localized_line_patterns,
    match_payload_pattern,
    pattern_signature,
    pattern_selection_preview,
    payload_cells,
    payload_position,
    payload_target_unit,
    payload_target_units,
    positions_to_dict,
)
from wujiang.tactical.heroes.excel_roster_data import EXCEL_HERO_SPECS
from wujiang.tactical.heroes import storm_spirit  # register R306 kit
from wujiang.tactical.heroes import fisher_garden_demon  # register R320/R321 kits
from wujiang.tactical.heroes import flame_manager  # register R322 kit
from wujiang.tactical.heroes import wyvern_raider  # register R323 kit
from wujiang.tactical.heroes import gentleman  # register R325 kit
from wujiang.tactical.heroes import super_crow  # register R324 kit
from wujiang.tactical.heroes.flying_dragon_ship import (
    AddHealthSkill, AddManaSkill, DefenseOneSkill, DepartureSkill,
    FlyingDragonShipSkill, RutoAttackLockTrait, RutoShipAttackTrait, ShipBorrowPermissionTrait,
    SilverSignalFlareSkill,
)
from wujiang.tactical.heroes import electronic_infinity  # register R373 kit
from wujiang.tactical.heroes.mechanics.blood_eater import (
    MagicShieldSkill,
    RecoverManaSkill,
)
from wujiang.tactical.heroes.mechanics.declared_area import DeclaredAreaSkillMixin
from wujiang.tactical.heroes.mechanics.dragon_breath import (
    DragonBreathSkill,
    RemoteDragonBreathSkill,
)
from wujiang.tactical.heroes.mechanics.half_pierce import HalfPierceAttackTrait
from wujiang.tactical.heroes.mechanics.jade import (
    CommonMissileSkill,
    IonShieldSkill,
    LaserSkill,
    QuantumShieldSkill,
)
from wujiang.tactical.heroes.mechanics.next_five_effects import (
    apply_piercing_status_effect,
    damage_followup_effect_applies,
    remote_rectangle_patterns,
)
from wujiang.tactical.heroes.mechanics.li import AttackLifeStealTrait, ChainPullSkill
from wujiang.tactical.heroes.mechanics.soul_wraith import AttackManaDrainTrait, BasicAttackImmunityTrait, PassThroughMovementTrait
from wujiang.tactical.heroes.mechanics.mana_recovery import NaturalManaRecoveryTrait
from wujiang.tactical.heroes.mechanics.next_five_geometry import ALL_DIRECTIONS, nearby_rectangle_patterns, position_key, square_around_cells
from wujiang.tactical.heroes.mechanics.clone_split import SplitSkill
from wujiang.tactical.heroes.mechanics.masamune import ArcAttackTrait
from wujiang.tactical.heroes.mechanics.kiku import KikuLegacyStatus, PassiveSkillLockStatus
from wujiang.tactical.heroes.mechanics.giant_shield import shield_named_neighbor
from wujiang.tactical.heroes.mechanics.piercing_control import piercing_nondamage_recipient
from wujiang.tactical.heroes.mechanics.ancient_mecha import AncientRepairSkill, ancient_cursed_rust_targets
from wujiang.tactical.heroes.mechanics.messenger_crow import MessengerCrowSummon
from wujiang.tactical.heroes.mechanics.mimic import MimicSkill
from wujiang.tactical.heroes.mechanics.fried_soldier import (
    FriedAuraTrait, FriedInspireStatus, RoyalSoldierSummon,
)
from wujiang.tactical.heroes.mechanics.stone_people import (
    MediumStoneSummon, SmallStoneSummon, stone_summon_anchors,
)
from wujiang.tactical.heroes.mechanics.boxer import BoxerTauntStatus


from wujiang.tactical.heroes.mechanics.nameless_messenger import MessengerDamageSkillManaTrait
from wujiang.tactical.heroes.mechanics.uesugi import _text
from wujiang.tactical.heroes.mechanics.winged_leopard import EagleEyeStatus
from wujiang.tactical.heroes.mechanics.wuchang import WuchangAttackSealStatus, WuchangMistField, WuchangMistImmunityStatus

from wujiang.tactical.heroes.mechanics.aaron_unicorn import AaronDestroyedSummonPendingStatus
from wujiang.tactical.heroes.mechanics.floating_cannon import (
    FloatingCannonBerserkStatus,
    FloatingCannonBuffStatus,
    FloatingCannonStatTrait,
    FloatingCannonSummon,
    floating_cannons_for,
)
from wujiang.tactical.heroes.mechanics.foundation import (
    BasicAttackPierceTrait,
    GuardianFinaleStatus,
    NaturalHealTrait,
    NaturalRecoveryTrait,
    PermanentMagicImmunityTrait,
    RemotePierceSkill,
    StationaryHealTrait,
    StationaryManaRecoveryTrait,
)
from wujiang.tactical.heroes.mechanics.lao_mage import LaoDamageStatReadyStatus, MageCloakEquippedStatus, MageCloakSummon
from wujiang.tactical.heroes.mechanics.mountain_god import LargeDrainManaSkill, MountainGodCounterStatus
from wujiang.tactical.heroes.mechanics.natsume import WindWallBlockStatus, WindWallCounterStatus

from wujiang.tactical.heroes.mechanics.avalanche import BigAvalancheWeatherEffect
from wujiang.tactical.heroes.mechanics.deadly_bow import BASIC_ATTACK_COPY_TRAIT_TYPES, DeadlyBowSkill
from wujiang.tactical.heroes.mechanics.hell_slash import HellSlashSkill
from wujiang.tactical.heroes.mechanics.holy_sola import SolaHarvestAuraEffect
from wujiang.tactical.heroes.mechanics.iaido import (
    FeiWangSpeedOnHeroKillTrait,
    ManaDrainImmunityTrait,
    PerfectDeflectStatus,
    PierceImmunityTrait,
)
from wujiang.tactical.heroes.mechanics.inner_dimension import (
    InnerDimensionSwordStatus,
    KingsInsightField,
)
from wujiang.tactical.heroes.mechanics.jirobo import JiroboAfterAttackStatus
from wujiang.tactical.heroes.mechanics.nuclear_gravity import GravityFieldSkill
from wujiang.tactical.heroes.mechanics.racial_weather import (
    PandemoniumSkill,
    RacialWeatherEffect,
    SimpleGlobalWeatherEffect,
    WeatherUltimateSkill,
    WetlandGrasslandEffect,
    weather_race_eligible,
)
from wujiang.tactical.heroes.mechanics.remi_bat import (
    RemiBatSummon,
    RemiUndyingTrait,
    _payload_direction,
)
from wujiang.tactical.heroes.mechanics.true_blade_bird import skill_has_movement_effect
from wujiang.tactical.heroes.mechanics.world_seed import (
    JudgmentStoneSummon,
    WorldRootSummon,
    WorldSeedSkillEffectGuardTrait,
    alive_world_root_numbers,
    apply_judgment_stone_explosion,
)

from wujiang.tactical.heroes.mechanics.fusion_rush import FusionCircleAttackTrait, NuclearRushStatus
from wujiang.tactical.heroes.mechanics.gladiator_core import (
    gladiator_swap,
    gladiator_swap_destinations,
)
from wujiang.tactical.heroes.mechanics.h1_line_damage import H1LineDamageSkill
from wujiang.tactical.heroes.mechanics.heaven_punishment import (
    HeavenPunishmentSkill,
    SkillDisabledStatus,
)
from wujiang.tactical.heroes.mechanics.hell_dog_hunter import HellDogSummon
from wujiang.tactical.heroes.mechanics.hell_dragon import (
    FlyingDragonOrbSkill,
    FlyingDragonOrbStatus,
    HellDragonFireSkill,
)
from wujiang.tactical.heroes.mechanics.hell_wargod import (
    HellWarGodRebirthTrait,
    HellWarGodStatus,
    resolve_hell_war_god_death_sweep,
)
from wujiang.tactical.heroes.mechanics.thor import ThorDestroyedLightningStatus
from wujiang.tactical.heroes.mechanics.vain_shadow import (
    FlorenzaAttackDebuffStatus,
    VainGiantShadowStatus,
)
from wujiang.tactical.heroes.mechanics.weapon_copy import (
    GlobalAttackStatus,
    InfiniteArmorSkill,
    InfiniteRobeSkill,
    PhysicalImmunityStatus,
    WeaponCopyStatus,
)

from wujiang.tactical.heroes.mechanics.black_cat import BlackCatFormStatus
from wujiang.tactical.heroes.mechanics.electric_wind import (
    ElectricWindSkill,
    ElectricWindStatus,
    SkySanctuarySkill,
)
from wujiang.tactical.heroes.mechanics.electronic_barrier import ElectronicBarrierAuraField
from wujiang.tactical.heroes.mechanics.electronic_guard import (
    ElectromagneticFieldEffect,
    NovaChargeStatus,
)
from wujiang.tactical.heroes.mechanics.fuma import FumaTrapEffect
from wujiang.tactical.heroes.mechanics.ice_emperor import FloatingCraneCityField
from wujiang.tactical.heroes.mechanics.iron_chain_red import RedRandomPierceTrait
from wujiang.tactical.heroes.mechanics.nian import NianNoHealStatus, NianRoarStatus
from wujiang.tactical.heroes.mechanics.rainbow_mirror import FriendlyMirrorStatus
from wujiang.tactical.heroes.mechanics.sanctuary import SkySanctuaryAuraEffect
from wujiang.tactical.heroes.mechanics.sphinx_solar import SphinxCannonSummon
from wujiang.tactical.heroes.mechanics.terrain_helpers import _destroy_terrain_in_cells
from wujiang.tactical.heroes.mechanics.tianyuan import TianyuanCounterStatus, TianyuanSkill
from wujiang.tactical.heroes.mechanics.zero_pass_through import PassThroughDamageTrait

HeroFactory = Callable[[int], object]


def _slug_tail(source_row: int, name: str) -> str:
    ascii_tail = re.sub(r"[^0-9a-zA-Z_]+", "_", name).strip("_").lower()
    return ascii_tail or f"r{source_row:03d}"


def _common_skill_factory(fragment: dict[str, Any]) -> Skill | None:
    # 旧 Excel 提取结果把纯名称“导弹/离子盾”标为非通用；最新通用规则优先。
    if _text(fragment.get("fragment")) == "导弹":
        return CommonMissileSkill()
    if _text(fragment.get("fragment")) == "离子盾":
        return IonShieldSkill()
    if not bool(fragment.get("common")):
        return None
    name = _text(fragment.get("name"))
    source = _text(fragment.get("fragment"))
    if name == "光墙":
        return LightWallSkill()
    if name == "魔墙":
        return MagicWallSkill()
    if name == "石墙":
        return StoneWallSkill()
    if name == "保护":
        return PassiveProtectionSkill()
    if name == "回避":
        return PassiveEvasionSkill()
    if name == "神速":
        return ShensuSkill()
    if name in {"变硬", "硬化"}:
        return HardenSkill()
    if name == "飞跃":
        return DashMoveSkill(
            "fly_leap",
            "飞跃",
            "普通技能：费 1 魔，每回合最多 1 次，直线飞行移动恰好 3 格。",
            max_distance=3,
            exact_distance=3,
            mana_cost=1,
            max_uses_per_turn=1,
            straight_only=True,
            ignore_units=True,
        )
    if name == "穿刺":
        return PierceSkill()
    if name == "远程穿刺":
        return RemotePierceSkill()
    if name == "震开":
        return KnockbackSkill()
    if name == "机枪":
        return MachineGunSkill()
    if name in {"吸魔", "吸魔（通用）"}:
        return DrainManaSkill()
    if name == "回魔":
        return RecoverManaSkill()
    if name == "魔盾":
        return MagicShieldSkill()
    if name == "分身":
        return SplitSkill()
    if name in {"链条", "锁链"}:
        return ChainPullSkill()
    if name == "龙息":
        return DragonBreathSkill()
    if name == "远程龙息":
        return RemoteDragonBreathSkill()
    if name in {"守*2", "守＊2"}:
        return DefendTwiceSkill()
    if name in {"回血", "治疗"}:
        return HealSkill()
    if name == "洗礼":
        return BaptismSkill()
    if name == "吟唱":
        return ChantSkill()
    if name == "隐身":
        return StealthSkill()
    if name == "撤步射击":
        return BackstepShotSkill()
    if source in {"离子盾", "激光"}:
        if source == "离子盾":
            return IonShieldSkill()
        return LaserSkill()
    return None


def _common_trait_factory(fragment: dict[str, Any]) -> Trait | None:
    if _text(fragment.get("fragment")).replace(" ", "") == "穿人有伤害":
        return PassThroughDamageTrait()
    if not bool(fragment.get("common")):
        return None
    text = _text(fragment.get("fragment")).replace(" ", "")
    if text == "飞行":
        return FlyingTrait()
    if text == "可穿人":
        return PassThroughMovementTrait()
    if text == "物免":
        return BasicAttackImmunityTrait()
    if text == "魔免":
        return PermanentMagicImmunityTrait()
    if text in {"攻击吸血", "普攻吸血"}:
        return AttackLifeStealTrait()
    if text in {"攻击吸魔", "普攻吸魔"}:
        return AttackManaDrainTrait()
    if text == "弧形攻击":
        return ArcAttackTrait()
    if text in {"可格挡反击", "可格挡，反击", "格挡反击"}:
        return BlockCounterTrait()
    if text == "自然回魔":
        return NaturalManaRecoveryTrait()
    if text == "自然回血":
        return NaturalHealTrait()
    if text == "自然回复":
        return NaturalRecoveryTrait()
    if text == "原地回魔":
        return StationaryManaRecoveryTrait()
    if text == "原地回血":
        return StationaryHealTrait()
    if text == "原地回复":
        return StationaryRecoveryTrait()
    if text in {"攻击半破魔", "普攻半破魔"}:
        return HalfPierceAttackTrait()
    if text == "普攻破魔":
        return BasicAttackPierceTrait()
    match = re.fullmatch(r"(?:攻击|攻)([二两三四五六七八九十\d]+)次", text)
    if match:
        attacks = _parse_small_int(match.group(1))
        if attacks is not None:
            return AttackCountTrait(attacks)
    return None


def _parse_small_int(text: str) -> int | None:
    value = _text(text)
    if value.isdigit():
        return int(value)
    mapping = {
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "十": 10,
    }
    return mapping.get(value)


def _footprint_size(spec: dict[str, Any]) -> tuple[int, int]:
    fragments = [_text(item.get("fragment")) for item in spec.get("trait_fragments", [])]
    text = "；".join(fragments)
    match = re.search(r"占\s*(\d+)\s*\*\s*(\d+)", text)
    if match:
        return max(1, int(match.group(1))), max(1, int(match.group(2)))
    if re.search(r"占\s*(?:4|四)格", text):
        return 2, 2
    if re.search(r"占\s*(?:9|九)格", text):
        return 3, 3
    return 1, 1


def _make_build_skills(spec: dict[str, Any]) -> Callable[[AbstractHero], list[Skill]]:
    kit = hero_kit_for(str(spec["code"]))

    def build_skills(self: AbstractHero) -> list[Skill]:
        skills: list[Skill] = []
        seen_codes: set[str] = set()
        for fragment in spec.get("skill_fragments", []):
            skill = (kit.special_skill(fragment) if kit is not None else None) or _common_skill_factory(fragment)
            if skill is None or skill.code in seen_codes:
                continue
            seen_codes.add(skill.code)
            skills.append(skill)
        if kit is not None:
            for skill in kit.extra_skills():
                if skill.code not in seen_codes:
                    seen_codes.add(skill.code)
                    skills.append(skill)
            skills = kit.augment_skills(skills)
        return skills

    return build_skills


def _make_build_traits(spec: dict[str, Any]) -> Callable[[AbstractHero], list[Trait]]:
    kit = hero_kit_for(str(spec["code"]))

    def build_traits(self: AbstractHero) -> list[Trait]:
        traits: list[Trait] = []
        seen_names: set[str] = set()
        for fragment in spec.get("trait_fragments", []):
            trait = (kit.special_trait(fragment) if kit is not None else None) or _common_trait_factory(fragment)
            if trait is None or trait.name in seen_names:
                continue
            seen_names.add(trait.name)
            traits.append(trait)
        if kit is not None:
            for trait in kit.extra_traits():
                if trait.name not in seen_names:
                    seen_names.add(trait.name)
                    traits.append(trait)
        return traits

    return build_traits


def _hero_class_from_spec(spec: dict[str, Any]) -> type[AbstractHero]:
    kit = hero_kit_for(str(spec["code"]))
    width, height = _footprint_size(spec)
    source_stats = Stats(
        attack=float(spec["attack"]),
        defense=float(spec["defense"]),
        speed=float(spec["speed"]),
        attack_range=float(spec["range"]),
        mana=float(spec["mana"]),
    )
    attrs: dict[str, Any] = {
        "__module__": __name__,
        "hero_code": spec["code"],
        "hero_name": spec["name"],
        "role": spec["role"],
        "attribute": spec["attribute"],
        "race": spec["race"],
        "level": int(spec["level"]),
        "base_stats": kit.base_stats(source_stats) if kit is not None else source_stats,
        "raw_skill_text": spec["raw_skill_text"],
        "raw_trait_text": spec["raw_trait_text"],
        "source_row": int(spec["source_row"]),
        "build_skills": _make_build_skills(spec),
        "build_traits": _make_build_traits(spec),
    }
    if width > 1 or height > 1:
        attrs["footprint_width"] = width
        attrs["footprint_height"] = height
        attrs["entry_footprint_width"] = width
        attrs["entry_footprint_height"] = height
    if kit is not None:
        attrs.update(kit.unit_attributes())
        if kit.ai_policy is not None:
            attrs["ai_policy"] = kit.ai_policy
    class_name = f"ExcelHero{int(spec['source_row']):03d}_{_slug_tail(int(spec['source_row']), spec['code'])}"
    return type(class_name, (AbstractHero,), attrs)


IMPLEMENTED_EXCEL_HERO_CODES: frozenset[str] = frozenset(
    {
        "excel_r020",
        "excel_r021",
        "excel_r022",
        "excel_r023",
        "excel_r024",
        "excel_r025",
        "excel_r026",
        "excel_r027",
        "excel_r028",
        "excel_r029",
        "excel_r030",
        "excel_r031",
        "excel_r032",
        "excel_r033",
        "excel_r034",
        "excel_r035",
        "excel_r036",
        "excel_r037",
        "excel_r047",
        "excel_r056",
        "excel_r059",
        "excel_r066",
        "excel_r070",
        "excel_r071",
        "excel_r093",
        "excel_r094",
        "excel_r113",
        "excel_r118",
        "excel_r120",
        "excel_r123",
        "excel_r126",
        "excel_r127",
        "excel_r136",
        "excel_r137",
        "excel_r138",
        "excel_r139",
        "excel_r142",
        "excel_r143",
        "excel_r158",
        "excel_r166",
        "excel_r172",
        "excel_r187",
        "excel_r188",
        "excel_r191",
        "excel_r192",
        "excel_r193",
        "excel_r194",
        "excel_r195",
        "excel_r196",
        "excel_r197",
        "excel_r198",
        "excel_r215",
        "excel_r219",
        "excel_r221",
        "excel_r222",
        "excel_r223",
        "excel_r206",
        "excel_r224",
        "excel_r225",
        "excel_r238",
        "excel_r239",
        "excel_r240",
        "excel_r242",
        "excel_r243",
        "excel_r244",
        "excel_r245",
        "excel_r246",
        "excel_r247",
        "excel_r248",
        "excel_r249",
        "excel_r250",
        "excel_r251",
        "excel_r252",
        "excel_r253",
        "excel_r254",
        "excel_r255",
        "excel_r256",
        "excel_r257",
        "excel_r259",
        "excel_r260",
        "excel_r262",
        "excel_r263",
        "excel_r264",
        "excel_r277",
        "excel_r290",
        "excel_r291",
        "excel_r302",
        "excel_r303",
        "excel_r304",
        "excel_r305",
        "excel_r306",
        "excel_r320",
        "excel_r321",
        "excel_r322",
        "excel_r323",
        "excel_r324",
        "excel_r325",
        "excel_r308",
        "excel_r326",
        "excel_r327",
        "excel_r328",
        "excel_r329",
        "excel_r373",
        "excel_r337",
        "excel_r352",
        "excel_r356",
        "excel_r379",
    }
)


# Late registration lets per-hero kits reference the component classes above.
from wujiang.tactical.heroes.kits import install_all as _install_excel_kits

_install_excel_kits(sys.modules[__name__])


EXCEL_HERO_REGISTRY: dict[str, HeroFactory] = {
    spec["code"]: _hero_class_from_spec(spec)
    for spec in EXCEL_HERO_SPECS
}
for _excel_hero_class in EXCEL_HERO_REGISTRY.values():
    globals()[_excel_hero_class.__name__] = _excel_hero_class

EXCEL_HERO_NAMES_BY_CODE: dict[str, str] = {
    spec["code"]: spec["name"]
    for spec in EXCEL_HERO_SPECS
}


_legacy_mechanic_classes: dict[str, type] | None = None


def __getattr__(name: str) -> type:
    """Resolve old pickle globals after their classes moved to mechanics modules.

    Historical battle snapshots name classes in this module. Keep the old
    import path readable without making the assembly layer own their behavior.
    """
    if not name or not name[0].isupper():
        raise AttributeError(name)
    global _legacy_mechanic_classes
    if _legacy_mechanic_classes is None:
        import importlib
        import pkgutil

        from wujiang.tactical.heroes import mechanics

        classes: dict[str, type] = {}
        for info in pkgutil.iter_modules(mechanics.__path__):
            module = importlib.import_module(f"{mechanics.__name__}.{info.name}")
            for value in vars(module).values():
                if isinstance(value, type) and value.__module__ == module.__name__:
                    classes[value.__name__] = value
        _legacy_mechanic_classes = classes
    if name in _legacy_mechanic_classes:
        value = _legacy_mechanic_classes[name]
        globals()[name] = value
        return value
    raise AttributeError(name)
