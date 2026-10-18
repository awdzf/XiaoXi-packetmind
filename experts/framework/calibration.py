"""校准弃权：不拿一个拍脑袋的绝对阈值决定“答不答”。

旧逻辑是“分数 ≥3 就答 / TF-IDF ≥0.35 就答”。这两个数是经验值，换一个包、
换一种问法就不一定合适。这里改成看**证据形状**：

    · 绝对强度：最高分在候选分布里有多突出（z 分数）；
    · 相对优势：第一名比第二名高多少（margin）；
    · 覆盖：问题里的词有多少真的被命中（coverage）；
    · 信任：高置信条目给一点加成，低置信条目扣一点。

输出一个 0–1 的置信度与是否弃权。它仍然不是“概率”，而是**可解释的校准分**：
同一条命中的四个分量都能在 `--trace` 里看到，阈值也能在 config.json 调。

零依赖、确定性：同样的候选与分数，永远得到同样的结论。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

DEFAULT_MIN_CONFIDENCE = 0.35
DEFAULT_RULE_SCALE = 8.0
DEFAULT_HYBRID_SCALE = 1.0


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = math.exp(-value)
        return 1.0 / (1.0 + z)
    z = math.exp(value)
    return z / (1.0 + z)


@dataclass(frozen=True)
class Calibration:
    """一次候选集合的校准结果。"""

    value: float
    level: str
    abstain: bool
    margin: float
    coverage: float
    reason: str

    @property
    def label(self) -> str:
        return f"{self.value:.2f}({self.level})"


class Calibrator:
    """把“分数列表 + 命中覆盖 + 信任级别”压成一个可解释置信度。"""

    def __init__(
        self,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
        *,
        rule_scale: float = DEFAULT_RULE_SCALE,
        hybrid_scale: float = DEFAULT_HYBRID_SCALE,
    ) -> None:
        self.min_confidence = max(0.0, min(1.0, float(min_confidence)))
        self.rule_scale = max(1e-6, float(rule_scale))
        self.hybrid_scale = max(1e-6, float(hybrid_scale))

    def calibrate(
        self,
        scores: Iterable[float],
        *,
        matched: int = 0,
        total: int = 0,
        trust: str = "normal",
        scale: str = "rule",
    ) -> Calibration:
        ranked = sorted(
            (max(0.0, float(score)) for score in scores),
            reverse=True,
        )
        best = ranked[0] if ranked else 0.0
        if best <= 0:
            return Calibration(
                value=0.0,
                level="low",
                abstain=True,
                margin=0.0,
                coverage=0.0,
                reason="没有任何候选",
            )

        second = ranked[1] if len(ranked) > 1 else 0.0
        margin = (best - second) / max(best, 1e-9)
        coverage = (matched / total) if total > 0 else 0.0
        coverage = max(0.0, min(1.0, coverage))

        if len(ranked) > 1:
            mean = sum(ranked) / len(ranked)
            variance = sum((score - mean) ** 2 for score in ranked) / len(ranked)
            std = math.sqrt(variance)
            z = (best - mean) / std if std > 1e-9 else 0.0
        else:
            z = 0.0
        quality = max(0.0, min(1.0, z / 2.5)) if len(ranked) > 1 else 0.5

        denominator = self.hybrid_scale if scale == "hybrid" else self.rule_scale
        strength = max(0.0, min(1.0, best / denominator))
        trust_bonus = {"high": 0.08, "low": -0.08}.get(trust, 0.0)

        # 四个分量都留在 0–1；权重和为 1，再加信任修正
        base = (
            0.30 * quality
            + 0.25 * margin
            + 0.30 * coverage
            + 0.15 * strength
            + trust_bonus
        )
        value = _sigmoid((base - 0.45) * 6.0)
        level = "high" if value >= 0.70 else ("normal" if value >= 0.45 else "low")
        abstain = value < self.min_confidence
        reason = (
            f"z={z:.2f} margin={margin:.2f} "
            f"coverage={coverage:.2f} strength={strength:.2f} trust={trust}"
        )
        return Calibration(
            value=round(value, 4),
            level=level,
            abstain=abstain,
            margin=round(margin, 4),
            coverage=round(coverage, 4),
            reason=reason,
        )

    def calibrate_hybrid(
        self,
        hit_scores: Iterable[float],
        *,
        matched: int,
        total: int,
        trust: str = "normal",
    ) -> Calibration:
        return self.calibrate(
            hit_scores,
            matched=matched,
            total=total,
            trust=trust,
            scale="hybrid",
        )


__all__ = [
    "DEFAULT_MIN_CONFIDENCE",
    "DEFAULT_RULE_SCALE",
    "DEFAULT_HYBRID_SCALE",
    "Calibration",
    "Calibrator",
]
