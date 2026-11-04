"""Stage 2 - reactance, impedance, resonance, series and parallel resonant circuits.

Series RLC band-pass (output across the load RL), with total series resistance
Rtot = Rs + RW + RL:
  ω0 = 1/√(LC) (the |H| peak, exact for this circuit);  Hmax = RL / Rtot
  half-power points (|H| = Hmax/√2) where |X(ω)| = Rtot, exactly:
     ω1,2 = ∓Rtot/(2L) + √((Rtot/(2L))² + ω0²)  ->  BW = f2 − f1 = Rtot / (2πL),  Q = ω0 L / Rtot

Parallel circuits are only solved for the explicitly selected arrangement and output
location; anything else is refused rather than approximated.
"""
from __future__ import annotations

import math

import numpy as np

from eesimlab.eecalc.basic import ladder
from eesimlab.eecalc.core import (
    CONV_LABEL, TWO_PI, VOLTAGE_CONVENTION, CalcError, Calculator, Field, Option, PlotSpec, Result, db, register,
)
from eesimlab.eecalc.units import fmt, q, sig

CAT = "RLC and resonance"


# ======================================================================= reactance
def _reactance(inp):
    u = inp.unknown
    r = Result("Reactance")
    r.symbols = {"XL": "inductive reactance", "XC": "capacitive reactance", "f": "frequency", "L": "inductance",
                 "C": "capacitance", "ω": "angular frequency (2πf)"}
    if u == "XL":
        f, L = inp.need("f", nonneg=True), inp.need("L", positive=True)
        x = r.add("XL", "inductive reactance", TWO_PI * f * L, "reactance", primary=True)
        r.formulas.append("XL = 2π f L")
        r.substitutions.append(f"XL = 2π × {q(f, 'frequency')} × {q(L, 'inductance')} = {q(x, 'reactance')}")
        if f == 0:
            r.steps.append("At DC (0 Hz) an ideal inductor is a short circuit: XL = 0 Ω.")
    elif u == "XC":
        f, C = inp.need("f", nonneg=True), inp.need("C", positive=True)
        r.formulas.append("XC = 1 / (2π f C)")
        if f == 0:
            r.add("XC", "capacitive reactance", math.inf, "reactance", primary=True, text="∞ (open circuit at DC)")
            r.steps.append("At DC (0 Hz) an ideal capacitor is an open circuit: XC is infinite.")
        else:
            x = r.add("XC", "capacitive reactance", 1 / (TWO_PI * f * C), "reactance", primary=True)
            r.substitutions.append(f"XC = 1 / (2π × {q(f, 'frequency')} × {q(C, 'capacitance')}) = {q(x, 'reactance')}")
    elif u == "L":
        f, x = inp.need("f", positive=True), inp.need("X", positive=True)
        L = r.add("L", "inductance", x / (TWO_PI * f), "inductance", primary=True)
        r.formulas.append("L = XL / (2π f)")
        r.substitutions.append(f"L = {q(x, 'reactance')} / (2π × {q(f, 'frequency')}) = {q(L, 'inductance')}")
    elif u == "C":
        f, x = inp.need("f", positive=True), inp.need("X", positive=True)
        C = r.add("C", "capacitance", 1 / (TWO_PI * f * x), "capacitance", primary=True)
        r.formulas.append("C = 1 / (2π f XC)")
        r.substitutions.append(f"C = 1 / (2π × {q(f, 'frequency')} × {q(x, 'reactance')}) = {q(C, 'capacitance')}")
    elif u == "fL":
        L, x = inp.need("L", positive=True), inp.need("X", nonneg=True)
        f = r.add("f", "frequency", x / (TWO_PI * L), "frequency", primary=True)
        r.formulas.append("f = XL / (2π L)")
        r.substitutions.append(f"f = {q(x, 'reactance')} / (2π × {q(L, 'inductance')}) = {q(f, 'frequency')}")
    else:  # fC
        C, x = inp.need("C", positive=True), inp.need("X", positive=True)
        f = r.add("f", "frequency", 1 / (TWO_PI * C * x), "frequency", primary=True)
        r.formulas.append("f = 1 / (2π C XC)")
        r.substitutions.append(f"f = 1 / (2π × {q(C, 'capacitance')} × {q(x, 'reactance')}) = {q(f, 'frequency')}")
    r.assumptions.append("Ideal components (no winding resistance, ESR or self-resonance).")
    return r


register(Calculator(
    "reactance", CAT, "Inductive / capacitive reactance", _reactance,
    fields=[
        Field("f", "f", "frequency", "frequency", "kHz", "1", show_if=lambda o, u: u not in ("fL", "fC")),
        Field("L", "L", "inductance", "inductance", "mH", "100", show_if=lambda o, u: u in ("XL", "fL")),
        Field("C", "C", "capacitance", "capacitance", "µF", "0.22", show_if=lambda o, u: u in ("XC", "fC")),
        Field("X", "X", "the known reactance (XL or XC)", "reactance", "Ω", "628", show_if=lambda o, u: u in ("L", "C", "fL", "fC")),
    ],
    unknowns=[("XL", "XL from f and L"), ("XC", "XC from f and C"), ("L", "L for a given XL at f"),
              ("C", "C for a given XC at f"), ("fL", "f where L has reactance X"), ("fC", "f where C has reactance X")],
    description="XL = 2πfL and XC = 1/(2πfC), solved for any variable.",
    keywords="reactance xl xc inductive capacitive",
))


