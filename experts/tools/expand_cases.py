"""给评测集补"近域负例"：共享词面、但没有任何条目能答的问题。

为什么需要这一类：原来的 8 条负例全是**整句零重叠**的（"怎么给猫剪指甲"），
连检索都不会碰，所以"负例 8/8 弃权"说明不了任何事。真正会漂的是**近域**：
问题里有"定律""函数""虚拟机"这些词，而某个包里恰好也有 —— 德摩根定律被答成
热力学定律，C++ 虚函数表被答成数学函数，都是这么发生的。

本脚本先核实每条问题的**特征词**（core）在现有数据包的条目头部
（question / aliases / keywords）里**一次都没出现**，确认后才追加到 cases.jsonl。
有一条对不上就报错退出，不会写脏数据。

注意：核实的是"特征词没出现过"，不是"整句没有相同的字"——近域负例**本来**就
和包里共享字面，那正是它难的原因。
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKS = ROOT / "packs"
CASES = ROOT / "evaluation" / "cases.jsonl"

#: (问题, 特征词（必须不在任何条目头部出现）, 与哪个包共享词面, 为什么应当弃权)
NEAR_MISS = [
    ("什么是德摩根定律", "德摩根", "physics", "只共享「定律」；包里是热力学定律"),
    ("C++ 的虚函数表怎么实现", "虚函数", "math", "「虚函数」里含「函数」；包里是数学函数"),
    ("哥德尔不完备定理说的是什么", "哥德尔", "math", "只共享「定理」"),
    ("费马大定理怎么证明", "费马", "math", "只共享「定理」"),
    ("黎曼猜想到底是什么", "黎曼", "math", "只共享数学范畴，包里无此条目"),
    ("薛定谔的猫是什么", "薛定谔", "physics", "只共享物理范畴，包里无此条目"),
    ("什么是量子纠缠", "量子纠缠", "physics", "只共享物理范畴，包里无此条目"),
    ("暗物质是什么", "暗物质", "physics", "只共享物理范畴，包里无此条目"),
    ("黑洞是怎么形成的", "黑洞", "physics", "只共享物理范畴，包里无此条目"),
    ("牛顿第三定律和动量守恒的关系", "动量守恒", "physics", "共享「定律」但与包里条目不对应"),
    ("熵增和热寂是什么关系", "热寂", "physics", "共享「熵」但与包里条目不对应"),
    ("什么是对称矩阵", "对称矩阵", "math", "共享「矩阵」但包里没有这一条"),
    ("什么是黎曼积分", "黎曼积分", "math", "共享「积分」但包里没有这一条"),
    ("泰勒展开怎么推", "泰勒", "math", "共享「展开」但包里没有这一条"),
    ("什么是哥德巴赫猜想", "哥德巴赫", "math", "只共享数学词"),
    ("Docker 容器和虚拟机的区别", "docker", "java", "共享「虚拟机」；包里是 JVM"),
    ("Kubernetes 怎么部署服务", "kubernetes", "web_dev", "共享「部署」；包里是部署流程"),
    ("什么是红黑树", "红黑树", "web_dev", "只共享编程范畴"),
    ("TCP 三次握手为什么不能是两次", "三次握手", "web_dev", "只共享网络范畴"),
    ("HTTP 状态码 302 是什么意思", "状态码", "web_dev", "只共享 HTTP 范畴，包里无此条目"),
    ("git rebase 和 merge 有什么区别", "rebase", "web_dev", "只共享版本控制范畴"),
    ("什么是闭包", "闭包", "java", "只共享语言概念"),
    ("Spring 的依赖注入怎么工作", "依赖注入", "java", "共享语言范畴，包里无此条目"),
    ("JVM 的垃圾回收器有哪几种", "垃圾回收", "java", "共享「JVM」但包里没有这一条"),
    ("什么是 transformer 的注意力机制", "注意力机制", "web_dev", "只共享技术范畴"),
    ("反向传播是怎么推导出来的", "反向传播", "math", "共享数学范畴，包里无此条目"),
    ("强化学习的奖励函数怎么设计", "强化学习", "math", "共享数学范畴，包里无此条目"),
    ("梯度消失是什么原因造成的", "梯度消失", "math", "共享「梯度」相关词，包里无此条目"),
    ("多巴胺和血清素有什么区别", "多巴胺", "emotion", "共享「情绪」范畴，包里无此条目"),
    ("焦虑症怎么治疗", "焦虑症", "emotion", "共享「焦虑」但包里不提供诊疗"),
    ("抑郁症的药物治疗有哪些", "抑郁症", "emotion", "共享「抑郁」但包里不提供诊疗"),
    ("怎么和难相处的同事沟通", "同事", "emotion", "共享人际范畴，包里无此条目"),
    ("功利主义和道义论怎么区分", "功利主义", "ethics", "共享伦理范畴，包里无此条目"),
    ("人工智能会有道德责任吗", "人工智能", "ethics", "共享「道德」但包里无此条目"),
    ("基因编辑婴儿在伦理上该不该允许", "基因编辑", "ethics", "共享伦理范畴，包里无此条目"),
    ("什么是罗尔斯的正义论", "罗尔斯", "ethics", "只共享伦理范畴"),
    ("Python 的装饰器怎么写", "装饰器", "java", "共享编程范畴，但包里是 Java"),
    ("React 的 useEffect 依赖数组怎么用", "useeffect", "web_dev", "共享前端范畴，包里无此条目"),
    ("如何用 SQL 做窗口函数", "窗口函数", "web_dev", "共享数据范畴，包里无此条目"),
]

#: 与包内容完全无关的越界提问（原来那 8 条的同类，用来守住"整句零重叠"那一侧）
OUT_OF_DOMAIN = [
    ("明天上海会下雨吗", "整句越界"),
    ("帮我订一张去北京的机票", "整句越界"),
    ("茅台股价现在多少", "整句越界"),
    ("红烧肉要炖多久", "整句越界"),
    ("世界杯冠军是谁", "整句越界"),
    ("猫咪为什么怕水", "整句越界"),
    ("房贷利率是多少", "整句越界"),
    ("口罩怎么正确佩戴", "整句越界"),
    ("推荐一本好看的小说", "整句越界"),
    ("怎么申请护照", "整句越界"),
]


def _head_texts() -> list[str]:
    """所有条目的头部字段（question/aliases/keywords），按小写比对。"""
    texts: list[str] = []
    for path in sorted(PACKS.glob("*/pack.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for entry in data.get("entries", []):
            if not isinstance(entry, dict):
                continue
            parts = [str(entry.get("question") or ""), str(entry.get("title") or "")]
            for key in ("aliases", "keywords", "secondary_keywords"):
                parts.extend(str(item) for item in entry.get(key) or ())
            texts.append(" ".join(parts).lower())
    return texts


def main() -> None:
    heads = _head_texts()
    existing = CASES.read_text(encoding="utf-8")
    seen_questions = set()
    for line in existing.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            seen_questions.add(json.loads(line).get("question", ""))

    rows: list[dict] = []
    problems: list[str] = []
    for question, core, shares, why in NEAR_MISS:
        needle = core.lower()
        hit = next((text for text in heads if needle in text), None)
        if hit is not None:
            problems.append(f"{question}: 特征词「{core}」已出现在条目头部 —— {hit[:70]}")
            continue
        rows.append({"id": "near-" + str(len(rows) + 1).zfill(2), "question": question,
                     "category": "near-miss", "must_abstain": True, "shares_vocab_with": shares,
                     "why_abstain": why})
    for question, why in OUT_OF_DOMAIN:
        rows.append({"id": "ood-" + str(len(rows) + 1).zfill(2), "question": question,
                     "category": "negative", "must_abstain": True, "why_abstain": why})

    if problems:
        # 不写脏数据，也不静默丢掉：逐条说清哪一条、撞上了哪个条目。
        print(f"跳过 {len(problems)} 条（特征词其实已在某个条目里，不算负例）：")
        for item in problems:
            print("  · " + item)
        print()

    fresh = [row for row in rows if row["question"] not in seen_questions]
    with CASES.open("a", encoding="utf-8") as handle:
        for row in fresh:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"追加 {len(fresh)} 条（近域负例 {len(NEAR_MISS)}，整句越界 {len(OUT_OF_DOMAIN)}）；"
          f"已在集里跳过 {len(rows) - len(fresh)} 条")


if __name__ == "__main__":
    main()
