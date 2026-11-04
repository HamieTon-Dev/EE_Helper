"""Stage 1a - basic electrical calculations."""
from __future__ import annotations

import math

from eesimlab.eecalc.core import CalcError, Calculator, Field, Option, Result, register
from eesimlab.eecalc.units import q, sig

CAT = "Basic electrical"


def ladder(source="dc", series=(), shunts=(), out=None, out_label="Vout", source_label="Vin", note=""):
    return {"kind": "ladder", "source": source, "source_label": source_label, "series": list(series),
            "shunts": [list(s) for s in shunts], "out": out, "out_label": out_label, "note": note}


# ======================================================================= Ohm's law
def _ohm(inp):
    u = inp.unknown
    r = Result("Ohm's law")
    r.symbols = {"V": "voltage across the resistor", "I": "current through the resistor", "R": "resistance",
                 "P": "power dissipated in the resistor"}
    if u == "V":
        i, R = inp.need("I"), inp.need("R", nonneg=True)
        v = r.add("V", "voltage", i * R, "voltage", primary=True)
        r.formulas.append("V = I × R")
        r.substitutions.append(f"V = {q(i, 'current')} × {q(R, 'resistance')} = {q(v, 'voltage')}")
    elif u == "I":
        v, R = inp.need("V"), inp.need("R", nonneg=True)
        if R == 0:
            if v == 0:
                raise CalcError("V and R are both zero: the current is undefined.")
            raise CalcError("R = 0 Ω with a non-zero voltage is a short circuit: the current would be infinite.")
        i = r.add("I", "current", v / R, "current", primary=True)
        r.formulas.append("I = V / R")
        r.substitutions.append(f"I = {q(v, 'voltage')} / {q(R, 'resistance')} = {q(i, 'current')}")
    else:
        v, i = inp.need("V"), inp.need("I")
        if i == 0:
            raise CalcError("I = 0 A: the resistance is infinite (open circuit) or undefined.")
        R = v / i
        if R < 0:
            raise CalcError("V and I have opposite signs, which would mean a negative resistance. Check the polarities.")
        r.add("R", "resistance", R, "resistance", primary=True)
        r.formulas.append("R = V / I")
        r.substitutions.append(f"R = {q(v, 'voltage')} / {q(i, 'current')} = {q(R, 'resistance')}")
    V, I = v, i
    p = r.add("P", "power", V * I, "power")
    r.formulas.append("P = V × I")
    r.substitutions.append(f"P = {q(V, 'voltage')} × {q(I, 'current')} = {q(p, 'power')}")
    r.assumptions.append("Linear (ohmic) resistor; DC values, or AC values of the same convention (e.g. both RMS).")
    return r


register(Calculator(
    "ohms_law", CAT, "Ohm's law", _ohm,
    fields=[
        Field("V", "V", "voltage across the resistor", "voltage", "V", "12", show_if=lambda o, u: u != "V"),
        Field("I", "I", "current through the resistor", "current", "mA", "10", show_if=lambda o, u: u != "I"),
        Field("R", "R", "resistance", "resistance", "kΩ", "1.2", show_if=lambda o, u: u != "R"),
    ],
    unknowns=[("I", "Current I"), ("V", "Voltage V"), ("R", "Resistance R")],
    description="V = I × R, solved for any one of the three, plus the power.",
    keywords="ohm voltage current resistance",
    diagram=lambda o, u: ladder("dc", shunts=[[("R", "R")]], out={"across": "shunt", "index": 0}, out_label="V",
                                source_label="V", note="current I flows through R"),
))


# ======================================================================= Power
PAIRS = [("VI", "V and I"), ("IR", "I and R"), ("VR", "V and R"), ("PV", "P and V"), ("PI", "P and I"), ("PR", "P and R")]


