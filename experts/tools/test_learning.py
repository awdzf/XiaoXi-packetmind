"""学习能力测试：教一遍公式/常数，看它能不能**换成别的数、换个键盘还算得对**。

为什么不只看"能不能算"：教一次算一次，可能是把这组数背下来了。真正要量的是六件事：

1. **记得住**：公式和常数都教得进去；
2. **会迁移**：同一个公式，代入从没教过的数，结果仍然对（用独立算出的期望值比）；
3. **会说不会**：参数个数不对、给了非数字、除以零 —— 要如实讲，不能编一个数；
4. **不挑键盘**：全角/半角/× ÷ −/上标 混着打都能用（中英文输入法随手切）；
5. **常数可用**：内置常数、用户自定义常数、以及"变量盖住常数"的优先级都对；
6. **答得出处**：教过的公式/常数要像其它知识一样，在正常对话里查得到。

跑法：python tools/test_learning.py
输出是一张计分表，任何一项没过都会逐条列出是哪条公式、哪个数。
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from framework import GoChat, formula  # noqa: E402

#: (教学行, [(代入值, 期望值), ...])。期望值全部独立算好，不从被测系统里取。
CURRICULUM = [
    ("圆的面积~π*r^2:r", [("1", 3.141593), ("3", 28.274334), ("10", 314.159265)]),
    ("圆周长~2*π*r:r", [("1", 6.283185), ("2.5", 15.707963), ("0", 0.0)]),
    ("圆柱体积~π*r^2*h:r,h", [("1,1", 3.141593), ("2,5", 62.831853), ("3,2", 56.548668)]),
    ("三角形面积~0.5*b*h:b,h", [("4,3", 6.0), ("10,0.5", 2.5), ("7,7", 24.5)]),
    ("平均数~(a+b+c)/3:a,b,c", [("1,2,3", 2.0), ("10,20,60", 30.0), ("0,0,0", 0.0)]),
    ("摄氏转华氏~c*9/5+32:c", [("0", 32.0), ("100", 212.0), ("-40", -40.0)]),
    ("复利本息~p*(1+r)^n:p,r,n", [("100,0.1,2", 121.0), ("1000,0.05,1", 1050.0)]),
    ("平方和~a^2+b^2:a,b", [("3,4", 25.0), ("5,12", 169.0)]),
]

#: 中英文键盘混着打：教学行与调用行都可能出现全角、×、÷、上标。
MIXED_SYMBOLS = [
    ("圆的面积～π×ｒ²：ｒ", "圆的面积：３", 28.274334),
    ("矩形面积～ａ×ｂ：ａ,ｂ", "矩形面积：２，５", 10.0),
    ("半圆周长～π×ｒ＋２×ｒ：ｒ", "半圆周长：１", 5.141593),
    ("立方体体积～ａ³：ａ", "立方体体积：２", 8.0),
]

#: 教学时**必须被拒**的行（写错了要当场说，而不是存成一句知识）。
SHOULD_REJECT = [
    ("勾股~a^2+b^2=c^2:a,b", "带等号的方程"),
    ("动能~0.5*m*v^2:m", "少声明了变量 v"),
    ("面积~r*h:r,h,z", "声明了没用到的 z"),
    ("面积~根号(2):r", "有函数调用/中文字符"),
    ("面积~sqrt(2):r", "函数调用"),
]

#: 调用时**必须如实说"算不了"**的输入。
SHOULD_FAIL = [
    ("圆的面积:3,4", "参数多了"),
    ("圆的面积:", "没给值"),
    ("圆的面积:abc", "不是数"),
    ("圆的面积:1/0", "参数本身除以零"),
]

#: 学会之后不该被打扰的日常提问。
UNTOUCHED = ["向量场是什么?", "引力波是什么?", "什么是群论"]

#: 内置常数抽查：(写法, 期望值)。
BUILTIN_PROBES = [
    ("pi", 3.141592653589793),
    ("phi", 1.618033988749895),
    ("hbar", 1.054571817e-34),
    ("g_n", 9.80665),
]


def _close(actual: float | None, expected: float) -> bool:
    return actual is not None and abs(actual - expected) <= max(1e-5, abs(expected) * 1e-6)


def _result_of(summary: str) -> float | None:
    for line in summary.splitlines():
        if "=" in line and "（" in line:
            try:
                return float(line.rsplit("=", 1)[1].strip().split("（")[0].strip())
            except ValueError:
                return None
    return None


def main() -> None:
    work = Path("..").resolve() / "_tmp_learning"
    shutil.rmtree(work, ignore_errors=True)
    shutil.copytree(ROOT / "packs", work / "packs")

    labels = ("记得住", "会迁移", "不挑键盘", "常数可用", "会拒绝", "会认怂", "不捣乱")
    score = {key: 0 for key in labels}
    total = {key: 0 for key in labels}
    problems: list[str] = []
    chat = GoChat(packs_root=work)

    def check(label: str, ok: bool, message: str) -> None:
        total[label] += 1
        score[label] += int(ok)
        if not ok:
            problems.append(message)

    print("=" * 70)
    print("1. 教学：能不能把每个公式记住")
    print("=" * 70)
    for body, _cases in CURRICULUM:
        parsed = formula.parse_teaching(body, chat.user_constants())
        ok = parsed is not None
        check("记得住", ok, f"教学失败：{body} —— {formula.explain_teaching(body)}")
        if ok:
            assert parsed is not None
            chat.teach_formula(parsed, raw=f"# {body}")
            print(f"  [OK] {parsed.name:8s} = {parsed.expr}   变量 {', '.join(parsed.vars)}")
        else:
            print(f"  [NG] {body}")

    print()
    print("=" * 70)
    print("2. 迁移：教过的公式，换没教过的数还算得对吗")
    print("=" * 70)
    for body, cases in CURRICULUM:
        name = body.split("~")[0]
        for args, expected in cases:
            query = f"{name}:{args}"
            got = _result_of(chat.ask(query).summary)
            ok = _close(got, expected)
            check("会迁移", ok, f"迁移失败：{query} 期望 {expected} 实得 {got}")
            print(f"  [{'OK' if ok else 'NG'}] {query:22s} 期望 {expected:<12} 实得 {got}")

    print()
    print("=" * 70)
    print("3. 不挑键盘：全角 / × ÷ / 上标，混着打")
    print("=" * 70)
    for teach_line, call_line, expected in MIXED_SYMBOLS:
        parsed = formula.parse_teaching(teach_line, chat.user_constants())
        ok_parse = parsed is not None
        if ok_parse:
            assert parsed is not None
            chat.teach_formula(parsed, raw=f"# {teach_line}")
            got = _result_of(chat.ask(call_line).summary)
            ok = _close(got, expected)
            check("不挑键盘", ok, f"混打失败：{call_line} 期望 {expected} 实得 {got}")
            print(f"  [{'OK' if ok else 'NG'}] {teach_line:28s} → {call_line:16s} 实得 {got}")
        else:
            check("不挑键盘", False, f"混打教学失败：{teach_line} —— {formula.explain_teaching(teach_line)}")
            print(f"  [NG] {teach_line}")

    print()
    print("=" * 70)
    print("4. 常数：内置的能用，自己定的也能用，变量盖得住")
    print("=" * 70)
    for spelling, expected in BUILTIN_PROBES:
        value, problem = formula.parse_number(spelling)
        ok = problem == "" and _close(value, expected)
        check("常数可用", ok, f"内置常数 {spelling} 期望 {expected} 实得 {value} {problem}")
        print(f"  [{'OK' if ok else 'NG'}] 内置 {spelling:6s} = {value!r}")

    user_constant, why = formula.parse_constant("c_light=2.998e8")
    ok = user_constant is not None
    check("常数可用", ok, f"自定义常数没解析出来：{why}")
    if ok:
        assert user_constant is not None
        chat.teach_constant(user_constant, raw="# c_light=2.998e8")
        energy = formula.parse_teaching("能量~m*c_light^2:m", chat.user_constants())
        ok = energy is not None
        if ok:
            assert energy is not None
            chat.teach_formula(energy, raw="# 能量~m*c_light^2:m")
            got = _result_of(chat.ask("能量:2").summary)
            expected = 2 * (2.998e8) ** 2
            good = _close(got, expected)
            check("常数可用", good, f"用自定义常数的公式算错：期望 {expected} 实得 {got}")
            print(f"  [{'OK' if good else 'NG'}] 自定义 c_light → 能量:2 = {got}")
        else:
            check("常数可用", False, "用自定义常数的公式没能教进去")

    shadow = formula.parse_teaching("周长~2*pi*r:r", {"pi": 3.0})
    got = formula.evaluate(shadow, ("1",), {"pi": 3.0}).value if shadow else None
    ok = _close(got, 6.0)
    check("常数可用", ok, f"用户常数没盖住内置 pi：期望 6.0 实得 {got}")
    print(f"  [{'OK' if ok else 'NG'}] 用户 pi=3 盖住内置 → 周长:1 = {got}")

    print()
    print("=" * 70)
    print("5. 会说不会：写错的公式必须被拒，并说清原因")
    print("=" * 70)
    for body, why in SHOULD_REJECT:
        parsed = formula.parse_teaching(body, chat.user_constants())
        problem = formula.explain_teaching(body, chat.user_constants())
        ok = parsed is None and bool(problem)
        check("会拒绝", ok, f"该拒没拒/没理由：{body}（{why}）")
        print(f"  [{'OK' if ok else 'NG'}] 拒绝 {body:26s} 理由：{problem or '(没给理由!)'}")

    print()
    print("=" * 70)
    print("6. 算不了的时候如实说，不编数字")
    print("=" * 70)
    for query, why in SHOULD_FAIL:
        summary = chat.ask(query).summary
        produced_number = " = " in (summary.splitlines()[0] if summary.splitlines() else "")
        explained = ("要" in summary and "个值" in summary) or "算不了" in summary or "不是一个能算的数" in summary
        ok = (not produced_number) and explained
        check("会认怂", ok, f"没如实说：{query}（{why}）")
        first = summary.splitlines()[0] if summary.splitlines() else ""
        print(f"  [{'OK' if ok else 'NG'}] {query:16s} -> {first[:56]}")

    print()
    print("=" * 70)
    print("7. 不捣乱：日常问答不变形；换进程公式还在；教过的查得到")
    print("=" * 70)
    for query in UNTOUCHED:
        first = chat.ask(query).summary.splitlines()[0]
        ok = "公式" not in first and "= " not in first[:12]
        check("不捣乱", ok, f"日常问答被影响：{query}")
        print(f"  [{'OK' if ok else 'NG'}] {query:14s} -> {first[:52]}")

    for question, needle in (("圆的面积怎么算", "π"), ("圆的面积公式是什么", "π"),
                             ("c_light是多少", "c_light")):
        summary = chat.ask(question).summary
        ok = needle in summary
        check("不捣乱", ok, f"教过的东西在正常对话里查不到：{question}")
        print(f"  [{'OK' if ok else 'NG'}] {question:16s} -> {summary.splitlines()[0][:52]}")

    restarted = GoChat(packs_root=work)
    persisted = "28.274334" in restarted.ask("圆的面积:3").summary
    print(f"  [{'OK' if persisted else 'NG'}] 新进程里 圆的面积:3 仍然算得对（写进了 pack.json）")
    if not persisted:
        problems.append("重启后公式丢了")

    shutil.rmtree(work, ignore_errors=True)

    print()
    print("=" * 70)
    print("计分")
    print("=" * 70)
    grand_ok = grand_total = 0
    for key in labels:
        grand_ok += score[key]
        grand_total += total[key]
        print(f"  {key:8s} {score[key]:2d}/{total[key]:<2d}")
    print(f"  {'合计':8s} {grand_ok}/{grand_total}   （另有 1 项持久化：{'通过' if persisted else '未过'}）")
    if problems:
        print("\n未通过明细：")
        for item in problems:
            print("  · " + item)
    raise SystemExit(0 if not problems and persisted else 1)


if __name__ == "__main__":
    main()
