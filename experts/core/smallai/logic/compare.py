"""比较:数值与"带单位的量"之间的大小关系。

单独成文件的原因:它是**逻辑能力里最容易出错的一环** ——
3 公斤和 2 斤比大小,必须先换算到同一量纲;量纲不同的两个量
(3 公斤 vs 2 米)之间没有大小关系,这时候要说"没法比",不能硬比。

所有比较结果都带一段 `basis`(人话依据),生成层直接引用它,
不允许自己另算一遍 —— 与算术同一条纪律。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..rules.lexicon import (
    ALIASES_BY_LENGTH,
    COMPARE_WORDS,
    SUPERLATIVE_WORDS,
    UNIT_ALIASES,
    UNIT_TO_KIND,
)
from ..validation.units import convert

RELATION_TEXT = {
    "gt": "大于", "lt": "小于", "ge": "大于等于", "le": "小于等于",
    "eq": "等于", "ne": "不等于",
}

_SYMBOL_RE = re.compile(r"(>=|<=|==|!=|>|<|=)")


@dataclass
class Quantity:
    """带单位的量。单位为空表示纯数字。"""

    value: float
    unit: str | None = None
    raw: str = ""

    @property
    def kind(self) -> str:
        return UNIT_TO_KIND.get(self.unit or "", "number")

    def describe(self) -> str:
        return f"{_fmt(self.value)} {self.unit}" if self.unit else _fmt(self.value)

    def in_base(self) -> float:
        """换成同量纲的基准值,便于比较。纯数字直接返回自身。"""
        if not self.unit:
            return self.value
        if self.kind == "temperature":
            # 温度是仿射的,先统一成摄氏度再比
            return convert(self.value, self.unit, "c")
        base = {"length": "m", "mass": "g", "time": "s", "data": "byte"}[self.kind]
        return convert(self.value, self.unit, base)


def _fmt(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def match_unit_alias(raw: str) -> str | None:
    """把一段字符匹配到规范单位(最长优先)。与理解层同一套词表。"""
    if not raw:
        return None
    text = raw.strip().lower()
    for alias in ALIASES_BY_LENGTH:
        if text == alias or text.endswith(alias):
            return UNIT_ALIASES[alias]
    return None


def extract_quantities(text: str) -> list[Quantity]:
    """抽出一句话里所有的"数值+单位"。

    单位用**词表直接匹配**而不是"抓一串汉字再看像不像单位" ——
    后者会把 "100华氏度比50摄氏度" 里的 "华氏度比" 抓成单位,导致两边都退化
    成纯数字比较(于是 100 > 50 就被报成"100华氏度比50摄氏度热")。
    这是实测踩到的坑,写在这里。
    """
    out: list[Quantity] = []
    consumed_until = 0
    for m in re.finditer(r"\d+(?:\.\d+)?", text):
        start, end = m.start(), m.end()
        if start < consumed_until:
            continue
        unit = match_unit_at(text, end)
        unit_len = len(unit) if unit else 0
        raw = text[start: end + unit_len]
        out.append(
            Quantity(
                value=float(m.group(0)),
                unit=UNIT_ALIASES[unit] if unit else None,
                raw=raw,
            )
        )
        consumed_until = end + unit_len
    return out


def match_unit_at(text: str, position: int) -> str | None:
    """在 position 处按**最长别名**匹配一个单位,返回别名原文(未归一)。

    只允许中间夹杂极少数连接字("的"、"水"),避免把动词当单位。
    """
    tail = text[position: position + 6]
    lowered = tail.lower()
    for alias in ALIASES_BY_LENGTH:
        if lowered.startswith(alias):
            return tail[: len(alias)]
    # 允许一个"的"字隔开,如 "3公斤的水"
    if lowered.startswith("的"):
        inner = lowered[1:]
        for alias in ALIASES_BY_LENGTH:
            if inner.startswith(alias):
                return tail[1: 1 + len(alias)]
    return None


def extract_relation(text: str) -> str | None:
    """抽比较关系:先看符号,再看中文比较词。"""
    m = _SYMBOL_RE.search(text)
    if m:
        return {"<": "lt", ">": "gt", "=": "eq", "<=": "le", ">=": "ge", "==": "eq", "!=": "ne"}[m.group(1)]
    for word, relation in sorted(COMPARE_WORDS, key=lambda kv: len(kv[0]), reverse=True):
        if "..." in word:
            continue
        if word in text:
            return relation
    return None


@dataclass
class CompareResult:
    ok: bool
    relation: str | None = None
    left: Quantity | None = None
    right: Quantity | None = None
    holds: bool | None = None
    basis: str = ""
    error: str = ""

    @property
    def relation_text(self) -> str:
        return RELATION_TEXT.get(self.relation or "", self.relation or "?")


def compare_quantities(left: Quantity, right: Quantity, relation: str) -> CompareResult:
    """比较两个量。量纲不同 -> 明确报"没法比",不硬判。"""
    if left.unit and right.unit and left.kind != right.kind:
        return CompareResult(
            ok=False,
            left=left, right=right, relation=relation,
            error=f"量纲不同:{left.describe()}({left.kind}) 与 {right.describe()}({right.kind}) 之间没有大小关系",
        )
    if not left.unit and right.unit:
        left = Quantity(left.value, right.unit)
    if left.unit and not right.unit:
        right = Quantity(right.value, left.unit)

    a, b = left.in_base(), right.in_base()
    tol = max(1e-9, abs(a) * 1e-12)
    if relation == "gt":
        holds = a > b + tol
    elif relation == "lt":
        holds = a < b + tol
    elif relation == "ge":
        holds = a >= b - tol
    elif relation == "le":
        holds = a <= b + tol
    elif relation == "eq":
        holds = abs(a - b) <= max(1e-9, abs(b) * 1e-12)
    elif relation == "ne":
        holds = abs(a - b) > max(1e-9, abs(b) * 1e-12)
    else:
        return CompareResult(ok=False, left=left, right=right, error=f"不认识的关系:{relation}")

    unit_note = ""
    if left.unit and right.unit and left.unit != right.unit:
        unit_note = f"(统一到 {left.kind} 基准后:{_fmt(a)} vs {_fmt(b)})"
    basis = f"{left.describe()} 与 {right.describe()} 比较 {RELATION_TEXT[relation]}{unit_note}"
    return CompareResult(
        ok=True, relation=relation, left=left, right=right, holds=holds, basis=basis
    )


@dataclass
class CompareParse:
    """一句话解析出的比较请求。"""

    ok: bool
    left: Quantity | None = None
    right: Quantity | None = None
    relation: str | None = None
    error: str = ""
    numbers_only: list[float] = field(default_factory=list)


def parse_comparison(text: str) -> CompareParse:
    """解析比较句,支持三种形态:

        A 比 B 重/大/多        -> 关系由形容词决定
        A > B / A 大于 B       -> 关系由符号/比较词决定
        A 和 B 哪个大          -> 问法给方向,但**等量要如实报等量**
    """
    relation = extract_relation(text)
    quantities = extract_quantities(text)
    superlative = any(word in text for word in SUPERLATIVE_WORDS)

    # 形态:A 比 B 重 -> 形容词给关系
    m = re.search(r"(.{1,8}?)\s*比\s*(.{1,8}?)\s*(重|大|多|高|长|快|轻|小|少|低|短|慢)", text)
    if m:
        relation = {"重": "gt", "大": "gt", "多": "gt", "高": "gt", "长": "gt", "快": "gt",
                    "轻": "lt", "小": "lt", "少": "lt", "低": "lt", "短": "lt", "慢": "lt"}[m.group(3)]

    if len(quantities) < 2:
        nums = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", text)]
        if len(nums) >= 2 and relation:
            left = Quantity(value=nums[0])
            right = Quantity(value=nums[1])
            return CompareParse(ok=True, left=left, right=right, relation=relation, numbers_only=nums[:2])
        return CompareParse(ok=False, error="没找到两个可以比较的量", numbers_only=nums)

    left, right = quantities[0], quantities[1]
    if relation is None:
        if superlative:
            # "1000米和1公里哪个大":先按等量比。
            # 若确实不等,再按"哪个更大"的方向重问一次 —— 这样
            # "哪个大" 既能如实报"两者相等",也不会把 1000米 vs 1里 答反。
            equal_check = compare_quantities(left, right, "eq")
            if equal_check.ok and equal_check.holds:
                relation = "eq"
            else:
                relation = "gt"
        else:
            relation = "gt"
    return CompareParse(ok=True, left=left, right=right, relation=relation)


def between(value: Quantity, low: Quantity, high: Quantity) -> CompareResult:
    """区间判断:low <= value <= high。"""
    if value.unit and low.unit and value.kind != low.kind:
        return CompareResult(ok=False, left=value, right=low, error="量纲不同,无法判断区间")
    a, b, c = value.in_base(), low.in_base(), high.in_base()
    holds = b <= a <= c
    basis = f"{value.describe()} 是否落在 [{low.describe()}, {high.describe()}] 区间内"
    return CompareResult(ok=True, relation="between", left=value, right=low,
                         holds=holds, basis=basis)
