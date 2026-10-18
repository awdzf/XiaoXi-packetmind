"""流水线装配:把五层按固定顺序串起来,并实现**修复回环**。

    用户输入
       │
       ▼
    [1] 规则引擎 / 逻辑树路由   RuleRouter           -> Context.route
       │   · 领域词/条目关键词命中 -> DOMAIN(本领域基础问答)
       │   · 都不命中               -> OFFTOPIC(如实说超出领域)
       ▼
    [2] 理解层(抽槽)          UnderstandingLayer   -> Context.frame
       │
       ▼
    [3] 逻辑层(推理/比较)      LogicEngine          -> Context.logic
       │
       ▼
    [3b] 语义层(词向量)        SemanticsIndex       -> Context.meta
       │
       ▼
    [4] 校验层(闸门 + 安全闸)  ValidationLayer      -> Context.verdict / meta["safety"]
       │  ├─ 不过 且 repair="understanding" -> 回 [2] 一次(仍不过就问澄清)
       │  └─ 不过 且 无 repair(安全红线)    -> 直接拒答,不进生成层
       ▼
    [5] 生成层                 GenerationLayer      -> Context.reply
       │   · 本领域问答只引用 pack.json 的条目
       │
       ▼
    [5b] 输出校验(答案一致性/长度)

与基础版 smallai 的差别:**模型端口整条拆掉**。
`build_pipeline` 只有一种装配方式,开放问题不会再转给任何模型 ——
改为查本地领域包,查不到就如实说不知道。

设计要点(与基础版一致):
  - 每层只通过 Context 通信,层不知道彼此的存在(装配处知道);
  - 回环有硬上限(max_loops),避免层间互相推诿导致死循环;
  - 任何一层的失败都是数据(Verdict/TraceStep),不是异常。
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from .core import (
    Context,
    Intent,
    Reply,
    Stage,
    Verdict,
)
from .domain import PACK
from .generation import GenerationLayer
from .logic import LogicEngine
from .rules import RuleRouter
from .semantics import SemanticsIndex, build_semantics
from .understanding import UnderstandingLayer
from .validation import ValidationLayer


@dataclass
class PipelineResult:
    """一次问答的完整交代:答案 + 判定 + 全部账。"""

    text: str
    ok: bool
    intent: Intent
    source: str
    verdict: Verdict | None
    context: Context
    loops: int = 0
    elapsed_ms: float = 0.0
    pack_id: str = ""
    pack_name: str = ""

    @property
    def domain_entry(self) -> str | None:
        """这一轮命中的知识条目 id(没有就是 None)—— 答案的可核对凭据。"""
        info = self.context.meta.get("domain") or {}
        return info.get("entry")

    @property
    def domain_question(self) -> str | None:
        info = self.context.meta.get("domain") or {}
        return info.get("question")

    @property
    def safety_level(self) -> str:
        report = self.context.meta.get("safety")
        return getattr(report, "level", "ok")

    def trace_report(self) -> str:
        return self.context.summarize_trace()


class Pipeline:
    """五层 + 回环的总装。"""

    def __init__(
        self,
        router: RuleRouter | None = None,
        understanding: UnderstandingLayer | None = None,
        logic: LogicEngine | None = None,
        validation: ValidationLayer | None = None,
        generation: GenerationLayer | None = None,
        semantics: SemanticsIndex | None = None,
        max_loops: int = 2,
    ) -> None:
        self.router = router or RuleRouter()
        self.understanding = understanding or UnderstandingLayer()
        self.logic = logic or LogicEngine()
        self.validation = validation or ValidationLayer()
        self.generation = generation or GenerationLayer()
        self.semantics = semantics or build_semantics()
        self.max_loops = max_loops

    # ---- 对外入口 --------------------------------------------------------
    def ask(self, text: str) -> PipelineResult:
        ctx = Context(raw_text=text or "")
        started = time.perf_counter()
        loops = 0

        # [1] 路由
        self.router.route(ctx)

        # [2] 理解
        self.understanding.understand(ctx)

        # [3] 逻辑:推理 / 布尔求值 / 量比较(只在实际需要时做重活)
        self.logic.think(ctx)

        # [3b] 语义:词向量算术与关系邻居(只在 SEMANTIC 意图时真的算)
        self.semantics.think(ctx)

        # [4] 校验 + 回环
        verdict = self.validation.validate(ctx)
        while not verdict.ok and verdict.repair == "understanding" and loops < self.max_loops:
            loops += 1
            ctx.record(
                Stage.VALIDATE,
                "pipeline.loop",
                ok=True,
                detail=f"第 {loops} 次回环:回理解层重做(原因:{verdict.repair_hint})",
            )
            self.understanding.understand(ctx)
            self.logic.think(ctx)
            self.semantics.think(ctx)
            verdict = self.validation.validate(ctx)

        if not verdict.ok and verdict.repair is None:
            # 安全红线这类:明确拒答,不进生成层 —— 这是"拒答",不是"失败"
            ctx.record(Stage.GENERATE, "pipeline.refuse", ok=True, detail="命中安全红线,拒绝生成")
            report = ctx.meta.get("safety")
            if report is not None and getattr(report, "level", "") == "refuse":
                text = report.refusal_text()
            else:
                text = ("这个请求我不能处理:" + "、".join(verdict.errors)
                        + "。\n如果你觉得判错了,可以换个说法再试。")
            reply = Reply(text=text, source="refusal")
            ctx.reply = reply
            return self._finish(ctx, reply, verdict, loops, started)

        if not verdict.ok:
            # 澄清路径:校验没过但可以问清楚 —— 生成层给一段澄清话术
            ctx.record(
                Stage.VALIDATE,
                "pipeline.clarify",
                ok=True,
                detail=f"校验未过且无法自动修复:{verdict.repair_hint}",
            )

        # [5] 生成
        self.generation.generate(ctx)

        # [5b] 输出侧校验 + 一次重试
        guard = 0
        while ctx.reply is not None:
            rv = self.validation.validate_reply(ctx, ctx.reply.text)
            if rv.ok or guard >= 1:
                break
            guard += 1
            ctx.record(
                Stage.GENERATE,
                "pipeline.loop",
                ok=True,
                detail=f"答复校验未过({rv.errors}),重生成一次",
            )
            self.generation.generate(ctx)

        reply = ctx.reply or Reply(text="(生成层没有产出)", source="empty")
        return self._finish(ctx, reply, verdict, loops, started)

    # ---- 内部 ------------------------------------------------------------
    def _finish(self, ctx: Context, reply: Reply, verdict: Verdict, loops: int, started: float) -> PipelineResult:
        elapsed = (time.perf_counter() - started) * 1000
        # ok 的判据是"有没有给出明确答复":
        #   · 安全拒答(repair=None)-> 也是明确答复;
        #   · 逻辑层给出了 answer -> 明确答复;
        #   · 其余情况(澄清、依据不足)-> 不算答复。
        answered = ctx.logic is not None and getattr(ctx.logic, "outcome", "") == "answer"
        ok = bool(reply.text.strip()) and (verdict.ok or verdict.repair is None or answered)
        ctx.record(
            Stage.GENERATE,
            "pipeline.done",
            ok=ok,
            detail=(
                f"total={elapsed:.1f}ms loops={loops} source={reply.source} "
                f"verdict_ok={verdict.ok} answered={answered}"
            ),
            elapsed_ms=elapsed,
        )
        return PipelineResult(
            text=reply.text,
            ok=ok,
            intent=ctx.frame.intent if ctx.frame else Intent.UNKNOWN,
            source=reply.source,
            verdict=verdict,
            context=ctx,
            loops=loops,
            elapsed_ms=elapsed,
            pack_id=PACK.id,
            pack_name=PACK.name,
        )


# --------------------------------------------------------------------------
# 装配工厂:这一版只有一种模式(纯规则 + 本地领域包),没有模型可选。
# --------------------------------------------------------------------------
def build_pipeline(kb_path: str | None = None, max_loops: int = 2) -> Pipeline:
    """装配流水线。

    kb_path 给了就用那份事实库 JSON(不存在则用内置库),
    于是"逻辑知识"可以按项目换,而推理骨架不动。

    注意:这里没有模型参数 —— `build_pipeline()` 只有一种装配方式,
    开放问题只查本地领域包。
    """
    logic = LogicEngine(kb_paths=[kb_path]) if kb_path else LogicEngine()
    return Pipeline(logic=logic, max_loops=max_loops)
