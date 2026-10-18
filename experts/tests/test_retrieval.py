"""新检索层:BM25 / 倒排增量 / 分层 / 混合 / 校准 / 信任 / 冲突。"""

from __future__ import annotations

import json
import shutil
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from framework import Decomposer, budget, settings  # noqa: E402
from framework.calibration import Calibrator  # noqa: E402
from framework.catalog import ExpertCard, card_for  # noqa: E402
from framework.conflicts import resolve  # noqa: E402
from framework.retrieval import (  # noqa: E402
    BM25Index,
    HierarchyIndex,
    HybridRetriever,
)
from framework.summarizer import ExpertAnswer  # noqa: E402
from framework.tfidf import entry_tokens, tokenize  # noqa: E402
from framework.trust import TrustMap  # noqa: E402


def _card(
    folder: str,
    entries: tuple[dict, ...],
    *,
    role: str = "domain",
    path: Path | None = None,
) -> ExpertCard:
    return ExpertCard(
        folder=folder,
        pack_id=folder,
        name=folder,
        tagline="",
        scope=(folder,),
        entries=entries,
        pack_path=path or Path(f"{folder}/pack.json"),
        role=role,
    )


def _entry(
    entry_id: str,
    question: str,
    keywords: tuple[str, ...],
    *,
    heading: str = "",
    source_file: str = "",
) -> dict:
    entry = {
        "id": entry_id,
        "question": question,
        "answer": question,
        "keywords": list(keywords),
    }
    if heading:
        entry["source_heading"] = heading
    if source_file:
        entry["source_file"] = source_file
    return entry


class TestBM25Index(unittest.TestCase):
    def _index(self) -> BM25Index:
        index = BM25Index()
        index.upsert_pack(_card("math", (
            _entry("vector-field", "向量场是什么?", ("向量场", "向量")),
            _entry("electric-field", "电场是什么?", ("电场", "电荷")),
        )))
        index.upsert_pack(_card("life", (
            _entry("cat", "怎么给猫剪指甲?", ("猫", "指甲")),
        )))
        return index

    def test_exact_question_tops(self) -> None:
        hits = self._index().search(tokenize("向量场是什么?"), limit=3)
        self.assertTrue(hits)
        self.assertEqual(hits[0].folder, "math")
        self.assertEqual(hits[0].entry_id, "vector-field")

    def test_remove_pack_removes_all_rows(self) -> None:
        index = self._index()
        self.assertEqual(index.size, 3)
        index.remove_pack("life")
        self.assertEqual(index.size, 2)
        self.assertFalse(index.search(tokenize("猫"), limit=3))

    def test_upsert_pack_is_incremental_and_replaces(self) -> None:
        index = self._index()
        before = index.stats()["updates"]
        index.upsert_pack(_card("life", (
            _entry("cat", "猫为什么怕水?", ("猫", "水")),
        )))
        self.assertGreater(index.stats()["updates"], before)
        self.assertEqual(index.size, 3)
        self.assertFalse(index.search(tokenize("剪指甲"), limit=3))
        self.assertTrue(index.search(tokenize("猫怕水"), limit=3))


class TestHierarchyIndex(unittest.TestCase):
    def test_heading_boosts_only_its_subtree(self) -> None:
        card = _card("notes", (
            _entry("q1", "量子是什么?", ("量子",), heading="物理 > 量子", source_file="notes.md"),
            _entry("q2", "经典力学是什么?", ("经典",), heading="物理 > 经典", source_file="notes.md"),
        ))
        index = HierarchyIndex()
        index.upsert_pack(card)
        boosts = index.boosts(tokenize("量子"))
        self.assertIn("notes:q1", boosts)
        self.assertNotIn("notes:q2", boosts)

    def test_remove_pack_clears_nodes(self) -> None:
        index = HierarchyIndex()
        index.upsert_pack(_card("notes", (
            _entry("q1", "量子是什么?", ("量子",), heading="物理", source_file="notes.md"),
        )))
        self.assertTrue(index.nodes)
        index.remove_pack("notes")
        self.assertEqual(index.nodes, {})


