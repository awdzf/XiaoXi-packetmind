必看声明：这是一个完全由AI完成的项目（除了这个必看声明）。由于我不是开发者，甚至完全不懂代码，所以里面的代码内容完全没有经过人工审核。后面该仓库大概率是不会有维护和更新
# 小析:一个不靠大模型的离线的本地知识问答系统

> 版本 **1.0.0** · MIT · 发布说明见 [RELEASE.md](RELEASE.md) / [CHANGELOG.md](CHANGELOG.md)。

小析是**一个人格**,知识是若干个**数据包**。

```
用户提问 → 分解(切词 + 错字候选) → 查找数据包(可多个) → 引用条目原句作答
```

- 不调用任何模型、不联网、不做实事;
- 每条答案都能追到「**哪个数据包、哪一条**」,例如 `数据包 math · 条目 vector-field`;
- 没有条目就直说没有,不拿常识补;
- **记得上一句**:拆句时会把"非常用词"记成关键词(「引力波是什么?」→ `引力波`),
  接着问「还有吗」时用它再查一次;有直接命中就不动用记忆,所以新话题不会被劫持;
- **会学新知识**:接不住时它会问一个具体问题(如“「X」是什么?”),推荐用
  `#问题~答案` 教一道题(旧写法 `#一句话` 仍兼容),确认后写进**记忆数据包**
  (`packs/memory/`);写入时从题面记主关键词和低权重次要关键词,
  同主题多条记忆会并列,追问时用次要关键词命中对应那条;
  同题再教一次自动更新，`#!问题~新答案` 显式修正（旧答案留在 `revisions` 里）；
  与别的包冲突时如实说"有歧义",并默认按 `author > memory` 排序(低优先级仍列出);
  也可用 `:trust` / `:priority` 调整；`#?` 按你当时的原话列出教过的知识,
  `#-<关键词>` 忘掉其中一条(先列出来再确认);
- **能把你的笔记装进来**:`pack.py import notes.md` 把 `.md` / `.txt` 变成数据包;
  先量令牌,**短的整段不切**,长的才按标题/段落/句子切(块间有重叠),每条都带来源;
- **没接住的问题会记下来**:`:missed` 看最近没答上的问题,挑一条用 `#` 教它,
  下次就能命中(纯本地 JSONL,不联网);
- **包之间能声明包含关系**:`"contained_by": ["fruit"]` 表示"苹果 ⊆ 水果";命中一个包后,
  它用条目关键词**反向**去查没查过的包,命中了"包含已命中包"的父包就补一句
  「A 有 C 中的性质,同时也有 D 中的性质」(再命中;轮数 `rehit_rounds` 可配,默认 1);
- **上限可调、可按次补查**:一次最多查几个包由 `experts/config.json` 的 `max_packs` 决定
  (默认 3,`--max-packs N` 可临时改);命中超过上限时会弹
  `能否再调用数据包[y/n]:` —— `y` 只补查这一次,上限配置不变;
- 代码只有一份(`experts/core/smallai/`),人格只有一处(`persona.py`),
  知识是可拆装的数据包(`experts/packs/<名字>/pack.json`)—— 加一位"懂什么"只要加一个目录。

## 目录

