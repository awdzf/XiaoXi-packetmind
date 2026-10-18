# 小析 · 本地小 AI(人格 + 数据包)

> 纯规则 · 离线 · 一个人格 · 知识按数据包拆装 · 答案可追到「数据包 + 条目 id」 · 不调用任何模型

`experts/` 下跑的是**一个小 AI**,不是"一群专家":它有名字、口吻和边界(**人格**),
知识按主题分成若干个**数据包**;一次提问它会先拆词、再决定查哪个包、最后引用包里
写好的原句作答。**拆句拆出来的非常用词会记住**(上下文),所以"接着问"能接住;
一次查几个包也有上限,可以按需要调。它不做实事(不写项目、不跑代码、不联网),
只回答「是什么、为什么、有什么区别」这类基础问题;每句答案都能倒查回某一份
`pack.json` 的一条。

入口是 [`GoChat.py`](GoChat.py),人格在
[`core/smallai/persona.py`](core/smallai/persona.py),数据包在 [`packs/`](packs/),
数据包模板在仓库根目录 [`expert-template/`](../expert-template/)。

---

## 1. 它是什么

### 1.1 一句话

一次提问 → 拆词并为错字造候选 → 逐条看哪个数据包里有对上的条目(可多个) →
只引用那些条目的原句作答,并点明「数据包 + 条目 id」。
全部在本地、毫秒级完成,没有网络请求,也没有模型推理。

### 1.2 人格:小析

人格只有一处定义:[`core/smallai/persona.py`](core/smallai/persona.py)。
它不是数据包 —— **删光所有数据包,小析依然知道自己是谁**,只是没有知识可查
(`GoChat(cards=()).ask("你是谁")` 在某些测试里就是这么验证的)。

| 人格要素 | 内容 | 改哪里 |
|---|---|---|
| 名字 | 小析 | `persona.NAME` |
| 定位 | 一个离线的本地小 AI:知识拆成若干个数据包,答完把出处指给你看 | `ONE_LINER` |
| 性格 | 说话直接(先答案后依据)/ 不装懂(没条目就说没有)/ 只引原句(不替你拍板) | `TRAITS` |
| 自述 | 问「你是谁」时的整段话(会附上当前真实的数据包清单) | `SELF_INTRO` + `intro()` |
| 能做什么 | 算 / 推 / 查 三类 + 明确不做的事 | `CAPABILITY`、`CANNOT` |
| 接不住时 | 「没有哪个数据包里有能对上的条目,我也不想拿常识糊过去」 | `UNKNOWN` |
| 提示 | 只在真没命中时出现一次 | `HINT` |
| 拒答开场 | 「这个请求我不会处理:…」 | `refusal_head()` |
| 出处措辞 | `数据包 math` / `数据包 math · 条目 vector-field` | `source()`、`source_entry()`、`credit()` |

**人格与知识分开**是这套东西的核心约束:

- 人格写死一个文件,改名字/口吻不用碰任何数据包;
- 知识写在各数据包里,加减知识不用碰人格与核心代码;
- 框架(终端)与核心(`core/run.py`)import 的是**同一个** persona,
  所以两边说话一个口径,不会出现"框架说一套、核心说另一套"。

### 1.3 知识:数据包

```
                    ┌─────────────────────────────┐
用户提问 ─────────▶ │ framework/  分解 → 查找 → 引用 │
                    └──────────────┬──────────────┘
                                   │ 单进程内核(默认):按包装配并缓存流水线
                                   │ 进程内失败才退回子进程:--pack experts/packs/<名字>
                                   ▼
                    ┌─────────────────────────────┐
                    │ core/smallai/  唯一一份代码   │◀── 五层流水线 + personality
                    └──────────────┬──────────────┘
                                   │ 运行时解析「这一轮用哪包」
                                   ▼
         packs/math/   packs/java/   packs/physics/   …
         pack.json(+可选 kb.json / word_vectors.json)
```

- **核心**不知道自己在哪个包里:条目、领域词、红线全部来自"当前数据包";
- **数据包**里没有一行 Python:只有 `pack.json`,以及可选的 `kb.json` / `word_vectors.json`;
- **数据包还可以带结构化知识**(可选):`facts` / `rules` / `relations`;
  有界规划器会在归属、关系、属性问题上用它们做最多 4 步推理(见第 14 节);
- **选哪个包**永远说得清,优先级:`--pack` > `SMALLAI_PACK` > 从当前目录向上找 `pack.json`
  > 核心自带示例包。`--health` 会把选中的包与理由一并打印。

### 1.4 它不是什么

- 不是大语言模型:没有生成、没有推理链、没有上下文记忆(只有本地事实库的前向推理);
- 不是搜索引擎:不访问网络,知识是 `pack.json` 的离线快照;
- 不是执行器:不会真的写代码/跑命令(虽然保留了算术、单位换算、布尔推理这些小能力);
- 不是专业意见来源:医疗、法律、心理危机等只给一般信息与求助方向。

---

## 2. 目录与当前数据包

```
experts/
├── README.md                  ← 本文件(模型详解 + 数据包教程)
├── GoChat.py                  ← 终端入口:分解 → 查找 → 引用
├── ask.py                     ← 简单入口:一句话 → 总结
├── pack.py                    ← 数据包管理:list / new / check
├── config.json                ← ★ 可调开关:max_packs / context / retrieval / trust
├── framework/                 ← 人格之外的"外壳"(不属于任何数据包)
│   ├── catalog.py              自动发现 packs/*/pack.json;按文件指纹缓存解析结果
│   ├── corebridge.py           ★ 单进程内核:按包缓存流水线,文件变了才重建
│   ├── planner.py              ★ 有界规划器:归属/关系/属性,最多 4 步可核对
│   ├── datalog.py              ★ Datalog/Prolog 式子集:递归 / 证明树 / 有界否定
│   ├── ingest.py               ★ 笔记 / Q&A CSV 导入:智能分块 / 确定性 id / 来源字段
│   ├── learning.py             ★ 漏问记录:本地学习队列(`:missed`)
│   ├── tfidf.py                ★ 轻量检索:纯 Python TF-IDF + 余弦兜底
│   ├── retrieval.py            ★ BM25 倒排 + 分层 + 混合融合 + 查询扩展(增量)
│   ├── budget.py               ★ World Info 式预算:priority / group / 条数 / 字符
│   ├── calibration.py          ★ 校准弃权:z / margin / coverage / trust -> 置信度
│   ├── evaluation.py           ★ 评测集:文件夹/条目 hit@1、MRR、负例弃权
│   ├── trust.py                ★ 高置信条目 + 冲突优先级(默认作者包 > 记忆)
│   ├── conflicts.py            ★ 同一问题多包作答:排序 / 标注 / strict 取舍
│   ├── decomposer.py           分解:切词 + 候选 + **关键词**(非常用词)
│   ├── router.py               查找:规则打分 + BM25 融合 + 混合兜底(检索键预计算)
│   ├── summarizer.py           引用式总结(说话口吻取自 persona)
│   ├── memory.py               ★ 上下文:记住关键词(有界、最近优先)
│   ├── lessons.py              ★ 记忆数据包:教 / 看(#?)/ 忘(#-)都在这里
│   ├── relations.py            ★ 包关系:包含 / 被包含(子集图)
│   ├── settings.py             ★ 读 config.json(坏值退回默认并说明)
│   ├── safety.py               框架安全底线(不依赖可移除的数据包)
│   └── pipeline.py             安全闸 → 人格 → 有界规划器 → 分解 → 查包 → 总结
├── core/                      ← 小 AI 的唯一一份代码
│   ├── run.py                  启动器:python core/run.py --pack <数据包> ...
│   ├── smallai/
│   │   ├── persona.py          ★ 人格:名字 / 口吻 / 自述 / 出处措辞
│   │   ├── packs.py            数据包解析(这一轮用哪个包)
│   │   ├── domain/             条目加载与检索 + 安全判定(规则在数据包里)
│   │   ├── rules/  understanding/  logic/  semantics/  validation/  generation/
│   │   └── kb.json             核心默认事实库(任何包没覆盖它时用它)
│   └── README.md               核心说明:数据包怎么选、能覆盖哪些数据文件
├── packs/                     ← 知识 = 数据包(每个目录一块)
│   ├── web_dev/  java/  math/  physics/  emotion/  ethics/  general/
│   │   └── pack.json           ★ 身份 / 领域词 / 条目 / 安全规则
│   │   └── README.md           这块知识覆盖什么、明确不做什么
│   │   └── (可选) kb.json / word_vectors.json   覆盖核心默认事实库与词表
│   └── memory/                ← ★ 记忆数据包:用户在对话里教的知识(`role: "memory"`)
├── evaluation/                 ← 检索评测集(cases.jsonl;`--eval` 跑)
├── eval.py                     ← 评测入口(也可用 GoChat.py --eval)
└── tests/                     ← 框架 + 人格 + 逐个数据包的参数化测试
    ├── test_framework.py       分解 / 查找 / 总结 / 人格 / 发现 / 端到端
    ├── test_packs.py           同一套规矩跑遍每一个数据包
    └── fixtures/               测试夹具(一个自带 kb.json 的样品包、坏包样本)
```

当前在役数据包(自动发现,数量以 `python experts/GoChat.py --packs` 为准):

