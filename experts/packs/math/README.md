# 数学(math)

这是小析的一个**数据包**:五层流水线(规则路由 → 理解 → 逻辑/语义 → 校验 → 生成)
在 `experts/core/smallai/`,本目录里只有数据 —— `pack.json` 的 25 条本地知识条目与 `safety` 段。
**开放问题不交给任何模型**:

- 条目命中 → 给答案,并交代**条目 id**(例如 `vector-field`),可以倒查回 JSON;
- 领域内但没条目 → 如实说"没有能对上的一条",给相邻条目,不拿常识补;
- 领域外 → 如实说不是我的领域,并列出别的数据包能问什么。

所以小析**不做实事**:不做题、不写证明、不跑计算、不联网;
但数学方向的基础概念问题答得出,而且每句都能核对来源条目。

## 能答什么(25 条)

| 主题 | 条目 id | 例子 |
|---|---|---|
| 学科与数系 | `math-what` `number-sets` `function` `set-theory` | 数学是什么? / 实数和复数有什么区别? / 集合论是什么? |
| 微积分 | `limit` `derivative` `integral` `ftc` `series` `differential-equation` | 极限是什么? / 导数是什么? / 微积分基本定理是什么? |
| 线性代数 | `vector` `vector-field` `matrix` `linear-transformation` `eigenvalue` `determinant` | 向量场是什么? / 矩阵是什么? / 特征值和特征向量是什么? |
| 概率统计 | `probability` `conditional-probability` `random-variable` `distribution` `statistical-inference` | 概率是什么? / 条件概率和贝叶斯公式是什么? / p 值是什么? |
| 离散与结构 | `group` `graph-theory` `proof` `topology` | 群是什么? / 图论是什么? / 数学证明是什么? |

## 明确不做

- 不做具体计算、不代写作业/证明、不提供考试答案;
- 不联网:不查最新文献与未收录的分支;
- 只答基础概念:具体定理的条件与证明细节请查教材;
- 不做"这道题选哪个选项"这类应试判断。

## 怎么跑

```bash
# 在仓库根目录:指定数据包
python experts/core/run.py --pack experts/packs/math "向量场是什么?"
python experts/core/run.py --pack experts/packs/math "导数和积分的关系是什么?" --trace
python experts/core/run.py --pack experts/packs/math --domain          # 名片
python experts/core/run.py --pack experts/packs/math --demo            # 25 条条目 + 通用能力 + 越界 + 安全
python experts/core/run.py --pack experts/packs/math --health          # 体检
python experts/core/run.py --pack experts/packs/math --json "矩阵是什么?"

# 或者进本目录:当前目录里有 pack.json,核心会自动认这一个包
cd experts/packs/math
python ../../core/run.py "向量场是什么?"

# 走完整框架(分解 → 路由 → 总结)
python experts/GoChat.py "向量场是什么?"
```

一条真实输出(节选):

```
>>> 向量场是什么?

[OK ] intent=domain conf=0.85 source=template 228ms
  向量场是「给空间中每一点配一个向量」的映射:F(x,y,z)=(P,Q,R)。……
  要点:
    · 向量场不是「一个向量」,而是「每点一个向量」的对应关系。
  (来自数据包 math · 条目 vector-field;离线快照,未联网核对最新版本)
  (凭据:本地知识条目 vector-field)
```

## 本目录里有什么

```
math/
├── README.md      ← 这份说明
└── pack.json      ← ★ 全部内容:身份 / 领域词 / 25 条条目 / 安全规则
```

代码不在这里(在 `experts/core/smallai/`)。数据包还可以再放两个**可选**文件来覆盖核心默认数据:

| 文件 | 效果 |
|---|---|
| `kb.json` | 覆盖事实库(与核心默认库合并) |
| `word_vectors.json` | 覆盖词向量与关系图 |

## 删掉它会发生什么

删掉本目录 = 少一块知识,框架照常启动:数学类问题会落到兜底包(general),再不行提示 `help`;
其它数据包与核心不受影响。反过来,把这个目录拷到另一台机器的 `experts/packs/` 下也能直接用。

## 加一条知识

只改 `pack.json`:

```json
{
  "id": "linear-algebra-what",
  "question": "线性代数是什么?",
  "aliases": ["线性代数是什么", "线性代数有什么用"],
  "keywords": ["线性代数", "向量空间", "线性方程组"],
  "answer": "……",
  "points": ["……"],
  "see_also": ["vector", "matrix"]
}
```

改完跑 `python experts/core/run.py --pack experts/packs/math --health`:检查重复 id、
二义别名、过短关键词、`see_also` 指向不存在的条目,以及"每条原题能不能检回自己"。

## 测试

```bash
cd experts
python -m unittest discover -s tests -t .      # 参数化测试覆盖全部数据包
```

## 边界(不装作支持)

- 知识条目是**离线快照**,不随教材/标准更新;
- 检索是字符串匹配,同义改写可能检不到 —— 会如实说没找到;
- 安全判定是正则规则,不是理解;
- 不提供解题步骤与计算服务:这里只答"是什么、为什么"。