# ======================================================================= resonance
def _resonance(inp):
    u = inp.unknown
    r = Result("Resonant frequency (LC)")
    r.symbols = {"fr": "resonant frequency (XL = XC)", "ωr": "resonant angular frequency", "L": "inductance",
                 "C": "capacitance", "Z0": "characteristic impedance √(L/C)"}
    if u == "fr":
        L, C = inp.need("L", positive=True), inp.need("C", positive=True)
        fr = r.add("fr", "resonant frequency", 1 / (TWO_PI * math.sqrt(L * C)), "frequency", primary=True)
        r.formulas.append("fr = 1 / (2π √(L C))")
        r.substitutions.append(f"fr = 1 / (2π √({q(L, 'inductance')} × {q(C, 'capacitance')})) = {q(fr, 'frequency')}")
    elif u == "L":
        fr, C = inp.need("fr", positive=True), inp.need("C", positive=True)
        L = r.add("L", "inductance", 1 / ((TWO_PI * fr) ** 2 * C), "inductance", primary=True)
        r.formulas.append("L = 1 / ((2π fr)² C)")
        r.substitutions.append(f"L = 1 / ((2π × {q(fr, 'frequency')})² × {q(C, 'capacitance')}) = {q(L, 'inductance')}")
    else:
        fr, L = inp.need("fr", positive=True), inp.need("L", positive=True)
        C = r.add("C", "capacitance", 1 / ((TWO_PI * fr) ** 2 * L), "capacitance", primary=True)
        r.formulas.append("C = 1 / ((2π fr)² L)")
        r.substitutions.append(f"C = 1 / ((2π × {q(fr, 'frequency')})² × {q(L, 'inductance')}) = {q(C, 'capacitance')}")
    fr = 1 / (TWO_PI * math.sqrt(L * C))
    r.add("ωr", "resonant angular frequency", TWO_PI * fr, "angular_frequency")
    r.add("T", "period at resonance", 1 / fr, "time")
    r.add("XL = XC", "reactance of L (and of C) at resonance", TWO_PI * fr * L, "reactance")
    r.add("Z0", "characteristic impedance √(L/C)", math.sqrt(L / C), "impedance")
    r.steps.append("At fr the inductive and capacitive reactances are equal and cancel.")
    r.assumptions.append("Ideal L and C. With winding resistance, a parallel tank's unity-power-factor frequency is lower; "
                         "use the parallel resonance calculator for that.")
    return r


register(Calculator(
    "resonance", CAT, "Resonant frequency fr = 1/(2π√LC)", _resonance,
    fields=[
        Field("fr", "fr", "resonant frequency", "frequency", "kHz", "1", show_if=lambda o, u: u != "fr"),
        Field("L", "L", "inductance", "inductance", "mH", "100", show_if=lambda o, u: u != "L"),
        Field("C", "C", "capacitance", "capacitance", "µF", "0.22", show_if=lambda o, u: u != "C"),
    ],
    unknowns=[("fr", "Resonant frequency fr"), ("L", "Inductance L for a given fr"), ("C", "Capacitance C for a given fr")],
    description="Ideal LC resonance, solved for fr, L or C.",
    keywords="resonance resonant frequency lc tank",
))


