"""受限算术求值器:不使用 eval / exec。

为什么不用 eval:`eval("__import__('os').system(...)")` 这种事在"把用户输入
当算式"的系统里是真实事故来源。这里的解析器只认识数字、四则运算、括号、乘方、
一元正负号,遇见别的字符直接抛 SafeEvalError —— 白名单,不是黑名单。

支持: + - * / // % ** () 空格,一元 +/-
拒绝: 变量、函数调用、属性访问、比较、任何其它字符
"""

from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"\s*(?:(\d+\.\d+|\d+\.|\.\d+|\d+)|(\*\*|[-+*/%()]))")
_ALLOWED = set("0123456789.+-*/%() \t")


class SafeEvalError(ValueError):
    """表达式不在白名单里,或语法不成立。"""


def _tokenize(expr: str) -> list[str]:
    if not expr or not expr.strip():
        raise SafeEvalError("表达式为空")
    bad = set(expr) - _ALLOWED
    if bad:
        raise SafeEvalError(f"含不允许的字符:{''.join(sorted(bad))}")
    tokens: list[str] = []
    pos = 0
    while pos < len(expr):
        m = _TOKEN_RE.match(expr, pos)
        if not m or m.end() == pos:
            if expr[pos].isspace():
                pos += 1
                continue
            raise SafeEvalError(f"无法识别的片段:{expr[pos:]!r}")
        number, op = m.group(1), m.group(2)
        if number is not None:
            tokens.append(number)
        elif op is not None:
            tokens.append(op)
        pos = m.end()
    if not tokens:
        raise SafeEvalError("表达式为空")
    return tokens


class _Parser:
    """递归下降:expr -> term (('+'|'-') term)* ; term -> unary (('*'|'/'|'%') unary)* ;
    unary -> ('+'|'-') unary | power ; power -> atom ('**' unary)? ; atom -> number | '(' expr ')'"""

    def __init__(self, tokens: list[str]) -> None:
        self.tokens = tokens
        self.i = 0

    def peek(self) -> str | None:
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def next(self) -> str:
        tok = self.peek()
        if tok is None:
            raise SafeEvalError("表达式意外结束")
        self.i += 1
        return tok

    def parse(self) -> float:
        value = self.expr()
        if self.peek() is not None:
            raise SafeEvalError(f"表达式尾部多余:{self.peek()!r}")
        return value

    def expr(self) -> float:
        value = self.term()
        while self.peek() in ("+", "-"):
            op = self.next()
            rhs = self.term()
            value = value + rhs if op == "+" else value - rhs
        return value

    def term(self) -> float:
        value = self.unary()
        while self.peek() in ("*", "/", "%"):
            op = self.next()
            rhs = self.unary()
            if op == "*":
                value *= rhs
            elif op == "/":
                if rhs == 0:
                    raise SafeEvalError("除以零")
                value /= rhs
            else:
                if rhs == 0:
                    raise SafeEvalError("对零取模")
                value = value - rhs * int(value / rhs)
        return value

    def unary(self) -> float:
        tok = self.peek()
        if tok == "-":
            self.next()
            return -self.unary()
        if tok == "+":
            self.next()
            return self.unary()
        return self.power()

    def power(self) -> float:
        base = self.atom()
        if self.peek() == "**":
            self.next()
            exp = self.unary()
            if abs(exp) > 64:
                raise SafeEvalError(f"指数过大:{exp}")
            return float(base) ** float(exp)
        return base

    def atom(self) -> float:
        tok = self.next()
        if tok == "(":
            value = self.expr()
            if self.peek() != ")":
                raise SafeEvalError("括号不匹配")
            self.next()
            return value
        if tok in ("+", "-", "*", "/", "%", "**", ")"):
            raise SafeEvalError(f"运算位置不对:{tok!r}")
        try:
            return float(tok)
        except ValueError as exc:  # pragma: no cover - tokenizer 已保证
            raise SafeEvalError(f"不是数字:{tok!r}") from exc


def safe_eval(expr: str) -> float:
    """求值。异常统一为 SafeEvalError,调用方不需要认识内部类型。"""
    try:
        return _Parser(_tokenize(expr)).parse()
    except SafeEvalError:
        raise
    except ZeroDivisionError as exc:
        raise SafeEvalError("除以零") from exc
    except RecursionError as exc:
        raise SafeEvalError("表达式嵌套过深") from exc


def format_number(value: float) -> str:
    """结果格式化:整数不留 .0,小数最多 6 位并去掉尾随零。"""
    if value != value or value in (float("inf"), float("-inf")):
        return str(value)
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.6f}".rstrip("0").rstrip(".")
