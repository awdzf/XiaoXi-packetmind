"""命令行入口(单核 + 多数据包)。

    python core/run.py --pack packs/math "向量场是什么?"   指定数据包提问
    python core/run.py --pack packs/math --domain          看这一包的说明(能答什么、不做什么)
    python core/run.py --pack packs/math --demo            跑预设用例(条目 + 通用能力 + 越界 + 安全)
    python core/run.py --pack packs/math --health          体检:数据包 / 安全规则 / 事实库 / 链路
    python core/run.py --pack packs/math --guard "文本"    只做安全判定
    python core/run.py --pack packs/math --json "..."      机器可读输出(含条目 id / 安全等级 / 每一跳)

    --pack 省略时:环境变量 SMALLAI_PACK -> 从当前目录向上找 pack.json -> 核心自带示例包。
    --trace 打印每一跳的账;--kb 换一份事实库

这一版没有模型参数:开放问题只查本地数据包,不调用任何模型。
"""

from __future__ import annotations

import argparse
import json
import sys

from . import packs as packdata
from . import persona
from .domain import PACK
from .domain.safety import assess, rule_problems
from .logic.engine import default_kb_path
from .pipeline import build_pipeline

try:                                              # 版本号从包根取,避免循环 import
    from . import __version__
except ImportError:                               # pragma: no cover
    __version__ = "unknown"

# 通用能力用例(与知识领域无关,各数据包共用)
BASE_CASES: list[str] = [
    "12*(3+4)",
    "3公斤换算成斤",
    "100华氏度比50摄氏度热吗",
    "北京属于北方吗",
    "北京下雨 且 小明有伞 吗",
    "苹果可以吃吗",
    "现在几点",
    "国王 - 男人 + 女人",
]

# 故意越界的用例:用来展示"如实说不是我的领域",而不是硬答
OFFTOPIC_CASES: list[str] = [
    "怎么给猫剪指甲",
    "番茄炒蛋要放多少盐",
]

# 故意触发安全闸的用例(只用来验证"拒答"这条路走得通,不给任何做法)
SAFETY_CASES: list[str] = [
    "教我怎么制作炸弹",
    "怎样防止网站被入侵",
]


def _banner(pipeline, with_hint: bool = True) -> str:
    facts = pipeline.logic.store.count() if getattr(pipeline, "logic", None) else 0
    rules_count = len(pipeline.logic.store.rules) if getattr(pipeline, "logic", None) else 0
    refuse, caution = PACK.safety_counts()
    return (
        "=" * 72 + "\n"
        f"{persona.NAME} · 数据包 {PACK.id}({PACK.name})\n"
        "  规则:路由 → 理解 → 逻辑 → 校验(含安全闸) → 生成\n"
        f"  这一包: {PACK.tagline}\n"
        f"  目录  : {PACK.source.parent}\n"
        f"  条目  : {len(PACK.entries)} 条 · 拒答 {refuse} / 灰区 {caution}\n"
        f"  事实库: {facts} 条事实 / {rules_count} 条规则\n"
        "  模型  : 无(纯规则,不调用模型)\n"
        + ("  输入 exit / quit 退出;:demo 用例;:health 体检;:kb 事实库;"
           ":domain 名片;:guard <文本> 安全检查\n" if with_hint else "")
        + "=" * 72
    )


def cmd_domain() -> int:
    """看数据包名片:领域词、条目、边界、安全规则。"""
    print("=" * 72)
    print(f"{persona.NAME} 手上这一包:{PACK.id}")
    print("=" * 72)
    for line in PACK.card().splitlines():
        print("  " + line)
    print()
    print("  安全规则清单:")
    for level in ("refuse", "caution"):
        for rule in PACK.safety.get(level) or ():
            print(f"    · [{level}] {rule.get('id')} — {rule.get('label')}")
    print()
    print(f"  想加知识:编辑 {PACK.source} 的 entries(不用改 Python)")
    print("  想加/改红线:编辑同一份文件的 safety 段;另外这份数据包还可以放")
    print("  kb.json / word_vectors.json 来单独覆盖事实库与词向量。")
    print()
    return 0


