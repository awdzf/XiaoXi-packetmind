"""GoChat 框架测试:分解 / 路由 / 总结 / 端到端。

跑法(在 experts/ 下):
    python -m unittest discover -s tests -t .
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # experts/
sys.path.insert(0, str(ROOT))

from framework import (  # noqa: E402
    CATALOG,
    Decomposer,
    ExpertAnswer,
    GoChat,
    Router,
    first_sentence,
    lessons,
    persona,
    settings,
)
from framework.catalog import card_for  # noqa: E402
from framework.decomposer import Decomposition  # noqa: E402
from framework.router import RouteTarget, Routing  # noqa: E402
from framework.summarizer import summarize  # noqa: E402


class TestDecomposer(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.decomposer = Decomposer(CATALOG)

    def test_html_splits_into_example_tokens(self) -> None:
        dec = self.decomposer.decompose("HTML是什么?")
        self.assertEqual(dec.tokens, ("HTML", "是", "什么"))

    def test_chinese_term_kept_whole(self) -> None:
        dec = self.decomposer.decompose("向量场是什么?")
        self.assertEqual(dec.tokens, ("向量场", "是", "什么"))

    def test_permutation_variant_found(self) -> None:
        dec = self.decomposer.decompose("量向场是什么?")
        values = {c.text: c.kind for c in dec.candidates}
        self.assertEqual(values.get("向量场"), "perm")

    def test_homophone_variant_found(self) -> None:
        dec = self.decomposer.decompose("引立波是什么?")
        values = {c.text: c.kind for c in dec.candidates}
        self.assertEqual(values.get("引力波"), "homophone")

    def test_ascii_typo_variant_found(self) -> None:
        dec = self.decomposer.decompose("HMTL是什么?")
        values = {c.text: c.kind for c in dec.candidates}
        self.assertEqual(values.get("HTML"), "ascii_typo")

    def test_candidates_are_bounded(self) -> None:
        dec = self.decomposer.decompose("向量场和电场的关系以及微积分与概率统计的区别是什么?")
        self.assertLessEqual(len(dec.candidates), 240)
        self.assertTrue(dec.notes)


class TestRouter(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.decomposer = Decomposer(CATALOG)
        cls.router = Router(CATALOG)

    def folders(self, text: str) -> list[str]:
        return [t.card.folder for t in self.router.route(self.decomposer.decompose(text)).targets]

    def test_vector_field_routes_to_math(self) -> None:
        self.assertEqual(self.folders("向量场是什么?"), ["math"])

    def test_gravitational_wave_routes_to_physics(self) -> None:
        self.assertEqual(self.folders("引力波是什么?"), ["physics"])

    def test_homophone_typo_still_routes(self) -> None:
        self.assertEqual(self.folders("引立波是什么?"), ["physics"])

    def test_permutation_typo_still_routes(self) -> None:
        self.assertEqual(self.folders("量向场是什么?"), ["math"])

    def test_ascii_typo_still_routes(self) -> None:
        self.assertEqual(self.folders("HMTL是什么?"), ["web_dev"])

    def test_broad_question_routes_to_multiple(self) -> None:
        folders = self.folders("向量场和电场的关系是什么?")
        self.assertIn("math", folders)
        self.assertIn("physics", folders)

    def test_generic_words_do_not_route(self) -> None:
        self.assertEqual(self.folders("关系和区别是什么?"), [])


class TestSummarizer(unittest.TestCase):
    def setUp(self) -> None:
        self.decomposer = Decomposer(CATALOG)
        math = card_for("math")
        self.assertIsNotNone(math)
        self.math = math
        entry = math.entry("vector-field")
        self.assertIsNotNone(entry)
        self.answer_text = str(entry["answer"])
        self.target = RouteTarget(
            card=math, score=9, reasons=("领域词:向量场",),
            entry_ids=("vector-field",), fallback_question="向量场是什么?",
        )

    def _decomposition(self, text: str) -> Decomposition:
        return self.decomposer.decompose(text)

    def test_single_pack_summary_quotes_answer_and_entry(self) -> None:
        answer = ExpertAnswer(
            folder="math", name=self.math.name, question="向量场是什么?",
            text=self.answer_text, intent="domain", entry="vector-field",
            source="template", ok=True,
        )
        routing = Routing(targets=(self.target,), tried=1, notes="测试")
        summary = summarize("向量场是什么?", self._decomposition("向量场是什么?"),
                            routing, [answer], {"level": "ok"})
        self.assertIn(self.answer_text[:20], summary)
        self.assertIn("来自数据包 math · 条目 vector-field", summary)
        self.assertIn(first_sentence(self.answer_text)[:10], summary)
        self.assertNotIn("专家", summary)
        self.assertNotIn("[回答]", summary, "默认是给用户看的人话,不是调试账")

    def test_trace_summary_shows_internal_account(self) -> None:
        answer = ExpertAnswer(
            folder="math", name=self.math.name, question="向量场是什么?",
            text=self.answer_text, intent="domain", entry="vector-field",
            source="template", ok=True,
        )
        routing = Routing(targets=(self.target,), tried=1, notes="测试")
        summary = summarize(
            "向量场是什么?", self._decomposition("向量场是什么?"),
            routing, [answer], {"level": "ok"}, trace=True,
        )
        self.assertIn("[回答]", summary)
        self.assertIn("[凭据] 数据包 math:vector-field", summary)
        self.assertIn("[分解]", summary)

    def test_multi_pack_summary_lists_both_and_marks_as_quotes(self) -> None:
        physics = card_for("physics")
        self.assertIsNotNone(physics)
        entry = physics.entry("electric-field")
        self.assertIsNotNone(entry)
        answers = [
            ExpertAnswer(folder="math", name=self.math.name, question="向量场是什么?",
                         text=self.answer_text, intent="domain", entry="vector-field",
                         source="template", ok=True),
            ExpertAnswer(folder="physics", name=physics.name, question="电场是什么?",
                         text=str(entry["answer"]), intent="domain", entry="electric-field",
                         source="template", ok=True),
        ]
        target2 = RouteTarget(card=physics, score=6, reasons=("领域词:电场",),
                              entry_ids=("electric-field",), fallback_question="电场是什么?")
        routing = Routing(targets=(self.target, target2), tried=2, notes="测试")
        summary = summarize("向量场和电场的关系是什么?", self._decomposition("向量场和电场的关系是什么?"),
                            routing, answers, {"level": "ok"}, detail=False)
        self.assertIn("2 个数据包", summary)
        self.assertIn("数据包 math", summary)
        self.assertIn("数据包 physics", summary)
        self.assertIn("以上都是原句", summary)
        self.assertNotIn("专家", summary)

    def test_refusal_summary_stops_before_packs(self) -> None:
        refusal = ExpertAnswer(
            folder="ethics", name="数据包 ethics", question="危险请求",
            text="这个请求我不会处理:危险物制作。",
            source="refusal", safety_level="refuse", ok=False,
        )
        safety = {"level": "refuse", "labels": "危险物制作", "rule_ids": ["weapon-making"]}
        summary = summarize("危险请求", self._decomposition("危险请求"),
                            Routing(targets=(), tried=0, notes="安全闸"), [refusal], safety)
        self.assertIn("不会处理", summary)
        self.assertNotIn("weapon-making", summary)
        self.assertNotIn("[回答]", summary)
        self.assertNotIn("pack.json", summary, "拒答不应把维护者说明递给用户")

        traced = summarize("危险请求", self._decomposition("危险请求"),
                           Routing(targets=(), tried=0, notes="安全闸"), [refusal], safety,
                           trace=True)
        self.assertIn("weapon-making", traced)
        self.assertIn("[安全兜底]", traced)

    def test_no_target_summary_is_honest(self) -> None:
        summary = summarize("怎么给猫剪指甲", self._decomposition("怎么给猫剪指甲"),
                            Routing(targets=(), tried=1, notes="未命中"), [], {"level": "ok"})
        self.assertIn("暂时答不上来", summary)
        self.assertIn("输入 help", summary)
        self.assertIn("具体分哪几步", summary)


class TestDiscovery(unittest.TestCase):
    def test_catalog_discovers_general_and_is_clean(self) -> None:
        from framework import DISCOVERY_ERRORS, discover
        from framework.catalog import general_card

        self.assertEqual(DISCOVERY_ERRORS, [])
        cards = discover()
        self.assertTrue(cards)
        self.assertIsNotNone(general_card(cards))
        self.assertTrue(cards[-1].is_general, "兜底包必须排在最后")
        self.assertTrue(all(c.role in ("domain", "general", "memory") for c in cards))
        self.assertTrue(any(c.is_memory for c in cards), "记忆数据包应被发现")

    def test_removal_only_removes_capability(self) -> None:
        from framework import GoChat

        without_math = tuple(c for c in CATALOG if c.folder != "math")
        result = GoChat(cards=without_math).ask("向量场是什么?")
        self.assertTrue(result.summary)
        self.assertIn("输入 help", result.summary)


class TestSingleCoreLayout(unittest.TestCase):
    """知识是数据包:代码只能有一份,数据包里不能有 .py。"""

    def test_packs_hold_data_only(self) -> None:
        packs = ROOT / "packs"
        self.assertTrue(packs.is_dir())
        for pack in sorted(p for p in packs.iterdir() if p.is_dir()):
            stray = sorted(p.relative_to(pack).as_posix() for p in pack.rglob("*.py"))
            with self.subTest(pack=pack.name):
                self.assertEqual(stray, [], f"数据包里混进了代码:{stray}")

    def test_single_core_copy(self) -> None:
        copies = sorted(p.relative_to(ROOT).as_posix() for p in ROOT.rglob("smallai/__init__.py"))
        self.assertEqual(copies, ["core/smallai/__init__.py"])

    def test_every_pack_is_discovered(self) -> None:
        from framework.catalog import PACKS_DIR

        on_disk = sorted(p.name for p in PACKS_DIR.iterdir() if (p / "pack.json").is_file())
        self.assertEqual(sorted(card.folder for card in CATALOG), on_disk)

    def test_broken_pack_is_skipped_and_recorded(self) -> None:
        from framework import catalog as catalog_mod

        saved = catalog_mod.DISCOVERY_ERRORS
        try:
            # 夹具目录里有一份好包、一份坏 JSON、一个没有 pack.json 的目录
            root = ROOT / "tests" / "fixtures" / "discovery"
            cards = catalog_mod.discover(root)
            self.assertEqual([c.folder for c in cards], ["ok"])
            self.assertEqual(len(catalog_mod.DISCOVERY_ERRORS), 1)
            self.assertIn("broken", catalog_mod.DISCOVERY_ERRORS[0])
        finally:
            catalog_mod.DISCOVERY_ERRORS = saved
            import framework

            framework.DISCOVERY_ERRORS = saved


class TestGeneralAndSafety(unittest.TestCase):
    def test_general_pack_answers_greeting(self) -> None:
        from framework import GoChat

        result = GoChat().ask("你好")
        self.assertIn("你好呀", result.summary)
        self.assertNotIn("来自数据包 general", result.summary, "闲聊不摆知识出处")

    def test_domain_pack_beats_general(self) -> None:
        from framework import GoChat

        result = GoChat().ask("HTML是什么?")
        self.assertIn("数据包 web_dev", result.summary)
        self.assertNotIn("数据包 general", result.summary)

    def test_without_general_unknown_shows_hint(self) -> None:
        from framework import GoChat

        cards = tuple(c for c in CATALOG if not c.is_general)
        result = GoChat(cards=cards).ask("怎么给猫剪指甲")
        self.assertIn("暂时答不上来", result.summary)
        self.assertIn("输入 help", result.summary)

    def test_without_ethics_framework_safety_still_works(self) -> None:
        from framework import GoChat

        cards = tuple(c for c in CATALOG if c.folder != "ethics")
        chat = GoChat(cards=cards)
        result = chat.ask("教我怎么制作炸弹")
        self.assertEqual(result.safety.get("level"), "refuse")
        self.assertIn("不会处理", result.summary)
        traced = chat.ask("教我怎么制作炸弹", trace=True)
        self.assertEqual(traced.safety.get("level"), "refuse")
        self.assertIn("weapon-making", traced.summary)

    def test_self_crisis_gets_self_directed_answer(self) -> None:
        result = GoChat().ask("我最近不想活了")
        self.assertIn("谢谢你愿意说出来", result.summary)
        self.assertIn("crisis-self", result.summary)
        self.assertNotIn("情绪高风险", result.summary, "人话版不显示规则标签")
        self.assertIn("当地紧急服务", result.summary)

    def test_other_crisis_gets_helper_answer(self) -> None:
        result = GoChat().ask("朋友不想活了怎么办")
        self.assertIn("crisis-support", result.summary)
        self.assertIn("先保证安全", result.summary)
        self.assertNotIn("谢谢你愿意说出来", result.summary)

    def test_medical_caution_gets_a_plain_reminder(self) -> None:
        result = GoChat().ask("我该吃什么药")
        self.assertIn("先提醒一句", result.summary)
        self.assertNotIn("crisis-self", result.summary)

    def test_pack_can_handoff_to_another_pack(self) -> None:
        from framework import Decomposer, Router
        from framework.catalog import ExpertCard

        fake = ExpertCard(
            folder="fake", pack_id="fake", name="测试数据包", tagline="",
            scope=("电场",), entries=(), pack_path=ROOT / "packs" / "fake" / "pack.json",
            routes=({"keywords": ["电场"], "to": "math"},),
        )
        math = card_for("math")
        self.assertIsNotNone(math)
        cards = (fake, math)
        decomposition = Decomposer(cards).decompose("电场是什么?")
        folders = [t.card.folder for t in Router(cards).route(decomposition).targets]
        self.assertIn("fake", folders)
        self.assertIn("math", folders)


class TestPersona(unittest.TestCase):
    """人格:一个人,与装了几个数据包无关。"""

    def test_who_am_i(self) -> None:
        from framework import GoChat

        summary = GoChat().ask("你是谁").summary
        self.assertIn(persona.NAME, summary)
        self.assertIn("数据包", summary)
        self.assertNotIn("[凭据]", summary, "自述不引用任何数据包条目")
        self.assertNotIn("[我]", summary, "默认是人话,不是内部标签")

        traced = GoChat().ask("你是谁", trace=True).summary
        self.assertIn("[我]", traced)

    def test_capability_mentions_packs_command(self) -> None:
        from framework import GoChat

        summary = GoChat().ask("你能做什么").summary
        self.assertIn(":packs", summary)
        self.assertIn("我不做的", summary)

    def test_persona_survives_without_any_pack(self) -> None:
        """删光数据包,小析依然知道自己是谁(人格不在数据里)。"""
        from framework import GoChat

        summary = GoChat(cards=()).ask("你是谁").summary
        self.assertIn(persona.NAME, summary)
        self.assertNotIn("[我]", summary)

    def test_identity_does_not_swallow_other_questions(self) -> None:
        from framework import GoChat

        summary = GoChat().ask("王小明是谁").summary
        self.assertNotIn("我是" + persona.NAME, summary)

    def test_no_expert_wording_anywhere(self) -> None:
        """用户看到的话里不再出现"哪位专家回答"这种说法。"""
        from framework import GoChat

        chat = GoChat()
        for question in ("向量场是什么?", "你好", "你是谁", "你能做什么", "怎么给猫剪指甲"):
            with self.subTest(question=question):
                self.assertNotIn("专家", chat.ask(question).summary)


class TestKeywords(unittest.TestCase):
    """拆句第一步的产物:非常用词 = 关键词。"""

    def setUp(self) -> None:
        self.decomposer = Decomposer(CATALOG)

    def test_drops_function_and_question_words(self) -> None:
        dec = self.decomposer.decompose("引力波是什么?")
        self.assertEqual(dec.tokens, ("引力波", "是", "什么"))
        self.assertEqual(dec.keywords, ("引力波",))

    def test_keeps_every_content_word(self) -> None:
        dec = self.decomposer.decompose("向量场和电场的关系是什么?")
        self.assertEqual(dec.keywords, ("向量场", "电场"))

    def test_pronouns_are_not_keywords(self) -> None:
        dec = self.decomposer.decompose("它的频率是多少?")
        self.assertNotIn("它", dec.keywords)
        self.assertIn("频率", dec.keywords)

    def test_pure_particles_have_no_keywords(self) -> None:
        self.assertEqual(self.decomposer.decompose("还有吗").keywords, ())


class TestMemory(unittest.TestCase):
    """上下文记忆:有界、最近优先、能挑出"当前话题"。"""

    def test_remembers_and_recalls(self) -> None:
        from framework import ConversationMemory

        memory = ConversationMemory()
        memory.remember("引力波是什么?", ("引力波",), packs=("physics",), domains=("physics",))
        memory.remember("频率是多少", ("频率",), packs=("math", "physics"),
                        domains=("math", "physics"))
        self.assertEqual(memory.keywords(), ("频率", "引力波"))
        self.assertEqual(memory.topic(), ("频率",))
        self.assertEqual(memory.packs(), ("math", "physics"))

    def test_bounded_by_turns_and_keywords(self) -> None:
        from framework import ConversationMemory

        memory = ConversationMemory(max_turns=2, max_keywords=2)
        for index in range(5):
            memory.remember(f"第{index}句", (f"词{index}",))
        self.assertEqual(len(memory), 2)
        self.assertEqual(len(memory.keywords()), 2)
        self.assertNotIn("词0", memory.keywords())

    def test_topic_skips_turns_nobody_answered(self) -> None:
        from framework import ConversationMemory

        memory = ConversationMemory()
        memory.remember("引力波是什么?", ("引力波",), packs=("physics",), domains=("physics",))
        memory.remember("怎么给猫剪指甲", ("给猫剪指甲",), packs=(), domains=())
        self.assertEqual(memory.topic(), ("引力波",))

    def test_forget(self) -> None:
        from framework import ConversationMemory

        memory = ConversationMemory()
        memory.remember("引力波是什么?", ("引力波",))
        memory.clear()
        self.assertEqual(memory.keywords(), ())
        self.assertEqual(len(memory), 0)

    def test_is_followup(self) -> None:
        from framework import is_followup

        self.assertTrue(is_followup("还有吗", ()))
        self.assertTrue(is_followup("它有什么用", ("用",)))
        self.assertFalse(is_followup("怎么给猫剪指甲", ("给猫剪指甲",)))
        self.assertFalse(is_followup("向量场是什么?", ("向量场",)))


class TestContext(unittest.TestCase):
    """接着问:只有"直接没命中 + 像接上一句"时才动用记忆。"""

    def test_followup_uses_remembered_keywords(self) -> None:
        chat = GoChat()
        first = chat.ask("引力波是什么?")
        self.assertIn("数据包 physics", first.summary)

        followup = chat.ask("还有吗", trace=True)
        self.assertIn("[上下文]", followup.summary)
        self.assertIn("数据包 physics", followup.summary)
        self.assertIn("gravitational-wave", followup.summary)

    def test_new_topic_is_not_hijacked(self) -> None:
        chat = GoChat()
        chat.ask("引力波是什么?")
        fresh = chat.ask("怎么给猫剪指甲", trace=True)
        self.assertNotIn("[上下文]", fresh.summary)
        self.assertIn("暂时答不上来", fresh.summary)

    def test_own_words_win_over_memory(self) -> None:
        chat = GoChat()
        chat.ask("引力波是什么?")
        switched = chat.ask("那向量场呢?", trace=True)
        self.assertIn("数据包 math", switched.summary)
        self.assertNotIn("[上下文]", switched.summary)
        self.assertEqual(chat.memory.topic(), ("向量场",))

    def test_context_can_be_turned_off(self) -> None:
        chat = GoChat(context=False)
        chat.ask("引力波是什么?")
        followup = chat.ask("还有吗", trace=True)
        self.assertNotIn("[上下文]", followup.summary)
        self.assertIn("暂时答不上来", followup.summary)

    def test_forget_clears_memory(self) -> None:
        chat = GoChat()
        chat.ask("引力波是什么?")
        self.assertTrue(chat.remembered())
        chat.forget()
        self.assertEqual(chat.remembered(), ())
        followup = chat.ask("还有吗")
        self.assertNotIn("[上下文]", followup.summary)


class TestMaxPacks(unittest.TestCase):
    """数据包上限:只截断、不放宽;精确性由阈值守。"""

    BROAD = "向量场和电场的关系是什么?"

    def test_one_pack_keeps_only_the_best(self) -> None:
        result = GoChat(max_packs=1).ask(self.BROAD)
        self.assertIn("数据包 math", result.summary)
        self.assertNotIn("数据包 physics", result.summary)

    def test_two_packs_keep_both(self) -> None:
        result = GoChat(max_packs=2).ask(self.BROAD)
        self.assertIn("数据包 math", result.summary)
        self.assertIn("数据包 physics", result.summary)

    def test_limit_is_clamped(self) -> None:
        self.assertEqual(GoChat(max_packs=99).max_packs, settings.MAX_PACKS_LIMIT)
        self.assertEqual(GoChat(max_packs=0).max_packs, 1)

    def test_router_honours_limit_and_threshold(self) -> None:
        from framework import Decomposer, Router

        decomposition = Decomposer(CATALOG).decompose(self.BROAD)
        router = Router(CATALOG)
        self.assertEqual(len(router.route(decomposition, max_targets=1).targets), 1)
        # 上限调大也不会凭空多出包:没到阈值的就是没有
        loose = router.route(decomposition, max_targets=99).targets
        self.assertTrue(all(t.score >= 3 for t in loose))
        self.assertEqual(len(loose), 2)


class TestSettings(unittest.TestCase):
    """config.json:读坏/越界不静默,退回默认并说明原因。"""

    def test_default_config_file(self) -> None:
        config = settings.load()
        self.assertGreaterEqual(config.max_packs, 1)
        self.assertTrue(config.context_enabled)
        self.assertEqual(config.notes, ())

    def test_bad_config_falls_back_with_notes(self) -> None:
        config = settings.load(ROOT / "tests" / "fixtures" / "settings_bad.json")
        self.assertEqual(config.max_packs, settings.MAX_PACKS_LIMIT)
        self.assertTrue(config.context_enabled)
        self.assertEqual(config.max_turns, 1)
        self.assertTrue(config.notes)

    def test_env_override(self) -> None:
        config = settings.load(environ={"SMALLAI_MAX_PACKS": "1"})
        self.assertEqual(config.max_packs, 1)


class TestTeaching(unittest.TestCase):
    """教新知识:写入前问、写进记忆包、以后命中、冲突时说歧义。"""

    SEED = ROOT / "tests" / "fixtures" / "packs_root" / "packs" / "memory" / "pack.json"

    def setUp(self) -> None:
        import shutil

        # 每个测试都用新种子,别让上一个测试教的东西漏进来
        self.path = self.SEED
        self.seed_text = self.path.read_text(encoding="utf-8")
        self.addCleanup(self._restore)
        self.knowledge = "量子纠缠就是两个粒子的状态不可分。"
        self.keys = ("量子纠缠", "纠缠", "粒子")

    def _restore(self) -> None:
        self.path.write_text(self.seed_text, encoding="utf-8")

    def _card(self):
        import json

        from framework.catalog import ExpertCard

        raw = json.loads(self.path.read_text(encoding="utf-8"))
        return ExpertCard(
            folder="memory", pack_id="memory", name=raw["expert"]["name"],
            tagline="", scope=("记忆",), entries=tuple(raw["entries"]),
            pack_path=self.path, role="memory", order=900,
        )

    def test_parse_marker(self) -> None:
        self.assertEqual(lessons.parse_marker("# 量子退相干是……"), "量子退相干是……")
        self.assertEqual(lessons.parse_marker("#这是新知识:甲"), "甲")
        self.assertIsNone(lessons.parse_marker("#"))
        self.assertIsNone(lessons.parse_marker("普通问题"))

    def test_is_yes(self) -> None:
        self.assertTrue(lessons.is_yes("y"))
        self.assertTrue(lessons.is_yes("Y"))
        self.assertFalse(lessons.is_yes("n"))
        self.assertFalse(lessons.is_yes(""))

    def test_teach_appends_entry_with_keywords(self) -> None:
        entry, created = lessons.teach(self._card(), self.knowledge, self.keys)
        self.assertTrue(created)
        self.assertEqual(entry["id"], "user-0001")
        self.assertEqual(entry["question"], self.knowledge)
        self.assertEqual(entry["keywords"], list(self.keys))
        self.assertIn("user-taught", entry["tags"])

        import json

        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(len(raw["entries"]), 1)
        self.assertEqual(raw["entries"][0]["id"], "user-0001")

    def test_teaching_again_updates_not_duplicates(self) -> None:
        lessons.teach(self._card(), self.knowledge, ("量子纠缠",))
        entry, created = lessons.teach(self._card(), self.knowledge, ("纠缠", "粒子"))
        self.assertFalse(created)
        self.assertEqual(set(entry["keywords"]), {"量子纠缠", "纠缠", "粒子"})

        import json

        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(len(raw["entries"]), 1)

    def test_taught_knowledge_is_hit_after_teach(self) -> None:
        chat = GoChat(packs_root=ROOT / "tests" / "fixtures" / "packs_root")
        chat.teach(self.knowledge, self.keys)
        result = chat.ask("量子纠缠是什么?")
        self.assertIn("数据包 memory", result.summary)
        self.assertIn("user-0001", result.summary)
        self.assertIn("不是我原来的知识", result.summary)

    def test_conflict_with_another_pack_is_flagged(self) -> None:
        from framework.catalog import card_for

        lessons.teach(self._card(), "引力波是时空的涟漪。", ("引力波", "时空", "涟漪"))
        memory_card = self._card()
        physics = card_for("physics")
        self.assertIsNotNone(physics)
        chat = GoChat(cards=(memory_card, physics))
        result = chat.ask("引力波是什么?")
        self.assertIn("各有一套说法", result.summary)
        self.assertIn("数据包 memory", result.summary)
        self.assertIn("数据包 physics", result.summary)

    def test_unrelated_knowledge_is_not_flagged(self) -> None:
        lessons.teach(self._card(), "香蕉是黄色的水果。", ("香蕉", "黄色", "水果"))
        from framework.catalog import card_for

        physics = card_for("physics")
        chat = GoChat(cards=(self._card(), physics))
        result = chat.ask("香蕉是什么?")
        self.assertIn("数据包 memory", result.summary)
        self.assertNotIn("[歧义]", result.summary)

    def test_unknown_invites_teaching(self) -> None:
        result = GoChat().ask("怎么给猫剪指甲")
        self.assertIn("具体分哪几步", result.summary)
        self.assertIn("# 问题~答案", result.summary)


class TestQATeaching(unittest.TestCase):
    """Q&A 教学（#问题~答案）与 CBR 修正（#!问题~新答案）。"""

    SEED = ROOT / "tests" / "fixtures" / "packs_root" / "packs" / "memory" / "pack.json"

    def setUp(self) -> None:
        self.seed = self.SEED.read_text(encoding="utf-8")
        self.addCleanup(lambda: self.SEED.write_text(self.seed, encoding="utf-8"))

    def _chat(self) -> GoChat:
        return GoChat(packs_root=ROOT / "tests" / "fixtures" / "packs_root")

    def test_parse_pair_and_correction(self) -> None:
        self.assertEqual(lessons.parse_pair("# 猫为什么怕水~因为祖先来自沙漠"),
                         ("猫为什么怕水", "因为祖先来自沙漠"))
        self.assertEqual(lessons.parse_pair("# 旧写法"), None)
        self.assertEqual(lessons.parse_correction("#!猫为什么怕水~新答案"),
                         ("猫为什么怕水", "新答案"))
        self.assertIsNone(lessons.parse_marker("#!猫为什么怕水~新答案"))

    def test_pair_keeps_question_and_answer_separate(self) -> None:
        chat = self._chat()
        entry, created = chat.teach(
            "猫为什么怕水?", ("猫", "怕水"), answer="因为祖先来自沙漠。",
            raw="# 猫为什么怕水?~因为祖先来自沙漠。",
        )
        self.assertTrue(created)
        self.assertEqual(entry["question"], "猫为什么怕水?")
        self.assertEqual(entry["answer"], "因为祖先来自沙漠。")
        self.assertIn("~", entry["raw"])

        result = chat.ask("猫为什么怕水?")
        self.assertIn("因为祖先来自沙漠", result.summary)
        self.assertIn("user-0001", result.summary)

    def test_revise_updates_answer_and_keeps_history(self) -> None:
        chat = self._chat()
        chat.teach("猫为什么怕水?", ("猫", "怕水"), answer="旧答案。")
        entry, previous = chat.revise("猫为什么怕水?", "新答案。")
        self.assertEqual(previous, "旧答案。")
        self.assertEqual(entry["answer"], "新答案。")
        self.assertTrue(entry.get("revisions"))
        self.assertEqual(entry["revisions"][-1]["previous_answer"], "旧答案。")

        result = chat.ask("猫为什么怕水?")
        self.assertIn("新答案。", result.summary)
        self.assertNotIn("旧答案。", result.summary)

    def test_reteach_same_question_is_not_duplicated(self) -> None:
        chat = self._chat()
        first, created = chat.teach("猫为什么怕水?", ("猫",), answer="答案一。")
        self.assertTrue(created)
        second, created_again = chat.teach("猫为什么怕水?", ("猫",), answer="答案二。")
        self.assertFalse(created_again)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(chat.taught()), 1)
        self.assertEqual(second["answer"], "答案二。")

    def test_old_single_line_still_works(self) -> None:
        chat = self._chat()
        entry, _created = chat.teach("量子退相干是……", ("量子退相干",))
        self.assertEqual(entry["question"], "量子退相干是……")
        self.assertEqual(entry["answer"], "量子退相干是……")


