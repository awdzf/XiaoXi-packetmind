"""用户教的公式：解析、求值、边界，以及"学会了没有"。

每个用例都能手算：`π*3^2 = 28.274334`，`(1/2)*4*3 = 6`，`max` 这种函数**不支持**也写在测试里 ——
边界是设计的一部分，不是遗漏。
"""

from __future__ import annotations

import json
import shutil
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from framework import GoChat, formula  # noqa: E402


class TestParseTeaching(unittest.TestCase):
    def test_basic_formula(self) -> None:
        parsed = formula.parse_teaching("圆的面积~π*r^2:r")
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.name, "圆的面积")
        self.assertEqual(parsed.expr, "π*r^2")
        self.assertEqual(parsed.vars, ("r",))

    def test_two_variables(self) -> None:
        parsed = formula.parse_teaching("圆柱体积~π*r^2*h:r,h")
        assert parsed is not None
        self.assertEqual(parsed.vars, ("r", "h"))

    def test_full_width_punctuation(self) -> None:
        parsed = formula.parse_teaching("三角形面积～0.5*底*高：底，高")
        # 中文变量名不支持，但全角符号本身要认出来
        self.assertIsNone(parsed)
        self.assertIn("英文字母", formula.explain_teaching("三角形面积～0.5*底*高：底，高") or "")

    def test_plain_qa_is_not_a_formula(self) -> None:
        """`#量子~薛定谔的猫是思想实验：把…` 用了中文冒号，但它是普通知识，不是公式。"""
        body = "量子退相干~薛定谔的猫是思想实验：把微观叠加态放大到一只猫身上"
        self.assertIsNone(formula.parse_teaching(body))
        self.assertFalse(formula.looks_like_formula(body))

    def test_equation_is_rejected_with_reason(self) -> None:
        body = "勾股~a^2+b^2=c^2:a,b"
        self.assertIsNone(formula.parse_teaching(body))
        self.assertTrue(formula.looks_like_formula(body))
        self.assertIn("等号", formula.explain_teaching(body) or "")

    def test_undeclared_variable_is_rejected(self) -> None:
        problem = formula.explain_teaching("动能~0.5*m*v^2:m")
        self.assertIsNotNone(problem)
        self.assertIn("v", problem or "")

    def test_unused_variable_is_rejected(self) -> None:
        self.assertIn("没用到", formula.explain_teaching("面积~r*h:r,h,z") or "")

    def test_variable_may_shadow_a_constant(self) -> None:
        """变量可以叫 `pi` —— 但它盖住了同名常数，教学时要提醒（不拒绝）。"""
        parsed = formula.parse_teaching("周长~2*pi*r:pi,r")
        self.assertIsNotNone(parsed)
        assert parsed is not None
        warnings = formula.teach_warnings(parsed)
        self.assertTrue(any("盖住" in note for note in warnings), warnings)

    def test_broken_syntax_is_rejected(self) -> None:
        self.assertIn("读不懂", formula.explain_teaching("面积~r**(:r") or "")

    def test_function_call_is_rejected_with_reason(self) -> None:
        problem = formula.explain_teaching("面积~sqrt(2):r")
        self.assertIn("函数调用", problem or "")

    def test_chinese_expression_is_rejected(self) -> None:
        problem = formula.explain_teaching("面积~根号(2):r")
        self.assertIn("不能出现中文", problem or "")

    def test_division_by_zero_at_one_is_still_a_valid_formula(self) -> None:
        """`1/(x-1)` 在 x=1 处没定义，但公式本身是合法的 —— 教学时不能拒。"""
        parsed = formula.parse_teaching("反比~1/(x-1):x")
        self.assertIsNotNone(parsed)
        assert parsed is not None
        result = formula.evaluate(parsed, ("1",))
        self.assertFalse(result.ok)
        self.assertIn("除以零", result.error)