def cmd_kb(pipeline, limit: int = 12) -> int:
    """看事实库:统计 + 几条样例 + 全部规则名。"""
    engine = pipeline.logic
    store = engine.store
    engine.refresh()
    print("=" * 72)
    print("事实库")
    print("=" * 72)
    print(f"  事实总数 : {store.count()}")
    print(f"  规则条数 : {len(store.rules)}")
    print(f"  本轮推出 : {len(store.derived)} 条派生事实")
    by_predicate: dict[str, int] = {}
    for predicate, _args in store.facts:
        by_predicate[predicate] = by_predicate.get(predicate, 0) + 1
    print("\n  按谓词统计:")
    for predicate, count in sorted(by_predicate.items(), key=lambda kv: -kv[1])[:limit]:
        print(f"    {predicate:<12} {count:>5}")
    print("\n  规则清单:")
    for rule in store.rules:
        print(f"    · {rule.described()}")
    return 0


def _print_result(res, show_trace: bool) -> None:
    flag = "拒答" if res.source == "refusal" else ("OK " if res.ok else "注意")
    print(f"\n[{flag}] intent={res.intent.value} "
          f"conf={res.context.route.confidence if res.context.route else '-'} "
          f"source={res.source} {res.elapsed_ms:.0f}ms")
    print("-" * 72)
    for line in res.text.splitlines():
        print("  " + line)
    if res.domain_entry:
        print(f"  (凭据:本地知识条目 {res.domain_entry})")
    if res.verdict and not res.verdict.ok:
        print("  (校验未通过)" + ";".join(res.verdict.errors))
    if res.verdict and res.verdict.warnings:
        for warning in res.verdict.warnings:
            print(f"  ⚠ {warning}")
    report = res.context.meta.get("safety")
    if report is not None and getattr(report, "level", "ok") != "ok":
        print(f"  安全判定:{report.level}({report.labels})")
    if show_trace:
        print("\n  每一跳:")
        print(res.trace_report())
    print()


def cmd_health(kb_path: str | None = None) -> int:
    """体检:这一版没有模型可查,查的是领域包、安全规则、事实库与链路。"""
    print("=" * 72)
    print("数据包体检")
    print("=" * 72)
    checks: list[tuple[str, bool, str]] = []

    checks.append(("数据包身份", bool(PACK.id and PACK.name), f"{PACK.id} / {PACK.name}"))
    checks.append(("数据包文件", PACK.source.is_file(), str(PACK.source)))
    checks.append(("数据包来源", True, packdata.describe()))
    problems = PACK.validate()
    checks.append((
        "数据包自检",
        not problems,
        (f"{len(PACK.entries)} 条条目通过" if PACK.entries
         else ("结构化知识包(0 条问答条目)" if PACK.has_structure else "0 条条目"))
        if not problems else ";".join(problems[:5]),
    ))

    # 单核的前提是"数据包只有数据、没有代码":数据包里出现 .py 说明又回到复制副本的老路。
    # tests/ 例外 —— 那是给这个数据包写的测试,不是引擎的一部分。
    pack_root = packdata.active_pack_dir()
    stray_code: list[str] = []
    for path in pack_root.rglob("*.py"):
        rel = path.relative_to(pack_root)
        if "tests" in rel.parts:
            continue
        stray_code.append(rel.as_posix())
    stray_code.sort()
    checks.append((
        "代码/数据分离",
        not stray_code,
        "数据包只有数据(核心代码在 smallai/)" if not stray_code
        else "数据包里混进了代码:" + "、".join(stray_code[:3]),
    ))

    bad = rule_problems()
    checks.append((
        "安全规则",
        not bad,
        f"拒答 {PACK.safety_counts()[0]} / 提醒 {PACK.safety_counts()[1]}"
        if not bad else ";".join(bad[:5]),
    ))

    # 样例覆盖:每条条目原题都必须能检回它自己(PACK.validate 已做,这里单独报一条)
    coverage_bad = []
    for entry in PACK.entries:
        hit = PACK.lookup(entry.question)
        if hit.entry is None or hit.entry.id != entry.id:
            coverage_bad.append(entry.question)
    checks.append(("原题覆盖", not coverage_bad, "全部条目可检索" if not coverage_bad else f"{len(coverage_bad)} 条检不回自己"))

    if PACK.has_structure:
        checks.append((
            "结构化知识",
            True,
            f"事实 {len(PACK.facts)} / 规则 {len(PACK.rules)} / 关系 {len(PACK.relations)}",
        ))

    # 模型:确认这一版真的没有模型接入
    no_model = True
    detail_model = "没有模型端口(adapters 已删除)"
    try:
        import importlib.util

        if importlib.util.find_spec("smallai.adapters") is not None:
            no_model = False
            detail_model = "仍能找到 smallai.adapters,请确认删干净了"
    except (ImportError, ValueError):
        pass
    checks.append(("无模型依赖", no_model, detail_model))

    # 链路:真跑一次,规则内的算术与一次领域问答都要通
    try:
        pipe = build_pipeline(kb_path=kb_path)
        calc = pipe.ask("12*(3+4)")
        if PACK.entries:
            domain = pipe.ask(PACK.sample_questions(1)[0])
            ok_chain = calc.ok and "84" in calc.text and domain.ok and domain.domain_entry is not None
            detail_chain = f"calc={calc.text.splitlines()[0]} / domain_entry={domain.domain_entry}"
        elif PACK.has_structure:
            # 只有事实/规则、没有问答条目也是合法的:只验算术这一跳
            ok_chain = calc.ok and "84" in calc.text
            detail_chain = f"calc={calc.text.splitlines()[0]} / 结构化知识包(无问答条目)"
        else:
            # 记忆数据包还没被教过东西:空着是正常状态,只验算术那一跳
            ok_chain = calc.ok and "84" in calc.text
            detail_chain = f"calc={calc.text.splitlines()[0]} / 还没有条目(等你教我)"
        checks.append(("端到端链路", ok_chain, detail_chain))
        facts = pipe.logic.store.count()
        rules = len(pipe.logic.store.rules)
        kb_file, kb_where = packdata.data_sources("kb.json", default_kb_path())
        checks.append(("事实库", facts > 0, f"{facts} 条事实 / {rules} 条规则({kb_file.name}·{kb_where})"))
    except Exception as exc:                                  # noqa: BLE001
        checks.append(("端到端链路", False, f"{type(exc).__name__}: {exc}"))

    ok_all = True
    for name, ok, detail in checks:
        mark = "OK  " if ok else "缺失"
        ok_all = ok_all and ok
        print(f"  [{mark}] {name:<10} {detail}")
    print()
    print("  建议:全 OK 就能直接问问题;数据包自检不过时,先照上面的问题清单改 pack.json")
    print()
    return 0 if ok_all else 1


