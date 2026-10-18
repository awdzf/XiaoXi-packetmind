"""GoChat 框架:小析(一个人格)+ 若干个数据包。

框架负责三件事:分解 → 查找数据包 → 引用总结;**人格**从
`experts/core/smallai/persona.py` 来,与装了几个数据包无关。

知识 = **同一份核心代码**(`experts/core/smallai/`)+ **数据包**目录,
框架自动发现 `experts/packs/*/pack.json`,可以随意增删:

  · 新增知识:放一个数据包(`python experts/pack.py new <名字>` 从模板建);
  · 移除知识:删掉那个目录,框架照常启动,只是少一块知识;
  · `role: "general"` 的包是兜底,只在领域包都没命中时出场;
  · 安全底线在 framework/safety.py 自带,不依赖 ethics 包是否还在;
  · 问"你是谁 / 你能做什么"由人格直接答,不查任何数据包。

各组件也可以单独用:

    from framework import Decomposer, Router, GoChat, persona

    text = "HTML是什么?"
    dec = Decomposer().decompose(text)      # HTML / 是 / 什么 + 候选
    routing = Router().route(dec)           # -> 网页开发与设计(数据包 web_dev)
    result = GoChat().ask(text)             # 完整一轮(含总结)
    print(result.summary)
"""

__version__ = "1.0.0"

from . import (
    budget,
    calibration,
    conflicts,
    corebridge,
    datalog,
    evaluation,
    ingest,
    learning,
    lessons,
    memory,
    retrieval,
    safety,
    settings,
    trust,
)
from .calibration import Calibration, Calibrator
from .catalog import (
    CATALOG,
    DISCOVERY_ERRORS,
    ExpertCard,
    card_for,
    discover,
    general_card,
    persona,
)
from .decomposer import Candidate, Decomposition, Decomposer, keywords_of, normalize
from .memory import ConversationMemory, Turn, is_followup
from .pipeline import ChatResult, GoChat
from .planner import BoundedPlanner, PlanResult
from .relations import PackGraph
from .retrieval import BM25Index, HybridRetriever, RetrievalHit
from .router import RouteTarget, Router, Routing
from .summarizer import ExpertAnswer, first_sentence, key_points, summarize
from .tfidf import SimilarHit, TfidfIndex
from .trust import TrustMap

__all__ = [
    "__version__",
    "CATALOG",
    "DISCOVERY_ERRORS",
    "ExpertCard",
    "card_for",
    "discover",
    "general_card",
    "persona",
    "safety",
    "settings",
    "lessons",
    "memory",
    "corebridge",
    "ingest",
    "learning",
    "budget",
    "calibration",
    "conflicts",
    "datalog",
    "retrieval",
    "trust",
    "evaluation",
    "Calibration",
    "Calibrator",
    "TrustMap",
    "BM25Index",
    "HybridRetriever",
    "RetrievalHit",
    "Candidate",
    "Decomposition",
    "Decomposer",
    "keywords_of",
    "normalize",
    "ConversationMemory",
    "Turn",
    "is_followup",
    "RouteTarget",
    "Router",
    "Routing",
    "PackGraph",
    "ExpertAnswer",
    "summarize",
    "first_sentence",
    "key_points",
    "ChatResult",
    "GoChat",
    "BoundedPlanner",
    "PlanResult",
    "SimilarHit",
    "TfidfIndex",
]