```
/
├── xiaoxi.py                根目录启动器(等价于 experts/GoChat.py)
├── VERSION / LICENSE / CHANGELOG.md / CITATION.cff
├── SECURITY.md / PRIVACY.md / CONTRIBUTING.md / RELEASE.md
├── tools/build_release.py   生成 dist/xiaoxi-<version>.zip（含干净副本自检）
├── experts/
│   ├── GoChat.py          ★ 终端入口(对话 / 单次提问 / 分解 / 查找 / 安全判定)
│   ├── ask.py             简单入口(一句话 → 总结)
│   ├── eval.py            检索评测入口(--eval 的独立版)
│   ├── pack.py            数据包管理:list / new / import / check
│   ├── config.json        ★ 可调开关:max_packs / rehit_rounds / context / retrieval / trust
│   ├── framework/         分解 / 查找 / 引用总结 / 上下文 / 包关系 / 安全 / 发现
│   │   ├── corebridge.py     单进程内核:按包缓存流水线,文件变了才重建
│   │   ├── planner.py        有界规划器:归属/关系/属性,最多 4 步可核对
│   │   ├── ingest.py         笔记 / Q&A CSV 导入:智能分块 / 确定性 id / 来源字段
│   │   ├── learning.py       漏问记录:本地学习队列(`:missed`)
│   │   ├── tfidf.py          轻量检索:纯 Python TF-IDF + 余弦兜底
│   │   ├── retrieval.py      BM25 倒排 + 分层 + 混合融合 + 查询扩展(增量)
│   │   ├── datalog.py        Datalog/Prolog 式子集:递归 / 证明树 / 有界否定
│   │   ├── budget.py         World Info 式预算:priority / group / 条数 / 字符
│   │   ├── evaluation.py     检索评测:hit@1 / MRR / 负例弃权
│   │   ├── calibration.py    校准弃权:z / margin / coverage / trust -> 置信度
│   │   ├── trust.py          高置信条目 + 冲突优先级(默认作者包 > 记忆)
│   │   ├── conflicts.py      同一问题多包作答:排序 / 标注 / strict 取舍
│   │   ├── memory.py         对话记忆:记住拆出来的关键词(有界)
│   │   ├── lessons.py        记忆数据包:把用户教的知识写进去 / 列出来 / 忘掉
│   │   ├── relations.py      包关系:包含 / 被包含(子集图)
│   │   └── settings.py       读 config.json(坏值退回默认并说明)
│   ├── core/              ★ 小 AI 的唯一一份代码
│   │   ├── run.py            启动器(python experts/core/run.py --pack <数据包> ...)
│   │   ├── smallai/
│   │   │   ├── persona.py      ★ 人格:名字 / 口吻 / 自述 / 出处措辞
│   │   │   ├── packs.py        数据包解析(这一轮用哪个包)
│   │   │   └── …               五层流水线(路由/理解/逻辑/校验/生成)
│   │   └── README.md
│   ├── packs/             ★ 知识 = 数据包(每个目录一块)
│   │   ├── web_dev/ java/ math/ physics/ emotion/ ethics/ general/
│   │   │   └── pack.json (+ 可选 kb.json / word_vectors.json / README.md)
│   │   └── memory/        ★ 记忆数据包:你在对话里教的知识(空着是正常的)
│   ├── data/              漏问记录(missed.jsonl;`:missed` 看/清)
│   ├── evaluation/        检索评测集(cases.jsonl;`--eval` 跑)
│   ├── eval.py            评测入口(也可用 GoChat.py --eval)
│   ├── tests/             框架 + 人格 + 逐个数据包的参数化测试
│   └── README.md          ★ 模型详解 + 制作教程
└── expert-template/       数据包模板(复制或新建一块知识时用,示例领域是咖啡)
    ├── pack.json
    ├── README.md
    └── tests/             模板自检(换领域后不用改)
```

## 快速开始

```bash
python experts/GoChat.py "你是谁"               # 小析的自我介绍(不查数据包)
python experts/GoChat.py "你好"                 # 闲聊(数据包 general)
python experts/GoChat.py "向量场是什么?"          # 查 数据包 math
python experts/GoChat.py "引力波是什么?"          # 查 数据包 physics
python experts/GoChat.py "向量场和电场的关系?"     # 查两个包并并列原句
python experts/GoChat.py --trace "HMTL是什么?"   # 看候选与查找明细(错字纠正)
python experts/GoChat.py --packs                 # 小析现在装着哪些数据包
python experts/GoChat.py --missed                # 最近没接住的问题(本地学习队列)
python experts/GoChat.py --eval                  # 跑检索评测集(hit@1 / MRR / 弃权)
python experts/GoChat.py --max-packs 1 "…"       # 这一次最多查 1 个包(更快、更聚焦)
python experts/GoChat.py --rehit 0 "…"           # 这一次不做"再命中"(不做反向扩展)
python experts/GoChat.py --no-context "…"        # 这一次不用上下文记忆
python experts/GoChat.py "# 量子退相干是什么~量子系统的相干性因与环境纠缠而衰减"  # 教一道题
python experts/GoChat.py "#!量子退相干是什么~新答案"  # 修正同一道题(CBR)
python experts/GoChat.py "#?"                    # 看教过的知识(原话回放)
python experts/GoChat.py "#- 量子退相干"         # 忘掉一条(先列出来,确认后才删)
python experts/GoChat.py --subprocess "…"        # 这一次改回子进程(隔离优先;默认单进程)
python experts/pack.py import notes.md --name my_notes --title 我的笔记  # 把 .md/.txt 变数据包
python experts/pack.py import qa.csv --name my_faq --title 我的问答      # 每行一条 Q&A 的 CSV
python experts/GoChat.py help                    # 完整命令
```