class TestEvaluate(unittest.TestCase):
    def test_circle_area(self) -> None:
        parsed = formula.parse_teaching("圆的面积~π*r^2:r")
        assert parsed is not None
        result = formula.evaluate(parsed, ("3",))
        self.assertTrue(result.ok)
        self.assertAlmostEqual(result.value or 0, 3.141592653589793 * 9, places=9)
        self.assertEqual(result.substituted, "3.141592653589793*(3)**2")

    def test_caret_is_power(self) -> None:
        parsed = formula.parse_teaching("平方~x^2:x")
        assert parsed is not None
        self.assertEqual(formula.evaluate(parsed, ("7",)).value, 49)

    def test_multiple_variables_in_declared_order(self) -> None:
        parsed = formula.parse_teaching("圆柱体积~π*r^2*h:r,h")
        assert parsed is not None
        result = formula.evaluate(parsed, ("2", "5"))
        self.assertAlmostEqual(result.value or 0, 3.141592653589793 * 4 * 5, places=9)

    def test_arguments_can_be_expressions(self) -> None:
        parsed = formula.parse_teaching("面积~r^2:r")
        assert parsed is not None
        self.assertEqual(formula.evaluate(parsed, ("3/2",)).value, 2.25)

    def test_wrong_argument_count(self) -> None:
        parsed = formula.parse_teaching("圆柱体积~π*r^2*h:r,h")
        assert parsed is not None
        result = formula.evaluate(parsed, ("1",))
        self.assertFalse(result.ok)
        self.assertIn("2 个值", result.error)

    def test_non_numeric_argument(self) -> None:
        parsed = formula.parse_teaching("面积~r^2:r")
        assert parsed is not None
        result = formula.evaluate(parsed, ("abc",))
        self.assertFalse(result.ok)
        self.assertIn("不是一个能算的数", result.error)

    def test_division_by_zero_at_call_time(self) -> None:
        parsed = formula.parse_teaching("反比~1/x:x")
        assert parsed is not None
        result = formula.evaluate(parsed, ("0",))
        self.assertFalse(result.ok)
        self.assertIn("除以零", result.error)

    def test_substitution_is_full_precision(self) -> None:
        """π 必须按完整精度代入：截成 3.141593 会让 3^2 那次算错在第 6 位。"""
        parsed = formula.parse_teaching("圆的面积~π*r^2:r")
        assert parsed is not None
        result = formula.evaluate(parsed, ("3",))
        self.assertNotEqual(result.value, 3.141593 * 9)


class TestParseCall(unittest.TestCase):
    def test_basic(self) -> None:
        self.assertEqual(formula.parse_call("圆的面积:3"), ("圆的面积", ("3",)))

    def test_multiple_values(self) -> None:
        self.assertEqual(formula.parse_call("圆柱体积:2,5"), ("圆柱体积", ("2", "5")))

    def test_full_width_comma(self) -> None:
        self.assertEqual(formula.parse_call("圆柱体积：2，5"), ("圆柱体积", ("2", "5")))

    def test_command_prefix_is_not_a_call(self) -> None:
        self.assertIsNone(formula.parse_call(":ctx"))

    def test_plain_question_is_not_a_call(self) -> None:
        self.assertIsNone(formula.parse_call("向量场是什么?"))


class TestNormalization(unittest.TestCase):
    """中英文键盘混着打：符号要归一，且不能把上标压坏。"""

    def test_full_width_and_symbols(self) -> None:
        self.assertEqual(formula.normalize_math("２＊π×ｒ²"), "2*π*r^(2)")

    def test_division_and_minus_variants(self) -> None:
        self.assertEqual(formula.normalize_math("（ａ＋ｂ）÷2"), "(a+b)/2")
        self.assertEqual(formula.normalize_math("3−1"), "3-1")
        self.assertEqual(formula.normalize_math("a·b"), "a*b")

    def test_superscripts_become_power_before_nfkc(self) -> None:
        """NFKC 会把 ² 折成 2；必须在它之前把 `r²` 变成 `r^(2)`，否则就成了 r2。"""
        self.assertEqual(formula.normalize_math("r²"), "r^(2)")
        self.assertEqual(formula.normalize_math("10⁻³"), "10^(-3)")

    def test_normalize_is_idempotent(self) -> None:
        once = formula.normalize_math("２*π×ｒ²")
        self.assertEqual(formula.normalize_math(once), once)

    def test_mixed_teaching_parses(self) -> None:
        parsed = formula.parse_teaching("圆的面积～π×ｒ²：ｒ")
        assert parsed is not None
        self.assertEqual(parsed.name, "圆的面积")
        self.assertEqual(parsed.vars, ("r",))
        result = formula.evaluate(parsed, ("3",))
        self.assertAlmostEqual(result.value or 0, 3.141592653589793 * 9, places=9)

    def test_mixed_call_parses(self) -> None:
        self.assertEqual(formula.parse_call("圆的面积：３"), ("圆的面积", ("3",)))


