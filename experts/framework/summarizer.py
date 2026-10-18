"""总结层:把这一轮查到的东西,用「小析」的口吻讲给用户听。

**它不生成新知识** —— 这是机制保证的,不是自觉:

  · 综合部分只引用各数据包答案里的**原句 / 要点**,不重写、不扩写、不推断;
  · 每条答案都点明**哪个数据包、哪一条**(`数据包 math · 条目 vector-field`),
    能倒查回 pack.json;
  · 答不上就照实说,并**只在此时**给一句提示。

两种读法:

  · 默认 `plain=True`:像人在说话 —— 先给答案,末尾一行出处;
    `[分解] [查找] [候选]` 这些内部账不出现;
  · `plain=False`(由 `--trace` 触发):完整展示分解、查找、尝试与规则 id,
    给维护者排查用。两种读法引用的是同一批原句与同一条出处。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .catalog import MEMORY_ROLE, persona
from .decomposer import Decomposition
from .router import Routing

_SENT_SPLIT = re.compile(r"(?<=[。！？!?])")
_BULLET = re.compile(r"^\s*[·•]\s*")
_META_PREFIXES = ("(", "（", "来自数据包", "来自本地")
_CAUTION_PREFIXES = ("安全提醒:", "安全提醒：")


@dataclass
class ExpertAnswer:
    """一个数据包这一轮的回答。attempts 记录试过哪几个问法。"""

    folder: str
    name: str
    question: str
    text: str
    intent: str = ""
    entry: str | None = None
    source: str = ""
    safety_level: str = "ok"
    ok: bool = False
    error: str | None = None
    attempts: tuple[str, ...] = ()
    role: str = "domain"
    #: 校准后的置信度（0–1；None = 没经过校准，例如安全拒答）
    confidence: float | None = None
    #: 这条命中来自 high / normal / low 信任条目
    trust: str = "normal"
    #: 低优先级冲突时给用户看的说明（作者包 > 用户记忆的默认顺序）
    conflict_note: str = ""


def _content_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith(_META_PREFIXES):
            continue
        lines.append(line)
    return lines


def first_sentence(text: str, limit: int = 160) -> str:
    """取答案的首个实义句:跳过"要点:"这类标题行。"""
    for line in _content_lines(text):
        if line.startswith("要点") or line.startswith("相关条目") or line.startswith("依据"):
            continue
        line = _BULLET.sub("", line)
        pieces = [p for p in _SENT_SPLIT.split(line) if p.strip()]
        sentence = pieces[0].strip() if pieces else line
        return sentence if len(sentence) <= limit else sentence[: limit - 1] + "…"
    return ""


def key_points(text: str, limit: int = 2) -> list[str]:
    points: list[str] = []
    for line in _content_lines(text):
        stripped = _BULLET.sub("", line)
        if stripped.startswith("要点"):
            continue
        if line.startswith("·") or line.startswith("•"):
            points.append(stripped)
        if len(points) >= limit:
            break
    return points


def _chat_text(text: str) -> str:
    """闲聊类答案:去掉条目边注,只留人话。"""
    keep: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith(_META_PREFIXES):
            continue
        if line.startswith("相关条目"):
            continue
        keep.append(line)
    return "\n".join(keep)


def _strip_caution(text: str) -> str:
    """核心层会把灰区提醒加在答案最前面;人话版由总结层统一补,这里先去掉。"""
    kept: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if any(line.startswith(prefix) for prefix in _CAUTION_PREFIXES):
            continue
        kept.append(raw)
    return "\n".join(kept).strip()


def _answer_body(answer: ExpertAnswer) -> str:
    """人话版的答案正文:去掉来源边注与核心层的提醒,保留要点/相关条目。"""
    if answer.role == "general":
        body = _strip_caution(_chat_text(answer.text))
    else:
        kept: list[str] = []
        for raw in (answer.text or "").splitlines():
            line = raw.strip()
            if not line:
                continue
            if line.startswith(_META_PREFIXES):
                continue
            kept.append(line)
        body = "\n".join(kept)
    body = _strip_caution(body)
    return re.sub(r"\n{3,}", "\n\n", body).strip()


def _source_label(answer: ExpertAnswer) -> str:
    """一个数据包的称呼:`数据包 math`。"""
    return persona.source(answer.folder)


def _provenance(answers: list[ExpertAnswer]) -> str:
    pairs = [(a.folder, a.entry) for a in answers if a.entry]
    return persona.credit(pairs)


def _route_line(routing: Routing) -> str:
    rehit_parents = {parent for _child, parent in routing.rehits}

    def label(target) -> str:
        pack = persona.source(target.card.folder)
        marks: list[str] = []
        if target.card.is_general:
            marks.append("兜底")
        if target.via == "reverse":
            marks.append(f"反向·第{target.wave}轮")
        if target.card.folder in rehit_parents:
            marks.append("再命中")
        score = f"{target.score} 分" if target.score > 0 else "转交"
        if target.confidence:
            score += f";置信 {target.confidence:.2f}"
        if target.trust == "high":
            score += ";高置信"
        if marks:
            score += ";" + ";".join(marks)
        return f"{pack}({score})"

    return " / ".join(label(t) for t in routing.targets)


# ---------------------------------------------------------------------------
# 人话版
# ---------------------------------------------------------------------------
def _plain_refusal(text: str, labels: str) -> str:
    """拒答:只留人话,不展示规则 id 与"改哪个文件"。"""
    body = text.strip() or persona.refusal_head(labels)
    kept = [line for line in body.splitlines() if not line.strip().startswith(("(", "（"))]
    return "\n".join(kept).strip()


def _plain_caution(safety: dict, answers: list[ExpertAnswer]) -> str:
    # 自伤危机由 crisis-self 条目直接回应,不再叠一条泛泛的提醒
    if any(a.entry == "crisis-self" for a in answers if a.ok):
        return ""
    advice = tuple(str(item) for item in safety.get("advice") or ())
    return persona.caution_plain(advice)


def _plain_summary(
    decomposition: Decomposition,
    routing: Routing,
    answers: list[ExpertAnswer],
    safety: dict,
    detail: bool,
    context: tuple[str, ...],
    ambiguity: tuple[str, ...],
) -> str:
    level = str(safety.get("level") or "ok")
    usable = [a for a in answers if a.ok]

    if level == "refuse":
        refusal = next((a for a in answers if a.source == "refusal"), None)
        return _plain_refusal(
            refusal.text if refusal else "", str(safety.get("labels") or "命中安全红线")
        )

    prefix: list[str] = []
    caution = _plain_caution(safety, answers)
    if caution:
        prefix = [caution, ""]

    if not routing.targets or not usable:
        return "\n".join(
            prefix + [persona.no_hit(decomposition.original, decomposition.keywords)]
        )

    if len(usable) == 1:
        answer = usable[0]
        lines = list(prefix)
        lines.append(_answer_body(answer))
        if answer.role == MEMORY_ROLE:
            lines.append(persona.MEMORY_NOTE)
        # 闲聊不是知识:不摆出处,否则一句「你好呀」后面跟一行数据包会出戏;
        # 知识条目仍然保留出处,方便倒查。
        if answer.entry and answer.role not in ("general", "persona"):
            lines.extend(["", persona.natural_credit([(answer.folder, answer.entry)])])
        return "\n".join(lines).strip()

    # 同一个数据包里的多条命中:典型是记忆包的"再命中"。
    # 不能说成"N 个数据包",否则会出现"memory、memory"这种怪话。
    if len({answer.folder for answer in usable}) == 1:
        folder = usable[0].folder
        lines = list(prefix)
        if all(answer.role == MEMORY_ROLE for answer in usable):
            lines.append(f"关于这个问题,我记得 {len(usable)} 条:")
        else:
            lines.append(
                f"关于这个问题,数据包 {folder} 里有 {len(usable)} 条相关内容,"
                "我把原句并列给你(没有改写):"
            )
        for answer in usable:
            label = (
                persona.source_entry(answer.folder, answer.entry)
                if answer.entry else _source_label(answer)
            )
            lines.extend(["", label, _answer_body(answer)])
        lines.extend(["", "以上都是原句,我没有改写。"])
        for note in dict.fromkeys(answer.conflict_note for answer in usable if answer.conflict_note):
            lines.append(note)
        if any(answer.role == MEMORY_ROLE for answer in usable):
            lines.append(persona.MEMORY_NOTE)
        if ambiguity:
            others = "、".join(persona.source(item) for item in ambiguity)
            lines.append(persona.ambiguity_plain(others))
        lines.extend([
            "",
            persona.natural_credit([(a.folder, a.entry) for a in usable if a.entry]),
        ])
        return "\n".join(lines).strip()

    packs = "、".join(a.folder for a in usable)
    lines = list(prefix)
    lines.append(
        f"这个问题在 {len(usable)} 个数据包里有内容({packs}),"
        "我把各自的原句并列给你(没有改写):"
    )
    for answer in usable:
        label = (
            persona.source_entry(answer.folder, answer.entry)
            if answer.entry else _source_label(answer)
        )
        lines.extend(["", label])
        if detail:
            lines.extend(_answer_body(answer).splitlines())
        else:
            lines.append(first_sentence(answer.text))
            points = key_points(answer.text, 1)
            if points:
                lines.append("  · " + points[0])
    lines.extend(["", "以上都是原句,我没有改写。"])
    for note in dict.fromkeys(answer.conflict_note for answer in usable if answer.conflict_note):
        lines.append(note)
    if ambiguity:
        others = "、".join(persona.source(folder) for folder in ambiguity)
        lines.append(persona.ambiguity_plain(others))
    if routing.rehits:
        topic = "、".join(decomposition.keywords) or "这个问题"
        for child, parent in routing.rehits:
            lines.append(persona.relation_plain(
                topic=topic,
                child=child,
                parent=parent,
                child_folder=child,
                parent_folder=parent,
            ))
    lines.extend([
        "",
        persona.natural_credit([(a.folder, a.entry) for a in usable if a.entry]),
    ])
    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# 维护者版(trace):保留全部内部账
# ---------------------------------------------------------------------------
def _verbose_summary(
    decomposition: Decomposition,
    routing: Routing,
    answers: list[ExpertAnswer],
    safety: dict,
    detail: bool,
    trace: bool,
    context: tuple[str, ...],
    ambiguity: tuple[str, ...],
) -> str:
    lines: list[str] = []
    if decomposition.tokens:
        lines.append("[分解] " + " | ".join(decomposition.tokens))
    else:
        lines.append("[分解] (安全闸先于拆词,未进入分解)")

    if decomposition.keywords:
        lines.append("[关键词] " + "、".join(decomposition.keywords))

    if context:
        lines.append("[上下文] 接着上一轮的关键词:" + "、".join(context))

    if trace:
        shown = "、".join(c.text for c in decomposition.candidates[:12])
        lines.append(f"[候选] 共 {len(decomposition.candidates)} 条,前 12 条:{shown}")
        lines.append(f"[查找明细] {routing.notes}")
        for target in routing.targets:
            why = " / ".join(target.reasons[:5]) or "-"
            lines.append(f"  · {target.label()} 得分 {target.score}:{why}")

    level = str(safety.get("level") or "ok")
    if level == "caution":
        advice = " ".join(str(a) for a in safety.get("advice") or ())
        lines.append(f"[安全提醒] {safety.get('labels') or '灰区'}。{advice}".rstrip())

    if level == "refuse":
        lines.append(
            f"[安全兜底] 判定:拒答(命中规则:{'、'.join(safety.get('rule_ids') or ['未说明'])})"
        )
        refusal = next((a for a in answers if a.source == "refusal"), None)
        if refusal:
            lines.extend(refusal.text.splitlines())
        lines.append("[说明] 安全闸在分解与查找之前;这一轮没有动任何数据包。")
        return "\n".join(lines)

    usable = [a for a in answers if a.ok]

    if not routing.targets:
        lines.append(f"[查找] 没有数据包能接住这个题目(逐条尝试 {routing.tried} 条候选)")
        lines.append("[说明] " + persona.UNKNOWN)
        lines.append("[说明] " + persona.teach_question(decomposition.original, decomposition.keywords))
        lines.append("[说明] " + persona.TEACH_INVITE)
        lines.append(persona.HINT)
        return "\n".join(lines)

    lines.append(f"[查找] {_route_line(routing)}")

    if not usable:
        only_general = all(a.role == "general" for a in answers)
        if only_general:
            lines.append("[说明] " + persona.UNKNOWN)
            lines.append("[说明] " + persona.teach_question(decomposition.original, decomposition.keywords))
            lines.append("[说明] " + persona.TEACH_INVITE)
            lines.append(persona.HINT)
        else:
            lines.append("[说明] 查到了数据包,但没有条目能对上:")
            for answer in answers:
                stripped = answer.text.strip()
                reason = answer.error or (stripped.splitlines()[0] if stripped else "无输出")
                lines.append(f"  · {_source_label(answer)}:{reason}")
        return "\n".join(lines)

    if len(usable) == 1:
        answer = usable[0]
        label = (
            persona.source_entry(answer.folder, answer.entry)
            if answer.entry else _source_label(answer)
        )
        lines.append(f"[回答] {label}")
        lines.append(answer.text.strip())
        if answer.role == MEMORY_ROLE:
            lines.append(persona.MEMORY_NOTE)
        gist = first_sentence(answer.text)
        if gist and answer.role != "general":
            lines.append(f"[一句话] {gist}")
    else:
        folders = {answer.folder for answer in usable}
        packs = "、".join(answer.folder for answer in usable)
        if len(folders) == 1:
            lines.append(
                f"[综合] 数据包 {usable[0].folder} 里有 {len(usable)} 条相关内容,"
                "按原句并列如下(我没有改写):"
            )
        else:
            lines.append(
                f"[综合] 我查了 {len(usable)} 个数据包({packs}),"
                "把它们各自的原句并列如下(我没有改写):"
            )
        for answer in usable:
            gist = first_sentence(answer.text)
            lines.append(f"  · {_source_label(answer)}:{gist}")
            points = key_points(answer.text, 1)
            if points:
                lines.append(f"      · {points[0]}")
        lines.append("  —— 以上都是原句引用,不是我的再创作;完整答案见下。")
        if any(answer.role == MEMORY_ROLE for answer in usable):
            lines.append(persona.MEMORY_NOTE)

        if detail:
            lines.append("[详细]")
            for answer in usable:
                label = (
                    persona.source_entry(answer.folder, answer.entry)
                    if answer.entry else _source_label(answer)
                )
                lines.append(f"  ▸ {label}")
                body = _answer_body(answer) if answer.role == "general" else answer.text.strip()
                for line in body.splitlines():
                    lines.append("    " + line)
        else:
            lines.append("(要看完整答案,去掉 --brief,或输入 :detail on)")

    for note in dict.fromkeys(answer.conflict_note for answer in usable if answer.conflict_note):
        lines.append("[冲突] " + note)

    if ambiguity:
        others = "、".join(f"数据包 {folder}" for folder in ambiguity)
        lines.append(persona.AMBIGUITY.format(others=others))

    if routing.rehits:
        topic = "、".join(decomposition.keywords) or "这个问题"
        for child, parent in routing.rehits:
            lines.append(persona.RELATION.format(
                topic=topic,
                child=persona.source(child),
                parent=persona.source(parent),
                child_folder=child,
                parent_folder=parent,
            ))

    if trace:
        for answer in answers:
            lines.append(f"[尝试] {_source_label(answer)}:{ ' → '.join(answer.attempts) }")

    lines.append(_provenance(usable))
    return "\n".join(lines)


def summarize(
    user_text: str,
    decomposition: Decomposition,
    routing: Routing,
    answers: list[ExpertAnswer],
    safety: dict | None = None,
    detail: bool = True,
    trace: bool = False,
    context: tuple[str, ...] = (),
    ambiguity: tuple[str, ...] = (),
    plain: bool = True,
    budget_note: str = "",
) -> str:
    safety = safety or {}
    if trace:
        plain = False

    # 人格自述(问"你是谁 / 你能做什么"):不查数据包,也不摆凭据
    if len(answers) == 1 and answers[0].role == persona.ROLE:
        if plain:
            return answers[0].text.strip()
        body = answers[0].text.strip().splitlines() or [""]
        lines = ["[我] " + body[0]]
        lines.extend(body[1:])
        if trace:
            lines.append(f"[说明] {routing.notes}")
        return "\n".join(lines)

    if plain:
        body = _plain_summary(
            decomposition, routing, answers, safety,
            detail=detail, context=context, ambiguity=ambiguity,
        )
    else:
        body = _verbose_summary(
            decomposition, routing, answers, safety,
            detail=detail, trace=trace, context=context, ambiguity=ambiguity,
        )
    if budget_note:
        body = body.rstrip() + "\n\n" + (
            "[预算] " + budget_note if not plain else budget_note
        )
    return body


__all__ = ["ExpertAnswer", "summarize", "first_sentence", "key_points"]