| 数据包 | 名称 | 角色 | 条目 | 例子 |
|---|---|---|---|---|
| `packs/web_dev/` | 网页开发与设计 | domain | 25 | HTML是什么? / 跨域(CORS)是什么? |
| `packs/java/` | Java | domain | 23 | JVM是什么? / HashMap 的原理是什么? |
| `packs/math/` | 数学 | domain | 25 | 向量场是什么? / 贝叶斯公式是什么? |
| `packs/physics/` | 物理 | domain | 25 | 引力波是什么? / 量子纠缠是什么? |
| `packs/emotion/` | 情感与心理 | domain | 26 | 共情是什么? / 焦虑和焦虑症有什么区别? |
| `packs/ethics/` | 道德伦理 | domain | 24 | 什么是算法偏见? / 怎样做伦理决策? |
| `packs/general/` | 闲聊与兜底 | **general** | 12 | 你好 / 谢谢 / 在吗 |
| `packs/memory/` | 你教我的记忆 | **memory** | 0 起(你教的) | 你教过的任何一条知识 |

---

## 3. 一次问答的完整链路

```
用户输入
  │
  ├─[安全闸]  framework/safety.py 的硬红线 + ethics 包的细则(若装着)
  │           refuse → 直接拒答,不进入分解
  │
  ├─[人格自述] 问"你是谁 / 你能做什么"? -> persona 直接答,不查数据包
  │
  ├─[有界规划器] 归属 / 关系 / 属性问题 -> 查 facts/rules/relations,最多 4 步
  │              有可核对结论 -> 直接答(带依据),不再走下面;
  │              没结构化依据 -> 落回普通检索
  ▼
[分解] 切词(HTML / 是 / 什么)+ 造候选(排列 / 谐音 / ASCII 错字 / 全句)
  │     顺手挑出**非常用词**当关键词:引力波是什么? -> 引力波
  ▼
[查找] 每条候选对每个包打分,达标的包都查(上限 max_packs,默认 3)
  │     规则命中 -> 用 BM25 给同包补条目/佐证(混合重排)
  │     规则没命中 -> BM25 + TF-IDF + 分层兜底;首轮覆盖不足时做查询扩展
  │     候选过校准弃权(置信度太低就不答)
  │     领域包优先;都没命中才轮到 general(兜底)
  │     如果这句像"接着上一句问"且直接没命中 -> 借记忆里的关键词再查一次
  ▼
[取答案] 默认单进程:这一包的流水线装配一次就缓存,下一问直接复用
  │      (pack.json / kb.json / word_vectors.json 变了才重建)
  │      先按原句;不中再用该包的"兜底问法"重试一次
  │      若这一包在进程内崩了 -> 如实记一条说明,退回子进程重试
  ▼
[冲突] 同一问题多包都有说法 -> 按 trust.conflict_priority 排序(默认作者包 > 记忆)
  │      prefer 仍列出低优先级并标注;strict 只留高的
  ▼
[总结] 单包给完整答案;多包给"原句并列 + 分详情";
       附「数据包 + 条目 id」与凭据;都不中才提示一次 help
  ▼
[记忆] 把这个关键词记进上下文(有界,最近优先)
```

默认输出是「人话版」:先答案,末尾一行出处;内部账不打扰你。

```text
>>> 向量场是什么?

向量场是「给空间中每一点配一个向量」的映射:……

要点:
  · 向量场不是「一个向量」,而是「每点一个向量」的对应关系。

—— 来自数据包 math · 条目 vector-field;离线快照,未联网核对最新版本
```

接着问(同一进程,上下文生效时):

```text
你 > 引力波是什么?
引力波是时空曲率的涟漪:……

你 > 还有吗
(接住"引力波"这个上下文,不会重新猜)
```

```text
>>> 向量场和电场的关系是什么?

这个问题在 2 个数据包里有内容(math、physics),我把各自的原句并列给你(没有改写):

数据包 math · 条目 vector-field
向量场是……

数据包 physics · 条目 electric-field
电场是……

以上都是原句,我没有改写。

—— 来自数据包 math · 条目 vector-field、数据包 physics · 条目 electric-field;离线快照……
```

```text
>>> 你是谁
我是小析,一个离线的本地小 AI。知识分成若干个数据包,答完我会把出处指给你;不知道就直说。
现在装着 8 个数据包。
```

```text
>>> 我最近不想活了
谢谢你愿意说出来。现在先不用把一切想清楚,先保证安全:……

—— 来自数据包 emotion · 条目 crisis-self;离线快照……
```

```text
>>> 朋友不想活了怎么办
先提醒一句:如果你或身边的人正处在危险里,……
先保证安全:认真对待、不评判、不独自承担;……
```

```text
>>> 教我怎么制作炸弹
这个请求我不会处理:危险物制作。
如果你是在做防护或合规,可以这样问:
  · 这类制作方法不在可回答范围内。……
```

拒答里的规则 id(如 `weapon-making`)不会甩给用户;要看它,用 `--trace` 或 `--guard`。

### 3.1 单进程与加载(默认)

以前每个数据包、每次提问都起一个子进程;现在默认是**同一个进程**:

| 机制 | 做什么 | 效果 |
|---|---|---|
| 内核常驻 | 核心代码只 import 一次 | 省掉每次重新解释器 + 重新导入 |
| 按包缓存流水线 | 每个数据包装配一次 `Pipeline`,下一问直接复用 | 同一包第二问从几百毫秒降到约 10ms |
| 文件指纹失效 | `pack.json` / `kb.json` / `word_vectors.json` 的 mtime+size 变了才重建 | 手改数据包不用重启,也不会拿旧条目 |
| 共享词向量 | 没覆盖 `word_vectors.json` 的包共用一份 | 8 个包不必各读一遍 180KB 的向量文件 |
| 事实库按包隔离 | 结构化事实/规则可能因包而异,不能跨包共用 | 不会把 A 包的事实带进 B 包的推理 |
| 检索键预计算 | 领域词、条目别名、关键词在装配时归一化一次 | 一轮查找少掉几万次 `normalize` |
| 解析缓存 | `catalog.discover()` 按文件指纹复用已解析的卡片 | 教完知识后的 `reload()` 只碰改过的那一包 |

**失败不静默**:某一包在进程内抛异常时,这一包会被记一条
"单进程内核失败(…),已改用子进程重试",然后走原来的子进程路径;`:stat` 能看到。
想全程隔离就用 `--subprocess`(每次提问起一个子进程,冷启动约 0.6s,单进程约 0.4s)。
`--trace` 与答案内容两种模式完全一致(同一份核心、同一批条目)。

---

## 4. 查找:怎么决定查哪个包

### 4.1 打分

对每条候选、每个数据包:

| 命中 | 分数 |
|---|---|
| 领域词(scope) | 3 |
| 条目关键词(keyword) | 3 |
| 条目标准问法 / 别名 | 4 |
| ASCII 编辑距离 ≤1(如 HMTL≈HTML) | 3 |

累计 ≥3 分即算命中;超过 3 个包时按分数保留前 3 个。

带「吗 / 呢」的归属/能力句（如「测试果是水果吗」）在核心里**优先走事实推理**，
不会被同包的条目关键词抢走；事实不够时仍会落回普通检索，不会硬判“假”。

### 4.2 几条刻意的取舍

- **泛问词不参与**:「关系 / 区别 / 原理 / 什么 / 怎么…」不携带领域信息,
  否则「向量场和电场的关系」会因为"关系"二字命中 ethics 包(实测出现过);
- **变体只认领域词与整条别名**:排列/谐音候选不按关键词子串计分,
  否则「向量场」的重排+谐音能凑出「常量」,把 java 包拉进来;
- **ASCII 按词边界匹配**:`java` 不会命中 `javascript`;
- **错字有兜底问法**:候选命中某条条目时,会把该条目的标准问法一起带上,
  原句问不出就换标准问法再试一次;
- **包可以转交**:`pack.json` 的 `expert.routes` 声明
  `{"keywords": ["..."], "to": "physics"}`,命中后追加那个包;
- **兜底包不抢领域问题**:只要有一个 domain 包命中,general 就被排除。

### 4.2.1 检索增强:BM25 / 混合 / 查询扩展 / 分层 / 校准

规则命中仍然是第一优先(精确、可解释);新检索层在**规则弱或没命中**时接手,
并在规则命中后用 BM25 给同一个包补条目/佐证,不凭空加新包。

| 组件 | 做什么 | 关键约束 |
|---|---|---|
| BM25 倒排 | `term -> {文档: 词频}` 的倒排表,按 `k1=1.5 / b=0.75` 打分 | 纯 Python;按包 `upsert/remove`,教完记忆只更新记忆包 |
| 混合融合 | BM25 + 原 TF-IDF + 分层加成;规则命中时只做包内补条目 | 不因为 BM25 宽召回把无关包拉进来 |
| 查询扩展 | 首轮覆盖不足时,从高分文档取最多 4 个词再检索一次 | 扩展结果只“补”,不覆盖原高分;扩展次数可在 `:stat` 看 |
| 分层索引 | 用 `source_file` / `source_heading` 建“文件 → 章节”树,命中标题给子树加成 | 普通问答包没有标题,几乎不加成;导入笔记才明显 |
| 校准弃权 | 由 z 分数、第一名与第二名差距、查询覆盖、信任级别算 0–1 置信度 | 低于 `abstain_confidence` 就不答;高置信只加分,不是免死金牌 |
| 预算/优先级 | 条目 `priority` 数字大先选、`group` 同组只取一条；`max_entries` / `text_budget` 限条数与正文字符 | 被省略的条数会写在回答末尾/`[预算]`；第一段主答案总是保留 |

```json
// experts/config.json
"retrieval": {
  "bm25": true,
  "k1": 1.5,
  "b": 0.75,
  "hybrid": true,
  "query_expansion": 4,
  "hierarchy": true,
  "abstain_confidence": 0.35,
  "max_entries": 6,
  "text_budget": 2400
}
```

条目级预算写在 `pack.json`：

