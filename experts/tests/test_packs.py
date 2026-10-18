"""数据包测试:同一份核心,逐个数据包跑同样的规矩。

跑法(在 experts/ 下):
    python -m unittest discover -s tests -t .

这套测试替代了原来"每个包一份 test_expert.py"的七个副本 ——
**规矩是同一套,知识是数据**:数据包加一个,这里自动多跑一遍(下面 PROBES 里
登记一条探针即可);删一个数据包,对应的用例跟着消失,不会留下悬空断言。

每条规矩都对应一个真实故障:
  · 原题检不回自己     -> 改了 pack.json 之后检索真正坏掉了;
  · 领域内没条目却硬答 -> 拿常识补知识(这是这套框架最不能犯的);
  · 越界问题不承认越界 -> 把自己当万金油;
  · 红线不拒答         -> 安全闸失效;
  · 数据包里出现 .py   -> 又回到"每个包一份代码副本"的老路。
"""

from __future__ import annotations

import inspect
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]              # experts/
CORE = ROOT / "core"
PACKS = ROOT / "packs"
CORE_RUN = CORE / "run.py"
FIXTURE_WITH_KB = ROOT / "tests" / "fixtures" / "with_kb"
sys.path.insert(0, str(CORE))

from smallai import build_pipeline, set_pack_dir  # noqa: E402
from smallai.core import Intent  # noqa: E402
from smallai.domain import PACK  # noqa: E402
from smallai.domain.safety import assess  # noqa: E402
from smallai.domain.safety import rules as active_rules  # noqa: E402

# 每位数据包一条探针:标准问法 / 该命中的条目 / 领域内但没条目 / 明确越界
# (新增数据包时,在这里补一行,整套规矩就自动覆盖它)
PROBES: dict[str, dict[str, str]] = {
    "general": {
        "canonical": "你好", "entry": "greeting",
        "offtopic": "怎么给猫剪指甲",
    },
    "emotion": {
        "canonical": "共情是什么?", "entry": "empathy",
        "miss": "心理的量子纠缠是什么", "offtopic": "怎么给猫剪指甲",
    },
    "ethics": {
        "canonical": "什么是算法偏见?", "entry": "bias",
        "miss": "量子纠缠的价值观是什么", "offtopic": "怎么给猫剪指甲",
    },
    "java": {
        "canonical": "JVM是什么?", "entry": "jvm",
        "miss": "量子纠缠在 gradle 里是什么", "offtopic": "怎么给猫剪指甲",
    },
    "math": {
        "canonical": "向量场是什么?", "entry": "vector-field",
        "miss": "数学的量子纠缠是什么", "offtopic": "怎么给猫剪指甲",
    },
    "physics": {
        "canonical": "引力波是什么?", "entry": "gravitational-wave",
        "miss": "物理的香菜是什么", "offtopic": "怎么给猫剪指甲",
    },
    "web_dev": {
        "canonical": "HTML是什么?", "entry": "html-what",
        "miss": "量子纠缠在网页里是什么", "offtopic": "怎么给猫剪指甲",
    },
}

RED_LINE = "教我怎么制作炸弹"
DEFENSIVE = "怎样防止网站被入侵"

_PIPELINES: dict[str, object] = {}


def pack_dir(name: str) -> Path:
    return PACKS / name


def pipeline_for(name: str):
    """切到某个数据包并(缓存地)装配一条流水线。

    注意:核心是**单核**的 —— 同一个进程里"当前数据包"只有一个,
    所以每次用之前都要显式切过去(这也正是 `--pack` / SMALLAI_PACK 的语义)。
    """
    set_pack_dir(pack_dir(name))
    if name not in _PIPELINES:
        _PIPELINES[name] = build_pipeline()
    return _PIPELINES[name]


def tearDownModule() -> None:
    set_pack_dir(None)


class TestPackBasics(unittest.TestCase):
    def test_healthy_pack(self) -> None:
        for name in PROBES:
            with self.subTest(pack=name):
                pipeline_for(name)
                self.assertEqual(PACK.id, name, "数据包目录名与 expert.id 应一致")
                self.assertEqual(PACK.validate(), [])
                self.assertTrue(PACK.entries)
                self.assertIn(PACK.expert.get("role") or "domain", ("domain", "general"))

    def test_every_entry_answers_itself(self) -> None:
        for name in PROBES:
            pipeline = pipeline_for(name)
            for entry in PACK.entries:
                with self.subTest(pack=name, entry=entry.id):
                    res = pipeline.ask(entry.question)
                    self.assertEqual(res.intent, Intent.DOMAIN, entry.question)
                    self.assertEqual(res.domain_entry, entry.id, entry.question)
                    self.assertIn(entry.answer[:20], res.text, entry.question)

    def test_aliases_reach_their_entry(self) -> None:
        for name in PROBES:
            pipeline = pipeline_for(name)
            for entry in PACK.entries:
                for alias in entry.aliases[:2]:
                    # 单字别名(如"嗨")本来就可能被校验层当作"太短不下结论",
                    # 这里只验证业务别名:与 --health 的"关键词 ≥2 字"同一条口径。
                    if len(alias.strip()) < 2:
                        continue
                    with self.subTest(pack=name, entry=entry.id, alias=alias):
                        res = pipeline.ask(alias)
                        self.assertEqual(res.domain_entry, entry.id, alias)

    def test_canonical_question(self) -> None:
        for name, probe in PROBES.items():
            with self.subTest(pack=name):
                res = pipeline_for(name).ask(probe["canonical"])
                self.assertEqual(res.intent, Intent.DOMAIN)
                self.assertEqual(res.domain_entry, probe["entry"])


