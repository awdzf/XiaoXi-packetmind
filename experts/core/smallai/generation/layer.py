"""生成层:把"已通过校验的结构化帧"变成一句人话 —— **用同一个人的口吻**。

这位本地小 AI 叫「小析」,人格定义在 `smallai/persona.py`;这一层是它的嘴。

第一原则仍然是**不许自己造事实**:

  · 知识问答只从**当前数据包**的 `pack.json` 条目里取答案,
    答完把**数据包 + 条目 id** 一并交代 —— 这条答案能倒查回那份 JSON;
  · 没有条目就如实说"没找到",不拿常识补一个;
  · 这一版**没有模型端口**:开放问题不再转给任何模型,
    所以这里也不存在"模型输出清洗"那一套(整段删掉,而不是留着不调用)。

模板与数据的分工:
  - 算术/换算/比较/逻辑/时间:引用前几层算好的结果(calc/convert/logic);
  - 知识问答:引用数据包条目,并说出是哪个包、哪一条;
  - 超出知识范围:如实说明边界,并把能接这个话题的数据包列出来。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime

from .. import persona
from ..core import (
    Context,
    Intent,
    Reply,
    Stage,
    truncate,
)
from ..domain import PACK
from ..validation.safe_eval import format_number


@dataclass
class GenerationLayer:
    """生成层:按意图分派模板。这一版没有 model 字段。"""

    name = "generation.render"

    def generate(self, ctx: Context) -> Reply:
        started = time.perf_counter()
        frame = ctx.frame
        intent = frame.intent if frame else Intent.UNKNOWN

        handler = {
            Intent.GREETING: self._gen_greeting,
            Intent.HELP: self._gen_help,
            Intent.WEATHER: self._gen_weather,
            Intent.TIME: self._gen_time,
            Intent.CALC: self._gen_calc,
            Intent.CONVERT: self._gen_convert,
            Intent.COMPARE: self._gen_logic,
            Intent.LOGIC: self._gen_logic,
            Intent.INFER: self._gen_logic,
            Intent.SEMANTIC: self._gen_semantic,
            Intent.SAFETY: self._gen_safety,
            Intent.DOMAIN: self._gen_domain,
            Intent.OFFTOPIC: self._gen_offtopic,
            Intent.UNKNOWN: self._gen_clarify,
        }.get(intent, self._gen_clarify)

        reply = handler(ctx, frame)

        # 灰区提醒:答案照给,但在前面加一条限定(医疗/法律/情绪这类)
        report = ctx.meta.get("safety")
        if report is not None and report.level == "caution" and reply.source != "refusal":
            reply = Reply(
                text=report.caution_note() + "\n\n" + reply.text,
                source=reply.source,
                citations=list(reply.citations) + ["安全:灰区提醒"],
            )

        ctx.reply = reply

        elapsed = (time.perf_counter() - started) * 1000
        ctx.record(
            Stage.GENERATE,
            self.name,
            ok=bool(reply.text.strip()),
            detail=f"intent={intent.value} source={reply.source} text={truncate(reply.text, 60)!r}",
            elapsed_ms=elapsed,
        )
        return reply

    # ---- 自述:问"你是谁 / 你能做什么" ------------------------------------
    def _gen_greeting(self, ctx: Context, frame) -> Reply:
        samples = PACK.sample_questions(3)
        text = (
            f"你好。{persona.register()}\n"
            f"这一轮我手上的是{persona.source(PACK.id)}({PACK.name}),"
            "它能答的例如:" + "、".join(samples) + "\n"
            "算术、单位换算、真假判断、归属推理这类通用能力我也还在。"
            + persona.CANNOT
        )
        return Reply(text=text, source="template")

    def _gen_help(self, ctx: Context, frame) -> Reply:
        text = (
            persona.capability() + "\n"
            f"这一轮我手上的是{persona.source(PACK.id)}({PACK.name}),"
            f"里面有 {len(PACK.entries)} 条知识条目,答完会把条目 id 给你。\n"
            f"它覆盖的领域词:{'、'.join(PACK.scope)}。"
        )
        return Reply(text=text, source="template")

    # ---- 通用能力(与基础版一致)-----------------------------------------
    def _gen_weather(self, ctx: Context, frame) -> Reply:
        city = frame.slot("city") or "该城市"
        day = frame.slot("day") or "今天"
        text = (
            f"关于 {city} {day}的天气:这个模板**没有接入任何气象数据源**,"
            "所以我不能给你温度和降水 —— 编一个数字比不回答更糟。\n"
            "要让它真的能答,需要在理解层之后插一个数据源适配器,"
            "并把返回的时间戳一并带上,便于核对。"
        )
        return Reply(text=text, source="template", citations=["未接入数据源"])

    def _gen_time(self, ctx: Context, frame) -> Reply:
        now = datetime.now()
        scope = frame.slot("scope")
        if scope == "date":
            weekday = "一二三四五六日"[now.weekday()]
            text = f"今天是 {now:%Y-%m-%d},星期{weekday}。"
        else:
            text = f"现在是 {now:%Y-%m-%d %H:%M:%S}(本机时区,读数来自系统时钟)。"
        return Reply(text=text, source="template")

    def _gen_calc(self, ctx: Context, frame) -> Reply:
        calc = (ctx.meta.get("computed") or {}).get("calc")
        if not calc:
            return self._gen_clarify(ctx, frame, reason="没有拿到可用的算式")
        text = (
            f"{calc['expression']} = {format_number(calc['value'])}\n"
            "(结果由受限求值器 safe_eval 算出,不使用 Python eval。)"
        )
        return Reply(text=text, source="template")

    def _gen_convert(self, ctx: Context, frame) -> Reply:
        conv = (ctx.meta.get("computed") or {}).get("convert")
        if not conv:
            return self._gen_clarify(ctx, frame, reason="换算没能算出来")
        text = (
            f"{format_number(float(conv['value']))} {conv['from']} = "
            f"{format_number(float(conv['result']))} {conv['to']}"
        )
        return Reply(text=text, source="template")

    def _gen_semantic(self, ctx: Context, frame) -> Reply:
        """词向量算术 / 语义邻居的渲染(离线训练的词向量,不是模型)。"""
        result = ctx.meta.get("semantic")
        if result is None or getattr(result, "kind", "none") == "none":
            reason = getattr(result, "error", "") or "这一句没走到语义处理"
            return self._gen_clarify(ctx, frame, reason=reason)

        lines: list[str] = []
        if result.outcome == "invalid":
            lines.append(result.error or "没解析出可用的语义查询")
        elif result.outcome == "unknown":
            lines.append(result.text or result.error or "语义层给不出结论")
        else:
            lines.append(result.text)
            if result.candidates:
                lines.append("候选(按向量余弦):" + "、".join(result.candidates[:3]))

        if result.basis:
            lines.append("依据:")
            for item in result.basis:
                lines.append("  · " + item)
        if result.rejected:
            lines.append("被一致性检查拒掉:" + "、".join(result.rejected[:5]))
        lines.append("(词向量由 tools/train_vectors.py 离线训练;运行期只查表 + 余弦 + 最近邻)")

        return Reply(text="\n".join(lines), source="template",
                     citations=["语义层:词向量 + 关系图"])

    def _gen_logic(self, ctx: Context, frame) -> Reply:
        """比较 / 布尔 / 推理三种结局的渲染:一律引用逻辑层算好的结论与依据。"""
        result = ctx.logic
        if result is None or result.kind == "none":
            return self._gen_clarify(ctx, frame, reason="这一句没走到逻辑处理")

        lines: list[str] = []

        if result.outcome == "invalid":
            lines.append(f"这一句我没解析成可判真假的结构:{result.error}")
            lines.append("试试这些形态:")
            lines.append("  · 北京属于华北吗")
            lines.append("  · 3公斤比2斤重吗")
            lines.append("  · 北京下雨 且 小明有伞 吗")
            lines.append("  · 如果下雨那么需要带伞")
        elif result.outcome == "unknown":
            lines.append(result.text or "依据不足,我不下结论。")
        else:
            lines.append(result.text)
            if isinstance(result.answer, bool):
                lines.append("结论:" + ("成立" if result.answer else "不成立"))
            elif result.answer is None:
                if not (result.basis and any("来自" in b or "<-" in b for b in result.basis)):
                    lines.append("结论:见下方依据")
            elif isinstance(result.answer, tuple):
                lines.append("这条事实已经在库里了。")

        if result.basis:
            lines.append("依据:")
            for item in result.basis:
                lines.append("  · " + item)
        if result.derived:
            lines.append("本次新推出:")
            for item in result.derived[:6]:
                lines.append("  · " + item)
        lines.append(f"(事实库现有 {result.store_size} 条事实)")

        return Reply(
            text="\n".join(lines),
            source="template",
            citations=["逻辑引擎:事实库 + 前向推理"],
        )

    # ---- 安全拒答(正常流程走不到:流水线在生成之前就拒答了)--------------
    def _gen_safety(self, ctx: Context, frame) -> Reply:
        """安全判定被改成放行时,别落到澄清话术上:照样给拒答话术。"""
        report = ctx.meta.get("safety")
        if report is not None and report.level == "refuse":
            return Reply(text=report.refusal_text(), source="refusal")
        return self._gen_clarify(ctx, frame, reason="命中安全规则")

    # ---- 本领域知识问答(替换掉原来的"借模型"路径)------------------------
    def _gen_domain(self, ctx: Context, frame) -> Reply:
        question = frame.slot("topic") or ctx.raw_text
        hit = PACK.lookup(question)

        ctx.meta["domain"] = {
            "question": question,
            "pack": PACK.id,
            "entry": hit.entry.id if hit.entry else None,
            "score": hit.score,
            "terms": list(hit.terms),
            "near": [e.id for e in hit.near],
        }

        if hit.entry is None:
            return Reply(text=self._domain_miss_text(question, hit), source="template",
                         citations=[f"{persona.source(PACK.id)}:未命中条目"])

        entry = hit.entry
        lines = [entry.answer]
        if entry.points:
            lines.append("要点:")
            lines.extend("  · " + point for point in entry.points)
        if entry.see_also:
            related = [PACK.entry(i) for i in entry.see_also]
            related = [e for e in related if e is not None]
            if related:
                lines.append("相关条目:" + "、".join(f"{e.question}({e.id})" for e in related))
        lines.append(
            f"(来自{persona.source_entry(PACK.id, entry.id)};离线快照,未联网核对最新版本)"
        )
        return Reply(text="\n".join(lines), source="template", citations=[f"条目:{entry.id}"])

    def _domain_miss_text(self, question: str, hit) -> str:
        lines = [
            f"这个话题在{persona.source(PACK.id)}的范围里,但知识条目里没有能对上的一条 —— 我不猜。",
        ]
        if hit.near:
            lines.append("我手头相邻的条目是:")
            lines.extend(f"  · {e.question}({e.id})" for e in hit.near)
        else:
            lines.append("我手头一条相邻的条目也没有。")
        lines.append("可以把它问得更具体一点(例如带上具体名词),我按条目检索。")
        if PACK.limits:
            lines.append("边界:" + ";".join(PACK.limits))
        return "\n".join(lines)

    def _gen_offtopic(self, ctx: Context, frame) -> Reply:
        scope_hint = "、".join(PACK.scope[:6]) + ("等" if len(PACK.scope) > 6 else "")
        lines = [
            f"这个问题不在我的知识里:{persona.source(PACK.id)}({PACK.name})"
            f"只管「{scope_hint}」这一片。",
            "这个数据包里能答的,例如:",
        ]
        lines.extend("  · " + q for q in PACK.sample_questions(4))
        if PACK.siblings:
            lines.append("如果问的其实是别的方向,我手上这些数据包能接:")
            for sibling in PACK.siblings:
                examples = " / ".join(sibling.get("examples") or ())
                folder = sibling.get("folder", sibling.get("id"))
                lines.append(
                    f"  · {sibling.get('name', folder)}({persona.source(folder)})"
                    + (f":{examples}" if examples else "")
                )
        if PACK.limits:
            lines.append("边界:" + ";".join(PACK.limits))
        return Reply(text="\n".join(lines), source="template", citations=["知识边界说明"])

    def _gen_clarify(self, ctx: Context, frame, reason: str = "") -> Reply:
        missing = list(getattr(frame, "missing", []) or [])
        head = "我没能确定你的意思" if not reason else f"这一条我处理不了:{reason}"
        lines = [head + "。"]
        if missing:
            lines.append(f"还缺:{'、'.join(missing)}。")
        lines.append("可以试试这些形态:")
        lines.append("  · " + " / ".join(PACK.sample_questions(3)))
        lines.append("  · 12*(3+4)")
        lines.append("  · 3公斤换算成斤")
        lines.append("  · 3公斤比2斤重吗")
        lines.append("  · 北京属于华北吗")
        lines.append("  · 现在几点")
        return Reply(text="\n".join(lines), source="template")