class TestMemoryCommands(unittest.TestCase):
    """教过的知识:原话可回放(#?)、可忘掉(#-),删掉的 id 不复用。"""

    SEED = ROOT / "tests" / "fixtures" / "packs_root" / "packs" / "memory" / "pack.json"

    def setUp(self) -> None:
        self.seed_text = self.SEED.read_text(encoding="utf-8")
        self.addCleanup(lambda: self.SEED.write_text(self.seed_text, encoding="utf-8"))

    def _chat(self) -> GoChat:
        return GoChat(packs_root=ROOT / "tests" / "fixtures" / "packs_root")

    def test_parse_forget_and_view(self) -> None:
        self.assertEqual(lessons.parse_forget("#- 量子退相干"), "量子退相干")
        self.assertEqual(lessons.parse_forget("#－量子"), "量子")
        self.assertEqual(lessons.parse_forget("#-"), "")
        self.assertIsNone(lessons.parse_forget("# 量子退相干是……"))
        self.assertEqual(lessons.parse_view("#?"), "")
        self.assertEqual(lessons.parse_view("#? 量子"), "量子")
        self.assertIsNone(lessons.parse_view("# 量子退相干是……"))
        # `#-` / `#?` 不能被当成"教知识"
        self.assertIsNone(lessons.parse_marker("#- 量子"))
        self.assertIsNone(lessons.parse_marker("#?"))

    def test_teach_stores_raw_and_view_plays_it_back(self) -> None:
        chat = self._chat()
        raw = "# 量子纠缠是两只手套的关系:分开看各自随机,合起来守恒。"
        entry, created = chat.teach(
            "量子纠缠是两只手套的关系:分开看各自随机,合起来守恒。",
            ("量子纠缠", "手套"), raw=raw,
        )
        self.assertTrue(created)
        self.assertEqual(entry["raw"], raw)
        self.assertIn(raw, chat.taught_text())
        self.assertEqual(len(chat.taught()), 1)
        self.assertEqual(len(chat.taught("量子")), 1)
        self.assertEqual(len(chat.taught("香蕉")), 0)

    def test_forget_removes_only_matching_entry(self) -> None:
        chat = self._chat()
        chat.teach("甲知识。", ("甲知识",), raw="# 甲知识。")
        chat.teach("乙知识。", ("乙知识",), raw="# 乙知识。")
        removed = chat.forget_taught("甲知识")
        self.assertEqual([item["id"] for item in removed], ["user-0001"])
        self.assertEqual(len(chat.taught()), 1)
        self.assertNotIn("# 甲知识。", chat.taught_text())
        self.assertIn("# 乙知识。", chat.taught_text())

    def test_forget_without_match_keeps_everything(self) -> None:
        chat = self._chat()
        chat.teach("甲知识。", ("甲知识",), raw="# 甲知识。")
        self.assertEqual(chat.forget_taught("完全不存在的词zzz"), [])
        self.assertEqual(len(chat.taught()), 1)

    def test_forget_empty_query_changes_nothing(self) -> None:
        chat = self._chat()
        chat.teach("甲知识。", ("甲知识",), raw="# 甲知识。")
        card = chat.memory_card()
        self.assertIsNotNone(card)
        self.assertEqual(lessons.forget(card, ""), [])     # type: ignore[arg-type]
        self.assertEqual(len(chat.taught()), 1)

    def test_id_is_not_reused_after_forget(self) -> None:
        chat = self._chat()
        chat.teach("甲知识。", ("甲知识",), raw="# 甲知识。")
        chat.forget_taught("甲知识")
        entry, _created = chat.teach("乙知识。", ("乙知识",), raw="# 乙知识。")
        self.assertEqual(entry["id"], "user-0002")

    def test_old_entry_without_raw_can_still_be_listed(self) -> None:
        import json

        chat = self._chat()
        card = chat.memory_card()
        self.assertIsNotNone(card)
        lessons.teach(card, "旧知识。", ("旧知识",))          # type: ignore[arg-type]
        # 旧版本写下的条目没有 raw:删掉它,确认还能按 `# <知识>` 还原
        raw = json.loads(self.SEED.read_text(encoding="utf-8"))
        raw["entries"][0].pop("raw", None)
        self.SEED.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.assertIn("# 旧知识。", self._chat().taught_text())

    def test_secondary_keywords_are_stored(self) -> None:
        chat = self._chat()
        knowledge = "What是'什么'的意思"
        keys = chat.decomposer.decompose(knowledge).keywords
        secondary = lessons.secondary_keywords(knowledge, keys, chat.decomposer)
        self.assertIn("意思", secondary)
        chat.teach(knowledge, keys, raw="# " + knowledge, secondary=secondary)
        card = chat.memory_card()
        self.assertIsNotNone(card)
        entry = card.entry("user-0001")
        self.assertIsNotNone(entry)
        self.assertIn("意思", entry.get("secondary_keywords") or [])

    def test_memory_rehit_and_followup_use_secondary_keywords(self) -> None:
        chat = self._chat()
        for text in ("What是'什么'的意思", "What是英语单词"):
            keys = chat.decomposer.decompose(text).keywords
            secondary = lessons.secondary_keywords(text, keys, chat.decomposer)
            chat.teach(text, keys, raw="# " + text, secondary=secondary)

        first = chat.ask("What是什么?")
        self.assertEqual(len(first.answered), 2)
        self.assertIn("What是'什么'的意思", first.summary)
        self.assertIn("What是英语单词", first.summary)
        self.assertIn("我记得 2 条", first.summary)

        second = chat.ask("What是英语单词吗?")
        self.assertEqual(len(second.answered), 1)
        self.assertIn("What是英语单词", second.summary)
        self.assertNotIn("什么意思", second.summary)

        follow = chat.ask("那它是什么意思?")
        self.assertIn("意思", follow.summary)
        self.assertNotIn("What是英语单词", follow.summary)

    def test_legacy_memory_without_secondary_still_works(self) -> None:
        import json

        chat = self._chat()
        for text in ("What是'什么'的意思", "What是英语单词"):
            keys = chat.decomposer.decompose(text).keywords
            chat.teach(text, keys, raw="# " + text)
        # 模拟升级前写下的记忆：删掉 secondary_keywords
        data = json.loads(self.SEED.read_text(encoding="utf-8"))
        for entry in data.get("entries") or []:
            entry.pop("secondary_keywords", None)
        self.SEED.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        chat.reload()

        chat.ask("What是英语单词吗?")
        follow = chat.ask("那它是什么意思?")
        self.assertIn("意思", follow.summary)
        self.assertNotIn("What是英语单词", follow.summary)