# ======================================================================= RLC impedance
def _impedance(inp):
    conn = inp.options["connection"]
    f = inp.need("f", nonneg=True)
    R, L, C = inp.opt("R", nonneg=True), inp.opt("L", positive=True), inp.opt("C", positive=True)
    if R is None and L is None and C is None:
        raise CalcError("Enter at least one of R, L or C (blank = component not present).")
    w = TWO_PI * f
    r = Result(f"{'Series' if conn == 'series' else 'Parallel'} RLC impedance")
    r.symbols = {"Z": "impedance (complex)", "|Z|": "impedance magnitude", "θ": "impedance phase angle (+ = inductive)",
                 "Y": "admittance 1/Z", "XL": "inductive reactance", "XC": "capacitive reactance"}
    xl = w * L if L is not None else None
    xc = (math.inf if w == 0 else 1 / (w * C)) if C is not None else None
    if xl is not None:
        r.add("XL", "inductive reactance", xl, "reactance")
    if xc is not None:
        r.add("XC", "capacitive reactance", xc, "reactance", text="∞ (open at DC)" if math.isinf(xc) else "")
    parts = [n for n, v in (("R", R), ("L", L), ("C", C)) if v is not None]
    if conn == "series":
        r.formulas.append("Z = R + j(XL − XC)")
        if xc is not None and math.isinf(xc):
            r.add("Z", "impedance", None, "impedance", primary=True, text="∞ (the series capacitor is open at DC)")
            r.steps.append("No DC current can flow through a series capacitor.")
            return _imp_tail(r)
        z = complex(R or 0.0, (xl or 0.0) - (xc or 0.0))
        r.substitutions.append(f"Z = {sig(R or 0)} + j({sig(xl or 0)} − {sig(xc or 0)}) Ω = {fmt(z, 'impedance')}")
    else:
        r.formulas.append("Y = 1/R + 1/(jXL) + jωC = G + j(BC − BL);  Z = 1/Y")
        if (R == 0) or (xl is not None and xl == 0):
            r.add("Z", "impedance", 0.0, "impedance", primary=True,
                  text="0 Ω (short circuit: " + ("R = 0" if R == 0 else "the inductor is a short at DC") + ")")
            return _imp_tail(r)
        y = complex((1 / R) if R else 0.0, 0.0)
        if xl is not None:
            y += complex(0, -1 / xl)
        if xc is not None and not math.isinf(xc):
            y += complex(0, 1 / xc)
        if xc is not None and math.isinf(xc) and len(parts) == 1:
            r.add("Z", "impedance", None, "impedance", primary=True, text="∞ (a lone capacitor is open at DC)")
            return _imp_tail(r)
        if abs(y) == 0 or abs(y) < 1e-15 * (1 / min(v for v in (xl, xc) if v)):
            r.add("Z", "impedance", None, "impedance", primary=True,
                  text="∞ (ideal parallel resonance: BL = BC and no resistance)")
            r.steps.append("Ideal L and C in parallel at resonance draw equal and opposite currents, so the "
                           "net current is zero and the impedance is infinite. Real coils have winding resistance.")
            return _imp_tail(r)
        z = 1 / y
        r.add("Y", "admittance", y, "conductance")
        r.substitutions.append(f"Y = {fmt(y, 'conductance')} → Z = 1/Y = {fmt(z, 'impedance')}")
    r.add("Z", "impedance (rectangular and polar)", z, "impedance", primary=True)
    r.add("|Z|", "impedance magnitude", abs(z), "impedance")
    th = math.atan2(z.imag, z.real)
    r.add("θ", "impedance angle (current lags voltage by θ when positive)", th, "angle")
    char = "resistive (in phase)" if abs(z.imag) <= 1e-12 * max(abs(z), 1e-300) else ("inductive" if z.imag > 0 else "capacitive")
    r.add("Behaviour", "circuit behaves as", None, text=char)
    return _imp_tail(r)


def _imp_tail(r):
    r.assumptions.append("Ideal components at a single sinusoidal frequency; blank components are absent (not shorted).")
    return r


register(Calculator(
    "rlc_impedance", CAT, "RLC impedance (series / parallel)", _impedance,
    fields=[
        Field("f", "f", "frequency (0 = DC)", "frequency", "kHz", "1"),
        Field("R", "R", "resistance (blank = none)", "resistance", "Ω", "1000", optional=True),
        Field("L", "L", "inductance (blank = none)", "inductance", "mH", "100", optional=True),
        Field("C", "C", "capacitance (blank = none)", "capacitance", "µF", "0.22", optional=True),
    ],
    options=[Option("connection", "Connection", [("series", "Series R + L + C"), ("parallel", "Parallel R ∥ L ∥ C")])],
    description="Complex impedance, magnitude and phase of any subset of R, L, C.",
    keywords="impedance z magnitude phase series parallel rlc admittance",
    diagram=lambda o, u: {"kind": "chain", "mode": o["connection"], "items": [("R", "R"), ("L", "L"), ("C", "C")]},
))


# ======================================================================= Series RLC
def series_bp_response(rtot, rl, L, C):
    def h(f):
        w = TWO_PI * np.asarray(f, dtype=float)
        return rl / (rtot + 1j * (w * L - 1 / (w * C)))

    return h


