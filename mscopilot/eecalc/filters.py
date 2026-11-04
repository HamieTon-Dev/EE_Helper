"""Stage 1b - first-order RC and RL filters (ideal, unloaded).

Formulas (as specified):
  RC cutoff   fC = 1 / (2π R C)            RL cutoff   fC = R / (2π L)
  low-pass    Vout = Vin / √(1 + (f/fC)²)
  high-pass   Vout = Vin · (f/fC) / √(1 + (f/fC)²)
  at cutoff   Vout = Vin / √2
  gain        Gain_dB = 20 · log10(Vout / Vin)

The output at the actual cutoff and the output at a user-specified frequency are always
reported separately; 0.707 × Vin is only ever used at f = fC.
"""
from __future__ import annotations

import math

import numpy as np

from mscopilot.eecalc.basic import ladder
from mscopilot.eecalc.core import (
    CONV_LABEL, TWO_PI, VOLTAGE_CONVENTION, CalcError, Calculator, Field, Option, PlotSpec, Result, db, register,
)
from mscopilot.eecalc.units import fmt, q

CAT = "RC and RL filters"
RESPONSES = [("lp", "Low-pass"), ("hp", "High-pass")]

# which component the output is measured across (series element first, then shunt/output)
TOPOLOGY = {
    ("RC", "lp"): (("R", "R"), ("C", "C"), "C", "capacitor C"),
    ("RC", "hp"): (("C", "C"), ("R", "R"), "R", "resistor R"),
    ("RL", "lp"): (("L", "L"), ("R", "R"), "R", "resistor R"),
    ("RL", "hp"): (("R", "R"), ("L", "L"), "L", "inductor L"),
}


def response(kind: str, fc: float):
    """Complex transfer function H(f) = Vout/Vin of the ideal first-order filter."""

    def h(f):
        x = np.asarray(f, dtype=float) / fc
        lp = 1.0 / (1.0 + 1j * x)
        return lp if kind == "lp" else (1j * x) * lp

    return h


def magnitude(kind: str, f: float, fc: float) -> float:
    if math.isinf(f):
        return 0.0 if kind == "lp" else 1.0
    x = f / fc
    return 1.0 / math.sqrt(1.0 + x * x) if kind == "lp" else x / math.sqrt(1.0 + x * x)


def phase(kind: str, f: float, fc: float) -> float:
    """Phase of Vout relative to Vin, radians. LP: −atan(f/fC); HP: 90° − atan(f/fC)."""
    a = math.atan(f / fc)
    return -a if kind == "lp" else math.pi / 2 - a