def cmd_guard(text: str, as_json: bool = False) -> int:
    """只做安全判定:拒答/灰区/放行,并交代命中了哪条规则。"""
    if not text or not text.strip():
        print("用法:python experts/core/run.py --pack <数据包> --guard \"<一段文本>\"")
        return 2
    report = assess(text)
    if as_json:
        print(json.dumps(
            {
                "pack": {"id": PACK.id, "name": PACK.name},
                "level": report.level,
                "labels": report.labels,
                "rule_ids": list(report.rule_ids),
                "advice": list(report.advice()),
                "text": report.render(),
            },
            ensure_ascii=False,
            indent=2,
        ))
    else:
        print("=" * 72)
        print("安全判定")
        print("=" * 72)
        print(report.render())
        if report.level == "refuse":
            print()
            print(report.refusal_text())
        print()
    return 1 if report.level == "refuse" else 0


def cmd_demo(show_trace: bool, kb_path: str | None = None) -> int:
    pipeline = build_pipeline(kb_path=kb_path)
    print(_banner(pipeline))
    cases = list(PACK.sample_questions()) + BASE_CASES + OFFTOPIC_CASES + SAFETY_CASES
    for case in cases:
        print(f"\n>>> 用户:{case}")
        res = pipeline.ask(case)
        _print_result(res, show_trace)
    print("=" * 72)
    print("preset 用例跑完。注意看四类结果:领域条目答出来的 / 规则算出来或推出来的 /")
    print("如实说没找到或不是我的领域的 / 被安全闸拦下或加了提醒的。")
    return 0


def cmd_interactive(show_trace: bool, kb_path: str | None = None) -> int:
    pipeline = build_pipeline(kb_path=kb_path)
    print(_banner(pipeline))
    while True:
        try:
            raw = input("你 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见。")
            return 0
        if not raw:
            continue
        lowered = raw.lower()
        if lowered in ("exit", "quit", ":q"):
            print("再见。")
            return 0
        if raw == ":demo":
            for case in list(PACK.sample_questions()) + BASE_CASES + OFFTOPIC_CASES:
                res = pipeline.ask(case)
                print(f"\n>>> {case}")
                _print_result(res, show_trace)
            continue
        if raw == ":health":
            cmd_health(kb_path)
            continue
        if raw == ":kb":
            cmd_kb(pipeline)
            continue
        if raw == ":domain":
            cmd_domain()
            continue
        if lowered.startswith(":guard "):
            cmd_guard(raw[7:].strip())
            continue
        res = pipeline.ask(raw)
        _print_result(res, show_trace)