def _series_rlc(inp):
    model = inp.options["model"]
    conv = CONV_LABEL[inp.options["vconv"]]
    vin = inp.need("Vin", nonneg=True)
    L, C = inp.need("L", positive=True), inp.need("C", positive=True)
    rl = inp.need("RL", positive=True)
    rw = inp.opt("RW", 0.0, nonneg=True) if model == "lossy" else 0.0
    rs = inp.opt("Rs", 0.0, nonneg=True) if model == "lossy" else 0.0
    rtot = rs + rw + rl
    w0 = 1 / math.sqrt(L * C)
    fr = w0 / TWO_PI
    r = Result(f"Series RLC band-pass ({'lossy' if model == 'lossy' else 'ideal'} model), output across RL")
    r.symbols = {"fr": "resonant frequency (peak of the response)", "Q": "quality factor of the loaded circuit",
                 "BW": "bandwidth between the half-power points", "f1": "lower half-power (−3 dB) frequency",
                 "f2": "upper half-power (−3 dB) frequency", "RL": "load resistor (output is across it)",
                 "RW": "inductor winding resistance", "Rs": "source internal resistance",
                 "Rtot": "total series resistance Rs + RW + RL", "Vout": "voltage across RL", "I": "series current"}
    r.formulas.append("fr = 1 / (2π √(L C))")
    r.substitutions.append(f"fr = 1 / (2π √({q(L, 'inductance')} × {q(C, 'capacitance')})) = {q(fr, 'frequency')}")
    r.add("fr", "resonant frequency", fr, "frequency", primary=True, note="exact")
    r.formulas.append("Rtot = Rs + RW + RL")
    r.substitutions.append(f"Rtot = {sig(rs)} + {sig(rw)} + {sig(rl)} Ω = {q(rtot, 'resistance')}")
    r.add("Rtot", "total series resistance", rtot, "resistance")

    bw = rtot / (TWO_PI * L)
    a = rtot / (2 * L)
    w1, w2 = -a + math.sqrt(a * a + w0 * w0), a + math.sqrt(a * a + w0 * w0)
    Q = w0 * L / rtot
    r.formulas += ["BW = Rtot / (2π L)  (exact for this circuit)", "Q = 2π fr L / Rtot = fr / BW",
                   "f1,2 = [∓Rtot/(2L) + √((Rtot/(2L))² + ωr²)] / 2π  (exact)"]
    r.substitutions.append(f"BW = {q(rtot, 'resistance')} / (2π × {q(L, 'inductance')}) = {q(bw, 'frequency')}")
    r.substitutions.append(f"Q = 2π × {q(fr, 'frequency')} × {q(L, 'inductance')} / {q(rtot, 'resistance')} = {Q:.6g}")
    r.add("BW", "bandwidth (f2 − f1)", bw, "frequency", primary=True, note="exact")
    r.add("Q", "loaded quality factor", Q, "ratio")
    r.add("f1", "lower half-power frequency", w1 / TWO_PI, "frequency", note="exact")
    r.add("f2", "upper half-power frequency", w2 / TWO_PI, "frequency", note="exact")
    r.add("f1 (approx.)", "fr − BW/2", fr - bw / 2, "frequency", note="high-Q approximation")
    r.add("f2 (approx.)", "fr + BW/2", fr + bw / 2, "frequency", note="high-Q approximation")
    if rw > 0:
        r.add("Q coil", "inductor's own Q at fr = ωr L / RW", w0 * L / rw, "ratio")

    i0 = vin / rtot
    hmax = rl / rtot
    vout0 = vin * hmax
    xl0 = w0 * L
    r.formulas += ["At resonance: I = Vin / Rtot,  Vout = Vin × RL / Rtot", "VL = VC = I × XL at resonance"]
    r.substitutions.append(f"Vout(fr) = {q(vin, 'voltage')} × {sig(rl)} / {sig(rtot)} = {q(vout0, 'voltage')}")
    r.add("Vout @ fr", "output across RL at resonance", vout0, "voltage", primary=True, note=conv)
    r.add("I @ fr", "series current at resonance", i0, "current", note=conv)
    r.add("Gain @ fr", "peak gain Vout/Vin", hmax, "ratio")
    r.add("Insertion loss", "peak gain in dB (loss caused by Rs and RW)", db(hmax), "decibel")
    r.add("VL @ fr", "voltage across the ideal inductance at resonance", i0 * xl0, "voltage", note=conv)
    r.add("VC @ fr", "voltage across C at resonance", i0 * xl0, "voltage", note=conv)
    if rw > 0:
        r.add("Vcoil @ fr", "voltage across the real coil (RW + L)", i0 * math.hypot(rw, xl0), "voltage", note=conv)
    r.add("Vout @ f1, f2", "output at the half-power points (peak/√2)", vout0 / math.sqrt(2), "voltage", note=conv)
    r.steps.append("Half-power points are found relative to the response's own peak (Vin × RL/Rtot), so insertion loss "
                   "from Rs and RW is already accounted for; they are not where Vout = 0.707 × Vin.")
    r.steps.append(f"f1 × f2 = fr² exactly ({sig(w1 / TWO_PI)} × {sig(w2 / TWO_PI)} = {sig(fr ** 2)}), so fr is the "
                   "geometric (not arithmetic) centre of the band.")

    f = inp.opt("f", nonneg=True)
    if f is not None:
        if f == 0:
            r.add("Vout @ f", "output at DC", 0.0, "voltage", note=conv)
            r.steps.append("At DC the series capacitor blocks all current, so Vout = 0.")
        else:
            w = TWO_PI * f
            xl, xc = w * L, 1 / (w * C)
            z = complex(rtot, xl - xc)
            i = vin / abs(z)
            h = rl / z
            r.formulas.append("At f: Z = Rtot + j(XL − XC),  I = Vin/|Z|,  Vout = I × RL")
            r.substitutions.append(f"Z({fmt(f, 'frequency')}) = {sig(rtot)} + j({sig(xl)} − {sig(xc)}) Ω, |Z| = {q(abs(z), 'impedance')}")
            r.add("Z @ f", "total impedance at f", z, "impedance")
            r.add("I @ f", "series current at f", i, "current", note=conv)
            r.add("Vout @ f", f"output across RL at f = {fmt(f, 'frequency')}", i * rl, "voltage", primary=True, note=conv)
            r.add("Gain @ f (dB)", "Vout/Vin at f in dB", db(abs(h)), "decibel")
            r.add("Rel. to peak @ f", "gain at f relative to the peak", db(abs(h) / hmax), "decibel")
            r.add("φ @ f", "phase of Vout relative to Vin", math.atan2(h.imag, h.real), "angle")
            r.add("VL @ f", "voltage across the inductance", i * xl, "voltage", note=conv)
            r.add("VC @ f", "voltage across C", i * xc, "voltage", note=conv)
            if rw > 0:
                r.add("VRW @ f", "voltage across the winding resistance", i * rw, "voltage", note=conv)
            if rs > 0:
                r.add("VRs @ f", "voltage lost in the source resistance", i * rs, "voltage", note=conv)
    if model == "ideal":
        r.assumptions.append("Ideal model: no winding or source resistance; the only loss is the load RL.")
    else:
        r.assumptions.append("Lossy model: winding resistance RW in series with L, source resistance Rs in series with the "
                             "source; capacitor ESR is neglected.")
    r.assumptions.append(f"Voltages use the {conv} convention; component voltages at resonance can exceed Vin (by ≈ Q).")
    r.plot = PlotSpec("freq", r.title, series_bp_response(rtot, rl, L, C), f_min=fr / 1000, f_max=fr * 1000, vin=vin,
                      vin_label=f"V ({conv})", markers=[("fr", fr), ("f1", w1 / TWO_PI), ("f2", w2 / TWO_PI)]
                      + ([("f", f)] if f else []), band=(w1 / TWO_PI, w2 / TWO_PI),
                      hlines=[("peak", hmax), ("peak −3 dB", hmax / math.sqrt(2))])
    return r


