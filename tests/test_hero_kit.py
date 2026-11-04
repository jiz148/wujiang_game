import unittest

from wujiang.tactical.heroes import excel_roster
from wujiang.tactical.heroes.flame_manager import ChimeraSkill, FireWallSkill, FlameManagerAttackCountTrait
from wujiang.tactical.heroes.hero_kit import FragmentMappedHeroKit, hero_kit_for
from wujiang.tactical.heroes.registry import create_hero


class HeroKitAssemblyTests(unittest.TestCase):
    def test_fragment_mapping_is_exact_and_creates_fresh_components(self):
        factories = {"专属技": FireWallSkill}
        kit = FragmentMappedHeroKit("example", skills=factories,
                                    traits={"完整特性文本": FlameManagerAttackCountTrait})
        factories.clear()
        first = kit.special_skill({"name": "专属技"})
        second = kit.special_skill({"name": "专属技"})
        self.assertEqual(first.code, "fire_wall")
        self.assertIsNot(first, second)
        self.assertIsNone(kit.special_skill({"name": "专属技附加文字"}))
        self.assertIsNot(kit.special_trait({"fragment": "完整特性文本"}),
                         kit.special_trait({"fragment": "完整特性文本"}))
        self.assertIsNone(kit.special_trait({"fragment": "完整特性文本附加文字"}))

    def test_fragment_override_and_extra_components_are_object_owned(self):
        kit = FragmentMappedHeroKit(
            "example", skills={"同名技": FireWallSkill},
            skill_fragments={"同名技的专属原文": ChimeraSkill},
            extra_skill_factories=(FireWallSkill,),
            extra_trait_factories=(FlameManagerAttackCountTrait,),
            attributes={"stat_minimums": {"attack": 0.0}},
        )
        self.assertIsInstance(kit.special_skill({"name": "同名技", "fragment": "同名技的专属原文"}), ChimeraSkill)
        self.assertIsInstance(kit.special_skill({"name": "同名技", "fragment": "其他原文"}), FireWallSkill)
        self.assertIsNot(kit.extra_skills()[0], kit.extra_skills()[0])
        self.assertIsNot(kit.extra_traits()[0], kit.extra_traits()[0])
        self.assertIsNot(kit.unit_attributes(), kit.unit_attributes())

    def test_all_public_excel_heroes_use_registered_object_assembly(self):
        for code in sorted(excel_roster.IMPLEMENTED_EXCEL_HERO_CODES):
            with self.subTest(code=code):
                self.assertIsNotNone(hero_kit_for(code))
                first = create_hero(code, 1)
                second = create_hero(code, 2)
                self.assertEqual([type(skill) for skill in first.skills],
                                 [type(skill) for skill in second.skills])
                self.assertTrue(all(component.owner is first for component in [*first.skills, *first.traits]))
                self.assertTrue(all(component.owner is second for component in [*second.skills, *second.traits]))

    def test_legendary_fisher_components_match_source_and_have_independent_state(self):
        first = create_hero("excel_r320", 1)
        second = create_hero("excel_r320", 2)

        self.assertEqual(
            [(skill.code, type(skill).__name__) for skill in first.skills],
            [
                ("gale_halberd", "GaleHalberdSkill"),
                ("fly_leap", "DashMoveSkill"),
                ("evasion", "PassiveEvasionSkill"),
                ("chain_pull", "ChainPullSkill"),
                ("fisher_stealth", "FisherStealthSkill"),
            ],
        )
        self.assertEqual(
            [type(trait).__name__ for trait in first.traits],
            ["AttackCountTrait", "AttackManaDrainTrait", "FisherGrowthTrait"],
        )
        self.assertTrue(all(component.owner is first for component in [*first.skills, *first.traits]))
        self.assertTrue(all(component.owner is second for component in [*second.skills, *second.traits]))

        first.get_skill("gale_halberd").uses_this_turn = 1
        first_growth = next(trait for trait in first.traits if type(trait).__name__ == "FisherGrowthTrait")
        second_growth = next(trait for trait in second.traits if type(trait).__name__ == "FisherGrowthTrait")
        first_growth.hero_kills = 2
        self.assertEqual(second.get_skill("gale_halberd").uses_this_turn, 0)
        self.assertEqual(second_growth.hero_kills, 0)

    def test_garden_demon_components_and_point_state_are_independent(self):
        first = create_hero("excel_r321", 1)
        second = create_hero("excel_r321", 2)

        self.assertEqual(
            [(skill.code, type(skill).__name__) for skill in first.skills],
            [
                ("harden", "HardenSkill"),
                ("protection", "PassiveProtectionSkill"),
                ("black_transformation", "BlackTransformationSkill"),
                ("black_night_shadow", "BlackNightShadowSkill"),
                ("enter_darkness", "EnterDarknessSkill"),
            ],
        )
        self.assertEqual(
            [(trait.name, type(trait).__name__) for trait in first.traits],
            [("苑魔食技", "GardenSkillFeastTrait")],
        )
        self.assertTrue(all(component.owner is first for component in [*first.skills, *first.traits]))
        self.assertTrue(all(component.owner is second for component in [*second.skills, *second.traits]))
        self.assertEqual((first.mana_points, second.mana_points), (3, 3))
        first.mana_points = 0
        first.get_skill("black_transformation").uses_this_battle = 1
        self.assertEqual(second.mana_points, 3)
        self.assertEqual(second.get_skill("black_transformation").uses_this_battle, 0)

    def test_flame_manager_kit_preserves_skill_order_and_per_unit_attack_count(self):
        first = create_hero("excel_r322", 1)
        second = create_hero("excel_r322", 2)
        self.assertEqual(
            [(skill.code, type(skill).__name__) for skill in first.skills],
            [
                ("fire_wall", "FireWallSkill"),
                ("chimera", "ChimeraSkill"),
                ("drain_mana", "DrainManaSkill"),
                ("flame_password", "FlamePasswordSkill"),
            ],
        )
        self.assertEqual([(trait.name, type(trait).__name__) for trait in first.traits],
                         [("火阵连击", "FlameManagerAttackCountTrait")])
        self.assertTrue(all(component.owner is first for component in [*first.skills, *first.traits]))
        self.assertTrue(all(component.owner is second for component in [*second.skills, *second.traits]))
        first.traits[0].bonus = 2
        first.get_skill("chimera").uses_this_turn = 1
        self.assertEqual(second.traits[0].bonus, 0)
        self.assertEqual(second.get_skill("chimera").uses_this_turn, 0)

    def test_wyvern_raider_kit_preserves_mount_and_independent_combat_state(self):
        first = create_hero("excel_r323", 1)
        second = create_hero("excel_r323", 2)
        self.assertEqual(
            [(skill.code, type(skill).__name__) for skill in first.skills],
            [
                ("wyvern", "WyvernSkill"),
                ("protection", "PassiveProtectionSkill"),
                ("chain_pull", "ChainPullSkill"),
                ("night_sky_party", "NightSkyPartySkill"),
            ],
        )
        self.assertEqual([type(trait).__name__ for trait in first.traits],
                         ["WyvernRiderCombatTrait", "AttackManaDrainTrait", "WyvernMountedStartTrait"])
        self.assertEqual((first.entry_footprint_width, first.entry_footprint_height), (1, 2))
        self.assertTrue(all(component.owner is first for component in [*first.skills, *first.traits]))
        self.assertTrue(all(component.owner is second for component in [*second.skills, *second.traits]))
        first.traits[0].attacked_ids.add("enemy")
        first.traits[0].attack_bonus = 2
        self.assertEqual(second.traits[0].attacked_ids, set())
        self.assertEqual(second.traits[0].attack_bonus, 0)

    def test_super_crow_kit_preserves_formation_order_and_independent_uses(self):
        first = create_hero("excel_r324", 1)
        second = create_hero("excel_r324", 2)
        self.assertEqual(
            [(skill.code, type(skill).__name__) for skill in first.skills],
            [
                ("light_wall", "LightWallSkill"),
                ("super_messenger_crow", "SuperCrowSummonSkill"),
                ("triangle_crow_array", "CrowFormationSkill"),
                ("square_crow_array", "CrowFormationSkill"),
                ("hexagram_crow_array", "CrowFormationSkill"),
                ("eight_dragon_crow_array", "CrowFormationSkill"),
            ],
        )
        self.assertEqual([type(trait).__name__ for trait in first.traits],
                         ["FlyingTrait", "SuperCrowAuraTrait", "SuperCrowFreeSkillTrait",
                          "SuperCrowReplenishmentTrait"])
        self.assertTrue(all(component.owner is first for component in [*first.skills, *first.traits]))
        self.assertTrue(all(component.owner is second for component in [*second.skills, *second.traits]))
        first.get_skill("super_messenger_crow").uses_this_turn = 3
        self.assertEqual(second.get_skill("super_messenger_crow").uses_this_turn, 0)

    def test_gentleman_kit_preserves_guard_and_independent_choice_budget(self):
        first = create_hero("excel_r325", 1)
        second = create_hero("excel_r325", 2)
        self.assertEqual([(skill.code, type(skill).__name__) for skill in first.skills],
                         [("evasion", "PassiveEvasionSkill"),
                          ("surprise_step", "GentlemanSurpriseStepSkill"),
                          ("gentleman_duel", "GentlemanDuelSkill")])
        self.assertEqual([type(trait).__name__ for trait in first.traits],
                         ["AttackCountTrait", "PassThroughMovementTrait",
                          "GentlemanCrossingCloneTrait", "GentlemanGuardTrait"])
        self.assertTrue(all(component.owner is first for component in [*first.skills, *first.traits]))
        self.assertTrue(all(component.owner is second for component in [*second.skills, *second.traits]))
        first.traits[-1].used = 2
        self.assertEqual(second.traits[-1].used, 0)

    def test_storm_spirit_kit_owns_locked_mana_and_independent_growth(self):
        first = create_hero("excel_r306", 1)
        second = create_hero("excel_r306", 2)
        self.assertEqual(first.base_stats.mana, 15)
        self.assertEqual((first.current_mana, second.current_mana), (15, 15))
        self.assertEqual([(skill.code, type(skill).__name__) for skill in first.skills],
                         [("electronic_storm", "ElectronicStormSkill"),
                          ("afterimage", "AfterimageSkill"),
                          ("lightning_chain", "LightningChainSkill")])
        self.assertEqual([type(trait).__name__ for trait in first.traits],
                         ["StormSpiritTrait", "PassThroughMovementTrait"])
        self.assertTrue(all(component.owner is first for component in [*first.skills, *first.traits]))
        self.assertTrue(all(component.owner is second for component in [*second.skills, *second.traits]))
        first.traits[0].destroyed_nearby = 2
        self.assertEqual(second.traits[0].destroyed_nearby, 0)

    def test_electronic_infinity_kit_wraps_each_own_skill_once(self):
        first = create_hero("excel_r373", 1)
        second = create_hero("excel_r373", 2)
        base_codes = ["shensu", "light_wall", "machine_gun", "missile", "ion_shield"]
        self.assertEqual([skill.code for skill in first.skills],
                         [*base_codes, *(f"infinity_instant__{code}" for code in base_codes)])
        self.assertEqual([type(trait).__name__ for trait in first.traits],
                         ["NaturalManaRecoveryTrait", "InstantConversionTrait", "ZeroManaOverdriveTrait"])
        self.assertTrue(all(component.owner is first for component in [*first.skills, *first.traits]))
        self.assertTrue(all(component.owner is second for component in [*second.skills, *second.traits]))
        self.assertTrue(all(wrapper.source is source
                            for source, wrapper in zip(first.skills[:5], first.skills[5:])))
        self.assertTrue(all(wrapper.source is source
                            for source, wrapper in zip(second.skills[:5], second.skills[5:])))
        self.assertIsNot(first.skills[0], second.skills[0])
