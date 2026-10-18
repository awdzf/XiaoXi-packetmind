"""校验层:任何一层的产物,在进入下一层之前都要过这道闸。

三条纪律,都是"传统架构"里最值钱的部分:
  1. **校验器是纯函数** —— 输入 frame/verdict 相关的小数据,输出判定,不产生副作用,方便单测。
  2. **不通过不是异常,是数据** —— 返回 Verdict(ok=False, errors=[...], repair=...),
     流水线照 repair 决定"回哪一层重做"或"降级",而不是抛栈崩掉。
  3. **必须真的算一遍** —— 算术类意图在这里把表达式求值(受限求值器,不用 eval),
     求不出来就是校验不过;这比让模型自由发挥可靠得多。

修复回环的两个出口:
  repair="understanding" -> 回理解层(通常意味着要问用户澄清)
  repair="generate"      -> 回生成层(通常是措辞/长度/口径问题)
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

from ..core import Context, Intent, Stage, Verdict
from ..domain.safety import assess as assess_safety
from ..understanding.layer import SLOT_SCHEMA, UNIT_TO_KIND
from .safe_eval import SafeEvalError, safe_eval

# --------------------------------------------------------------------------
# 内容安全:红线与灰区**定义在 domain/pack.json 的 safety 段**(数据),
# 判定与话术在 domain/safety.py。这里只负责把判定接进闸门。
# --------------------------------------------------------------------------

MAX_INPUT_LEN = 500
MAX_REPLY_LEN = 1200
MIN_ROUTE_CONFIDENCE = 0.3


@dataclass
class Issue:
    code: str
    message: str
    severity: str = "error"        # error = 拦下;warning = 放行但记账
    repair: str | None = None      # understanding / generate / logic


@dataclass
class ValidationLayer:
    """校验层。每个 _check_* 是一个独立校验器,加校验器不需要改主流程。"""

    name = "validation.gate"
    strict: bool = True

    def validate(self, ctx: Context) -> Verdict:
        started = time.perf_counter()
        issues: list[Issue] = []
        computed: dict[str, Any] = {}

        frame = ctx.frame
        route = ctx.route
        if frame is None or route is None:
            issues.append(Issue("E100", "上下文不完整:缺少路由或理解层产物", repair="understanding"))
        else:
            issues.extend(self._check_input(ctx))
            issues.extend(self._check_confidence(route.confidence, frame.intent))
            issues.extend(self._check_slots(ctx, frame, computed))
            issues.extend(self._check_content(ctx))

        errors = [i for i in issues if i.severity == "error"]
        warnings = [i for i in issues if i.severity == "warning"]

        # 校验算出来的中间结果(算术答案、换算结果)挂在 ctx.meta,
        # 生成层只能引用它,不能自己另算一份 —— 这是"答案唯一来源"。
        if computed:
            ctx.meta["computed"] = computed

        repair = errors[0].repair if errors else None
        hint = errors[0].message if errors else None

        verdict = Verdict(
            ok=not errors,
            errors=[f"[{i.code}] {i.message}" for i in errors],
            warnings=[f"[{i.code}] {i.message}" for i in warnings],
            repair=repair,
            repair_hint=hint,
            checks={code: True for code in ("input", "confidence", "slots", "content")} if not errors else {},
        )
        ctx.verdict = verdict

        elapsed = (time.perf_counter() - started) * 1000
        ctx.record(
            Stage.VALIDATE,
            self.name,
            ok=verdict.ok,
            detail=(
                f"ok={verdict.ok} errors={len(errors)} warnings={len(warnings)} "
                f"repair={repair or '-'}"
                + (f" computed={computed}" if computed else "")
            ),
            elapsed_ms=elapsed,
            errors=verdict.errors,
            warnings=verdict.warnings,
        )
        return verdict

    # ---- 校验器们 --------------------------------------------------------
    def _check_input(self, ctx: Context) -> list[Issue]:
        out: list[Issue] = []
        text = ctx.raw_text or ""
        if not text.strip():
            out.append(Issue("E101", "输入为空", repair="understanding"))
        elif len(text) > MAX_INPUT_LEN:
            out.append(Issue("E102", f"输入超长({len(text)} > {MAX_INPUT_LEN})", repair="understanding"))
        if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", text):
            out.append(Issue("E103", "输入含控制字符", repair="understanding"))
        return out

    def _check_confidence(self, confidence: float, intent: Intent) -> list[Issue]:
        if intent is Intent.UNKNOWN:
            return []          # 兜底意图不按置信度拦
        if confidence < MIN_ROUTE_CONFIDENCE:
            return [Issue("W201", f"路由置信度偏低({confidence})", severity="warning")]
        return []

    def _check_slots(self, ctx: Context, frame, computed: dict[str, Any]) -> list[Issue]:
        out: list[Issue] = []
        specs = SLOT_SCHEMA.get(frame.intent, [])

        # 1) 必填槽齐不齐
        #   例外:target 是"要问/要断言的谓词",而能力列举(capability_of)
        #   与"为什么"追问(why + left + right)不需要它 —— 别把它们误判成缺槽。
        for name in frame.missing:
            if name == "target" and (
                frame.slots.get("capability_of") or frame.slots.get("why")
            ):
                continue
            spec = next((s for s in specs if s.name == name), None)
            desc = spec.desc if spec else name
            out.append(Issue("E301", f"缺少必填信息:{name}({desc})", repair="understanding"))

        # 2) 槽位类型对不对
        for spec in specs:
            if spec.name not in frame.slots:
                continue
            value = frame.slots[spec.name]
            if spec.kind == "number" and not isinstance(value, (int, float)):
                try:
                    frame.slots[spec.name] = float(value)
                except (TypeError, ValueError):
                    out.append(Issue("E302", f"槽位 {spec.name} 不是数值:{value!r}", repair="understanding"))

        # 2b) 逻辑意图:表达式必须是解析成功的树,不是自由文本
        if frame.intent is Intent.LOGIC and "expression" in frame.slots:
            from ..logic.expr import Expr

            if not isinstance(frame.slots["expression"], Expr):
                out.append(Issue("E303", "逻辑式没有解析成结构(不是可判真假的命题)",
                                 repair="understanding"))

        # 3) 算术:在这里真算一遍
        if frame.intent is Intent.CALC and "expression" in frame.slots:
            expr = str(frame.slots["expression"])
            try:
                computed["calc"] = {"expression": expr, "value": safe_eval(expr)}
            except SafeEvalError as exc:
                out.append(Issue("E310", f"表达式无法安全求值:{expr}({exc})", repair="understanding"))

        # 4) 换算:量纲要对得上,而且真算
        if frame.intent is Intent.CONVERT and not frame.missing:
            frm, to = frame.slots.get("from_unit"), frame.slots.get("to_unit")
            k1, k2 = UNIT_TO_KIND.get(str(frm), ""), UNIT_TO_KIND.get(str(to), "")
            if not k1 or not k2:
                out.append(Issue("E320", f"不支持的单位:{frm} -> {to}", repair="understanding"))
            elif k1 != k2:
                out.append(Issue("E321", f"量纲不一致:{frm}({k1}) 不能换算成 {to}({k2})", repair="understanding"))
            else:
                try:
                    from .units import convert

                    value = convert(float(frame.slots["value"]), str(frm), str(to))
                    computed["convert"] = {
                        "value": frame.slots["value"], "from": frm, "to": to, "result": value,
                    }
                    if not (-1e12 < value < 1e12):
                        out.append(Issue("E322", f"换算结果越界:{value}", repair="understanding"))
                except Exception as exc:
                    out.append(Issue("E323", f"换算失败:{exc}", repair="understanding"))

        # 5) 逻辑层:推不出来 / 依据不足,都要在这里如实拦住,别让生成层瞎圆
        out.extend(self._check_logic(ctx))

        # 6) 语义层:算术的答案是"候选 + 依据",给不出来就得如实说
        out.extend(self._check_semantic(ctx))

        return out

    def _check_semantic(self, ctx: Context) -> list[Issue]:
        """校验语义层产物:answer 通过;unknown 记 warning;invalid 回理解层。"""
        result = ctx.meta.get("semantic")
        if result is None or getattr(result, "kind", "none") == "none":
            return []
        if result.outcome == "invalid":
            return [Issue("E340", f"语义层没解出来:{result.error or '原因未说明'}",
                          repair="understanding")]
        if result.outcome == "unknown":
            return [Issue("W341", f"语义层给不出结论:{result.error or result.text}",
                          severity="warning")]
        computed = ctx.meta.setdefault("computed", {})
        computed["semantic"] = {"text": result.text, "answer": result.answer}
        return []

    def _check_logic(self, ctx: Context) -> list[Issue]:
        """校验逻辑层的产物。三种结局对应三种处理:

        answer  -> 通过(结论已有依据)
        unknown -> 不拦,但记一条 warning:生成层要如实说"依据不足"
        invalid -> 错误,回理解层(通常是话没说清)
        """
        result = ctx.logic
        if result is None or result.kind == "none":
            return []
        if result.outcome == "invalid":
            return [Issue("E330", f"逻辑层没解出来:{result.error or '原因未说明'}",
                          repair="understanding")]
        if result.outcome == "unknown":
            return [Issue("W331", f"逻辑层依据不足:{result.text}", severity="warning")]
        # 答案落进 meta,生成层只能引用(与 calc/convert 同一纪律)
        computed = ctx.meta.setdefault("computed", {})
        if result.kind == "compare":
            computed["compare"] = {"text": result.text, "holds": result.answer}
        elif result.kind in ("logic", "infer"):
            computed["logic"] = {"text": result.text, "holds": result.answer}
        return []

    def _check_content(self, ctx: Context) -> list[Issue]:
        """内容安全:红线命中 = 拒答(repair=None);灰区 = 放行但记账。"""
        report = assess_safety(ctx.raw_text)
        ctx.meta["safety"] = report
        if report.level == "refuse":
            return [Issue("E400", f"命中安全红线({report.labels}),拒绝生成", repair=None)]
        if report.level == "caution":
            return [Issue("W401", f"灰区提醒:{report.labels}", severity="warning")]
        return []

    # ---- 输出侧校验(生成之后再过一遍) ----------------------------------
    def validate_reply(self, ctx: Context, text: str) -> Verdict:
        started = time.perf_counter()
        issues: list[Issue] = []

        if not text or not text.strip():
            issues.append(Issue("E501", "生成结果为空", repair="generate"))
        if len(text) > MAX_REPLY_LEN:
            issues.append(Issue("E502", f"生成结果超长({len(text)} > {MAX_REPLY_LEN})", repair="generate"))

        # 答案一致性:算了就别算错。生成层若把数字写成别的值,这里抓出来。
        computed = ctx.meta.get("computed") or {}
        if "calc" in computed:
            value = computed["calc"]["value"]
            if not _mentions_number(text, value):
                issues.append(Issue("E510", f"答复里没出现应给出的计算结果 {value}", repair="generate"))
        if "convert" in computed:
            value = computed["convert"]["result"]
            if not _mentions_number(text, value):
                issues.append(Issue("E511", f"答复里没出现应有的换算结果 {value}", repair="generate"))

        errors = [i for i in issues if i.severity == "error"]
        verdict = Verdict(
            ok=not errors,
            stage="validate_reply",
            errors=[f"[{i.code}] {i.message}" for i in errors],
            repair=errors[0].repair if errors else None,
        )
        ctx.record(
            Stage.VALIDATE,
            "validation.gate.reply",
            ok=verdict.ok,
            detail=f"ok={verdict.ok} len={len(text)}",
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )
        return verdict


def _mentions_number(text: str, value: float) -> bool:
    """答复里有没有那个数(容忍 1.0 与 1 两种写法)。"""
    for token in re.findall(r"-?\d+(?:\.\d+)?", text):
        try:
            if abs(float(token) - float(value)) < 1e-6:
                return True
        except ValueError:
            continue
    return False
