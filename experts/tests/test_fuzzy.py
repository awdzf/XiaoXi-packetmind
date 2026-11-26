"""模糊匹配层:编辑距离、预算、形近/同音、词典纠错、窗口近似。

跑法(在 experts/ 下):
    python -m unittest discover -s tests -t .
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from framework.fuzzy import (  # noqa: E402
    CONFUSABLE_PAIRS,
    Correction,
    correct_token,
    damerau_levenshtein,
    near_in,
    shape_like,
    similarity,
    typo_budget,
)


class TestDistance(unittest.TestCase):
    def test_identical_is_zero(self) -> None:
        self.assertEqual(damerau_levenshtein("向量场", "向量场"), 0)

    def test_substitution(self) -> None:
        self.assertEqual(damerau_levenshtein("向量场", "向量厂"), 1)

    def test_adjacent_transposition_counts_once(self) -> None:
        self.assertEqual(damerau_levenshtein("HTML", "HMTL"), 1)
        self.assertEqual(damerau_levenshtein("javascrpit", "javascript"), 1)

    def test_insert_and_delete(self) -> None:
        self.assertEqual(damerau_levenshtein("jvm", "jvmm"), 1)
        self.assertEqual(damerau_levenshtein("", "abc"), 3)
        self.assertEqual(damerau_levenshtein("abc", ""), 3)

    def test_distant_strings_exceed_budget(self) -> None:
        self.assertGreater(damerau_levenshtein("番茄炒蛋", "电车难题", 1), 1)
        # 预算只影响"提前返回",不影响"确实在预算内"的判断
        self.assertEqual(damerau_levenshtein("引力波", "引立波", 1), 1)

    def test_bounded_early_exit_returns_above_budget(self) -> None:
        # 返回值只保证"大于预算";调用方不该拿它当准确距离用
        self.assertGreater(damerau_levenshtein("abcdef", "uvwxyz", 1), 1)


class TestSimilarity(unittest.TestCase):
    def test_identical_is_one(self) -> None:
        self.assertEqual(similarity("量子", "量子"), 1.0)
        self.assertEqual(similarity("", ""), 1.0)

    def test_one_edit_out_of_four(self) -> None:
        self.assertAlmostEqual(similarity("向量场是", "向量厂是"), 0.75)

    def test_unrelated_is_low(self) -> None:
        self.assertLess(similarity("向量场", "番茄炒蛋"), 0.3)


class TestBudget(unittest.TestCase):
    def test_short_words_have_no_budget(self) -> None:
        self.assertEqual(typo_budget("熵"), 0)
        self.assertEqual(typo_budget("ab"), 0)
        self.assertEqual(typo_budget(""), 0)

    def test_ascii_budget_by_length(self) -> None:
        self.assertEqual(typo_budget("jvm"), 1)
        self.assertEqual(typo_budget("javascript"), 2)

    def test_cjk_budget_by_length(self) -> None:
        self.assertEqual(typo_budget("向量"), 1)
        self.assertEqual(typo_budget("量子退相干"), 1)

    def test_punctuation_does_not_buy_budget(self) -> None:
        # 标点/空格不占预算,长度按字算
        self.assertEqual(typo_budget("熵"), typo_budget("熵!"))


class TestShapeTable(unittest.TestCase):
    def test_pairs_are_distinct_single_chars(self) -> None:
        seen: set[frozenset[str]] = set()
        for left, right in CONFUSABLE_PAIRS:
            self.assertEqual(len(left), 1, f"{left!r} 不是单字")
            self.assertEqual(len(right), 1, f"{right!r} 不是单字")
            self.assertNotEqual(left, right, f"自反字对:{left!r}")
            key = frozenset((left, right))
            self.assertNotIn(key, seen, f"重复字对:{left}{right}")
            seen.add(key)

    def test_shape_is_symmetric(self) -> None:
        self.assertTrue(shape_like("己", "已"))
        self.assertTrue(shape_like("已", "己"))
        self.assertTrue(shape_like("场", "厂"))
        self.assertFalse(shape_like("场", "场"))
        self.assertFalse(shape_like("场", "们"))
        self.assertFalse(shape_like("电场", "电厂"))


class TestCorrectToken(unittest.TestCase):
    def test_unique_best_is_corrected(self) -> None:
        result = correct_token("向亮场", {"向量场", "电场"})
        self.assertEqual(result, Correction(term="向量场", distance=1))

    def test_known_word_is_not_touched(self) -> None:
        self.assertIsNone(correct_token("向量场", {"向量场"}))

    def test_ambiguity_is_refused(self) -> None:
        # 「向量厂」只差一个字就到「向量场」和「向量长」;两边一样近就不猜
        self.assertIsNone(correct_token("向量厂", {"向量场", "向量长"}))

    def test_two_char_words_need_related_evidence(self) -> None:
        vocabulary = {"共情"}
        # 没有同音/形近证据:「共晴」只是字面差一个字,不认
        self.assertIsNone(correct_token("共晴", vocabulary))
        # 有证据(由调用方给 related 判据)才认
        result = correct_token(
            "共晴", vocabulary, related=lambda a, b: (a, b) == ("晴", "情")
        )
        self.assertEqual(result, Correction(term="共情", distance=1))

    def test_cjk_correction_never_changes_length(self) -> None:
        # 「磁电场」删一个字能到「电场」也能到「磁场」—— 中文只认换字
        self.assertIsNone(correct_token("磁电场", {"电场", "磁场"}))

    def test_ascii_correction_allows_insert_and_delete(self) -> None:
        self.assertEqual(correct_token("jvmm", {"JVM"}), Correction(term="JVM", distance=1))
        self.assertEqual(correct_token("htlm", {"HTML"}), Correction(term="HTML", distance=1))

    def test_no_correction_across_scripts(self) -> None:
        self.assertIsNone(correct_token("htlm", {"向量场"}))

    def test_long_ascii_gets_two_edits_of_budget(self) -> None:
        self.assertEqual(
            correct_token("javascrpit", {"javascript"}),
            Correction(term="javascript", distance=1),
        )
        # 8 个字母以上才容 2 个错:6 个字母的 jvm 只容 1 个
        self.assertIsNone(correct_token("jvmmx", {"jvm"}))


class TestNearIn(unittest.TestCase):
    def test_ascii_typo_inside_sentence(self) -> None:
        found = near_in("html", "htlm是什么")
        self.assertEqual(found, ("htlm", 1))

    def test_ascii_word_boundary_is_respected(self) -> None:
        # 「java」不该在「javascript」里被当成精确命中;
        # 这里比的是整词,距离 5 超出预算
        self.assertIsNone(near_in("java", "javascript"))

    def test_cjk_window(self) -> None:
        found = near_in("向量场", "向亮场是什么")
        self.assertEqual(found, ("向亮场", 1))

    def test_cjk_short_term_needs_related_when_given(self) -> None:
        self.assertIsNone(near_in("电场", "磁场", related=lambda a, b: False))
        self.assertEqual(
            near_in("电场", "磁场", related=lambda a, b: True), ("磁场", 1)
        )

    def test_exact_match_reports_zero(self) -> None:
        self.assertEqual(near_in("向量场", "向量场是什么"), ("向量场", 0))

    def test_empty_inputs(self) -> None:
        self.assertIsNone(near_in("", "向量场"))
        self.assertIsNone(near_in("向量场", ""))


if __name__ == "__main__":
    unittest.main()
