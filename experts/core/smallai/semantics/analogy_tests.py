"""类比自检用例。

写在这里而不是只写在训练脚本里,是为了让**运行期**也能自检:
    python -m smallai --semantics

这份用例是词向量领域的经典评测形态(a 之于 b,正如 c 之于 ?)。
里面刻意分成两组,报告时分开看,免得好看的数字掩盖难看的部分:

    关系平行组  性别对(国王/男人/女人)、亲属、动物 —— 结构平行,应当稳定成立
    泛化组      语料里只有间接支撑的类比 —— 允许失败,失败要如实说
"""

from __future__ import annotations

# (a, b, c, 期望的 d) 表示 a - b + c ≈ d
ANALOGY_TESTS: list[tuple[str, str, str, str]] = [
    ("国王", "男人", "女人", "女王"),
    ("皇帝", "男人", "女人", "皇后"),
    ("王子", "男人", "女人", "公主"),
    ("父亲", "男人", "女人", "母亲"),
    ("哥哥", "男人", "女人", "姐姐"),
    ("爷爷", "男人", "女人", "奶奶"),
    ("丈夫", "男人", "女人", "妻子"),
    ("男孩", "男人", "女人", "女孩"),
    ("公鸡", "男人", "女人", "母鸡"),
    ("雄狮", "男人", "女人", "雌狮"),
]

# 语料里没有直接平行结构、但从归属链能间接支撑的类比(泛化组)
GENERALIZATION_TESTS: list[tuple[str, str, str, str]] = [
    ("父亲", "男人", "女人", "母亲"),
    ("叔叔", "男人", "女人", "阿姨"),
    ("外公", "男人", "女人", "外婆"),
    ("弟弟", "男人", "女人", "妹妹"),
    ("侄子", "男人", "女人", "侄女"),
]


def all_tests() -> list[tuple[str, str, str, str]]:
    seen: set[tuple[str, str, str, str]] = set()
    out: list[tuple[str, str, str, str]] = []
    for item in ANALOGY_TESTS + GENERALIZATION_TESTS:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
