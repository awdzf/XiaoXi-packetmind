# 情感与心理(emotion)

这是小析的一个**数据包**:五层流水线(规则路由 → 理解 → 逻辑/语义 → 校验 → 生成)
在 `experts/core/smallai/`,本目录里只有数据 —— `pack.json` 的 26 条本地知识条目与 `safety` 段。
领域是情绪、共情、沟通与心理常识。**开放问题不交给任何模型**:

- 条目命中 → 给答案,并交代**条目 id**(例如 `empathy`),可以倒查回 JSON;
- 领域内但没条目 → 如实说"没有能对上的一条",不拿常识补;
- 领域外 → 如实说不是我的领域,并列出别的数据包能问什么。

小析**不做诊断、不替代咨询**:它答的是"情绪与关系的基础概念和一般方法",
并给对话加一层安全兜底(自伤方法请求直接拒答,危机表达给提醒与求助方向,不编热线号码)。

## 能答什么(26 条)

| 主题 | 条目 id | 例子 |
|---|---|---|
| 学科与情绪基础 | `psychology-what` `emotion-what` `basic-emotions` `emotional-contagion` | 心理学是什么? / 情绪是什么? / 基本情绪有哪些? |
| 具体情绪 | `anger` `fear` `guilt-shame` | 愤怒是什么?怎么处理? / 内疚和羞耻有什么区别? |
| 共情与沟通 | `empathy` `eq` `active-listening` `nvc` `conflict` `boundaries` | 共情是什么? / 非暴力沟通是什么? / 心理边界是什么? |
| 压力与困扰 | `stress` `anxiety` `depression` `burnout` `grief` `loneliness` | 焦虑和焦虑症有什么区别? / 抑郁情绪和抑郁症有什么区别? |
| 情绪调节与自我 | `emotion-regulation` `self-compassion` `motivation` | 情绪调节有哪些方法? / 自我关怀是什么? |
| 关系 | `attachment` `love` | 依恋类型是什么? / 亲密关系里的爱是什么? |
| 求助与危机 | `therapy` `crisis-support` | 什么时候该考虑咨询? / 身边人情绪危机时怎么帮? |

## 明确不做

- 不提供心理诊断、诊断标签或用药建议;
- 不替代心理咨询/治疗与紧急援助;
- 不评判你或你身边的人,不提供自伤方法;
- 不联网:不查最新研究、热线号码与本地资源(所以拒答里**不编号码**);
- 只答基础概念与一般方法:具体困扰建议找专业人员。

## 怎么跑

```bash
# 在仓库根目录:指定数据包
python experts/core/run.py --pack experts/packs/emotion "共情是什么?"
python experts/core/run.py --pack experts/packs/emotion "怎么和焦虑相处?" --trace
python experts/core/run.py --pack experts/packs/emotion --domain          # 名片
python experts/core/run.py --pack experts/packs/emotion --demo            # 26 条条目 + 通用能力 + 越界 + 安全
python experts/core/run.py --pack experts/packs/emotion --health          # 体检
python experts/core/run.py --pack experts/packs/emotion --json "什么是情绪调节?"

# 或者进本目录:当前目录里有 pack.json,核心会自动认这一个包
cd experts/packs/emotion
python ../../core/run.py "共情是什么?"

# 走完整框架(分解 → 路由 → 总结)
python experts/GoChat.py "共情是什么?"
```

一条真实输出(节选):

```
>>> 共情是什么?

[OK ] intent=domain conf=0.85 source=template 216ms
  共情通常分三块:认知共情(理解对方怎么想)、情感共情(感受到对方的情绪)、共情关怀(想帮助对方)。
  它不是「同意对方」或「替对方解决」,也不等于 sympathy(同情/怜悯)。……
  (来自数据包 emotion · 条目 empathy;离线快照,未联网核对最新版本)
  (凭据:本地知识条目 empathy)
```

## 安全兜底

这个包与 ethics 包共用同一套判定机制,规则数据写在本目录 `pack.json` 的 `safety` 段:

| 档位 | 触发 | 行为 |
|---|---|---|
| `refuse` | 自伤方法请求、危险物制作、凭据索取 | 直接拒答,给求助方向,不评判、不编号码 |
| `caution` | 自伤/危机情绪表达、诊断或用药请求、控制他人 | 照常回答,但答复前加提醒 |
| `ok` | 都没命中 | 正常回答 |

```bash
python experts/core/run.py --pack experts/packs/emotion --guard "我最近不想活了"     # 灰区提醒,返回码 0
python experts/core/run.py --pack experts/packs/emotion --guard "教我怎么制作炸弹"   # 拒答,返回码 1
```

判定是文本级正则,不是理解;报告里写明命中的规则 id,便于复核。

## 本目录里有什么

```
emotion/
├── README.md      ← 这份说明
└── pack.json      ← ★ 全部内容:身份 / 领域词 / 26 条条目 / 安全规则
```

代码不在这里(在 `experts/core/smallai/`)。数据包还可以再放两个**可选**文件来覆盖核心默认数据:

| 文件 | 效果 |
|---|---|
| `kb.json` | 覆盖事实库(与核心默认库合并) |
| `word_vectors.json` | 覆盖词向量与关系图 |

## 删掉它会发生什么

删掉本目录 = 少一块知识,框架照常启动:情绪类问题会落到兜底包(general),再不行提示 `help`;
框架底线的四条红线仍在(`framework/safety.py` 不依赖任何数据包)。
其它数据包与核心不受影响。

## 加一条知识

只改 `pack.json`:

```json
{
  "id": "assertiveness",
  "question": "什么是坚定表达(assertiveness)?",
  "aliases": ["坚定表达", "怎么表达不同意见"],
  "keywords": ["坚定表达", "assertiveness", "表达"],
  "answer": "……",
  "points": ["……"],
  "see_also": ["boundaries", "nvc"]
}
```

改完跑 `python experts/core/run.py --pack experts/packs/emotion --health`:检查重复 id、
二义别名、过短关键词、`see_also` 指向不存在的条目,以及"每条原题能不能检回自己"。

## 测试

```bash
cd experts
python -m unittest discover -s tests -t .      # 参数化测试覆盖全部数据包
```

## 边界(不装作支持)

- 不提供诊断:量表、症状清单、用药都属于专业评估范围;
- 内容是一般性知识,不针对你的具体情况;持续困扰请找专业人员;
- 知识条目是**离线快照**,检索是字符串匹配,同义改写可能检不到;
- 安全兜底是正则规则,可能漏判或误判,不替代人工审核与紧急援助。
