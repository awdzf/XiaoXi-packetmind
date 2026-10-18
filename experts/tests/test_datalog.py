"""Datalog 子集与有界规划器的接线。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from framework.catalog import ExpertCard  # noqa: E402
from framework.datalog import DatalogEngine, parse_atom, parse_rule  # noqa: E402
from framework.planner import BoundedPlanner  # noqa: E402


class TestDatalogEngine(unittest.TestCase):
    def _kin(self) -> DatalogEngine:
        return DatalogEngine.from_spec({
            "facts": [
                "parent(alice, bob)",
                "parent(bob, carol)",
                "parent(carol, dave)",
            ],
            "rules": [
                "ancestor(?x, ?z) :- parent(?x, ?z).",
                "ancestor(?x, ?z) :- parent(?x, ?y), ancestor(?y, ?z).",
            ],
        })

    def test_recursive_rule_derives_transitive_closure(self) -> None:
        engine = self._kin()
        result = engine.query_first(["ancestor", "alice", "dave"])
        self.assertIsNotNone(result)
        self.assertIn("ancestor(alice, dave)", result.proof.atom.format())
        steps = engine.explain(result.proof)
        self.assertTrue(any("推出「ancestor(alice, dave)」" in step for step in steps))

    def test_variable_query_returns_bindings(self) -> None:
        engine = self._kin()
        results = engine.query(["ancestor", "alice", "?who"], limit=10)
        values = {item.get("?who") for item in results}
        self.assertIn("dave", values)
        self.assertIn("bob", values)

    def test_negation_over_settled_facts(self) -> None:
        engine = DatalogEngine.from_spec({
            "facts": ["fruit(apple)", "fruit(banana)", "red(apple)"],
            "rules": ["non_red(?x) :- fruit(?x), not red(?x)."],
        })
        self.assertIsNotNone(engine.query_first(["non_red", "banana"]))
        self.assertIsNone(engine.query_first(["non_red", "apple"]))

    def test_unsafe_rule_is_reported_not_crashing(self) -> None:
        engine = DatalogEngine.from_spec({
            "facts": [],
            "rules": ["bad(?x) :- good(?y)."],
        })
        self.assertTrue(any("规则头变量未在正体中出现" in item for item in engine.errors))
        self.assertEqual(engine.query(["bad", "x"]), [])

    def test_parse_helpers_accept_strings_and_arrays(self) -> None:
        self.assertEqual(parse_atom("parent(alice, bob)").args, ("alice", "bob"))
        rule = parse_rule("ancestor(?x, ?z) :- parent(?x, ?z).")
        self.assertEqual(rule.head.predicate, "ancestor")
        self.assertEqual(len(rule.body), 1)


class TestDatalogPlanner(unittest.TestCase):
    def _card(self) -> ExpertCard:
        return ExpertCard(
            folder="kin",
            pack_id="kin",
            name="亲属",
            tagline="",
            scope=("亲属",),
            entries=(),
            pack_path=Path("kin/pack.json"),
            datalog={
                "facts": ["parent(alice, bob)", "parent(bob, carol)"],
                "rules": [
                    "ancestor(?x, ?z) :- parent(?x, ?z).",
                    "ancestor(?x, ?z) :- parent(?x, ?y), ancestor(?y, ?z).",
                ],
            },
        )

    def test_relation_question_uses_recursive_datalog(self) -> None:
        planner = BoundedPlanner((self._card(),))
        result = planner.plan("alice和carol是什么关系", lambda _card, _text: None)
        self.assertIsNotNone(result)
        self.assertIn("ancestor", result.text)
        self.assertIn("alice", result.text)
        self.assertIn("carol", result.text)

    def test_membership_question_uses_datalog(self) -> None:
        card = ExpertCard(
            folder="food", pack_id="food", name="食物", tagline="",
            scope=("食物",), entries=(), pack_path=Path("food/pack.json"),
            datalog={
                "facts": ["是(测试果,水果)", "是(水果,食物)"],
                "rules": [
                    "是(?x,?z) :- 是(?x,?y), 是(?y,?z).",
                    "可以(?x,吃) :- 是(?x,食物).",
                ],
            },
        )
        result = BoundedPlanner((card,)).plan(
            "测试果可以吃吗", lambda _card, _text: None
        )
        self.assertIsNotNone(result)
        self.assertIn("测试果可以吃", result.text)
        self.assertEqual(result.kind, "datalog-membership")

    def test_no_derivation_falls_through(self) -> None:
        planner = BoundedPlanner((self._card(),))
        self.assertIsNone(planner.plan("alice和dave是什么关系", lambda _card, _text: None))


if __name__ == "__main__":
    unittest.main()
