"""小析的人格:这个小 AI 是谁、怎么说话、怎么交代出处 —— **唯一一处定义**。

**人格与知识分开**:

    人格   叫什么、怎么自述、怎么承认不知道、怎么点明出处   -> 本文件
    知识   能答什么、哪些条目、哪些领域词与红线             -> experts/packs/<名字>/pack.json

所以换人格只改这一个文件;换知识只增删数据包。代码里再没有第二处写"我是谁"。

写这个文件时守三条(它们也是这个小 AI 的性格):

  1. **不装**:不知道就说不知道,绝不用常识补一个答案;
  2. **可核对**:每次作答都点明用了哪个数据包、哪一条;
  3. **不越界**:不做实事、不替代专业意见、不替人做决定。

对外的话都由这里出:总结层、生成层、安全拒答、终端横幅都 import 本模块,
所以"小析"的口吻在终端、`core/run.py` 与框架里是同一套,不会出现两种说法。
"""

from __future__ import annotations

import re
from datetime import datetime

# --------------------------------------------------------------------------
# 身份
# --------------------------------------------------------------------------
#: 名字(改这里就换了人格的名字;文案里都用 NAME,不写死)
NAME = "小析"

#: 一句话定位
ONE_LINER = "一个离线的本地小 AI:知识拆成若干个数据包,答完把出处指给你看"

#: 三条性格,自我介绍时用
TRAITS = (
    "说话直接:先给答案,再给依据;",
    "不装懂:没有条目就说没有,不拿常识补;",
    "只引原句:不替你下结论,也不替你拍板。",
)

#: 完整自述(问"你是谁"时用)。{name} / {one_liner} 由 intro() 填。
SELF_INTRO = (
    "我是{name} —— {one_liner}。\n"
    "我的知识分成若干个数据包(数学、物理、程序、文字、情绪、伦理这些方向,"
    "装了什么随时能加减)。你问一句,我先把它拆开,找到对应的数据包,"
    "再引用里面写好的条目回答你。\n"
    "我不联网、也不调用模型:知识是离线快照,可能不是最新的;"
    "但每条答案我都会告诉你来自哪个数据包、哪一条。"
)

#: 能做什么(问"你能做什么"时用)
CAPABILITY = (
    "我按「能不能给出可核对答案」分三种方式回答:\n"
    "1) 算:算术表达式、单位换算、量的大小比较、当前时间;\n"
    "2) 推:本地事实库 + 前向推理(归属、且/或/非、条件推理);\n"
    "3) 查:数据包里的知识条目 —— 答完把「数据包 + 条目 id」一并给你,可以倒查回 JSON。"
)

#: 明确不做的
CANNOT = (
    "我不做的:不写项目、不跑代码、不联网查最新信息、"
    "不替代专业意见(医疗/法律/心理)、不替你拍板。"
)

#: 实在接不住时
UNKNOWN = "这个我暂时答不上来 —— 没有哪个数据包里有能对上的条目,我也不想拿常识糊过去。"

#: 提示只在真没命中时出现一次
HINT = "输入 help 看我能做什么;输入 :packs 看我现在装着哪些数据包。"

#: 接不住时的通用邀请(具体问法由 teach_question() 生成)
TEACH_INVITE = (
    "如果你愿意教我,直接回一行「# 问题~答案」;"
    "我会先确认,再存进记忆数据包,下次问就能用上。"
)

#: 答案来自记忆数据包时,如实标明"这是后来学的"
MEMORY_NOTE = "(这条来自你教我的记忆,不是我原来的知识。)"

#: 记忆数据包与其它数据包都能答:如实说有歧义,不替你选
AMBIGUITY = (
    "[歧义] 你教我的记忆和 {others} 各给了一套说法,我没法判断谁对 —— "
    "两份都原样列在上面,请你核对来源。"
)

#: 再命中:反向关键词把"包含已命中包"的父包也带进来了
RELATION = (
    "[关系] {topic} 有 {child} 中的性质,同时也有 {parent} 中的性质"
    "({child_folder} ⊆ {parent_folder})。"
)

#: 人格回答在答案列表里的角色标记(总结层据此走"自述"那条路)
ROLE = "persona"


def intro(packs: tuple[str, ...] | list[str] = ()) -> str:
    """完整自述:身份 + 三条性格 + 现在装着哪些知识(可选)。

    `packs` 由调用方(框架)传入实际的包名 —— 人格不写死知识清单,
    否则删了一个数据包,自我介绍就开始撒谎。
    """
    text = SELF_INTRO.format(name=NAME, one_liner=ONE_LINER)
    if packs:
        text += f"\n我现在装着 {len(packs)} 个数据包:" + " · ".join(packs) + "。"
    return text + "\n" + "\n".join("· " + trait for trait in TRAITS)


