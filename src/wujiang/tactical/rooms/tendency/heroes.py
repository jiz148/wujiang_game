"""Per-hero tendency weights for the first five implemented heroes.

A weight is not a path. When two intents are close, the picker may sample.
"""
from __future__ import annotations

from wujiang.tactical.rooms.tendency.situation import Situation

INTENTS = ("survive", "trade", "pressure", "spacing", "open_skill")


def _base() -> dict[str, float]:
    return {name: 0.2 for name in INTENTS}


def generic_weights(situation: Situation) -> dict[str, float]:
    weights = _base()
    weights["pressure"] += 0.6
    if situation.hp_ratio < 0.35:
        weights["survive"] += 1.4
    if situation.attack_range >= 4:
        weights["spacing"] += 0.8
    if situation.enemy_skill_codes or situation.self_skill_codes:
        weights["open_skill"] += 0.5
    if (
        situation.outnumbered
        and situation.can_attack_enemy
        and situation.weakest_enemy_hp_ratio is not None
        and situation.weakest_enemy_hp_ratio < 0.4
    ):
        weights["trade"] += 1.1
    return weights


def generic_skills(situation: Situation, intent: str) -> tuple[str, ...]:
    del situation, intent
    return ()


def ellie_weights(situation: Situation) -> dict[str, float]:
    weights = generic_weights(situation)
    weights["open_skill"] += 0.9
    weights["spacing"] += 0.4
    weights["pressure"] -= 0.2
    if situation.nearest_enemy_distance is not None and situation.nearest_enemy_distance <= 2:
        weights["spacing"] += 0.7
        weights["survive"] += 0.4
    if situation.hp_ratio >= 0.5 and situation.enemy_skill_codes:
        weights["open_skill"] += 0.5
    return weights


def ellie_skills(situation: Situation, intent: str) -> tuple[str, ...]:
    if intent == "survive":
        return ("crystal_ball",)
    if intent == "trade" and situation.allies_alive >= 3 and situation.hp_ratio >= 0.55:
        return ("curse", "experiment", "medusa")
    return ("curse", "drain_mana", "crystal_ball", "medusa")


def dark_human_weights(situation: Situation) -> dict[str, float]:
    weights = generic_weights(situation)
    weights["open_skill"] += 0.7
    weights["pressure"] += 0.5
    weights["spacing"] -= 0.2
    if not situation.stealthed:
        weights["open_skill"] += 0.6
    else:
        weights["trade"] += 0.5
        weights["pressure"] += 0.4
    if situation.hp_ratio < 0.35:
        weights["survive"] += 1.2
    if situation.nearest_enemy_distance is not None and situation.nearest_enemy_distance > 3:
        weights["open_skill"] += 0.3
    return weights


def dark_human_skills(situation: Situation, intent: str) -> tuple[str, ...]:
    if intent == "survive" or situation.hp_ratio < 0.35:
        return ("into_darkness", "stealth")
    if situation.stealthed:
        return ("paralyzing_glove",)
    return ("stealth", "paralyzing_glove", "into_darkness")


def fire_funeral_weights(situation: Situation) -> dict[str, float]:
    weights = generic_weights(situation)
    weights["pressure"] += 0.7
    if situation.attack <= 1 and "judgment_fire" in situation.self_skill_codes:
        weights["open_skill"] += 1.6
    elif situation.enemies_alive >= 2:
        weights["open_skill"] += 0.6
    if situation.weakest_enemy_hp_ratio is not None and situation.weakest_enemy_hp_ratio < 0.35:
        weights["trade"] += 0.7
    return weights


def fire_funeral_skills(situation: Situation, intent: str) -> tuple[str, ...]:
    del intent
    if situation.attack <= 1:
        return ("judgment_fire", "great_funeral", "harden")
    if situation.enemies_alive >= 2:
        return ("great_funeral", "shensu", "harden")
    return ("shensu", "harden", "great_funeral")


def elite_soldier_weights(situation: Situation) -> dict[str, float]:
    weights = generic_weights(situation)
    weights["spacing"] += 1.1
    weights["pressure"] += 0.3
    if situation.nearest_enemy_distance is not None and situation.nearest_enemy_distance <= 3:
        weights["survive"] += 1.0
        weights["spacing"] += 0.6
        weights["trade"] -= 0.3
    elif situation.can_attack_enemy:
        weights["pressure"] += 0.6
        weights["open_skill"] += 0.4
    return weights