对话里还能 `:ctx`(看记得什么)、`:forget`(忘掉上下文)、`:max 2`(改上限)、
`:rehit 0`(关再命中)、`:trust`(看/改高置信条目)、`:priority`(看/改冲突优先级)、
`:stat`(看进程、缓存与检索)、`:eval`(跑评测集)、`:missed`(看漏问),以及拿 `#`
开头教它一道题（`#问题~答案`；`#!问题~新答案` 修正；写入前会弹 `y/n`）、
`#?` 看教过的、`#-` 忘掉一条。

默认输出是「人话版」:先答案,末尾一行出处;内部账不打扰你。

```text
>>> 向量场是什么?

向量场是「给空间中每一点配一个向量」的映射:……

要点:
  · 向量场不是「一个向量」,而是「每点一个向量」的对应关系。

—— 来自数据包 math · 条目 vector-field;离线快照,未联网核对最新版本
```

接着问(同一个终端进程里,上下文生效时):

```text
你 > 引力波是什么?
引力波是时空曲率的涟漪:……

—— 来自数据包 physics · 条目 gravitational-wave;……

你 > 还有吗
(直接接住"引力波"这个上下文,不会重新猜)
```

想看 `[分解] / [查找] / [回答] / [凭据]` 这些内部账,加 `--trace`:

```bash
python experts/GoChat.py --trace "向量场是什么?"
```

单独跑一块知识(不经过框架):

```bash
python experts/core/run.py --pack experts/packs/math "向量场是什么?"
cd experts/packs/math && python ../../core/run.py --health    # 在包目录里可省略 --pack
```

给小析加一块知识:

```bash
python experts/pack.py new my_pack --title 我的领域     # 从模板建一个数据包并体检
# 改 experts/packs/my_pack/pack.json 的 expert.scope 与 entries
python experts/pack.py check my_pack
python experts/GoChat.py "你的标准问题"
```

在对话里现教它一条(写进记忆数据包,不碰别的包):

```text
你 > 量子退相干是什么?
这个我暂时答不上来 —— 没有哪个数据包里有能对上的条目,我也不想拿常识糊过去。
你能用一句话告诉我「量子退相干」是什么吗?
如果你愿意教我,直接回一行:
  # <一句话答案>

你 > # 量子退相干是量子系统的相干性因与环境纠缠而衰减的过程。
[记忆] 拆出的关键词:量子退相干、量子系统、相干性、环境、纠缠
是否写入记忆[y/n]: y
[记忆] 已写入 数据包 memory · 条目 user-0001

你 > 量子退相干是什么?
量子退相干是……
(这条来自你教我的记忆,不是我原来的知识。)

—— 来自数据包 memory · 条目 user-0001;离线快照,未联网核对最新版本

你 > #?
我现在记着 1 条你教的知识(下面是你当时的原话):

# 量子退相干是量子系统的相干性因与环境纠缠而衰减的过程。

要忘掉哪条:用「#-<那句话里的一小段>」;只看一部分:用「#?<关键词>」。

你 > #- 量子退相干
[记忆] 找到 1 条对得上的:
  # 量子退相干是量子系统的相干性因与环境纠缠而衰减的过程。
确认忘掉这 1 条?[y/n]: y
[记忆] 已忘掉 1 条。
[记忆] 现在还剩 0 条你教的知识。
```

删掉一块知识:直接删 `experts/packs/<名字>/` 那个目录(框架下次启动就少一块,
人格与其它数据包不受影响)。完整教程见 [`experts/README.md`](experts/README.md)。

把**自己的笔记**装进来(零依赖,不需要模型):

```bash
python experts/pack.py import notes.md --name my_notes --title 我的笔记
python experts/pack.py import notes.md --dry-run     # 先看会切成几块
python experts/pack.py import qa.csv --name my_faq --title 我的问答   # 每行一条 Q&A
```

Q&A CSV 支持 `pattern,response` 或 `question,answer` 表头（大小写不敏感），
也可以没有表头按前两列读；每行一条、不切段，答案原文进数据包。
关键词只从问题里抽（不拿答案里的 `work/help/time` 当检索键）。

