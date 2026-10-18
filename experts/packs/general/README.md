# 闲聊与兜底(general)

这是小析的一个**数据包**,但它不负责某个学科,负责**接住不落在领域里的问题**:
寒暄、感谢、自我介绍、寒暄式追问。它的角色写在本目录 `pack.json` 里:

```json
"role": "general"
```

框架看到这个角色就把它放在**兜底位**:

- 查找先找领域包;只要有一个领域包命中,兜底包不参与;
- 一个领域包都没命中时,才轮到兜底包;
- 兜底包也接不住(既不是寒暄,也没落到任何条目)时,我才会给一句
  "输入 help 看我能做什么" 的提示 —— **提示只在真没命中时出现**。

所以「你好」得到的是「你好呀。想聊点什么?……」,而不是一份使用说明。

## 能接什么(12 条)

| 条目 id | 例子 |
|---|---|
| `greeting` | 你好 / 您好 / 嗨 / 哈喽 / hi / hello / 早上好 |
| `thanks` | 谢谢 / 多谢 / 感谢 |
| `bye` | 再见 / 拜拜 |
| `who` | 你是谁 / 你叫什么 / 介绍一下你自己 |
| `capability` | 你能做什么 / 你会什么 / 怎么用你 |
| `how-are-you` | 你好吗 / 最近怎么样 |
| `in-there` | 在吗 / 在不在 |
| `tired` | 今天好累 / 心累 / 累了 |
| `praise` | 你真棒 / 厉害 |
| `apology` | 对不起 / 抱歉 |
| `chat` | 陪我聊会儿天 / 聊聊天 |
| `ok` | 好的 / 收到 |

## 它不做什么

- 不做领域判断:数学/物理/网页/Java/情感/伦理问题交给对应的数据包;
- 不替代专业援助,不编造具体信息;
- 不联网:只接寒暄与兜底;
- 答不上就直说 —— 这是框架的"提示只在真没命中时出现"的落点。

## 怎么跑

```bash
# 在仓库根目录:指定数据包
python experts/core/run.py --pack experts/packs/general "你好"
python experts/core/run.py --pack experts/packs/general "你是谁"
python experts/core/run.py --pack experts/packs/general --domain
python experts/core/run.py --pack experts/packs/general --health

# 或者进本目录
cd experts/packs/general
python ../../core/run.py "你好"

# 走完整框架(这时它是兜底位)
python experts/GoChat.py "你好"
```

## 角色与发现

框架不写死数据包名单,而是扫描 `experts/packs/*/pack.json`。
兜底包靠 `role: "general"` 被识别;想换一个兜底包,只要把新包的
`role` 设为 `general`(同一时间建议只保留一个)。

**移除它也不会影响框架启动**:只是少掉寒暄与兜底 —— 没命中的问题会直接
回到"暂时答不上来 + 输入 help"的提示。

## 本目录里有什么

```
general/
├── README.md      ← 这份说明
└── pack.json      ← ★ 全部内容:身份 / 领域词 / 12 条条目 / 安全规则
```

代码不在这里(在 `experts/core/smallai/`)。数据包还可以再放 `kb.json` /
`word_vectors.json` 来单独覆盖事实库与词表。

## 测试

```bash
cd experts
python -m unittest discover -s tests -t .      # 参数化测试覆盖全部数据包(含本包)
```
