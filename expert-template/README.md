# 数据包模板(`expert-template/`)

这是一块**可以照抄的知识**:一个只有数据、没有代码的数据包。小析的代码在
[`../experts/core/`](../experts/core/),所以这里只有一份 `pack.json`(示例领域是咖啡)。
把它复制到 `experts/packs/<你的包名>/` 之后,框架就会自动发现它;
模板本身不在 `packs/` 下,不会被当成在役知识。

> 完整的制作教程(字段规则、写作要求、排错、进阶)**在
> [`../experts/README.md`](../experts/README.md) 的「给小析加一块知识」一节**。
> 这份 README 只给最短路径。

## 一分钟上手

```bash
# 1) 建一个数据包(推荐:自动复制模板 + 体检)
python experts/pack.py new my_pack --title 我的领域

#    或者手工复制
#    cp -r expert-template experts/packs/my_pack
#    Copy-Item expert-template experts\packs\my_pack -Recurse

# 2) 改知识:编辑 experts/packs/my_pack/pack.json
#    expert.id / name / tagline / scope / entries / limits

# 3) 体检 + 试跑
python experts/pack.py check my_pack
python experts/core/run.py --pack experts/packs/my_pack "咖啡是什么?"

# 4) 交给小析(自动发现,不需要改代码)
python experts/GoChat.py "咖啡是什么?"
python experts/GoChat.py --packs           # 应该能看到你的包
```

## 文件说明

```
expert-template/
├── README.md                  ← 本文件(最短路径)
├── pack.json                  ← ★ 只改这里:身份、领域词、条目、边界、安全规则
└── tests/test_pack.py         ← 模板自检(跟着 pack.json 走,换领域不用改)
```

数据包目录里**可以**再放两个可选文件,用来覆盖核心的默认数据:

| 文件 | 效果 |
|---|---|
| `kb.json` | 覆盖事实库(与核心默认库合并) |
| `word_vectors.json` | 覆盖词向量与关系图 |

## 你要改的四处

| 位置 | 改什么 |
|---|---|
| `pack.json` → `expert` | `id`、`name`(**领域名**,不是人名)、`tagline`、`scope`(领域词)、`limits`(不做什么) |
| `pack.json` → `entries` | 你的知识条目:问题、别名、关键词、答案、要点、相关条目 |
| `pack.json` → `safety` | 保留公共红线;需要时加本领域的高危规则 |
| `experts/tests/test_packs.py` → `PROBES` | 给新数据包登记三条探针(canonical / miss / offtopic) |

目录名(`my_pack`)与 `expert.id` 保持一致最省心;框架按**目录**发现知识,
按 `expert.id` 展示归属。

## 可选:角色与转交

- 普通知识包不写 `role`(默认 `domain`)。
- 想做兜底包,写 `"role": "general"` —— **同一时间只保留一个**;
  它只在领域包都没命中时出场(接寒暄与"答不上来"的那两句)。
- 想声明"命中某些关键词就再交给别的包":

  ```json
  "routes": [{"keywords": ["电场"], "to": "math"}]
  ```

## 可选:包关系(包含 / 被包含)

想表达"苹果 ⊆ 水果",在 `expert` 段写**一个方向**就够(两边等价):

```json
"contains": ["apple"]        // 本包包含 apple
"contained_by": ["fruit"]    // 本包被 fruit 包含
```

效果:命中一个包后,它用条目关键词反向去查没查过的包;如果命中了包含它的父包,
就记一次"再命中",回答里补一句「A 有 C 中的性质,同时也有 D 中的性质」。
轮数由 `experts/config.json` 的 `rehit_rounds` 控制(默认 1,0 = 关闭)。
写错(不存在的包 / 自包含 / 成环)不会崩,`--packs` 与 `pack.py check` 会列出来。

## 可选:结构化知识(事实 / 规则 / 关系)

如果这块知识更适合写成事实而不是问答,可以在 `pack.json` 顶层加:

```json
"facts": [{"predicate": "是", "args": ["咖啡豆", "食品"]}],
"relations": [{"type": "属于", "from": "咖啡豆", "to": "食品"}],
"rules": [
  {"name": "含咖啡因", "if": {"fact": ["是", "?x", "咖啡豆"]},
   "then": ["含", "?x", "咖啡因"]}
]
```

格式与核心 `kb.json` 完全一致;有界规划器会用它们回答归属/关系/属性问题,
每条结论带依据。没有结构化字段时完全照旧。详见
[`experts/README.md`](../experts/README.md) 第 14 节。

## 自检

```bash
cd expert-template
python -m unittest discover -s tests -t .                    # 模板自身的测试(6 项)
python experts/core/run.py --pack expert-template --health   # 在仓库根目录跑
python experts/core/run.py --pack expert-template --domain
```

模板测试是"跟着 `pack.json` 走"的:只要数据包自洽,测试不用改。
它会真起子进程调核心,所以测的正是"这块知识能被核心正常驱动"。

## 不要做的事

- 不要把密钥、个人数据放进 `pack.json`;
- 不要删除 `safety` 里的公共红线(自伤方法 / 危险物制作 / 凭据索取 / 恶意程序);
- 不要让条目答案依赖联网或实时信息 —— 这个框架离线运行,答不了就如实说;
- **不要把代码放进数据包**:引擎在 `experts/core/smallai/`,数据包里出现 `.py`
  会被 `--health` 的"代码/数据分离"判为不通过;
- 不要用数据包去"改人格":名字与口吻在 `core/smallai/persona.py`;
- 不要直接改别人的数据包:复制一份再改。