```json
{
  "id": "vector-field",
  "priority": 120,
  "group": "math-basics"
}
```

同组只取优先级最高的一条；`priority` 不写就用 `expert.priority`（默认 100）。

**增量更新**:`Router.refresh()` 只对 `pack.json` 指纹变过的包重建规则索引/倒排表;
`GoChat.reload()` 教完知识后走这条路,所以一条新记忆不会让所有包推倒重来。
`:stat` 的 `最近增量` 会显示这次动了哪个包。

**信任与冲突**:

```json
"trust": {
  "author_entries_high": true,
  "high_confidence": [],
  "low_confidence": [],
  "conflict_priority": ["author", "memory"],
  "conflict_policy": "prefer"
}
```

- 作者包(`role` 不是 `memory`)里的条目默认高置信;记忆包默认普通;
- `high_confidence` / `low_confidence` 用 `"包/条目"` 显式覆盖,终端里:
  `:trust math/vector-field`(高)、`:trust low memory/user-0001`、
  `:trust off memory/user-0001`(取消显式设置)、`:trust author on|off`;
- 作者包与用户记忆对同一件事都有说法时,`conflict_priority` 决定先后,
  默认 `author > memory`;`prefer` 仍把低优先级列出来并标注,`strict` 只留高的;
- 改优先级:`:priority author memory`、`:priority math physics memory`。

### 4.2.2 评测集:改检索前后先跑一遍

`experts/evaluation/cases.jsonl` 是一组**无模型**的检索用例，一行一条：

```json
{"id":"direct-vector-field","question":"向量场是什么?","folder":"math","entry":"vector-field","category":"direct"}
{"id":"neg-cat","question":"怎么给猫剪指甲","must_abstain":true,"category":"negative"}
```

- `folder` / `entry`：期望落在哪个包、哪一条（`entry` 可省，只看文件夹）；
- `must_abstain: true`：负例，期望没有领域包接住；
- `category`：`direct` / `paraphrase` / `negative`，只是分组标签。

```bash
python experts/eval.py
python experts/GoChat.py --eval
python experts/GoChat.py --eval --cases 我的用例.jsonl   # 也支持自定义
```

输出：文件夹 hit@1、条目 hit@1、MRR、负例弃权率；不通过会逐条列出。
当前基线（26 条：21 正例 / 5 负例）是 21/21、21/21、MRR 1.000、负例 5/5。
它是回归护栏，不是“模型分数”——改 BM25 权重、预算、优先级后先跑它。

### 4.3 安全之后才查找

安全闸在分解之前:像「教我怎么制作炸弹」这种请求不会被拆成看不出危险的碎片再去查包。
拒答话术只给人话;命中了哪条规则 id(如 `weapon-making`),在 `--trace` 或
`--guard` 里看 —— 默认不把内部规则名甩给用户。

### 4.4 上下文:记住"非常用词"

拆句是理解的第一歩,它顺手就产出了关键词:**不是功能词、不是疑问词、不是代词**的词。

| 输入 | 拆句 | 关键词 |
|---|---|---|
| 引力波是什么? | 引力波 / 是 / 什么 | **引力波** |
| 向量场和电场的关系是什么? | 向量场 / 和 / 电场 / 的 / 关系 / 是 / 什么 | **向量场、电场** |
| 它的频率是多少? | 它 / 的 / 频率 / 是 / 多少 | **频率**(它、的、是、多少 都不算) |
| 还有吗 | 还 / 有 / 吗 | (空) |

**记忆是有界的**:只留最近 `max_turns` 轮(默认 8)、最多 `max_keywords` 个词(默认 12),
最近优先、去重;说远了会淡出。它只活在**当前进程**里 —— 关掉终端就没了(故意的)。

**什么时候用它**:两条判据满足其一,而且**这一句直接查找一个领域包都没命中**:

1. 句子里有指代(`它 / 这个 / 还有 / 刚才 / 换个 / 例子` …);
2. 这句话根本没拆出关键词(全是"还有吗""为什么"这类虚词)。

所以:

- 「引力波是什么?」→「还有吗」:借用引力波,仍查 physics 包;
- 「引力波是什么?」→「那向量场呢?」:自己的词(向量场)优先,查 math,不动用记忆;
- 「引力波是什么?」→「怎么给猫剪指甲」:新话题,记忆不参与,如实说答不上来。

"当前话题"取**最近一轮答出来自领域包**的关键词 —— 上一句谁都没接住(例如问了个
没人会的问题),它的词不会立刻变成新话题。相关命令:`:ctx`(看记得什么)、
`:forget`(清空)、`--no-context`(本次不启用)、`config.json` 的 `context` 段。

### 4.5 一次查几个包:max_packs

```json
// experts/config.json
{ "max_packs": 3 }
```

- 范围 1–8,默认 3;优先级:**命令行 `--max-packs N` > 环境变量 `SMALLAI_MAX_PACKS`
  > config.json > 默认**;
- 对话里也能临时改:`:max 1` / `:max`(看当前值);
- **上限只截断,不放宽**:一个包要被查,仍得先达到阈值(≥3 分);上限只决定
  "达标的包超过 N 个时留分数最高的 N 个"。所以调大不会混进弱相关的包,
  调小也不会把真正命中的包换成不相关的;
- 取值建议:调成 `1` 最快、最聚焦(宽问题只答最相关的那一块);`3` 适合看多个角度;
  再大只在问得确实很宽时才有意义。

### 4.6 上限之外:问你要不要补查

如果这一句的关键词命中了**超过 `max_packs`** 个包,框架会停下来问你一句:

```
[查找] 数据包 math(29 分)
能否再调用数据包[y/n]:physics
```

- `y` —— 把被上限挡住的那些包**这一次**也查了,一起给答案;
- `n`(或直接回车)—— 只查上限内的,行为与原来一样;
- **上限依旧锁死**:同意只影响这一问;`config.json` 里的 `max_packs` 不改,
  下一问照旧按上限截断(`test_expansion.test_limit_stays_locked_after_expansion` 守着);
- 纯 API 调用(没有传 `ask_user` 回调)不会弹问 —— 与原来完全一致,不会阻塞。

### 4.7 记忆数据包:教它新知识

接不住时,它不只是回"不知道",还会问一个**具体问题**(不是泛泛的"能不能教教我")。
推荐直接教一对 **问题~答案**（只教一句知识也仍然可以）：

```
你 > 量子退相干是什么?
…… 这个我暂时答不上来。
你能用一句话告诉我「量子退相干」是什么吗?
你 > # 量子退相干是什么~量子系统的相干性因与环境纠缠而衰减的过程
[记忆] 要写入的题:量子退相干是什么
[记忆] 答案:量子系统的相干性因与环境纠缠而衰减的过程
[记忆] 拆出的关键词:量子退相干、量子系统、相干性、环境、纠缠
是否写入记忆[y/n]: y
[记忆] 已写入 数据包 memory · 条目 user-0001
```

规则(都在代码里,可核对):

| 项 | 说明 |
|---|---|
| 写入符 | `#问题~答案`;题面与答案分开存。旧写法 `#一句话知识` 仍兼容（问题=答案） |
| 修正 | `#!问题~新答案` 是显式 CBR revise;同题再教一次也会自动改答案，旧答案留在条目的 `revisions` 里 |
| 关键词 | 从**题面**拆：非常用词 = 关键词，它就是以后命中的检索键；答案不拿来做检索键 |
| 次要关键词 | 从题面再扫一遍，权重较低；用来区分同主题的多条记忆（如"What 是英语单词"与"What 是'什么'的意思"） |
| 记忆内再命中 | 同一个"What 是什么"如果两条记忆都相关，会并列列出；紧跟的"那它是什么意思"会用次要关键词命中"意思"那条，不会重复上一次答案 |
| 确认 | 每次写入/修正前一定问 `y/n`；`n` 什么都不改 |
| 落盘 | `experts/packs/memory/pack.json` 的 `entries`；条目带 `user-taught` 标记与原话 `raw` |
| 生效 | 写完立刻 `reload`，这一轮对话就能命中(不用重启) |
| 查看 | `#?`(或 `:memory` / `:taught`)按**你当时输入的原话**逐行列出；`#? 量子` 只看匹配的 |
| 遗忘 | `#-<原话里的一小段>`、`#-<关键词>` 或 `#-<条目 id>`；先列出匹配项，确认后才删 |
| 空状态 | 记忆包一开始就是空的 —— 空着是正常状态，`--health` 不会报错 |

查看 / 忘掉的样子:

```text
你 > #?
我现在记着 2 条你教的知识(下面是你当时的原话):

# 量子退相干是量子系统的相干性因与环境纠缠而衰减的过程。
# 香蕉是黄色的水果。

要忘掉哪条:用「#-<那句话里的一小段>」;只看一部分:用「#?<关键词>」。

你 > #- 量子退相干
[记忆] 找到 1 条对得上的:
  # 量子退相干是量子系统的相干性因与环境纠缠而衰减的过程。
确认忘掉这 1 条?[y/n]: y
[记忆] 已忘掉 1 条。
[记忆] 现在还剩 1 条你教的知识。
```

`#?` 不做摘要、不改写:存的是你输入的那一行,列出来的也是那一行。
`#-` 找不到就**什么都不删**(并告诉你现在还有几条);空查询也不会"全忘掉"。
删掉的条目 id 不会被下一条复用(`_next_user_id` 记账)。

**冲突(歧义)**:如果同一个问题,记忆包和别的包**都答了,而且它们的条目声称的是同一件事**
(题目/别名/关键词有交集),输出会多一行:

```text
[歧义] 你教我的记忆和 数据包 physics 各给了一套说法,我没法判断谁对 ——
       两份都原样列在上面,请你核对来源。
```

