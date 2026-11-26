"""GoChat:在终端里和小析聊天。

小析是**一个人格**,知识是若干个**数据包**:
  · 人格(名字/口吻/自述)在 `experts/core/smallai/persona.py`;
  · 知识(条目/领域词/红线)在 `experts/packs/<名字>/pack.json`;
  · 核心代码只有一份,在 `experts/core/smallai/`。

一次问答(全部本地、不接模型):
    分解 —— 切词 + 造候选(汉字排列 / 谐音字 / ASCII 错字);
            拆出来的**非常用词**就是关键词,也是要记住的东西
    查找 —— 候选逐条尝试,哪个数据包里能对上就查哪个(最多查 max_packs 个)
    引用 —— 只引用数据包条目的原句,点明「数据包 + 条目 id」,不生成新知识

**单进程(默认)**:核心代码只 import 一次;每个数据包按需装配一条流水线并缓存,
下一问直接复用,只有 `pack.json` / `kb.json` / `word_vectors.json` 变了才重建。
`--subprocess` 回到"每问一次开一个子进程"的旧路径(隔离优先;也是单进程失败时的退路)。

**上下文**:每一轮会记住拆出来的关键词(有界,默认最近 8 轮 / 12 个词)。
下一句如果是"接着上一句问的"(带 它/这个/还有… 或全是虚词),而且直接查找
一个包都没命中,才会拿记忆里的关键词再查一次;有直接命中就不动用记忆。

**上限之外,问你要不要补查**:当一个问题的关键词命中了超过 `max_packs` 个包,
会弹「能否再调用数据包[y/n]:」—— `y` 只对**这一次**补查,配置的上限不变。

**包关系与再命中**:`pack.json` 可以声明包含关系(写一边就够):
    "contains": ["apple"]       # 本包包含 apple (apple ⊆ 本包)
    "contained_by": ["fruit"]   # 本包被 fruit 包含 (本包 ⊆ fruit)
命中一个包后,它会用自己条目里的关键词**反向**去查还没查过的包(每轮最多
max_packs 个);如果命中的是"包含已命中包"的父包,就叫**再命中**,回答会补一句
「A 有 数据包 C 中的性质,同时也有 数据包 D 中的性质」。轮数由 `rehit_rounds`
决定(默认 1,0 = 关闭;`--rehit N` / `:rehit N` 临时改)。

**结构化知识与有界规划**:`pack.json` 可选 `facts` / `rules` / `relations` / `datalog`。
归属/能力、关系、属性这三类问题会先走有界规划器(最多 4 步),用事实库和规则推出结论,
并列出依据；Datalog 子集补上递归/传递闭包（祖先、可达），每条结论带证明树。
没有结构化依据就落回普通检索。它不是通用推理机,也不生成新知识。

**教它新知识**:推荐直接教一对 **问题~答案**:
    # 量子退相干是什么~量子系统的相干性因与环境的纠缠而衰减、叠加态看起来变成经典概率的过程
旧写法 `#一句话知识` 仍然有效（问题=答案）。它会先拆出关键词(再抽一组低权重的
**次要关键词**),弹「是否写入记忆[y/n]:」,`y` 才写进记忆数据包
(`experts/packs/memory/`),`n` 什么都不改。同一道题再教一次会更新答案；
显式修正用 `#!问题~新答案`（CBR revise，旧答案留在 `revisions` 里）。
以后问起时,**记忆包和别的包都能答**的话它会如实说"有歧义",按 `author > memory`
排序（低优先级仍列出），不替你判断谁对。同一个"What 是什么"如果命中两条记忆,
会把两条都列出来;追问"那它是什么意思"会用次要关键词命中"意思"那条。

**查看 / 忘掉教过的知识**(同一个 `#` 家族):
    #?              列出我记着的知识(**原样回放你当时输入的那一行**)
    #? 量子         只看匹配的
    #- 量子退相干   忘掉这条(先列出来,确认后才删)
`#-` 也是按"原话 / 关键词 / 条目 id"找;找不到就不删,并告诉你现在记着几条。

**检索层**:规则命中优先;规则弱/没命中时,用 BM25 倒排 + TF-IDF + 笔记标题分层
做混合兜底;证据不足时按校准分弃权,而不是拿一个拍脑袋的固定阈值硬答。
BM25 索引按数据包增量更新(教完一条只动记忆包),`--trace` / `:stat` 能看到
置信度、查询扩展次数与最近增量了哪个包。

**文档模式**(只读某一份文档):句尾写 `@文档名` 或 `@@相对路径`,
这一轮**只读那一份文档**,并且只引用原文(带章节与行号),不查数据包。

    python experts/GoChat.py "怎么回滚@手册"           # 在 docs/ 里找 手册.md
    python experts/GoChat.py "怎么回滚@@docs/手册.md"  # 按 xiaoxi.py 的相对路径取
    python experts/GoChat.py "@手册"                  # 只给文档不给问题 -> 列目录

约定:文档放 `<项目根>/docs/`(项目根 = xiaoxi.py 所在目录);可省扩展名、
大小写不敏感、可带子目录;`@@` 只接受**项目目录内**的相对路径(不能用 `..` 跑出去),
只认 `.md` / `.markdown` / `.txt`。找不到、有歧义、内容命中安全底线都会如实说明。
`--docs` 或对话里的 `:docs` 列出目录里有哪些文档。

**可调开关**:`experts/config.json` 的 `max_packs`(一次最多查几个包,1–8,
默认 3)、`rehit_rounds`(再命中轮数,0–3,默认 1)、`context`(记忆开关与大小),
以及 `retrieval`(BM25/混合/扩展/分层/弃权/条数与字符预算)与
`trust`(高置信条目、冲突优先级)。
命令行 `--max-packs N` / `--rehit N` 可临时覆盖。
上限只按分数截断,不降低命中阈值 —— 所以精确性不变。

**信任与冲突**:作者包里的条目默认高置信;用户可用 `:trust <包>/<条目>` 把任意
条目标成高/低置信。作者包与用户记忆对同一问题都有说法时,默认按
`author > memory` 排序,低优先级仍列出并注明;`conflict_policy=strict` 只留高的。

问"你是谁 / 你能做什么"由人格直接答,不查任何数据包;只有当所有数据包都接不住时,
才会提示你输入 help。

用法:
    python experts/GoChat.py                     # 进入终端对话
    python experts/GoChat.py "你是谁"            # 小析的自我介绍
    python experts/GoChat.py "引力波是什么?"      # 单次提问(查数据包)
    python experts/GoChat.py "# 量子退相干是什么~量子系统的相干性因与环境纠缠而衰减"  # 教一道题
    python experts/GoChat.py "#!量子退相干是什么~新的答案"   # 修正同一道题
    python experts/GoChat.py "#?"                # 看教过的知识(原话回放)
    python experts/GoChat.py "#- 量子退相干"     # 忘掉一条(会问确认)
    python experts/GoChat.py --trace "HTML是什么?"  # 显示候选与查找明细
    python experts/GoChat.py --decompose "什么的场向量"  # 只看分解
    python experts/GoChat.py --route "向量场和电场的关系?" # 只看查找(含再命中)
    python experts/GoChat.py --guard "某段文本"    # 只看安全判定
    python experts/GoChat.py --packs              # 看数据包与包含关系
    python experts/GoChat.py --docs               # 看 docs/ 里有哪些文档
    python experts/GoChat.py "怎么回滚@手册"       # 只读 docs/手册.md 这一份并引用原文
    python experts/GoChat.py "怎么回滚@@docs/手册.md"  # 用相对 xiaoxi.py 的路径指定
    python experts/GoChat.py "@手册"              # 只给文档不给问题:列它的目录
    python experts/GoChat.py --missed             # 看最近没接住的问题(本地学习队列)
    python experts/GoChat.py --eval               # 跑检索评测集(hit@1 / MRR / 弃权)
    python experts/GoChat.py --max-packs 2 "..."  # 这一次最多查 2 个包
    python experts/GoChat.py --rehit 0 "..."      # 这一次不做再命中
    python experts/GoChat.py --no-context "..."   # 这次不启用上下文记忆
    python experts/GoChat.py --subprocess "..."   # 这一次改回子进程(隔离优先)

对话中的命令:
    help  :quit  :packs  :docs [文档名]  :trace [on|off]  :detail [on|off]  :stat 看进程/缓存
    :ctx 看上下文   :forget 忘掉上下文   :max [N] 看/改上限   :rehit [N] 看/改再命中轮数
    :missed [N] 看漏问   :missed clear 清空漏问
    :trust [high|low|off|author on|off] [包/条目]  看/改高置信条目（作者包默认高）
    :priority [顺序...]  看/改知识冲突优先级（默认 author memory）
    :eval [cases.jsonl]  跑检索评测集（正例 hit@1 / MRR / 负例弃权）
    :memory / :taught 看教过的知识(等同 #?)   :decompose <句子>   :route <句子>   :guard <文本>
    #<知识>  教它一条新知识(写入记忆数据包前会再问一次)
    #?      看教过的知识(原话回放)     #-<关键词或原话>  忘掉一条(先确认)
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from framework import (  # noqa: E402  (必须在 sys.path 调整之后)
    DISCOVERY_ERRORS,
    GoChat,
    PackGraph,
    __version__,
    discover,
    evaluation,
    formula,
    learning,
    lessons,
    persona,
    settings,
)


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")   # type: ignore[union-attr]
        except Exception:                                            # noqa: BLE001
            pass
    # 从管道/文件喂命令时(非终端),按 UTF-8 读输入:`# 知识` 这类中文才不会被
    # 按系统代码页误解成乱码。终端里不动它,免得影响本机输入法实际发送的编码。
    try:
        if sys.stdin is not None and not sys.stdin.isatty():
            sys.stdin.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:                                                # noqa: BLE001
        pass


def _prepare_stdin() -> None:
    """把管道进来的字节按"真能读出来"的编码解码。

    终端里不动它。管道/重定向时没有两全的编码:cmd 的管道是系统代码页(中文 Windows
    通常是 GBK),PowerShell 7 默认是 UTF-8。先按 UTF-8 试,解不开再按系统代码页
    (`mbcs`)—— 纯 ASCII 与 UTF-8 都走第一条,GBK 只在 UTF-8 解不开时才落到第二条。

    代价:非终端输入会被一次性读完(交互式管道里"边写边答"变成"写完再答"),
    换来的是 `echo 你是谁 | python xiaoxi.py` 在各家 shell 里都能读到正常的中文。
    """
    stream = sys.stdin
    if stream is None:
        return
    try:
        if stream.isatty():
            return
    except (OSError, ValueError):
        return
    buffer = getattr(stream, "buffer", None)
    if buffer is None:
        return
    try:
        data = buffer.read()
    except (OSError, ValueError):
        return
    text: str | None = None
    for encoding in ("utf-8", "mbcs"):
        try:
            text = data.decode(encoding)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    sys.stdin = io.StringIO(text if text is not None else data.decode("utf-8", "replace"))


def _banner(chat: GoChat, trace: bool, detail: bool) -> str:
    names = " · ".join(card.folder for card in chat.cards) or "(一个数据包也没有)"
    general = next((card.folder for card in chat.cards if card.is_general), None)
    context = "on" if chat.context_enabled else "off"
    engine = "单进程(内核常驻)" if chat.engine == "inprocess" else "子进程(隔离优先)"
    doc_count = chat.docs.count() if chat.docs.available else 0
    documents = (
        f"文档 {doc_count} 份(问题@文档名 只读一份)"
        if chat.docs.available
        else "文档模式:新建 docs/ 放 .md/.txt,用 问题@文档名"
    )
    return (
        "=" * 72 + "\n"
        f"{persona.time_greeting()},我是{persona.NAME}。\n"
        "  分解 → 查数据包 → 引用作答(纯规则,不接模型)\n"
        f"  知识 {len(chat.cards)} 个数据包:{names}\n"
        f"  兜底包:{general or '未安装(不影响其它数据包)'}\n"
        f"  引擎 {engine} · 一次最多查 {chat.max_packs} 个包 · "
        f"再命中 {chat.rehit_rounds} 轮 · 上下文 {context}\n"
        f"  {documents}\n"
        f"  trace={'on' if trace else 'off'} detail={'on' if detail else 'off'}"
        " · help 可看命令\n"
        + "=" * 72
    )


def _print_packs() -> None:
    cards = discover()          # 每次重新发现:教完知识后能立刻看到新条目
    graph = PackGraph.from_cards(cards)
    print("=" * 72)
    print(f"{persona.NAME} 现在装着 {len(cards)} 个数据包(核心代码 1 份)")
    print("=" * 72)
    if not cards:
        print("  一个也没有。放一个 experts/packs/<名字>/pack.json 就会出现。")
    for card in cards:
        role = " · 记忆包" if card.is_memory else (" · 兜底包" if card.is_general else "")
        relation = graph.describe(card.folder)
        print(f"\n数据包 {card.folder}({card.name}){role}")
        print(f"  {card.tagline}")
        print(f"  路径:{card.pack_path}")
        if relation:
            print(f"  关系:{relation}")
        print(f"  条目 {len(card.entries)} 条;例子:")
        for question in card.examples(3):
            print(f"    · {question}")
        if card.has_structure:
            print(
                f"  结构化: 事实 {len(card.facts)} / 规则 {len(card.rules)} / "
                f"关系 {len(card.relations)}"
            )
    problems = graph.problems()
    if problems:
        print("\n[注意] 数据包关系有问题(已跳过,不影响检索):")
        for problem in problems:
            print("  · " + problem)
    print()


def _print_decomposition(chat: GoChat, text: str) -> None:
    dec = chat.decompose(text)
    print("[分解] " + (" | ".join(dec.tokens) or "(空)"))
    print("[关键词] " + ("、".join(dec.keywords) or "(没有非常用词)"))
    print(f"  候选 {len(dec.candidates)} 条:")
    for cand in dec.candidates[:40]:
        print(f"    · {cand.text}[{cand.kind}] <- {cand.origin or '全句'}")
    if len(dec.candidates) > 40:
        print(f"    …其余 {len(dec.candidates) - 40} 条略")
    for note in dec.notes:
        print("  注:" + note)
    print()


def _print_routing(chat: GoChat, text: str) -> None:
    dec, routing = chat.route(text)
    print("[分解] " + (" | ".join(dec.tokens) or "(空)"))
    print("[关键词] " + ("、".join(dec.keywords) or "(没有非常用词)"))
    print(f"[查找] {routing.notes}")
    if not routing.targets:
        print("  没有数据包能对上这一句。")
    for target in routing.targets:
        print(f"  · {target.label()} 得分 {target.score}")
        print(f"      理由:{' / '.join(target.reasons[:6]) or '-'}")
        if target.fallback_question:
            print(f"      兜底问法:{target.fallback_question}")
    print()


def _print_context(chat: GoChat) -> None:
    print("=" * 72)
    print("[上下文] " + chat.context_line())
    print("=" * 72)
    keys = chat.remembered()
    if keys:
        print("  记得的关键词:" + "、".join(keys))
    if not chat.context_enabled:
        print("  (本次运行关掉了上下文;要用就重启时去掉 --no-context)")
    print()


def _print_memory(chat: GoChat, query: str = "") -> None:
    """`#?`:把教过的知识按**用户当时的原话**逐行列出来。"""
    print(chat.taught_text(query))
    print()


def _forget(chat: GoChat, raw: str) -> None:
    """`#-……`:先列出对得上的条目,确认后才删(不静默删任何东西)。"""
    query = lessons.parse_forget(raw)
    if query is None:
        print("用法:以「#-」开头写要忘掉的内容,例如:")
        print("  #- 量子退相干        (按那句话里的一小段找)")
        print("  #- user-0001        (按条目 id 找)")
        print("先看看现在记着什么:#?\n")
        return
    if not query:
        print("「#-」后面要写忘掉哪一条(按原话、关键词或条目 id 都行)。")
        print("现在记着这些:\n")
        print(chat.taught_text())
        print()
        return

    matches = chat.taught(query)
    if not matches:
        print(f"[记忆] 没有找到和「{query}」对得上的条目,什么都没删。")
        print(f"现在记着 {len(chat.taught())} 条;用 #? 看全部。\n")
        return

    print(f"[记忆] 找到 {len(matches)} 条对得上的:")
    for entry in matches:
        print("  " + lessons.raw_of(entry))
    if not lessons.is_yes(_ask_yes_no(f"确认忘掉这 {len(matches)} 条?[y/n]:")):
        print("[记忆] 好,不删。\n")
        return

    try:
        removed = chat.forget_taught(query)
    except (RuntimeError, OSError, ValueError) as exc:         # noqa: BLE001
        print(f"[记忆] 删不掉:{exc}\n")
        return
    print(f"[记忆] 已忘掉 {len(removed)} 条。")
    print(f"[记忆] 现在还剩 {len(chat.taught())} 条你教的知识。\n")


def _print_docs(chat: GoChat) -> None:
    """看 docs/ 里有哪些文档(文档模式的入口)。"""
    print("=" * 72)
    print(chat.documents_text())
    print("=" * 72)
    print()


def _print_stat(chat: GoChat) -> None:
    """看这一轮用的进程形态、流水线缓存命中与上一问耗时。"""
    info = chat.engine_info()
    print("=" * 72)
    print("[引擎] " + (
        "单进程(核心常驻,按数据包缓存流水线)"
        if info.get("mode") == "inprocess" else "子进程(每问一次开一个)"
    ))
    if info.get("mode") == "inprocess":
        ready = "就绪" if info.get("ready") else "未加载(还没问过问题)"
        print(f"  内核 {ready} · 已装 {info.get('loaded', 0)} 条流水线 · "
              f"词向量 {info.get('semantics', 0)} 份 / 事实库 {info.get('logic', 0)} 份共享")
        print(f"  构建 {info.get('builds', 0)} 次 / 命中缓存 {info.get('hits', 0)} 次 · "
              f"这一进程里问了 {info.get('asks', 0)} 次 / 安全判定 "
              f"{info.get('guards', 0)} 次")
        if info.get("error"):
            print(f"  内核错误:{info['error']}")
    else:
        print("  (想比较速度:去掉 --subprocess,或重启后不传这个参数)")
    print(f"[速度] 上一问 {float(info.get('last_ms') or 0):.0f}ms")
    retrieval = info.get("retrieval") or {}
    if retrieval:
        bm25 = retrieval.get("documents", 0)
        terms = retrieval.get("terms", 0)
        print(f"[检索] BM25 文档 {bm25} / 倒排词 {terms} · "
              f"查询扩展 {retrieval.get('expansions', 0)} 次 · "
              f"最近增量 {','.join(retrieval.get('last_refresh') or []) or '无'}")
    for note in info.get("notes") or ():
        print("[回退] " + str(note))
    print(f"[文档] {chat.docs.count()} 份 · 读盘 {chat.docs.reads} 次 / "
          f"复用 {chat.docs.reuses} 次 · 目录 {chat.docs.root}")
    print("=" * 72)
    print()


def _print_eval(chat: GoChat, path: str | None = None) -> int:
    """跑评测集并返回退出码:0 = 全过,1 = 有用例没过,2 = 评测集都跑不了。

    之前这里恒返回 None(=> 退出码 0),CI 里 `--eval` 失败也会"绿"。
    """
    try:
        report = evaluation.run(chat, path)
    except (OSError, ValueError) as exc:
        print(f"[评测] 跑不了：{exc}\n")
        return 2
    print("=" * 72)
    print("[评测] " + report.render())
    print("=" * 72)
    print()
    return 1 if report.failures else 0


def _print_trust(chat: GoChat) -> None:
    print("=" * 72)
    print("[信任] 条目置信与冲突优先级")
    for line in chat.trust_lines():
        print("  " + line)
    print("  改法：:trust <包>/<条目>（高）| :trust low <包>/<条目>")
    print("        :trust off <包>/<条目>（取消显式设置）| :trust author on|off")
    print("=" * 72)
    print()


def _set_trust(chat: GoChat, argument: str) -> None:
    parts = argument.split()
    if not parts:
        _print_trust(chat)
        return
    if parts[0].lower() in ("author", "作者"):
        if len(parts) < 2 or parts[1].lower() not in ("on", "off", "true", "false"):
            print("用法：:trust author on|off\n")
            return
        enabled = parts[1].lower() in ("on", "true")
        chat.set_author_entries_high(enabled)
        print(f"作者包条目默认高置信 = {'on' if enabled else 'off'}\n")
        return
    level = "high"
    if parts[0].lower() in ("high", "low", "normal", "off"):
        level = "normal" if parts[0].lower() == "off" else parts[0].lower()
        parts = parts[1:]
    if not parts:
        print("用法：:trust [high|low|normal|off] <包>/<条目>\n")
        return
    ref = parts[0]
    try:
        chat.set_trust_ref(ref, level)
    except (OSError, ValueError) as exc:
        print(f"[信任] 改不了：{exc}\n")
        return
    print(f"[信任] {ref} = {level}（已写回 config.json）\n")


def _print_priority(chat: GoChat) -> None:
    print("=" * 72)
    print("[冲突优先级] " + chat.trust.priority_text())
    print("  策略：" + chat.trust.conflict_policy
          + "（prefer = 高的在前、低的仍列出；strict = 只留高的）")
    print("  改法：:priority author memory  |  :priority math physics memory")
    print("=" * 72)
    print()


def _set_priority(chat: GoChat, argument: str) -> None:
    tokens = tuple(argument.split())
    if not tokens:
        _print_priority(chat)
        return
    try:
        order = chat.set_conflict_priority(tokens)
    except (OSError, ValueError) as exc:
        print(f"[优先级] 改不了：{exc}\n")
        return
    print(f"[优先级] {' > '.join(order)}（已写回 config.json）\n")


def _print_guard(chat: GoChat, text: str) -> str:
    report = chat.guard(text)
    print(f"[安全判定] {report.get('level', 'ok')}")
    if report.get("rule_ids"):
        print("  命中规则:" + "、".join(str(r) for r in report.get("rule_ids") or ()))
    if report.get("labels"):
        print("  类别:" + str(report.get("labels")))
    for advice in report.get("advice") or ():
        print("  建议:" + str(advice))
    if report.get("error"):
        print("  注意:" + str(report["error"]))
    print()
    return str(report.get("level") or "ok")


def _ask_yes_no(prompt: str) -> str:
    try:
        return input(prompt + " ")
    except (EOFError, KeyboardInterrupt):
        print()
        return "n"


def _stdin_is_interactive() -> bool:
    """单次提问时判断有没有"人"能回答 y/n。

    管道/CI 里 stdin 不是终端:这时不该弹「能否再调用数据包」——
    既没人能答(只会在输出里留一条悬空提示),也会让脚本读到的回答错位。
    对话模式(`_interactive`)不受影响,它本来就在读 stdin。
    """
    try:
        return bool(sys.stdin is not None and sys.stdin.isatty())
    except Exception:                                          # noqa: BLE001
        return False


def _is_miss(result) -> bool:
    """这一轮是不是"没接住"（用来记漏问）。

    任何一条 ok 的回答都算接住了：兜底包的寒暄、人格自述、教过的记忆也算；
    拒答与空结果不算漏问（前者是明确拒绝，后者可能只是输入为空）。
    文档模式也不算漏问：没读到 / 没命中是"文档或问法"的事，不是知识缺口 ——
    记进学习队列只能教出"文档里的一句话"，没有意义。
    """
    if result.safety.get("level") == "refuse":
        return False
    if any(answer.source == "document" for answer in result.answers):
        return False
    return not any(answer.ok for answer in result.answers)


def _answer(
    chat: GoChat,
    text: str,
    trace: bool,
    detail: bool,
    ask_user=None,
) -> None:
    try:
        result = chat.ask(
            text, detail=detail, trace=trace,
            plain=not trace, ask_user=ask_user,
        )
    except Exception as exc:                                   # noqa: BLE001
        print(f"[错误] 这一轮没能完成:{type(exc).__name__}: {exc}")
        return
    print(result.summary)
    # 没接住 -> 记进本地学习队列(RAGless 的 missed query 思路);
    # 只记这一句与当时的关键词,不记聊天上下文,也不联网。
    if _is_miss(result):
        try:
            learning.log_missed(text, result.decomposition.keywords)
        except Exception:                                      # noqa: BLE001
            pass
    print()


def _print_missed(limit: int = 20) -> None:
    """看最近没接住的问题:下一条 `#` 教什么,从这里挑。"""
    records = learning.read_missed(limit)
    print("=" * 72)
    print(f"[漏问] 最近 {len(records)} 条我没接住的（只存在本地）")
    print("=" * 72)
    if not records:
        print("  还没有记录。问一句我答不上来的,就会出现在这里。")
        print()
        return
    for record in records:
        stamp = str(record.get("ts") or "")
        print(f"  · {stamp}  {record.get('query', '')}")
    print()
    print("  教它一条:直接输入「# <知识>」;清空记录:输「:missed clear」")
    print()


def _clear_missed() -> None:
    count = learning.clear_missed()
    print(f"[漏问] 清掉了 {count} 条记录(数据包与记忆不受影响)。\n")


def _teach_formula(chat: GoChat, raw: str, taught: "formula.Formula",
                   constants: dict[str, float] | None = None) -> None:
    """`#名字~算式:变量` 教一个公式：确认后写进记忆包，之后 `名字:值,值` 就能算。"""
    print("[公式] 要记住的公式:" + taught.name)
    print("[公式] 算式:" + taught.expr)
    print("[公式] 变量:" + "、".join(taught.vars))
    print("[公式] 以后这样算:" + taught.call_hint()
          + "  例：" + taught.name + ":" + ",".join("1" for _ in taught.vars))
    for note in formula.teach_warnings(taught, constants):
        print("[公式] 注意:" + note)
    if not lessons.is_yes(_ask_yes_no("是否写入记忆[y/n]:")):
        print("[公式] 好,不写。\n")
        return
    try:
        entry, created = chat.teach_formula(taught, raw=raw)
    except (RuntimeError, OSError, ValueError) as exc:     # noqa: BLE001
        print(f"[公式] 写不进去:{exc}\n")
        return
    verb = "已学会" if created else "已更新(同一个名字不重复写,算式会覆盖旧的)"
    print(f"[公式] {verb} 数据包 memory · 条目 {entry['id']}。")
    print(f"       现在可以直接算：{taught.call_hint()}")
    print(f"       也可以正常提问（教过的知识一样能被查到）：{taught.name}怎么算\n")


def _teach_constant(chat: GoChat, raw: str, taught: "formula.UserConstant") -> None:
    """`#名字=数值` 定一个常数：确认后写进记忆包，之后在算式里直接用这个名字。"""
    print("[常数] 要记住的常数:" + taught.name)
    print("[常数] 数值:" + taught.expr + f"  (= {taught.value!r})")
    if taught.name in formula.CONSTANTS:
        symbol = formula.constant_symbol_of(taught.name)
        print(f"[常数] 注意：这个名字和内置常数 {symbol} 重名 —— 你的定义会盖住它")
    if not lessons.is_yes(_ask_yes_no("是否写入记忆[y/n]:")):
        print("[常数] 好,不写。\n")
        return
    try:
        entry, created = chat.teach_constant(taught, raw=raw)
    except (RuntimeError, OSError, ValueError) as exc:     # noqa: BLE001
        print(f"[常数] 写不进去:{exc}\n")
        return
    verb = "已记住" if created else "已更新"
    print(f"[常数] {verb} 数据包 memory · 条目 {entry['id']}。")
    print(f"       现在可以在公式里直接用：{taught.name}\n")


def _teach(chat: GoChat, raw: str) -> None:
    """`#问题~答案` 教一道题；`#!问题~新答案` 修正（CBR revise）。

    旧写法 `#一句话知识` 仍然有效：问题与答案都是这句话。
    """
    # 公式（`#名字~算式:变量`）。必须排在 `#问题~答案` 之前：
    # `圆的面积~π*r^2:r` 用普通成对写法也能拆成 问题/答案，会被当成一句话知识存下去。
    body = lessons.parse_marker(raw)
    if body is not None:
        constants = chat.user_constants()
        taught_formula = formula.parse_teaching(body, constants)
        if taught_formula is not None:
            _teach_formula(chat, raw, taught_formula, constants)
            return
        if formula.looks_like_formula(body):
            problem = formula.explain_teaching(body, constants)
            if problem:
                print("[公式] 这句像是要教公式，但还差点意思：")
                print("  " + problem)
                print("  写对了是这样：  # 圆的面积~π*r^2:r")
                print("  之后要算就写：  圆的面积:3\n")
                return
        # 常数（`#名字=数值`）。排在公式之后：公式行里也可能出现 `=`（例如误写成方程），
        # 那种情况要交给上面的公式报错，而不是被当成"常数名不合法"。
        if "~" not in body and "=" in body:
            taught_constant, why = formula.parse_constant(body)
            if taught_constant is not None:
                _teach_constant(chat, raw, taught_constant)
                return
            if why:
                print("[常数] 这句像是要定常数，但还差点意思：")
                print("  " + why)
                print("  写对了是这样：  # c_light=2.998e8\n")
                return

    correction = lessons.parse_correction(raw)
    if correction is not None:
        question, answer = correction
        keys = chat.decomposer.decompose(question).keywords
        secondary = lessons.secondary_keywords(question, keys, chat.decomposer)
        print("[记忆] 要修正的题:" + question)
        print("[记忆] 新答案:" + answer)
        print("[记忆] 拆出的关键词:"
              + ("、".join(keys) or "(没有非常用词:以后只能靠原题命中)"))
        if not lessons.is_yes(_ask_yes_no("是否修正记忆[y/n]:")):
            print("[记忆] 好,不改。\n")
            return
        try:
            entry, previous = chat.revise(
                question, answer, keys, raw=raw, secondary=secondary,
            )
        except (RuntimeError, OSError, ValueError) as exc:     # noqa: BLE001
            print(f"[记忆] 改不进去:{exc}\n")
            return
        if previous is None:
            print(f"[记忆] 没找到同题旧条目,已新建 数据包 memory · 条目 {entry['id']}。\n")
        else:
            print(f"[记忆] 已修正 数据包 memory · 条目 {entry['id']};"
                  "旧答案留在该条目的 revisions 里。\n")
        return

    pair = lessons.parse_pair(raw)
    if pair is not None:
        question, answer = pair
        keys = chat.decomposer.decompose(question).keywords
        secondary = lessons.secondary_keywords(question, keys, chat.decomposer)
        print("[记忆] 要写入的题:" + question)
        print("[记忆] 答案:" + answer)
        print("[记忆] 拆出的关键词:"
              + ("、".join(keys) or "(没有非常用词:以后只能靠原题命中)"))
        if secondary:
            print("[记忆] 次要关键词(低权重,用来区分同主题):" + "、".join(secondary))
        if not lessons.is_yes(_ask_yes_no("是否写入记忆[y/n]:")):
            print("[记忆] 好,不写。\n")
            return
        try:
            entry, created = chat.teach(
                question, keys, raw=raw, secondary=secondary, answer=answer,
            )
        except (RuntimeError, OSError, ValueError) as exc:     # noqa: BLE001
            print(f"[记忆] 写不进去:{exc}\n")
            return
        hit_hint = keys[0] if keys else question[:12]
        verb = "已写入" if created else "已更新(同一道题不重复写;答案有变会记修正)"
        print(f"[记忆] {verb} 数据包 memory · 条目 {entry['id']};"
              f"下次问「{hit_hint}」就能命中。\n")
        return

    knowledge = lessons.parse_marker(raw)
    if knowledge is None:
        print("用法:以「#」开头教一道题（问题~答案），或一句知识：")
        print("  # 量子退相干是什么~量子系统的相干性因与环境纠缠而衰减的过程")
        print("  # 量子退相干是量子系统的相干性因与环境的纠缠而衰减的过程")
        print("修正同一道题用「#!问题~新答案」。\n")
        return

    keys = chat.decomposer.decompose(knowledge).keywords
    secondary = lessons.secondary_keywords(knowledge, keys, chat.decomposer)
    print("[记忆] 要写入的内容:" + knowledge)
    print("[记忆] 拆出的关键词:" + ("、".join(keys) or "(没有非常用词:以后只能靠原题命中)"))
    if secondary:
        print("[记忆] 次要关键词(低权重,用来区分同主题):" + "、".join(secondary))
    if not lessons.is_yes(_ask_yes_no("是否写入记忆[y/n]:")):
        print("[记忆] 好,不写。\n")
        return

    try:
        entry, created = chat.teach(knowledge, keys, raw=raw, secondary=secondary)
    except (RuntimeError, OSError, ValueError) as exc:         # noqa: BLE001
        print(f"[记忆] 写不进去:{exc}\n")
        return
    hit_hint = keys[0] if keys else knowledge[:12]
    verb = "已写入" if created else "已更新(同一句知识不重复写)"
    print(f"[记忆] {verb} 数据包 memory · 条目 {entry['id']};"
          f"下次问「{hit_hint}」就能命中。\n")


def _interactive(chat: GoChat, trace: bool, detail: bool) -> int:
    print(_banner(chat, trace, detail))
    while True:
        try:
            raw = input("你 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见。")
            return 0
        if not raw:
            continue
        lowered = raw.lower()

        if lowered in ("exit", "quit", ":q", ":quit"):
            print("再见。")
            return 0
        # `#?` 看教过的知识、`#-` 忘掉一条,都排在 `#` 教知识之前
        view_query = lessons.parse_view(raw)
        if view_query is not None:
            _print_memory(chat, view_query)
            continue
        forget_query = lessons.parse_forget(raw)
        if forget_query is not None:
            _forget(chat, raw)
            continue
        if raw.startswith("#"):
            _teach(chat, raw)
            continue
        if lowered in ("help", ":help", ":h"):
            print(__doc__)
            continue
        if lowered in (":memory", ":taught", ":mem"):
            _print_memory(chat)
            continue
        if lowered == ":stat":
            _print_stat(chat)
            continue
        if lowered == ":missed" or lowered.startswith(":missed "):
            argument = raw[len(":missed"):].strip().lower()
            if argument in ("clear", "reset", "清空"):
                count = len(learning.read_missed(0))
                if not count:
                    print("还没有漏问记录,不用清。\n")
                elif lessons.is_yes(_ask_yes_no(f"清空 {count} 条漏问记录?[y/n]:")):
                    _clear_missed()
                else:
                    print("好,不清。\n")
            else:
                try:
                    limit = int(argument) if argument else 20
                except ValueError:
                    limit = 20
                _print_missed(limit)
            continue
        if raw in (":packs", ":experts"):
            _print_packs()
            continue
        if lowered in (":docs", ":doc"):
            _print_docs(chat)
            continue
        if lowered.startswith(":docs ") or lowered.startswith(":doc "):
            # `:docs 手册` -> 直接看这份文档的目录(等价于只发一个 `@手册`)
            argument = raw[len(":docs"):].strip()
            try:
                print(chat.ask("@" + argument, plain=not trace, trace=trace).summary)
            except Exception as exc:                          # noqa: BLE001
                print(f"[文档] 这一轮没能完成:{type(exc).__name__}: {exc}")
            print()
            continue
        if raw in (":ctx", ":context"):
            _print_context(chat)
            continue
        if raw == ":forget":
            chat.forget()
            print("忘了。接下来的问题从零开始。\n")
            continue
        if lowered == ":trust" or lowered.startswith(":trust "):
            _set_trust(chat, raw[len(":trust"):].strip())
            continue
        if lowered == ":priority" or lowered.startswith(":priority "):
            _set_priority(chat, raw[len(":priority"):].strip())
            continue
        if lowered == ":max" or lowered.startswith(":max "):
            arg = raw[len(":max"):].strip()
            if arg:
                try:
                    requested = int(arg)
                except ValueError:
                    print(f"「{arg}」不是数字;当前 max_packs = {chat.max_packs},"
                          "改法: :max 2\n")
                    continue
                value = chat.set_max_packs(requested)
                note = ""
                if value != requested:
                    note = (f"(你给的是 {requested},超出 1–{settings.MAX_PACKS_LIMIT},"
                            f"已收敛到 {value})")
                print(f"max_packs = {value}(一次最多查 {value} 个包;"
                      f"上限只按分数截断,不降低命中阈值){note}\n")
            else:
                print(f"max_packs = {chat.max_packs}"
                      f"(1–{settings.MAX_PACKS_LIMIT};改法: :max 2 或 --max-packs 2)\n")
            continue
        if lowered == ":rehit" or lowered.startswith(":rehit "):
            arg = raw[len(":rehit"):].strip()
            if arg:
                try:
                    requested = int(arg)
                except ValueError:
                    print(f"「{arg}」不是数字;当前 rehit_rounds = {chat.rehit_rounds},"
                          "改法: :rehit 0\n")
                    continue
                value = chat.set_rehit_rounds(requested)
                note = ""
                if value != requested:
                    note = (f"(你给的是 {requested},超出 "
                            f"0–{settings.MAX_REHIT_ROUNDS_LIMIT},已收敛到 {value})")
                print(f"rehit_rounds = {value}"
                      f"(再命中轮数,0 = 关闭;上限 {settings.MAX_REHIT_ROUNDS_LIMIT}){note}\n")
            else:
                print(f"rehit_rounds = {chat.rehit_rounds}"
                      f"(0–{settings.MAX_REHIT_ROUNDS_LIMIT};改法: :rehit 0 或 --rehit 0)\n")
            continue
        if lowered == ":trace" or lowered.startswith(":trace "):
            arg = raw[len(":trace"):].strip().lower()
            trace = (arg != "off") if arg else (not trace)
            print(f"trace = {'on' if trace else 'off'}\n")
            continue
        if lowered == ":detail" or lowered.startswith(":detail "):
            arg = raw[len(":detail"):].strip().lower()
            detail = (arg != "off") if arg else (not detail)
            print(f"detail = {'on' if detail else 'off'}\n")
            continue
        if lowered == ":eval" or lowered.startswith(":eval "):
            _print_eval(chat, raw[len(":eval"):].strip() or None)
            continue
        if lowered.startswith(":decompose "):
            _print_decomposition(chat, raw[len(":decompose "):].strip())
            continue
        if lowered.startswith(":route "):
            _print_routing(chat, raw[len(":route "):].strip())
            continue
        if lowered.startswith(":guard "):
            _print_guard(chat, raw[len(":guard "):].strip())
            continue
        if lowered in (":decompose", ":route", ":guard"):
            print(f"用法: {lowered} <一句话>   (例如 {lowered} 向量场是什么)\n")
            continue
        if raw.startswith(":"):
            # 不认识的命令不要掉进"提问"分支:那会把 `:foo` 当成知识问题、
            # 记进漏问队列,还污染上下文。
            print(f"不认识这个命令「{raw.split()[0]}」")
            print("输 help 看命令清单(:packs :docs :ctx :stat :trust :priority "
                  ":missed :eval :trace :detail)。\n")
            continue

        _answer(chat, raw, trace, detail, _ask_yes_no)


def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="GoChat",
        description=f"{persona.NAME}的终端入口:分解 → 查数据包 → 引用作答(纯规则,不接模型)",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("query", nargs="*", help="单次提问(省略则进入对话模式)")
    p.add_argument("--trace", action="store_true", help="显示候选与查找明细")
    p.add_argument("--brief", action="store_true", help="查到多个数据包时只看关键句,不展开完整答案")
    p.add_argument("--decompose", metavar="句子", default=None, help="只看分解结果")
    p.add_argument("--route", metavar="句子", default=None, help="只看分解 + 查找")
    p.add_argument("--guard", metavar="文本", default=None, help="只看安全判定")
    p.add_argument("--packs", dest="packs", action="store_true", help="列出小析现在装着哪些数据包")
    p.add_argument("--docs", dest="docs", action="store_true",
                   help="列出 docs/ 里有哪些文档(文档模式:问题@文档名)")
    p.add_argument("--missed", action="store_true", help="看最近没接住的问题(本地学习队列)")
    p.add_argument("--eval", dest="eval", action="store_true",
                   help="跑检索评测集(experts/evaluation/cases.jsonl)")
    p.add_argument("--cases", default=None, metavar="路径",
                   help="配合 --eval 指定自定义 cases.jsonl")
    p.add_argument("--experts", dest="packs", action="store_true",
                   help="旧名字,等同 --packs(保留兼容)")
    p.add_argument("--max-packs", type=int, default=None, metavar="N",
                   help=f"这一次最多查几个数据包(1–{settings.MAX_PACKS_LIMIT};省略=用 config.json)")
    p.add_argument("--rehit", type=int, default=None, metavar="N",
                   help=f"这一次的再命中轮数(0–{settings.MAX_REHIT_ROUNDS_LIMIT},默认取 config.json)")
    p.add_argument("--no-context", action="store_true",
                   help="这一次不启用上下文记忆(等价于 config.json 里 context.enabled=false)")
    p.add_argument("--subprocess", action="store_true",
                   help="这一次改回子进程调用核心(隔离优先;默认是单进程 + 流水线缓存)")
    return p


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    _prepare_stdin()
    parser = build_argparser()
    args = parser.parse_args(argv)
    # 这些开关各代表一种"这一次只做这件事":同时给两个时以前会静默只做第一个,
    # `--route X --guard Y` 甚至会让人以为做了安全判定。
    modes = [
        name for name, active in (
            ("--packs", args.packs),
            ("--docs", args.docs),
            ("--missed", args.missed),
            ("--eval", args.eval),
            ("--decompose", args.decompose is not None),
            ("--route", args.route is not None),
            ("--guard", args.guard is not None),
        ) if active
    ]
    if len(modes) > 1:
        parser.error("这些开关是互斥的,一次只能用一个:" + " ".join(modes))
    if args.cases and not args.eval:
        parser.error("--cases 只在 --eval 下有意义")
    if args.query and any((
        args.decompose is not None, args.route is not None, args.guard is not None,
    )):
        parser.error("--decompose / --route / --guard 不和提问一起用")
    if DISCOVERY_ERRORS:
        print("[注意] 有数据包读取失败,已跳过(框架继续运行):")
        for problem in DISCOVERY_ERRORS:
            print("  · " + problem)
        print()
    config = settings.load()
    for note in config.notes:
        print("[配置] " + note)

    # 命令行优先于配置文件:--max-packs / --rehit / --no-context 只影响这一次运行。
    # 超范围的值以前是静默收敛的(config/env 那条路会提示,命令行不提示)——
    # 这里补上同一句说明,别让人以为 99 生效了。
    max_packs = config.max_packs
    if args.max_packs is not None:
        max_packs = settings.clamp_max_packs(args.max_packs)
        if max_packs != args.max_packs:
            print(f"[配置] --max-packs {args.max_packs} 超出 1–{settings.MAX_PACKS_LIMIT},"
                  f"已按 {max_packs} 处理")
    rehit = config.rehit_rounds
    if args.rehit is not None:
        rehit = settings.clamp_rehit_rounds(args.rehit)
        if rehit != args.rehit:
            print(f"[配置] --rehit {args.rehit} 超出 0–{settings.MAX_REHIT_ROUNDS_LIMIT},"
                  f"已按 {rehit} 处理")
    chat = GoChat(max_packs=max_packs,
                  rehit_rounds=rehit,
                  context=False if args.no_context else None,
                  config=config,
                  engine="subprocess" if args.subprocess else "inprocess")
    detail = not args.brief

    if args.packs:
        _print_packs()
        return 0
    if args.docs:
        _print_docs(chat)
        return 0
    if args.missed:
        _print_missed()
        return 0
    if args.eval:
        return _print_eval(chat, args.cases)
    if args.query:
        single = " ".join(args.query).strip()
        if not single:
            # `python xiaoxi.py ""` 以前会被当成一次"没接住"的提问:
            # 打印"你想问的那个概念是什么",还往漏问队列里写一条空记录。
            print('用法:python xiaoxi.py "你的问题"   (不带参数则进入对话模式)')
            print("      python xiaoxi.py --help  看全部命令")
            return 2
        if single.lower() == "help":
            print(__doc__)
            return 0
        if lessons.parse_view(single) is not None:
            _print_memory(chat, lessons.parse_view(single) or "")
            return 0
        if lessons.parse_forget(single) is not None:
            _forget(chat, single)
            return 0
        if single.startswith("#"):
            _teach(chat, single)
            return 0
    if args.decompose is not None:
        _print_decomposition(chat, args.decompose)
        return 0
    if args.route is not None:
        _print_routing(chat, args.route)
        return 0
    if args.guard is not None:
        # 拒答 = 1(与 ask.py / core run.py 同一契约),脚本才判得出"被拒了"。
        return 1 if _print_guard(chat, args.guard) == "refuse" else 0

    if args.query:
        _answer(chat, " ".join(args.query), args.trace, detail,
                _ask_yes_no if _stdin_is_interactive() else None)
        return 0

    return _interactive(chat, args.trace, detail)


if __name__ == "__main__":
    raise SystemExit(main())