class TestHybridRetriever(unittest.TestCase):
    def _retriever(self) -> HybridRetriever:
        card = _card("demo", (
            _entry(
                "decoherence",
                "量子退相干是什么?",
                ("量子退相干", "退相干", "量子"),
            ),
        ))
        return HybridRetriever((card,))

    def test_paraphrase_is_reached(self) -> None:
        retriever = self._retriever()
        hits = retriever.search("相干性为什么会消失", limit=3)
        self.assertTrue(hits)
        self.assertEqual(hits[0].entry_id, "decoherence")
        self.assertGreater(hits[0].score, 0)
        self.assertGreaterEqual(retriever.expansions, 1)

    def test_offtopic_stays_empty(self) -> None:
        self.assertEqual(self._retriever().search("怎么给猫剪指甲", limit=3), [])

    def test_pack_search_is_scoped(self) -> None:
        retriever = self._retriever()
        self.assertTrue(retriever.search_pack("demo", "退相干", limit=3))
        self.assertEqual(retriever.search_pack("other", "退相干", limit=3), [])


class TestCalibrator(unittest.TestCase):
    def test_strong_evidence_is_not_abstained(self) -> None:
        result = Calibrator().calibrate_hybrid(
            [10.0, 2.0], matched=5, total=5, trust="high"
        )
        self.assertFalse(result.abstain)
        self.assertGreaterEqual(result.value, 0.7)

    def test_weak_evidence_abstains(self) -> None:
        result = Calibrator().calibrate_hybrid(
            [0.2, 0.19], matched=0, total=6, trust="normal"
        )
        self.assertTrue(result.abstain)
        self.assertLess(result.value, 0.35)

    def test_high_trust_beats_low_trust(self) -> None:
        high = Calibrator().calibrate_hybrid(
            [1.0, 0.9], matched=1, total=6, trust="high"
        )
        low = Calibrator().calibrate_hybrid(
            [1.0, 0.9], matched=1, total=6, trust="low"
        )
        self.assertGreater(high.value, low.value)


class TestTrustMap(unittest.TestCase):
    def test_author_entries_are_high_by_default(self) -> None:
        trust = TrustMap()
        self.assertEqual(trust.level(_card("math", ()), "x"), "high")
        self.assertEqual(trust.level(_card("memory", (), role="memory"), "x"), "normal")

    def test_explicit_refs_override_default(self) -> None:
        trust = TrustMap(
            high=frozenset({"memory/u1"}),
            low=frozenset({"math/x"}),
        )
        self.assertEqual(trust.level(_card("memory", (), role="memory"), "u1"), "high")
        self.assertEqual(trust.level(_card("math", ()), "x"), "low")

    def test_default_priority_author_before_memory(self) -> None:
        trust = TrustMap()
        self.assertLess(trust.rank("math", "domain"), trust.rank("memory", "memory"))


class TestConflictResolution(unittest.TestCase):
    def _cards(self) -> tuple[ExpertCard, ExpertCard]:
        physics = _card("physics", (
            _entry("gravitational-wave", "引力波是什么?", ("引力波",)),
        ))
        memory = _card("memory", (
            _entry("user-1", "引力波是时空的涟漪。", ("引力波",)),
        ), role="memory")
        return physics, memory

    def _answers(self) -> tuple[list[ExpertAnswer], tuple[ExpertCard, ExpertCard]]:
        physics, memory = self._cards()
        answers = [
            ExpertAnswer(folder="memory", name="memory", question="q", text="m",
                         entry="user-1", ok=True, role="memory"),
            ExpertAnswer(folder="physics", name="physics", question="q", text="p",
                         entry="gravitational-wave", ok=True),
        ]
        return answers, (physics, memory)

    def test_author_comes_first_and_low_is_marked(self) -> None:
        answers, cards = self._answers()
        report = resolve(answers, cards, TrustMap())
        self.assertEqual([a.folder for a in report.answers], ["physics", "memory"])
        self.assertIn("低优先级", report.answers[1].conflict_note)
        self.assertIn("physics", report.ambiguity)
        self.assertTrue(report.notes)

    def test_strict_drops_low_priority(self) -> None:
        answers, cards = self._answers()
        report = resolve(
            answers, cards, TrustMap(conflict_policy="strict")
        )
        self.assertEqual([a.folder for a in report.answers], ["physics"])

    def test_custom_priority_can_flip_memory_first(self) -> None:
        answers, cards = self._answers()
        report = resolve(
            answers, cards, TrustMap(conflict_priority=("memory", "author"))
        )
        self.assertEqual([a.folder for a in report.answers], ["memory", "physics"])

    def test_unrelated_answers_are_untouched(self) -> None:
        physics, memory = self._cards()
        answers = [
            ExpertAnswer(folder="memory", name="memory", question="q", text="m",
                         entry="user-1", ok=True, role="memory"),
            ExpertAnswer(folder="physics", name="physics", question="q", text="p",
                         entry="gravitational-wave", ok=True),
        ]
        # 让记忆条目的键与物理条目不相交
        memory = _card("memory", (
            _entry("user-1", "香蕉是什么?", ("香蕉",)),
        ), role="memory")
        report = resolve(answers, (physics, memory), TrustMap())
        self.assertEqual([a.folder for a in report.answers], ["memory", "physics"])
        self.assertEqual(report.ambiguity, ())