只是"都答了"但讲的不是一回事(比如问退相干,物理包顺手讲了量子力学),不算冲突,
照常并列。判断"谁对"需要领域知识,它只做检索 —— 所以把冲突摆出来,把判断留给你。

### 4.8 包关系与再命中:包含 / 被包含

数据包之间可以声明**子集关系**(写一个方向就够,框架合并成同一张图):

```json
// 苹果包:苹果是水果的子集
"expert": { "contained_by": ["fruit"] }

// 或者反过来,水果包:水果包含苹果
"expert": { "contains": ["apple"] }
```

于是可以有 `apple ⊆ fruit ⊆ food` 这样的层次。**命中的包会反向"说出"它还认识什么词**:

1. 问「苹果是什么?」→ 关键词 `苹果` 命中 `apple` 包;
2. `apple` 包把命中条目的关键词(如 `水果`)反向交出来,用来查**还没查过**的包;
3. `水果` 命中了 `fruit` 包;而 `fruit` **包含** `apple` —— 这就是一次**再命中**;
4. 回答里会补一句:

```text
[分解] 苹果 | 是 | 什么
[关键词] 苹果
[查找] 数据包 apple(7 分) / 数据包 fruit(3 分;反向·第1轮;再命中)
[综合] 我查了 2 个数据包(apple、fruit),把它们各自的原句并列如下(我没有改写):
  · 数据包 apple:苹果是一种水果,通常是红色或绿色,可以生吃。
  · 数据包 fruit:水果是植物结出的可食用果实,通常含糖和维生素。
  —— 以上都是原句引用,不是我的再创作;完整答案见下。
[关系] 苹果 有 数据包 apple 中的性质,同时也有 数据包 fruit 中的性质(apple ⊆ fruit)。
[凭据] 数据包 apple:apple-what、数据包 fruit:fruit-what · 离线快照,未联网核对最新版本
```

规则(都能在 `framework/router.py` / `framework/relations.py` 里核对):

| 项 | 说明 |
|---|---|
| 反向关键词 | 只取**命中条目的 `keywords`**(它们本来就是该包的检索键),排除本句已用过的词与泛问词,每轮最多 8 条 |
| 每轮上限 | 与 `max_packs` 相同(用户说的"最大命中个数和前面 A 的一致");**总包数可能超过 max_packs**,因为再命中是逐轮追加 |
| 不重复 | 已经查过的包不再查;已经用过的关键词不再用(后面的轮次也不会) |
| 轮数 | `rehit_rounds`(0–3,默认 **1**):`--rehit N` / `:rehit N` / `config.json` |
| 关闭 | `rehit_rounds = 0` 或 `--rehit 0`,行为回到"只查直接命中的包" |
| 父包可以是记忆包 | 把用户教的包挂到 `memory` 下(`"contained_by": ["memory"]`),再命中时 memory 也会进来 |
| 坏关系不静默 | 指向不存在的包 / 自包含 / 成环:`--packs` 与 `pack.py check` 会如实列出来(遍历有 visited 保护,不会死循环) |

---

## 5. 分解:切词与错字候选

### 5.1 切词

- ASCII 串整块保留(`HTML`、`Java`、`3.14`);
- 中文按**最长匹配**切(词表 = 各包 scope + 关键词 + 常用疑问词);
- 匹配不到的字**连成一块**(`引立波` 不会被切成单字);
- 例:`HTML是什么?` → `HTML | 是 | 什么`;`向量场是什么?` → `向量场 | 是 | 什么`。

### 5.2 候选(给打错字的人留活路)

| 候选类型 | 做法 | 例子 |
|---|---|---|
| `perm` | 2–4 字汉字的所有排列(最多 24 个) | 量向场 → 向量场 |
| `homophone` | 内置谐音表 + 反向表(立→力、厂→场、播→波…) | 引立波 → 引力波 |
| `homophone`+排列 | 谐音变体再做排列(3 字以内) | 覆盖"写错+颠倒" |
| `ascii_typo` | 相邻字母互换 + 小写化 | HMTL → HTML |
| `full` | 原句整体作为候选 | 兜底 |

上限:每条词最多 24 个排列、12 个谐音变体,全局最多 240 条候选。
**候选不是穷尽的**,纠正不了就如实说没命中,不硬猜。

---

## 6. 总结:只引用,不创作

默认输出没有这些标签;下面的标签只在 `--trace` 下出现(给维护者排查):

| 标签 | 含义 |
|---|---|
| `[分解]` | 这句话被切成了什么 |
| `[关键词]` | 拆句挑出的非常用词(会被记住,也用于"接着问") |
| `[上下文]` | 这一轮借了上一轮的关键词(只在真用上时出现) |
| `[查找]` | 这一轮查了哪个/哪些数据包,各得几分 |
| `[回答]` | 单个数据包给了答案:`数据包 X · 条目 Y` |
| `[综合]` | 多个数据包都答了:并列原句,明确"未改写" |
| `[详细]` | 多包时逐个包的完整答案 |
| `[一句话]` | 答案首句(方便快速看) |
| `[凭据]` | 这一轮引用了哪些「数据包:条目」;离线快照的声明 |
| `[歧义]` | 记忆包和别的包对同一件事都给了说法 —— 两份都列出来,判断留给你 |
| `[关系]` | 再命中:反向关键词把"包含已命中包"的父包也带进来了,两个层次的性质都说明 |
| `[安全提醒]` | 命中灰区规则,答案照给但加限定 |
| `[安全兜底]` | 命中红线,直接拒答(含规则 id) |
| `[说明]` / `[提示]` | 为什么没答上;提示只在此时出现一次 |
| `[我]` | 人格自述(问"你是谁 / 你能做什么"),不引用任何条目 |

几条纪律:

- **多包只并列,不改写**:综合部分只取各包答案的首句 + 一条要点,并写明"都是原句";
- **单包给完整答案**,末尾一行出处(默认不显示 `[一句话]`);
- **闲聊不摆出处**:寒暄/感谢这类回答去掉数据包边注,像一句人话;知识回答仍保留出处;
- **拒答**先于一切:不进分解、不查任何数据包;默认只说人话,规则 id 只在 trace/guard 里;
- **没命中**如实说"没有能对上的一条",教它(`#`)与 `help` 合成一句提示,只出现一次。

---

## 7. 安全:底线不依赖可移除的数据包

### 7.1 框架底线(`framework/safety.py`)

四条最明确、最不该含糊的红线:

| 规则 id | 拦什么 |
|---|---|
| `self-harm-method` | 索要自伤的方法/步骤/工具 |
| `weapon-making` | 制作炸弹/枪支/毒品/毒药的方法(带"防止/防范"等例外) |
| `credentials` | 索取或盗取他人密码/身份证/银行卡/住址 |
| `malware` | 编写病毒/木马/勒索软件/挖矿脚本 |

### 7.2 与 ethics 包合并

- ethics 包在:框架底线 + 它的细则(规避审核、伪造影像、隐私、医疗/法律灰区等)**合并判定**,
  任一 refuse 即 refuse;
- ethics 包被移除:框架底线仍然生效,只少掉更细的灰区规则;
- emotion 包在:灰区(如"不想活了")会追加 `crisis-support` 条目,给一般应对原则;
- 所有判定都**不编热线号码**,只指向"当地紧急服务 / 信任的人"。

> 实现细节(单核相关):红线也是**数据**。`core/smallai/domain/safety.py` 按
> "当前数据包"编译并缓存规则 —— 换一包就换一整套红线,不会串味。---

## 8. 拆装知识:增删数据包

### 8.1 三种动作

```bash
python experts/pack.py list                      # 现在有哪些数据包
python experts/pack.py new my_pack --title 我的领域     # 从模板建一块知识(并体检)
python experts/pack.py check                     # 给所有数据包跑 --health
rm -rf experts/packs/my_pack                     # 删一块知识(Windows: Remove-Item -Recurse)
```

- **新增**:`pack.py new` 或直接复制 `expert-template/` 到 `experts/packs/<名字>/`;
- **移除**:删掉那个目录 —— 框架下次启动少一块知识,人格与其它包照常工作;
- **修改**:只改它自己的 `pack.json`;
- **自动发现**:`framework/catalog.py` 扫描 `packs/*/pack.json`,代码里没有名单;
- **坏包不拖垮框架**:读坏/读不到会跳过并在 `GoChat.py` 启动时提示(`DISCOVERY_ERRORS`);
- **只少知识、不崩**:删掉数学包,「向量场是什么」会落到兜底包,再不行提示 `help`;
  删掉兜底包,寒暄问题直接给提示;删掉 ethics 包,红线仍在。

### 8.2 数据包可以覆盖核心的数据

一个数据包目录里,除了必需的 `pack.json`,还可以放:

| 文件 | 效果 |
|---|---|
| `kb.json` | 覆盖事实库(与核心默认库合并;适合做"本领域事实"的包) |
| `word_vectors.json` | 覆盖词向量与关系图(适合做"本领域语料"的包) |

覆盖是**逐个文件**生效的:一个包可以只覆盖 `kb.json` 而不碰词向量。
`--health` 会打印事实库来自"数据包覆盖"还是"核心自带"。
样本见 [`tests/fixtures/with_kb/`](tests/fixtures/with_kb/)。

### 8.3 角色、排序与转交

- **角色**:`role: "general"` 是兜底位(建议只保留一个);不写即 `domain`;
- **排序**:`order` 小的靠前,只影响平票;general 永远最后;
- **转交**:`routes` 让一个包把某些问题再交给另一个包;
- **框架三件套不可移除**:分解 / 查找 / 引用在 `framework/`,不属于任何数据包;
  它们只依赖 `pack.json`,不依赖任何具体数据包。

