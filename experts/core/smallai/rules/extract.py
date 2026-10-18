"""抽取工具:路由层与理解层共用。

把"从自由文本里抠出算式 / 数值 / 单位"单独成文件,因为它最容易写错,
也最值得单独测 —— 抠错了,后面三层全都在错的前提上工作。

两个已知边界(写下来,不装作支持):
  · 多步自然语言算式("2+3再乘4")只能抽到第一段算式,不做语义串联;
  · 单位只认**紧邻数值**的那个词,绝不隔空猜(猜单位 = 换算错误第一大来源)。
"""

from __future__ import annotations

import re

# 中文口语运算符:抽算式前做一次保守替换(把连接词吃掉,免得破坏数字邻接)
OPERATOR_ALIASES: tuple[tuple[str, str], ...] = (
    ("乘以", "*"), ("乘", "*"),
    ("除以", "/"), ("除", "/"),
    ("加上", "+"), ("加", "+"),
    ("减去", "-"), ("减", "-"),
    ("的平方", "**2"), ("平方", "**2"),
    ("再", ""), ("然后", ""), ("之后", ""),
    ("×", "*"), ("÷", "/"),
)

# 单位字符集:中文字 / 拉丁字母 / 度数符号(摄氏华氏常写成 °C ℃)
_UNIT_CHARS = r"\u4e00-\u9fffA-Za-z°℃℉"
UNIT_PATTERN = f"[{_UNIT_CHARS}]{{0,4}}"

CJK_DIGITS = {"零": "0", "一": "1", "二": "2", "两": "2", "三": "3", "四": "4",
              "五": "5", "六": "6", "七": "7", "八": "8", "九": "9", "十": "10"}


def normalize_operators(text: str) -> str:
    t = text
    for src, dst in OPERATOR_ALIASES:
        t = t.replace(src, dst)
    return t


def extract_expression(text: str) -> str | None:
    """抠出**最长**的合法算术表达式(含括号、乘方、链式运算)。

    从左到右扫;遇数字或左括号开始收集,后面只接 运算符 / 数字 / 括号,
    遇其它字符就停。收集完要求"含运算符且括号配平且不以运算符收尾",否则丢弃。
    """
    t = normalize_operators(text)
    n = len(t)
    i = 0
    best: str | None = None
    while i < n:
        ch = t[i]
        if not (ch.isdigit() or (ch == "(" and i + 1 < n and t[i + 1] in "0123456789(+-")):
            i += 1
            continue

        out: list[str] = []
        depth = 0
        j = i
        expect_operand = True
        has_op = False
        seen_digit = False
        while j < n:
            cj = t[j]
            if cj.isdigit() or cj == ".":
                out.append(cj)
                seen_digit = True
                expect_operand = False
                j += 1
                continue
            if cj == "(" and expect_operand:
                depth += 1
                out.append(cj)
                j += 1
                continue
            if cj == ")" and depth > 0 and not expect_operand:
                depth -= 1
                out.append(cj)
                j += 1
                continue
            if cj == "*" and j + 1 < n and t[j + 1] == "*" and not expect_operand:
                out.append("**")
                has_op = True
                expect_operand = True
                j += 2
                continue
            if cj in "+-*/%":
                if expect_operand:
                    # 只在"这里本该是操作数"时接受一元正负号
                    if cj in "+-" and (not out or out[-1] in ("(", "+", "-", "*", "/", "%", "**")):
                        out.append(cj)
                        j += 1
                        continue
                    break
                out.append(cj)
                has_op = True
                expect_operand = True
                j += 1
                continue
            break

        candidate = "".join(out)
        if depth == 0 and seen_digit and has_op and not expect_operand:
            if best is None or len(candidate) > len(best):
                best = candidate
        i = j if j > i else i + 1
    return best


def extract_value_unit(text: str) -> tuple[float | None, str | None]:
    """抽『数值 + 紧跟其后的字符片段』。返回 (值, 单位原文);没有就是 None。

    例:'3公斤换算成斤' -> (3.0, '公斤换算');'五斤换算成公斤' -> (5.0, '斤换算成')

    注意:这里**故意**多切一点尾巴,把"紧邻数值的那串字"整段交出去,
    由理解层用词表按**最长真实单位**匹配(公斤 必须赢过 斤)。
    """
    m = re.search(rf"(\d+(?:\.\d+)?)\s*({UNIT_PATTERN})", text)
    if m:
        value = float(m.group(1))
        return (int(value) if value.is_integer() else value), (m.group(2) or None)

    m2 = re.search(rf"([零一二两三四五六七八九十]+)\s*({UNIT_PATTERN})", text)
    if m2:
        n = cjk_digit_to_int(m2.group(1))
        if n is not None:
            return n, (m2.group(2) or None)
    return None, None


def extract_target_unit(text: str) -> str | None:
    """抽换算目标:取换算动词后面的那个单位原文。"""
    m = re.search(
        rf"(?:换算成|换算为|换成|转换为|转成|转换成|等于多少|是多少|到)\s*({UNIT_PATTERN})",
        text,
    )
    if m and m.group(1):
        return m.group(1)
    return None


def cjk_digit_to_int(text: str) -> int | None:
    """中文数字转整数(只处理 0-99,够用就好)。"""
    t = (text or "").strip()
    if not t:
        return None
    if t in CJK_DIGITS:
        return int(CJK_DIGITS[t])
    m = re.match(r"^十([零一二三四五六七八九])$", t)
    if m:
        return 10 + int(CJK_DIGITS[m.group(1)])
    m = re.match(r"^([一二三四五六七八九])十([零一二三四五六七八九])?$", t)
    if m:
        tens = int(CJK_DIGITS[m.group(1)])
        ones = int(CJK_DIGITS[m.group(2)]) if m.group(2) else 0
        return tens * 10 + ones
    return None