def _filter(inp, family: str):
    kind = inp.options["response"]
    react_sym, react_qty = ("C", "capacitance") if family == "RC" else ("L", "inductance")
    u = inp.unknown
    series, shunt, out_sym, out_desc = TOPOLOGY[(family, kind)]
    r = Result(f"{family} {'low' if kind == 'lp' else 'high'}-pass filter")
    r.symbols = {
        "fC": "cutoff frequency (−3 dB, half-power point)", "R": "resistance", react_sym: "capacitance" if family == "RC" else "inductance",
        "Vin": "input voltage", "Vout": f"output voltage, measured across {out_desc}", "f": "the frequency you specified",
        "ωC": "cutoff angular frequency (2π fC)", "τ": "time constant", "φ": "phase of Vout relative to Vin",
    }
    # ---------------------------------------------------------- solve for the unknown
    if u == "fc":
        R = inp.need("R", positive=True)
        X = inp.need(react_sym, positive=True)
        if family == "RC":
            fc = 1.0 / (TWO_PI * R * X)
            r.formulas.append("fC = 1 / (2π R C)")
            r.substitutions.append(f"fC = 1 / (2π × {q(R, 'resistance')} × {q(X, react_qty)}) = {q(fc, 'frequency')}")
        else:
            fc = R / (TWO_PI * X)
            r.formulas.append("fC = R / (2π L)")
            r.substitutions.append(f"fC = {q(R, 'resistance')} / (2π × {q(X, react_qty)}) = {q(fc, 'frequency')}")
        r.add("fC", "cutoff frequency", fc, "frequency", primary=True, note="calculated from the components")
    elif u == "R":
        fc = inp.need("fc_t", positive=True)
        X = inp.need(react_sym, positive=True)
        if family == "RC":
            R = 1.0 / (TWO_PI * fc * X)
            r.formulas.append("R = 1 / (2π fC C)")
            r.substitutions.append(f"R = 1 / (2π × {q(fc, 'frequency')} × {q(X, react_qty)}) = {q(R, 'resistance')}")
        else:
            R = TWO_PI * fc * X
            r.formulas.append("R = 2π fC L")
            r.substitutions.append(f"R = 2π × {q(fc, 'frequency')} × {q(X, react_qty)} = {q(R, 'resistance')}")
        r.add("R", "required resistance", R, "resistance", primary=True)
        r.add("fC", "cutoff frequency", fc, "frequency")
    else:
        fc = inp.need("fc_t", positive=True)
        R = inp.need("R", positive=True)
        if family == "RC":
            X = 1.0 / (TWO_PI * fc * R)
            r.formulas.append("C = 1 / (2π fC R)")
        else:
            X = R / (TWO_PI * fc)
            r.formulas.append("L = R / (2π fC)")
        r.substitutions.append(f"{react_sym} = {q(X, react_qty)} for fC = {q(fc, 'frequency')} and R = {q(R, 'resistance')}")
        r.add(react_sym, f"required {react_qty}", X, react_qty, primary=True)
        r.add("fC", "cutoff frequency", fc, "frequency")
        r.steps.append(f"Standard component values rarely match exactly; recalculate fC with the value you fit.")

    wc = TWO_PI * fc
    r.add("ωC", "cutoff angular frequency", wc, "angular_frequency")
    tau = R * X if family == "RC" else X / R
    r.add("τ", "time constant (RC or L/R) = 1/ωC", tau, "time")
    r.add("Output across", "component the output is measured across", None, text=f"{out_desc} ({out_sym})")

    # ---------------------------------------------------------- voltages
    vin = inp.opt("Vin", nonneg=True)
    f = inp.opt("f", nonneg=True)
    conv = CONV_LABEL[inp.options["vconv"]]
    r.formulas.append("Vout at cutoff = Vin / √2  (gain −3.01 dB, phase " + ("−45°" if kind == "lp" else "+45°") + ")")
    if vin is not None:
        vc = vin / math.sqrt(2)
        r.add("Vout @ fC", "output at the actual cutoff frequency", vc, "voltage", primary=f is None, note=conv)
        r.substitutions.append(f"Vout(fC) = {q(vin, 'voltage')} / √2 = {q(vc, 'voltage')}  ({conv})")
    r.add("Gain @ fC", "voltage gain at cutoff", 1 / math.sqrt(2), "ratio", note="Vout/Vin")
    r.add("Gain @ fC (dB)", "gain at cutoff in decibels", db(1 / math.sqrt(2)), "decibel")
    r.add("φ @ fC", "phase of Vout relative to Vin at cutoff", phase(kind, fc, fc), "angle")

    if f is not None:
        x = f / fc
        g = magnitude(kind, f, fc)
        ph = phase(kind, f, fc) if f > 0 else (0.0 if kind == "lp" else math.pi / 2)
        if kind == "lp":
            r.formulas.append("Vout = Vin / √(1 + (f/fC)²)")
            sub = f"Vout/Vin = 1 / √(1 + ({q(f, 'frequency')} / {q(fc, 'frequency')})²) = 1 / √(1 + {x:.6g}²) = {g:.6g}"
            r.formulas.append("φ = −tan⁻¹(f/fC)")
        else:
            r.formulas.append("Vout = Vin × (f/fC) / √(1 + (f/fC)²)")
            sub = f"Vout/Vin = ({x:.6g}) / √(1 + {x:.6g}²) = {g:.6g}"
            r.formulas.append("φ = 90° − tan⁻¹(f/fC)")
        r.substitutions.append(sub)
        r.formulas.append("Gain_dB = 20 log10(Vout / Vin)")
        r.add("Gain @ f", "voltage gain at the specified frequency", g, "ratio", note="Vout/Vin")
        r.add("Gain @ f (dB)", "gain at the specified frequency in decibels", db(g), "decibel",
              text="−∞ dB (no output at DC)" if g == 0 else "")
        r.add("φ @ f", "phase of Vout relative to Vin at the specified frequency", ph, "angle")
        if vin is not None:
            vo = vin * g
            r.add("Vout @ f", f"output at the specified frequency f = {fmt(f, 'frequency')}", vo, "voltage", primary=True, note=conv)
            r.substitutions.append(f"Vout(f) = {q(vin, 'voltage')} × {g:.6g} = {q(vo, 'voltage')}  ({conv})")
        if f == 0:
            r.steps.append("f = 0 Hz is DC: " + ("the capacitor is open / inductor shorted, so the full input reaches the output."
                                                  if kind == "lp" else "the output is zero (gain −∞ dB)."))
        elif math.isclose(f, fc, rel_tol=1e-9):
            r.steps.append("The specified frequency equals the cutoff, so Vout = Vin/√2 and the gain is −3.01 dB.")
        else:
            rel = "above" if f > fc else "below"
            r.steps.append(f"The specified frequency {fmt(f, 'frequency')} is {rel} the actual cutoff {fmt(fc, 'frequency')} "
                           f"(f/fC = {x:.4g}). The output there comes from the full formula, not Vin × 0.707; "
                           "Vin × 0.707 applies only at f = fC, which is shown separately.")
    else:
        r.steps.append("Enter a frequency f to get the output at a frequency other than the cutoff.")

    r.steps.append(f"The output is measured across the {out_desc}: "
                   + ("it passes low frequencies and attenuates high ones (−20 dB/decade above fC)." if kind == "lp"
                      else "it blocks low frequencies and passes high ones (+20 dB/decade below fC)."))
    r.assumptions += [
        "Ideal components; unloaded output (the next stage draws no current) and an ideal source (zero source resistance).",
        "A load or source resistance changes the effective R and therefore fC and the passband gain.",
        f"Voltages use the {conv} convention you selected; Vout is in the same convention as Vin.",
    ]
    r.plot = PlotSpec("freq", r.title, response(kind, fc), f_min=fc / 1000, f_max=fc * 1000,
                      vin=vin, vin_label=f"V ({conv})", markers=[("fC", fc)] + ([("f", f)] if f else []),
                      hlines=[("−3 dB", 1 / math.sqrt(2))])
    return r


