"""事实库 + 前向推理引擎。

这是"基本逻辑能力"的核心。与"再训一个模型"的区别在于:它是**符号的、可追溯的**——
结论怎么来的,能一步步摆出来给人核对,而不是一个概率分布。

三件事:
  1. 事实库(FactStore):存 (谓词, 参数) 断言与蕴含规则;可持久化成 JSON;
  2. 前向推理(forward_chain):反复套用规则直到不再产生新事实(有轮数上限);
  3. 查询(query):回答"这个事实成不成立",并给出**用的哪条规则、代入了什么**。

变量约定:参数里以 `?` 开头的是变量(如 "?x"),其余是常量。
规则形如:满足 conditions 则推出 conclusion(带同样的变量)。
conditions 支持:
    {"fact": [谓词, 参数...]}                  单个条件
    {"all": [cond, cond, ...]}                 全部满足(合取)
    {"any": [cond, cond, ...]}                 任一满足(析取)
    {"not": cond}                              不满足
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .expr import And, Expr, Fact, Not, Or, collect_facts, to_text

FactTuple = tuple[str, tuple[str, ...]]
MAX_ROUNDS = 200          # 前向推理轮数上限,防规则互相触发导致不收敛


# --------------------------------------------------------------------------
# 规则
# --------------------------------------------------------------------------
@dataclass
class Rule:
    name: str
    condition: Any
    conclusion: FactTuple
    note: str = ""

    def described(self) -> str:
        return f"{self.name}: 若 {_cond_text(self.condition)} 则 {Fact(*self.conclusion)}"


def _cond_text(cond: Any) -> str:
    if not isinstance(cond, dict):
        return str(cond)
    if "fact" in cond:
        return str(Fact(cond["fact"][0], tuple(cond["fact"][1:])))
    if "all" in cond:
        return " 且 ".join(_cond_text(c) for c in cond["all"])
    if "any" in cond:
        return " 或 ".join(_cond_text(c) for c in cond["any"])
    if "not" in cond:
        return f"并非({_cond_text(cond['not'])})"
    return str(cond)


def rule_from_dict(data: dict[str, Any]) -> Rule:
    return Rule(
        name=data.get("name") or "未命名规则",
        condition=data["if"],
        conclusion=(data["then"][0], tuple(data["then"][1:])),
        note=data.get("note", ""),
    )


# --------------------------------------------------------------------------
# 事实库
# --------------------------------------------------------------------------
@dataclass
class FactStore:
    """事实 + 规则 + 推理账本。"""

    facts: set[FactTuple] = field(default_factory=set)
    rules: list[Rule] = field(default_factory=list)
    # 本次推理新增出来的派生事实(生成层用它说明"我从规则推出来的")
    derived: set[FactTuple] = field(default_factory=set)
    # 派生链:新事实 <- 规则名 + 触发它的已有事实
    derivations: list["Derivation"] = field(default_factory=list)

    # ---- 基本操作 --------------------------------------------------------
    def add_fact(self, predicate: str, *args: str) -> bool:
        key = self._normalize((predicate, tuple(args)))
        existing = self._lookup(key)
        if existing is not None:
            return False
        self.facts.add(key)
        return True

    def add(self, key: FactTuple) -> bool:
        return self.add_fact(key[0], *key[1])

    def _normalize(self, key: FactTuple) -> FactTuple:
        return (key[0].strip(), tuple(str(a).strip() for a in key[1]))

    def _lookup(self, key: FactTuple) -> FactTuple | None:
        if key in self.facts:
            return key
        return None

    def has(self, predicate: str, *args: str) -> bool:
        return self._normalize((predicate, tuple(args))) in self.facts

    def count(self) -> int:
        return len(self.facts)

    def add_rule(self, rule: Rule) -> None:
        """同名规则按 **user-wins** 处理:后加的覆盖先前的。

        这条纪律很重要,而且是踩出来的:早先的写法是"同名就不加",
        结果 kb.json 里一条名字撞上内置规则的自定义规则被静默丢掉,
        表现成"我明明写了规则却推不出来"。
        规则是数据,数据不该有这种隐形优先级 —— 所以显式覆盖。
        """
        self.rules = [r for r in self.rules if r.name != rule.name]
        self.rules.append(rule)

    # ---- 持久化 ----------------------------------------------------------
    def to_json(self) -> dict[str, Any]:
        return {
            "facts": [
                {"predicate": p, "args": list(a)} for p, a in sorted(self.facts)
            ],
            "rules": [
                {"name": r.name, "if": r.condition,
                 "then": [r.conclusion[0], *r.conclusion[1]], "note": r.note}
                for r in self.rules
            ],
        }

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(self.to_json(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return p

    @classmethod
    def load(cls, path: str | Path, *, merge_defaults: bool = True) -> "FactStore":
        p = Path(path)
        store = default_store() if merge_defaults else cls()
        if not p.exists():
            return store
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return store
        store.extend(data)
        return store

    def extend(self, data: dict[str, Any] | None) -> None:
        """把一份 JSON 形状的事实/规则/关系并进来（数据包的结构化知识用）。

        坏条目**跳过而不抛异常**：一条写错的事实不应该让整个包失效；
        结构问题由 `--health` / `ExpertPack.validate()` 单独报出来。
        """
        if not isinstance(data, dict):
            return
        for item in data.get("facts", []) or []:
            if not isinstance(item, dict):
                continue
            predicate = str(item.get("predicate") or "").strip()
            args = item.get("args") or []
            if not predicate or not isinstance(args, (list, tuple)):
                continue
            self.add_fact(predicate, *(str(arg) for arg in args))
        for item in data.get("rules", []) or []:
            if not isinstance(item, dict):
                continue
            try:
                self.add_rule(rule_from_dict(item))
            except (KeyError, TypeError, AttributeError, IndexError):
                continue
        for item in data.get("relations", []) or []:
            if not isinstance(item, dict):
                continue
            relation = str(item.get("type") or item.get("predicate") or "").strip()
            subject = str(item.get("from") or item.get("subject") or "").strip()
            obj = str(item.get("to") or item.get("object") or "").strip()
            if relation and subject and obj:
                self.add_fact(relation, subject, obj)


@dataclass
class Derivation:
    """一条派生记录:哪条规则、代入了什么、推出了什么。"""

    fact: FactTuple
    rule: str
    premises: list[FactTuple]

    def described(self) -> str:
        premises = "、".join(str(Fact.from_key(p)) for p in self.premises)
        return f"{Fact.from_key(self.fact)}  <-({self.rule})<- {premises}"


# --------------------------------------------------------------------------
# 合一(unification)
# --------------------------------------------------------------------------
def _is_var(token: str) -> bool:
    return token.startswith("?")


def unify(pattern: FactTuple, fact: FactTuple, bindings: dict[str, str]) -> dict[str, str] | None:
    """把 pattern 与事实事实对齐:成功返回扩展后的绑定,失败返回 None。"""
    if pattern[0] != fact[0] or len(pattern[1]) != len(fact[1]):
        return None
    out = dict(bindings)
    for pat, val in zip(pattern[1], fact[1]):
        if _is_var(pat):
            bound = out.get(pat)
            if bound is None:
                out[pat] = val
            elif bound != val:
                return None
        elif pat != val:
            return None
    return out


def substitute(pattern: FactTuple, bindings: dict[str, str]) -> FactTuple | None:
    """把结论里的变量换成绑定值;有变量没绑上就返回 None(说明规则不适用)。"""
    args = []
    for arg in pattern[1]:
        if _is_var(arg):
            if arg not in bindings:
                return None
            args.append(bindings[arg])
        else:
            args.append(arg)
    return (pattern[0], tuple(args))


# --------------------------------------------------------------------------
# 前向推理
# --------------------------------------------------------------------------
def forward_chain(store: FactStore, max_rounds: int = MAX_ROUNDS) -> int:
    """反复套用规则直到不动点。返回新增事实数。

    只在**新增**事实时才记录 derivation,免得账本里塞满重复推导。
    """
    added = 0
    for _ in range(max_rounds):
        newly: list[Derivation] = []
        for rule in store.rules:
            for bindings, premises in match_condition(store, rule.condition, {}):
                conclusion = substitute(rule.conclusion, bindings)
                if conclusion is None:
                    continue
                if conclusion in store.facts:
                    continue
                newly.append(Derivation(conclusion, rule.name, premises))
        if not newly:
            break
        for derivation in newly:
            if derivation.fact not in store.facts:
                store.facts.add(derivation.fact)
                store.derived.add(derivation.fact)
                store.derivations.append(derivation)
                added += 1
    return added


def match_condition(
    store: FactStore, cond: Any, bindings: dict[str, str]
) -> list[tuple[dict[str, str], list[FactTuple]]]:
    """求出一个条件的所有满足方式,返回 (绑定, 用到的前提事实) 列表。"""
    if not isinstance(cond, dict):
        return []

    if "fact" in cond:
        pattern = (cond["fact"][0], tuple(cond["fact"][1:]))
        out: list[tuple[dict[str, str], list[FactTuple]]] = []
        for fact in store.facts:
            merged = unify(pattern, fact, bindings)
            if merged is not None:
                out.append((merged, [fact]))
        return out

    if "not" in cond:
        inner = match_condition(store, cond["not"], bindings)
        # 内层用**原绑定**求过没有:没有才成立(不引入新绑定的保守做法)
        if inner:
            return []
        return [(dict(bindings), [])]

    if "all" in cond:
        results: list[tuple[dict[str, str], list[FactTuple]]] = [(dict(bindings), [])]
        for sub in cond["all"]:
            next_results: list[tuple[dict[str, str], list[FactTuple]]] = []
            for binds, premises in results:
                for new_binds, new_premises in match_condition(store, sub, binds):
                    next_results.append((new_binds, premises + new_premises))
            results = next_results
            if not results:
                break
        return results

    if "any" in cond:
        results = []
        for sub in cond["any"]:
            results.extend(match_condition(store, sub, bindings))
        return results

    return []


# --------------------------------------------------------------------------
# 查询
# --------------------------------------------------------------------------
@dataclass
class QueryResult:
    fact: FactTuple
    holds: bool
    path: list[str] = field(default_factory=list)      # 推导链(人话)
    rule: str | None = None
    premises: list[FactTuple] = field(default_factory=list)

    def described(self) -> str:
        head = "成立" if self.holds else "不成立(不在事实库里,也推不出来)"
        if not self.path:
            return f"{Fact.from_key(self.fact)}:{head}"
        return f"{Fact.from_key(self.fact)}:{head};依据 " + ";".join(self.path)


def explain(store: FactStore, fact: FactTuple, depth: int = 0, seen: set[FactTuple] | None = None) -> list[str]:
    """给一个派生事实找一条推导链(用于向人解释'为什么')."""
    seen = seen or set()
    if fact in seen or depth > 6:
        return []
    seen.add(fact)
    for derivation in store.derivations:
        if derivation.fact != fact:
            continue
        lines = [derivation.described()]
        for premise in derivation.premises:
            lines.extend(explain(store, premise, depth + 1, seen))
        return lines
    return []


def query(store: FactStore, predicate: str, *args: str) -> QueryResult:
    """问一个原子事实成不成立。"""
    fact = (predicate, tuple(args))
    holds = fact in store.facts
    derivation = next((d for d in store.derivations if d.fact == fact), None)
    if derivation is None:
        return QueryResult(fact=fact, holds=holds)
    return QueryResult(
        fact=fact,
        holds=True,
        path=explain(store, fact),
        rule=derivation.rule,
        premises=derivation.premises,
    )


# --------------------------------------------------------------------------
# 布尔式求值
# --------------------------------------------------------------------------
@dataclass
class EvalDetail:
    expr: str
    value: bool
    reason: str


@dataclass
class ExprResult:
    value: bool
    details: list[EvalDetail] = field(default_factory=list)

    def described(self) -> str:
        return "\n".join(f"  · {d.expr} => {'真' if d.value else '假'}({d.reason})" for d in self.details)


def evaluate(store: FactStore, expr: Expr) -> ExprResult:
    """求值表达式树。每个节点都留一条明细,便于生成层解释。"""
    details: list[EvalDetail] = []

    def walk(node: Expr) -> bool:
        if isinstance(node, Fact):
            fact = node.key()
            holds = fact in store.facts
            why = "在事实库里" if holds else "不在事实库里"
            derivation = next((d for d in store.derivations if d.fact == fact), None)
            if derivation:
                why = f"由规则『{derivation.rule}』推出"
            details.append(EvalDetail(to_text(node), holds, why))
            return holds
        if isinstance(node, Not):
            value = not walk(node.operand)
            details.append(EvalDetail(to_text(node), value, "取反"))
            return value
        if isinstance(node, And):
            left = walk(node.left)
            right = walk(node.right)
            value = left and right
            details.append(
                EvalDetail(to_text(node), value, f"左={'真' if left else '假'} 且 右={'真' if right else '假'}")
            )
            return value
        if isinstance(node, Or):
            left = walk(node.left)
            right = walk(node.right)
            value = left or right
            details.append(
                EvalDetail(to_text(node), value, f"左={'真' if left else '假'} 或 右={'真' if right else '假'}")
            )
            return value
        return False

    value = walk(expr)
    return ExprResult(value=value, details=details)


def unknown_facts(store: FactStore, expr: Expr) -> list[Fact]:
    """表达式里所有"既不在库里、也推不出来"的原子。

    这一条很重要:逻辑求值如果拿一堆不知道真假的原子去做与/或,
    结果会**看着很像结论**。所以三层为真:全知道 -> 给结果;
    有不知道 -> 只对"能定论的"下结论(与运算见到假就是假;或运算见到真就是真),
    否则如实说"我不知道这几个"。
    """
    return [f for f in collect_facts(expr) if f.key() not in store.facts]


def decide(store: FactStore, expr: Expr) -> tuple[bool | None, ExprResult, list[Fact]]:
    """返回 (结论, 明细, 未知原子)。结论为 None = 依据不足,不硬判。"""
    result = evaluate(store, expr)
    unknown = unknown_facts(store, expr)
    if not unknown:
        return result.value, result, []

    # 部分未知时,看能不能凭已知部分定论(与/或的短路语义):
    # 合取里已有一个确定为假 -> 整句确定为假;析取里已有一个确定为真 -> 整句确定为真。
    # 其余情况一律返回 None(依据不足),不要拿"未知"当"假"用。
    if _has_definite_false(store, expr):
        return False, result, unknown

    # 析取:任一分支确定为真,整句确定为真
    if isinstance(expr, Or) and _has_definite_true(store, expr):
        return True, result, unknown

    # 其余情况:依据不足,如实返回 None —— 不把"不知道"说成"假"
    return None, result, unknown


def _has_definite_false(store: FactStore, expr: Expr) -> bool:
    """合取里只要有一个原子确定为假,整句确定为假。"""
    if isinstance(expr, And):
        return _has_definite_false(store, expr.left) or _has_definite_false(store, expr.right)
    if isinstance(expr, Or):
        return _has_definite_false(store, expr.left) and _has_definite_false(store, expr.right)
    if isinstance(expr, Not):
        return _has_definite_true(store, expr.operand)
    if isinstance(expr, Fact):
        # 原子"确定为假"仅当它在库里被显式否定(如 属于(X,Y) 与 是(X,Y) 不同层级),
        # 这里保守处理:未知不算假。
        return False
    return False


def _has_definite_true(store: FactStore, expr: Expr) -> bool:
    """析取里只要有一个原子确定为真,整句确定为真。"""
    if isinstance(expr, And):
        return _has_definite_true(store, expr.left) and _has_definite_true(store, expr.right)
    if isinstance(expr, Or):
        return _has_definite_true(store, expr.left) or _has_definite_true(store, expr.right)
    if isinstance(expr, Not):
        return _has_definite_false(store, expr.operand)
    if isinstance(expr, Fact):
        return expr.key() in store.facts
    return False


# --------------------------------------------------------------------------
# 默认知识库
# --------------------------------------------------------------------------
def default_rules() -> list[Rule]:
    """内置规则:都是"结构型"的,不依赖具体领域知识。

    继承规则**按谓词族生成**(见 inference.inheritance_rules):
    属性(具有)与能力(能/可以/会/需要)各有一条上推规则。
    这样加一类关系只需要往谓词族表加一行,不用再改这里 ——
    这是"进一步推理"的实质:机制通用了,而不是再特判一个谓词。
    """
    from .inference import inheritance_rules

    rules = [
        Rule("继承·类属传递", {"all": [
            {"fact": ["是", "?x", "?y"]},
            {"fact": ["是", "?y", "?z"]},
        ]}, ("是", ("?x", "?z")), "A 是 B、B 是 C => A 是 C"),
        Rule("区划·城市在地区", {"fact": ["城市在", "?city", "?region"]},
             ("地区含", ("?region", "?city")), "反向索引,便于双向查询"),
        Rule("区划·地区在大区", {"fact": ["地区属于", "?region", "?area"]},
             ("属于", ("?region", "?area")), "地区归大区"),
        # 把『A 在 B』归一成『A 属于 B』:这样问『北京属于华北吗』
        # (城市 -> 地区)和问『华北属于北方吗』(地区 -> 大区)走同一条查询路径。
        # 三套谓词(城市在/地区属于/属于)是踩出来的:不归一的话
        # 同一个"属于"在三种粒度上会互相答不上。
        Rule("区划·城市在归一为属于", {"fact": ["城市在", "?city", "?region"]},
             ("属于", ("?city", "?region")), "城市 -> 地区"),
        # 直辖市的地理归属:走 直辖市属于 这条专用谓词,再归一成 地区属于。
        # 直接归一成 属于 是**不够**的:那样就接不上"地区 -> 大区"那条链
        # (北京 属于 华北 之后没有"华北 属于 北方"这一跳可走)。
        Rule("区划·直辖市归一为地区", {"fact": ["直辖市属于", "?city", "?area"]},
             ("地区属于", ("?city", "?area")), "直辖市 -> 大区,供两跳链使用"),
        Rule("区划·城市属大区", {"all": [
            {"fact": ["城市在", "?city", "?region"]},
            {"fact": ["地区属于", "?region", "?area"]},
        ]}, ("属于", ("?city", "?area")), "两跳:城市 -> 地区 -> 大区"),
        Rule("条件·下雨要带伞", {"fact": ["下雨", "?place"]},
             ("需要", ("?place", "带伞")), "下雨 => 要带伞"),
        Rule("条件·带伞则可出门", {"all": [
            {"fact": ["下雨", "?place"]},
            {"fact": ["有", "?who", "伞"]},
        ]}, ("可以", ("?who", "出门")), "下雨但有伞 => 可以出门"),
    ]
    rules.extend(inheritance_rules())
    return rules


def default_facts() -> list[FactTuple]:
    """内置事实:一份**示例**知识,标注清楚便于替换。"""
    facts: list[FactTuple] = [
        # 区划(城市 -> 地区 由 lexicon.CITIES_BY_REGION 注入,这里放大区归属)
        ("地区属于", ("华北", "北方")),
        ("地区属于", ("东北", "北方")),
        ("地区属于", ("西北", "北方")),
        ("地区属于", ("华东", "南方")),
        ("地区属于", ("华中", "南方")),
        ("地区属于", ("华南", "南方")),
        ("地区属于", ("西南", "南方")),
        # 直辖市的行政层级(与地理归属分开放,两者是不同的事实)
        ("是", ("直辖市", "省级行政区")),
        # 旧谓词 直辖市属于 保留:外部 kb.json 可能已经在用它
        ("直辖市属于", ("北京", "华北")),
        ("直辖市属于", ("天津", "华北")),
        ("直辖市属于", ("上海", "华东")),
        ("直辖市属于", ("重庆", "西南")),
        # 类别层级
        ("是", ("人类", "哺乳动物")),
        ("是", ("哺乳动物", "动物")),
        ("是", ("动物", "生物")),
        ("是", ("北京", "城市")),
        ("是", ("上海", "城市")),
        ("是", ("麻雀", "鸟")),
        ("是", ("鸟", "动物")),
        # 能力与性质:存储用**规范谓词**(能/具有);查询时按谓词族展开,
        # 所以用户问"苹果可以吃吗"也能查到 ("能", ("苹果","吃"))。
        ("是", ("苹果", "水果")),
        ("是", ("水果", "食物")),
        ("能", ("食物", "吃")),
        ("能", ("食物", "切开")),
        ("具有", ("食物", "营养")),
        ("能", ("鸟", "飞")),
        ("具有", ("鸟", "翅膀")),
        # 状态示例(可被用户改)
        ("下雨", ("北京",)),
        ("有", ("小明", "伞")),
    ]
    return facts


def default_store() -> FactStore:
    """默认事实库:内置事实 + 城市区划表 + 内置规则。

    注意城市区划是**合成**的(不是背下来的):CITIES_BY_REGION 里写的
    "某某城市属于哪个地区"就是这里的 城市在 事实;谁的归属改了,
    只改词表一处,推理立刻跟着变。
    """
    from ..rules.lexicon import CITIES_BY_REGION

    store = FactStore()
    for fact in default_facts():
        store.add(fact)
    for region, cities in CITIES_BY_REGION.items():
        for city in cities:
            store.add(("城市在", (city, region)))
    # "是城市"只加一次:一个城市可能在两个分组里(上海既在直辖市也在华东),
    # 重复加不会出错(集合去重),但显式去重让意图更清楚。
    for city in {c for cities in CITIES_BY_REGION.values() for c in cities}:
        store.add(("是", (city, "城市")))
    for rule in default_rules():
        store.add_rule(rule)
    return store


def store_for(project_paths: Iterable[str | Path] | None = None) -> FactStore:
    """按给定路径找 kb.json;找不到就用默认库。

    路径按顺序试,第一个存在的胜出 —— 于是可以做到
    "项目内 kb.json 覆盖默认库,而默认库始终可用"。
    """
    for raw in project_paths or ():
        path = Path(raw)
        if path.exists():
            return FactStore.load(path)
    return default_store()
