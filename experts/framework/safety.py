"""框架自带的安全底线(不依赖任何可移除的数据包)。

为什么放在 framework/ 而不是 ethics 包里:
  数据包是可以被移出的 —— 删掉 ethics/ 之后,红线不能跟着一起消失。
这里只保留**最小、最明确**的几条(自伤方法 / 危险物制作 / 凭据索取 / 恶意程序);
更细的灰区规则仍然由 ethics 包(如果还在)补充。

`assess()` 返回和 ethics 包 `--guard --json` 相同的形状,便于合并:
    {"level": "ok|caution|refuse", "labels": str, "rule_ids": [...], "advice": [...]}
"""

from __future__ import annotations

import re

# (id, label, patterns, unless, reason, advice)
RULES: tuple[tuple[str, str, tuple[str, ...], tuple[str, ...], str, tuple[str, ...]], ...] = (
    (
        "self-harm-method",
        "自伤方法请求",
        (
            r"(自杀|自残|轻生).{0,8}(方法|方式|步骤|途径|工具|手段|实施|动手)",
            r"(怎么|如何|教我|告诉).{0,6}(自杀|自残|轻生)",
        ),
        (),
        "提供自伤方法可能直接造成伤害",
        (
            "我不会提供任何自伤的方式、步骤或工具信息。",
            "如果你正处在危险中,请立刻联系当地紧急服务,或告诉一个你信任的人。",
        ),
    ),
    (
        "weapon-making",
        "危险物制作",
        (r"(制作|制造|组装|自制|教我).{0,10}(炸弹|爆炸物|枪支|毒品|毒药)",),
        ("防止", "防范", "法规", "法律", "查处"),
        "提供制造方法可能造成严重伤害",
        (
            "这类制作方法不在可回答范围内。",
            "如果出于安全防护或法规合规的疑问,可以问相关防护问题。",
        ),
    ),
    (
        "credentials",
        "凭据与隐私索取",
        (
            r"(把|将)?(密码|身份证号|银行卡号).{0,8}(告诉|发给|给我|发我)",
            r"(怎么|如何).{0,6}(盗取|拿到|查)(别人|他人).{0,6}(密码|账号|身份证|住址)",
        ),
        (),
        "索取或盗取他人凭据可能被用于侵害",
        (
            "我不会提供获取他人账号、密码、身份证、住址的方法。",
            "如果是找回自己的账号,请走服务方的官方找回流程。",
        ),
    ),
    (
        "malware",
        "恶意程序",
        (r"(写|做|生成|制作).{0,10}(病毒|木马|勒索软件|恶意程序|挖矿脚本)",),
        ("防范", "查杀", "识别", "分析"),
        "恶意程序会伤害他人设备与数据",
        ("木马、勒索软件、挖矿脚本的编写不回答。", "防御与查杀思路可以问相关防御问题。"),
    ),
)

_LEVEL_RANK = {"ok": 0, "caution": 1, "refuse": 2}


def _compile(patterns: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p, re.IGNORECASE) for p in patterns)


_COMPILED = tuple((rid, label, _compile(patterns), _compile(unless), reason, advice)
                  for rid, label, patterns, unless, reason, advice in RULES)


def assess(text: str) -> dict:
    """只跑框架自带规则;这些都是硬红线,命中即 refuse。"""
    if not text or not text.strip():
        return {"level": "ok", "labels": "无", "rule_ids": [], "advice": []}

    hits: list[tuple[str, str, str, tuple[str, ...]]] = []
    for rid, label, patterns, unless, reason, advice in _COMPILED:
        if not any(p.search(text) for p in patterns):
            continue
        if any(p.search(text) for p in unless):
            continue
        hits.append((rid, label, reason, advice))

    if not hits:
        return {"level": "ok", "labels": "无", "rule_ids": [], "advice": []}
    labels = "、".join(dict.fromkeys(h[1] for h in hits))
    rule_ids = list(dict.fromkeys(h[0] for h in hits))
    advice = list(dict.fromkeys(a for h in hits for a in h[3]))
    return {"level": "refuse", "labels": labels, "rule_ids": rule_ids, "advice": advice}


def merge(reports: list[dict]) -> dict:
    """合并多份判定(框架底线 + ethics 包):任一份 refuse 即 refuse。"""
    level = "ok"
    labels: list[str] = []
    rule_ids: list[str] = []
    advice: list[str] = []
    errors: list[str] = []
    for report in reports:
        if not isinstance(report, dict):
            continue
        level = report.get("level", "ok") if _LEVEL_RANK.get(report.get("level", "ok"), 0) > _LEVEL_RANK[level] else level
        if report.get("labels") and report.get("labels") != "无":
            labels.append(str(report["labels"]))
        for rid in report.get("rule_ids") or ():
            rule_ids.append(str(rid))
        for item in report.get("advice") or ():
            advice.append(str(item))
        if report.get("error"):
            errors.append(str(report["error"]))
    merged = {
        "level": level,
        "labels": "、".join(dict.fromkeys(labels)) or "无",
        "rule_ids": list(dict.fromkeys(rule_ids)),
        "advice": list(dict.fromkeys(advice)),
    }
    if errors:
        merged["error"] = ";".join(dict.fromkeys(errors))
    return merged


__all__ = ["RULES", "assess", "merge"]