class TestEngine(unittest.TestCase):
    """单进程内核:默认开启、与子进程答案一致、同一包复用流水线。"""

    def test_default_engine_is_inprocess(self) -> None:
        self.assertEqual(GoChat().engine, "inprocess")
        self.assertEqual(GoChat(engine="subprocess").engine, "subprocess")

    def test_same_answer_as_subprocess(self) -> None:
        fast = GoChat(engine="inprocess")
        slow = GoChat(engine="subprocess")
        for question in ("向量场是什么?", "引力波是什么?"):
            self.assertEqual(fast.ask(question).summary, slow.ask(question).summary)

    def test_pipeline_is_reused_for_same_pack(self) -> None:
        chat = GoChat()
        chat.ask("向量场是什么?")
        before = chat.engine_info()
        chat.ask("向量场是什么?")
        after = chat.engine_info()
        self.assertEqual(after["builds"], before["builds"])
        self.assertGreater(after["hits"], before["hits"])

    def test_engine_info_reports_mode(self) -> None:
        info = GoChat(engine="subprocess").engine_info()
        self.assertEqual(info["mode"], "subprocess")
        self.assertIn("last_ms", info)


class TestEntryAggregation(unittest.TestCase):
    """Q-Q 聚合:同一知识的多条问法按条目累加后,依次用条目原题重试。"""

    def test_tries_entry_question_after_user_text(self) -> None:
        chat = GoChat()
        card = card_for("math")
        self.assertIsNotNone(card)
        target = RouteTarget(
            card=card, score=10, reasons=(), entry_ids=("vector-field",),
            fallback_question="向量场是什么?",
        )
        asked: list[str] = []

        def fake(card, question, attempts=()):
            asked.append(question)
            ok = question == "向量场是什么?"
            return ExpertAnswer(
                folder=card.folder, name="math", question=question,
                text="答案" if ok else "没有", entry="vector-field" if ok else None,
                intent="domain", source="domain", ok=ok,
            )

        original = chat._ask_expert_card
        chat._ask_expert_card = fake          # type: ignore[method-assign]
        try:
            result = chat._ask_target(target, "量向场")
        finally:
            chat._ask_expert_card = original   # type: ignore[method-assign]
        self.assertTrue(result.ok)
        self.assertEqual(asked[0], "量向场")
        self.assertIn("向量场是什么?", asked)