def _power(inp):
    pair = inp.options["pair"]
    r = Result("Power")
    r.symbols = {"P": "power", "V": "voltage", "I": "current", "R": "resistance"}
    if pair == "VI":
        V, I = inp.need("V"), inp.need("I")
        R = math.inf if I == 0 else V / I
        P = V * I
        r.formulas.append("P = V × I")
        r.substitutions.append(f"P = {q(V, 'voltage')} × {q(I, 'current')} = {q(P, 'power')}")
    elif pair == "IR":
        I, R = inp.need("I"), inp.need("R", nonneg=True)
        V, P = I * R, I * I * R
        r.formulas.append("P = I² × R")
        r.substitutions.append(f"P = ({q(I, 'current')})² × {q(R, 'resistance')} = {q(P, 'power')}")
    elif pair == "VR":
        V, R = inp.need("V"), inp.need("R", positive=True)
        I, P = V / R, V * V / R
        r.formulas.append("P = V² / R")
        r.substitutions.append(f"P = ({q(V, 'voltage')})² / {q(R, 'resistance')} = {q(P, 'power')}")
    elif pair == "PV":
        P, V = inp.need("P", nonneg=True), inp.need("V", nonzero=True)
        I = P / V
        R = V * V / P if P > 0 else math.inf
        r.formulas.append("I = P / V,  R = V² / P")
        r.substitutions.append(f"I = {q(P, 'power')} / {q(V, 'voltage')} = {q(I, 'current')}")
    elif pair == "PI":
        P, I = inp.need("P", nonneg=True), inp.need("I", nonzero=True)
        V = P / I
        R = P / (I * I)
        r.formulas.append("V = P / I,  R = P / I²")
        r.substitutions.append(f"V = {q(P, 'power')} / {q(I, 'current')} = {q(V, 'voltage')}")
    else:
        P, R = inp.need("P", nonneg=True), inp.need("R", positive=True)
        V = math.sqrt(P * R)
        I = math.sqrt(P / R)
        r.formulas.append("V = √(P × R),  I = √(P / R)")
        r.substitutions.append(f"V = √({q(P, 'power')} × {q(R, 'resistance')}) = {q(V, 'voltage')}")
        r.substitutions.append(f"I = √({q(P, 'power')} / {q(R, 'resistance')}) = {q(I, 'current')}")
    if R < 0:
        raise CalcError("These values imply a negative resistance (a source, not a load). Check the signs.")
    r.add("P", "power", P, "power", primary="P" not in pair)
    r.add("V", "voltage", V, "voltage", primary="V" not in pair)
    r.add("I", "current", I, "current", primary="I" not in pair)
    r.add("R", "resistance", R, "resistance", primary="R" not in pair,
          text="∞ (open circuit)" if math.isinf(R) else "")
    r.assumptions.append("Resistive load. For sinusoidal AC use RMS values (P = Vrms × Irms); peak values give 2× the power.")
    return r


register(Calculator(
    "power", CAT, "Power (P, V, I, R)", _power,
    fields=[
        Field("P", "P", "power", "power", "W", "0.5", show_if=lambda o, u: "P" in o["pair"]),
        Field("V", "V", "voltage", "voltage", "V", "12", show_if=lambda o, u: "V" in o["pair"]),
        Field("I", "I", "current", "current", "mA", "50", show_if=lambda o, u: "I" in o["pair"]),
        Field("R", "R", "resistance", "resistance", "Ω", "220", show_if=lambda o, u: "R" in o["pair"]),
    ],
    options=[Option("pair", "Known values", PAIRS, "VI")],
    description="Any two of P, V, I, R give the other two.",
    keywords="power watt dissipation",
))


# ======================================================================= Series / parallel
COMPONENT = {"R": ("resistance", "kΩ", "R"), "L": ("inductance", "mH", "L"), "C": ("capacitance", "nF", "C")}
N_COMBO = 6


