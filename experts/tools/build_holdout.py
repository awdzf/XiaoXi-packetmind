"""造一套**没被用过**的题：held-out 集（cases_holdout.jsonl）。

为什么需要它
------------
`cases.jsonl` 已经被用来定阈值（0.32 就是在它上面扫出来的），所以它不再是一把独立的尺子 ——
在上面拿 100 分，只能证明"对着答案调过"。这里重新造一套：

· 正例只取**旧评测一条都没碰过的条目**（161 条里只碰过 16 条）；
· 问法用**不在任何条目的 aliases 里**的句式（否则等于把答案抄成题目）；
· 每个拟造的句子都先核实：它没有原样出现在任何条目的头部字段里 —— 出现就换/丢；
· 负例全部手写，并逐条核实特征词**一次都没在条目头部出现过**。

纪律
----
这套题造完就**冻结**：跑出什么数就是什么数，不许拿它再调阈值。
要再调，请另造第三套 —— 否则它也会变成训练集。

用法
----
    python tools/build_holdout.py            # 生成（会打印每条的正/负与核实结果）
    python eval.py --cases evaluation/cases_holdout.jsonl
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKS = ROOT / "packs"
CASES = ROOT / "evaluation" / "cases.jsonl"
OUT = ROOT / "evaluation" / "cases_holdout.jsonl"

#: 问法模板。刻意选口语化、且**不与数据包 aliases 同形**的说法。
TEMPLATES = (
    "解释一下{t}",
    "{t}怎么理解",
    "{t}到底指什么",
    "能讲讲{t}吗",
    "说说{t}",
    "{t}要怎么理解",
)

#: 第二套（v2）的手写近域负例。与 v1 不重叠 —— v1 已经被看过一眼，作废了。
NEAR_MISS_2 = [
    ("什么是卡尔曼滤波", "卡尔曼", "math", "共享数学范畴，包里没有这一条"),
    ("C 语言的指针和数组有什么区别", "指针", "java", "共享编程范畴，但包里是 Java"),
    ("虚拟内存是怎么工作的", "虚拟内存", "web_dev", "共享系统范畴，包里没有这一条"),
    ("Linux 的定时任务怎么配", "定时任务", "web_dev", "共享运维范畴，包里没有这一条"),
    ("什么是 CAP 定理", "cap", "web_dev", "共享分布式范畴，包里没有这一条"),
    ("消息队列怎么保证不丢消息", "消息队列", "web_dev", "共享后端范畴，包里没有这一条"),
    ("什么是 JWT", "jwt", "web_dev", "共享鉴权范畴，包里没有这一条"),
    ("单元测试的覆盖率怎么算", "覆盖率", "web_dev", "共享测试范畴，包里没有这一条"),
    ("什么是死锁，怎么避免", "死锁", "java", "共享并发范畴，包里没有这一条"),
    ("OSI 七层模型分别是哪七层", "osi", "web_dev", "共享网络范畴，包里没有这一条"),
    ("Vue 和 React 的响应式有什么区别", "vue", "web_dev", "共享前端范畴，包里没有这一条"),
    ("什么是 WebSocket 长连接", "websocket", "web_dev", "共享网络范畴，包里没有这一条"),
    ("哥廷根学派对数学有什么影响", "哥廷根", "math", "只共享数学范畴"),
    ("什么是蒙特卡洛方法", "蒙特卡洛", "math", "共享概率范畴，包里没有这一条"),
    ("费曼图是用来算什么", "费曼", "physics", "共享物理范畴，包里没有这一条"),
    ("什么是拉马努金和", "拉马努金", "math", "只共享数学范畴"),
]

#: 第二套整句越界
OUT_OF_DOMAIN_2 = [
    "附近有什么好吃的",
    "这个周末适合去哪里玩",
    "帮我算一下房贷月供",
    "怎么才能早睡",
    "哪款电动车续航最长",
    "明天股市会涨吗",
]

#: 第二套陷阱
TRAPS_2 = [
    ("这个东西到底靠谱吗", "没有指代对象，无法核对"),
    ("为什么", "没有命题，问不出可核对的东西"),
    ("它和上一个有什么关系", "没有上文，也没有两个对象"),
    ("请总结一下今天发生了什么", "没有今日信息，也不联网"),
    ("把刚才那条再念一遍", "没有上文可念"),
]

#: 从一个标准问法里剥出"概念本体"（"向量场是什么?" -> "向量场"）。
SUFFIXES = (
    "是什么?", "是什么", "是什么意思?", "是什么意思", "指的是什么?", "指的是什么",
    "的含义是什么?", "的含义", "怎么理解?", "怎么理解", "有哪些?", "有什么用?", "有什么用",
)

#: 手写近域负例：(问题, 特征词, 共享词面的包, 为什么该弃权)
NEAR_MISS = [
    ("什么是柯西不等式", "柯西", "math", "共享数学范畴，包里没有这一条"),
    ("洛必达法则什么时候能用", "洛必达", "math", "共享「极限/求导」范畴，包里没有这一条"),
    ("什么是线性规划的单纯形法", "单纯形", "math", "共享「线性」范畴，包里没有这一条"),
    ("泊松分布和指数分布的关系", "泊松", "math", "共享概率范畴，包里没有这一条"),
    ("什么是拉格朗日乘子法", "拉格朗日", "math", "共享数学范畴，包里没有这一条"),
    ("霍尔效应是什么", "霍尔", "physics", "共享物理范畴，包里没有这一条"),
    ("什么是超导现象", "超导", "physics", "共享物理范畴，包里没有这一条"),
    ("麦克斯韦方程组讲了什么", "麦克斯韦", "physics", "共享电磁范畴，包里没有这一条"),
    ("什么是宇称不守恒", "宇称", "physics", "共享物理范畴，包里没有这一条"),
    ("Redis 的持久化有哪几种", "redis", "web_dev", "共享后端范畴，包里没有这一条"),
    ("Nginx 怎么做反向代理", "nginx", "web_dev", "共享部署范畴，包里没有这一条"),
    ("SQL 索引为什么会失效", "索引", "web_dev", "共享数据范畴，包里没有这一条"),
    ("什么是 B+ 树和哈希索引的区别", "b+", "web_dev", "共享数据结构范畴，包里没有这一条"),
    ("Maven 和 Gradle 有什么区别", "maven", "java", "共享构建工具范畴，包里没有这一条"),
    ("Java 的反射是怎么实现的", "反射", "java", "共享语言范畴，包里没有这一条"),
    ("什么是设计模式里的观察者模式", "观察者", "java", "共享编程范畴，包里没有这一条"),
    ("怎么判断自己是不是职业倦怠", "职业倦怠", "emotion", "共享情绪范畴，包里没有这一条"),
    ("拖延症的心理机制是什么", "拖延", "emotion", "共享情绪范畴，包里没有这一条"),
    ("什么是依恋类型", "依恋", "emotion", "共享关系范畴，包里没有这一条"),
    ("罗尔斯的无知之幕是什么意思", "无知之幕", "ethics", "共享「罗尔斯/正义」范畴，包里没有这一条"),
    ("动物有没有道德地位", "动物", "ethics", "共享伦理范畴，包里没有这一条"),
    ("什么是程序正义", "程序正义", "ethics", "共享伦理范畴，包里没有这一条"),
]

#: 手写整句越界（与包内容无关）。
OUT_OF_DOMAIN = [
    "明天去杭州的高铁还有票吗",
    "帮我写一封辞职信",
    "今年冬天会特别冷吗",
    "哪款手机拍照最好",
    "如何申请公积金贷款",
    "推荐一部科幻电影",
]

#: 陷阱：正确答案是"弃权"或"按人格自述答"，不是拿某条知识顶上去。
TRAPS = [
    ("它是什么", "指代不明：没有上下文时不该猜一个条目"),
    ("请把上面那段翻译成英文", "没有上文，也没有翻译能力"),
    ("爱因斯坦因为发现引力波获得了诺贝尔奖吗", "前提为假：他因光电效应获奖"),
    ("量子力学和相对论哪个更重要", "评价性问题，包里没有比较依据"),
    ("你说的那个东西到底对不对", "没有指代对象，无法核对"),
]

FULLWIDTH = {chr(c): chr(c - 0x20) if False else chr(c + 0xFEE0) for c in range(0x21, 0x7F)}
TRAD = {"电": "電", "车": "車", "难": "題", "题": "題", "场": "場", "么": "麼",
        "为": "為", "与": "與", "这": "這", "个": "個", "学": "學", "术": "術"}


def _load_packs() -> list[dict]:
    cards: list[dict] = []
    for path in sorted(PACKS.glob("*/pack.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for entry in data.get("entries", []):
            if isinstance(entry, dict) and entry.get("question") and entry.get("id"):
                cards.append({
                    "folder": data["expert"]["id"],
                    "id": entry["id"],
                    "question": str(entry["question"]),
                    "head": _head(entry),
                })
    return cards


def _head(entry: dict) -> str:
    parts = [str(entry.get("question") or ""), str(entry.get("title") or "")]
    for key in ("aliases", "keywords", "secondary_keywords"):
        parts.extend(str(item) for item in entry.get(key) or ())
    return " ".join(parts)


def bare_term(question: str) -> str | None:
    """从标准问法里剥出概念本体；剥不出（或太短/太长）就返回 None。"""
    text = question.strip()
    for suffix in SUFFIXES:
        if text.endswith(suffix):
            term = text[: -len(suffix)].strip(" ，,、")
            if 2 <= len(term) <= 8:
                return term
            return None
    return None


def _used_pairs() -> set[tuple[str, str]]:
    used: set[tuple[str, str]] = set()
    for line in CASES.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        raw = json.loads(line)
        if not raw.get("must_abstain"):
            used.add((str(raw.get("folder") or ""), str(raw.get("entry") or "")))
    return used


def _typo_variants(term: str) -> list[tuple[str, str]]:
    """给一个词造"写法不一样"的问法（全角 / 繁体 / 换序 / 词中间空格）。"""
    out: list[tuple[str, str]] = []
    if term.isascii() and len(term) >= 3:
        wide = "".join(FULLWIDTH.get(ch, ch) for ch in term)
        if wide != term:
            out.append(("typo-fullwidth", wide))
        swapped = term[:1] + term[2:3] + term[1:2] + term[3:]
        if swapped != term:
            out.append(("typo-transpose", swapped))
        return out
    for ch, trad in TRAD.items():
        if ch in term:
            out.append(("typo-traditional", term.replace(ch, trad, 1)))
            break
    spaced = " ".join(term)
    if len(term) >= 3:
        out.append(("typo-spaced", spaced))
    if len(term) >= 3 and len(set(term)) >= 2:
        swapped = term[1] + term[0] + term[2:]
        if swapped != term:
            out.append(("typo-swap", swapped))
    return out


def main() -> None:
    variant = 1
    for index, arg in enumerate(sys.argv[1:]):
        if arg == "--variant" and index + 2 <= len(sys.argv[1:]):
            variant = int(sys.argv[1:][index + 1])
    near_list = NEAR_MISS if variant == 1 else NEAR_MISS_2
    ood_list = OUT_OF_DOMAIN if variant == 1 else OUT_OF_DOMAIN_2
    trap_list = TRAPS if variant == 1 else TRAPS_2
    out_path = OUT if variant == 1 else OUT.with_name("cases_holdout2.jsonl")

    cards = _load_packs()
    used = _used_pairs()
    # 第二套要把第一套用过的条目也排掉：同一批条目上再测一遍，等于没换题。
    for extra in (ROOT / "evaluation" / "cases_holdout.jsonl",):
        if extra.exists():
            for line in extra.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                raw = json.loads(line)
                if not raw.get("must_abstain"):
                    used.add((str(raw.get("folder") or ""), str(raw.get("entry") or "")))
    all_heads = "\n".join(card["head"] for card in cards).lower()

    def leaked(text: str) -> bool:
        """这句话是不是已经原样出现在某个条目的头部字段里（那就是泄漏）。"""
        return text.strip().lower() in all_heads

    rows: list[dict] = []
    skipped: list[str] = []
    free = [c for c in cards if (c["folder"], c["id"]) not in used]

    # ---- 正例：没碰过的条目 + 不在 aliases 里的问法 ----
    picked = 0
    for index, card in enumerate(free):
        if picked >= 24:
            break
        if index % 6 != (0 if variant == 1 else 3):   # 跨包、跨条目地取；v2 用另一个相位
            continue
        term = bare_term(card["question"])
        if term is None:
            continue
        if term.lower() not in card["head"].lower():
            skipped.append(f"{card['folder']}/{card['id']}：本体「{term}」不在头部")
            continue
        template = TEMPLATES[(index // 6 + variant - 1) % len(TEMPLATES)]
        query = template.format(t=term)
        if leaked(query):
            skipped.append(f"{card['folder']}/{card['id']}：问法「{query}」会在头部出现")
            continue
        rows.append({"id": f"h-direct-{picked + 1:02d}", "question": query,
                     "folder": card["folder"], "entry": card["id"],
                     "category": "holdout-paraphrase"})
        picked += 1

    # ---- 正例：写法不一样（错字/全角/繁体/换序/空格）----
    typo_picked = 0
    for index, card in enumerate(free):
        if typo_picked >= 10:
            break
        if index % 9 != (0 if variant == 1 else 4):
            continue
        term = bare_term(card["question"])
        if term is None:
            continue
        for kind, variant in _typo_variants(term):
            query = f"{variant}是什么"
            if leaked(query) or variant.lower() in all_heads:
                continue
            rows.append({"id": f"h-typo-{typo_picked + 1:02d}", "question": query,
                         "folder": card["folder"], "entry": card["id"],
                         "category": kind})
            typo_picked += 1
            break

    # ---- 负例：近域（手写，逐条核实特征词不在头部）----
    near = 0
    for question, core, shares, why in near_list:
        if core.lower() in all_heads:
            skipped.append(f"负例「{question}」：特征词「{core}」其实在条目里")
            continue
        near += 1
        rows.append({"id": f"h-near-{near:02d}", "question": question,
                     "category": "near-miss", "must_abstain": True,
                     "shares_vocab_with": shares, "why_abstain": why})

    # ---- 负例：整句越界 ----
    ood = 0
    for question in ood_list:
        ood += 1
        rows.append({"id": f"h-ood-{ood:02d}", "question": question,
                     "category": "negative", "must_abstain": True})

    # ---- 陷阱：正确答案是弃权/自述，不是拿条目顶上 ----
    trap = 0
    for question, why in trap_list:
        trap += 1
        rows.append({"id": f"h-trap-{trap:02d}", "question": question,
                     "category": "trap", "must_abstain": True, "why_abstain": why})

    out_path.write_text(
        "# held-out 集：造完之后冻结，不许拿它调阈值（要调请另造第三套）\n"
        + "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )

    kinds: dict[str, int] = {}
    for row in rows:
        kinds[row["category"]] = kinds.get(row["category"], 0) + 1
    print(f"写入 {out_path.name}：{len(rows)} 条  {kinds}")
    if skipped:
        print(f"跳过 {len(skipped)} 条（核实没过）：")
        for item in skipped[:12]:
            print("  · " + item)


if __name__ == "__main__":
    main()
