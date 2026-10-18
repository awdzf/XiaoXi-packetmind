"""有界规划器 + 结构化知识（事实/规则/关系）。"""

from __future__ import annotations

import json
import shutil
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from framework import GoChat, persona  # noqa: E402
from framework.catalog import ExpertCard  # noqa: E402
from framework.planner import BoundedPlanner  # noqa: E402


def _card(facts=(), rules=(), relations=(), entries=()) -> ExpertCard:
    return ExpertCard(
        folder="demo", pack_id="demo", name="演示", tagline="",
        scope=("测试果",), entries=tuple(entries),
        pack_path=Path(ROOT / "tests" / ".tmp_structured" / "packs" / "demo" / "pack.json"),
        role="domain", facts=tuple(facts), rules=tuple(rules), relations=tuple(relations),
    )


class TestBoundedPlanner(unittest.TestCase):
    def test_membership_uses_proof_chain(self) -> None:
        card = _card(facts=[
            {"predicate": "是", "args": ["测试果", "水果"]},
            {"predicate": "是", "args": ["水果", "食物"]},
            {"predicate": "能", "args": ["食物", "吃"]},
        ])
        planner = BoundedPlanner((card,))

        def ask(_card, _question):
            return {"logic": {
                "outcome": "answer", "answer": True,
                "text": "能(测试果, 吃) => 成立",
                "basis": [
                    "查询:能(测试果, 吃)",
                    "能(测试果, 吃)  <-(继承·ability上推)<- 是(测试果, 水果)、能(水果, 吃)",
                ],
            }}

        plan = planner.plan("测试果可以吃吗", ask)
        self.assertIsNotNone(plan)
        self.assertEqual(plan.kind, "membership")
        self.assertIn("测试果可以吃", plan.text)
        self.assertIn("继承·ability上推", plan.text)
        self.assertIn("来自数据包 demo", plan.text)

    def test_false_is_not_treated_as_answer(self) -> None:
        card = _card(facts=[{"predicate": "是", "args": ["测试果", "水果"]}])
        planner = BoundedPlanner((card,))
        plan = planner.plan(
            "测试果是蔬菜吗",
            lambda _card, _question: {"logic": {"outcome": "answer", "answer": False}},
        )
        self.assertIsNone(plan)

    def test_relation_and_attribute(self) -> None:
        card = _card(
            facts=[
                {"predicate": "是", "args": ["测试果", "水果"]},
                {"predicate": "储存", "args": ["测试果", "常温"]},
            ],
            relations=[{"type": "属于", "from": "测试果", "to": "水果"}],
        )
        planner = BoundedPlanner((card,))
        relation = planner.plan("测试果和水果是什么关系", lambda _c, _q: None)
        self.assertIsNotNone(relation)
        self.assertIn("测试果属于水果", relation.text)
        attribute = planner.plan("测试果的储存是什么", lambda _c, _q: None)
        self.assertIsNotNone(attribute)
        self.assertIn("常温", attribute.text)

    def test_no_facts_returns_none(self) -> None:
        self.assertIsNone(BoundedPlanner((_card(),)).plan("测试果可以吃吗", lambda _c, _q: None))


class TestStructuredPack(unittest.TestCase):
    def setUp(self) -> None:
        self.root = ROOT / "tests" / ".tmp_structured"
        shutil.rmtree(self.root, ignore_errors=True)
        target = self.root / "packs" / "demo"
        target.mkdir(parents=True)
        pack = {
            "expert": {"id": "demo", "name": "结构测试", "scope": ["测试果"], "tagline": ""},
            "entries": [{
                "id": "test-fruit-what", "question": "测试果是什么?",
                "aliases": [], "keywords": ["测试果"], "answer": "测试果是一种水果。",
            }],
            "facts": [
                {"predicate": "是", "args": ["测试果", "水果"]},
                {"predicate": "是", "args": ["水果", "食物"]},
                {"predicate": "能", "args": ["食物", "吃"]},
                {"predicate": "储存", "args": ["测试果", "常温"]},
            ],
            "relations": [{"type": "属于", "from": "测试果", "to": "水果"}],
            "safety": {"refuse": [], "caution": []},
        }
        (target / "pack.json").write_text(
            json.dumps(pack, ensure_ascii=False), encoding="utf-8",
        )
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_planner_answers_from_pack_facts(self) -> None:
        chat = GoChat(packs_root=self.root)
        result = chat.ask("测试果可以吃吗")
        self.assertIn("结论", result.summary)
        self.assertIn("测试果可以吃", result.summary)
        self.assertEqual(result.answers[0].source, "planner")

    def test_planner_works_in_subprocess_mode(self) -> None:
        chat = GoChat(packs_root=self.root, engine="subprocess")
        result = chat.ask("测试果可以吃吗")
        self.assertIn("测试果可以吃", result.summary)
        self.assertEqual(result.answers[0].source, "planner")

    def test_relation_and_attribute(self) -> None:
        chat = GoChat(packs_root=self.root)
        self.assertIn("测试果属于水果", chat.ask("测试果和水果是什么关系").summary)
        self.assertIn("常温", chat.ask("测试果的储存是什么").summary)

    def test_direct_membership(self) -> None:
        chat = GoChat(packs_root=self.root)
        result = chat.ask("测试果是水果吗")
        self.assertIn("测试果是水果", result.summary)


class TestTeachableQuestions(unittest.TestCase):
    def test_definition_question_is_specific(self) -> None:
        question = persona.teach_question("蓝鲸是什么?")
        self.assertIn("蓝鲸", question)
        self.assertIn("是什么", question)

    def test_howto_question_is_specific(self) -> None:
        question = persona.teach_question("怎么给猫剪指甲")
        self.assertIn("给猫剪指甲", question)
        self.assertIn("分哪几步", question)

    def test_relation_question_is_specific(self) -> None:
        question = persona.teach_question("向量场和电场有什么区别")
        self.assertIn("向量场", question)
        self.assertIn("电场", question)
        self.assertIn("区别/关系", question)

    def test_no_hit_no_longer_says_generic_invite(self) -> None:
        summary = GoChat().ask("怎么给仓鼠洗澡").summary
        self.assertIn("分哪几步", summary)
        self.assertIn("# 问题~答案", summary)
        self.assertNotIn("能不能教教我", summary)


class TestStructuredValidation(unittest.TestCase):
    def test_facts_only_pack_is_valid(self) -> None:
        from smallai.domain import ExpertPack

        raw = {
            "expert": {"id": "facts", "name": "事实包", "scope": []},
            "entries": [],
            "facts": [{"predicate": "是", "args": ["测试果", "水果"]}],
        }
        pack = ExpertPack(raw, Path("facts.json"))
        self.assertEqual(pack.validate(), [])

    def test_bad_structured_entries_are_reported(self) -> None:
        from smallai.domain import ExpertPack

        raw = {
            "expert": {"id": "bad", "name": "坏结构", "scope": ["测试"]},
            "entries": [],
            "facts": [{"predicate": "", "args": []}],
            "rules": [{"name": "缺条件", "then": []}],
            "relations": [{"type": "属于", "from": "A"}],
        }
        problems = ExpertPack(raw, Path("bad.json")).validate()
        self.assertTrue(any("事实" in item for item in problems))
        self.assertTrue(any("规则" in item for item in problems))
        self.assertTrue(any("关系" in item for item in problems))


if __name__ == "__main__":
    unittest.main()