def _combo(inp):
    comp, conn = inp.options["component"], inp.options["connection"]
    qty, _unit, sym = COMPONENT[comp]
    vals = [(i, inp.values.get(f"x{i}")) for i in range(1, N_COMBO + 1)]
    vals = [(i, v) for i, v in vals if v is not None]
    if len(vals) < 2:
        raise CalcError("Enter at least two component values.")
    for i, v in vals:
        if v <= 0:
            raise CalcError(f"{sym}{i} must be greater than zero (a 0 value is a short/open, not a component).")
    names = [f"{sym}{i}" for i, _ in vals]
    xs = [v for _, v in vals]
    # resistors and inductors add in series; capacitors add in parallel
    adds = (conn == "series") != (comp == "C")
    r = Result(f"{'Series' if conn == 'series' else 'Parallel'} {qty}")
    if adds:
        total = sum(xs)
        r.formulas.append(f"{sym}eq = " + " + ".join(names))
        r.substitutions.append(f"{sym}eq = " + " + ".join(q(x, qty) for x in xs) + f" = {q(total, qty)}")
    else:
        total = 1.0 / sum(1.0 / x for x in xs)
        r.formulas.append(f"1/{sym}eq = " + " + ".join(f"1/{n}" for n in names))
        r.substitutions.append(f"{sym}eq = 1 / (" + " + ".join(f"1/{q(x, qty)}" for x in xs) + f") = {q(total, qty)}")
        if len(xs) == 2:
            r.steps.append(f"For two components: {sym}eq = {names[0]}·{names[1]} / ({names[0]} + {names[1]}).")
    r.add(f"{sym}eq", f"equivalent {qty}", total, qty, primary=True)
    r.symbols = {f"{sym}eq": f"equivalent {qty}"} | {n: f"{qty} {n}" for n in names}
    if comp == "L":
        r.assumptions.append("Ideal inductors with no mutual coupling (no shared magnetic flux).")
    elif comp == "C":
        r.assumptions.append("Ideal capacitors (no leakage or ESR).")
    else:
        r.assumptions.append("Ideal resistors.")
    return r


def _combo_fields():
    out = []
    for i in range(1, N_COMBO + 1):
        out.append(Field(f"x{i}", f"X{i}", f"component {i}",
                         lambda o: COMPONENT[o["component"]][0],
                         lambda o: COMPONENT[o["component"]][1],
                         {1: "1", 2: "2.2"}.get(i, ""), optional=i > 2))
    return out


register(Calculator(
    "series_parallel", CAT, "Series and parallel R, L, C", _combo,
    fields=_combo_fields(),
    options=[Option("component", "Component", [("R", "Resistors"), ("L", "Inductors (uncoupled)"), ("C", "Capacitors")]),
             Option("connection", "Connection", [("series", "Series"), ("parallel", "Parallel")])],
    description="Equivalent value of up to six ideal components. Blank fields are ignored.",
    keywords="series parallel equivalent resistance capacitance inductance combine total",
    diagram=lambda o, u: {"kind": "chain", "mode": o["connection"], "comp": o["component"], "count": 3},
))


