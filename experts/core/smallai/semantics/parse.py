"""把"国王 - 男人 + 女人"这类话解析成向量算术。

两种写法都要认:
    符号式   国王 - 男人 + 女人      国王-男人+女人        a - b + c = ?
    口语式   国王减去男人加上女人     国王减男人加女人

还有一类"看邻居"的问法(不算术):
    国王 的近义词 / 和国王最近的词 / 国王 和谁像

解析原则与逻辑层一致:**宁可解析失败,不要解析出一个错的式子**。
失败时返回原因,由生成层给出可用写法,而不是硬凑一个 a-b+c。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# 词里的字:中文、字母数字(允许 米/kg/℃ 这类)
_WORD = r"[\u4e00-\u9fffA-Za-z0-9°℃℉]{1,12}"

# 口语运算符 -> 符号
MINUS_WORDS = ("减去", "减掉", "减少", "去掉", "去掉", "减")
PLUS_WORDS = ("加上", "加", "再")
EQ_WORDS = ("等于什么", "等于多少", "是什么", "是谁", "=? ", "= ?", "?")

NEIGHBOR_PATTERNS = (
    re.compile(rf"^{_WORD}\s*(?:的)?\s*(?:近义词|同义词|近义|相关词|相似词)\s*(?:有哪些|是什么|是啥)?$"),
    re.compile(rf"^(?:和|跟|与)\s*{_WORD}\s*(?:最)?(?:近|相似|像|相关)\s*(?:的)?(?:词|有哪些|是什么)?$"),
    re.compile(rf"^{_WORD}\s*(?:和|跟|与)\s*谁\s*(?:最)?(?:像|近|相似)$"),
    re.compile(rf"^(?:看|查|给我看)\s*(?:一下)?\s*{_WORD}\s*的\s*(?:邻居|相关词)$"),
)


@dataclass
class SemanticQuery:
    """解析结果。kind=analogy 时 a/b/c 一定有值;kind=neighbors 时只有 word。"""

    ok: bool
    kind: str = "analogy"          # analogy / neighbors
    a: str | None = None
    b: str | None = None
    c: str | None = None
    word: str | None = None
    reason: str = ""
    raw: str = ""
    steps: list[str] = field(default_factory=list)

    def described(self) -> str:
        if self.kind == "analogy" and self.a and self.b and self.c:
            return f"{self.a} - {self.b} + {self.c}"
        if self.word:
            return f"与 {self.word} 最近的词"
        return self.raw


def parse_semantic(text: str) -> SemanticQuery:
    """解析语义查询。失败返回 ok=False + reason。"""
    raw = text.strip()
    cleaned = raw.rstrip("?？。!！ ").strip()

    # ---- 先看是不是"找邻居" ----
    for pattern in NEIGHBOR_PATTERNS:
        if pattern.match(cleaned):
            word = _extract_word(cleaned)
            if word:
                return SemanticQuery(ok=True, kind="neighbors", word=word, raw=raw)

    # ---- 符号式:a - b + c ----
    symbolic = re.match(
        rf"^\s*({_WORD})\s*[-−]\s*({_WORD})\s*\+\s*({_WORD})\s*(?:=|＝|等于|是)?\s*(?:什么|多少|谁|啥)?\s*$",
        cleaned,
    )
    if symbolic:
        a, b, c = symbolic.group(1), symbolic.group(2), symbolic.group(3)
        return SemanticQuery(ok=True, kind="analogy", a=a, b=b, c=c, raw=raw,
                             steps=[f"{a} - {b}", f"+ {c}"])

    # ---- 口语式:按运算符切成 a / b / c ----
    tokens = _split_by_operators(cleaned)
    if tokens is not None:
        a, b, c = tokens
        return SemanticQuery(ok=True, kind="analogy", a=a, b=b, c=c, raw=raw,
                             steps=[f"{a} - {b}", f"+ {c}"])

    reason = (
        "没解析出向量算术。可用写法:『国王 - 男人 + 女人』"
        "或『国王减去男人加上女人』;找语义邻居可用『国王的近义词』。"
    )
    return SemanticQuery(ok=False, reason=reason, raw=raw)


def _split_by_operators(text: str) -> tuple[str, str, str] | None:
    """把 a[减]b[加]c 切成三段。切不出恰好三段就返回 None(不猜)。"""
    pattern = "|".join(
        sorted((w for w in MINUS_WORDS + PLUS_WORDS if w), key=len, reverse=True)
    )
    parts = re.split(f"({pattern})", text)
    words: list[str] = []
    operators: list[str] = []
    for part in parts:
        piece = part.strip()
        if not piece:
            continue
        if piece in MINUS_WORDS:
            operators.append("-")
        elif piece in PLUS_WORDS:
            operators.append("+")
        else:
            words.append(piece)
    if len(words) != 3 or len(operators) != 2:
        return None
    if operators != ["-", "+"]:
        return None                  # 只支持 a - b + c 这一种形态
    # 每段还得是"像词"的东西(不含标点、长度合理)
    for word in words:
        if not re.fullmatch(_WORD, word):
            return None
    return words[0], words[1], words[2]


# 先从问句里剥掉的"脚手架词"。剥掉之后剩下的最短片段就是中心词。
SCAFFOLD = (
    "的近义词有哪些", "的近义词是什么", "的近义词", "同义词", "近义词", "相关词", "相似词",
    "有哪些", "是什么", "是啥", "最近的词", "最相似的词", "的邻居", "相关", "相似", "近义",
    "给我看", "查一下", "看一下", "一下", "哪个词", "哪个", "哪些", "什么", "最", "像", "近",
    "和", "跟", "与", "谁", "的", "词", "看", "查", "是",
)


def _extract_word(text: str) -> str | None:
    """从"找邻居"的问句里抠出中心词。

    做法:先剥掉脚手架词,再从剩下的片段里取最长的那个。
    直接对原句用 findall 会连"国王的近义词"整串抓出来(实测踩到),
    所以必须先剥壳再取词。
    """
    cleaned = text
    for token in sorted(SCAFFOLD, key=len, reverse=True):
        cleaned = cleaned.replace(token, " ")
    pieces = [p for p in re.split(r"[\s,，、。?？!！]+", cleaned) if p]
    if not pieces:
        return None
    return max(pieces, key=len)