register(Calculator(
    "series_rlc", CAT, "Series RLC band-pass (output across RL)", _series_rlc,
    fields=[
        Field("Vin", "Vin", "source voltage amplitude", "voltage", "V", "5"),
        Field("L", "L", "inductance", "inductance", "mH", "95"),
        Field("C", "C", "capacitance", "capacitance", "nF", "100"),
        Field("RL", "RL", "load resistor (the output is measured across it)", "resistance", "Ω", "120"),
        Field("RW", "RW", "inductor winding resistance", "resistance", "Ω", "40", optional=True,
              show_if=lambda o, u: o["model"] == "lossy"),
        Field("Rs", "Rs", "source internal resistance", "resistance", "Ω", "0", optional=True,
              show_if=lambda o, u: o["model"] == "lossy"),
        Field("f", "f", "frequency of interest (blank = resonance only)", "frequency", "kHz", "", optional=True),
    ],
    options=[Option("model", "Model", [("lossy", "Lossy (winding RW and source Rs)"), ("ideal", "Ideal (load RL only)")]),
             VOLTAGE_CONVENTION],
    description="Source → Rs → L (with RW) → C → RL to ground; output across RL. Resonance, exact BW, f1/f2, Q, voltages.",
    keywords="series rlc band-pass bandpass resonance bandwidth q factor cutoff winding resistance load insertion loss",
    diagram=lambda o, u: ladder("ac", series=([("R", "Rs")] if o["model"] == "lossy" else [])
                                + ([("R", "RW"), ("L", "L")] if o["model"] == "lossy" else [("L", "L")]) + [("C", "C")],
                                shunts=[[("R", "RL")]], out={"across": "shunt", "index": 0}, note="Vout across RL"),
))


# ======================================================================= Parallel resonance
ARRANGEMENTS = [
    ("tank_v", "Voltage source → Rs → [ (L + RW) ∥ C ∥ RL ]"),
    ("ideal_i", "Current source → [ R ∥ L ∥ C ] (ideal)"),
]
OUTPUTS = [("tank", "Across the tank (band-pass)"), ("rs", "Across Rs (source-current sensing, notch)")]


def _bracket_extremum(fun, lo, hi, maximize=True, n=4001):
    fs = np.geomspace(lo, hi, n)
    vals = fun(fs)
    k = int(np.argmax(vals) if maximize else np.argmin(vals))
    a, b = fs[max(k - 1, 0)], fs[min(k + 1, n - 1)]
    # golden-section on log f
    g = (math.sqrt(5) - 1) / 2
    la, lb = math.log(a), math.log(b)
    sign = 1 if maximize else -1
    for _ in range(200):
        c, d = lb - g * (lb - la), la + g * (lb - la)
        fc_, fd_ = sign * float(fun(np.array([math.exp(c)]))[0]), sign * float(fun(np.array([math.exp(d)]))[0])
        if fc_ > fd_:
            lb = d
        else:
            la = c
        if lb - la < 1e-13:
            break
    fx = math.exp((la + lb) / 2)
    return fx, float(fun(np.array([fx]))[0]), k in (0, n - 1)