def elite_soldier_skills(situation: Situation, intent: str) -> tuple[str, ...]:
    if intent in {"survive", "spacing"} and (
        situation.nearest_enemy_distance is None or situation.nearest_enemy_distance > 3
    ):
        return ("shensu",)
    return ("headshot", "shensu")


def bard_weights(situation: Situation) -> dict[str, float]:
    weights = generic_weights(situation)
    weights["open_skill"] += 1.0
    weights["spacing"] += 0.5
    weights["pressure"] -= 0.3
    ally_hurt = situation.lowest_ally_hp_ratio is not None and situation.lowest_ally_hp_ratio < 0.6
    if situation.hp_ratio < 0.55 or ally_hurt:
        weights["survive"] += 0.8
        weights["open_skill"] += 0.4
    return weights


def medusa_weights(situation: Situation) -> dict[str, float]:
    weights = generic_weights(situation)
    weights["pressure"] += 0.9
    weights["open_skill"] += 0.4
    weights["spacing"] -= 0.3
    return weights


def medusa_skills(situation: Situation, intent: str) -> tuple[str, ...]:
    del situation, intent
    return ("medusa_blink",)


def element_hunter_weights(situation: Situation) -> dict[str, float]:
    weights = generic_weights(situation)
    weights["open_skill"] += 0.8
    weights["spacing"] += 0.4
    if situation.nearest_enemy_distance is not None and situation.nearest_enemy_distance <= 1:
        weights["spacing"] += 0.6
    return weights


def element_hunter_skills(situation: Situation, intent: str) -> tuple[str, ...]:
    del situation, intent
    return ("thunder_god", "water_wave", "shensu")


def undead_king_lina_weights(situation: Situation) -> dict[str, float]:
    weights = generic_weights(situation)
    weights["open_skill"] += 0.5
    weights["pressure"] += 0.5
    if not situation.stealthed:
        weights["open_skill"] += 0.7
    else:
        weights["trade"] += 0.6
    if situation.hp_ratio < 0.35:
        weights["survive"] += 0.8
    return weights


def undead_king_lina_skills(situation: Situation, intent: str) -> tuple[str, ...]:
    if situation.stealthed or intent == "pressure":
        return ("rending", "harden")
    return ("stealth", "rending", "harden")


def rock_god_weights(situation: Situation) -> dict[str, float]:
    weights = generic_weights(situation)
    weights["pressure"] += 0.8
    weights["spacing"] -= 0.2
    if situation.hp_ratio < 0.45:
        weights["survive"] += 0.7
    return weights


def rock_god_skills(situation: Situation, intent: str) -> tuple[str, ...]:
    del situation, intent
    return ("harden",)


def doomlight_dragon_weights(situation: Situation) -> dict[str, float]:
    weights = generic_weights(situation)
    weights["open_skill"] += 0.5
    weights["spacing"] += 0.5
    weights["pressure"] += 0.3
    return weights


def doomlight_dragon_skills(situation: Situation, intent: str) -> tuple[str, ...]:
    del situation, intent
    return ("shensu", "harden")


def masamune_weights(situation: Situation) -> dict[str, float]:
    weights = generic_weights(situation)
    weights["pressure"] += 0.7
    weights["open_skill"] += 0.4
    return weights


def masamune_skills(situation: Situation, intent: str) -> tuple[str, ...]:
    del situation, intent
    return ("motor_horse", "six_blade_style", "heal_mount", "mounted_leap")


def jade_weights(situation: Situation) -> dict[str, float]:
    weights = generic_weights(situation)
    weights["pressure"] += 0.4
    weights["spacing"] += 0.5
    weights["open_skill"] += 0.4
    if situation.hp_ratio < 0.45:
        weights["survive"] += 0.8
    return weights


def jade_skills(situation: Situation, intent: str) -> tuple[str, ...]:
    if intent == "survive" or situation.hp_ratio < 0.5:
        return ("mech_enhancement", "stance", "plasma_thruster")
    return ("stance", "mech_enhancement", "plasma_thruster")


def n_weights(situation: Situation) -> dict[str, float]:
    weights = generic_weights(situation)
    weights["open_skill"] += 0.6
    weights["pressure"] += 0.3
    return weights


def n_skills(situation: Situation, intent: str) -> tuple[str, ...]:
    del situation, intent
    return ("n_skill", "drain_mana")


def blood_eater_weights(situation: Situation) -> dict[str, float]:
    weights = generic_weights(situation)
    weights["open_skill"] += 0.9
    ally_hurt = situation.lowest_ally_hp_ratio is not None and situation.lowest_ally_hp_ratio < 0.6
    if situation.hp_ratio < 0.55 or ally_hurt:
        weights["survive"] += 0.7
    return weights