class TestExpansion(unittest.TestCase):
    """上限之外的达标包:问一句再补查;同意只影响这一次。"""

    BROAD = "向量场和电场的关系是什么?"

    def test_yes_adds_the_dropped_pack(self) -> None:
        prompts: list[str] = []

        def ask_user(prompt: str) -> str:
            prompts.append(prompt)
            return "y"

        result = GoChat(max_packs=1).ask(self.BROAD, ask_user=ask_user)
        self.assertTrue(prompts)
        self.assertIn("能否再调用数据包[y/n]:", prompts[0])
        self.assertIn("physics", prompts[0])
        self.assertIn("数据包 physics", result.summary)

    def test_no_keeps_the_limit(self) -> None:
        result = GoChat(max_packs=1).ask(self.BROAD, ask_user=lambda _p: "n")
        self.assertIn("数据包 math", result.summary)
        self.assertNotIn("数据包 physics", result.summary)

    def test_without_callback_nothing_changes(self) -> None:
        result = GoChat(max_packs=1).ask(self.BROAD)
        self.assertIn("数据包 math", result.summary)
        self.assertNotIn("数据包 physics", result.summary)

    def test_limit_stays_locked_after_expansion(self) -> None:
        chat = GoChat(max_packs=1)
        chat.ask(self.BROAD, ask_user=lambda _p: "y")
        self.assertEqual(chat.max_packs, 1)
        # 下一次不再问、也不再补查
        second = chat.ask(self.BROAD)
        self.assertNotIn("数据包 physics", second.summary)