def _bisect(fun, a, b, target):
    fa = float(fun(np.array([a]))[0]) - target
    for _ in range(200):
        m = math.sqrt(a * b)
        fm = float(fun(np.array([m]))[0]) - target
        if (fa < 0) == (fm < 0):
            a, fa = m, fm
        else:
            b = m
        if b / a - 1 < 1e-14:
            break
    return math.sqrt(a * b)


def _parallel(inp):
    arr = inp.options["arrangement"]
    conv = CONV_LABEL[inp.options["vconv"]]
    L, C = inp.need("L", positive=True), inp.need("C", positive=True)
    w0 = 1 / math.sqrt(L * C)
    f0 = w0 / TWO_PI
    r = Result("Parallel resonance — " + dict(ARRANGEMENTS)[arr])
    r.symbols = {"fr": "resonant frequency", "f0": "ideal LC frequency 1/(2π√LC)", "Q": "quality factor",
                 "BW": "bandwidth between half-power points", "f1": "lower half-power frequency",
                 "f2": "upper half-power frequency", "RW": "inductor winding resistance (in series with L)",
                 "Rs": "source / current-sensing series resistance", "RL": "load resistor across the tank"}
    r.add("f0", "ideal LC frequency 1/(2π√LC)", f0, "frequency")
    f_user = inp.opt("f", nonneg=True)

    if arr == "ideal_i":
        I = inp.need("I", nonneg=True)
        R = inp.need("R", positive=True)
        rl = inp.opt("RL", positive=True)
        reff = R if rl is None else R * rl / (R + rl)
        if rl is not None:
            r.add("R∥RL", "effective parallel resistance", reff, "resistance")
            r.formulas.append("Reff = R ∥ RL")
        Q = reff * math.sqrt(C / L)
        bw = 1 / (TWO_PI * reff * C)
        a = 1 / (2 * reff * C)
        w1, w2 = -a + math.sqrt(a * a + w0 * w0), a + math.sqrt(a * a + w0 * w0)
        r.formulas += ["fr = 1 / (2π √(L C))  (Z is purely resistive and maximal here)", "Q = Reff √(C/L) = Reff / (ωr L)",
                       "BW = 1 / (2π Reff C)  (exact)", "f1,2 = [∓1/(2 Reff C) + √((1/(2 Reff C))² + ωr²)] / 2π  (exact)",
                       "V = I × Z;  at fr, Z = Reff so V = I × Reff"]
        r.add("fr", "resonant frequency", f0, "frequency", primary=True, note="exact")
        r.add("BW", "bandwidth", bw, "frequency", primary=True, note="exact")
        r.add("Q", "quality factor", Q, "ratio")
        r.add("f1", "lower half-power frequency", w1 / TWO_PI, "frequency", note="exact")
        r.add("f2", "upper half-power frequency", w2 / TWO_PI, "frequency", note="exact")
        vpk = I * reff
        r.add("V @ fr", "tank voltage at resonance (peak of the response)", vpk, "voltage", primary=True, note=conv)
        r.add("IL @ fr", "inductor current at resonance", vpk / (w0 * L), "current", note=conv)
        r.add("IC @ fr", "capacitor current at resonance", vpk * w0 * C, "current", note=conv)
        r.substitutions += [f"Q = {sig(reff)} × √({sig(C)}/{sig(L)}) = {Q:.6g}",
                            f"BW = 1 / (2π × {sig(reff)} × {sig(C)}) = {q(bw, 'frequency')}",
                            f"V(fr) = {q(I, 'current')} × {q(reff, 'resistance')} = {q(vpk, 'voltage')}"]

        def h(f):  # normalised: Z/Reff
            w = TWO_PI * np.asarray(f, dtype=float)
            return 1 / (reff * (1 / reff + 1 / (1j * w * L) + 1j * w * C))

        if f_user:
            z = reff * complex(h(np.array([f_user]))[0])
            r.add("Z @ f", "tank impedance at f", z, "impedance")
            r.add("V @ f", f"tank voltage at f = {fmt(f_user, 'frequency')}", I * abs(z), "voltage", primary=True, note=conv)
        r.steps.append("IL and IC at resonance are Q times the source current and circulate inside the tank.")
        r.assumptions.append("Ideal model: lossless L and C, ideal current source; all losses are lumped in R (and RL).")
        r.plot = PlotSpec("freq", r.title, h, f_min=f0 / 1000, f_max=f0 * 1000, vin=vpk, vin_label=f"V ({conv})",
                          markers=[("fr", f0), ("f1", w1 / TWO_PI), ("f2", w2 / TWO_PI)] + ([("f", f_user)] if f_user else []),
                          band=(w1 / TWO_PI, w2 / TWO_PI), hlines=[("−3 dB", 1 / math.sqrt(2))], ratio_label="V / (I·Reff)")
        return r

    # ---------------- voltage source -> Rs -> tank (L+RW) ∥ C ∥ RL
    out = inp.options["output"]
    vin = inp.need("Vin", nonneg=True)
    rs = inp.need("Rs", nonneg=True)
    rw = inp.opt("RW", 0.0, nonneg=True)
    rl = inp.opt("RL", positive=True)
    if rs == 0:
        raise CalcError("With Rs = 0 the ideal source fixes the tank voltage at Vin at every frequency "
                        "(and the Rs voltage is zero). Enter the source / sensing resistance Rs > 0.")

    def ztank(f):
        w = TWO_PI * np.asarray(f, dtype=float)
        y = 1 / (rw + 1j * w * L) + 1j * w * C
        if rl is not None:
            y = y + 1 / rl
        return 1 / y

    def h(f):
        zt = ztank(f)
        return zt / (rs + zt) if out == "tank" else rs / (rs + zt)

    mag = lambda f: np.abs(h(f))  # noqa: E731
    r.formulas += ["Ztank = 1 / [ 1/(RW + jωL) + jωC + 1/RL ]",
                   "Vout/Vin = Ztank / (Rs + Ztank)" if out == "tank" else "Vout/Vin = Rs / (Rs + Ztank)  (proportional to source current)"]
    # unity-power-factor frequency of the lossy coil ∥ C (independent of resistive RL and Rs)
    disc = 1 / (L * C) - (rw / L) ** 2
    if disc > 0:
        fupf = math.sqrt(disc) / TWO_PI
        r.formulas.append("fr(unity pf) = (1/2π) √(1/(LC) − (RW/L)²)  (exact, tank impedance purely resistive)")
        r.substitutions.append(f"fr = (1/2π) √(1/({sig(L)}×{sig(C)}) − ({sig(rw)}/{sig(L)})²) = {q(fupf, 'frequency')}")
        r.add("fr (unity pf)", "frequency where the tank is purely resistive", fupf, "frequency", primary=True, note="exact")
        if rw > 0:
            r.add("Q coil", "coil quality factor ωr L / RW at fr", TWO_PI * fupf * L / rw, "ratio")
            r.add("R_D", "tank resistance at fr = L / (RW C) (dynamic resistance, before RL)", L / (rw * C), "resistance")
        else:
            r.add("Ztank @ fr", "tank impedance at fr", None, text="∞ (ideal lossless tank)" if rl is None else f"{fmt(rl, 'impedance')} (RL alone)")
    else:
        fupf = None
        r.warnings.append("RW ≥ √(L/C): the coil is so lossy that the tank never becomes purely resistive; no unity-power-factor "
                          "resonance exists.")
    lo, hi = f0 / 100, f0 * 100
    if out == "tank":
        fpk, hpk, edge = _bracket_extremum(mag, lo, hi, maximize=True)
        if edge or hpk <= 0:
            raise CalcError("The response has no peak in the band f0/100 … 100·f0; this arrangement is too heavily damped "
                            "for a meaningful resonance/bandwidth.")
        target = hpk / math.sqrt(2)
        if float(mag(np.array([lo]))[0]) >= target or float(mag(np.array([hi]))[0]) >= target:
            r.warnings.append("The response does not fall 3 dB below its peak within f0/100 … 100·f0 on both sides; "
                              "bandwidth not reported.")
            f1 = f2 = None
        else:
            f1 = _bisect(mag, lo, fpk, target)
            f2 = _bisect(mag, fpk, hi, target)
        r.add("f peak", "frequency of maximum output (numerical, exact model)", fpk, "frequency", primary=True)
        r.add("Vout @ peak", "maximum output across the tank", vin * hpk, "voltage", primary=True, note=conv)
        r.add("Insertion loss", "peak gain in dB", db(hpk), "decibel")
        if f1:
            bw = f2 - f1
            r.add("f1", "lower half-power frequency (peak/√2)", f1, "frequency", note="numerical, exact model")
            r.add("f2", "upper half-power frequency (peak/√2)", f2, "frequency", note="numerical, exact model")
            r.add("BW", "bandwidth f2 − f1", bw, "frequency", primary=True, note="numerical, exact model")
            r.add("Q loaded", "f peak / BW", fpk / bw, "ratio")
            r.steps.append("f1 and f2 are where the output is 1/√2 of its own peak (not of Vin), found numerically "
                           "from the exact transfer function, so insertion loss from Rs, RW and RL is included.")
        r.steps.append("The peak of the tank voltage, the maximum of |Ztank| and the unity-power-factor frequency differ "
                       "slightly when RW > 0; all three coincide only for an ideal tank.")
        markers = [("f peak", fpk)] + ([("f1", f1), ("f2", f2)] if f1 else [])
        band = (f1, f2) if f1 else None
        hl = [("peak", hpk), ("peak −3 dB", hpk / math.sqrt(2))]
    else:
        fmin, hmin, edge = _bracket_extremum(mag, lo, hi, maximize=False)
        if edge:
            raise CalcError("No current minimum between f0/100 and 100·f0 for these values.")
        r.add("f min", "frequency of minimum source current (numerical, exact model)", fmin, "frequency", primary=True)
        r.add("Vout @ min", "minimum voltage across Rs", vin * hmin, "voltage", primary=True, note=conv)
        r.add("I @ min", "minimum source current", vin * hmin / rs, "current", note=conv)
        r.add("Notch depth", "minimum gain in dB", db(hmin), "decibel")
        r.steps.append("Across Rs the response is a notch (the tank blocks current at resonance). A −3 dB bandwidth "
                       "relative to a peak is not defined for this output, so none is reported.")
        markers = [("f min", fmin)]
        band = None
        hl = []
    if fupf:
        markers.append(("fr upf", fupf))
    if f_user:
        if f_user == 0:
            hv = complex((rw / (rs + rw)) if out == "tank" else rs / (rs + rw)) if rw > 0 or out == "tank" else 0
            r.add("Vout @ f", "output at DC", abs(hv) * vin if rw > 0 else (0.0 if out == "tank" else vin), "voltage",
                  note=conv)
            r.steps.append("At DC the inductor is a short (only RW remains), the capacitor is open.")
        else:
            hv = complex(h(np.array([f_user]))[0])
            zt = complex(ztank(np.array([f_user]))[0])
            r.add("Ztank @ f", "tank impedance at f", zt, "impedance")
            r.add("Vout @ f", f"output at f = {fmt(f_user, 'frequency')}", vin * abs(hv), "voltage", primary=True, note=conv)
            i_s = vin / abs(rs + zt)
            r.add("I source @ f", "source current at f", i_s, "current", note=conv)
            vt = i_s * abs(zt)
            r.add("IL @ f", "coil current at f", vt / abs(complex(rw, TWO_PI * f_user * L)), "current", note=conv)
            r.add("IC @ f", "capacitor current at f", vt * TWO_PI * f_user * C, "current", note=conv)
            markers.append(("f", f_user))
    r.assumptions += ["This arrangement only: ideal voltage source with series resistance Rs; coil = L in series with RW; "
                      "ideal capacitor C; optional resistive load RL across the tank. Other parallel arrangements need "
                      "their own analysis and are not approximated with these results.",
                      f"Voltages use the {conv} convention."]
    r.plot = PlotSpec("freq", r.title, h, f_min=f0 / 1000, f_max=f0 * 1000, vin=vin, vin_label=f"V ({conv})",
                      markers=markers, band=band, hlines=hl)
    return r