- 先量估算令牌:整篇 ≤ 1200 就**不切正文**;有多个 Markdown 标题时按标题分条
  (标题是天然边界,不是把句子切碎);
- 超过阈值才按 标题 → 段落 → 句子 切,单块 ≤ 900,重叠 120;
- 每条 `answer` 都是原文,带 `source_file` / `source_heading` / `source_quote`;
- 令牌数是**本地保守估算**(CJK 1 字≈1 token,ASCII 4 字符≈1 token),不是模型分词器;
  这样做是为了零依赖、可复现、小设备不装模型，`_import.estimator` 会如实写明；
- id 由内容哈希得到:同一份文件重复导入得到同一批 id,不会堆重复;
- 只支持 `.md` / `.txt`(≤2MB);PDF 需要第三方库,明确不做。

规则命中优先;规则弱/没命中时,用纯 Python **BM25 倒排 + TF-IDF + 笔记标题分层**
做混合兜底,首轮证据不足时还会做一次查询扩展。所有候选都要过**校准弃权**
(z 分数 / 第一名差距 / 查询覆盖 / 信任级别),证据太弱就如实说没找到;
过了也只是多一个候选包,答案仍是条目原句。BM25 倒排按数据包**增量更新**,
教完一条记忆不会把所有包推倒重来。`experts/config.json` 的 `retrieval` 段可调。

数据包还可以把知识写成结构化的 `facts` / `rules` / `relations`(格式同 `kb.json`)：

```json
"facts": [{"predicate": "是", "args": ["测试果", "水果"]}],
"relations": [{"type": "属于", "from": "测试果", "to": "水果"}]
```

有界规划器会在“归属 / 关系 / 属性”问题上用它做最多 4 步推理，结论带事实/规则依据；
没有结构化知识时完全照旧。细节见 [`experts/README.md`](experts/README.md) 第 14 节。

没答上的问题会进本地学习队列:`:missed` 看列表,挑一条用 `#` 教它;`:missed clear` 清空。
文件在 `experts/data/missed.jsonl`,只存那一句话与关键词,不联网、不记上下文。

## 特点

- **一个人格**:名字、口吻、自述、拒绝方式都在 `core/smallai/persona.py`,只有一处;
- **单核**:核心代码一份,修一处所有知识同时受益(以前是八份副本);
- **知识可拆装**:自动发现 `experts/packs/*/pack.json`;删掉任何包只少一块知识;
- **数据包可覆盖**:一个包放 `kb.json` / `word_vectors.json` 就能单独换事实库与词表;
- **记得上一句**:关键词来自拆句结果,有界记忆(默认 8 轮 / 12 词),`:forget` 可清空;
- **会学新知识**:`#问题~答案` 教一道题（旧写法 `#一句话` 仍兼容），写入前确认；
  同题再教自动更新，`#!问题~新答案` 显式修正（CBR revise，旧答案留在 `revisions`）；
  `#?` 原话回放，`#-` 忘掉一条；与别的包冲突时如实说"有歧义"；
- **能把笔记装进来**:`pack.py import` 把 `.md` / `.txt` 变成数据包;先量令牌,
  短的整段不切,长的才按标题/段落/句子切并留重叠;每条都带来源,id 可重复导入而不重复;
  也支持 QnA-Chatbot 的 `pattern,response` 风格 CSV(每行一条 Q&A,不切段);
- **规则没命中时还有混合兜底**:纯 Python BM25 倒排 + TF-IDF + 分层索引;
  规则命中后用 BM25 给同包补条目,规则没命中才加新包;查询扩展只在首轮覆盖不足时触发;
- **校准弃权**:置信度由 z 分数、第一名/第二名差距、查询覆盖与信任级别合成,
  低于 `retrieval.abstain_confidence` 就不答,不再只靠一个固定阈值;
- **高置信条目可设**:作者包条目默认高置信,用户可用 `:trust <包>/<条目>` 把任意条目
  标成高/低;冲突优先级默认 `author > memory`,可用 `:priority` 改,
  `prefer` 仍列出低优先级、`strict` 只留高的;
- **可以把知识写“结构化”**:`pack.json` 可选 `facts` / `rules` / `relations` / `datalog`,
  有界规划器做最多 4 步的归属/关系/属性推理；Datalog 子集补上递归/传递闭包
  （祖先、可达），每条结论带证明树；没有结构化知识时照常按条目答；