class TestPackRelations(unittest.TestCase):
    """包关系:包含 / 被包含,两边写法合并成同一张图。"""

    ROOT_REL = ROOT / "tests" / "fixtures" / "relations"

    def _cards(self):
        from framework import discover

        return discover(self.ROOT_REL)

    def test_parses_both_directions(self) -> None:
        from framework import PackGraph

        graph = PackGraph.from_cards(self._cards())
        self.assertEqual(graph.parents_of("apple"), ("fruit",))
        self.assertEqual(graph.children_of("fruit"), ("apple",))

    def test_transitive_containment(self) -> None:
        from framework import PackGraph

        graph = PackGraph.from_cards(self._cards())
        self.assertEqual(graph.ancestors("apple"), ("fruit", "food"))
        self.assertTrue(graph.contains("food", "apple"))
        self.assertTrue(graph.contains("fruit", "apple"))
        self.assertFalse(graph.contains("apple", "fruit"))
        self.assertFalse(graph.contains("apple", "apple"))

    def test_clean_relations_have_no_problems(self) -> None:
        from framework import PackGraph

        self.assertEqual(PackGraph.from_cards(self._cards()).problems(), [])

    def test_bad_relations_are_reported_not_crashing(self) -> None:
        import dataclasses

        from framework import PackGraph, discover

        cards = {card.folder: card for card in discover(self.ROOT_REL)}
        broken = dataclasses.replace(cards["apple"], contained_by=("nope",))
        cycle = dataclasses.replace(cards["fruit"], contained_by=("apple",))
        graph = PackGraph.from_cards((broken, cycle, cards["food"]))
        problems = graph.problems()
        self.assertTrue(any("nope" in item for item in problems))
        self.assertTrue(any("成环" in item for item in problems))
        # 成环也要能安全遍历
        self.assertIn("fruit", graph.ancestors("apple"))