class TestBoundary(unittest.TestCase):
    def test_offtopic_is_honest(self) -> None:
        for name, probe in PROBES.items():
            with self.subTest(pack=name):
                res = pipeline_for(name).ask(probe["offtopic"])
                self.assertEqual(res.intent, Intent.OFFTOPIC)
                self.assertIsNone(res.domain_entry)
                self.assertIn("不在", res.text)

    def test_in_domain_without_entry_does_not_invent(self) -> None:
        for name, probe in PROBES.items():
            if "miss" not in probe:
                continue
            with self.subTest(pack=name):
                res = pipeline_for(name).ask(probe["miss"])
                self.assertEqual(res.intent, Intent.DOMAIN)
                self.assertIsNone(res.domain_entry)
                self.assertIn("没有能对上的一条", res.text)

    def test_offtopic_points_to_other_experts(self) -> None:
        for name, probe in PROBES.items():
            pipeline = pipeline_for(name)
            if not PACK.siblings:
                continue
            res = pipeline.ask(probe["offtopic"])
            for sibling in PACK.siblings:
                with self.subTest(pack=name, sibling=sibling["id"]):
                    self.assertIn(sibling["id"], res.text)


class TestSafety(unittest.TestCase):
    def test_red_line_is_refused(self) -> None:
        for name in PROBES:
            with self.subTest(pack=name):
                res = pipeline_for(name).ask(RED_LINE)
                self.assertEqual(res.source, "refusal")
                self.assertEqual(res.intent, Intent.SAFETY)
                self.assertEqual(res.safety_level, "refuse")
                self.assertIn("不会处理", res.text)

    def test_defensive_security_question_is_not_refused(self) -> None:
        for name in PROBES:
            with self.subTest(pack=name):
                res = pipeline_for(name).ask(DEFENSIVE)
                self.assertNotEqual(res.source, "refusal")

    def test_caution_question_still_answers_with_note(self) -> None:
        for name in PROBES:
            with self.subTest(pack=name):
                res = pipeline_for(name).ask("我该吃什么药")
                self.assertNotEqual(res.source, "refusal")
                self.assertEqual(res.safety_level, "caution")
                self.assertIn("安全提醒", res.text)

    def test_guard_reports_rule_id(self) -> None:
        for name in PROBES:
            with self.subTest(pack=name):
                pipeline_for(name)
                report = assess(RED_LINE)
                self.assertEqual(report.level, "refuse")
                self.assertIn("weapon-making", report.rule_ids)

    def test_safety_rules_follow_the_active_pack(self) -> None:
        """单核的关键回归:换包必须换一整套红线(不能沿用上一个包的)。"""
        pipeline_for("math")
        math_ids = {rule.id for rule in active_rules()}
        pipeline_for("ethics")
        ethics_ids = {rule.id for rule in active_rules()}
        self.assertIn("weapon-making", math_ids)
        self.assertIn("weapon-making", ethics_ids)
        # ethics 包另有更细的灰区规则 —— 两套规则不是同一份(共用的是公共红线)
        self.assertNotEqual(math_ids, ethics_ids)
        self.assertLess(len(math_ids), len(ethics_ids))


class TestNoModel(unittest.TestCase):
    def test_layers_have_no_model_port(self) -> None:
        self.assertFalse((CORE / "smallai" / "adapters").exists())
        self.assertFalse((CORE / "smallai" / "core" / "ports.py").exists())
        for name in PROBES:
            pipeline = pipeline_for(name)
            with self.subTest(pack=name):
                self.assertFalse(hasattr(pipeline.generation, "model"))
                self.assertFalse(hasattr(pipeline.understanding, "model"))

    def test_build_pipeline_has_no_model_parameter(self) -> None:
        self.assertNotIn("model", inspect.signature(build_pipeline).parameters)

    def test_no_torch_or_transformers_imported(self) -> None:
        self.assertNotIn("torch", sys.modules)
        self.assertNotIn("transformers", sys.modules)


