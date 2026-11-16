"""Structural checks on the frontend module graph.

The rest of the frontend suite asserts against source text, which cannot catch a
broken import. These tests load the real module graph so that a missing export
or a cross-domain dependency fails here instead of in the browser.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
VERIFIER = ROOT / "tools" / "verify_frontend_modules.mjs"

sys.path.insert(0, str(ROOT / "tools"))
from verify_frontend_globals import scan as scan_undefined_identifiers  # noqa: E402

NODE = shutil.which("node")

# Neither game domain may reach into the other; anything they genuinely share
# belongs in core/ or is mediated by the backend.
FORBIDDEN_EDGES = [("tactical", "strategic"), ("strategic", "tactical")]


class FrontendModuleGraphTests(unittest.TestCase):
    @unittest.skipIf(NODE is None, "node is required to evaluate the placement prompt")
    def test_optional_placement_displays_self_and_ally_destinations(self) -> None:
        script = """
import { displayActions } from './static/tactical/vfx.js';
import { state } from './static/core/state.js';
import assert from 'node:assert/strict';
state.battle = { units: [{ id: 'actor', name: '行动者' }, { id: 'ally', name: '友军' }],
  pending_damage_choice: { kind: 'optional_placement', unit_id: 'actor',
    action_name: '选择落点', options: ['2,3', 'ally@4,5'] } };
let actions = displayActions();
assert.equal(actions.length, 3);
assert.equal(actions[0].target_unit_id, '2,3');
assert.deepEqual(actions[0].preview.cells, [{ x: 2, y: 3 }]);
assert.deepEqual(actions[0].preview.target_unit_ids, ['actor']);
assert.equal(actions[1].target_unit_id, 'ally@4,5');
assert.deepEqual(actions[1].preview.target_unit_ids, ['ally']);
state.battle.pending_damage_choice.kind = 'formation';
state.battle.pending_damage_choice.options = ['ally@4,5'];
actions = displayActions();
assert.equal(actions.length, 1);
assert.equal(actions[0].target_unit_id, 'ally@4,5');
"""
        result = subprocess.run(
            [NODE, "--input-type=module", "-e", script], cwd=ROOT,
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")

    @unittest.skipIf(NODE is None, "node is required to link-check the frontend modules")
    def test_module_graph_links_and_evaluates(self) -> None:
        result = subprocess.run(
            [NODE, str(VERIFIER)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")
        self.assertIn("LINK OK", result.stdout)

    def test_tactical_and_strategic_modules_do_not_import_each_other(self) -> None:
        violations: list[str] = []
        for domain, forbidden in FORBIDDEN_EDGES:
            for module in sorted((STATIC / domain).glob("*.js")):
                source = module.read_text(encoding="utf-8")
                for specifier in re.findall(r"^import .*? from '([^']+)';", source, re.M):
                    if f"/{forbidden}/" in specifier or specifier.startswith(f"./{forbidden}/"):
                        violations.append(f"{domain}/{module.name} imports {specifier}")
        self.assertEqual(violations, [], msg="the two game domains must stay independent")

    def test_every_shipped_module_is_reachable_from_the_entry_point(self) -> None:
        reachable: set[Path] = set()

        def walk(module: Path) -> None:
            module = module.resolve()
            if module in reachable or not module.exists():
                return
            reachable.add(module)
            source = module.read_text(encoding="utf-8")
            for specifier in re.findall(r"^import .*? from '([^']+)';", source, re.M):
                walk(module.parent / specifier)

        walk(STATIC / "app.js")
        # Classic scripts loaded directly by index.html are not part of the graph.
        classic = {"home-ui.js", "replay-ui.js", "battle-feedback.js", "analytics.js"}
        shipped = {
            path.resolve()
            for path in STATIC.rglob("*.js")
            if path.name not in classic
        }
        self.assertEqual(shipped - reachable, set(), msg="dead frontend module found")

    def test_no_module_reads_an_identifier_it_never_imports(self) -> None:
        """Catch the failure the link check structurally cannot see.

        A name that was module-local before the split has no import to resolve,
        so the graph links cleanly and the browser only throws once it executes
        that line. Splitting `app.js` left 13 modules calling `$` this way.
        """
        findings = scan_undefined_identifiers()
        detail = "; ".join(f"{module}: {', '.join(sorted(names))}" for module, names in findings.items())
        self.assertEqual(findings, {}, msg=f"undefined identifiers would throw at runtime -> {detail}")


if __name__ == "__main__":
    unittest.main()