class TestRehit(unittest.TestCase):
    """再命中:反向关键词 → 查没查过的包 → 认出"包含已命中包"的父包。"""

    ROOT_REL = ROOT / "tests" / "fixtures" / "relations"

    def _chat(self, rounds: int = 1, **kwargs):
        from framework import discover

        return GoChat(cards=discover(self.ROOT_REL), rehit_rounds=rounds,
                      context=False, **kwargs)

    def test_rehit_off_stays_precise(self) -> None:
        result = self._chat(rounds=0).ask("苹果是什么?")
        self.assertIn("数据包 apple", result.summary)
        self.assertNotIn("数据包 fruit", result.summary)

    def test_one_round_brings_in_parent_and_explains(self) -> None:
        chat = self._chat(rounds=1)
        result = chat.ask("苹果是什么?")
        self.assertIn("数据包 fruit", result.summary)
        self.assertIn("补充一句:", result.summary)
        self.assertIn("apple 属于 fruit", result.summary)
        self.assertIn("既在数据包 apple 里有条目", result.summary)
        self.assertIn("也在数据包 fruit 里有条目", result.summary)

        _decomposition, routing = chat.route("苹果是什么?")
        self.assertEqual(routing.rehits, (("apple", "fruit"),))
        self.assertTrue(any(t.via == "reverse" for t in routing.targets))

    def test_two_rounds_reach_grandparent(self) -> None:
        _decomposition, routing = self._chat(rounds=2).route("苹果是什么?")
        folders = [t.card.folder for t in routing.targets]
        self.assertIn("food", folders)
        self.assertIn(("apple", "food"), routing.rehits)
        # 不会重复命中同一个包
        self.assertEqual(len(folders), len(set(folders)))

    def test_rounds_are_clamped(self) -> None:
        self.assertEqual(self._chat(rounds=99).rehit_rounds, 3)
        self.assertEqual(self._chat(rounds=-1).rehit_rounds, 0)

    def test_default_config_rounds(self) -> None:
        self.assertEqual(GoChat().rehit_rounds, settings.DEFAULT_REHIT_ROUNDS)


