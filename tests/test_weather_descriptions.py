from __future__ import annotations

import sys
import unittest
from pathlib import Path


SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wujiang.tactical.heroes.registry import create_battle, create_hero, list_heroes  # noqa: E402


class WeatherDescriptionTests(unittest.TestCase):
    def test_weather_effects_reach_roster_and_battle_details(self) -> None:
        roster = {hero["code"]: hero for hero in list_heroes()}
        for code, expected in {
            "undead_king_lina": "1/16",
            "rock_god": "回避距离-1",
            "excel_r027": "1/2概率失败",
            "excel_r028": "3/4生命",
            "excel_r036": "半破魔",
            "excel_r071": "不能移动",
            "excel_r187": "额外",
            "excel_r188": "魔上限+1",
            "excel_r337": "等级1",
        }.items():
            description = roster[code]["weather_effect_text"]
            if code == "excel_r187":
                self.assertIn("增加本全局回合1次普攻", description)
            else:
                self.assertIn(expected, description)

        battle = create_battle("rock_god", "excel_r188")
        public_units = {unit["hero_code"]: unit for unit in battle.to_public_dict()["units"]}
        self.assertIn("沙尘", public_units["rock_god"]["weather_effect_text"])
        self.assertIn("天使", public_units["excel_r188"]["weather_effect_text"])

    def test_weather_skill_tooltips_include_the_resulting_effect(self) -> None:
        for hero_code, skill_code, expected in (
            ("undead_king_lina", "wind_sand", "1/16生命"),
            ("excel_r071", "big_avalanche", "速+2"),
            ("excel_r187", "pandemonium", "攻+1"),
            ("excel_r188", "sky_sanctuary", "魔上限+1"),
            ("excel_r337", "wetland_grassland", "等级1"),
        ):
            hero = create_hero(hero_code, 1)
            self.assertIn(expected, hero.get_skill(skill_code).description)


if __name__ == "__main__":
    unittest.main()
