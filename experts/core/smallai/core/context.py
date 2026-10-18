"""层间契约:上下文对象(Context)是五层之间唯一的数据载体。

传统架构的要点是**层与层之间只通过显式契约通信**,不做隐式全局状态。
这里 Context 就是那份契约:每一层只允许读上一层的产物、写自己负责的字段。

流水线:
    用户输入 --> 规则引擎/逻辑树路由 --> 理解层 --> 逻辑/语义 --> 校验层(含安全闸) --> 生成层

对应到本文件的字段:
    输入        Context.raw_text
    路由        Context.route       (RouteDecision)
    理解        Context.frame       (SemanticFrame)
    校验        Context.verdict     (Verdict)
    生成        Context.reply       (Reply)

与"基础版 smallai"的差别:
    · 开放问题**不再**交给任何模型 —— 模型端口整条拆掉;
    · 兜底意图换成两条:DOMAIN(本领域基础问答,查本地知识条目)与
      OFFTOPIC(超出本包知识范围,如实说明并列出能接的包);
    · 因此 RouteDecision 不再有 needs_model,Reply 不再有 used_model:
      这里没有任何一条路径会"借模型"。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# --------------------------------------------------------------------------
# 意图枚举:路由层与理解层共用的词表,避免两边用字符串硬比对。
# --------------------------------------------------------------------------
class Intent(str, Enum):
    GREETING = "greeting"      # 寒暄
    WEATHER = "weather"        # 天气查询(需要 city 槽)
    TIME = "time"              # 时间/日期
    CALC = "calc"              # 算术(需要 expression 槽)
    CONVERT = "convert"        # 单位换算(需要 value/from_unit/to_unit 槽)
    COMPARE = "compare"        # 数值/量比较(3公斤比2斤重吗)
    LOGIC = "logic"            # 布尔式求值(带伞 且 下雨 吗)
    INFER = "infer"            # 规则推理(北京属于华北吗)
    SEMANTIC = "semantic"      # 词向量算术(国王-男人+女人)/ 词的语义邻居
    HELP = "help"              # 能力说明
    SAFETY = "safety"          # 命中安全红线:拒答(不进入生成层)
    DOMAIN = "domain"          # 本领域基础问答:查本地知识条目,不接模型
    OFFTOPIC = "offtopic"      # 超出本包知识范围:如实说明,并列出能接的包
    UNKNOWN = "unknown"        # 连兜底都不适用


class Stage(str, Enum):
    """追踪用的层标识 —— 让每一跳都能被看见。"""

    ROUTE = "route"
    UNDERSTAND = "understand"
    LOGIC = "logic"            # 推理/比较那一跳(在理解与校验之间)
    SEMANTIC = "semantic"      # 词向量算术/关系邻居那一跳
    VALIDATE = "validate"
    GENERATE = "generate"


@dataclass
class TraceStep:
    """一次层调用留下的账:谁、干了什么、花了多久、成没成。

    传统架构里这一层常被忽略,但没有它就无法回答"这句话为什么这么答"。
    """

    stage: Stage
    actor: str          # 具体是哪个组件(rules.tree / understanding.slots / ...)
    ok: bool
    detail: str
    elapsed_ms: float = 0.0
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class RouteDecision:
    """路由层的产物。"""

    intent: Intent
    confidence: float                       # 0.0 ~ 1.0,规则树的命中比例
    matched_rule: str | None = None         # 命中的规则名,便于回溯
    path: list[str] = field(default_factory=list)   # 逻辑树走过节点的轨迹
    scores: dict[str, float] = field(default_factory=dict)  # 各意图得分
    slots_hint: dict[str, Any] = field(default_factory=dict)

    @property
    def is_confident(self) -> bool:
        return self.confidence >= 0.5


@dataclass
class SemanticFrame:
    """理解层的产物:结构化之后的一句话(意图 + 槽位 + 未决问题)。"""

    intent: Intent
    slots: dict[str, Any] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)   # 还缺哪些必填槽
    unresolved: list[str] = field(default_factory=list)  # 提到了但没解析出来的东西
    # 从这句话里读出来的"事实断言"(如『北京属于华北』),交给逻辑层入库。
    derived_fact: tuple[str, tuple[str, ...]] | None = None
    # 布尔式解析结果(只对 LOGIC 意图):嵌套的 and/or/not/atom 结构。
    logic_expr: Any = None

    def slot(self, key: str, default: Any = None) -> Any:
        return self.slots.get(key, default)


@dataclass
class Verdict:
    """校验层的产物:通过 / 不通过 / 需要澄清,外加可执行的修正建议。"""

    ok: bool
    stage: str = "validate"
    errors: list[str] = field(default_factory=list)     # 硬失败
    warnings: list[str] = field(default_factory=list)   # 软问题,不拦
    repair: str | None = None                            # 回环提示:该回哪一层重做
    repair_hint: str | None = None                       # 给人看的一句话
    checks: dict[str, bool] = field(default_factory=dict)

    @classmethod
    def passed(cls, checks: dict[str, bool] | None = None) -> "Verdict":
        return cls(ok=True, checks=checks or {})


@dataclass
class Reply:
    """生成层的产物。source 只可能是 template / refusal / empty(没有 model 这一档)。"""

    text: str
    source: str = "template"        # template / refusal / empty
    citations: list[str] = field(default_factory=list)


@dataclass
class Context:
    """贯穿五层的唯一上下文(黑盒/黑板)。"""

    raw_text: str
    request_id: str = ""
    created_at: float = field(default_factory=time.time)

    route: RouteDecision | None = None
    frame: SemanticFrame | None = None
    logic: Any = None                   # LogicResult(小 AI 的逻辑引擎产物)
    verdict: Verdict | None = None
    reply: Reply | None = None

    trace: list[TraceStep] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.request_id:
            self.request_id = f"req-{int(self.created_at * 1000) % 10_000_000}"

    # ---- 追踪 ------------------------------------------------------------
    def record(
        self,
        stage: Stage,
        actor: str,
        ok: bool,
        detail: str,
        elapsed_ms: float = 0.0,
        **data: Any,
    ) -> None:
        self.trace.append(
            TraceStep(
                stage=stage,
                actor=actor,
                ok=ok,
                detail=detail,
                elapsed_ms=round(elapsed_ms, 2),
                data=data,
            )
        )

    def steps_of(self, stage: Stage) -> list[TraceStep]:
        return [s for s in self.trace if s.stage == stage]

    def summarize_trace(self) -> str:
        lines = []
        for i, s in enumerate(self.trace, 1):
            mark = "OK " if s.ok else "FAIL"
            lines.append(
                f"  {i:>2}. [{mark}] {s.stage.value:<10} {s.actor:<26} "
                f"{s.elapsed_ms:>7.2f}ms  {s.detail}"
            )
        return "\n".join(lines)


# --------------------------------------------------------------------------
# 文本小工具:所有层共用,保持依赖单向(谁都能用 core,core 不依赖谁)。
# --------------------------------------------------------------------------
_CJK = r"\u4e00-\u9fff"
_PUNCT_RE = re.compile(rf"[\s，。！？、；:：,.!?;~～\"'“”‘’()（）\[\]【】]+")


def normalize(text: str) -> str:
    """归一化输入:全角转半角、去首尾空白、压缩标点。

    路由与理解都跑在归一化后的文本上,避免同一条规则写两遍。
    """
    if not text:
        return ""
    out = []
    for ch in text:
        code = ord(ch)
        if code == 0x3000:              # 全角空格
            out.append(" ")
        elif 0xFF01 <= code <= 0xFF5E:  # 全角 ASCII
            out.append(chr(code - 0xFEE0))
        else:
            out.append(ch)
    s = "".join(out).strip()
    s = re.sub(r"\s+", " ", s)
    return s


def has_cjk(text: str) -> bool:
    return bool(re.search(f"[{_CJK}]", text))


def tokenize(text: str) -> list[str]:
    """极简分词:中文按标点切,英文按空格切。

    真实项目该换成 jieba;这里刻意保持零依赖,规则树用的是模式匹配而非词频。
    """
    parts = [p for p in _PUNCT_RE.split(text) if p]
    return parts


def truncate(text: str, limit: int = 120) -> str:
    text = text.replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"