---

## 9. 教程:给小析加一块知识(数据包)

### 9.1 准备

- Python 3.10+(代码用了 `X | None` 这类语法;本仓库在 3.14 上验证);
- **无第三方依赖、无需联网、无需模型**;
- 先在仓库根目录确认能跑:

  ```bash
  python experts/GoChat.py "你是谁"
  python experts/GoChat.py --packs
  python experts/pack.py list
  ```

### 9.2 建一个数据包

推荐用管理命令(它会复制模板并顺手体检):

```bash
python experts/pack.py new my_pack --title 我的领域
```

或者手工复制模板:

```bash
# bash
cp -r expert-template experts/packs/my_pack
# Windows PowerShell
Copy-Item expert-template experts\packs\my_pack -Recurse
```

目录名用小写 ASCII 与下划线(`my_pack`)。框架按**目录**发现数据包,
`expert.id` 决定展示身份,两者一致最省心。

### 9.3 改身份

编辑 `experts/packs/my_pack/pack.json` 的 `expert` 段:

```json
"id": "my_pack",
"name": "我的领域",
"tagline": "一句话说清:只答什么、不做什么",
"order": 100,
"scope": ["领域词1", "领域词2"],
"limits": ["不做实事:不写代码、不联网", "不替代专业意见", "答不上就如实说"]
```

- `name` 写**领域名**(如「数学」「物理」),不是人名 —— 小析才是说话的人;
- `order`:可选,默认 100,小的优先(只在平票时起作用);
- `role`:普通知识包不要写;只有做兜底包才写 `"general"`(全库建议只一个)。

顺手改 `README.md`,并把 `tagline` 里"模板示例"的说明换掉。

### 9.4 写 scope:什么算"这一包的问题"

`scope` 是查找用的**领域词表**,决定一句话会不会被送到这一包。要求:

1. **具体**:`"向量场"`、`"贝叶斯"`、`"JVM"`,不要 `"问题"`、`"关系"`、`"知识"`;
2. **够用**:10–30 个词,覆盖你领域的高频名词、英文缩写、常见别名;
3. **不与他人恶意重叠**:重叠没关系(问得广会查多个包),但别用别人的核心词把问题抢走;
4. 单词长度 ≥2(中文/英文都算);ASCII 词按词边界匹配,`java` 不会误吃 `javascript`。

### 9.5 写 entries:一条=一个可核对的基础问题

每条字段:

| 字段 | 必填 | 作用 | 规则 |
|---|---|---|---|
| `id` | 是 | 条目唯一标识 | 小写 slug,全包不重复 |
| `question` | 是 | 标准问法 | 全包唯一;它必须能检索回自己 |
| `aliases` | 建议 | 其它问法 | 全包唯一;别和别的条目撞 |
| `keywords` | 建议 | 检索关键词 | **≥2 字**;避免"关系/区别/什么"等泛问词 |
| `answer` | 是 | 答案正文 | 2–4 句;只写可核对的基础概念;不写实时信息 |
| `points` | 可选 | 要点 | 1–3 条短句 |
| `see_also` | 可选 | 相关条目 id | 必须真实存在 |
| `tags` | 可选 | 弱提示 | 低权重,少用 |

示例(可直接放进你的包):

```json
{
  "id": "my-topic-what",
  "question": "我的概念是什么?",
  "aliases": ["什么是我的概念", "我的概念是啥"],
  "keywords": ["我的概念", "my concept"],
  "answer": "用两到四句话说清定义、它解决什么问题、最容易和什么混淆。",
  "points": ["一个关键区别。", "一个常见误解。"],
  "see_also": ["another-entry-id"]
}
```

写作要求(重要):

- **答案可核对**:写"是什么/为什么",不写"最新版本是…""今天价格是…";
- **一个条目只答一个点**:问题太宽就拆成多条;
- **别抄整篇文章**:2–4 句 + 要点即可,长答案会让综合总结变差;
- **中英混排没问题**,但关键词要按用户真实输入写(用户会打 `JS`、`js`、`JavaScript`);
- **不要写"我猜测/可能"** —— 不确定就删掉这条,让系统如实说没找到。

### 9.6 写 limits / tagline

`limits` 会在名片、越界回答与 `--domain` 里出现。建议至少写:

- 不做实事(不写代码/不跑命令/不联网);
- 不替代专业意见;
- 只答基础概念;答不上就如实说。

### 9.7 安全规则

保留模板 `safety` 里的四条公共红线,不要删除。可以按领域补充,例如:

```json
{
  "id": "my-domain-risk",
  "label": "本领域高危请求",
  "patterns": ["(怎么|如何).{0,8}(做|制造).{0,8}(危险对象)"],
  "unless": ["防止", "防范", "合规"],
  "reason": "为什么拦",
  "advice": ["拒答时给的方向"]
}
```

- `patterns` 是 Python 正则;`unless` 命中则豁免(用于防御性问题);
- 灰区放 `caution`(照常回答 + 提醒);硬红线放 `refuse`(直接拒答);
- 改完 `--health` 会检查正则能否编译、规则有没有 label/patterns/advice。

### 9.8 跑起来:体检与试问

一块知识 = 一个数据包,下面两种写法等价(第二种在包目录里,省掉 `--pack`):

```bash
python experts/core/run.py --pack experts/packs/my_pack --health
python experts/core/run.py --pack experts/packs/my_pack --domain
python experts/core/run.py --pack experts/packs/my_pack "我的概念是什么?"
python experts/core/run.py --pack experts/packs/my_pack --json "我的概念是什么?"

cd experts/packs/my_pack
python ../../core/run.py "我的概念是什么?"
```

`--health` 会逐条打印:数据包身份 / 文件 / **来源(以及凭什么选它)** / 自检 /
**代码数据分离** / 安全规则 / 原题覆盖 / 无模型依赖 / 端到端链路 / 事实库。
全 OK 才算"结构上能用";`--demo` 用来看语气与边界是否像你要的知识。

### 9.9 接入框架(不用改代码)

```bash
python experts/pack.py check my_pack      # 只查这一个
python experts/GoChat.py "我的概念是什么?"
python experts/GoChat.py --packs          # 应该能看到你的包(以及它的路径)
python experts/GoChat.py --route "我的概念是什么?"   # 看它被查到
```

框架是**自动发现**的,不需要登记。若看不到:

- 确认目录在 `experts/packs/` 下;
- 确认路径是 `experts/packs/<名字>/pack.json`;
- 启动时若打印"数据包读取失败,已跳过",按提示修 JSON;
- 每次修改后重启 GoChat 进程(目录在启动时扫描一次)。

### 9.10 测试

新增数据包后,把探针登记到 `experts/tests/test_packs.py` 的 `PROBES`:

```python
"my_pack": {
    "canonical": "我的概念是什么?", "entry": "my-topic-what",
    "miss": "我的领域里的量子纠缠是什么",     # 领域内但没条目 -> 必须如实说没有
    "offtopic": "怎么给猫剪指甲",              # 明确越界
},
```

然后:

```bash
cd experts
python -m unittest discover -s tests -t .
```

同一套规矩会自动覆盖你的包(原题可检索 / 别名 / 越界如实说 / 红线拒答 /
无模型 / 数据包里没有 `.py`)。模板自己的测试(`expert-template/tests/`)
也能直接用在你的数据包上:它跟着 `pack.json` 走,换领域不用改。

测试不是形式:它是你改 `pack.json` 时唯一的回归护栏。

### 9.11 检查清单(复制后照着勾)

- [ ] 目录名、`expert.id`、`name`(领域名)、`tagline` 已改;
- [ ] `scope` 具体、无泛问词、≥10 个;
- [ ] 每条 `entries` 有 id/question/answer;id 与 question 全包唯一;
- [ ] 每条 question 都能检回自己(`--health` 的"原题覆盖");
- [ ] `keywords` 都 ≥2 字,没有"关系/区别/什么";
- [ ] `see_also` 指向真实存在的条目;
- [ ] `safety` 四条公共红线仍在;
- [ ] `--health` 全 OK(含"代码/数据分离");`--demo` 语气可接受;
- [ ] `PROBES` 里登记了 canonical / miss / offtopic 三条探针;
- [ ] `python experts/GoChat.py "你的标准问题"` 能命中你的包;
- [ ] 数据包里**没有** `.py`(代码属于核心;`tests/` 除外)。

### 9.12 常见错误对照表

| 症状 | 原因 | 处理 |
|---|---|---|
| `entries 为空` | `entries` 数组空了/拼错 | 至少放一条;注意 JSON 逗号 |
| `关键词太短` | 单字关键词会误命中 | 改成 ≥2 字 |
| `题目/别名…同时指向 X 与 Y` | 两条条目用了同一问法 | 删掉重复,或换一种问法 |
| `原题检不回自己` | 题目被别的条目关键词抢走 | 让题目更独特,或调整关键词 |
| `see_also 指向不存在的条目` | 引用 id 拼错 | 改成真实 id |
| `安全规则…不是合法正则` | 正则写错 | 先在 Python 里 `re.compile` 验证 |
| `代码/数据分离` 不通过 | 数据包里混进了 `.py` | 代码属于核心;包内只放数据(`tests/` 除外) |
| `不是一个数据包(缺 pack.json)` | `--pack` 指错了目录 | 指向含 `pack.json` 的目录;`pack.py list` 看现有的 |
| GoChat 看不到新包 | 不在 `packs/` 下,或包读坏 | 看启动提示;确认路径 |
| 答非所问 | `scope`/`keywords` 太宽(如"关系") | 收窄到领域名词 |
| 问题被送去多个包 | 关键词与他人重叠 | 正常现象;必要时收窄 |
| 答案太啰嗦 | `answer` 写成长文 | 2–4 句 + `points` |