class TestConstants(unittest.TestCase):
    def test_builtin_single_symbol_constants(self) -> None:
        for name in ("π", "pi", "φ", "phi", "ℏ", "hbar", "ε0", "eps0", "μ0", "mu0",
                     "k_B", "N_A", "m_e", "m_p", "q_e", "g_n", "e"):
            self.assertIn(name, formula.CONSTANTS, name)

    def test_ambiguous_single_letters_are_deliberately_absent(self) -> None:
        """c/G/h/g/R/F 不收：它们是最常见的变量名，收进来会静默算错。"""
        for name in ("c", "G", "h", "g", "R", "F"):
            self.assertNotIn(name, formula.CONSTANTS, name)

    def test_formula_using_builtin_constant(self) -> None:
        parsed = formula.parse_teaching("圆周长~2*pi*r:r")
        assert parsed is not None
        result = formula.evaluate(parsed, ("1",))
        self.assertAlmostEqual(result.value or 0, 6.283185307179586, places=9)
        self.assertIn("pi", result.used_constants)

    def test_argument_may_be_a_constant(self) -> None:
        parsed = formula.parse_teaching("面积~r^2:r")
        assert parsed is not None
        result = formula.evaluate(parsed, ("pi",))
        self.assertAlmostEqual(result.value or 0, 3.141592653589793 ** 2, places=9)

    def test_user_constant_parsing(self) -> None:
        parsed, problem = formula.parse_constant("c_light=2.998e8")
        self.assertEqual(problem, "")
        assert parsed is not None
        self.assertAlmostEqual(parsed.value, 2.998e8)

    def test_user_constant_accepts_arithmetic(self) -> None:
        parsed, problem = formula.parse_constant("half=1/2")
        self.assertEqual(problem, "")
        assert parsed is not None
        self.assertAlmostEqual(parsed.value, 0.5)

    def test_user_constant_rejects_chinese_name(self) -> None:
        parsed, problem = formula.parse_constant("光速=3e8")
        self.assertIsNone(parsed)
        self.assertIn("英文字母", problem)

    def test_user_constant_rejects_bad_value(self) -> None:
        parsed, problem = formula.parse_constant("x=abc")
        self.assertIsNone(parsed)
        self.assertIn("不是一个能算的数", problem)

    def test_user_constant_overrides_builtin(self) -> None:
        parsed = formula.parse_teaching("周长~2*pi*r:r", {"pi": 3.0})
        assert parsed is not None
        result = formula.evaluate(parsed, ("1",), {"pi": 3.0})
        self.assertAlmostEqual(result.value or 0, 6.0, places=9)

    def test_formula_can_use_user_constant(self) -> None:
        constants = {"c_light": 2.998e8}
        parsed = formula.parse_teaching("能量~m*c_light^2:m", constants)
        assert parsed is not None
        result = formula.evaluate(parsed, ("2",), constants)
        self.assertAlmostEqual(result.value or 0, 2 * (2.998e8) ** 2, places=3)

    def test_missing_constant_is_explained(self) -> None:
        problem = formula.explain_teaching("能量~m*c_light^2:m")
        self.assertIn("c_light", problem or "")