class TestEndToEnd(unittest.TestCase):
    def run_gochat(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "GoChat.py", *args],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=180,
        )

    def test_html_question(self) -> None:
        proc = self.run_gochat("HTML是什么?")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("html-what", proc.stdout)
        self.assertIn("数据包 web_dev", proc.stdout)
        self.assertIn("来自数据包 web_dev · 条目 html-what", proc.stdout)

    def test_typo_is_corrected_end_to_end(self) -> None:
        proc = self.run_gochat("引立波是什么?")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("gravitational-wave", proc.stdout)

    def test_broad_question_uses_two_packs(self) -> None:
        proc = self.run_gochat("--brief", "向量场和电场的关系是什么?")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("数据包 math", proc.stdout)
        self.assertIn("数据包 physics", proc.stdout)
        self.assertIn("2 个数据包", proc.stdout)

    def test_greeting_goes_to_general_pack(self) -> None:
        proc = self.run_gochat("你好")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("你好呀", proc.stdout)
        self.assertNotIn("来自数据包 general", proc.stdout)

    def test_listing_marks_general_pack(self) -> None:
        proc = self.run_gochat("--packs")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("数据包 general", proc.stdout)
        self.assertIn("兜底包", proc.stdout)

    def test_old_experts_flag_still_works(self) -> None:
        proc = self.run_gochat("--experts")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("数据包", proc.stdout)

    def test_view_memory_command(self) -> None:
        proc = self.run_gochat("#?")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("你教的知识", proc.stdout)

    def test_forget_unknown_keeps_memory(self) -> None:
        proc = self.run_gochat("#-", "完全不存在的知识zzz")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("没有找到", proc.stdout)
        self.assertIn("什么都没删", proc.stdout)

    def test_framework_does_not_import_a_model(self) -> None:
        self.assertNotIn("torch", sys.modules)
        self.assertNotIn("transformers", sys.modules)


if __name__ == "__main__":
    unittest.main()