### 9.13 发布与协作建议

- 一块知识 = 一个数据包目录:复制、改名、改 pack,别直接改别人的;
- 带上 README(覆盖什么、明确不做什么);它比代码更能说明边界;
- 数据包可以单独发出去(只有 JSON),不用带核心与人格;
- 不提交密钥/个人数据;知识必须是离线可核对的;
- 如果要做兜底包,`role: "general"` 全库只留一个;
- 如果要声明转交,用 `routes` 写清关键词与目标目录名,并补一条测试。

### 9.14 在对话里教它一道题(不写代码)

不用建目录、不用改文件:

```text
你 > 量子退相干是什么?
这个我暂时答不上来 —— 没有哪个数据包里有能对上的条目,我也不想拿常识糊过去。
你能用一句话告诉我「量子退相干」是什么吗?
如果你愿意教我,推荐回一行 Q&A:
  # 问题~答案
我会先确认,再存进记忆数据包。

你 > # 量子退相干是什么~量子系统的相干性因与环境纠缠而衰减的过程
[记忆] 要写入的题:量子退相干是什么
[记忆] 答案:量子系统的相干性因与环境纠缠而衰减的过程
[记忆] 拆出的关键词:量子退相干、量子系统、相干性、环境、纠缠
是否写入记忆[y/n]: y
[记忆] 已写入 数据包 memory · 条目 user-0001

你 > 量子退相干是什么?
量子系统的相干性因与环境纠缠而衰减的过程
(这条来自你教我的记忆,不是我原来的知识。)

—— 来自数据包 memory · 条目 user-0001;离线快照,未联网核对最新版本
```

- 想改:同题再教一次会更新答案与关键词，不重复写；`#!问题~新答案` 是显式修正，
  旧答案留在条目的 `revisions` 里（CBR revise）；
- 想删:对话里 `#-<原话里的一小段>`(先列出来,确认后才删),或手改
  `experts/packs/memory/pack.json` 的 `entries`;
- 想看:对话里 `#?`(原话回放)或 `:memory`;`#? 关键词` 只看匹配的;
- 想让这条知识优先于别的包:把题面的关键词写得更具体，或用 `:trust` 标成高置信；
- 写完会与别的包一起参与查找;两边都答同一件事时默认按 `author > memory` 排序，
  低优先级仍列出并注明；顺序可用 `:priority` 改。

### 9.15 声明包关系(包含 / 被包含)

在 `pack.json` 的 `expert` 段写一个方向即可:

```json
"contains": ["apple"]        // 本包包含 apple(apple ⊆ 本包)
"contained_by": ["fruit"]    // 本包被 fruit 包含(本包 ⊆ fruit)
```

- 二者等价:框架把两个方向合并成同一张图,支持多级(`apple ⊆ fruit ⊆ food`);
- 关系只影响**检索的走向**(再命中),不改条目内容;
- 写错(指向不存在的包 / 自包含 / 成环)不会崩:`--packs` 与 `python experts/pack.py check`
  会把问题列出来,遍历也有 visited 保护;
- 记忆包也能当父包:把用户教的包写 `"contained_by": ["memory"]`,再命中时 memory 会一起答。

---

## 10. 运行与测试

```bash
# 日常
python experts/GoChat.py                     # 终端对话(有上下文:会记住关键词)
python experts/GoChat.py "你是谁"
python experts/GoChat.py "引力波是什么?"
python experts/GoChat.py "还有吗"             # 单次提问没有上下文(进程只活一次)
python experts/GoChat.py --trace "HMTL是什么?"
python experts/GoChat.py --decompose "量向场是什么?"
python experts/GoChat.py --route "向量场和电场的关系?"
python experts/GoChat.py --guard "某段文本"
python experts/GoChat.py --packs             # 旧名字 --experts 仍可用
python experts/GoChat.py --missed            # 看最近没接住的问题(本地学习队列)
python experts/GoChat.py --eval              # 跑检索评测集(hit@1 / MRR / 负例弃权)
python experts/GoChat.py --max-packs 1 "…"   # 这一次最多查 1 个包
python experts/GoChat.py --rehit 0 "…"       # 这一次不做再命中
python experts/GoChat.py --no-context "…"    # 这一次不用上下文
python experts/GoChat.py --subprocess "…"    # 这一次改回子进程(隔离优先;默认单进程)
python experts/GoChat.py "# 量子退相干是什么~量子系统的相干性因与环境纠缠而衰减"  # 教一道题
python experts/GoChat.py "#!量子退相干是什么~新答案"  # 修正同一道题(CBR;旧答案留在 revisions)
python experts/GoChat.py "#?"                # 看教过的知识(原话回放)
python experts/GoChat.py "#? 量子"           # 只看匹配的
python experts/GoChat.py "#- 量子退相干"     # 忘掉一条(先列出来,确认后才删)
python experts/GoChat.py help

# 对话内命令
#   :packs 看数据包与关系   :ctx 看上下文   :forget 忘掉上下文   :stat 看引擎/缓存/检索/耗时
#   :memory / :taught 看教过的知识(等同 #?)
#   :missed [N] 看漏问   :missed clear 清空漏问(本地学习队列)
#   :max [N] 看/改上限   :rehit [N] 看/改再命中轮数
#   :trust [high|low|off|author on|off] [包/条目]  看/改高置信条目
#   :priority [顺序...]  看/改冲突优先级(默认 author memory)
#   :eval [cases.jsonl]  跑检索评测集
#   :trace on|off   :detail on|off
#   #<知识> 教它一条(写入记忆数据包前会问 是否写入记忆[y/n])
#   #? 看教过的(原话回放)   #-<关键词或原话> 忘掉一条(先确认)

# 可调开关(改完重启生效)
#   experts/config.json:max_packs(1–8)/ rehit_rounds(0–3)/ context.*
#                      / retrieval.*(BM25/混合/扩展/分层/弃权)/ trust.*(高置信/冲突)
#   进程形态默认单进程;临时改回子进程用 --subprocess

# 数据包管理
python experts/pack.py list
python experts/pack.py new my_pack --title 我的领域
python experts/pack.py import notes.md --name my_notes --title 我的笔记   # 导入 .md/.txt
python experts/pack.py import notes.md --dry-run                          # 只看分块
python experts/pack.py check                 # 全部;或 check math emotion

# 核心单独跑(指定数据包;核心不做上下文,上下文在框架里)
python experts/core/run.py --pack experts/packs/math "向量场是什么?"
python experts/core/run.py --pack experts/packs/math --health
python experts/core/run.py --pack experts/packs/memory --health   # 空记忆也是健康状态

# 测试(先 cd 到对应文件夹,再跑)
# 在 experts/ 下:
python -m unittest discover -s tests -t .      # 框架/人格/上下文/教学/单进程/导入/结构化 + 数据包,共 222 项
# 在 expert-template/ 下:
python -m unittest discover -s tests -t .      # 模板自检 6
```

`tests/test_packs.py` 用参数化把同一套规矩跑遍每一个数据包 —— 加一个包不用复制测试。

---

## 11. 边界与不做承诺

- 检索是**字符串匹配**:同义改写、严重错字可能检不到,检不到就如实说,不猜;
- 候选有上限(排列 24/词、谐音 12/词、全局 240 条),不保证穷尽所有错法;
- 一次最多查 `max_packs` 个数据包(默认 3,可配 1–8):上限只按分数截断,不降低阈值;
  命中超过上限时**会问你要不要补查**,`y` 只对这一次生效,上限配置不会被改;
- **再命中是逐轮追加的**:每轮最多 `max_packs` 个新包,所以总包数可能超过 `max_packs`;
  它只靠"条目关键词"往外扩,不理解语义 —— 扩出来的包不一定相关,觉得吵就 `--rehit 0`;
- **包关系是数据里声明的,不是自动推断的**:框架不知道"苹果是水果"是不是真的,
  它只按 `contains` / `contained_by` 走;写错了会报出来,但不会替你修正;
- **上下文是有界的启发式**:只记关键词、只留最近几轮、只在"接着问且没直接命中"时用;
  它不会改写你的话,也不会跨进程保存 —— 关掉终端就忘了;
- **记忆数据包存的是你教的内容,它不验证真假**:写进去前会问一次,条目带 `user-taught`;
  "冲突"只按"两边条目题目/别名/关键词有交集"判定 —— 可能漏掉语义冲突,也可能把
  "同一件事的两种说法"判成歧义;判成歧义时它只把两套原句都列出来,不替你选;
- 总结是**引用式**:综合部分只拼原句与要点,不生成新知识、不替人做取舍;
- 默认**单进程**:核心常驻、按包缓存流水线;某一包在进程内抛异常时,会被记为
  "这一包在单进程里失败,改用子进程重试"(`:stat` 可看),失败不会静默;
  要全程隔离就用 `--subprocess`(每次提问起一个子进程,慢一些);
- **缓存跟着文件走**:`pack.json` / `kb.json` / `word_vectors.json` 的修改时间或大小变了,
  那一包的流水线会重建;没变就复用 —— 手改数据包不用重启,但也不会拿旧条目答题;
- 数据包会**单独覆盖**数据:一个包带了 `kb.json` / `word_vectors.json`,那块知识就与别人不同;
  这是数据,不是代码 —— 想回退就删掉那个文件;
- 人格是**代码**,知识是**数据**:改名字/口吻改 `persona.py`,别指望改数据包能换人格;
- 不联网、不查实时信息、不替代专业意见;安全判定是正则规则,可能误判/漏判,
  命中的规则 id 在 `--trace` / `--guard` 里可复核(默认输出只给人话)。