register(Calculator(
    "parallel_rlc", CAT, "Parallel resonance (choose arrangement)", _parallel,
    fields=[
        Field("Vin", "Vin", "source voltage amplitude", "voltage", "V", "10", show_if=lambda o, u: o["arrangement"] == "tank_v"),
        Field("I", "I", "source current amplitude", "current", "mA", "1", show_if=lambda o, u: o["arrangement"] == "ideal_i"),
        Field("Rs", "Rs", "series source / sensing resistance", "resistance", "Ω", "1000", show_if=lambda o, u: o["arrangement"] == "tank_v"),
        Field("R", "R", "parallel resistance across the tank", "resistance", "kΩ", "10", show_if=lambda o, u: o["arrangement"] == "ideal_i"),
        Field("L", "L", "inductance", "inductance", "mH", "100"),
        Field("RW", "RW", "inductor winding resistance (series with L)", "resistance", "Ω", "1", optional=True,
              show_if=lambda o, u: o["arrangement"] == "tank_v"),
        Field("C", "C", "capacitance", "capacitance", "nF", "47"),
        Field("RL", "RL", "load resistor across the tank (blank = none)", "resistance", "kΩ", "", optional=True),
        Field("f", "f", "frequency of interest (blank = none)", "frequency", "kHz", "", optional=True),
    ],
    options=[Option("arrangement", "Circuit arrangement", ARRANGEMENTS),
             Option("output", "Output measured", OUTPUTS), VOLTAGE_CONVENTION],
    description="Parallel tanks for an explicitly chosen arrangement and output location; exact or numerical-exact results.",
    keywords="parallel resonance tank lc winding resistance q factor bandwidth current source notch",
    diagram=lambda o, u: (
        ladder("isrc", shunts=[[("R", "R")], [("L", "L")], [("C", "C")], [("R", "RL")]], out={"across": "shunts"},
               source_label="I", note="output: voltage across the tank")
        if o["arrangement"] == "ideal_i" else
        ladder("ac", series=[("R", "Rs")], shunts=[[("R", "RW"), ("L", "L")], [("C", "C")], [("R", "RL")]],
               out={"across": "shunts"} if o["output"] == "tank" else {"across": "series", "index": 0},
               note="output across the tank" if o["output"] == "tank" else "output across Rs (∝ source current)")),
))
