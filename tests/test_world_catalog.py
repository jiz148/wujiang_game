from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wujiang.strategic import (  # noqa: E402
    advance_month,
    choose_player_hero_path,
    construct_city_building,
    first_campaign_contract,
    generate_random_world,
    true_campaign_contract,
    world_catalog_public,
)
from wujiang.strategic.catalog import (  # noqa: E402
    CATALOG_PATH,
    MAX_WORLD_CITIES,
    catalog_nations,
    default_true_scenario,
    resolve_world_city_budget,
)
from wujiang.strategic.rare_resources import (  # noqa: E402
    accept_resource_trade,
    apply_ai_trade_responses,
    faction_monthly_rare_income,
    faction_vein_counts,
    propose_resource_trade,
    quote_unit_price,
)


class WorldCatalogTests(unittest.TestCase):
    def test_catalog_file_is_utf8_and_readable(self) -> None:
        self.assertTrue(CATALOG_PATH.exists())
        catalog = world_catalog_public()
        self.assertEqual(catalog["path"], "wujiang_game/data/world_catalog.json")
        self.assertEqual(len(catalog["rare_resources"]), 6)
        names = {item["name"] for item in catalog["rare_resources"]}
        self.assertEqual(names, {"谷露", "玄铁", "祭晶", "星砂", "商珀", "炉心"})
        nations = catalog_nations()
        self.assertEqual([item["name"] for item in nations], ["第六天", "联合国", "电子国", "海之国", "EU"])
        self.assertGreaterEqual(sum(item["city_count"] for item in nations), 1)
        self.assertGreaterEqual(sum(item["hero_count"] for item in nations), 1)

    def test_true_campaign_follows_catalog_counts(self) -> None:
        nations = catalog_nations()
        scenario = default_true_scenario()
        world = generate_random_world(seed=7, campaign_contract=true_campaign_contract())
        majors = [faction for faction in world.factions if faction.is_major]
        self.assertEqual([faction.name for faction in majors], [item["name"] for item in nations])
        counts = {faction.name: sum(1 for city in world.cities if city.owner_faction_id == faction.faction_id) for faction in majors}
        owned = sum(item["city_count"] for item in nations)
        if owned < MAX_WORLD_CITIES:
            for nation in nations:
                self.assertEqual(counts[nation["name"]], nation["city_count"])
        self.assertEqual(len(world.cities), scenario["city_count"])
        self.assertLessEqual(len(world.cities), MAX_WORLD_CITIES)
        self.assertEqual(sum(1 for faction in world.factions if faction.is_neutral_city_state), scenario["neutral_city_state_count"])
        serving = {
            faction.faction_id: [hero.hero_code for hero in world.strategic_heroes if hero.faction_id == faction.faction_id and hero.office_id]
            for faction in majors
        }
        self.assertEqual(len(serving[majors[0].faction_id]), nations[0]["hero_count"])
        self.assertEqual(len(serving[majors[1].faction_id]), nations[1]["hero_count"])
        self.assertIn("doomlight_dragon", serving[majors[0].faction_id])
        self.assertIn("elite_soldier", serving[majors[1].faction_id])
        self.assertTrue(any(city.veins for city in world.cities if city.owner_faction_id == majors[0].faction_id))
        self.assertGreater(sum(faction_vein_counts(world, majors[0].faction_id).values()), 0)

    def test_random_campaign_still_fills_offices(self) -> None:
        world = generate_random_world(seed=42, city_count=6, faction_count=2)
        major = next(faction for faction in world.factions if faction.is_major)
        offices = [office for office in world.offices if office.faction_id == major.faction_id and office.status != "disabled"]
        filled = [office for office in offices if office.holder_id]
        self.assertEqual(len(filled), len(offices))
        self.assertTrue(any(city.veins for city in world.cities))

    def test_same_seed_random_worlds_still_match(self) -> None:
        first = generate_random_world(seed=42, campaign_contract=first_campaign_contract())
        second = generate_random_world(seed=42, campaign_contract=first_campaign_contract())
        self.assertEqual(first.to_dict(), second.to_dict())

    def test_veins_produce_rare_resources_each_month(self) -> None:
        world = generate_random_world(seed=3, campaign_contract=true_campaign_contract())
        faction = next(item for item in world.factions if item.name == "第六天")
        before = dict(faction.rare_resources)
        expected = faction_monthly_rare_income(world, faction.faction_id)
        next_world = advance_month(world)
        after = next(item for item in next_world.factions if item.faction_id == faction.faction_id).rare_resources
        for resource_id, amount in expected.items():
            self.assertEqual(int(after.get(resource_id, 0)), int(before.get(resource_id, 0)) + amount)

    def test_building_upgrade_spends_rare_resource(self) -> None:
        world = generate_random_world(seed=3, campaign_contract=true_campaign_contract())
        faction = next(item for item in world.factions if item.name == "第六天")
        city = next(city for city in world.cities if city.owner_faction_id == faction.faction_id)
        office = next(item for item in world.offices if item.faction_id == faction.faction_id and item.office_type == "lord")
        city.resources.money = 2000
        city.resources.food = 2000
        faction.rare_resources["dark_iron"] = 40
        before = int(faction.rare_resources["dark_iron"])
        next_world = construct_city_building(
            world,
            faction_id=faction.faction_id,
            city_id=city.city_id,
            building_id="barracks",
            issuer_office_id=office.office_id,
        )
        after_faction = next(item for item in next_world.factions if item.faction_id == faction.faction_id)
        after_city = next(item for item in next_world.cities if item.city_id == city.city_id)
        self.assertEqual(int(after_city.building_levels["barracks"]), int(city.building_levels.get("barracks", 0)) + 1)
        self.assertLess(int(after_faction.rare_resources["dark_iron"]), before)

    def test_trade_settles_in_money(self) -> None:
        world = generate_random_world(seed=3, campaign_contract=true_campaign_contract())
        seller = next(item for item in world.factions if item.name == "第六天")
        buyer = next(item for item in world.factions if item.name == "联合国")
        seller.rare_resources["dark_iron"] = 30
        buyer.resources.money = 800
        price = quote_unit_price(buyer, "dark_iron") * 5
        offered = propose_resource_trade(
            world,
            actor_faction_id=seller.faction_id,
            target_faction_id=buyer.faction_id,
            direction="sell",
            resource_id="dark_iron",
            amount=5,
            money=price,
        )
        offer = next(item for item in offered.trade_offers if item.status == "pending")
        settled = accept_resource_trade(
            offered,
            actor_faction_id=buyer.faction_id,
            offer_id=offer.offer_id,
        )
        after_seller = next(item for item in settled.factions if item.faction_id == seller.faction_id)
        after_buyer = next(item for item in settled.factions if item.faction_id == buyer.faction_id)
        self.assertEqual(int(after_seller.rare_resources["dark_iron"]), 25)
        self.assertEqual(int(after_buyer.rare_resources["dark_iron"]), int(buyer.rare_resources.get("dark_iron", 0)) + 5)
        self.assertEqual(after_seller.resources.money, seller.resources.money + price)
        self.assertEqual(after_buyer.resources.money, 800 - price)
        self.assertGreater(quote_unit_price(buyer, "dark_iron"), 0)

    def test_ai_replies_to_incoming_trade_same_month(self) -> None:
        world = generate_random_world(seed=3, campaign_contract=true_campaign_contract())
        seller = next(item for item in world.factions if item.name == "第六天")
        buyer = next(item for item in world.factions if item.name == "联合国")
        seller.rare_resources["dark_iron"] = 30
        buyer.resources.money = 800
        offered = propose_resource_trade(
            world,
            actor_faction_id=seller.faction_id,
            target_faction_id=buyer.faction_id,
            direction="sell",
            resource_id="dark_iron",
            amount=5,
            money=quote_unit_price(buyer, "dark_iron") * 5,
        )
        replied = apply_ai_trade_responses(offered, controlled_faction_ids={seller.faction_id})
        resolved = next(item for item in replied.trade_offers if item.proposer_faction_id == seller.faction_id)
        self.assertIn(resolved.status, {"accepted", "rejected"})
        self.assertTrue(any("贸易" in event.message for event in replied.event_log if event.category == "resource_trade"))

    def test_player_can_inherit_another_catalog_nation(self) -> None:
        world = generate_random_world(seed=3, campaign_contract=true_campaign_contract())
        hero = next(item for item in world.strategic_heroes if item.status == "roaming")
        eu = next(item for item in world.factions if item.nation_id == "eu")
        updated = choose_player_hero_path(
            world,
            user_id=7,
            hero_code=hero.hero_code,
            path="lord",
            assigned_faction_id="faction_1",
            target_faction_id=eu.faction_id,
            allow_reselect=True,
        )
        chosen = next(item for item in updated.strategic_heroes if item.hero_code == hero.hero_code)
        lord = next(
            item
            for item in updated.offices
            if item.faction_id == eu.faction_id and item.office_type == "lord"
        )
        self.assertEqual(chosen.faction_id, eu.faction_id)
        self.assertEqual(chosen.status, "serving")
        self.assertEqual(lord.holder_id, hero.hero_code)
        self.assertEqual(lord.controller_user_id, 7)
        self.assertEqual(next(item for item in updated.factions if item.faction_id == eu.faction_id).name, "EU")

    def test_inherited_nation_can_be_renamed(self) -> None:
        world = generate_random_world(seed=3, campaign_contract=true_campaign_contract())
        hero = next(item for item in world.strategic_heroes if item.status == "roaming")
        eu = next(item for item in world.factions if item.nation_id == "eu")
        updated = choose_player_hero_path(
            world,
            user_id=7,
            hero_code=hero.hero_code,
            path="lord",
            assigned_faction_id="faction_1",
            target_faction_id=eu.faction_id,
            faction_name="西欧盟",
            allow_reselect=True,
        )
        self.assertEqual(next(item for item in updated.factions if item.faction_id == eu.faction_id).name, "西欧盟")

    def test_founded_faction_uses_custom_name(self) -> None:
        world = generate_random_world(seed=3, campaign_contract=true_campaign_contract())
        hero = next(item for item in world.strategic_heroes if item.status == "roaming")
        updated = choose_player_hero_path(
            world,
            user_id=7,
            hero_code=hero.hero_code,
            path="found",
            assigned_faction_id="faction_1",
            faction_name="沧海盟",
            allow_reselect=True,
        )
        chosen = next(item for item in updated.strategic_heroes if item.hero_code == hero.hero_code)
        faction = next(item for item in updated.factions if item.faction_id == chosen.faction_id)
        self.assertEqual(faction.name, "沧海盟")

    def test_world_city_budget_caps_at_128(self) -> None:
        sizes, total, independents = resolve_world_city_budget([80, 80, 80, 80, 80], 500)
        self.assertEqual(total, MAX_WORLD_CITIES)
        self.assertEqual(sum(sizes) + independents, MAX_WORLD_CITIES)
        self.assertTrue(all(size >= 1 for size in sizes))
        contract = true_campaign_contract()
        for nation in contract["nations"]:
            nation["city_count"] = 80
        contract["city_count"] = 500
        world = generate_random_world(seed=1, campaign_contract=contract)
        self.assertEqual(len(world.cities), MAX_WORLD_CITIES)


if __name__ == "__main__":
    unittest.main()
