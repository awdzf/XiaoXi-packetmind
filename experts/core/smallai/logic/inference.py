"""进一步推理:把"继承"从特例变成**对所有谓词族通用**的机制。

问题(用户提的那条链):
    苹果 是 食物  →  食物 能 吃  →  苹果 能 吃

原来的实现只对『具有』这一个谓词做了属性上推,所以
`苹果 具有 含维生素` 能推出来,而 `苹果 能 吃` 推不出来 ——
不是规则写错了,是**机制太窄**:它把"属性"当成了一个写死的谓词。

解法:引入**谓词族**(predicate group)。
    · 一个族里的谓词语义等价或可互相推出:`能` / `可以` / `会` / `需要`
    · 继承规则按族自动生成:成员 是 类别、类别 <族> 某事 => 成员 <族> 某事
    · 查询时按族找候选:`苹果可以吃吗` 能找到 `能(苹果, 吃)`

于是加一类新关系(比如"可以""需要""属于")只需要往 GROUPS 里加一行,
不用再改推理代码 —— 这是"进一步推理能力"的实质:机制通用了。

族里还有两项配套能力,回答这类链式追问:
    read_chain(...)      给一条结论摆出**推导链**(为什么)
    read_capabilities()  列出一个东西**能做什么 / 有什么性质**(列举)
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# 谓词族:同族谓词在继承与查询时视为等价
# --------------------------------------------------------------------------
GROUPS: dict[str, tuple[str, ...]] = {
    # 能力/许可:可以吃、能吃、会飞……
    "ability": ("能", "可以", "会", "需要"),
    # 性质:具有、有
    "property": ("具有", "有"),
    # 归属:是、属于、在
    "membership": ("是", "属于", "在"),
    # 并列:同类
    "peer": ("同类",),
}

# 谓词 -> 族名(注意映射到**族名**而不是族代表:
# 早先映射成代表,结果 candidates_for('能') 拿 "能" 去查 GROUPS["能"] 直接 KeyError ——
# 族名与族代表是两个不同的东西,别混。)
GROUP_NAME_OF: dict[str, str] = {
    predicate: group
    for group, members in GROUPS.items()
    for predicate in members
}
# 兼容旧名字
GROUP_OF = GROUP_NAME_OF

# 写事实时的**规范形式**:能/可以/会/需要 都存成"能"
# (查询时按族展开,所以用户说"可以"也能查到)
CANONICAL: dict[str, str] = {
    "可以": "能", "会": "能", "需要": "能",
    "有": "具有",
    "在": "城市在",
}

# 生成继承规则时要跳过的族:归属/并列的传递已经由别的规则覆盖,
# 再生成一遍只会让事实库膨胀,还可能与"城市在"这类专用谓词打架。
_GROUP_RULE_SKIP = {"membership", "peer"}


def canonical(predicate: str) -> str:
    """把谓词归一到存储用的规范形式。"""
    return CANONICAL.get(predicate, predicate)


def candidates_for(predicate: str) -> list[str]:
    """一个谓词在查询时该试哪些写法(同族都要试,规范形式排第一)。"""
    canonical_form = canonical(predicate)
    group = GROUP_NAME_OF.get(predicate) or GROUP_NAME_OF.get(canonical_form)
    if group is None:
        return [canonical_form]
    members = GROUPS[group]
    ordered = [canonical_form] + [canonical(m) for m in members]
    out: list[str] = []
    for item in ordered:
        if item not in out:
            out.append(item)
    return out


def inheritance_rules() -> list:
    """按族生成"属性/能力上推"规则。

    每条规则都长这样(以 ability 族为例):
        若  是(?x, ?y) 且 能(?y, ?p)   则  能(?x, ?p)
    即:A 是 B、B 能做 P => A 能做 P。属性、能力、归属各自一条。
    """
    from .reasoner import Rule

    rules = []
    for group, members in GROUPS.items():
        if group in _GROUP_RULE_SKIP:
            continue
        representative = members[0]
        rules.append(
            Rule(
                f"继承·{group}上推",
                {"all": [
                    {"fact": ["是", "?x", "?y"]},
                    {"fact": [representative, "?y", "?p"]},
                ]},
                (representative, ("?x", "?p")),
                f"A 是 B、B {representative} P => A {representative} P",
            )
        )
    return rules


# --------------------------------------------------------------------------
# 链式追问:为什么 / 能做什么
# --------------------------------------------------------------------------
@dataclass
class ChainLink:
    """推导链上的一环。"""

    fact: tuple[str, tuple[str, ...]]
    rule: str | None = None
    premises: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)

    def described(self) -> str:
        fact = _fact_text(self.fact)
        if not self.rule:
            return f"{fact}(事实库里直接有)"
        premises = "、".join(_fact_text(p) for p in self.premises)
        return f"{fact}  <-({self.rule})<- {premises}"


def _fact_text(fact: tuple[str, tuple[str, ...]]) -> str:
    predicate, args = fact
    if not args:
        return predicate
    return f"{args[0]} {predicate} " + "、".join(args[1:]) if len(args) > 1 else f"{args[0]} {predicate}"


@dataclass
class CapabilityReport:
    """一个东西"能做什么 / 有什么性质"的列举结果。"""

    subject: str
    categories: list[str] = field(default_factory=list)
    abilities: list[CapabilityLink] = field(default_factory=list)
    properties: list[CapabilityLink] = field(default_factory=list)
    missing: bool = False
    note: str = ""


@dataclass
class CapabilityLink:
    value: str
    from_category: str
    chain: list[str] = field(default_factory=list)


def read_chain(store, fact: tuple[str, tuple[str, ...]], depth: int = 6,
               seen: set | None = None) -> list[ChainLink]:
    """给一条结论摆出推导链(为什么成立)。

    用**深度受限**的递归往上找依据:每一环都记下用了哪条规则、前提是什么。
    找不到派生记录时返回单环(说明它是直接存进事实库的)。
    """
    seen = seen if seen is not None else set()
    if fact in seen or depth <= 0:
        return []
    seen.add(fact)
    derivation = next((d for d in store.derivations if d.fact == fact), None)
    if derivation is None:
        return [ChainLink(fact=fact)]
    links = [ChainLink(fact=fact, rule=derivation.rule, premises=list(derivation.premises))]
    for premise in derivation.premises:
        links.extend(read_chain(store, premise, depth - 1, seen))
    return links


def read_capabilities(store, subject: str, max_categories: int = 12) -> CapabilityReport:
    """列出一个东西的能力与性质,并指出**每条是从哪个上位类别继承来的**。

    做法:先沿『是』链收集上位类别(含自己),再在这些类别的『能/具有』事实里
    找该 subject 能推出的项目。这样报告里每一项都能追到"因为它属于某某"。
    """
    report = CapabilityReport(subject=subject)

    # 1) 收集上位类别:从 subject 出发沿『是』向上走
    edges: dict[str, set[str]] = {}
    for predicate, args in store.facts:
        if predicate == "是" and len(args) == 2:
            edges.setdefault(args[0], set()).add(args[1])

    categories: list[str] = []
    frontier = [subject]
    seen = {subject}
    while frontier and len(categories) < max_categories:
        nxt: list[str] = []
        for node in frontier:
            for parent in sorted(edges.get(node, ())):
                if parent in seen:
                    continue
                seen.add(parent)
                categories.append(parent)
                nxt.append(parent)
        frontier = nxt
    report.categories = categories

    if not categories:
        report.missing = True
        report.note = f"事实库里没有『{subject} 是 某类』这条,推不出它属于什么"
        return report

    # 2) 对每个上位类别,收集它能做的事与具有的性质
    for group, target in (("ability", report.abilities), ("property", report.properties)):
        for predicate, args in sorted(store.facts):
            if predicate != GROUPS[group][0] or len(args) != 2:
                continue
            category, value = args
            if category == subject or category not in seen:
                continue
            chain = [f"是({subject}, {category})", f"{predicate}({category}, {value})"]
            if group == "ability":
                target.append(CapabilityLink(value=value, from_category=category, chain=chain))
            else:
                target.append(CapabilityLink(value=value, from_category=category, chain=chain))
    # 去重:同一个值可能从多个类别继承来
    for target in (report.abilities, report.properties):
        seen_values: set[str] = set()
        unique: list[CapabilityLink] = []
        for link in target:
            if link.value in seen_values:
                continue
            seen_values.add(link.value)
            unique.append(link)
        target[:] = unique
    return report
