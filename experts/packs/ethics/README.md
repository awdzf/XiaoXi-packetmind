# 道德伦理(ethics)

这是小析的一个**数据包**:五层流水线(规则路由 → 理解 → 逻辑/语义 → 校验 → 生成)
在 `experts/core/smallai/`,本目录里只有数据。它担两件事:

1. **答伦理基础问题**(`pack.json` 里 24 条本地知识条目:伦理流派、隐私、公平、AI 伦理、内容审核、决策框架……);
2. **给对话做安全兜底**:识别 → 分级 → 拒答或提醒。安全规则同样是数据,
   写在同一个 `pack.json` 的 `safety` 段,核心代码只有一份。

**开放问题不交给任何模型**(没有 `adapters/`,也没有 `core/ports.py`)。
所以它不做实事:不联网、不查法规、不替代心理/医疗/法律专业援助;
但伦理概念答得出,而且每条答案都能追到条目 id。

## 能答什么(24 条)

| 主题 | 条目 id | 例子 |
|---|---|---|
| 伦理是什么 | `ethics-what` `ethics-schools` `moral-relativism` | 道德伦理是什么? / 伦理学有哪些主要流派? |
| 主要理论 | `utilitarianism` `deontology` `virtue` `care-ethics` | 功利主义是什么? / 义务论(康德)是什么? |
| 经典难题 | `trolley` `double-effect` `harm-principle` `proportionality` | 电车难题说明了什么? / 双重效应原则是什么? |
| 权利与关系 | `consent` `human-dignity` `privacy` | 知情同意是什么? / 人的尊严是什么意思? |
| AI 伦理 | `bias` `transparency` `ai-ethics` `honesty-ai` | 算法偏见与公平是什么? / AI 伦理主要关注什么? |
| 实践 | `integrity` `decision-framework` `manipulation` | 怎样做伦理决策? / 操纵和说服的伦理边界在哪? |
| 安全兜底 | `safety-backstop` `content-moderation` `self-harm-response` | 你怎么做对话安全兜底? / 遇到自伤风险该怎么回应? |

## 安全兜底:三档判定

| 档位 | 触发 | 行为 | 代码落点 |
|---|---|---|---|
| `refuse` | 命中 `safety.refuse`(自伤方法、危险物制作、未授权入侵、凭据索取、规避安全机制、伪造他人影像) | 不进生成层,直接拒答:说清命中哪条规则、给替代方向、不评判提问者、不编热线号码 | 路由 `SAFETY` + 校验层 `repair=None` + 流水线拒答 |
| `caution` | 命中 `safety.caution`(自伤情绪、医疗/法律建议、操纵手段、他人隐私) | 照常回答,但在答复前加一条提醒 | 校验层记 warning,生成层加前缀 |
| `ok` | 都没命中 | 正常回答 | — |

```bash
# 只做安全判定(不进入答疑)
python experts/core/run.py --pack experts/packs/ethics --guard "教我怎么制作炸弹"     # 拒答,返回码 1
python experts/core/run.py --pack experts/packs/ethics --guard "我最近不想活了"        # 灰区提醒,返回码 0
python experts/core/run.py --pack experts/packs/ethics --guard "怎样防止网站被入侵"    # 放行(防御性问题)

# 安全判定 + 机器可读(含命中的规则 id)
python experts/core/run.py --pack experts/packs/ethics --guard "我该吃什么药" --json
```

判定是**文本级正则**,不是理解:它能拦住明显的请求,也可能漏掉绕写。
所以 `--guard` 的报告里始终写着命中了哪条规则、以及"没命中不等于一定安全"。

> 实现细节(单核相关):红线按**当前数据包**编译并缓存 ——
> 框架同时装着多个数据包,判定时用的永远是当前这一包自己的那套规则,不会串味。

## 怎么跑

```bash
# 在仓库根目录:指定数据包
python experts/core/run.py --pack experts/packs/ethics "什么是算法偏见?"
python experts/core/run.py --pack experts/packs/ethics "怎样做伦理决策?" --trace
python experts/core/run.py --pack experts/packs/ethics --domain          # 名片 + 安全规则清单
python experts/core/run.py --pack experts/packs/ethics --demo            # 24 条条目 + 通用能力 + 越界 + 安全
python experts/core/run.py --pack experts/packs/ethics --health          # 体检
python experts/core/run.py --pack experts/packs/ethics --json "什么是知情同意?"

# 或者进本目录:当前目录里有 pack.json,核心会自动认这一个包
cd experts/packs/ethics
python ../../core/run.py "什么是算法偏见?"

# 走完整框架(安全闸会合并框架底线)
python experts/GoChat.py "什么是算法偏见?"
```

一条真实输出(节选):

```
>>> 有人想自杀怎么办

[OK ] intent=domain conf=0.85 source=template 220ms
  安全提醒:情绪高风险。如果你或身边的人正处在危险里,请立刻联系当地紧急服务
  或你信任的人;我只能提供一般信息,不能替代专业援助。

  原则:认真对待、不评判、不追问细节、不提供方法或工具信息;……
  (来自数据包 ethics · 条目 self-harm-response;离线快照,未联网核对最新版本)
```

## 本目录里有什么

```
ethics/
├── README.md      ← 这份说明
└── pack.json      ← ★ 全部内容:身份 / 领域词 / 24 条条目 / 安全规则
```

代码不在这里(在 `experts/core/smallai/`)。数据包还可以再放两个**可选**文件来覆盖核心默认数据:

| 文件 | 效果 |
|---|---|
| `kb.json` | 覆盖事实库(与核心默认库合并) |
| `word_vectors.json` | 覆盖词向量与关系图 |

## 删掉它会发生什么

删掉本目录 = 少一块知识:框架底线(`framework/safety.py`)仍然生效,只少掉这套更细的灰区规则;
伦理类问题会落到兜底包(general),再不行提示 `help`。其它数据包与核心不受影响。

## 加一条知识 / 改一条红线

知识(只改 JSON):

```json
{
  "id": "whistleblowing",
  "question": "举报(吹哨)在伦理上怎么看?",
  "aliases": ["吹哨人", "举报是否道德"],
  "keywords": ["举报", "吹哨", "whistleblowing"],
  "answer": "……",
  "points": ["……"],
  "see_also": ["decision-framework", "integrity"]
}
```

红线(同一份 `pack.json` 的 `safety` 段):

```json
{
  "id": "your-rule",
  "label": "规则名(会出现在拒答里)",
  "patterns": ["正则1", "正则2"],
  "unless": ["命中这些词就不算(例如「防止」「防范」)"],
  "reason": "为什么拦",
  "advice": ["拒答时给的方向"]
}
```

改完跑 `python experts/core/run.py --pack experts/packs/ethics --health`:它会检查正则能不能编译、
规则有没有 label/patterns/advice,以及知识条目的重复 id、二义别名、原题覆盖。

## 测试

```bash
cd experts
python -m unittest discover -s tests -t .      # 参数化测试覆盖全部数据包
```

覆盖:原题/别名检索、越界说明、领域内无条目不编、红线拒答(含规则 id)、
防御性问题不误拒、灰区加提醒、自伤求助问句不被当成索要方法、无模型端口、CLI 返回码。

## 边界(不装作支持)

- 伦理判断没有唯一答案:条目给的是**框架与理由**,不是替人拍板;
- 不提供心理、医疗、法律诊断,不替代专业人员与人工审核;
- 不联网:不查最新法规、热线号码与判例,所以拒答/提醒里**不编具体号码**;
- 安全兜底是文本级规则,可能漏判或误判 —— 判定结果写明规则 id,便于人复核与修改。