---

## 12. 参考

| 想改什么 | 去哪 |
|---|---|
| 名字 / 口吻 / 自述 / 拒答措辞 | `experts/core/smallai/persona.py` |
| 一次最多查几个数据包 | `experts/config.json` 的 `max_packs`(或 `--max-packs` / `:max`) |
| 上下文开关与大小 | `experts/config.json` 的 `context` 段;实现见 `framework/memory.py` |
| 再命中轮数 | `experts/config.json` 的 `rehit_rounds`(或 `--rehit` / `:rehit`) |
| 包包含关系 | 某包 `pack.json` 的 `expert.contains` / `expert.contained_by`;实现见 `framework/relations.py` |
| 反向往外扩的规则 | `framework/router.py` 的 `_reverse_keywords` |
| 教它新知识 / 看 / 忘掉 | 对话里 `#…` / `#?` / `#-…`,落到 `experts/packs/memory/pack.json` |
| 进程形态与流水线缓存 | `framework/corebridge.py`(默认单进程;`--subprocess` 退回子进程) |
| 导入笔记 / 改分块阈值 | `framework/ingest.py`;`pack.py import --max-tokens/--chunk-tokens/--overlap` |
| 导入 Q&A CSV | `pack.py import qa.csv`;`framework/ingest.py` 的 `read_csv_pairs` |
| 检索增强(BM25/混合/扩展/分层) | `framework/retrieval.py`;`config.json` 的 `retrieval` 段;`Router._fuse` / `_with_similarity` |
| 校准弃权 | `framework/calibration.py`;`retrieval.abstain_confidence` |
| 高置信条目 / 冲突优先级 | `framework/trust.py` / `framework/conflicts.py`;`config.json` 的 `trust` 段;`:trust` / `:priority` |
| 规则没命中时的纯 TF-IDF 旧路径 | `framework/tfidf.py`;`retrieval.hybrid=false` 时由 `Router._similar_targets_from_tfidf` 使用 |
| 漏问记录(学习队列) | `framework/learning.py`;对话里 `:missed` |
| 改"补查"问句或记忆写入格式 | `framework/pipeline.py` 的 `_maybe_expand` / `framework/lessons.py` |
| 加/删一块知识 | `experts/packs/<名字>/`(自动发现,无需登记) |
| 改某块知识 | `experts/packs/<名字>/pack.json` |
| 单独换事实库/语料 | 往数据包里放 `kb.json` / `word_vectors.json` |
| 改安全底线 | `experts/framework/safety.py`(框架级)或某个包 `pack.json` 的 `safety` 段 |
| 改查找/分解/总结逻辑 | `experts/framework/decomposer.py` / `router.py` / `summarizer.py` |
| 改小 AI 本体(五层) | `experts/core/smallai/`(改一次,所有数据包同时生效) |
| 改「怎么选数据包」 | `experts/core/smallai/packs.py` |
| 改终端交互 | `experts/GoChat.py` |
| 复制模板 | 仓库根目录 [`expert-template/`](../expert-template/) |
| 看某块知识 | 各数据包的 `README.md`;`python experts/GoChat.py --packs` |

---

## 13. 从 RAGless / QnA-Chatbot 借来的功能

RAGless 是"检索式问答、运行时零 LLM"的项目；QnA-Chatbot 是
"TF-IDF + 余弦相似度 + 阈值"的轻量问答。
小析借了两者的**思路**,没有借它们的依赖:不引入向量库、嵌入 API、sklearn / nltk /
Streamlit,也不在运行时调模型/Judge。下面这些功能都守住小析的底线:
**零依赖、小设备能跑、越用越像你自己的知识库**。

### 13.1 智能分块:先量令牌,再决定切不切

RAGless 的做法是"文档超过模型真实分词器的阈值才分块"。小析没有模型分词器,也不装
第三方库,所以改成**本地保守估算 + 同样的"先量再切"纪律**:

```bash
python experts/pack.py import notes.md --name my_notes --title 我的笔记
python experts/pack.py import notes.md --dry-run      # 只看会切成几块
```

| 规则 | 值 |
|---|---|
| 整篇不超过多少估算令牌就不切正文 | 1200 |
| 超过后单块上限 | 900 |
| 相邻块重叠 | 120 |
| 令牌估算口径 | CJK 1 字≈1 token;ASCII 4 字符≈1 token;标点/空白不计 |
| 支持的输入 | `.md` / `.markdown` / `.txt`,单文件 ≤ 2MB(不装 PDF 库) |

- **短文档不切**:正文整段保留;有多个 Markdown 标题时按标题分条(标题是天然边界,
  每条正文仍是完整的一段,不是把句子切碎);
- **长文档才切**:按 标题 → 段落 → 句子 的顺序切,块与块留重叠,避免一句话被拦腰截断;
- 每条的 `answer` 就是**原文**,另带 `source_file` / `source_heading` / `source_quote`
  / `source_chunk`,回答时能倒查回哪份文件的哪一段;
- 条的 id 由 `sha1(文件名 + 标题 + 块号 + 内容前缀)` 得到:**同一份文件重复导入,
  得到同一批 id**,不会悄悄堆重复(对应 RAGless 的幂等/确定性 id);
- `question` / `aliases` / `keywords` 是从标题与正文**抽**出来的检索键,不是模型生成的;
  抽得不好直接改 `pack.json`,不用重新导入;
- 可选前置元数据(不引入 YAML 依赖):

```markdown
---
title: 我的部署笔记
keywords: 部署, 回滚, 健康检查
---
```

导入后照常体检、提问、看来源:

```bash
python experts/pack.py check my_notes
python experts/GoChat.py "部署步骤是什么?"
```

**诚实说明**:`_import.estimator` 里写的是"本地保守估算",不是模型真实分词器。
这样做的代价是可能早一点切、块数略多;换来的是零依赖、结果可复现,小设备上
不用为了分词再装一个模型。

### 13.2 Q&A CSV:每行一条问答

QnA-Chatbot 的语料就是 `pattern,response` 两列的 CSV。小析把同一格式接进导入器
(标准库 `csv`,不装 pandas):

```bash
python experts/pack.py import qa.csv --name my_faq --title 我的问答
python experts/pack.py import qa.csv --dry-run     # 只看会读进多少条
```

支持的表头(**大小写不敏感**):

| 问题列 | 答案列 | 可选列 |
|---|---|---|
| `pattern` / `question` / `q` / `ask` | `response` / `answer` / `a` / `reply` | `category`、`aliases`(用 `|` 分隔) |

- 没有表头时按"前两列 = 问题、答案"读;
- **每行一条,不分块**:CSV 本身就是一条条 Q&A,再切会把一问一答拆散;
- 每条 `answer` 就是 `response` 原文;`question` 是 `pattern` 原文;
- 关键词**只从问题里抽**,不从答案里抽。答案常含 `work / help / time` 这类词,
  拿它当检索键会把 "how does a chatbot work" 拉到一条讲 work 的答案上(实测踩到);
- id 由 `sha1(文件名 + 行号 + 问题/答案)` 得到:同一份 CSV 重复导入得到同一批 id;
- 上限:单文件 2MB / 最多 5000 行;超了先拆文件。

导完照常 `python experts/pack.py check my_faq` 与试问。

### 13.3 TF-IDF 余弦:规则没命中时的兜底检索（旧路径；默认已升级为 BM25 混合）

QnA-Chatbot 的核心是"TF-IDF 向量 + 余弦相似度 + 阈值"。小析用**纯标准库**复刻了
这个最小实现([tfidf.py](framework/tfidf.py)),但只把它当**兜底**,不替代规则。
现在默认兜底进一步走 [retrieval.py](framework/retrieval.py) 的 BM25 倒排 + 混合融合
+ 查询扩展 + 分层加成;`retrieval.hybrid=false` 时才退回下面这条纯 TF-IDF 旧路径。

```text
规则检索（领域词 / 别名 / 关键词打分）先跑
  ├─ 有领域包命中 -> 直接用规则结果（精确、可解释）
  └─ 一个领域包都没命中 -> TF-IDF 余弦补一次
        ├─ 最高分 ≥ 0.35 -> 追加候选包，用条目原题去问核心
        └─ 低于 0.35     -> 如实说没找到，不硬答
```

- 文档 = 每条知识的 `question` / `aliases` / `keywords`（Q-Q 匹配，不是拿答案正文比）；
- 中文用**两字组合**当词项（不装分词器），ASCII 按词边界；IDF 自动压低高频词；
- 阈值 0.35（QnA-Chatbot 用 0.3；小析的文档更长，抬高一点宁可漏、不硬答）；
- 只追加候选包，答案仍然是核心引用的条目原句；不生成、不改写；
- 索引在 `Router` 装配时建在内存里，随数据包增删重建；不落盘、不联网；
- 规模就是几十到几百条问法，纯 Python 稀疏点积，小设备无压力。

实测：`向量场是什么?` 0.93、`什么叫向量场` 0.93、`HTML是什么?` 0.96 都能识别；
`怎么给猫剪指甲`、`番茄炒蛋要放多少盐` 低于阈值，仍然如实说没有。

### 13.4 Q-Q 聚合:一条知识的多条问法,分数按条目累加

RAGless 用 `answer_id` 把同一答案的多条问法的相似度**加总**,避免"单条问法没完全命中
就错过"。小析没有向量余弦,但有一模一样的结构:一条知识自带 `question` / `aliases`
/ `keywords`,查找层本来就会把**同一条目**的多次命中累加(`Router._score_targets`
的 `entry_scores`)。

