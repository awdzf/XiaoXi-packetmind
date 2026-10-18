# Java(java)

这是小析的一个**数据包**:五层流水线(规则路由 → 理解 → 逻辑/语义 → 校验 → 生成)
在 `experts/core/smallai/`,本目录里只有数据 —— `pack.json` 的 23 条本地知识条目与 `safety` 段。
**开放问题不交给任何模型**:

- 条目命中 → 给答案,并交代**条目 id**(例如 `jvm`),可以倒查回 JSON;
- 领域内但没条目 → 如实说"没有能对上的一条",给相邻条目,不拿常识补;
- 领域外 → 如实说不是我的领域,并列出别的数据包能问什么。

所以小析**不做实事**:不写项目、不跑 `javac`/`mvn`/测试、不联网;
但 Java 方向的基础概念问题答得出,而且每句都能核对来源条目。

## 能答什么(23 条)

| 主题 | 条目 id | 例子 |
|---|---|---|
| 语言与运行环境 | `java-what` `jvm` `jdk-jre` `bytecode` | Java是什么? / JVM是什么? / JDK、JRE 和 JVM 有什么区别? |
| 面向对象 | `oop` `class-object` `interface-abstract` `overload-override` | 面向对象的三大特性是什么? / 接口和抽象类有什么区别? |
| 字符串与相等 | `string-types` `equals` | String、StringBuilder 和 StringBuffer 有什么区别? / == 和 equals 有什么区别? |
| 集合 | `collections` `hashmap` `generics` | List、Set、Map 有什么区别? / HashMap 的原理是什么? |
| 异常 | `exception` | Java 的异常体系是什么? |
| 内存与并发 | `gc` `thread` `sync` | 垃圾回收是怎么工作的? / synchronized 和 volatile 有什么区别? |
| 工程与框架 | `spring` `maven` `compile-run` | Spring 是什么? / Maven 是什么? / 怎么编译运行一个 Java 程序? |
| 语言细节 | `primitives` `final-static` `versions` | 基本类型和包装类是什么? / Java 有哪些 LTS 版本? |

## 明确不做

- 不写项目、不生成完整代码、不跑 `javac`/`mvn`/测试;
- 不联网:不查最新版本文档与支持截止日期;
- 不代替人做架构决策与代码评审;
- 只答基础概念:具体报错排查与性能调优请给出现场信息,或查官方文档。

## 怎么跑

```bash
# 在仓库根目录:指定数据包
python experts/core/run.py --pack experts/packs/java "JVM是什么?"
python experts/core/run.py --pack experts/packs/java "HashMap 的原理是什么?" --trace   # 看每一跳的账
python experts/core/run.py --pack experts/packs/java --domain                         # 名片
python experts/core/run.py --pack experts/packs/java --demo                           # 23 条条目 + 通用能力 + 越界 + 安全
python experts/core/run.py --pack experts/packs/java --health                         # 体检
python experts/core/run.py --pack experts/packs/java --json "== 和 equals 有什么区别?"

# 或者进本目录:当前目录里有 pack.json,核心会自动认这一个包
cd experts/packs/java
python ../../core/run.py "JVM是什么?"

# 走完整框架(分解 → 路由 → 总结)
python experts/GoChat.py "JVM是什么?"
```

一条真实输出(节选):

```
>>> JVM是什么?

[OK ] intent=domain conf=0.85 source=template 228ms
  JVM(Java Virtual Machine,Java 虚拟机)是执行 Java 字节码的运行时:……
  (来自数据包 java · 条目 jvm;离线快照,未联网核对最新版本)
  (凭据:本地知识条目 jvm)
```

## 本目录里有什么

```
java/
├── README.md      ← 这份说明
└── pack.json      ← ★ 全部内容:身份 / 领域词 / 23 条条目 / 安全规则
```

代码不在这里(在 `experts/core/smallai/`)。数据包还可以再放两个**可选**文件来覆盖核心默认数据:

| 文件 | 效果 |
|---|---|
| `kb.json` | 覆盖事实库(与核心默认库合并) |
| `word_vectors.json` | 覆盖词向量与关系图 |

## 删掉它会发生什么

删掉本目录 = 少一块知识,框架照常启动:Java 类问题会落到兜底包(general),再不行提示 `help`;
其它数据包与核心不受影响。反过来,把这个目录拷到另一台机器的 `experts/packs/` 下也能直接用。

## 加一条知识

只改 `pack.json`:

```json
{
  "id": "record",
  "question": "Java 的 record 是什么?",
  "aliases": ["record是什么", "什么是record"],
  "keywords": ["record", "记录类"],
  "answer": "……",
  "points": ["……"],
  "see_also": ["primitives"]
}
```

改完跑 `python experts/core/run.py --pack experts/packs/java --health`,它会检查重复 id、
二义别名、过短关键词、`see_also` 指向不存在的条目,以及"每条原题能不能检回自己"。

## 测试

```bash
cd experts
python -m unittest discover -s tests -t .      # 参数化测试覆盖全部数据包
```

覆盖:原题/别名检索、越界说明、领域内无条目不编、红线拒答、防御性问题不误拒、
灰区提醒、无模型端口、CLI 的 JSON 与返回码、数据包里没有代码。

## 边界(不装作支持)

- 知识条目是**离线快照**,不随 JDK 版本更新;
- 检索是字符串匹配,同义改写可能检不到 —— 会如实说没找到;
- 安全判定是正则规则,不是理解,也不替代人工审核;
- 不提供实时信息,不做代码诊断与性能调优的具体参数建议。