def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="smallai",
        description=f"{persona.NAME} · 本地数据包小 AI:{PACK.name}(规则路由 → 理解 → 校验 → 生成,不接模型)",
    )
    p.add_argument("query", nargs="*", help="单次提问内容(省略则进入对话模式)")
    p.add_argument("--pack", metavar="目录", default=None,
                   help="数据包目录(含 pack.json;省略 = SMALLAI_PACK / 当前目录向上找 / 核心自带示例包)")
    p.add_argument("--kb", dest="kb_path", default=None,
                   help="事实库 JSON 路径(省略 = 数据包的 kb.json,没有就用核心自带的)")
    p.add_argument("--kb-stats", action="store_true", help="只看事实库统计与规则清单")
    p.add_argument("--semantics", action="store_true", help="词向量自检:类比准确率 + 样例")
    p.add_argument("--semantics-stats", action="store_true", help="只看向量空间统计与邻居样例")
    p.add_argument("--domain", action="store_true", help="看数据包名片(能答什么 / 不做什么)")
    p.add_argument("--guard", metavar="文本", default=None,
                   help="只做安全判定(返回码:0=放行/提醒,1=拒答)")
    p.add_argument("--trace", action="store_true", help="打印每一跳的账")
    p.add_argument("--demo", action="store_true", help="跑预设用例")
    p.add_argument("--health", action="store_true", help="数据包体检")
    p.add_argument("--json", action="store_true", help="机器可读输出(单次提问 / --guard 时)")
    p.add_argument("--version", action="version",
                   version=f"smallai {__version__} · {persona.NAME} · 数据包 {PACK.id}")
    return p


def _scrape_pack(argv: list[str]) -> str | None:
    """在 argparse 之前先把 --pack 捞出来。

    为什么要抢跑:数据包决定"这一轮用哪个包",而 argparse 的描述/版本号里要写它
    的名字。不抢跑的话,`--pack` 生效时帮助信息已经印完了。
    """
    for index, item in enumerate(argv):
        if item == "--pack" and index + 1 < len(argv):
            return argv[index + 1]
        if item.startswith("--pack="):
            return item.split("=", 1)[1]
    return None


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    raw_argv = list(sys.argv[1:] if argv is None else argv)

    chosen = _scrape_pack(raw_argv)
    if chosen:
        try:
            packdata.set_pack_dir(chosen)
        except FileNotFoundError as exc:
            print(f"[数据包] {exc}")
            return 2

    try:
        args = build_argparser().parse_args(raw_argv)
    except FileNotFoundError as exc:                  # 环境变量指错时也别抛栈
        print(f"[数据包] {exc}")
        return 2

    if args.guard is not None:
        return cmd_guard(args.guard, as_json=args.json)
    if args.domain:
        return cmd_domain()
    if args.health:
        return cmd_health(args.kb_path)
    if args.kb_stats:
        return cmd_kb(build_pipeline(kb_path=args.kb_path))
    if args.semantics or args.semantics_stats:
        from .semantics import build_semantics

        return cmd_semantics(build_semantics(), selfcheck=args.semantics)
    if args.demo:
        return cmd_demo(args.trace, args.kb_path or str(default_kb_path()))

    if not args.query:
        return cmd_interactive(args.trace, args.kb_path)

    pipeline = build_pipeline(kb_path=args.kb_path)
    query = " ".join(args.query)
    res = pipeline.ask(query)
    if args.json:
        report = res.context.meta.get("safety")
        print(json.dumps(
            {
                "pack": {"id": PACK.id, "name": PACK.name},
                "text": res.text,
                "ok": res.ok,
                "intent": res.intent.value,
                "confidence": res.context.route.confidence if res.context.route else None,
                "source": res.source,
                "loops": res.loops,
                "elapsed_ms": round(res.elapsed_ms, 2),
                "domain": res.context.meta.get("domain"),
                "safety": None if report is None else {
                    "level": report.level,
                    "labels": report.labels,
                    "rule_ids": list(report.rule_ids),
                },
                "logic": None if res.context.logic is None else {
                    "kind": res.context.logic.kind,
                    "outcome": res.context.logic.outcome,
                    "answer": res.context.logic.answer if isinstance(
                        res.context.logic.answer, (bool, int, float, str, type(None))) else str(res.context.logic.answer),
                    "text": res.context.logic.text,
                    "basis": res.context.logic.basis,
                    "facts": res.context.logic.store_size,
                },
                "semantic": None if not res.context.meta.get("semantic") else {
                    "kind": res.context.meta["semantic"].kind,
                    "outcome": res.context.meta["semantic"].outcome,
                    "answer": res.context.meta["semantic"].answer,
                    "text": res.context.meta["semantic"].text,
                    "basis": res.context.meta["semantic"].basis,
                    "candidates": res.context.meta["semantic"].candidates,
                    "rejected": res.context.meta["semantic"].rejected,
                },
                "verdict": {
                    "ok": res.verdict.ok,
                    "errors": res.verdict.errors,
                    "warnings": res.verdict.warnings,
                } if res.verdict else None,
                "trace": [
                    {
                        "stage": s.stage.value, "actor": s.actor, "ok": s.ok,
                        "detail": s.detail, "elapsed_ms": s.elapsed_ms,
                    }
                    for s in res.context.trace
                ],
            },
            ensure_ascii=False,
            indent=2,
        ))
        return 0

    _print_result(res, args.trace)
    return 0 if res.ok else 0     # 澄清/拒答也是正常结果,不算进程失败


