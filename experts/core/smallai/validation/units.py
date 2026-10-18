"""单位换算表:只放能被验证的**精确**定义。

两类特例单独处理,因为它们不是简单的比例关系:
  · 温度:仿射变换(°C/°F/K),不能用一个 factor 解决;
  · 时间里的"月/年":长度不固定,所以**故意不放进表里** ——
    遇到它宁可报"不支持",也不要给一个看着对的数。

不认识的单位一律抛 ConversionError,由校验层转成一条明确的错误,
而不是猜一个数出来。
"""

from __future__ import annotations

# 比例类:1 单位 = factor 基准单位
#   基准:length -> m,mass -> g,time -> s,data -> byte
_LINEAR: dict[str, tuple[str, float]] = {
    # 长度
    "m": ("length", 1.0),
    "km": ("length", 1000.0),
    "dm": ("length", 0.1),
    "cm": ("length", 0.01),
    "mm": ("length", 0.001),
    "um": ("length", 1e-6),
    "nmi": ("length", 1852.0),          # 海里(国际)
    "ft": ("length", 0.3048),           # 国际英尺
    "inch": ("length", 0.0254),         # 国际英寸
    "chi": ("length", 1 / 3),           # 市尺 = 1/3 米
    "cun": ("length", 1 / 30),          # 市寸 = 1/30 米
    "li": ("length", 500.0),            # 市里 = 500 米
    # 质量
    "g": ("mass", 1.0),
    "kg": ("mass", 1000.0),
    "mg": ("mass", 0.001),
    "t": ("mass", 1_000_000.0),
    "jin": ("mass", 500.0),             # 市斤 = 500 克
    "liang": ("mass", 50.0),            # 市两 = 50 克
    "lb": ("mass", 453.59237),          # 国际磅
    "oz": ("mass", 28.349523125),       # 国际盎司
    # 时间(月/年不进来:长度不固定)
    "s": ("time", 1.0),
    "min": ("time", 60.0),
    "h": ("time", 3600.0),
    "day": ("time", 86400.0),
    "week": ("time", 604800.0),
    # 数据量(按二进制口径,写清楚免得争)
    "byte": ("data", 1.0),
    "kb": ("data", 1024.0),
    "mb": ("data", 1024.0 ** 2),
    "gb": ("data", 1024.0 ** 3),
}

TEMPERATURE_UNITS = ("c", "f", "k")


class ConversionError(ValueError):
    pass


def convert(value: float, from_unit: str, to_unit: str) -> float:
    frm, to = from_unit.lower(), to_unit.lower()

    if frm in TEMPERATURE_UNITS or to in TEMPERATURE_UNITS:
        return _convert_temperature(value, frm, to)

    if frm not in _LINEAR:
        raise ConversionError(f"未知单位:{from_unit}")
    if to not in _LINEAR:
        raise ConversionError(f"未知单位:{to_unit}")

    kind_a, factor_a = _LINEAR[frm]
    kind_b, factor_b = _LINEAR[to]
    if kind_a != kind_b:
        raise ConversionError(f"量纲不同:{frm}({kind_a}) -> {to}({kind_b})")
    return value * factor_a / factor_b


def _convert_temperature(value: float, frm: str, to: str) -> float:
    if frm not in TEMPERATURE_UNITS or to not in TEMPERATURE_UNITS:
        other = frm if frm not in TEMPERATURE_UNITS else to
        raise ConversionError(f"温度不能与 {other} 直接换算")
    if frm == to:
        return value
    # 先统一到摄氏度
    if frm == "c":
        celsius = value
    elif frm == "f":
        celsius = (value - 32) * 5 / 9
    else:                                   # kelvin
        celsius = value - 273.15
        if celsius < -273.15:
            raise ConversionError("低于绝对零度")
    if to == "c":
        return celsius
    if to == "f":
        return celsius * 9 / 5 + 32
    return celsius + 273.15


def supported_kinds() -> dict[str, list[str]]:
    """给 CLI / 帮助文本用的"支持哪些量纲与单位"。"""
    out: dict[str, list[str]] = {}
    for unit, (kind, _factor) in _LINEAR.items():
        out.setdefault(kind, []).append(unit)
    out["temperature"] = list(TEMPERATURE_UNITS)
    return {k: sorted(v) for k, v in sorted(out.items())}
