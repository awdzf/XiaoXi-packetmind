# 核心(`experts/core/`):小析的唯一一份代码

```
core/
├── run.py                ← 启动器:python experts/core/run.py --pack <数据包目录> ...
└── smallai/              ← 五段式小 AI 本体(路由 → 理解 → 逻辑/语义 → 校验 → 生成)
    ├── persona.py        ★ 人格:名字 / 口吻 / 自述 / 出处措辞(唯一一处)
    ├── packs.py          ★ 数据包解析:这一轮用哪个包
    ├── kb.json           核心默认事实库(任何数据包没覆盖它时用它)
    ├── domain/           条目加载 / 检索;安全判定(规则也在数据包里)
    ├── semantics/        word_vectors.json(核心默认语料)+ 词向量运算
    ├── logic/            前向推理 / 布尔求值 / 单位与量比较
    ├── rules/            规则路由(领域词 + 条目关键词)
    ├── understanding/    抽槽
    ├── generation/       生成(只引用条目原句,不造事实;口吻取自 persona)
    └── validation/       校验 + 安全闸
```

**这一份是唯一的一份。** 以前每个数据目录里都塞着一份逐字节相同的 `smallai/`,
只有 `domain/pack.json` 不同 —— 八份代码、八个升级点。现在代码在这里,
「这一轮用哪个包」由一个**数据包目录**决定。

## 人格在代码里,知识在数据包里

- **人格**(`persona.py`):叫什么、怎么自述、怎么承认不知道、怎么点明出处。
  框架(终端)与核心(`run.py`)import 的是同一个模块,所以两边一个口径。
  改名字/口吻只改它,不用碰任何数据包。
- **知识**(`packs/<名字>/pack.json`):条目、领域词、红线。加减知识不用碰人格与核心。

## 它怎么知道这一轮用哪个包

`smallai/packs.py` 按下面的顺序解析,并把选中的理由(`explicit` / `env` / `cwd` /
`bundled`)一直带到 `--health` 的输出里:

| 优先级 | 来源 | 做什么 |
|---|---|---|
| 1 | `--pack <目录>` / `smallai.set_pack_dir()` | 明确指定这一个 |
| 2 | 环境变量 `SMALLAI_PACK` | 进程级指定(GoChat 退化到子进程时也用它兜底) |
| 3 | 从当前目录向上找 `pack.json` | `cd experts/packs/math && python ../../core/run.py` 就查数学 |
| 4 | 核心自带示例包 | 什么都没指定时的兜底,`smallai/domain/pack.json`(咖啡示例) |

数据包可以**逐个文件覆盖**核心的数据:

| 文件 | 必需 | 说明 |
|---|---|---|
| `pack.json` | 是 | 身份、领域词、知识条目、安全规则;可选 `facts` / `rules` / `relations` |
| `kb.json` | 否 | 覆盖事实库(与核心默认库合并) |
| `word_vectors.json` | 否 | 覆盖词向量与关系图 |

`pack.json` 里的结构化字段(`facts` / `rules` / `relations`)会在装配时并进事实库
(`LogicEngine._merge_structured`),所以数据包可以只写事实/规则、不写问答条目;
删掉包,事实与规则一起消失。规则的格式和 `kb.json` 完全一致。

`smallai/domain.PACK` 是个**惰性代理**:同一份代码、同一个进程里换包,知识立刻换成
那一包 —— 安全规则、事实库、词向量都按包解析,不会串味。

## 跑法

```bash
# 指定数据包(最常用)
python experts/core/run.py --pack experts/packs/math "向量场是什么?"
python experts/core/run.py --pack experts/packs/math --health
python experts/core/run.py --pack experts/packs/math --domain
python experts/core/run.py --pack experts/packs/general --json "你好"
python experts/core/run.py --pack experts/packs/ethics --guard "某段文本"

# 在数据包目录里,不必写 --pack(cwd 决定)
cd experts/packs/math
python ../../core/run.py "向量场是什么?"

# 不指定任何包 -> 核心自带示例包(咖啡),用来确认核本身没坏
python experts/core/run.py "咖啡是什么?"

# 也可以当作普通 Python 包用
PYTHONPATH=experts/core python -c "from smallai import build_pipeline, set_pack_dir; set_pack_dir('experts/packs/math'); print(build_pipeline().ask('向量场是什么?').text)"
```

## 边界

- 核心不含任何**领域知识**:条目、领域词、红线全在数据包的 `pack.json` 里;
- 核心也不含模型:没有 `adapters/`,没有 `core/ports.py`,不调用任何模型;
- 核心里的 `kb.json` / `word_vectors.json` 只是"默认值",数据包可以覆盖;
- **上下文与数据包上限不在这里**:核心一次只回答一个问题(无状态);"记住上一句的关键词"
  与"一次最多查几个包"属于外壳,在 `experts/framework/memory.py` 与
  `experts/config.json`(见 [`../README.md`](../README.md) 的 4.4 / 4.5);
- **包关系(包含/被包含)与再命中也不在这里**:核心只认 `pack.json` 里的条目与规则;
  谁包含谁、反向关键词怎么扩,都是 `experts/framework/relations.py` 与
  `framework/router.py` 的事(见 [`../README.md`](../README.md) 的 4.8);
- **有界推理的“问哪几步、怎么组织依据”在 `framework/planner.py`**:核心只提供事实库、
  前向推理与查询;规划器决定先问什么、最多展示几步(见 [`../README.md`](../README.md) 第 14 节);
- **记忆数据包也是数据包**:`packs/memory/`(`role: "memory"`)一开始就是空 entries,
  空着是正常状态 —— `--health` 对它不报"entries 为空";
  写入由 `framework/lessons.py` 完成,核心只是照常检索它;
- 改核心 = 一次改,所有数据包同时受益(这正是单核的意义);
  改数据包 = 只影响那一块知识;改人格 = 只影响说话方式。
