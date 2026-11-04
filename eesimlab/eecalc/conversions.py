"""Complex-number arithmetic, exact SI-prefix/unit conversion, decibels."""
from __future__ import annotations

import cmath
import math
from decimal import Decimal, InvalidOperation, getcontext

from eesimlab.eecalc import cexpr
from eesimlab.eecalc.core import CalcError, Calculator, Field, Option, Result, register
from eesimlab.eecalc.units import sig

CAT = "Complex numbers & conversions"
getcontext().prec = 60

# --------------------------------------------------------------------------- prefixes
# (name, symbol, power of ten) - full SI set, quecto … quetta
SI_PREFIXES = [
    ("quetta", "Q", 30), ("ronna", "R", 27), ("yotta", "Y", 24), ("zetta", "Z", 21), ("exa", "E", 18),
    ("peta", "P", 15), ("tera", "T", 12), ("giga", "G", 9), ("mega", "M", 6), ("kilo", "k", 3),
    ("hecto", "h", 2), ("deca", "da", 1), ("(none)", "", 0), ("deci", "d", -1), ("centi", "c", -2),
    ("milli", "m", -3), ("micro", "µ", -6), ("nano", "n", -9), ("pico", "p", -12), ("femto", "f", -15),
    ("atto", "a", -18), ("zepto", "z", -21), ("yocto", "y", -24), ("ronto", "r", -27), ("quecto", "q", -30),
]
PREFIX_POWER = {sym: p for _, sym, p in SI_PREFIXES}
PREFIX_CHOICES = [(sym or "_", f"{name} ({sym or '—'}) 10^{p}") for name, sym, p in SI_PREFIXES]
CONV_UNITS = [("W", "watt (W)"), ("V", "volt (V)"), ("A", "ampere (A)"), ("Ω", "ohm (Ω)"), ("F", "farad (F)"),
              ("H", "henry (H)"), ("Hz", "hertz (Hz)"), ("s", "second (s)"), ("J", "joule (J)"), ("Wh", "watt-hour (Wh)"),
              ("VA", "volt-ampere (VA)"), ("var", "volt-ampere reactive (var)"), ("S", "siemens (S)"),
              ("C", "coulomb (C)"), ("Ah", "ampere-hour (Ah)"), ("Wb", "weber (Wb)"), ("T", "tesla (T)"),
              ("m", "metre (m)"), ("g", "gram (g)"), ("bit", "bit"), ("B", "byte (B)"), ("", "(no unit)")]


def exact(text: str) -> Decimal:
    s = (text or "").strip().replace("−", "-").replace(" ", "").replace(",", "")
    try:
        d = Decimal(s)
    except InvalidOperation as e:
        raise CalcError(f"'{text}' is not a number.") from e
    if not d.is_finite():
        raise CalcError(f"'{text}' is not a finite number.")
    return d