- **World Info 式预算**:条目可写 `priority` / `group`（同组只取一条），
  `retrieval.max_entries` / `text_budget` 限制一次并入的条数与正文字符；
  被预算挡掉的条目会如实说明；
- **有评测集**:`experts/evaluation/cases.jsonl` + `--eval` / `:eval`，
  跑文件夹 hit@1、条目 hit@1、MRR 与负例弃权率，改检索前后可直接对比；
- **没接住时会问一个具体问题**:不是“能不能教教我”,而是按问法问“X 是什么 / 分哪几步 /
  和 Y 的区别”,回答一行就能写进记忆;
- **没接住的问题会进入学习队列**:`:missed` 看最近漏问,挑一条 `#` 教它;
  只写本地 JSONL(约 256KB 上限),不联网;
- **单进程 + 缓存**:默认核心常驻,每个数据包按需装配一条流水线并复用
  (文件变了才重建);同一包的第二问从几百毫秒降到十毫秒级;`--subprocess` 可退回隔离路径;
- **有包关系**:`contains` / `contained_by` 声明子集;再命中默认 1 轮,`--rehit 0` 可关;
- **有上限、也不失精度**:`max_packs` 控制一次查几个包,阈值仍是 ≥3 分;
  命中超过上限会问要不要补查(`y` 仅这一次,上限不变);
- **安全不依赖单点**:框架自带红线,ethics 包在时合并细则;默认只说人话,规则 id 在 `--trace` / `--guard` 里;
- **危机分对象**:「我不想活了」和「朋友不想活了」拿到的不是同一段话;
- **默认人话版**:先答案、末尾一行出处;`[分解]/[查找]/[回答]` 等内部账只在 `--trace` 里;
- **可核对**:单包给完整答案,多包给原句并列 + 数据包与条目 id + 凭据;
- **零依赖**:Python 3.10+ 标准库;无网络、无模型、无构建步骤。

## 测试

```bash
# 在 experts/ 下:框架/人格/上下文/教学/单进程/导入/结构化/新检索 + 数据包,共 222 项
python -m unittest discover -s tests -t .

# 在 expert-template/ 下:模板 6 项
python -m unittest discover -s tests -t .
```

新增数据包后,只要在 `experts/tests/test_packs.py` 的 `PROBES` 里补一行探针,
同一套规矩(原题可检索 / 别名 / 越界如实说 / 红线拒答 / 无模型 / 代码数据分离)自动覆盖它。

## 边界

知识是 `pack.json` 的离线快照,检索是字符串匹配;不查实时信息、不替代专业意见;
一次最多查 3 个数据包(`config.json` 可改,1–8;**超过上限要补查必须你同意**);
再命中(反向关键词 → 父包)默认 1 轮、每轮同样受上限约束,`--rehit 0` 可关;
上下文只活在当前进程里,关掉终端就没了;记忆数据包存的是**你教的**内容,
它不验证真假 —— 与别的包冲突时会如实说"有歧义",把判断留给你;
默认单进程:某一包在进程内失败会自动退回子进程重试并如实记一条说明
(`:stat` 可看);要全程隔离就用 `--subprocess`;
导入笔记只支持 `.md` / `.txt` / `.csv`(不装 PDF 库),令牌数是本地保守估算而不是模型真实分词器;
导进来的条目是**原文 + 抽出的检索键**,抽得不准时直接改 `pack.json`;漏问只记本地,
`experts/data/missed.jsonl` 可随时删;BM25/TF-IDF 兜底仍是词面相似,不懂同义改写,
低分会被阈值挡掉,但边界附近仍可能误命中——它只追加候选包,答案仍可倒查来源;
总结只引用原句,不生成新知识。详见
[`experts/README.md`](experts/README.md) 的「边界与不做承诺」。

## 发布与验证

```bash
# 全量测试 + 数据包体检 + 检索评测
cd experts && python -m unittest discover -s tests -t . && python pack.py check && python eval.py

# 生成可上传的源码包（会清空记忆、删日志、在干净副本里再跑一遍测试）
cd .. && python tools/build_release.py
```

产物在 `dist/xiaoxi-1.0.0/` 与 `dist/xiaoxi-1.0.0.zip`。
发布前检查清单、上传方式与版本升级步骤见 [RELEASE.md](RELEASE.md)；
隐私与安全边界见 [PRIVACY.md](PRIVACY.md) / [SECURITY.md](SECURITY.md)。