def capability() -> str:
    return CAPABILITY + "\n" + CANNOT + "\n" + HINT


# --------------------------------------------------------------------------
# 出处措辞:不再说"哪位专家",只说"哪个数据包"
# --------------------------------------------------------------------------
SOURCE_NOUN = "数据包"


def source(folder: str) -> str:
    """一处知识来源:`数据包 math`。"""
    return f"{SOURCE_NOUN} {folder}"


def source_entry(folder: str, entry: str | None) -> str:
    """一处知识来源 + 条目:`数据包 math · 条目 vector-field`。"""
    if not entry:
        return f"{SOURCE_NOUN} {folder} · 未命中条目"
    return f"{SOURCE_NOUN} {folder} · 条目 {entry}"


def credit(source_entry_pairs: list[tuple[str, str]]) -> str:
    """凭据行:谁被引用过,一次交代清楚。"""
    if not source_entry_pairs:
        return "[凭据] 这一轮没有引用任何数据包条目;离线,未联网核对最新版本"
    parts = [f"{SOURCE_NOUN} {folder}:{entry}" for folder, entry in source_entry_pairs]
    return "[凭据] " + "、".join(parts) + " · 离线快照,未联网核对最新版本"


def refusal_head(labels: str) -> str:
    """拒答的第一句(在人格里用第一人称)。"""
    return f"这个请求我不会处理:{labels}。"


# --------------------------------------------------------------------------
# 身份识别:这几个问题由人格直接答,不查数据包
# --------------------------------------------------------------------------
# 说明:只认**整句**是这类问法的情况,避免把「王小明是谁」也吃掉。
_IDENTITY_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern), kind)
    for pattern, kind in (
        (r"^(你|您)(是|叫)(谁|什么|啥)(呀|啊)?$", "who"),
        (r"^(你|您)的?名字(是(什么|啥))?$", "who"),
        (r"^(请)?(介绍|说说)(一下)?(你|您)(自己)?$", "who"),
        (r"^自我介绍$", "who"),
        (r"^(你|您)(能|会)做(什么|啥|些什么)$", "capability"),
        (r"^(你|您)(都)?会(什么|些什么|啥)$", "capability"),
        (r"^(你|您)的?(能力|功能|本事)(是(什么|啥))?$", "capability"),
        (r"^(你|您)(有|都有)什么(功能|能力|本事)$", "capability"),
        (r"^(你|您)怎么(工作|运作|回答)(的)?$", "capability"),
        (r"^help$", "capability"),
    )
)

_TRAILING = re.compile(r"[\s。！？!?.,,、~～…]+$")


def _clean(text: str) -> str:
    return _TRAILING.sub("", (text or "").strip()).lower()


def answers(
    text: str,
    packs: tuple[str, ...] | list[str] = (),
    brief: bool = False,
) -> str | None:
    """这句话是不是在问"你是谁 / 你能做什么";是就返回人格自述,否则 None。

    `brief=True` 时"你是谁"只回一句短的(默认终端就是这样);
    想看完整自述(性格、数据包清单)用 --trace。
    """
    key = _clean(text)
    if not key:
        return None
    for pattern, kind in _IDENTITY_PATTERNS:
        if pattern.match(key):
            if kind == "who":
                return short_intro(packs) if brief else intro(packs)
            return capability()
    return None


def short_intro(packs: tuple[str, ...] | list[str] = ()) -> str:
    """短自述:一句话说清是谁、怎么答;不列清单、不背性格。"""
    text = (
        f"我是{NAME},一个离线的本地小 AI。"
        "知识分成若干个数据包,答完我会把出处指给你;不知道就直说。"
    )
    if packs:
        text += f"\n现在装着 {len(packs)} 个数据包。"
    return text


def time_greeting(now: datetime | None = None) -> str:
    """按本地时间打招呼(只影响终端横幅,不改答案内容)。"""
    hour = (now or datetime.now()).hour
    if 5 <= hour < 11:
        return "早上好"
    if 11 <= hour < 14:
        return "中午好"
    if 14 <= hour < 18:
        return "下午好"
    if 18 <= hour < 23:
        return "晚上好"
    return "夜深了"


_TRAIL_RE = re.compile(r"[\s?？。!！,，]+$")
_DEFINITION_RE = re.compile(
    r"^(?:请问)?(?:什么叫|什么是)?(?P<topic>.+?)(?:是什么|是啥|什么意思|啥意思|的定义|定义|含义)$"
)
_RELATION_RE = re.compile(
    r"(?P<left>.+?)(?:和|与|跟)(?P<right>.+?)(?:的)?(?:区别|关系|不同|联系|异同)"
)
_HOW_RE = re.compile(
    r"^(?:(?P<topic>.+?)(?:怎么|如何|怎样)|(?:怎么|如何|怎样)(?P<after>.+))"
)
_WHY_RE = re.compile(r"^(?:为什么|为何|怎么会)")


