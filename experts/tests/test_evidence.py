"""证据闸门：切词、覆盖度、以及"什么时候该弃权"。

这些测试全是**手算得出来**的断言：每个用例的命中/漏掉都能在 `--trace` 里看到，
改阈值或改权重时，坏掉的是哪一条一目了然。
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from framework import evidence  # noqa: E402


def _load(folder: str, entry_id: str) -> dict:
    data = json.loads((ROOT / "packs" / folder / "pack.json").read_text(encoding="utf-8"))
    for entry in data["entries"]:
        if entry.get("id") == entry_id:
            return entry
    raise AssertionError(f"包 {folder} 里没有条目 {entry_id}")


def _gate(threshold: float = 0.40) -> evidence.EvidenceGate:
    cards = []
    for path in sorted((ROOT / "packs").glob("*/pack.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = tuple(e for e in data.get("entries", []) if isinstance(e, dict))
        cards.append(type("Card", (), {"entries": entries})())
    return evidence.EvidenceGate.from_cards(cards, threshold=threshold)


class TestContentTerms(unittest.TestCase):
    def test_cjk_gives_unigrams_and_bigrams(self) -> None:
        terms = evidence.content_terms("向量场")
        self.assertEqual(terms, ("向", "量", "场", "向量", "量场"))

    def test_stopwords_are_dropped(self) -> None:
        terms = evidence.content_terms("什么是向量场")
        self.assertNotIn("什么", terms)
        self.assertNotIn("是", terms)
        self.assertIn("向量", terms)

    def test_ascii_kept_whole(self) -> None:
        terms = evidence.content_terms("HTML 是什么")
        self.assertIn("html", terms)
        self.assertNotIn("h", terms)          # 不拆字母

    def test_conjunction_is_a_boundary(self) -> None:
        """"向量场和电场" 不该产出 "场和"/"和电" 这种跨连接词的二元组。"""
        terms = evidence.content_terms("向量场和电场")
        self.assertNotIn("场和", terms)
        self.assertNotIn("和电", terms)
        self.assertIn("向量", terms)
        self.assertIn("电场", terms)

    def test_normalisation_reuses_decomposer(self) -> None:
        """全角/繁简折叠与框架其余部分同口径（不新增第二份实现）。"""
        self.assertEqual(evidence.content_terms("電車"), evidence.content_terms("电车"))

    def test_bigrams_do_not_cross_function_words(self) -> None:
        """虚词是边界：`AI 模型的偏见是从哪来的` 不该产出 `型的 / 的偏 / 是从`。

        这些跨虚词的二元组在任何条目里都不可能命中，却被算成"漏掉的实义词"，
        把一条本来对得上的问题摊到阈值以下（1.0.3 实测：「AI 模型的偏见」0.27）。
        """
        terms = evidence.content_terms("AI 模型的偏见是从哪来的")
        for junk in ("型的", "的偏", "是从", "从哪", "哪来", "来的"):
            self.assertNotIn(junk, terms)
        self.assertIn("偏见", terms)
        self.assertIn("模型", terms)


class TestAssess(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.gate = _gate()

    def test_drift_case_is_blocked(self) -> None:
        """「什么是德摩根定律」不能拿热力学定律当答案 —— 这是 1.0.2 的真事故。"""
        verdict = self.gate.assess("什么是德摩根定律", _load("physics", "thermodynamics"))
        self.assertFalse(verdict.ok)
        self.assertLess(verdict.coverage, self.gate.threshold)

    def test_substring_drift_is_blocked(self) -> None:
        """「虚函数」里含「函数」：二元组 虚函/数表 都没命中，所以挡得住。"""
        verdict = self.gate.assess("C++ 的虚函数表怎么实现", _load("math", "function"))
        self.assertFalse(verdict.ok)

    def test_direct_question_passes(self) -> None:
        verdict = self.gate.assess("向量场是什么?", _load("math", "vector-field"))
        self.assertTrue(verdict.ok)
        self.assertAlmostEqual(verdict.coverage, 1.0, places=3)

    def test_addends_are_readable(self) -> None:
        verdict = self.gate.assess("什么是德摩根定律", _load("physics", "thermodynamics"))
        joined = " ".join(verdict.addends())
        self.assertIn("漏掉", joined)
        self.assertIn("命中", joined)

    def test_typo_of_standard_question_is_forgiven(self) -> None:
        """「引立波是什么」是「引力波是什么」的错写（同长度、只差一处）。

        注意这一条**本来就够覆盖度**（0.36）：豁免只在覆盖度不够时兜底，
        见下一条。这里同时锁住"认得出错写"和"认得出"两个结果。
        """
        entry = _load("physics", "gravitational-wave")
        self.assertIsNotNone(self.gate._as_typo_of_question("引立波是什么?", entry))
        verdict = self.gate.assess("引立波是什么?", entry)
        self.assertTrue(verdict.ok)

    def test_typo_forgiveness_does_not_leak(self) -> None:
        """错写豁免不能把德摩根放进来：它跟热力学定律差了三处。"""
        entry = _load("physics", "thermodynamics")
        self.assertIsNone(self.gate._as_typo_of_question("什么是德摩根定律", entry))

    def test_multi_topic_query_needs_both_halves(self) -> None:
        """「向量场和电场的关系」只覆盖了"电场"那一半 —— 不算答上。

        1.0.2 的判据是"分句取最高"，于是这个问题能拿电场的定义作答；同样的算法
        让「熵增和热寂是什么关系」「Docker 容器和虚拟机的区别」也过了闸门，
        答的是一个问关系的问题、给的却是单条定义。1.0.3 改成**每个分句都要有证据**：
        一半有、一半没有，就弃权（边界写在 README「边界」一节）。
        """
        verdict = self.gate.assess("向量场和电场的关系", _load("physics", "electric-field"))
        self.assertFalse(verdict.ok)
        self.assertIn("一半有证据", verdict.reason)
        # 两边都覆盖的条目照样通过：问题是关系，条目里也写着两样东西
        both = self.gate.assess("接口和抽象类有什么区别", _load("java", "interface-abstract"))
        self.assertTrue(both.ok)

    def test_single_char_clause_cannot_decide(self) -> None:
        """「熵和寿命有什么关系」不能被"熵"那一半算成 1.0：一个字不是证据。"""
        verdict = self.gate.assess("熵和寿命有什么关系", _load("physics", "entropy"))
        self.assertFalse(verdict.ok)

    def test_disabled_gate_lets_everything_through(self) -> None:
        gate = _gate(threshold=0.0)
        verdict = gate.assess("什么是德摩根定律", _load("physics", "thermodynamics"))
        self.assertTrue(verdict.ok)
        self.assertTrue(verdict.skipped)
        self.assertTrue(gate.disabled)

    def test_no_content_terms_skips_the_gate(self) -> None:
        """"还有吗" 这种整句虚词，闸门不该参与（交给上下文/规则路径）。"""
        verdict = self.gate.assess("还有吗", _load("math", "vector-field"))
        self.assertTrue(verdict.ok)
        self.assertTrue(verdict.skipped)

    def test_missing_entry_is_not_evidence(self) -> None:
        verdict = self.gate.assess("向量场是什么?", None)
        self.assertFalse(verdict.ok)


class TestSameWordDifferentSense(unittest.TestCase):
    """同一个词、另一个意思：闸门必须挡住（1.0.3 的同词异义测试集就量这一条）。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.gate = _gate(threshold=0.40)

    def test_not_about_cue_blocks_the_wrong_sense(self) -> None:
        """「USB 接口」不是 Java 的 interface：条目的 not_about 写明了这件事。"""
        entry = _load("java", "interface-abstract")
        verdict = self.gate.assess("USB 接口和 Type-C 接口有什么区别", entry)
        self.assertFalse(verdict.ok)
        self.assertIn("不适用词", verdict.reason)
        # 同一个条目的正常问法不受影响
        self.assertTrue(self.gate.assess("接口和抽象类有什么区别", entry).ok)

    def test_pack_level_cue_applies_to_every_entry(self) -> None:
        """包级 `not_about`（java 包的「java 岛」）并进了每条条目。"""
        from framework import catalog

        card = catalog.card_for("java")
        self.assertIsNotNone(card)
        verdict = self.gate.assess("Java 岛在哪儿", card.entries[0],
                                   folder="java", entry_id="?")
        self.assertFalse(verdict.ok)
        self.assertIn("不适用词", verdict.reason)

    def test_other_sense_questions_are_blocked(self) -> None:
        cases = [
            ("积分商城怎么兑换礼品", "math", "integral"),
            ("天气预报里的异常升温是什么原因", "java", "exception"),
            ("水管里的压力怎么测量", "emotion", "stress"),
            ("怎么证明我的居住地址", "math", "proof"),
            ("矢量图是什么格式", "math", "vector"),
        ]
        for question, folder, entry_id in cases:
            with self.subTest(question=question):
                verdict = self.gate.assess(question, _load(folder, entry_id))
                self.assertFalse(verdict.ok)

    def test_same_keyword_finds_the_other_entry(self) -> None:
        """同一个词，问的其实是别一条/另一个包：那条要能过闸门。"""
        cases = [
            ("浏览器是怎么把 DOM 树渲染成页面的", "web_dev", "browser-render"),
            ("超导的迈斯纳效应是什么", "physics", "superconductivity"),
            ("AI 模型的偏见是从哪来的", "ethics", "bias"),
            ("HashMap 的哈希冲突怎么处理", "java", "hashmap"),
            ("统计推断里的 p 值怎么解释", "math", "statistical-inference"),
        ]
        for question, folder, entry_id in cases:
            with self.subTest(question=question):
                verdict = self.gate.assess(question, _load(folder, entry_id))
                self.assertTrue(verdict.ok, f"{question} / {folder}/{entry_id} "
                                           f"cov={verdict.coverage:.2f} {verdict.reason}")


if __name__ == "__main__":
    unittest.main()