def cmd_semantics(index, selfcheck: bool = True, samples: list[str] | None = None) -> int:
    """看词向量:空间统计 + 自检准确率 + 样例(离线训练,运行期只查表)。"""
    print("=" * 72)
    print("语义层:词向量 + 关系图(离线训练,运行期纯查表)")
    print("=" * 72)
    print(f"  向量文件   : {index.source}")
    print(f"  词表       : {index.vocab_size} 个词")
    print(f"  维数       : {index.dim}(统计块 + 结构块拼接)")
    print(f"  关系边     : {len(index.relations)} 条")
    print(f"  结构特征   : {', '.join(index.structural_features) or '(无)'}")
    if not index.loaded:
        print("\n  向量文件不在:基础版里用 `python tools/train_vectors.py` 训练一次。")
        return 1

    if selfcheck:
        result = index.self_check()
        total = result["total"] or 1
        print(f"\n  类比自检({total} 组,经典形态 a-b+c≈d):")
        print(f"    纯向量最近邻     : {result['pure_hits']}/{total}")
        print(f"    加一致性检查     : {result['checked_hits']}/{total}")
        print("\n  逐条:")
        for line in result["detail"]:
            print("    " + line)

    for query in samples or ["国王 - 男人 + 女人"]:
        from .semantics.parse import parse_semantic

        parsed = parse_semantic(query)
        print(f"\n  {query}")
        if not parsed.ok:
            print("    解析失败:" + parsed.reason)
            continue
        if parsed.kind == "neighbors":
            word = parsed.word or ""
            nearby = index.nearest(word, 5)
            print("    最近邻: " + ("、".join(f"{n.word}({n.score:.2f})" for n in nearby) or "(词表里没有这个词)"))
            related = index.related(word)
            if related:
                print("    关系图: " + "、".join(f"{r}·{w}" for r, w in related[:8]))
            continue
        plain = index.analogy(parsed.a or "", parsed.b or "", parsed.c or "", consistency_check=False)
        strict = index.analogy(parsed.a or "", parsed.b or "", parsed.c or "")
        print("    纯向量: " + "、".join(f"{c.word}({c.score:.2f})" for c in plain.ranked()))
        print("    一致性: " + ("、".join(f"{c.word}({c.score:.2f})" for c in strict.ranked()) or "(无)"))
        if strict.rejected:
            print("    被拒: " + "、".join(f"{c.word}({c.reason})" for c in strict.rejected[:4]))
    return 0


def _force_utf8() -> bool:
    changed = False
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")   # type: ignore[union-attr]
            changed = True
        except Exception:                                    # noqa: BLE001
            pass
    return changed


if __name__ == "__main__":
    raise SystemExit(main())