class TestSettingsTrust(unittest.TestCase):
    def _write(self, payload: dict) -> Path:
        directory = ROOT / "tests" / ".tmp_trust"
        directory.mkdir(parents=True, exist_ok=True)
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        path = directory / f"config_{id(self)}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def test_parse_trust_and_retrieval(self) -> None:
        path = self._write({
            "retrieval": {"bm25": False, "k1": 9},
            "trust": {
                "high_confidence": ["memory/u1"],
                "conflict_priority": ["memory", "author"],
                "conflict_policy": "strict",
            },
        })
        config = settings.load(path)
        self.assertFalse(config.retrieval.bm25)
        self.assertEqual(config.retrieval.k1, 3.0)
        self.assertEqual(config.trust.high_confidence, ("memory/u1",))
        self.assertEqual(config.trust.conflict_priority, ("memory", "author"))
        self.assertTrue(config.notes)

    def test_set_ref_and_priority_writes_back(self) -> None:
        path = self._write({"trust": {}})
        settings.set_trust_ref("memory/u1", "high", path=path)
        settings.set_priority(("math", "memory"), path=path)
        config = settings.load(path)
        self.assertIn("memory/u1", config.trust.high_confidence)
        self.assertEqual(config.trust.conflict_priority, ("math", "memory"))

    def test_bad_ref_is_reported(self) -> None:
        path = self._write({"trust": {"high_confidence": ["not-a-ref"]}})
        config = settings.load(path)
        self.assertEqual(config.trust.high_confidence, ())
        self.assertTrue(any("不是「包/条目」格式" in note for note in config.notes))


class TestRouterIncremental(unittest.TestCase):
    def test_refresh_only_rebuilds_changed_pack(self) -> None:
        root = ROOT / "tests" / ".tmp_router"
        shutil.rmtree(root, ignore_errors=True)
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        a_path = root / "a" / "pack.json"
        b_path = root / "b" / "pack.json"
        a_path.parent.mkdir(parents=True, exist_ok=True)
        b_path.parent.mkdir(parents=True, exist_ok=True)
        a_path.write_text("{}", encoding="utf-8")
        b_path.write_text("{}", encoding="utf-8")
        a = _card("a", (_entry("one", "甲是什么?", ("甲",)),), path=a_path)
        b = _card("b", (_entry("two", "乙是什么?", ("乙",)),), path=b_path)

        from framework.router import Router

        router = Router((a, b))
        self.assertEqual(router.retrieval.bm25.size, 2)
        a_path.write_text('{"changed": true}', encoding="utf-8")
        a_new = _card("a", (_entry("three", "丙是什么?", ("丙",)),), path=a_path)
        router.refresh((a_new, b))
        self.assertEqual(router.retrieval.bm25.size, 2)
        self.assertEqual(router.last_refresh, ("a",))
        self.assertTrue(router.retrieval.bm25.search(tokenize("丙"), limit=2))
        self.assertFalse(router.retrieval.bm25.search(tokenize("甲"), limit=2))

        router.refresh((a_new,))
        self.assertEqual(router.retrieval.bm25.size, 1)
        self.assertEqual(router.last_refresh, ("b",))
        self.assertFalse(router.retrieval.bm25.search(tokenize("乙"), limit=2))