def blood_eater_skills(situation: Situation, intent: str) -> tuple[str, ...]:
    if intent in {"survive", "open_skill"} or situation.hp_ratio < 0.7:
        return ("blood_dance", "blood_art", "blood_guard", "recover_mana")
    return ("drain_mana", "blood_art", "recover_mana")


def li_weights(situation: Situation) -> dict[str, float]:
    weights = generic_weights(situation)
    weights["pressure"] += 0.7
    if situation.hp_ratio < 0.4:
        weights["survive"] += 1.3
        weights["pressure"] -= 0.4
    return weights


def li_skills(situation: Situation, intent: str) -> tuple[str, ...]:
    if intent == "survive" or situation.hp_ratio < 0.4:
        return ("stillness", "harden", "whirlwind_attack")
    return ("red_heat", "essence", "whirlwind_attack", "harden", "leap")


def chanter_weights(situation: Situation) -> dict[str, float]:
    weights = generic_weights(situation)
    weights["open_skill"] += 1.0
    weights["spacing"] += 0.7
    weights["pressure"] -= 0.3
    if situation.nearest_enemy_distance is not None and situation.nearest_enemy_distance <= 2:
        weights["spacing"] += 0.5
    return weights


def chanter_skills(situation: Situation, intent: str) -> tuple[str, ...]:
    del situation, intent
    return ("paralysis_card", "poison_card", "drain_card", "form_shift")


def erasure_apostle_weights(situation: Situation) -> dict[str, float]:
    weights = generic_weights(situation)
    weights["open_skill"] += 0.8
    weights["trade"] += 0.4
    if not situation.stealthed:
        weights["open_skill"] += 0.5
    if situation.hp_ratio < 0.35:
        weights["survive"] += 1.0
    return weights


def erasure_apostle_skills(situation: Situation, intent: str) -> tuple[str, ...]:
    if situation.stealthed:
        return ("premature_burial", "erasure", "descent_moment", "drain_mana")
    if intent == "survive":
        return ("extra_stealth", "stealth")
    return ("stealth", "premature_burial", "erasure", "descent_moment")


def dragon_rider_weights(situation: Situation) -> dict[str, float]:
    weights = generic_weights(situation)
    weights["pressure"] += 0.6
    weights["open_skill"] += 0.5
    return weights


def dragon_rider_skills(situation: Situation, intent: str) -> tuple[str, ...]:
    del situation, intent
    return ("summon_dragon",)


def soul_wraith_weights(situation: Situation) -> dict[str, float]:
    weights = generic_weights(situation)
    weights["pressure"] += 0.9
    weights["trade"] += 0.4
    weights["spacing"] -= 0.2
    if situation.hp_ratio < 0.3:
        weights["survive"] += 0.6
    return weights


def soul_wraith_skills(situation: Situation, intent: str) -> tuple[str, ...]:
    del situation, intent
    return ()


def excel_r020_weights(situation: Situation) -> dict[str, float]:
    weights = generic_weights(situation)
    weights["open_skill"] += 0.7
    weights["spacing"] += 0.6
    weights["pressure"] += 0.2
    return weights


def excel_r020_skills(situation: Situation, intent: str) -> tuple[str, ...]:
    del situation, intent
    return ("heaven_lock",)


def excel_r021_weights(situation: Situation) -> dict[str, float]:
    weights = generic_weights(situation)
    weights["pressure"] += 0.8
    weights["open_skill"] += 0.4
    if situation.hp_ratio < 0.4:
        weights["survive"] += 0.7
    return weights


def excel_r021_skills(situation: Situation, intent: str) -> tuple[str, ...]:
    if intent == "survive":
        return ("time_stop", "focus_reset", "iaido_charge")
    return ("iaido_charge", "focus_reset")


def motor_horse_weights(situation: Situation) -> dict[str, float]:
    weights = generic_weights(situation)
    weights["open_skill"] += 0.5
    weights["pressure"] += 0.4
    return weights


def motor_horse_skills(situation: Situation, intent: str) -> tuple[str, ...]:
    del situation, intent
    return ("shensu",)


def bard_skills(situation: Situation, intent: str) -> tuple[str, ...]:
    if intent in {"survive", "open_skill"} or situation.hp_ratio < 0.7:
        return ("heal", "defend_twice", "baptism", "chant")
    return ("great_holy_light", "chant", "defend_twice")