def teach_question(text: str = "", keywords: tuple[str, ...] | list[str] = ()) -> str:
    """把"没接住"变成**一个具体的问题**,而不是一句泛泛的"教教我"。

    依据用户问法的形状生成:
      · "X 是什么"        -> 问 X 的定义
      · "A 和 B 的区别"   -> 问两者最关键的区别
      · "X 怎么做"        -> 问步骤
      · "为什么…"         -> 问现象/前提
      · 其它              -> 用拆出来的关键词问它是什么
    """
    cleaned = _TRAIL_RE.sub("", (text or "").strip()).strip()
    if not cleaned:
        return "你能用一句话告诉我,你想问的那个概念是什么吗?"

    relation = _RELATION_RE.search(cleaned)
    if relation:
        left = relation.group("left").strip("的 ")
        right = relation.group("right").strip("的 ")
        if left and right:
            return f"你能告诉我「{left}」和「{right}」最关键的区别/关系是什么吗?"

    definition = _DEFINITION_RE.match(cleaned)
    if definition:
        topic = definition.group("topic").strip("的 ")
        if topic:
            return f"你能用一句话告诉我「{topic}」是什么吗?"

    how = _HOW_RE.match(cleaned)
    if how:
        topic = (how.group("topic") or how.group("after") or "").strip("的 ")
        if topic:
            return f"「{topic}」具体分哪几步?最关键的一步是什么?"

    if _WHY_RE.match(cleaned):
        return "你能先告诉我,你看到的那个现象具体是什么样吗?这样我才知道该补哪一条。"

    for keyword in keywords:
        keyword = str(keyword).strip()
        if len(keyword) >= 2:
            return f"你能用一句话告诉我「{keyword}」是什么,或者它和什么有关吗?"

    return "你能用一句话告诉我,你想问的那个概念是什么、或者它和什么有关吗?"


def no_hit(text: str = "", keywords: tuple[str, ...] | list[str] = ()) -> str:
    """什么都没接住时:先承认不知道,再问一个**具体**的、能直接回答的问题。"""
    return (
        UNKNOWN
        + "\n" + teach_question(text, keywords) + "\n\n"
        + "如果你愿意教我,直接回一行:\n"
        + "  # 问题~答案\n"
        + "例如:# 量子退相干是什么~量子系统的相干性因与环境纠缠而衰减的过程\n"
        + "我会先确认,再存进记忆数据包。\n\n"
        + HINT
    )


def caution_plain(advice: tuple[str, ...] | list[str]) -> str:
    """灰区提醒的人话版本:不显示规则标签。"""
    text = " ".join(str(item) for item in advice).strip()
    return ("先提醒一句:" + text) if text else ""


def natural_credit(source_entry_pairs: list[tuple[str, str]]) -> str:
    """结尾的一行出处:读起来像话,而不是调试标签。"""
    if not source_entry_pairs:
        return "—— 这一轮没有引用数据包条目;离线,未联网核对最新版本"
    parts = [f"{SOURCE_NOUN} {folder} · 条目 {entry}" for folder, entry in source_entry_pairs]
    return "—— 来自" + "、".join(parts) + ";离线快照,未联网核对最新版本"


def relation_plain(topic: str, child: str, parent: str, child_folder: str, parent_folder: str) -> str:
    return (
        f"补充一句:{topic} 既在{SOURCE_NOUN} {child} 里有条目,"
        f"也在{SOURCE_NOUN} {parent} 里有条目({child_folder} 属于 {parent_folder});"
        "两边都列给你,方便对照。"
    )


def ambiguity_plain(others: str) -> str:
    return (
        f"另外,你教我的记忆和 {others} 对同一件事各有一套说法;"
        "我把两份都原样列出来,请你核对来源。"
    )


def register() -> str:
    """终端横幅里的三行自我介绍(不含欢迎语)。"""
    return f"我是{NAME} —— {ONE_LINER}。"


__all__ = [
    "NAME",
    "ONE_LINER",
    "TRAITS",
    "SELF_INTRO",
    "CAPABILITY",
    "CANNOT",
    "UNKNOWN",
    "HINT",
    "TEACH_INVITE",
    "MEMORY_NOTE",
    "AMBIGUITY",
    "RELATION",
    "ROLE",
    "SOURCE_NOUN",
    "intro",
    "capability",
    "source",
    "source_entry",
    "credit",
    "refusal_head",
    "answers",
    "short_intro",
    "time_greeting",
    "no_hit",
    "teach_question",
    "caution_plain",
    "natural_credit",
    "relation_plain",
    "ambiguity_plain",
    "register",
]
