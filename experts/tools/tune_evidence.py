"""给"一元字命中"降权，看能不能把剩下的错误引用压下去，同时不伤正例。

问题：`费马大定理怎么证明` 之所以能盖过 0.30，是因为"定/理/证/明"这四个**单字**
都在 proof 条目的头部出现过 —— 单字命中太多，把"费马/马大/大定"这些真正的
特征二元组摊薄了。单字的证据力本来就弱于二元组（"理"字几乎哪儿都有）。

这个脚本不改进代码，只把 `evidence.UNIGRAM_WEIGHT` 扫一遍，跑同一个评测集，
把"错误引用 / 正例命中"的取舍曲线打出来 —— 阈值和权重都应该是量出来的，不是拍的。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from framework import GoChat, evaluation  # noqa: E402
from framework import evidence as evidence_mod  # noqa: E402


def run(weight: float, threshold: float) -> tuple[int, int, int]:
    evidence_mod.UNIGRAM_WEIGHT = weight
    chat = GoChat()
    # 权重改了要重建闸门（IDF 与权重在评估时读取）
    from framework import evidence
    chat.gate = evidence.EvidenceGate.from_cards(chat.cards, threshold=threshold)
    report = evaluation.run(chat)
    return report.answer_hit, report.answer_abstain_ok, report.answer_false


def main() -> None:
    print("weight thr | 正例命中/33 | 负例弃权/46 | 错误引用/46")
    for weight in (1.0, 0.7, 0.5, 0.35):
        for threshold in (0.30, 0.35, 0.40):
            hit, abst, false = run(weight, threshold)
            print(f"{weight:4.2f}  {threshold:.2f} | {hit:2d}/33        | {abst:2d}/46         | {false:2d}")


if __name__ == "__main__":
    main()