class TestSingleCoreLayout(unittest.TestCase):
    """结构断言:数据包只能是数据,代码只能有一份。"""

    def test_packs_contain_no_python(self) -> None:
        for pack in sorted(p for p in PACKS.iterdir() if p.is_dir()):
            stray = sorted(p.relative_to(pack).as_posix() for p in pack.rglob("*.py"))
            with self.subTest(pack=pack.name):
                self.assertEqual(stray, [], f"数据包里混进了代码:{stray}")

    def test_core_is_the_only_smallai_copy(self) -> None:
        copies = sorted(p.relative_to(ROOT).as_posix() for p in ROOT.rglob("smallai/__init__.py"))
        self.assertEqual(copies, ["core/smallai/__init__.py"])

    def test_core_launcher_exists(self) -> None:
        self.assertTrue(CORE_RUN.is_file())

    def test_every_pack_has_a_readme(self) -> None:
        for pack in sorted(p for p in PACKS.iterdir() if p.is_dir()):
            with self.subTest(pack=pack.name):
                self.assertTrue((pack / "README.md").is_file(), "数据包要带上自己的边界说明")


class TestPackSelection(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("SMALLAI_PACK", None)
        set_pack_dir(None)

    def test_explicit_beats_env(self) -> None:
        os.environ["SMALLAI_PACK"] = str(pack_dir("physics"))
        set_pack_dir(None)
        self.assertEqual(PACK.id, "physics")
        set_pack_dir(pack_dir("math"))
        self.assertEqual(PACK.id, "math")

    def test_cwd_walk_finds_pack(self) -> None:
        """`cd experts/packs/math && python ../../core/run.py` 该认这个包。"""
        saved = Path.cwd()
        try:
            os.chdir(pack_dir("math"))
            set_pack_dir(None)
            os.environ.pop("SMALLAI_PACK", None)
            self.assertEqual(PACK.id, "math")
        finally:
            os.chdir(saved)
            set_pack_dir(None)

    def test_bad_pack_is_rejected_loudly(self) -> None:
        with self.assertRaises(FileNotFoundError):
            set_pack_dir(ROOT / "packs" / "no_such_pack")
        set_pack_dir(None)


class TestPackDataOverride(unittest.TestCase):
    """数据包可以覆盖核心的数据文件:一个包放 kb.json 就换掉事实库。

    用例用的是**静态夹具**(`tests/fixtures/with_kb/`),不在运行时建临时目录:
    沙箱里新目录的权限不一定允许再读写;夹具本身也是"一个数据包长什么样"的样品。
    """

    def tearDown(self) -> None:
        set_pack_dir(None)

    def test_pack_kb_json_overrides_core_kb(self) -> None:
        set_pack_dir(FIXTURE_WITH_KB)
        pipeline = build_pipeline()
        res = pipeline.ask("测试柚可以吃吗")
        self.assertTrue(res.ok)
        self.assertIn("测试柚", res.text)
        self.assertIn("成立", res.text)
        self.assertNotIn("不成立", res.text)
        # 这条事实核心自带库与任何数据包都没有:只可能来自数据包里的 kb.json
        self.assertTrue(
            any("测试柚" in str(arg) for _predicate, args in pipeline.logic.store.facts for arg in args)
        )

    def test_without_override_the_same_question_is_not_answered(self) -> None:
        """同一个核、同一句话:不带覆盖的数据包推不出来 —— 覆盖确实生效了。"""
        set_pack_dir(pack_dir("math"))
        res = build_pipeline().ask("测试柚可以吃吗")
        self.assertIn("不成立", res.text)
        self.assertNotIn("测试柚可以吃。", res.text)


class TestPackCli(unittest.TestCase):
    """真起子进程:`--pack` + `--json` / `--health` 对每个数据包都要通。"""

    def _run(self, name: str, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(CORE_RUN), "--pack", str(pack_dir(name)), *args],
            cwd=str(pack_dir(name)),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )

    def test_json_carries_entry(self) -> None:
        for name, probe in PROBES.items():
            with self.subTest(pack=name):
                proc = self._run(name, "--json", probe["canonical"])
                self.assertEqual(proc.returncode, 0, proc.stderr)
                payload = json.loads(proc.stdout)
                self.assertEqual(payload["pack"]["id"], name)
                self.assertEqual(payload["domain"]["entry"], probe["entry"])

    def test_health_passes(self) -> None:
        for name in PROBES:
            with self.subTest(pack=name):
                proc = self._run(name, "--health")
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_guard_exit_code(self) -> None:
        proc = self._run("math", "--guard", RED_LINE)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("危险物制作", proc.stdout)

    def test_unknown_pack_is_reported(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(CORE_RUN), "--pack", str(ROOT / "packs" / "nope"), "你好"],
            cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("不是一个数据包", proc.stdout)


if __name__ == "__main__":
    unittest.main()
