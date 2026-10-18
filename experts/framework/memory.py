"""对话上下文:记住用户之前提到过的关键词。

记忆是**有界**的:只保留最近 `max_turns` 轮、最多 `max_keywords` 个关键词。
所以它"一定程度上"记得当前对话,而不是无限累积 —— 说远了会淡出,
这比假装记得更诚实(也不会让三句之前的话题一直劫持当前问题)。

它只做三件事:

1. 每轮把拆句拆出来的**非常用词**(`Decomposition.keywords`)记下来;
2. 下一句如果是"接着上一句问的"(带 它 / 这个 / 还有… 这类指代,或者根本没拆出
   新关键词),而直接查找一个包都没命中时,允许拿记忆里的关键词**再查一次**
   (`GoChat.ask` 里那个"第二轮")。判断在 `is_followup()`;
3. 把"记得什么"如实展示(`:ctx` / `--trace`),用户可以随时 `:forget`。

**不做**:改写用户的话、编造上一轮的内容、跨进程持久化。
记忆只活在这一次对话进程里 —— 关掉终端就没了,这是故意的。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

#: 一句里出现这些指代,就当作"接着上一句问"(仅在没有直接命中时才用记忆)
FOLLOWUP_MARKERS: tuple[str, ...] = (
    "它", "他", "她", "这个", "那个", "这些", "那些", "这里", "那里",
    "上面", "刚才", "刚刚", "前面", "之前", "继续", "还有", "另外", "再",
    "呢", "换一个", "举个例子", "详细点",
)


@dataclass(frozen=True)
class Turn:
    """一轮对话:用户说了什么、拆出哪些关键词、答出来自哪个包。"""

    text: str
    keywords: tuple[str, ...] = ()
    packs: tuple[str, ...] = ()
    domains: tuple[str, ...] = ()

    def line(self) -> str:
        keys = "、".join(self.keywords) or "(没有新关键词)"
        return f"{self.text} → {keys}"


class ConversationMemory:
    """有界的对话记忆:最近优先、去重、可清空。"""

    def __init__(self, max_turns: int = 8, max_keywords: int = 12) -> None:
        self.max_turns = max(1, int(max_turns))
        self.max_keywords = max(1, int(max_keywords))
        self._turns: deque[Turn] = deque(maxlen=self.max_turns)

    # ---- 写 --------------------------------------------------------------
    def remember(
        self,
        text: str,
        keywords: tuple[str, ...] | list[str] = (),
        packs: tuple[str, ...] | list[str] = (),
        domains: tuple[str, ...] | list[str] = (),
    ) -> Turn:
        turn = Turn(text=(text or "").strip(),
                    keywords=tuple(keywords),
                    packs=tuple(packs),
                    domains=tuple(domains))
        self._turns.append(turn)
        return turn

    def clear(self) -> None:
        self._turns.clear()

    # ---- 读 --------------------------------------------------------------
    @property
    def turns(self) -> tuple[Turn, ...]:
        return tuple(self._turns)

    def __len__(self) -> int:
        return len(self._turns)

    def keywords(self) -> tuple[str, ...]:
        """记得的关键词,**最近优先**、去重、最多 max_keywords 个。"""
        picked: list[str] = []
        for turn in reversed(self._turns):
            for keyword in turn.keywords:
                if keyword and keyword not in picked:
                    picked.append(keyword)
                if len(picked) >= self.max_keywords:
                    return tuple(picked)
        return tuple(picked)

    def topic(self) -> tuple[str, ...]:
        """当前话题的关键词:最近**答出来自领域包**的那一轮。

        为什么要挑"答上了"的那一轮:上一句如果谁都没接住(比如问了个没人会的问题),
        它的词不该立刻变成新话题;继续沿用最近一次真正聊起来的知识。
        """
        for turn in reversed(self._turns):
            if turn.domains and turn.keywords:
                return turn.keywords
        return self.keywords()

    def packs(self) -> tuple[str, ...]:
        """最近一轮答出来的包(给"接着问"时的提示用)。"""
        for turn in reversed(self._turns):
            if turn.packs:
                return turn.packs
        return ()

    def query(self, limit: int = 4) -> str:
        """把记得的关键词拼成一句,给数据包做检索用。"""
        return " ".join(self.keywords()[:limit])

    def snapshot(self) -> str:
        """一句话交代"记得什么"(给 :ctx / --trace 用)。"""
        if not self._turns:
            return "还没有记住任何东西。"
        keys = self.keywords()
        head = f"记得 {len(self._turns)} 轮"
        if keys:
            head += ":" + "、".join(keys)
        topic = self.topic()
        if topic:
            head += f";当前话题:{'、'.join(topic)}"
        if self.packs():
            head += f";最近查过:{'、'.join(self.packs())}"
        head += f";最近一句:{self._turns[-1].text}"
        return head


def is_followup(text: str, keywords: tuple[str, ...] | list[str]) -> bool:
    """这句话是不是"接着上一句问的"?

    两条判据(满足一条就算):
      · 带指代:它 / 这个 / 还有 / 刚才 …
      · 这一句**根本没拆出非常用词**(全是"为什么""还有吗"这类虚词)
    只有在这种问法下、而且直接查找没命中时,才会动用记忆 ——
    于是"怎么给猫剪指甲"这种新话题不会被上一轮的"引力波"劫持。
    """
    if not keywords:
        return True
    body = text or ""
    return any(marker in body for marker in FOLLOWUP_MARKERS)


__all__ = ["FOLLOWUP_MARKERS", "Turn", "ConversationMemory", "is_followup"]
