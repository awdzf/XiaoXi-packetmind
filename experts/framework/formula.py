"""用户教的公式与常数：记住一个能反复代入的算式，和一个能反复引用的符号。

写法（用户侧的约定）
--------------------
教公式： `#<名字>~<算式>:<变量1>,<变量2>,...`    例：`# 圆的面积~π*r^2:r`
用公式： `<名字>:<值1>,<值2>,...`                例：`圆的面积:3` → 28.274334
教常数： `#<名字>=<数值>`                        例：`# 光速=2.998e8`、`# 匝数=100`
用常数： 直接写在算式或参数里                     例：`# 能量~1/2*m*c^2:m`

为什么要单独一层
----------------
这不是"背一句话"，是**学会一种能反复用的东西**：教一次，之后代入任何值都能算。
所以必须满足：

1. **零依赖、不 eval**：求值走 `safe_eval.py`（只认识数字、四则、括号、乘方）。
   公式先做**文本代入**（变量换成 `(数值)`），再交给它 —— 代入过程本身就是审计材料。
2. **可核对**：答案里写出代入后的完整式子，任何人可以按一遍计算器。
3. **说清楚**：变量个数不对、算式里有不认识的符号、除以零 —— 都如实讲，不猜一个数。

符号兼容（中英文键盘混着打都能用）
----------------------------------
输入先过一道 `normalize_math`，再交给后面的逻辑：

1. **上标**先还原成乘方：`r²` → `r^(2)`、`10⁻³` → `10^(-3)`
   （必须在 NFKC 之前做 —— NFKC 会把 `²` 直接变成 `2`，`r²` 就变成 `r2` 了）；
2. **NFKC**：全角转半角、兼容字符折叠（`（），：～％＊＋－＝／＾` 全部转成 ASCII，
   `１２３` → `123`，`ε₀` → `ε0`）；
3. **数学符号**：`×` `·` `⋅` `∗` → `*`；`÷` `⁄` → `/`；`−`（U+2212）`–` `—` → `-`。

于是 `２*π×ｒ²` 与 `2*pi*r^2` 是同一个式子。

常数（内置的只收"单一符号且无歧义"的）
--------------------------------------
数学：π/pi、e、φ/phi
物理化学：ℏ/hbar、ε0/eps0、μ0/mu0、k_B、N_A、m_e、m_p、q_e、g_n

**故意不收 `c` / `G` / `h` / `g` / `R` / `F` 这些单字母**：它们同时是最常见的变量名
（半径 R、高 h、力 F、重力加速度 g）。收进来的后果是"少写一个变量名，它悄悄用了普朗克常数"
—— 算错了还不报错。这类要用的常数请自己定：`# c=299792458`，一条命令的事，而且看得见。

用户自己定的常数会**盖住**同名内置常数（并在教学时就说明这一点），变量又会盖住常数：
优先级 变量 > 用户常数 > 内置常数。三个层次都在答案里标出来。

边界（有意为之，不是没做完）
--------------------------
- 变量名/常数名用**英文字母**（`r`、`h`、`v0`、`c_light`）。中文名会跟算式里的中文撞车。
- 只支持**算式**，不支持带等号的方程（`a^2+b^2=c^2` 会被拒，并说明原因）。
- **不支持函数调用**（`sqrt`/`sin`/`max`）：要先定定义域与误差，那是另一件事。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal

from . import catalog  # noqa: F401  它负责把 core/ 放进 sys.path（与 persona 同一口径）
from smallai.validation.safe_eval import (  # noqa: E402  必须在 sys.path 调整之后
    SafeEvalError,
    format_number,
    safe_eval,
)

#: 公式/常数名：允许中文、字母、数字、下划线，2–20 字。名字是用户自己起的，不做语义判断。
NAME_RE = re.compile(r"^[\w\u4e00-\u9fff][\w\u4e00-\u9fff \-]{0,19}$")

#: 变量名/常数名：英文字母开头，可带数字和下划线（`r`、`h`、`v0`、`c_light`）。
VAR_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")

#: 算式里认得的标识符（用来核对"声明的变量"与"算式里真出现的字母"一致）。
IDENT_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*")

#: 教/用时的分隔符。经过 `normalize_math` 之后全角都变成半角，这里只留 ASCII + 顿号。
COLONS = (":",)
TILDES = ("~",)
COMMAS = (",", "、")

# ---------------------------------------------------------------- 符号归一

#: 上标字符 -> 普通字符。必须在 NFKC **之前**展开，否则 `r²` 会被折成 `r2`。
_SUPERSCRIPT = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻", "0123456789+-")
_SUPERSCRIPTS = set("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻")

#: 数学符号 -> 键盘符号。
_SYMBOLS = {
    "×": "*", "·": "*", "⋅": "*", "∙": "*", "∗": "*", "✕": "*", "✖": "*",
    "÷": "/", "⁄": "/", "∕": "/",
    "−": "-", "–": "-", "—": "-", "‐": "-", "﹣": "-",
    "＝": "=", "＋": "+", "－": "-", "＊": "*", "％": "%", "／": "/", "＾": "^",
}

#: 常数能用的非 ASCII 符号（`looks_like_formula` 判"像不像算式"时也算算式字符）。
_SYMBOL_CHARS = set("πφℏħεμ") | set(_SYMBOLS)


def _expand_superscripts(text: str) -> str:
    """把连续的上标还原成 `^(...)`：`r²` → `r^(2)`、`10⁻³` → `10^(-3)`。"""
    out: list[str] = []
    i = 0
    while i < len(text):
        if text[i] in _SUPERSCRIPTS:
            j = i
            while j < len(text) and text[j] in _SUPERSCRIPTS:
                j += 1
            out.append("^(" + text[i:j].translate(_SUPERSCRIPT) + ")")
            i = j
            continue
        out.append(text[i])
        i += 1
    return "".join(out)


def normalize_math(text: str) -> str:
    """把"中文键盘打出来的算式"归一成 ASCII 算式。幂等。"""
    folded = _expand_superscripts(text or "")
    folded = unicodedata.normalize("NFKC", folded)
    return "".join(_SYMBOLS.get(ch, ch) for ch in folded)


# ---------------------------------------------------------------- 常数

@dataclass(frozen=True)
class Constant:
    """一个内置常数：显示符号 + 数值 + 可接受的写法。"""

    symbol: str
    value: float
    aliases: tuple[str, ...]
    note: str = ""

    def spellings(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((self.symbol, *self.aliases)))


#: 数值取自 SI 2019 定义值或 CODATA 2018 推荐值；这里只是抄录，不做重新推算。
BUILTIN: tuple[Constant, ...] = (
    Constant("π", 3.141592653589793, ("pi",), "圆周率"),
    Constant("e", 2.718281828459045, (), "自然对数的底"),
    Constant("φ", 1.618033988749895, ("phi",), "黄金比 (1+√5)/2"),
    Constant("ℏ", 1.054571817e-34, ("ħ", "hbar"), "约化普朗克常数 J·s"),
    Constant("ε0", 8.8541878128e-12, ("eps0",), "真空介电常数 F/m"),
    Constant("μ0", 1.25663706212e-6, ("mu0",), "真空磁导率 H/m"),
    Constant("k_B", 1.380649e-23, ("kB", "k_b"), "玻尔兹曼常数 J/K（SI 定义值）"),
    Constant("N_A", 6.02214076e23, ("NA", "n_A"), "阿伏伽德罗常数 1/mol（SI 定义值）"),
    Constant("m_e", 9.1093837015e-31, ("me",), "电子质量 kg"),
    Constant("m_p", 1.67262192369e-27, ("mp",), "质子质量 kg"),
    Constant("q_e", 1.602176634e-19, ("qe",), "基本电荷 C（SI 定义值）"),
    Constant("g_n", 9.80665, ("gn",), "标准重力加速度 m/s²（定义值）"),
)


def builtin_table() -> dict[str, float]:
    """内置常数展开成 {写法: 数值}。`looks_like_formula` 与求值都用它。"""
    table: dict[str, float] = {}
    for constant in BUILTIN:
        for spelling in constant.spellings():
            table[spelling] = constant.value
    return table


#: 常用写法表（内置）。用户常数在运行期合并进来。
CONSTANTS: dict[str, float] = builtin_table()


def merge_constants(user: dict[str, float] | None = None) -> dict[str, float]:
    """内置 + 用户常数；**用户同名盖住内置**（教学时会提示）。"""
    table = dict(CONSTANTS)
    for name, value in (user or {}).items():
        if VAR_RE.match(name):
            table[name] = float(value)
    return table


def constant_symbol_of(name: str) -> str:
    """给一个写法找到它的显示符号（用于提示"你盖住了 π"）。"""
    for constant in BUILTIN:
        if name in constant.spellings():
            return constant.symbol
    return name


# ---------------------------------------------------------------- 公式


@dataclass(frozen=True)
class Formula:
    """一条学会了的公式。存进记忆数据包时就是这三样。"""

    name: str
    expr: str
    vars: tuple[str, ...]

    def call_hint(self) -> str:
        """写法提示：`圆的面积:<r>` —— 尖括号是占位符，提醒这里要填数。"""
        return f"{self.name}:" + ",".join(f"<{v}>" for v in self.vars)

    def describe(self) -> str:
        return f"{self.expr}（变量 {', '.join(self.vars)}）"

    def as_dict(self) -> dict:
        return {"name": self.name, "expr": self.expr, "vars": list(self.vars)}

    @classmethod
    def from_dict(cls, raw: dict) -> "Formula | None":
        name = str(raw.get("name") or "").strip()
        expr = str(raw.get("expr") or "").strip()
        variables = tuple(str(v).strip() for v in (raw.get("vars") or []) if str(v).strip())
        if not (name and expr and variables):
            return None
        return cls(name=name, expr=expr, vars=variables)


@dataclass(frozen=True)
class UserConstant:
    """用户 `# 名字=数值` 教进来的常数。"""

    name: str
    value: float
    expr: str = ""

    def as_dict(self) -> dict:
        return {"name": self.name, "value": self.value, "expr": self.expr or format_number(self.value)}

    @classmethod
    def from_dict(cls, raw: dict) -> "UserConstant | None":
        name = str(raw.get("name") or "").strip()
        try:
            value = float(raw.get("value"))
        except (TypeError, ValueError):
            return None
        if not name or not VAR_RE.match(name):
            return None
        return cls(name=name, value=value, expr=str(raw.get("expr") or ""))


# ---------------------------------------------------------------- 解析


def _split_once(text: str, separators: tuple[str, ...]) -> tuple[str, str] | None:
    for sep in separators:
        if sep in text:
            head, tail = text.split(sep, 1)
            return head.strip(), tail.strip()
    return None


def parse_teaching(body: str, constants: dict[str, float] | None = None) -> Formula | None:
    """`#<名字>~<算式>:<变量...>` -> Formula；不像公式或写错了都返回 None。

    只在该行**看起来就是公式**（`~` 后面有 `:`）时才插手；否则交回原来的
    `#问题~答案` 流程。写错的原因由 `explain_teaching` 负责说 —— 两个函数一套判断。
    """
    text = normalize_math(body).strip()
    if not any(t in text for t in TILDES):
        return None
    name, rest = _split_once(text, TILDES)
    if not name or not rest:
        return None
    pair = _split_once(rest, COLONS)
    if pair is None:
        return None                      # 没有冒号 = 普通"问题~答案"，不碰
    expr, var_text = pair
    variables = tuple(part for part in re.split(r"[,、]", var_text) if part)
    if _validate(name, expr, variables, constants):
        return None
    return Formula(name=name, expr=expr, vars=variables)


def _is_formula_char(ch: str) -> bool:
    return ch in "0123456789.+-*/%^()= \t" or ch in _SYMBOL_CHARS or ch in "{}(),_"


def looks_like_formula(body: str) -> bool:
    """这一行**像不像**在写公式。

    用来决定"写错了要报错"还是"这本来就是一句普通知识"。判据故意宽进严出：
    算式部分里 ≥60% 的字符是算式字符（含 π、φ、ℏ 这些符号），才算"想写公式"。

    这样 `#量子~薛定谔的猫是思想实验：把微观态放大到一只猫身上` 不会因为用了中文冒号
    就被当成写错的公式；而 `#勾股~a^2+b^2=c^2:a,b` 会被认出来并提示"不支持等号"。
    """
    text = normalize_math(body).strip()
    if not any(t in text for t in TILDES):
        return False
    _name, rest = _split_once(text, TILDES) or ("", "")
    pair = _split_once(rest, COLONS)
    if pair is None:
        return False
    chars = [ch for ch in pair[0] if not ch.isspace()]
    if not chars:
        return False
    return sum(1 for ch in chars if _is_formula_char(ch)) / len(chars) >= 0.6


def explain_teaching(body: str, constants: dict[str, float] | None = None) -> str | None:
    """这行**像**公式但写错了时说清哪里错；不像公式返回 None。"""
    text = normalize_math(body).strip()
    if not any(t in text for t in TILDES):
        return None
    name, rest = _split_once(text, TILDES)
    if not name or not rest:
        return None
    pair = _split_once(rest, COLONS)
    if pair is None:
        return None
    expr, var_text = pair
    variables = tuple(part for part in re.split(r"[,、]", var_text) if part)
    return _validate(name, expr, variables, constants)


def _validate(name: str, expr: str, variables: tuple[str, ...],
              constants: dict[str, float] | None = None) -> str | None:
    """返回问题说明；没有问题返回 None。"""
    table = merge_constants(constants)
    if not NAME_RE.match(name):
        return f"公式名「{name}」不合法（2–20 字，中文/字母/数字/下划线）"
    if not variables:
        return "冒号后面要写变量名，例如 `圆的面积~π*r^2:r`"
    for variable in variables:
        if not VAR_RE.match(variable):
            return (f"变量名「{variable}」得用英文字母（r、h、v0 这样）——"
                    "中文变量会和算式里的中文撞车，这一版不支持")
    if len(set(variables)) != len(variables):
        return "变量名有重复"
    if "=" in expr:
        return "只支持算式，不支持带等号的方程（`a^2+b^2=c^2` 这种请改写成两边移项）"
    if re.search(r"[\u4e00-\u9fff]", expr):
        return "算式里不能出现中文 —— 函数调用（根号/sqrt/max 之类）这一版不支持，只能用 + - * / % ** ()"
    if re.search(r"[^\w\s.+\-*/%^()]", expr):
        bad = re.search(r"[^\w\s.+\-*/%^()]", expr)
        return f"算式里有不认识的符号「{bad.group(0) if bad else ''}」"
    used = set(IDENT_RE.findall(expr))
    unknown = sorted(used - set(variables) - set(table))
    for ident in unknown:
        if re.search(rf"\b{re.escape(ident)}\s*\(", expr):
            return (f"「{ident}(...)」是函数调用，这一版不支持 —— "
                    "只能用 + - * / % ** 和括号")
    if unknown:
        return (f"算式里出现了没声明的变量「{', '.join(unknown)}」——"
                f"请在冒号后面列全：{name}~{expr}:{','.join(v for v in variables) or '…'}"
                f"（常数只有这些：{', '.join(sorted(set(table)))[:80]}…）")
    unused = [v for v in variables if v not in used]
    if unused:
        return f"变量「{', '.join(unused)}」在算式里没用到"
    # 语法体检：变量先按 1 代进去跑一遍。
    # "除以零/对零取模"不算错 —— 那只是这个点在定义域外（如 1/(x-1) 在 x=1）。
    try:
        safe_eval(_substitute(expr, {v: 1.0 for v in variables}, table))
    except SafeEvalError as exc:
        if "除以零" not in str(exc) and "取模" not in str(exc):
            return f"算式读不懂：{exc}"
    return None


def teach_warnings(formula: Formula, constants: dict[str, float] | None = None) -> tuple[str, ...]:
    """教学时该提醒但不该拒绝的事：变量盖住了同名常数、用户常数盖住了内置常数。"""
    builtin = CONSTANTS
    notes: list[str] = []
    shadowed = [v for v in formula.vars if v in builtin]
    if shadowed:
        notes.append(
            "变量 " + "、".join(shadowed) + " 盖住了同名内置常数（"
            + "、".join(f"{v}={format_number(builtin[v])}" for v in shadowed)
            + "）—— 这条公式里它是你的变量"
        )
    return tuple(notes)


def parse_call(text: str) -> tuple[str, tuple[str, ...]] | None:
    """`<名字>:<值1>,<值2>` -> (名字, 值...)。

    只在**开头**是"名字 + 冒号"时算调用；`:` 开头的（`:ctx` 这类命令）不算。
    值可以是算式（`3/2`、`2*pi`），交给求值器算。
    """
    item = normalize_math(text).strip()
    if not item or item.startswith(COLONS):
        return None
    pair = _split_once(item, COLONS)
    if pair is None:
        return None
    name, args_text = pair
    if not name or not NAME_RE.match(name):
        return None
    args = tuple(part for part in re.split(r"[,、]", args_text) if part)
    return name, args


# ---------------------------------------------------------------- 常数教学


def parse_constant(body: str) -> tuple[UserConstant, str] | None:
    """`#<名字>=<数值>` -> (常数, "") 或 (None 说明, 原因)。

    数值可以是算术式（`1/3`、`2*pi`）或科学计数法（`2.998e8`）；
    科学计数法用 `float()` 试，其它交给受限求值器 —— 两条路都不碰 `eval`。
    """
    text = normalize_math(body).strip()
    if "=" not in text:
        return None
    left, right = text.split("=", 1)
    left, right = left.strip(), right.strip()
    if not left or not right:
        return None
    if not VAR_RE.match(left):
        if re.search(r"[\u4e00-\u9fff]", left):
            return None, (f"常数名「{left}」得用英文字母（c_light、k1 这样）——"
                          "常数要写进算式，而算式里只能有英文字母")
        return None, f"常数名「{left}」不合法（英文字母开头，字母/数字/下划线）"
    value, problem = parse_number(right)
    if problem:
        return None, f"「{right}」不是一个能算的数：{problem}"
    return UserConstant(name=left, value=value, expr=right), ""


def parse_number(text: str, constants: dict[str, float] | None = None) -> tuple[float, str]:
    """把一个数或算式求值。返回 (数值, 问题说明)。"""
    table = merge_constants(constants)
    raw = normalize_math(text).strip()
    if not raw:
        return 0.0, "空值"
    try:
        return float(raw), ""              # 科学计数法 2.998e8 走这条
    except ValueError:
        pass
    try:
        return safe_eval(_substitute(raw, {}, table)), ""
    except SafeEvalError as exc:
        return 0.0, str(exc)


def looks_like_constant(body: str) -> bool:
    """这行像不像在定义常数：`名字=一个能算的数`。"""
    text = normalize_math(body).strip()
    if "=" not in text:
        return False
    left, right = text.split("=", 1)
    if not VAR_RE.match(left.strip()) or not right.strip():
        return False
    value, problem = parse_number(right)
    return problem == ""


# ---------------------------------------------------------------- 求值


@dataclass(frozen=True)
class Result:
    """一次代入求值的结果。`substituted` 是给人核对的那一行。"""

    ok: bool
    value: float | None = None
    substituted: str = ""
    text: str = ""
    error: str = ""
    used_constants: tuple[str, ...] = ()


def _display(value: float) -> str:
    """给人看的结果格式：很大的数用科学计数法，其余按 `format_number`。"""
    number = float(value)
    if number != 0 and (abs(number) >= 1e15 or abs(number) < 1e-6):
        return f"{number:.6g}"
    return format_number(number)


def _num(value: float) -> str:
    """代入时用完整精度；整数就写成整数（(3) 比 (3.0) 好读）。

    特别地：**不能留下科学计数法**。`safe_eval` 的白名单里没有 `e`，
    `1.054571817e-34` 会被它判成"含不允许的字符:e" —— `ℏ`、`m_e` 这些常数全都栽在这上面。
    所以指数形式的数要展开成完整小数（Decimal 给出的是精确展开，不是估算）。
    """
    number = float(value)
    if number == int(number) and abs(number) < 1e15:
        return repr(int(number))
    text = repr(number)
    if "e" in text or "E" in text:
        return format(Decimal(number), "f")
    return text


def _substitute(expr: str, values: dict[str, float],
                constants: dict[str, float] | None = None) -> str:
    """把算式里的变量换成 `(数值)`、常数换成数值，`^` 换成 `**`。

    顺序：**变量 > 用户/内置常数**（变量先替换，所以同名常数不会抢先）。
    """
    text = expr.replace("^", "**")
    # 用完整精度代入（不是显示精度）：π 截成 3.141593 会让 3^2 那次算错在第 6 位。
    for variable in sorted(values, key=len, reverse=True):
        text = re.sub(rf"\b{re.escape(variable)}\b", f"({_num(values[variable])})", text)
    table = merge_constants({} if constants is None else {
        k: v for k, v in constants.items() if k not in values
    })
    for constant in sorted(table, key=len, reverse=True):
        if constant in values:
            continue
        if constant.isascii():
            text = re.sub(rf"\b{re.escape(constant)}\b", _num(table[constant]), text)
        else:
            text = text.replace(constant, _num(table[constant]))
    return text


def evaluate(formula: Formula, args: tuple[str, ...],
             constants: dict[str, float] | None = None) -> Result:
    """按用户给的值求值。任何不对劲都返回 ok=False + 一句人话。"""
    table = merge_constants(constants)
    if len(args) != len(formula.vars):
        wanted = ", ".join(formula.vars)
        example = ",".join("3" for _ in formula.vars)
        return Result(
            ok=False,
            error=(f"「{formula.name}」要 {len(formula.vars)} 个值（{wanted}），"
                   f"你给了 {len(args)} 个。写法像这样：{formula.name}:{example}"),
        )
    values: dict[str, float] = {}
    for variable, raw in zip(formula.vars, args):
        number, problem = parse_number(raw, constants)
        if problem:
            return Result(ok=False, error=f"「{variable}={raw}」不是一个能算的数：{problem}")
        values[variable] = number
    try:
        substituted = _substitute(formula.expr, values, constants)
        value = safe_eval(substituted)
    except SafeEvalError as exc:
        return Result(ok=False, substituted=formula.expr, error=f"算不下去：{exc}")
    if value != value or value in (float("inf"), float("-inf")):
        return Result(ok=False, substituted=substituted, error="结果是无穷大/未定义")
    used = tuple(
        sorted({name for name in table if name not in values
                and re.search(rf"\b{re.escape(name)}\b", formula.expr) if name.isascii()})
    )
    given = "，".join(f"{v}={_display(values[v])}" for v in formula.vars)
    detail = f"（常数 {', '.join(used)}）" if used else ""
    text = (
        f"{formula.name}（{given}） = {_display(value)}{detail}\n"
        f"代入后：{substituted}"
    )
    return Result(ok=True, value=value, substituted=substituted, text=text,
                  used_constants=used)


def entry_answer(formula: Formula, constants: dict[str, float] | None = None) -> str:
    """写进记忆条目的答案正文（`#?` 回放、以及被普通检索命中时看到的就是它）。"""
    table = merge_constants(constants)
    used = sorted({c for c in table if re.search(rf"\b{re.escape(c)}\b", formula.expr)})
    lines = [
        f"你教我的公式：{formula.name} = {formula.expr}",
        f"要算的时候这样写：{formula.call_hint()}",
        f"例如：{formula.name}:" + ",".join("1" for _ in formula.vars) + " → 代入求值",
    ]
    if used:
        lines.append("式子里用到的常数：" + "，".join(f"{c}={format_number(table[c])}" for c in used))
    return "\n".join(lines)


def constant_answer(constant: UserConstant) -> str:
    """用户常数写进记忆条目的答案正文。"""
    return (
        f"你定的常数：{constant.name} = {constant.expr or format_number(constant.value)}\n"
        f"数值：{_num(constant.value)}\n"
        "在公式里直接用这个名字就行，例如：" + f"{constant.name}*2"
    )


def formula_aliases(name: str) -> list[str]:
    """让公式在**正常对话**里也能被检索到（和教的一句话知识一样）。"""
    return [
        f"{name}是什么", f"{name}怎么算", f"{name}怎么求",
        f"{name}的公式", f"{name}公式", f"{name}公式是什么",
    ]


def constant_aliases(name: str) -> list[str]:
    return [f"{name}是多少", f"{name}是什么", f"{name}的值", f"{name}等于多少"]


__all__ = [
    "BUILTIN",
    "CONSTANTS",
    "Constant",
    "Formula",
    "Result",
    "UserConstant",
    "builtin_table",
    "constant_aliases",
    "constant_answer",
    "constant_symbol_of",
    "entry_answer",
    "evaluate",
    "explain_teaching",
    "formula_aliases",
    "looks_like_constant",
    "looks_like_formula",
    "merge_constants",
    "normalize_math",
    "parse_call",
    "parse_constant",
    "parse_number",
    "parse_teaching",
    "teach_warnings",
]
