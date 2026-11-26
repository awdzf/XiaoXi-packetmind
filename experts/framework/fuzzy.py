"""模糊匹配:编辑距离、相似度、错字预算与词典纠错(零依赖)。

规则路由是主力,这一层只负责一件事:**两个字符串差多少、算不算"差一点"**。
"算不算"不靠感觉,靠 `typo_budget()` 按长度给出的预算:

    · ASCII 3–7 个字母 -> 容 1 个错;8 个以上 -> 容 2 个错(HMTL / javascrpit);
    · 中文 2 个字 -> 容 1 个字,但**要求同音或形近**(判据由调用方传入,
      小析用的是 `decomposer.related_char`);
    · 中文 3 个字以上 -> 容 1 个字(唯一最佳才认,见 `correct_token`);
    · 预算之外一律返回"不近" —— 宁可漏一点,也不硬猜。

为什么单独一层:

    · 以前"距离 ≤1"散在 `router._damerau1` 与分解器的变体表里,两处口径不同,
      改一处另一处不会跟着改;
    · 中文与 ASCII 的容错尺度不一样(按字 / 按字母),统一成"预算 = f(长度)"
      之后,阈值只有一处定义;
    · 纠错要用**词表**(各包的领域词 + 关键词)当字典,而词表在分解器手里:
      这里只提供"怎么比",不掺"比什么"。

依赖只有标准库;`correct_token` 的词表与 `related` 判据都由调用方传入,
所以这一层不认识任何数据包。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: ASCII 词的边界(与 router 的口径一致:词内允许 `_ + # . -`)
_ASCII_WORD = re.compile(r"[a-z0-9][a-z0-9_+#.\-]{0,31}")
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")

#: 形近字:人眼容易写混、且常见于输入法误选的字对(双向生效)。
#: 只收"长得像"的;同音不同形的字归 `decomposer.HOMOPHONES`,两张表不重复。
CONFUSABLE_PAIRS: tuple[tuple[str, str], ...] = (
    ("己", "已"), ("已", "巳"), ("未", "末"), ("土", "士"), ("日", "目"),
    ("白", "自"), ("千", "干"), ("干", "于"), ("万", "方"), ("木", "术"),
    ("田", "由"), ("由", "甲"), ("甲", "申"), ("戊", "戌"), ("戌", "戍"),
    ("侯", "候"), ("即", "既"), ("辩", "辨"), ("辨", "辫"), ("拔", "拨"),
    ("晴", "睛"), ("冶", "治"), ("博", "搏"), ("概", "慨"), ("拆", "折"),
    ("微", "徽"), ("暖", "暧"), ("段", "断"), ("族", "旅"), ("待", "侍"),
    ("廷", "延"), ("析", "折"), ("辛", "幸"), ("梁", "粱"), ("虚", "虑"),
    ("免", "兔"), ("因", "困"), ("贝", "见"), ("斤", "斥"), ("矢", "失"),
    ("勿", "匆"), ("勾", "句"), ("场", "厂"), ("力", "刀"), ("力", "办"),
    ("具", "真"), ("且", "目"), ("免", "兑"), ("冒", "冐"), ("赢", "羸"),
)


def _build_shape_index() -> dict[str, frozenset[str]]:
    """把字对拍平成 字 -> 形近字集合(双向),查的时候一次哈希。"""
    index: dict[str, set[str]] = {}
    for left, right in CONFUSABLE_PAIRS:
        if left == right:
            continue
        index.setdefault(left, set()).add(right)
        index.setdefault(right, set()).add(left)
    return {key: frozenset(value) for key, value in index.items()}


SHAPES: dict[str, frozenset[str]] = _build_shape_index()


@dataclass(frozen=True)
class Correction:
    """词典纠错的结果:改成了哪个词、差几个字。"""

    term: str
    distance: int


def damerau_levenshtein(a: str, b: str, max_distance: int | None = None) -> int:
    """Damerau-Levenshtein 距离(替换/插入/删除/相邻互换各算 1)。

    `max_distance` 给了就在超过预算时提前返回 `max_distance + 1` —— 调用方
    只关心"在不在预算内",不需要知道到底差多远时,不必把整张表算完。

    实现用滚动三行 + 最优字符串对齐(OSA)变体:相邻互换只认一次,
    这对"手快打反了两个字母"足够,且不会像完整的 DL 那样把长串算得更近。
    """
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    la, lb = len(a), len(b)
    limit = None if max_distance is None else max(0, int(max_distance))
    if limit is not None and abs(la - lb) > limit:
        return limit + 1

    previous_previous: list[int] = []
    previous: list[int] = list(range(lb + 1))
    for i in range(1, la + 1):
        current = [i] + [0] * lb
        row_min = i
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            value = min(
                previous[j] + 1,            # 删除
                current[j - 1] + 1,         # 插入
                previous[j - 1] + cost,     # 替换 / 相同
            )
            if (
                i > 1 and j > 1
                and a[i - 1] == b[j - 2]
                and a[i - 2] == b[j - 1]
            ):
                value = min(value, previous_previous[j - 2] + 1)   # 相邻互换
            current[j] = value
            if value < row_min:
                row_min = value
        if limit is not None and row_min - (la - i) > limit:
            # 剩下的行每行最多把距离降 1,已经不可能回到预算内
            return limit + 1
        previous_previous, previous = previous, current
    return previous[lb]


def similarity(a: str, b: str) -> float:
    """0–1 的相似度:`1 - 距离 / 较长那个的长度`。两边都空算 1。"""
    left, right = a or "", b or ""
    if not left and not right:
        return 1.0
    length = max(len(left), len(right))
    if not length:
        return 1.0
    return 1.0 - damerau_levenshtein(left, right) / length


def typo_budget(text: str) -> int:
    """按长度给"容几个错"的预算;返回 0 表示这个词太短,不做模糊匹配。

    预算只跟**长度**有关,所以是确定的、可核对的:
    短词容错就等于乱认(两个字的词差一个字,可能根本是另一个词),
    长词容 1–2 个错仍然信息量足够。
    """
    word = (text or "").strip()
    if not word:
        return 0
    if word.isascii():
        length = len(word)
        if length < 3:
            return 0
        return 1 if length < 8 else 2
    # 中文按"字数"算,标点/空格不占预算
    length = sum(len(match.group(0)) for match in _CJK_RUN.finditer(word)) or len(word)
    if length < 2:
        return 0
    return 1 if length < 9 else 2


def shape_like(left: str, right: str) -> bool:
    """两个字是不是形近字(只对单字有意义)。"""
    if len(left) != 1 or len(right) != 1:
        return False
    return right in SHAPES.get(left, ())


def _all_diffs_related(word: str, term: str, related) -> bool:
    """逐位比较:每一处不同都得"同音或形近",否则不算错字。"""
    diffs = [(x, y) for x, y in zip(word, term) if x != y]
    return bool(diffs) and all(related(x, y) for x, y in diffs)


def correct_token(
    token: str,
    vocabulary,
    *,
    related=None,
    budget: int | None = None,
) -> Correction | None:
    """在词表里给一个"没认出来的词"找纠错;不唯一就放弃。

    三条自律(都是为了"不猜"):

    1. **只在预算内**:`typo_budget` 说容几个错,就只找那么近的;
    2. **中文只纠"换字"**:中文候选必须与词同长(多字/少字可能是另一个词,
       例如「磁电场」既像「电场」又像「磁场」,两边都能删一个字 —— 不纠);
    3. **最佳必须唯一**:同距离、同长度的候选有两个以上,就返回 None,
       让上层如实说"没找到",而不是替用户选一个。

    两个字的词还要过 `related`(同音/形近):两个字的词只差一个字的时候,
    "错字"和"另一个词"在字面上分不开(「电厂」/「电场」),得靠读音或字形加一道证据;
    三个字以上只靠"唯一最优"和"虚词不参与"两条守(调用方还可以再加自己的判据)。
    """
    word = (token or "").strip()
    if len(word) < 2 or word in vocabulary:
        return None
    word_is_ascii = word.isascii()
    limit = typo_budget(word) if budget is None else max(0, int(budget))
    if limit <= 0:
        return None
    lowered = word.lower() if word_is_ascii else word
    chars = set(lowered)

    found: list[Correction] = []
    for raw in vocabulary:
        term = (raw or "").strip()
        if not term or term == word:
            continue
        term_is_ascii = term.isascii()
        if term_is_ascii != word_is_ascii:
            continue                                    # 不跨文字体系纠错
        target = term.lower() if term_is_ascii else term
        if term_is_ascii:
            if abs(len(target) - len(lowered)) > limit:
                continue
        elif len(target) != len(lowered):
            continue                                    # 中文只认"换字"
        # 便宜的前置筛:重合的字不够,就根本不可能在预算内
        if len(chars & set(target)) < len(target) - limit:
            continue
        distance = damerau_levenshtein(lowered, target, limit)
        if not 0 < distance <= limit:
            continue
        if not term_is_ascii and len(target) <= 2 and distance == 1:
            if related is None or not _all_diffs_related(lowered, target, related):
                continue
        found.append(Correction(term=term, distance=distance))

    if not found:
        return None
    found.sort(key=lambda item: (item.distance, 0 if len(item.term) == len(word) else 1, item.term))
    best = found[0]
    if len(found) > 1:
        runner_up = found[1]
        if (runner_up.distance, len(runner_up.term)) == (best.distance, len(best.term)):
            return None                                 # 不唯一:不猜
    return best


def near_in(
    term: str,
    text: str,
    *,
    related=None,
    budget: int | None = None,
) -> tuple[str, int] | None:
    """在 `text` 里找一段与 `term` 差在预算内的内容;返回 (那一段, 距离)。

    精确命中不归它管(调用方先做子串 / 词边界匹配),这里只找"差一点"的:
    ASCII 按词比对整词,中文按词长滑窗(中文没有空格,窗口就是候选词)。
    """
    if not term or not text:
        return None
    limit = typo_budget(term) if budget is None else max(0, int(budget))
    if limit <= 0:
        return None

    if term.isascii():
        best: tuple[str, int] | None = None
        for match in _ASCII_WORD.finditer(text):
            window = match.group(0).strip(".-")
            if not window or abs(len(window) - len(term)) > limit:
                continue
            distance = damerau_levenshtein(term, window, limit)
            if distance <= limit and (best is None or distance < best[1]):
                best = (window, distance)
        return best

    size = len(term)
    best = None
    for match in _CJK_RUN.finditer(text):
        run = match.group(0)
        if len(run) < size:
            continue
        for start in range(len(run) - size + 1):
            window = run[start:start + size]
            distance = damerau_levenshtein(window, term, limit)
            if distance > limit:
                continue
            if distance and size <= 2 and related is not None:
                if not _all_diffs_related(window, term, related):
                    continue
            if best is None or distance < best[1]:
                best = (window, distance)
                if distance == 0:
                    return best
    return best


__all__ = [
    "CONFUSABLE_PAIRS",
    "SHAPES",
    "Correction",
    "damerau_levenshtein",
    "similarity",
    "typo_budget",
    "shape_like",
    "correct_token",
    "near_in",
]