# ======================================================================= Voltage divider
def _divider(inp):
    u = inp.unknown
    vin = inp.need("Vin")
    rl = inp.opt("RL", positive=True)
    r = Result("Voltage divider")
    r.symbols = {"Vin": "input voltage", "R1": "top resistor (input to output)", "R2": "bottom resistor (output to ground)",
                 "RL": "load resistor across R2", "Vout": "output voltage across R2", "R2∥RL": "R2 in parallel with the load"}
    if u == "Vout":
        r1, r2 = inp.need("R1", nonneg=True), inp.need("R2", nonneg=True)
        if r1 + r2 == 0:
            raise CalcError("R1 + R2 = 0 Ω shorts the source.")
    elif u == "R2":
        r1, vt = inp.need("R1", positive=True), inp.need("Vout_t")
        if vin == 0 or not (0 < vt / vin < 1):
            raise CalcError("The desired Vout must lie strictly between 0 and Vin (same sign as Vin).")
        req = r1 * vt / (vin - vt)  # required R2∥RL
        r.formulas.append("R2∥RL = R1 × Vout / (Vin − Vout)")
        r.substitutions.append(f"R2∥RL = {q(r1, 'resistance')} × {q(vt, 'voltage')} / ({q(vin, 'voltage')} − {q(vt, 'voltage')}) = {q(req, 'resistance')}")
        if rl is not None:
            if req >= rl:
                raise CalcError(f"Impossible with this load: R2∥RL must be {sig(req)} Ω, but RL alone is only {sig(rl)} Ω. "
                                "Lower R1 or use a buffer.")
            r2 = 1 / (1 / req - 1 / rl)
            r.formulas.append("R2 = 1 / (1/(R2∥RL) − 1/RL)")
            r.substitutions.append(f"R2 = 1 / (1/{q(req, 'resistance')} − 1/{q(rl, 'resistance')}) = {q(r2, 'resistance')}")
        else:
            r2 = req
        r.add("R2", "bottom resistor needed", r2, "resistance", primary=True)
    else:  # R1
        r2, vt = inp.need("R2", positive=True), inp.need("Vout_t")
        if vin == 0 or not (0 < vt / vin < 1):
            raise CalcError("The desired Vout must lie strictly between 0 and Vin (same sign as Vin).")
        r2l = r2 if rl is None else r2 * rl / (r2 + rl)
        r1 = r2l * (vin - vt) / vt
        r.formulas.append("R1 = (R2∥RL) × (Vin − Vout) / Vout")
        r.substitutions.append(f"R1 = {q(r2l, 'resistance')} × ({q(vin, 'voltage')} − {q(vt, 'voltage')}) / {q(vt, 'voltage')} = {q(r1, 'resistance')}")
        r.add("R1", "top resistor needed", r1, "resistance", primary=True)

    # analysis with the final values
    vu = vin * r2 / (r1 + r2) if (r1 + r2) > 0 else 0.0
    r.add("Vout (no load)", "output with no load", vu, "voltage", primary=(u == "Vout" and rl is None))
    r.formulas.append("Vout(no load) = Vin × R2 / (R1 + R2)")
    r.substitutions.append(f"Vout = {q(vin, 'voltage')} × {q(r2, 'resistance')} / ({q(r1, 'resistance')} + {q(r2, 'resistance')}) = {q(vu, 'voltage')}")
    if rl is not None:
        r2l = r2 * rl / (r2 + rl) if (r2 + rl) > 0 else 0.0
        vl = vin * r2l / (r1 + r2l) if (r1 + r2l) > 0 else 0.0
        r.add("R2∥RL", "R2 in parallel with the load", r2l, "resistance")
        r.add("Vout (loaded)", "output with RL connected", vl, "voltage", primary=(u == "Vout"))
        r.formulas.append("R2∥RL = R2 × RL / (R2 + RL);  Vout(loaded) = Vin × (R2∥RL) / (R1 + R2∥RL)")
        r.substitutions.append(f"R2∥RL = {q(r2, 'resistance')} × {q(rl, 'resistance')} / ({q(r2, 'resistance')} + {q(rl, 'resistance')}) = {q(r2l, 'resistance')}")
        r.substitutions.append(f"Vout(loaded) = {q(vin, 'voltage')} × {q(r2l, 'resistance')} / ({q(r1, 'resistance')} + {q(r2l, 'resistance')}) = {q(vl, 'voltage')}")
        if vu != 0:
            drop = (vu - vl) / vu * 100
            r.add("Loading error", "drop caused by the load, relative to no load", drop, "percent")
        il = vl / rl
        r.add("IL", "load current", il, "current")
        i_total = (vin - vl) / r1 if r1 > 0 else math.nan
        r.add("I(R1)", "current through R1", i_total, "current")
        r.steps.append("The load is in parallel with R2, lowering the bottom resistance and therefore Vout.")
    else:
        r.add("I", "divider current", vin / (r1 + r2) if (r1 + r2) else math.inf, "current")
    r.add("Vin", "input voltage", vin, "voltage")
    r.assumptions.append("Ideal voltage source (no internal resistance); DC or AC values in one convention.")
    return r


