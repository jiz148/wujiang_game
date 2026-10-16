import json
import tempfile
import unittest
from pathlib import Path

from wujiang.tactical.rooms.imitation.hero_models import living_actor_key, split_records


class HeroModelSplitTests(unittest.TestCase):
    def test_plain_soldiers_stay_out_of_the_networks(self) -> None:
        from wujiang.tactical.rooms.imitation.hero_models import uses_soldier_logic
        self.assertTrue(uses_soldier_logic("普通步兵"))
        self.assertTrue(uses_soldier_logic("弓兵"))
        self.assertTrue(uses_soldier_logic("守备兵"))
        self.assertTrue(uses_soldier_logic("strategy_archer"))
        self.assertFalse(uses_soldier_logic("红衣"))
        self.assertEqual(living_actor_key({"situation": {"actor": {"hero_code": "excel_r120", "hp_ratio": 0}}}), "")

    def test_split_keeps_only_the_living_actor(self) -> None:
        rows = [
            {"situation": {"actor": {"hero_code": "excel_r120", "hp_ratio": 0.4}}, "verb": "move"},
            {"situation": {"actor": {"name": "盲人剑士", "hp_ratio": 1}}, "verb": "attack"},
            {"situation": {"actor": {"hero_code": "excel_r120", "hp_ratio": 0}}, "verb": "move"},
        ]
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "records.jsonl"
            source.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
            out = Path(folder) / "heroes"
            catalog = split_records(source, out)
            self.assertEqual(catalog["rows"], 2)
            self.assertEqual(catalog["counts"]["excel_r120"], 1)
            self.assertEqual(catalog["counts"]["盲人剑士"], 1)
            saved = (out / "excel_r120.jsonl").read_text(encoding="utf-8")
            self.assertIn("excel_r120", saved)
