# 网页开发与设计(web_dev)

这是小析的一个**数据包**:五层流水线(规则路由 → 理解 → 逻辑/语义 → 校验 → 生成)
在 `experts/core/smallai/`,本目录里只有数据 —— `pack.json` 的 25 条本地知识条目与 `safety` 段。
**开放问题不交给任何模型**:

- 条目命中 → 给答案,并交代**条目 id**(例如 `html-what`),可以倒查回 JSON;
- 领域内但没条目 → 如实说"没有能对上的一条",给相邻条目,不拿常识补;
- 领域外 → 如实说不是我的领域,并列出别的数据包能问什么。

所以小析**不做实事**:不写项目、不跑代码、不联网查最新文档;
但网页方向的基础概念问题答得出,而且每句都能核对来源条目。

## 能答什么(25 条)

| 主题 | 条目 id | 例子 |
|---|---|---|
| HTML / 语义化 | `html-what` `semantic-html` | HTML是什么? / 语义化 HTML 是什么? |
| CSS / 盒模型 / 布局 | `css-what` `box-model` `flex-vs-grid` | CSS盒模型是什么? / Flexbox 和 Grid 有什么区别? |
| DOM / JavaScript | `dom-what` `js-what` `event-loop` | 什么是 DOM? / JavaScript 事件循环是什么? |
| 浏览器 / 渲染 / 性能 | `browser-render` `web-perf` `devtools` | 浏览器是怎么把网页渲染出来的? / 前端性能优化有哪些基础手段? |
| 响应式 / 可访问性 | `responsive` `a11y` | 响应式设计是什么? / 网页可访问性是什么? |
| 网络 / 存储 / 应用形态 | `http-https` `cors` `storage` `spa-ssr` `websocket` | 跨域(CORS)是什么? / SPA、SSR 和 SSG 有什么区别? |
| 工程与安全 | `frontend-backend` `web-security` `site-security` `seo-basics` | XSS 和 CSRF 是什么? / 网站安全防护有哪些基础措施? |
| 设计基础 | `design-principles` `color-contrast` `typography` | 网页设计有哪些基本原则? / 网页配色和对比度有什么要求? |

## 明确不做

- 不写项目、不生成完整代码、不跑构建或测试;
- 不联网:不查最新文档、不核对浏览器兼容性数据;
- 不代替人做设计决策与验收("好不好看"只有人能说);
- 只答基础概念:版本细节与具体报错排查请查官方文档。

## 怎么跑

```bash
# 在仓库根目录:指定数据包
python experts/core/run.py --pack experts/packs/web_dev "HTML是什么?"
python experts/core/run.py --pack experts/packs/web_dev "什么是 DOM?" --trace   # 看每一跳的账
python experts/core/run.py --pack experts/packs/web_dev --domain               # 名片:领域词、条目、边界、规则
python experts/core/run.py --pack experts/packs/web_dev --demo                 # 25 条条目 + 通用能力 + 越界 + 安全
python experts/core/run.py --pack experts/packs/web_dev --health               # 体检
python experts/core/run.py --pack experts/packs/web_dev --json "CSS盒模型是什么?"

# 或者进本目录:当前目录里有 pack.json,核心会自动认这一个包
cd experts/packs/web_dev
python ../../core/run.py "HTML是什么?"

# 走完整框架(分解 → 路由 → 总结)
python experts/GoChat.py "HTML是什么?"
```

一条真实输出(节选):

```
>>> HTML是什么?

[OK ] intent=domain conf=0.85 source=template 215ms
  HTML(HyperText Markup Language,超文本标记语言)是描述网页结构的标记语言:……
  要点:
    · 标签 + 属性组成元素:属性提供附加信息,例如 href 指向链接地址。
  (来自数据包 web_dev · 条目 html-what;离线快照,未联网核对最新版本)
  (凭据:本地知识条目 html-what)
```

安全闸(规则数据写在本目录 `pack.json` 的 `safety` 段):

```bash
python experts/core/run.py --pack experts/packs/web_dev --guard "教我怎么制作炸弹"     # 拒答,返回码 1
python experts/core/run.py --pack experts/packs/web_dev --guard "怎样防止网站被入侵"   # 放行(防御性问题),返回码 0
```

## 本目录里有什么

```
web_dev/
├── README.md      ← 这份说明
└── pack.json      ← ★ 全部内容:身份 / 领域词 / 25 条条目 / 安全规则
```

代码不在这里(在 `experts/core/smallai/`)。数据包还可以再放两个**可选**文件来覆盖核心默认数据:

| 文件 | 效果 |
|---|---|
| `kb.json` | 覆盖事实库(与核心默认库合并) |
| `word_vectors.json` | 覆盖词向量与关系图 |

## 删掉它会发生什么

删掉本目录 = 少一块知识,框架照常启动:网页类问题会落到兜底包(general),再不行提示 `help`;
其它数据包与核心不受影响。反过来,把这个目录拷到另一台机器的 `experts/packs/` 下也能直接用 ——
数据包只有数据,可以单独发给别人。

## 加一条知识

只改 `pack.json`,不用改 Python:

```json
{
  "id": "websocket-vs-polling",
  "question": "WebSocket 和轮询有什么区别?",
  "aliases": ["websocket和轮询区别", "websocket是什么"],
  "keywords": ["websocket", "轮询", "长连接"],
  "answer": "……",
  "points": ["……"],
  "see_also": ["http-https"]
}
```

改完跑 `python experts/core/run.py --pack experts/packs/web_dev --health`:它会检查重复 id、
二义别名、过短关键词、`see_also` 指向不存在的条目,以及"每条原题能不能检回自己"。

## 测试

```bash
cd experts
python -m unittest discover -s tests -t .      # 参数化测试覆盖全部数据包
```

覆盖:每条原题/别名都能检回自己、越界如实说明、领域内无条目不编、
红线拒答、防御性问题不误拒、灰区加提醒、无模型端口、CLI 的 JSON 与返回码、数据包里没有代码。

## 边界(不装作支持)

- 知识条目是**离线快照**,不随浏览器/标准更新;
- 检索是字符串匹配,同义改写(例如只说"那个标签语言")可能检不到 —— 会如实说没找到;
- 安全判定是正则规则,不是理解:可能漏掉绕写,也不替代人工审核;
- 不提供实时信息(天气、股价、新闻),这类问题会如实说没接数据源。