register(Calculator(
    "divider", CAT, "Voltage divider (with load)", _divider,
    fields=[
        Field("Vin", "Vin", "input voltage", "voltage", "V", "12"),
        Field("R1", "R1", "top resistor (between input and output)", "resistance", "kΩ", "10", show_if=lambda o, u: u != "R1"),
        Field("R2", "R2", "bottom resistor (between output and ground)", "resistance", "kΩ", "4.7", show_if=lambda o, u: u != "R2"),
        Field("Vout_t", "Vout", "desired output voltage", "voltage", "V", "3.3", show_if=lambda o, u: u in ("R1", "R2")),
        Field("RL", "RL", "load resistor across R2 (blank = no load)", "resistance", "kΩ", "", optional=True),
    ],
    unknowns=[("Vout", "Output voltage Vout"), ("R2", "Bottom resistor R2 for a desired Vout"), ("R1", "Top resistor R1 for a desired Vout")],
    description="Unloaded and loaded output of a two-resistor divider; solve for R1 or R2.",
    keywords="voltage divider potential load rl output",
    diagram=lambda o, u: ladder("dc", series=[("R", "R1")], shunts=[[("R", "R2")], [("R", "RL")]],
                                out={"across": "shunt", "index": 0}, note="RL (optional) is across R2"),
))


# ======================================================================= Sine conversions
FORMS = [("peak", "Peak (Vp)"), ("pp", "Peak-to-peak (Vpp)"), ("rms", "RMS (Vrms)"), ("avg", "Average of |v| (Vavg)")]


def _sine(inp):
    form = inp.options["form"]
    v = inp.need("V", nonneg=True)
    vp = {"peak": v, "pp": v / 2, "rms": v * math.sqrt(2), "avg": v * math.pi / 2}[form]
    r = Result("Sine-wave amplitude conversions")
    r.add("Vp", "peak (zero to crest)", vp, "voltage", primary=form != "peak", note="peak")
    r.add("Vpp", "peak-to-peak (trough to crest)", 2 * vp, "voltage", primary=form != "pp", note="peak-to-peak")
    r.add("Vrms", "RMS (heating-equivalent DC)", vp / math.sqrt(2), "voltage", primary=form != "rms", note="RMS")
    r.add("Vavg", "average of the rectified wave (half-cycle average)", 2 * vp / math.pi, "voltage", note="average")
    r.formulas += ["Vpp = 2 × Vp", "Vrms = Vp / √2 ≈ 0.7071 × Vp", "Vavg = 2 × Vp / π ≈ 0.6366 × Vp"]
    r.substitutions.append(f"Vp = {q(vp, 'voltage')} → Vpp = {q(2 * vp, 'voltage')}, Vrms = {q(vp / math.sqrt(2), 'voltage')}")
    r.symbols = {"Vp": "peak voltage", "Vpp": "peak-to-peak voltage", "Vrms": "root-mean-square voltage",
                 "Vavg": "average of the full-wave rectified sine"}
    r.assumptions.append("Pure sine wave with no DC offset. These factors do not apply to square, triangle or distorted waves.")
    return r


register(Calculator(
    "sine_convert", CAT, "Sine wave: peak / p-p / RMS", _sine,
    fields=[Field("V", "V", "the known amplitude (in the form selected)", "voltage", "V", "10")],
    options=[Option("form", "Known value is", FORMS, "peak")],
    description="Convert a sine amplitude between peak, peak-to-peak, RMS and average.",
    keywords="rms peak peak-to-peak vpp vp sine amplitude convert average",
))
