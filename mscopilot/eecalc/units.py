"""Units: per-quantity unit lists, conversion to base SI units, input parsing and
engineering-notation display.

Every value inside the calculator is stored in base SI units (Ω, H, F, Hz, V, A, s, W,
rad ...). Units only matter at the edges: parsing what the user typed and displaying
results.
"""
from __future__ import annotations

import math

# quantity -> (base symbol, [(unit label, factor to base), ...], allow SI prefixes on output)
QUANTITIES: dict[str, tuple[str, list[tuple[str, float]], bool]] = {
    "resistance": ("Ω", [("Ω", 1.0), ("kΩ", 1e3), ("MΩ", 1e6)], True),
    "impedance": ("Ω", [("Ω", 1.0), ("kΩ", 1e3), ("MΩ", 1e6)], True),
    "reactance": ("Ω", [("Ω", 1.0), ("kΩ", 1e3), ("MΩ", 1e6)], True),
    "conductance": ("S", [("S", 1.0), ("mS", 1e-3), ("µS", 1e-6)], True),
    "inductance": ("H", [("H", 1.0), ("mH", 1e-3), ("µH", 1e-6)], True),
    "capacitance": ("F", [("F", 1.0), ("µF", 1e-6), ("nF", 1e-9), ("pF", 1e-12)], True),
    "frequency": ("Hz", [("Hz", 1.0), ("kHz", 1e3), ("MHz", 1e6)], True),
    "angular_frequency": ("rad/s", [("rad/s", 1.0), ("krad/s", 1e3)], True),
    "voltage": ("V", [("V", 1.0), ("mV", 1e-3), ("kV", 1e3)], True),
    "current": ("A", [("A", 1.0), ("mA", 1e-3), ("µA", 1e-6)], True),
    "time": ("s", [("s", 1.0), ("ms", 1e-3), ("µs", 1e-6)], True),
    "power": ("W", [("W", 1.0), ("mW", 1e-3), ("kW", 1e3), ("MW", 1e6)], True),
    "reactive_power": ("var", [("var", 1.0), ("kvar", 1e3), ("Mvar", 1e6)], True),
    "apparent_power": ("VA", [("VA", 1.0), ("kVA", 1e3), ("MVA", 1e6)], True),
    "angle": ("rad", [("°", math.pi / 180.0), ("rad", 1.0)], False),
    "ratio": ("", [("", 1.0)], False),
    "decibel": ("dB", [("dB", 1.0)], False),
    "percent": ("%", [("%", 1.0)], False),
    "turns": ("turns", [("turns", 1.0)], False),
    "number": ("", [("", 1.0)], False),
}

_PREFIXES = [(1e9, "G"), (1e6, "M"), (1e3, "k"), (1.0, ""), (1e-3, "m"), (1e-6, "µ"), (1e-9, "n"), (1e-12, "p"), (1e-15, "f")]


class UnitError(ValueError):
    pass


def units_for(quantity: str) -> list[str]:
    return [u for u, _ in QUANTITIES[quantity][1]]


def factor(quantity: str, unit: str) -> float:
    for u, f in QUANTITIES[quantity][1]:
        if u == unit:
            return f
    raise UnitError(f"'{unit}' is not a unit of {quantity.replace('_', ' ')} (use {', '.join(units_for(quantity)) or 'no unit'})")


def to_base(value: float, quantity: str, unit: str) -> float:
    return value * factor(quantity, unit)


def from_base(value: float, quantity: str, unit: str) -> float:
    return value / factor(quantity, unit)


def parse_number(text: str) -> float:
    """Parse what the user typed. Accepts 4.7, -3, 1e-9, 4.7E3, 1,000 (thousands commas) and
    a leading '+'. Rejects anything else rather than guessing."""
    s = (text or "").strip().replace("−", "-").replace(" ", "")
    if not s:
        raise UnitError("empty")
    if "," in s:
        parts = s.split(",")
        if all(p.isdigit() and len(p) == 3 for p in parts[1:]) and parts[0].lstrip("+-").replace(".", "", 1).isdigit():
            s = s.replace(",", "")
        else:
            raise UnitError(f"'{text}' is not a number (use a dot for decimals)")
    try:
        v = float(s)
    except ValueError as e:
        raise UnitError(f"'{text}' is not a number") from e
    if math.isnan(v) or math.isinf(v):
        raise UnitError(f"'{text}' is not a finite number")
    return v