class TestLearn(unittest.TestCase):
    """端到端：教一次 → 算得出来 → 教第二次 → 覆盖旧的。"""

    def setUp(self) -> None:
        import tempfile

        self.tmp = Path("..").resolve() / "_tmp_formula_test"
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.copytree(ROOT / "packs", self.tmp / "packs")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _chat(self) -> GoChat:
        return GoChat(packs_root=self.tmp)

    def _teach(self, chat: GoChat, body: str) -> dict:
        parsed = formula.parse_teaching(body, chat.user_constants())
        assert parsed is not None, body
        entry, _created = chat.teach_formula(parsed, raw=f"# {body}")
        return entry

    def test_learn_once_use_many_times(self) -> None:
        chat = self._chat()
        entry = self._teach(chat, "圆的面积~π*r^2:r")
        self.assertEqual(entry["id"], entry["id"])          # 有 id 就是写进去了
        for value, expected in (("1", 3.141593), ("2", 12.566371), ("10", 314.159265)):
            result = chat.ask(f"圆的面积:{value}")
            self.assertIn(expected.__round__(4).__str__()[:5], result.summary)

    def test_teaching_survives_restart(self) -> None:
        """写进 pack.json 之后，换一个新进程（新 GoChat）还能算。"""
        self._teach(self._chat(), "圆的面积~π*r^2:r")
        fresh = self._chat()
        result = fresh.ask("圆的面积:3")
        self.assertIn("28.274334", result.summary)

    def test_reteaching_replaces_the_formula(self) -> None:
        chat = self._chat()
        self._teach(chat, "圆的面积~π*r^2:r")
        self._teach(chat, "圆的面积~3.14*r^2:r")
        self.assertIn("28.26", chat.ask("圆的面积:3").summary)

    def test_formula_does_not_disturb_retrieval(self) -> None:
        chat = self._chat()
        self._teach(chat, "圆的面积~π*r^2:r")
        normal = chat.ask("向量场是什么?").summary
        self.assertIn("向量场", normal)
        self.assertNotIn("圆的面积", normal.splitlines()[0])

    def test_unknown_name_falls_through(self) -> None:
        chat = self._chat()
        self._teach(chat, "圆的面积~π*r^2:r")
        # 没教过的名字 + 冒号：不该被当成公式，走原来的检索路径（多半是没接住）
        result = chat.ask("椭圆的面积:3")
        self.assertNotIn("28.27", result.summary)

    def test_taught_text_is_still_answerable_as_question(self) -> None:
        chat = self._chat()
        self._teach(chat, "圆的面积~π*r^2:r")
        self.assertIn("公式", chat.ask("圆的面积是什么").summary)

    def test_formula_is_retrievable_in_normal_conversation(self) -> None:
        """教过的公式要像其它教的知识一样，在正常对话里查得到。"""
        chat = self._chat()
        self._teach(chat, "圆的面积~π*r^2:r")
        for question in ("圆的面积公式是什么", "圆的面积怎么算", "圆的面积是什么"):
            summary = chat.ask(question).summary
            self.assertIn("π", summary, question)

    def test_constant_end_to_end(self) -> None:
        """自定义常数：教 → 写进记忆 → 公式里能用 → 正常对话里查得到。"""
        chat = self._chat()
        constant, problem = formula.parse_constant("c_light=2.998e8")
        self.assertEqual(problem, "")
        assert constant is not None
        chat.teach_constant(constant, raw="# c_light=2.998e8")
        self.assertAlmostEqual(chat.user_constants()["c_light"], 2.998e8)

        energy = formula.parse_teaching("能量~m*c_light^2:m", chat.user_constants())
        assert energy is not None
        chat.teach_formula(energy, raw="# 能量~m*c_light^2:m")
        summary = chat.ask("能量:2").summary
        self.assertIn("c_light", summary)
        self.assertIn("c_light", chat.ask("c_light是多少").summary)

    def test_mixed_symbols_end_to_end(self) -> None:
        """全角、×、上标混着打，教学和调用都要认。"""
        chat = self._chat()
        parsed = formula.parse_teaching("圆的面积～π×ｒ²：ｒ", chat.user_constants())
        assert parsed is not None
        chat.teach_formula(parsed, raw="# 圆的面积～π×ｒ²：ｒ")
        self.assertIn("28.274334", chat.ask("圆的面积：３").summary)

    def test_entry_carries_structured_fields(self) -> None:
        chat = self._chat()
        entry = self._teach(chat, "圆柱体积~π*r^2*h:r,h")
        raw = json.loads((self.tmp / "packs" / "memory" / "pack.json").read_text(encoding="utf-8"))
        stored = next(e for e in raw["entries"] if e["id"] == entry["id"])
        self.assertEqual(stored["formula"]["name"], "圆柱体积")
        self.assertEqual(stored["formula"]["vars"], ["r", "h"])
        self.assertEqual(stored["question"], "圆柱体积")


if __name__ == "__main__":
    unittest.main()
