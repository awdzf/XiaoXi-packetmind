"""语义层:词向量 + 关系图。

这一层的存在是为了回答一个具体问题:**词与词之间的距离能不能当运算用。**
    · 向量空间 —— smallai/semantics/index.py
    · 词与词的显式联系(语料)—— seed_relations.json / extra_relations.json
    · 离线训练 —— tools/train_vectors.py(运行期只查表,零依赖)

设计底线:
    · 缺向量文件就降级成"只有关系图",不抛异常;
    · 算术结果必须给出候选**与拒绝理由**,人能核对;
    · 不假装这是大语料词向量 —— 语料量与准确率都写在 README 里。
"""

from .analogy_tests import ANALOGY_TESTS, GENERALIZATION_TESTS, all_tests
from .index import (
    AnalogyCandidate,
    AnalogyResult,
    Neighbor,
    SemanticsIndex,
    build_semantics,
)

__all__ = [
    "SemanticsIndex",
    "build_semantics",
    "AnalogyResult",
    "AnalogyCandidate",
    "Neighbor",
    "ANALOGY_TESTS",
    "GENERALIZATION_TESTS",
    "all_tests",
]
