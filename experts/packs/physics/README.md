# 物理(physics)

这是小析的一个**数据包**:五层流水线(规则路由 → 理解 → 逻辑/语义 → 校验 → 生成)
在 `experts/core/smallai/`,本目录里只有数据 —— `pack.json` 的 25 条本地知识条目与 `safety` 段。
**开放问题不交给任何模型**:

- 条目命中 → 给答案,并交代**条目 id**(例如 `gravitational-wave`),可以倒查回 JSON;
- 领域内但没条目 → 如实说"没有能对上的一条",不拿常识补;
- 领域外 → 如实说不是我的领域,并列出别的数据包能问什么。

所以小析**不做实事**:不做题、不跑实验、不联网;
但物理方向的基础概念问题答得出,而且每句都能核对来源条目。

## 能答什么(25 条)

| 主题 | 条目 id | 例子 |
|---|---|---|
| 学科与力学 | `physics-what` `newton-laws` `gravity` `momentum` `energy` | 物理学是什么? / 牛顿三大定律是什么? / 万有引力是什么? |
| 相对论与引力 | `relativity` `gravitational-wave` `light-speed` `black-hole` | 引力波是什么? / 相对论是什么? / 黑洞是什么? |
| 电磁 | `electric-field` `magnetic-field` `maxwell` `electromagnetic-wave` | 电场是什么? / 麦克斯韦方程组说了什么? / 电磁波是什么? |
| 热与统计 | `thermodynamics` `entropy` | 热力学定律是什么? / 熵是什么? |
| 量子与粒子 | `quantum` `wave-particle` `heisenberg` `quantum-entanglement` `particle` `higgs` | 量子力学是什么? / 波粒二象性是什么? / 量子纠缠是什么? |
| 宇宙 | `big-bang` `dark-matter` `doppler` | 大爆炸理论是什么? / 暗物质和暗能量是什么? / 多普勒效应是什么? |
| 凝聚态 | `superconductivity` | 超导是什么? |

## 明确不做

- 不做题、不代写作业、不提供考试答案;
- 不跑实验、不提供可能伤人的自制装置方案;
- 不联网:不查最新观测与预印本,条目是离线快照;
- 只答基础概念:具体计算与工程参数请查教材或规范。

## 怎么跑

```bash
# 在仓库根目录:指定数据包
python experts/core/run.py --pack experts/packs/physics "引力波是什么?"
python experts/core/run.py --pack experts/packs/physics "量子纠缠能超光速传信息吗?" --trace
python experts/core/run.py --pack experts/packs/physics --domain            # 名片
python experts/core/run.py --pack experts/packs/physics --demo              # 25 条条目 + 通用能力 + 越界 + 安全
python experts/core/run.py --pack experts/packs/physics --health            # 体检
python experts/core/run.py --pack experts/packs/physics --json "熵是什么?"

# 或者进本目录:当前目录里有 pack.json,核心会自动认这一个包
cd experts/packs/physics
python ../../core/run.py "引力波是什么?"

# 走完整框架(分解 → 路由 → 总结)
python experts/GoChat.py "引力波是什么?"
```

一条真实输出(节选):

```
>>> 引力波是什么?

[OK ] intent=domain conf=0.85 source=template 225ms
  引力波是时空曲率的涟漪:加速运动的质量(如双黑洞并合)扰动时空,以光速向外传播,……
  要点:
    · 引力波不是「引力」,是引力的变化以波的形式传播。
  (来自数据包 physics · 条目 gravitational-wave;离线快照,未联网核对最新版本)
  (凭据:本地知识条目 gravitational-wave)
```

## 本目录里有什么

```
physics/
├── README.md      ← 这份说明
└── pack.json      ← ★ 全部内容:身份 / 领域词 / 25 条条目 / 安全规则
```

代码不在这里(在 `experts/core/smallai/`)。数据包还可以再放两个**可选**文件来覆盖核心默认数据:

| 文件 | 效果 |
|---|---|
| `kb.json` | 覆盖事实库(与核心默认库合并) |
| `word_vectors.json` | 覆盖词向量与关系图 |

## 删掉它会发生什么

删掉本目录 = 少一块知识,框架照常启动:物理类问题会落到兜底包(general),再不行提示 `help`;
其它数据包与核心不受影响。反过来,把这个目录拷到另一台机器的 `experts/packs/` 下也能直接用。

## 加一条知识

只改 `pack.json`:

```json
{
  "id": "pressure",
  "question": "压强是什么?",
  "aliases": ["压强是什么", "什么是压强"],
  "keywords": ["压强", "帕斯卡", "受力面积"],
  "answer": "……",
  "points": ["……"],
  "see_also": ["newton-laws", "thermodynamics"]
}
```

改完跑 `python experts/core/run.py --pack experts/packs/physics --health`:检查重复 id、
二义别名、过短关键词、`see_also` 指向不存在的条目,以及"每条原题能不能检回自己"。

## 测试

```bash
cd experts
python -m unittest discover -s tests -t .      # 参数化测试覆盖全部数据包
```

## 边界(不装作支持)

- 知识条目是**离线快照**,不随最新实验/观测更新;
- 检索是字符串匹配,同义改写可能检不到 —— 会如实说没找到;
- 安全判定是正则规则,不是理解;
- 不提供解题、实验方案与工程参数:这里只答"是什么、为什么"。