def fmt(value, quantity: str = "number", decimals: int = 3, angle_unit: str = "°") -> str:
    """Format a base-unit value for display with an engineering prefix."""
    if value is None:
        return "—"
    if isinstance(value, complex):
        return fmt_complex(value, quantity, decimals, angle_unit)
    v = float(value)
    if math.isnan(v):
        return "undefined"
    base, _units, prefixed = QUANTITIES.get(quantity, ("", [], False))
    if math.isinf(v):
        return ("−∞" if v < 0 else "∞") + (f" {base}" if base else "")
    if quantity == "angle":
        if angle_unit == "rad":
            return f"{v:.{decimals}f} rad"
        return f"{math.degrees(v):.{decimals}f}°"
    if quantity == "percent":
        return f"{v:.{decimals}f} %"
    if quantity == "decibel":
        return f"{v:.{decimals}f} dB"
    if quantity == "turns":
        return f"{v:.{decimals}g} turns" if v != int(v) else f"{int(v)} turns"
    if not prefixed or v == 0:
        s = _plain(v, decimals)
        return f"{s} {base}".strip()
    a = abs(v)
    for f, p in _PREFIXES:
        if a >= f * (1 - 0.5 * 10 ** (-decimals - 3)):
            scaled = v / f
            # rounding can push 999.9995 k to 1000.000 k -> move up one prefix
            if abs(round(scaled, decimals)) >= 1000 and f < 1e9:
                continue
            return f"{scaled:.{decimals}f} {p}{base}"
    return f"{v:.{decimals}e} {base}"


def _plain(v: float, decimals: int) -> str:
    a = abs(v)
    if a != 0 and (a < 10 ** (-decimals) or a >= 1e9):
        return f"{v:.{decimals}e}"
    return f"{v:.{decimals}f}"


def fmt_complex(z: complex, quantity: str = "impedance", decimals: int = 3, angle_unit: str = "°") -> str:
    """Rectangular and polar form, e.g. '30.000 + j40.000 Ω = 50.000 Ω ∠53.130°'."""
    if any(math.isinf(x) for x in (z.real, z.imag)):
        return "∞ (open circuit)" if quantity in ("impedance", "resistance") else "∞"
    base = QUANTITIES.get(quantity, ("", [], False))[0]
    re_s = fmt(z.real, quantity, decimals).replace(f" {base}", "") if base else _plain(z.real, decimals)
    im = abs(z.imag)
    im_s = fmt(im, quantity, decimals).replace(f" {base}", "") if base else _plain(im, decimals)
    sign = "−" if z.imag < 0 else "+"
    mag = fmt(abs(z), quantity, decimals)
    ang = fmt(math.atan2(z.imag, z.real), "angle", decimals, angle_unit)
    return f"{re_s} {sign} j{im_s} {base}".rstrip() + f"  =  {mag} ∠{ang}"


def sig(v: float, digits: int = 6) -> str:
    """Compact number for substituted-value lines (keeps enough precision to re-check by hand)."""
    if v is None:
        return "—"
    if isinstance(v, complex):
        return f"({sig(v.real, digits)} {'−' if v.imag < 0 else '+'} j{sig(abs(v.imag), digits)})"
    if math.isinf(v):
        return "∞"
    if v == 0:
        return "0"
    s = f"{v:.{digits}g}"
    if "e" in s:
        m, e = s.split("e")
        return f"{m}×10^{int(e)}"
    return s


def q(v: float, quantity: str, digits: int = 6) -> str:
    """Value with its unit for substitution lines, e.g. '1000 Ω', '4×10^-9 F'."""
    base = QUANTITIES.get(quantity, ("", [], False))[0]
    if quantity == "angle":
        return f"{sig(math.degrees(v), digits)}°"
    return f"{sig(v, digits)} {base}".strip()