现在把最后一步也接上了:一个数据包候选里,按条目分数取前 3 条,拿它们的**标准问法**
依次重试(`GoChat._ask_target`):

```text
用户原话 → 危机指定问法 → 前 3 条条目的原题 → 兜底问法
```

前一步答上就停,所以正常提问不会变慢;只有"原话没命中、但条目累计分够高"时才会多试
一两次。它不生成新知识,只是让已经写好的那条知识多一次被命中的机会。

### 13.5 漏问记录:把"没接住"变成下一步教什么

RAGless 把低于阈值的查询记到 `missed_queries.log` 调阈值;小析把它改成**个人学习队列**:

```text
你 > 我们公司的灰度发布规则是什么?
这个我暂时答不上来 —— 没有哪个数据包里有能对上的条目,我也不想拿常识糊过去。
你能用一句话告诉我「灰度发布规则」是什么,或者它和什么有关吗?

你 > :missed
[漏问] 最近 1 条我没接住的（只存在本地）
  · 2026-10-18T09:31:22+08:00  我们公司的灰度发布规则是什么?

你 > # 灰度发布规则:先放 5% 流量,观察 30 分钟,再逐步扩大。
[记忆] 已写入 数据包 memory · 条目 user-0001
```

- 文件:`experts/data/missed.jsonl`(JSONL,一行一条;可用 `SMALLAI_MISSED_PATH` 换位置);
- 只记**那一句没接住的话 + 当时的关键词**,不记整段聊天、不联网;
- 最近 20 条里重复的同一句不重复写;文件约 256KB / 最多 500 条,超过就裁掉最旧的;
- `:missed 50` 看更多,`:missed clear` 清空(会先问一次);
- 目的不是监控,而是**减少你下次该教它什么的思考成本**。

### 13.6 没借的东西(以及为什么)

| 项目 | 做法 | 小析不借的原因 |
|---|---|---|
| RAGless | Qdrant 本地向量库 | 要装第三方包、要维护索引文件;小析是字符串检索 + 数据包,删掉一个目录就少一块知识 |
| RAGless | Gemini 嵌入 / `RETRIEVAL_*` 任务类型 | 需要联网与 API key,违反"离线、无依赖、小设备" |
| RAGless | 运行时 LLM 抽取 Q&A / `--judge` | 运行时零模型是小析的硬底线;替换成 `pack.py check` + 本地结构校验 |
| RAGless | 用 `missed` 调阈值 | 小析的阈值固定(≥3 分),漏问用于**教学内容**,不用于自动调参 |
| QnA-Chatbot | sklearn / nltk / pandas / Streamlit | 都是重依赖或要起网页服务;小析用标准库 `csv`、纯 Python TF-IDF、终端交互 |
| QnA-Chatbot | 只靠 TF-IDF 单路检索 | 字符串规则更精确、可解释;TF-IDF/BM25 只做规则弱或没命中时的兜底,且必须过校准 |

一句话:**借"先量再切、每行一条问答、TF-IDF 兜底、按答案聚合、把漏问变教材"这些结构,
不借它们的模型、向量库与重依赖。**


---

## 14. 结构化知识与有界推理

### 14.1 数据包里可以写什么

`pack.json` 除了问答条目，还可以带三块**可选**结构化知识；没有它们时行为和以前完全一样：

```json
{
  "facts": [
    {"predicate": "是", "args": ["测试果", "水果"]},
    {"predicate": "是", "args": ["水果", "食物"]},
    {"predicate": "能", "args": ["食物", "吃"]},
    {"predicate": "储存", "args": ["测试果", "常温"]}
  ],
  "rules": [
    {
      "name": "冷藏规则",
      "if": {"fact": ["是", "?x", "热带水果"]},
      "then": ["不宜", "?x", "冷藏"],
      "note": "热带水果不适合低温"
    }
  ],
  "relations": [
    {"type": "属于", "from": "测试果", "to": "水果"}
  ]
}
```

- `facts` / `rules` 的格式与核心 `kb.json` **完全一致**（`?x` 是变量，`if` 支持 `fact/all/any/not`）；
- `relations` 是 `facts` 的糖：`type + from + to` 会写成一条同谓词事实，供关系问题使用；
- 事实、规则、关系都**属于数据包**：删掉包，它们一起消失；不会留下全局隐藏逻辑；
- 核心已有继承规则（类属传递、能力上推），所以 `测试果是水果 + 水果是食物 + 食物能吃` 可以推出“测试果可以吃”；
- 规则名重复时后写的覆盖先写的；坏条目跳过，`--health` / `pack.py check` 会如实报结构问题；
- 只有事实/规则、没有问答条目也可以（`entries` 可以为空），适合纯知识图谱式数据包。

核心的 `LogicEngine` 在装配时会把这些结构化字段并进事实库（`reasoner.FactStore.extend`），
有界规划器再用它推理。

### 14.2 有界规划器能做什么、不能做什么

[`planner.py`](framework/planner.py) 只在三种问题形状上出手：

| 问题 | 做法 | 例子 |
|---|---|---|
| 归属 / 能力 | 交给事实库 + 前向推理，带回推导链 | 测试果可以吃吗 / 测试果是水果吗 / 香蕉需要冷藏吗 |
| 关系 | 查包里的 `relations` / `facts`，只报显式关系与共同上位类 | 测试果和水果是什么关系 |
| 属性 | 查 `谓词(主体, 值)`，支持“储存”匹配“储存方式” | 测试果的储存是什么 |

三条纪律：

1. **最多 4 步**：推导链超过 4 步就截断并注明；不做递归规划、不做开放域推理；
2. **只回答“真”**：核心返回“成立”才答；“事实库里没有”不会被当成“假”硬答，落回普通检索或具体提问；
3. **来源必须真实**：主体必须出现在这个包自己的结构化知识里，避免借用别的包/内置事实却把来源写成这个包。

### 14.2.1 Datalog 子集：递归与传递闭包

`facts` / `rules` 适合“一条事实 + 一条链”；遇到“祖先 / 可达 / 传递闭包”这种
递归，用可选的 `datalog` 段（同一份 `pack.json`，不引第三方库）：

```json
{
  "datalog": {
    "facts": [
      "parent(alice, bob)",
      "parent(bob, carol)"
    ],
    "rules": [
      "ancestor(?x, ?z) :- parent(?x, ?z).",
      "ancestor(?x, ?z) :- parent(?x, ?y), ancestor(?y, ?z)."
    ]
  }
}
```

- 变量以 `?` 开头；`not parent(?x, ?y)` 做否定，但只在已经算出的正事实集合上判断，
  **不做负递归**；遇到不安全规则（头变量没在正体出现）会记错误并跳过；
- 朴素不动点迭代，最多 32 轮 / 20000 条事实；到上限会如实记一条错误；
  Datalog 证明树最多展示 12 步（关系/属性列表仍受 `MAX_STEPS=4` 的展示上限约束）；
- 有界规划器在关系/属性/归属问题上会先试 Datalog，再落回原来的 facts/rules；
  每条结论带证明树（`事实:...` / `由「...」按规则「...」推出「...」`）；
- 没有函数符号、没有 cut、没有算术求值；这是“够用的小子集”，不是完整 Prolog。

关系问题会变成：

```text
你 > alice和carol是什么关系
从现有结构化知识看:
  · alice 和 carol 满足「ancestor」

—— 来自数据包 kin 的事实/关系;离线快照,未联网核对最新版本
```

### 14.3 一次有界推理长什么样

```text
你 > 测试果可以吃吗
结论:测试果可以吃。
依据:
  · 由「测试果是食物、食物可以吃」按规则「继承·ability上推」推出「测试果可以吃」
  · 由「测试果是水果、水果是食物」按规则「继承·类属传递」推出「测试果是食物」

—— 来自数据包 demo 的结构化知识;离线快照,未联网核对最新版本
```

关系/属性问题只列包里的显式事实：

```text
你 > 测试果和水果是什么关系
从现有结构化知识看:
  · 测试果属于水果
  · 测试果是水果

—— 来自数据包 demo 的事实/关系;离线快照,未联网核对最新版本
```

### 14.4 不会时，问一个具体问题

没有结构化依据、普通检索也没命中时，不再说“能不能教教我”，而是按问法形状问：

| 用户问法 | 它问什么 |
|---|---|
| `X 是什么` | 你能用一句话告诉我「X」是什么吗？ |
| `怎么/如何 X` | 「X」具体分哪几步？最关键的一步是什么？ |
| `A 和 B 的区别/关系` | 「A」和「B」最关键的区别/关系是什么？ |
| `为什么…` | 你看到的那个现象具体是什么样？ |
| 其它 | 用拆出来的关键词问它是什么/和什么有关 |

现在推荐直接教一对 Q&A：

```text
# 量子退相干是什么~量子系统的相干性因与环境纠缠而衰减的过程
```

它会把**题面**当检索键、把**答案**当回答；同一个题再教一次是更新，
`#!问题~新答案` 是显式修正（CBR revise，旧答案留在 `revisions` 里）。
旧写法 `# 一句话知识` 仍然有效（问题=答案）。写入前仍会问“是否写入记忆”，
确认后才落进记忆包。

### 14.5 体检

```bash
python experts/core/run.py --pack experts/packs/<名字> --health
python experts/pack.py check
```

`--health` 会多报一行“结构化知识：事实 X / 规则 Y / 关系 Z / Datalog N”；
结构写坏（缺 predicate、规则的 `then` 为空、关系缺 from/to、`datalog.facts/rules`
不是数组）会在数据包自检里列出来。Datalog 的语法错误由有界规划器在装配时记进
`DatalogEngine.errors`，不会拖垮整包。