POLICIES = {
    "ellie": (ellie_weights, ellie_skills),
    "dark_human": (dark_human_weights, dark_human_skills),
    "fire_funeral": (fire_funeral_weights, fire_funeral_skills),
    "elite_soldier": (elite_soldier_weights, elite_soldier_skills),
    "bard": (bard_weights, bard_skills),
    "medusa": (medusa_weights, medusa_skills),
    "element_hunter": (element_hunter_weights, element_hunter_skills),
    "undead_king_lina": (undead_king_lina_weights, undead_king_lina_skills),
    "rock_god": (rock_god_weights, rock_god_skills),
    "doomlight_dragon": (doomlight_dragon_weights, doomlight_dragon_skills),
    "masamune": (masamune_weights, masamune_skills),
    "jade": (jade_weights, jade_skills),
    "n": (n_weights, n_skills),
    "blood_eater": (blood_eater_weights, blood_eater_skills),
    "li": (li_weights, li_skills),
    "chanter": (chanter_weights, chanter_skills),
    "erasure_apostle": (erasure_apostle_weights, erasure_apostle_skills),
    "dragon_rider": (dragon_rider_weights, dragon_rider_skills),
    "soul_wraith": (soul_wraith_weights, soul_wraith_skills),
    "excel_r020": (excel_r020_weights, excel_r020_skills),
    "excel_r021": (excel_r021_weights, excel_r021_skills),
    "motor_horse": (motor_horse_weights, motor_horse_skills),
}


def _generated_pair(
    safe: tuple[str, ...],
    *,
    ranged: bool,
    fragile: bool,
    bulky: bool,
    sneaky: bool,
    attack: float,
    speed: float,
):
    def weights(situation: Situation) -> dict[str, float]:
        scored = generic_weights(situation)
        if ranged:
            scored["spacing"] += 0.7
            scored["pressure"] += 0.15
        else:
            if fragile and speed >= 8:
                scored["spacing"] += 0.8
            elif attack >= 2:
                scored["pressure"] += 0.65
                scored["spacing"] -= 0.1
            if attack < 2:
                scored["open_skill"] += 0.8
        if fragile and situation.hp_ratio < 0.4:
            scored["survive"] += 1.0
        if bulky:
            scored["trade"] += 0.25
            if attack >= 2:
                scored["pressure"] += 0.2
        if safe:
            scored["open_skill"] += 0.5
        if sneaky and not situation.stealthed:
            scored["open_skill"] += 0.55
        return scored

    def preferred(situation: Situation, intent: str) -> tuple[str, ...]:
        if sneaky and not situation.stealthed and intent != "pressure":
            first = [code for code in safe if code in {"stealth", "extra_stealth"}]
            rest = [code for code in safe if code not in first]
            return tuple(first + rest)
        if intent == "survive":
            first = [code for code in safe if code in {"harden", "shensu", "stillness", "time_stop"}]
            return tuple(first or safe)
        return safe

    return weights, preferred


def _register_implemented() -> None:
    from wujiang.tactical.heroes.excel_roster import IMPLEMENTED_EXCEL_HERO_CODES
    from wujiang.tactical.heroes.registry import create_hero

    direction_skills = {"mana_pull", "fate_kick"}
    for code in sorted(IMPLEMENTED_EXCEL_HERO_CODES):
        if code in POLICIES:
            continue
        hero = create_hero(code, 1)
        safe: list[str] = []
        for skill in hero.build_skills():
            timing = getattr(skill, "timing", "")
            if timing == "passive" or skill.code in direction_skills:
                continue
            if timing not in {"active", "instant"}:
                continue
            safe.append(skill.code)
        stats = hero.base_stats
        POLICIES[code] = _generated_pair(
            tuple(safe),
            ranged=float(stats.attack_range) >= 3,
            fragile=float(stats.defense) <= 2,
            bulky=float(stats.defense) >= 5,
            sneaky="stealth" in safe,
            attack=float(stats.attack),
            speed=float(stats.speed),
        )


def weights_for(situation: Situation) -> dict[str, float]:
    pair = POLICIES.get(situation.hero_code)
    if pair is None:
        return generic_weights(situation)
    return pair[0](situation)


def skills_for(situation: Situation, intent: str) -> tuple[str, ...]:
    pair = POLICIES.get(situation.hero_code)
    if pair is None:
        return generic_skills(situation, intent)
    return pair[1](situation, intent)


_register_implemented()
