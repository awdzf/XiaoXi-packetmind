"""smallai · 小析:一个有人格的本地小 AI,知识按数据包拆装。

五层流水线(规则路由 → 理解 → 逻辑/语义 → 校验 → 生成)都在这里,一次只装配**一个**
数据包;说话的口吻与自述来自人格(`smallai/persona.py`),不是某位"专家"。

**人格与知识分开**:
  - 人格(叫什么、怎么自述、怎么承认不知道)在 `persona.py`,只有一处;
  - 知识(条目、领域词、红线)在 `experts/packs/<名字>/pack.json`,可以随便增删。

**代码只有这一份**:这份代码不知道当前装的是哪个包 ——
「这一轮用哪包」由一个数据包目录决定(里面有 `pack.json` 就是一份知识)。
增删数据包 = 增删一个目录,核心一个字节都不用改。数据包还能各自覆盖
`kb.json` / `word_vectors.json`,于是同一个核可以按包换知识、换语料。

回答边界(也是人格的三条):
  - 条目命中 -> 给答案 + 数据包与条目 id(可核对);
  - 包里有这个话题但没条目 -> 如实说没找到,给相邻条目;
  - 包外 -> 如实说不在我的知识里,并列出别的数据包能接什么。

数据包怎么选(优先级从高到低,`smallai/packs.py` 负责解析):
    1. `--pack <目录>`(命令行)/ `set_pack_dir()`(进程内)
    2. 环境变量 `SMALLAI_PACK`
    3. 从当前目录向上找到的第一个含 `pack.json` 的目录
    4. 都没有 -> 核心自带的示例包(`smallai/domain/pack.json`)

快速用法:
    python core/run.py --pack packs/math "向量场是什么?"
    python core/run.py --pack packs/math --domain
    python core/run.py --pack packs/math --health
    python core/run.py --pack packs/math --guard "某段文本"
    cd experts/packs/math && python ../../core/run.py "向量场是什么?"   # 从 cwd 认包
"""

from . import persona
from .core import Context, Intent, Reply, RouteDecision, SemanticFrame, Stage, Verdict
from .domain import PACK, ExpertPack, active_pack_dir, active_pack_file
from .generation import GenerationLayer
from .logic import FactStore, LogicEngine, LogicResult
from .packs import (
    ENV_VAR,
    available_packs,
    describe as describe_pack,
    resolve as resolve_pack,
    set_pack_dir,
)
from .pipeline import Pipeline, PipelineResult, build_pipeline
from .rules import RuleRouter
from .understanding import UnderstandingLayer
from .validation import ValidationLayer

__version__ = "0.5.0"

__all__ = [
    "build_pipeline",
    "Pipeline",
    "PipelineResult",
    "Context",
    "Intent",
    "Reply",
    "RouteDecision",
    "SemanticFrame",
    "Stage",
    "Verdict",
    "RuleRouter",
    "UnderstandingLayer",
    "LogicEngine",
    "LogicResult",
    "FactStore",
    "ValidationLayer",
    "GenerationLayer",
    "ExpertPack",
    "PACK",
    "persona",
    "ENV_VAR",
    "active_pack_dir",
    "active_pack_file",
    "set_pack_dir",
    "resolve_pack",
    "describe_pack",
    "available_packs",
    "__version__",
]
