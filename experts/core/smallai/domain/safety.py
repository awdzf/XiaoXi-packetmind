"""安全判定:**红线与灰区都是数据**(定义在 pack.json 的 safety 段)。

三档结论:

    refuse   拒答 —— 不进入生成层(校验层报 repair=None,流水线直接给拒答话术)
    caution  放行,但在答复前加一条提醒(医疗/法律/隐私/情绪这类高风险话题)
    ok       正常回答

为什么把规则放进 pack.json:
  · 各数据包的红线本来就不完全一样(ethics 包要管更细的灰区,网页/Java 包
    只守公共红线 + 本行业的高危请求),把差异写成数据,代码只有一份;
  · 改话术、加规则不用改 Python;规则命中了哪一条也能被 --guard 原样交代。

判定是**文本级正则匹配**,不是"理解":它能拦住明显的请求,也可能漏掉绕写。
所以报告里始终写明这一点 —— 不把"没命中"说成"安全"。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import PACK
from ..persona import refusal_head

_LEVELS = ("refuse", "caution")

# 规则是从**当前数据包**的 safety 段编出来的:单核 + 多数据包,所以每条规则都带
# 一组包身份(数据包文件的路径)。缓存按包分开,换包就换一套规则 —— 否则同一个进程
# 里判过 A 包之后再判 B 包,红线还是 A 的(实测会出错)。
_RULES_CACHE: dict[str, tuple["Rule", ...]] = {}
_BAD_CACHE: dict[str, list[str]] = {}


@dataclass(frozen=True)
class Rule:
    """一条安全规则。patterns 命中且 unless 不命中 = 这条规则成立。"""

    id: str
    label: str
    level: str
    reason: str
    advice: tuple[str, ...]
    patterns: tuple[re.Pattern[str], ...]
    unless: tuple[re.Pattern[str], ...]

    def matches(self, text: str) -> bool:
        if not any(p.search(text) for p in self.patterns):
            return False
        return not any(p.search(text) for p in self.unless)


def _compile(raw: list[str], rule_id: str, bad: list[str]) -> tuple[re.Pattern[str], ...]:
    compiled: list[re.Pattern[str]] = []
    for pattern in raw:
        try:
            compiled.append(re.compile(pattern, re.IGNORECASE))
        except re.error as exc:
            bad.append(f"{rule_id}:{pattern!r} 不是合法正则({exc})")
    return tuple(compiled)


def _load_rules(bad: list[str]) -> tuple[Rule, ...]:
    rules: list[Rule] = []
    for level in _LEVELS:
        for raw in PACK.safety.get(level) or ():
            rule_id = str(raw.get("id") or "")
            patterns = _compile([str(p) for p in raw.get("patterns") or ()], rule_id, bad)
            unless = _compile([str(p) for p in raw.get("unless") or ()], rule_id, bad)
            if not patterns:
                continue
            rules.append(
                Rule(
                    id=rule_id,
                    label=str(raw.get("label") or rule_id),
                    level=level,
                    reason=str(raw.get("reason") or ""),
                    advice=tuple(str(a) for a in raw.get("advice") or ()),
                    patterns=patterns,
                    unless=unless,
                )
            )
    return tuple(rules)


def rules() -> tuple[Rule, ...]:
    """当前数据包的红线与灰区(按包缓存;换包或包文件变了就自动重编)。"""
    key = PACK.stamp
    if key not in _RULES_CACHE:
        bad: list[str] = []
        _BAD_CACHE[key] = bad
        _RULES_CACHE[key] = _load_rules(bad)
    return _RULES_CACHE[key]


@dataclass(frozen=True)
class SafetyReport:
    """一次安全判定的完整交代。"""

    level: str = "ok"                 # ok / caution / refuse
    hits: tuple[Rule, ...] = ()

    @property
    def labels(self) -> str:
        return "、".join(dict.fromkeys(hit.label for hit in self.hits)) or "无"

    @property
    def rule_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(hit.id for hit in self.hits))

    @property
    def reasons(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(hit.reason for hit in self.hits if hit.reason))

    def advice(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(a for hit in self.hits for a in hit.advice))

    # ---- 话术 ------------------------------------------------------------
    def refusal_text(self) -> str:
        """拒答话术:第一人称、只说人话;规则 id 与改动方式留给 --guard / --trace。"""
        lines = [refusal_head(self.labels)]
        advice = self.advice()
        if advice:
            lines.append("如果你是在做防护或合规,可以这样问:")
            lines.extend("  · " + item for item in advice)
        return "\n".join(lines)

    def caution_note(self) -> str:
        """灰区提醒:附在正常答复前面,不改变答案本身。"""
        advice = self.advice()
        text = f"安全提醒:{self.labels}。"
        if advice:
            text += " ".join(advice)
        return text

    def render(self) -> str:
        lines = [f"安全判定:{self.level}"]
        if not self.hits:
            lines.append("  命中的规则:无(注意:没命中不等于一定安全,这是文本级规则)。")
            return "\n".join(lines)
        lines.append("  命中的规则:")
        for hit in self.hits:
            lines.append(f"    · [{hit.level}] {hit.id} — {hit.label}")
            if hit.reason:
                lines.append(f"      原因:{hit.reason}")
        advice = self.advice()
        if advice:
            lines.append("  建议:")
            lines.extend("    · " + item for item in advice)
        return "\n".join(lines)


def assess(text: str) -> SafetyReport:
    """对一段文本做安全判定。空输入直接放行(校验层另有空输入检查)。"""
    if not text or not text.strip():
        return SafetyReport()
    hits = tuple(rule for rule in rules() if rule.matches(text))
    if not hits:
        return SafetyReport()
    if any(hit.level == "refuse" for hit in hits):
        return SafetyReport(level="refuse", hits=hits)
    return SafetyReport(level="caution", hits=hits)


def rule_problems() -> list[str]:
    """给 --health 用:正则编不过的规则要如实报出来,而不是静默失效。"""
    rules()                                     # 确保当前包已经编译过一遍
    return list(_BAD_CACHE.get(PACK.stamp, ()))


__all__ = ["Rule", "SafetyReport", "rules", "assess", "rule_problems"]
