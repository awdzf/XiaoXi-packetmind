"""轻量检索:TF-IDF + 余弦（零依赖）。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from framework import CATALOG  # noqa: E402
from framework.catalog import ExpertCard  # noqa: E402
from framework.decomposer import Decomposer  # noqa: E402
from framework.router import Router  # noqa: E402
from framework.tfidf import MIN_COSINE, TfidfIndex, tokenize  # noqa: E402


class TestTokenizer(unittest.TestCase):
    def test_ascii_words_are_tokens(self) -> None:
        tokens = tokenize("HTML 的 JVM 是什么")
        self.assertIn("w:html", tokens)
        self.assertIn("w:jvm", tokens)

    def test_chinese_bigrams_are_tokens(self) -> None:
        tokens = tokenize("电场和磁场")
        self.assertIn("b:电场", tokens)
        self.assertIn("b:磁场", tokens)

    def test_function_crossing_bigrams_are_dropped(self) -> None:
        tokens = tokenize("电场是什么")
        self.assertNotIn("b:场是", tokens)
        self.assertNotIn("b:是什", tokens)

    def test_single_character_word_is_kept(self) -> None:
        self.assertIn("u:熵", tokenize("熵"))


class TestTfidfIndex(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.index = TfidfIndex(CATALOG)

    def test_exact_question_is_top(self) -> None:
        hits = self.index.search("向量场是什么?", limit=1)
        self.assertTrue(hits)
        self.assertEqual(hits[0].folder, "math")
        self.assertEqual(hits[0].entry_id, "vector-field")
        self.assertGreater(hits[0].score, 0.8)

    def test_paraphrase_is_still_top(self) -> None:
        hits = self.index.search("什么叫向量场", limit=1)
        self.assertTrue(hits)
        self.assertEqual(hits[0].entry_id, "vector-field")
        self.assertGreater(hits[0].score, MIN_COSINE)

    def test_offtopic_is_below_threshold(self) -> None:
        self.assertEqual(self.index.search("怎么给猫剪指甲"), [])
        self.assertEqual(self.index.search("番茄炒蛋要放多少盐"), [])

    def test_empty_query_returns_nothing(self) -> None:
        self.assertEqual(self.index.search("  "), [])


class TestSimilarityFallback(unittest.TestCase):
    """规则没命中时,TF-IDF 才补候选;规则命中时不动它。"""

    def _card(self) -> ExpertCard:
        entry = {
            "id": "decoherence",
            "question": "量子退相干是什么?",
            "aliases": [],
            "keywords": ["量子退相干", "退相干", "量子"],
            "answer": "量子退相干是相干性因与环境纠缠而衰减的过程。",
        }
        return ExpertCard(
            folder="demo", pack_id="demo", name="演示", tagline="",
            scope=("量子", "退相干"), entries=(entry,),
            pack_path=Path(ROOT / "tests" / ".tmp_sim" / "packs" / "demo" / "pack.json"),
            role="domain",
        )

    def _route(self, text: str):
        card = self._card()
        decomposition = Decomposer((card,)).decompose(text)
        return Router((card,)).route(decomposition)

    def test_similarity_adds_target_when_rule_misses(self) -> None:
        routing = self._route("相干性为什么会消失")
        self.assertTrue(routing.targets)
        self.assertEqual(routing.targets[0].via, "similarity")
        self.assertIn("TF-IDF", routing.notes)

    def test_similarity_does_not_answer_offtopic(self) -> None:
        self.assertEqual(self._route("怎么给猫剪指甲").targets, ())

    def test_rule_hit_skips_similarity(self) -> None:
        routing = self._route("量子退相干是什么?")
        self.assertTrue(routing.targets)
        self.assertEqual(routing.targets[0].via, "direct")
        self.assertNotIn("TF-IDF", routing.notes)


if __name__ == "__main__":
    unittest.main()