def dec_str(d: Decimal, decimals: int | None = None) -> str:
    """Plain decimal string with no binary rounding (e.g. 1000000000000)."""
    if decimals is not None:
        d = d.quantize(Decimal(1).scaleb(-decimals)) if d.adjusted() < 40 else d
    s = format(d.normalize(), "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".") or "0"
    return s


def sci_str(d: Decimal) -> str:
    if d == 0:
        return "0"
    t = d.normalize().as_tuple()
    digits = "".join(map(str, t.digits))
    exp10 = len(t.digits) - 1 + t.exponent
    mant = digits[0] + ("." + digits[1:] if len(digits) > 1 else "")
    return f"{'-' if t.sign else ''}{mant} × 10^{exp10}"


# --------------------------------------------------------------------------- complex arithmetic
def _cfmt(z: complex, dec: int = 6, angle_unit: str = "°") -> str:
    z = cexpr.clean(z)
    a = math.degrees(cmath.phase(z)) if angle_unit != "rad" else cmath.phase(z)
    return (f"{z.real:.{dec}f} {'−' if z.imag < 0 else '+'} j{abs(z.imag):.{dec}f}   =   "
            f"{abs(z):.{dec}f} ∠ {a:.{dec}f}{'°' if angle_unit != 'rad' else ' rad'}")


def _complex_calc(inp):
    au = inp.options["angle"]
    r = Result("Complex-number calculation")
    r.symbols = {"j": "imaginary unit (√−1)", "∠": "polar angle (measured from the positive real axis)",
                 "|z|": "magnitude", "θ": "angle", "a + jb": "rectangular form", "M∠θ": "polar form"}
    vars_ = {}
    for k in "ABCD":
        t = inp.text(k)
        if t:
            try:
                vars_[k] = cexpr.parse_value(t, au)
            except cexpr.ExprError as e:
                raise CalcError(f"{k}: {e}") from e
            z = vars_[k]
            r.steps.append(f"{k} = {t}  →  {_cfmt(z, 6, au)}")
        else:
            vars_[k] = None
    expr = inp.text("expr") or "A"
    try:
        z = cexpr.clean(cexpr.evaluate(expr, vars_, au))
    except cexpr.ExprError as e:
        raise CalcError(str(e)) from e
    r.add("Result (rectangular)", "a + jb", z, "number", primary=True,
          text="")  # formatted via units.fmt_complex (both forms) with the chosen decimals
    r.add("Real part a", "Re(result)", z.real, "number")
    r.add("Imaginary part b", "Im(result)", z.imag, "number", note="multiplies j; sign included")
    r.add("Magnitude |z|", "√(a² + b²)", abs(z), "number", primary=True)
    if z != 0:
        r.add("Angle θ", "atan2(b, a), quadrant-correct", cmath.phase(z), "angle", primary=True)
    else:
        r.add("Angle θ", "angle", None, text="undefined (zero has no direction)", primary=True)
    r.formulas += ["Rectangular → polar: |z| = √(a² + b²),  θ = atan2(b, a)",
                   "Polar → rectangular: a = |z| cos θ,  b = |z| sin θ",
                   "Multiply: magnitudes multiply, angles add.  Divide: magnitudes divide, angles subtract.",
                   "Add/subtract: use rectangular form (add real parts and imaginary parts separately)."]
    used = [k for k in "ABCD" if vars_[k] is not None and k in expr.upper()]
    if len(used) == 2:
        a, b = (vars_[used[0]], vars_[used[1]])
        x, y = used
        e = expr.replace(" ", "").upper()
        if e in (f"{x}*{y}", f"{x}/{y}") and a != 0 and b != 0:
            pa, pb = cmath.phase(a), cmath.phase(b)
            if au != "rad":
                pa, pb = math.degrees(pa), math.degrees(pb)
            u = "°" if au != "rad" else " rad"
            if "*" in e:
                r.substitutions.append(f"|{x}·{y}| = {abs(a):.6g} × {abs(b):.6g} = {abs(a) * abs(b):.6g};  "
                                       f"θ = {pa:.6g}{u} + ({pb:.6g}{u}) = {pa + pb:.6g}{u}")
            else:
                r.substitutions.append(f"|{x}/{y}| = {abs(a):.6g} / {abs(b):.6g} = {abs(a) / abs(b):.6g};  "
                                       f"θ = {pa:.6g}{u} − ({pb:.6g}{u}) = {pa - pb:.6g}{u}")
            r.steps.append("The angle is shown in (−180°, 180°]; add or subtract 360° for an equivalent angle.")
    r.substitutions.append(f"{expr} = {_cfmt(z, 6, au)}")
    r.assumptions.append("Angles typed after ∠ (or <, @, ang) use the unit selected above. Results keep full precision; "
                         "only the display is rounded to the chosen decimals.")
    return r


register(Calculator(
    "complex_calc", CAT, "Complex calculator (+ − × ÷, polar ↔ rectangular)", _complex_calc,
    fields=[
        Field("A", "A", "e.g. 8 + j3   or   8+3j", "text", "", "8 + j3", optional=True),
        Field("B", "B", "e.g. 29∠-72   (also 29<-72 or 29@-72)", "text", "", "29∠-72", optional=True),
        Field("C", "C", "optional operand", "text", "", "", optional=True),
        Field("D", "D", "optional operand", "text", "", "", optional=True),
        Field("expr", "Expression", "e.g. A*B, A/B, A+B, (A+B)/C, par(A,B), conj(A), sqrt(A), A^2 — or type numbers directly",
              "text", "", "A*B"),
    ],
    options=[Option("angle", "Angles in", [("°", "Degrees"), ("rad", "Radians")])],
    description="Add, subtract, multiply, divide and more with complex numbers in rectangular (a + jb) or polar (M∠θ) "
                "form; the answer is shown in both forms.",
    keywords="complex number multiply divide add subtract polar rectangular phasor j angle conjugate",
))

# move the existing converter into this category too
from eesimlab.eecalc import core as _core  # noqa: E402

_core.get("complex_convert").category = CAT


# --------------------------------------------------------------------------- prefix / unit converter
def _prefix_convert(inp):
    unit = inp.options["unit"]
    frm = inp.options["from"].replace("_", "")
    to = inp.options["to"].replace("_", "")
    d = exact(inp.text("value"))
    p_from, p_to = PREFIX_POWER[frm], PREFIX_POWER[to]
    out = d.scaleb(p_from - p_to)
    r = Result("SI prefix conversion")
    u = unit
    r.add("Result", f"value in {to or '(no prefix)'}{u}", None, primary=True, text=f"{dec_str(out)} {to}{u}".strip())
    r.add("Scientific", "same value, scientific notation", None, text=f"{sci_str(out)} {to}{u}".strip())
    base = d.scaleb(p_from)
    r.add("In base unit", f"value in {u or 'base units'}", None, text=f"{dec_str(base)} {u}".strip())
    r.formulas.append(f"value_to = value_from × 10^(power_from − power_to)")
    r.substitutions.append(f"{dec_str(d)} {frm}{u} × 10^({p_from} − ({p_to})) = {dec_str(d)} × 10^{p_from - p_to} = "
                           f"{dec_str(out)} {to}{u}")
    for name, sym, p in SI_PREFIXES:
        if sym in ("h", "da", "d", "c") and u not in ("m", "g", ""):
            continue  # rarely used outside length/mass
        r.steps.append(f"{dec_str(base.scaleb(-p)):>40s} {sym}{u}   ({name})")
    r.assumptions.append("Exact decimal arithmetic (no binary rounding): 1 GW = 1 000 000 000 000 mW exactly.")
    return r


register(Calculator(
    "prefix_convert", CAT, "Unit prefix converter (pico … giga … and beyond)", _prefix_convert,
    fields=[Field("value", "Value", "the number to convert (exact decimal, e.g. 2.5 or 1e-3)", "text", "", "1")],
    options=[Option("unit", "Unit", CONV_UNITS, "W"),
             Option("from", "From prefix", PREFIX_CHOICES, "G"),
             Option("to", "To prefix", PREFIX_CHOICES, "m")],
    description="Convert between any SI prefixes exactly, e.g. gigawatts to milliwatts; the steps list the value in every prefix.",
    keywords="convert prefix giga mega kilo milli micro nano pico unit magnitude watt",
))


# --------------------------------------------------------------------------- energy / misc unit conversions
UNIT_SETS = {
    "energy": [("J", 1), ("kJ", 1e3), ("MJ", 1e6), ("Wh", 3600), ("kWh", 3.6e6), ("MWh", 3.6e9), ("cal", 4.184),
               ("kcal", 4184), ("BTU", 1055.05585262), ("eV", 1.602176634e-19)],
    "power": [("W", 1), ("kW", 1e3), ("MW", 1e6), ("hp (mechanical)", 745.69987158227022), ("hp (metric)", 735.49875),
              ("BTU/h", 0.29307107017222), ("erg/s", 1e-7)],
    "charge": [("C", 1), ("mC", 1e-3), ("µC", 1e-6), ("Ah", 3600), ("mAh", 3.6), ("e (elementary)", 1.602176634e-19)],
    "length": [("m", 1), ("mm", 1e-3), ("cm", 1e-2), ("km", 1e3), ("in", 0.0254), ("ft", 0.3048), ("mil", 0.0000254),
               ("mi", 1609.344)],
    "area": [("m²", 1), ("mm²", 1e-6), ("cm²", 1e-4), ("in²", 0.00064516), ("circular mil", 5.067074790975e-10),
             ("kcmil", 5.067074790975e-7)],
    "temperature": [("°C", None), ("°F", None), ("K", None)],
    "magnetic": [("T", 1), ("mT", 1e-3), ("gauss", 1e-4)],
}


def _unit_convert(inp):
    fam = inp.options["family"]
    d = exact(inp.text("value"))
    units = UNIT_SETS[fam]
    frm = inp.options[f"from_{fam}"]
    r = Result(f"{fam.title()} conversion")
    if fam == "temperature":
        v = float(d)
        c = {"°C": v, "°F": (v - 32) * 5 / 9, "K": v - 273.15}[frm]
        if c < -273.15 - 1e-9:
            raise CalcError("That is below absolute zero.")
        vals = {"°C": c, "°F": c * 9 / 5 + 32, "K": c + 273.15}
        for k, val in vals.items():
            r.add(k, f"temperature in {k}", None, primary=k != frm, text=f"{val:.10g} {k}")
        r.formulas += ["°F = °C × 9/5 + 32", "K = °C + 273.15"]
        return r
    f_from = Decimal(str(dict(units)[frm]))
    base = d * f_from
    for name, f in units:
        val = base / Decimal(str(f))
        r.add(name, f"in {name}", None, primary=name != frm, text=f"{dec_str(val) if abs(val.adjusted()) < 15 else sci_str(val)} {name}")
    r.formulas.append(f"value_to = value × (factor_from / factor_to), factors relative to {units[0][0]}")
    r.assumptions.append("hp (mechanical) = 745.699872 W; BTU (IT) = 1055.05585 J; eV and e use the exact 2019 SI values.")
    return r


register(Calculator(
    "unit_convert", CAT, "Energy, power, charge, length, area, temperature", _unit_convert,
    fields=[Field("value", "Value", "the number to convert", "text", "", "1")],
    options=[Option("family", "Quantity", [(k, k.title()) for k in UNIT_SETS], "energy")]
    + [Option(f"from_{k}", f"From ({k})", [(u, u) for u, _ in v], v[0][0]) for k, v in UNIT_SETS.items()],
    description="Convert between common electrical-engineering units (J/Wh/kWh/BTU, W/hp, C/Ah/mAh, AWG-related areas …).",
    keywords="convert unit energy joule kwh wh btu horsepower hp charge mah ah length area circular mil temperature",
))


# --------------------------------------------------------------------------- decibels
DB_KINDS = [("power_ratio", "Power ratio ↔ dB  (10·log10)"), ("voltage_ratio", "Voltage/current ratio ↔ dB  (20·log10)"),
            ("dbm", "dBm ↔ watts"), ("dbw", "dBW ↔ watts"), ("dbv", "dBV ↔ volts (1 V ref)"),
            ("dbu", "dBu ↔ volts (0.7746 V RMS ref)"), ("dbmv", "dBmV ↔ volts (1 mV ref)")]
DB_REF = {"dbm": (1e-3, 10, "W"), "dbw": (1.0, 10, "W"), "dbv": (1.0, 20, "V"), "dbu": (math.sqrt(0.6), 20, "V"),
          "dbmv": (1e-3, 20, "V"), "power_ratio": (1.0, 10, ""), "voltage_ratio": (1.0, 20, "")}


def _db(inp):
    kind, dirn = inp.options["kind"], inp.options["dir"]
    ref, k, unit = DB_REF[kind]
    r = Result("Decibel conversion")
    if dirn == "to_db":
        x = inp.need("x", positive=True)
        v = k * math.log10(x / ref)
        r.add("dB", "level in decibels", v, "decibel", primary=True)
        r.formulas.append(f"dB = {k}·log10(x / {sig(ref)}{(' ' + unit) if unit else ''})")
        r.substitutions.append(f"dB = {k}·log10({sig(x)} / {sig(ref)}) = {v:.10g}")
    else:
        dbv = inp.need("db")
        x = ref * 10 ** (dbv / k)
        r.add("Value", f"linear value ({unit or 'ratio'})", x, {"W": "power", "V": "voltage", "": "ratio"}[unit], primary=True)
        r.formulas.append(f"x = {sig(ref)} × 10^(dB/{k})")
        r.substitutions.append(f"x = {sig(ref)} × 10^({sig(dbv)}/{k}) = {sig(x, 10)}")
    r.assumptions.append("Voltage dB assume equal impedances (20·log10 of a voltage ratio equals 10·log10 of the power ratio "
                         "only when both voltages are across the same resistance). dBu reference is 0.7746 V RMS (1 mW in 600 Ω).")
    return r


register(Calculator(
    "decibels", CAT, "Decibels (dB, dBm, dBW, dBV, dBu)", _db,
    fields=[Field("x", "x", "linear value (ratio, W or V as selected)", "number", "", "2", show_if=lambda o, u: o["dir"] == "to_db"),
            Field("db", "dB", "level in decibels", "decibel", "dB", "3", show_if=lambda o, u: o["dir"] == "from_db")],
    options=[Option("kind", "Type", DB_KINDS, "power_ratio"),
             Option("dir", "Direction", [("to_db", "Linear → dB"), ("from_db", "dB → linear")])],
    description="Convert ratios, powers and voltages to and from decibels.",
    keywords="decibel db dbm dbw dbv dbu gain ratio log",
))