def _fields(family):
    rs, qty, unit, dflt = ("C", "capacitance", "nF", "4") if family == "RC" else ("L", "inductance", "mH", "14")
    return [
        Field("R", "R", "resistance", "resistance", "kΩ", "1" if family == "RC" else "2", show_if=lambda o, u: u != "R"),
        Field(rs, rs, qty, qty, unit, dflt, show_if=lambda o, u, rs=rs: u != rs),
        Field("fc_t", "fC", "desired cutoff frequency", "frequency", "kHz", "10", show_if=lambda o, u: u != "fc"),
        Field("Vin", "Vin", "input voltage amplitude (blank = gains only)", "voltage", "V", "3.3", optional=True),
        Field("f", "f", "frequency of interest (blank = cutoff only; 0 = DC)", "frequency", "kHz", "", optional=True),
    ]


def _diagram(family):
    def d(o, u):
        series, shunt, out_sym, out_desc = TOPOLOGY[(family, o["response"])]
        return ladder("ac", series=[series], shunts=[[shunt]], out={"across": "shunt", "index": 0},
                      note=f"Vout is measured across {out_desc}")

    return d


for fam, title, kw in (("RC", "RC filter (low-pass / high-pass)", "rc cutoff low-pass high-pass resistor capacitor -3db"),
                       ("RL", "RL filter (low-pass / high-pass)", "rl cutoff low-pass high-pass resistor inductor -3db")):
    react = "C" if fam == "RC" else "L"
    register(Calculator(
        f"{fam.lower()}_filter", CAT, title, (lambda inp, fam=fam: _filter(inp, fam)),
        fields=_fields(fam),
        options=[Option("response", "Response", RESPONSES), VOLTAGE_CONVENTION],
        unknowns=[("fc", "Cutoff frequency fC"), ("R", "Resistance R for a desired fC"),
                  (react, f"{'Capacitance C' if fam == 'RC' else 'Inductance L'} for a desired fC")],
        description=f"Ideal unloaded first-order {fam} filter: cutoff, output at cutoff and at any frequency, gain, phase.",
        keywords=kw + " filter gain decibel phase",
        diagram=_diagram(fam),
    ))
