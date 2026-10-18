"""一个很小的 Datalog / Prolog 式子集：事实 + 规则 + 查询 + 证明树。

支持：

    · 事实：`parent(alice, bob).` 或 `["parent", "alice", "bob"]`；
    · 规则：`ancestor(?x, ?z) :- parent(?x, ?z).`
             `ancestor(?x, ?z) :- parent(?x, ?y), ancestor(?y, ?z).`
      也接受 `{"head": [...], "body": [[...], ...]}`；
    · 递归：朴素不动点迭代（有轮数/事实数上限，不会无限跑）；
    · 否定：`not parent(?x, ?y)`；只在已经算出的正事实集合上判断
      （负递归不做，遇到就记一条错误并跳过，不假装支持）；
    · 查询：常量/变量混合，返回绑定与证明树。

它不是完整的 Prolog：没有函数符号、没有 cut、没有算术求值；变量名以 `?`
开头，其余都当常量。零依赖、确定性、小数据量够用。

为什么要它：`facts` / `rules` 适合“一条事实 + 一条链”，但表达不了
“递归的祖先/可达/传递闭包”。Datalog 用同一份数据包知识补上这一块，
每一步仍然能给出证明树。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

MAX_ROUNDS = 32
MAX_FACTS = 20000
MAX_PROOF_STEPS = 12

_VAR_RE = re.compile(r"^\?[A-Za-z_][A-Za-z0-9_]*$")
_ATOM_RE = re.compile(r"^\s*([^\s(]+)\s*\((.*)\)\s*\.?\s*$", re.DOTALL)


def is_variable(term: str) -> bool:
    return bool(_VAR_RE.match(str(term or "").strip()))


def _strip(text: str) -> str:
    return str(text or "").strip().strip('"').strip("'")


def _split_top(text: str, separator: str = ",") -> list[str]:
    """按顶层分隔符切（括号/引号里的不切）。"""
    parts: list[str] = []
    buffer: list[str] = []
    depth = 0
    quote = ""
    for char in text:
        if quote:
            buffer.append(char)
            if char == quote:
                quote = ""
            continue
        if char in ("'", '"'):
            quote = char
            buffer.append(char)
        elif char == "(":
            depth += 1
            buffer.append(char)
        elif char == ")":
            depth = max(0, depth - 1)
            buffer.append(char)
        elif char == separator and depth == 0:
            part = "".join(buffer).strip()
            if part:
                parts.append(part)
            buffer = []
        else:
            buffer.append(char)
    part = "".join(buffer).strip()
    if part:
        parts.append(part)
    return parts


@dataclass(frozen=True)
class Atom:
    predicate: str
    args: tuple[str, ...]

    def format(self) -> str:
        return f"{self.predicate}(" + ", ".join(self.args) + ")"

    @property
    def ground(self) -> bool:
        return all(not is_variable(arg) for arg in self.args)


@dataclass(frozen=True)
class Rule:
    head: Atom
    body: tuple[Atom, ...]
    negated: tuple[Atom, ...] = ()
    name: str = ""

    def format(self) -> str:
        body = [atom.format() for atom in self.body]
        body.extend("not " + atom.format() for atom in self.negated)
        return f"{self.head.format()} :- " + ", ".join(body) + "."


@dataclass(frozen=True)
class Proof:
    atom: Atom
    rule: str = ""
    premises: tuple["Proof", ...] = ()


@dataclass(frozen=True)
class QueryResult:
    bindings: dict[str, str]
    proof: Proof

    def get(self, name: str, default: str = "") -> str:
        key = name if name.startswith("?") else "?" + name
        return self.bindings.get(key, default)


def parse_atom(value: Any) -> Atom:
    """把字符串/数组/对象解析成 Atom；坏输入抛 ValueError。"""
    if isinstance(value, Atom):
        return value
    if isinstance(value, dict):
        predicate = _strip(value.get("predicate") or value.get("pred") or "")
        args = value.get("args") or ()
        if not predicate or not isinstance(args, (list, tuple)):
            raise ValueError(f"事实/原子格式不对:{value!r}")
        return Atom(predicate, tuple(_strip(arg) for arg in args))
    if isinstance(value, (list, tuple)):
        if not value:
            raise ValueError("空原子")
        predicate = _strip(value[0])
        args = tuple(_strip(arg) for arg in value[1:])
        if not predicate:
            raise ValueError("原子缺谓词")
        return Atom(predicate, args)
    text = _strip(value)
    match = _ATOM_RE.match(text)
    if not match:
        raise ValueError(f"原子格式不对:{text!r}")
    predicate = match.group(1).strip()
    inside = match.group(2).strip()
    args = tuple(_strip(part) for part in _split_top(inside)) if inside else ()
    if not predicate:
        raise ValueError(f"原子缺谓词:{text!r}")
    return Atom(predicate, args)


def parse_rule(value: Any) -> Rule:
    """解析 `head :- body.` 或 `{"head":..., "body":...}`。"""
    if isinstance(value, Rule):
        return value
    if isinstance(value, dict):
        head = parse_atom(value.get("head"))
        body_raw = value.get("body") or ()
        neg_raw = value.get("negated") or value.get("not") or ()
        if not isinstance(body_raw, (list, tuple)):
            raise ValueError("规则 body 必须是数组")
        if not isinstance(neg_raw, (list, tuple)):
            neg_raw = [neg_raw]
        body = tuple(parse_atom(item) for item in body_raw)
        negated = tuple(parse_atom(item) for item in neg_raw)
        return Rule(
            head=head,
            body=body,
            negated=negated,
            name=str(value.get("name") or "").strip(),
        )

    text = _strip(value).rstrip(".")
    if ":-" in text:
        head_text, body_text = text.split(":-", 1)
    elif "←" in text:
        head_text, body_text = text.split("←", 1)
    elif "<-" in text:
        head_text, body_text = text.split("<-", 1)
    else:
        raise ValueError(f"规则缺 :- :{text!r}")
    head = parse_atom(head_text)
    body: list[Atom] = []
    negated: list[Atom] = []
    for part in _split_top(body_text):
        part = part.strip()
        lowered = part.lower()
        if lowered.startswith("not "):
            negated.append(parse_atom(part[4:].strip()))
        else:
            body.append(parse_atom(part))
    return Rule(head=head, body=tuple(body), negated=tuple(negated))


def _variables(atom: Atom) -> set[str]:
    return {arg for arg in atom.args if is_variable(arg)}


def _unify(atom: Atom, fact: tuple[str, ...], bindings: dict[str, str]) -> dict[str, str] | None:
    if len(atom.args) != len(fact):
        return None
    out = dict(bindings)
    for term, value in zip(atom.args, fact):
        if is_variable(term):
            current = out.get(term)
            if current is None:
                out[term] = value
            elif current != value:
                return None
        elif term != value:
            return None
    return out


def _instantiate(atom: Atom, bindings: dict[str, str]) -> Atom | None:
    args: list[str] = []
    for term in atom.args:
        if is_variable(term):
            value = bindings.get(term)
            if value is None:
                return None
            args.append(value)
        else:
            args.append(term)
    return Atom(atom.predicate, tuple(args))


class DatalogEngine:
    """朴素不动点 Datalog；事实/规则都来自一个数据包的 `datalog` 段。"""

    def __init__(
        self,
        facts: Iterable[Any] = (),
        rules: Iterable[Any] = (),
        *,
        max_rounds: int = MAX_ROUNDS,
        max_facts: int = MAX_FACTS,
    ) -> None:
        self.max_rounds = max(1, int(max_rounds))
        self.max_facts = max(1, int(max_facts))
        self.facts: dict[str, set[tuple[str, ...]]] = {}
        self.proofs: dict[tuple[str, tuple[str, ...]], Proof] = {}
        self.rules: list[Rule] = []
        self.errors: list[str] = []
        self.rounds = 0
        self._derived = False
        for item in facts:
            self.add_fact(item)
        for item in rules:
            self.add_rule(item)

    @classmethod
    def from_spec(cls, spec: Any, **kwargs: Any) -> "DatalogEngine":
        if spec is None:
            return cls()
        if isinstance(spec, dict):
            facts = spec.get("facts") or ()
            rules = spec.get("rules") or ()
        elif isinstance(spec, (list, tuple)):
            facts, rules = spec, ()
        else:
            raise ValueError("datalog 需要对象（facts/rules）或数组")
        return cls(facts=facts, rules=rules, **kwargs)

    # ---- 构建 ------------------------------------------------------------
    def add_fact(self, value: Any, proof: Proof | None = None) -> Atom | None:
        try:
            atom = parse_atom(value)
        except ValueError as exc:
            self.errors.append(str(exc))
            return None
        if not atom.ground:
            self.errors.append(f"事实必须是常量:{atom.format()}")
            return None
        key = (atom.predicate, atom.args)
        if key in self.proofs:
            return atom
        if sum(len(bucket) for bucket in self.facts.values()) >= self.max_facts:
            self.errors.append(f"事实数超过上限 {self.max_facts}，后面的忽略")
            return atom
        self.facts.setdefault(atom.predicate, set()).add(atom.args)
        self.proofs[key] = proof or Proof(atom=atom)
        self._derived = False
        return atom

    def add_rule(self, value: Any) -> Rule | None:
        try:
            rule = parse_rule(value)
        except ValueError as exc:
            self.errors.append(str(exc))
            return None
        head_vars = _variables(rule.head)
        body_vars: set[str] = set()
        for atom in rule.body:
            body_vars |= _variables(atom)
        # 安全规则：头里的变量必须在正体里出现，否则无法实例化
        missing = head_vars - body_vars
        if missing:
            self.errors.append(
                f"规则头变量未在正体中出现:{rule.format()}（{', '.join(sorted(missing))}）"
            )
            return None
        for atom in rule.negated:
            missing_negative = _variables(atom) - body_vars
            if missing_negative:
                self.errors.append(
                    f"否定原子变量未在正体中出现:{rule.format()}"
                    f"（{', '.join(sorted(missing_negative))}）"
                )
                return None
        self.rules.append(rule)
        self._derived = False
        return rule

    # ---- 推理 ------------------------------------------------------------
    def derive(self) -> "DatalogEngine":
        if self._derived:
            return self
        for _round in range(self.max_rounds):
            self.rounds = _round + 1
            added = False
            for rule in self.rules:
                added = self._apply_rule(rule) or added
            if not added:
                break
        else:
            self.errors.append(f"推理达到 {self.max_rounds} 轮上限，可能仍有未展开的规则")
        self._derived = True
        return self

    def _matches(
        self,
        atom: Atom,
        bindings: dict[str, str],
    ) -> list[tuple[dict[str, str], Proof]]:
        out: list[tuple[dict[str, str], Proof]] = []
        for fact in self.facts.get(atom.predicate, ()):  # 只扫同谓词
            merged = _unify(atom, fact, bindings)
            if merged is None:
                continue
            proof = self.proofs.get((atom.predicate, fact))
            if proof is None:
                proof = Proof(atom=Atom(atom.predicate, fact))
            out.append((merged, proof))
        return out

    def _apply_rule(self, rule: Rule) -> bool:
        states: list[tuple[dict[str, str], tuple[Proof, ...]]] = [({}, ())]
        for atom in rule.body:
            next_states: list[tuple[dict[str, str], tuple[Proof, ...]]] = []
            for bindings, premises in states:
                for merged, proof in self._matches(atom, bindings):
                    next_states.append((merged, premises + (proof,)))
            states = next_states
            if not states:
                return False

        added = False
        for bindings, premises in states:
            for atom in rule.negated:
                if any(True for _merged, _proof in self._matches(atom, bindings)):
                    break
            else:
                head = _instantiate(rule.head, bindings)
                if head is None:
                    continue
                key = (head.predicate, head.args)
                if key in self.proofs:
                    continue
                self.facts.setdefault(head.predicate, set()).add(head.args)
                self.proofs[key] = Proof(
                    atom=head,
                    rule=rule.name or rule.format(),
                    premises=premises,
                )
                added = True
        return added

    # ---- 查询 ------------------------------------------------------------
    def query(self, value: Any, limit: int = 20) -> list[QueryResult]:
        self.derive()
        try:
            atom = parse_atom(value)
        except ValueError:
            return []
        results: list[QueryResult] = []
        for fact in self.facts.get(atom.predicate, ()):
            merged = _unify(atom, fact, {})
            if merged is None:
                continue
            proof = self.proofs.get((atom.predicate, fact))
            if proof is None:
                proof = Proof(atom=Atom(atom.predicate, fact))
            results.append(QueryResult(bindings=merged, proof=proof))
            if len(results) >= max(1, int(limit)):
                break
        results.sort(key=lambda item: item.proof.atom.format())
        return results

    def query_first(self, value: Any) -> QueryResult | None:
        results = self.query(value, limit=1)
        return results[0] if results else None

    def predicates(self) -> list[str]:
        return sorted(self.facts)

    def stats(self) -> dict:
        return {
            "facts": sum(len(bucket) for bucket in self.facts.values()),
            "predicates": len(self.facts),
            "rules": len(self.rules),
            "rounds": self.rounds,
            "errors": list(self.errors),
        }

    # ---- 证明 ------------------------------------------------------------
    def explain(self, proof: Proof, limit: int = MAX_PROOF_STEPS) -> list[str]:
        lines: list[str] = []

        def walk(item: Proof, depth: int = 0) -> None:
            if len(lines) >= limit or depth > 12:
                return
            if not item.premises:
                line = f"事实:{item.atom.format()}"
                if line not in lines:
                    lines.append(line)
                return
            for premise in item.premises:
                walk(premise, depth + 1)
            premises = "、".join(premise.atom.format() for premise in item.premises)
            line = (
                f"由「{premises}」按规则「{item.rule or '规则'}」"
                f"推出「{item.atom.format()}」"
            )
            if line not in lines:
                lines.append(line)

        walk(proof)
        if len(lines) >= limit:
            lines = lines[:limit]
            lines.append(f"……证明树超过 {limit} 步，已截断")
        return lines


__all__ = [
    "MAX_ROUNDS",
    "MAX_FACTS",
    "MAX_PROOF_STEPS",
    "Atom",
    "Rule",
    "Proof",
    "QueryResult",
    "DatalogEngine",
    "parse_atom",
    "parse_rule",
    "is_variable",
]