class TestWorldInfoBudget(unittest.TestCase):
    def _card(self) -> ExpertCard:
        return _card("demo", (
            {"id": "low", "question": "低优先级", "answer": "低", "keywords": ["共同"],
             "priority": 1},
            {"id": "high", "question": "高优先级", "answer": "高", "keywords": ["共同"],
             "priority": 999},
            {"id": "g1", "question": "同组一", "answer": "一", "keywords": ["共同"],
             "priority": 50, "group": "same"},
            {"id": "g2", "question": "同组二", "answer": "二", "keywords": ["共同"],
             "priority": 60, "group": "same"},
        ))

    def test_priority_and_inclusion_group(self) -> None:
        card = self._card()
        kept, omitted = budget.select_entries(
            card, ["low", "high", "g1", "g2"], max_entries=3
        )
        self.assertEqual(kept[0], "high")
        self.assertEqual(omitted, 1)
        self.assertEqual(sum(1 for item in kept if item.startswith("g")), 1)

    def test_trim_answers_respects_budget(self) -> None:
        answers = [
            ExpertAnswer(folder="a", name="a", question="q", text="x" * 10, ok=True),
            ExpertAnswer(folder="b", name="b", question="q", text="y" * 10, ok=True),
            ExpertAnswer(folder="c", name="c", question="q", text="z" * 10, ok=True),
        ]
        result = budget.trim_answers(answers, max_entries=2, text_budget=100)
        self.assertEqual(len(result.answers), 2)
        self.assertEqual(result.omitted, 1)
        self.assertIn("预算", result.note)

    def test_router_applies_entry_budget(self) -> None:
        from framework.router import Router
        from framework.settings import RetrievalSettings

        card = self._card()
        router = Router(
            (card,),
            retrieval=RetrievalSettings(max_entries=2, text_budget=0),
        )
        routing = router.route(Decomposer((card,)).decompose("共同"))
        self.assertTrue(routing.targets)
        ids = routing.targets[0].entry_ids
        self.assertLessEqual(len(ids), 2)
        self.assertEqual(ids[0], "high")


class TestGoChatTrustIntegration(unittest.TestCase):
    SEED = ROOT / "tests" / "fixtures" / "packs_root" / "packs" / "memory" / "pack.json"

    def setUp(self) -> None:
        self.seed = self.SEED.read_text(encoding="utf-8")
        self.addCleanup(lambda: self.SEED.write_text(self.seed, encoding="utf-8"))

    def _memory_card(self) -> ExpertCard:
        raw = json.loads(self.SEED.read_text(encoding="utf-8"))
        return ExpertCard(
            folder="memory", pack_id="memory", name=raw["expert"]["name"],
            tagline="", scope=("记忆",), entries=tuple(raw["entries"]),
            pack_path=self.SEED, role="memory", order=900,
        )

    def test_author_answer_precedes_memory_on_conflict(self) -> None:
        from framework import lessons

        lessons.teach(self._memory_card(), "引力波是时空的涟漪。", ("引力波", "时空"))
        memory = self._memory_card()
        physics = card_for("physics")
        from framework import GoChat

        result = GoChat(cards=(memory, physics)).ask("引力波是什么?")
        self.assertEqual(
            [answer.folder for answer in result.answered],
            ["physics", "memory"],
        )
        self.assertIn("低优先级", result.summary)

    def test_memory_can_be_marked_high(self) -> None:
        from framework import lessons

        lessons.teach(self._memory_card(), "香蕉是黄色的。", ("香蕉", "黄色"))
        memory = self._memory_card()
        config = settings.Settings(
            trust=settings.TrustSettings(high_confidence=("memory/user-0001",))
        )
        from framework import GoChat

        chat = GoChat(cards=(memory,), config=config)
        self.assertEqual(chat.trust.level(memory, "user-0001"), "high")
        traced = chat.ask("香蕉是什么?", trace=True)
        self.assertIn("高置信", traced.summary)


if __name__ == "__main__":
    unittest.main()
