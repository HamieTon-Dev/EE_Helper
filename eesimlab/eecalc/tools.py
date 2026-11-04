"""Stage 3b - additional tools: complex numbers, impedance networks, AC power, transformer,
balanced three-phase, frequency/period."""
from __future__ import annotations

import cmath
import math

from eesimlab.eecalc.core import CONV_LABEL, TWO_PI, VOLTAGE_CONVENTION, CalcError, Calculator, Field, Option, Result, register, to_rms
from eesimlab.eecalc.units import fmt, q, sig

CAT = "AC tools"


# ======================================================================= complex conversion
def _complex(inp):
    r = Result("Complex number conversion")
    r.symbols = {"a": "real part", "b": "imaginary part", "M": "magnitude", "θ": "angle from the positive real axis"}
    if inp.unknown == "polar":
        a, b = inp.need("a"), inp.need("b")
        z = complex(a, b)
        r.formulas += ["M = √(a² + b²)", "θ = atan2(b, a)  (quadrant-correct)"]
        r.substitutions.append(f"M = √({sig(a)}² + {sig(b)}²) = {sig(abs(z))}")
        r.add("M", "magnitude", abs(z), "number", primary=True)
        if z == 0:
            r.add("θ", "angle", None, text="undefined (zero has no direction)", primary=True)
        else:
            r.add("θ", "angle", math.atan2(b, a), "angle", primary=True)
            r.substitutions.append(f"θ = atan2({sig(b)}, {sig(a)}) = {sig(math.degrees(math.atan2(b, a)))}°")
    else:
        m, th = inp.need("M", nonneg=True), inp.need("theta")
        z = cmath.rect(m, th)
        r.formulas += ["a = M cos θ", "b = M sin θ"]
        r.substitutions.append(f"a = {sig(m)} × cos({sig(math.degrees(th))}°) = {sig(z.real)};  b = {sig(m)} × sin({sig(math.degrees(th))}°) = {sig(z.imag)}")
        r.add("a", "real part", z.real, "number", primary=True)
        r.add("b", "imaginary part", z.imag, "number", primary=True)
    r.add("z", "rectangular and polar form", z, "number")
    r.assumptions.append("Angles are measured counter-clockwise from the positive real axis.")
    return r


register(Calculator(
    "complex_convert", CAT, "Complex numbers: rectangular ↔ polar", _complex,
    fields=[
        Field("a", "a", "real part", "number", "", "3", show_if=lambda o, u: u == "polar"),
        Field("b", "b", "imaginary part", "number", "", "4", show_if=lambda o, u: u == "polar"),
        Field("M", "M", "magnitude", "number", "", "5", show_if=lambda o, u: u == "rect"),
        Field("theta", "θ", "angle", "angle", "°", "53.13", show_if=lambda o, u: u == "rect"),
    ],
    unknowns=[("polar", "Rectangular a + jb → polar M∠θ"), ("rect", "Polar M∠θ → rectangular a + jb")],
    description="Convert between a + jb and M∠θ (degrees or radians).",
    keywords="complex rectangular polar phasor angle magnitude convert",
))


# ======================================================================= impedance network
N_Z = 4


def _zfields():
    out = []
    for i in range(1, N_Z + 1):
        dflt = {1: ("100", "50", "100", "30"), 2: ("200", "-150", "250", "-36.87")}.get(i, ("", "", "", ""))
        rect = lambda o, u: o["form"] == "rect"  # noqa: E731
        polar = lambda o, u: o["form"] == "polar"  # noqa: E731
        out += [
            Field(f"r{i}", f"R{i}", f"Z{i} real part (resistance)", "impedance", "Ω", dflt[0], optional=i > 2, show_if=rect),
            Field(f"x{i}", f"X{i}", f"Z{i} imaginary part (reactance, + inductive, − capacitive)", "impedance", "Ω", dflt[1],
                  optional=i > 2, show_if=rect),
            Field(f"m{i}", f"|Z{i}|", f"Z{i} magnitude", "impedance", "Ω", dflt[2], optional=i > 2, show_if=polar),
            Field(f"a{i}", f"θ{i}", f"Z{i} angle", "angle", "°", dflt[3], optional=i > 2, show_if=polar),
        ]
    return out


def _znet(inp):
    form, conn = inp.options["form"], inp.options["connection"]
    zs = []
    for i in range(1, N_Z + 1):
        if form == "rect":
            a, b = inp.values.get(f"r{i}"), inp.values.get(f"x{i}")
            if a is None and b is None:
                continue
            z = complex(a or 0.0, b or 0.0)
        else:
            m, th = inp.values.get(f"m{i}"), inp.values.get(f"a{i}")
            if m is None and th is None:
                continue
            if m is None:
                raise CalcError(f"Enter the magnitude of Z{i}.")
            if m < 0:
                raise CalcError(f"|Z{i}| cannot be negative.")
            z = cmath.rect(m, th or 0.0)
        if z.real < -1e-12:
            raise CalcError(f"Z{i} has a negative resistance; passive impedances have R ≥ 0.")
        zs.append((i, z))
    if len(zs) < 2:
        raise CalcError("Enter at least two impedances.")
    r = Result(f"{'Series' if conn == 'series' else 'Parallel'} impedance combination")
    r.symbols = {"Zeq": "equivalent impedance", "Zn": "individual impedances", "Y": "admittance 1/Z"}
    for i, z in zs:
        r.add(f"Z{i}", f"impedance {i}", z, "impedance")
    if conn == "series":
        zeq = sum(z for _, z in zs)
        r.formulas.append("Zeq = Z1 + Z2 + …  (add real and imaginary parts)")
        r.substitutions.append("Zeq = " + " + ".join(sig(z) for _, z in zs) + f" = {sig(zeq)} Ω")
    else:
        if any(z == 0 for _, z in zs):
            r.add("Zeq", "equivalent impedance", 0j, "impedance", primary=True, text="0 Ω (one branch is a short circuit)")
            return r
        y = sum(1 / z for _, z in zs)
        r.formulas.append("1/Zeq = 1/Z1 + 1/Z2 + …")
        r.substitutions.append("Y = " + " + ".join(f"1/{sig(z)}" for _, z in zs) + f" = {sig(y)} S")
        if abs(y) < 1e-15 * max(abs(1 / z) for _, z in zs):
            r.add("Zeq", "equivalent impedance", None, "impedance", primary=True,
                  text="∞ (branch admittances cancel: ideal parallel resonance)")
            return r
        zeq = 1 / y
        r.add("Y", "total admittance", y, "conductance")
    r.add("Zeq", "equivalent impedance", zeq, "impedance", primary=True)
    r.add("|Zeq|", "magnitude", abs(zeq), "impedance")
    r.add("θeq", "angle (+ inductive, − capacitive)", math.atan2(zeq.imag, zeq.real), "angle")
    r.assumptions.append("Impedances evaluated at one common frequency; no mutual coupling.")
    return r


register(Calculator(
    "impedance_network", CAT, "Impedance combination (complex)", _znet,
    fields=_zfields(),
    options=[Option("connection", "Connection", [("series", "Series"), ("parallel", "Parallel")]),
             Option("form", "Enter impedances as", [("rect", "Rectangular R + jX"), ("polar", "Polar |Z|∠θ")])],
    description="Series or parallel combination of up to four complex impedances.",
    keywords="impedance complex series parallel combine phasor",
    diagram=lambda o, u: {"kind": "chain", "mode": o["connection"], "items": [("Z", "Z1"), ("Z", "Z2"), ("Z", "Z3")]},
))


# ======================================================================= AC power
def _ac_power(inp):
    mode = inp.options["mode"]
    r = Result("AC power (sinusoidal, single-phase)")
    r.symbols = {"P": "real (active) power", "Q": "reactive power (+ inductive / lagging)", "S": "apparent power",
                 "PF": "power factor cos θ", "θ": "angle by which current lags voltage (impedance angle)",
                 "V": "RMS voltage", "I": "RMS current"}
    conv = inp.options["vconv"]
    v_note = ""
    if mode == "VIθ":
        V = to_rms(inp.need("V", nonneg=True), conv)
        I = to_rms(inp.need("I", nonneg=True), conv)
        th = inp.need("theta")
        if abs(th) > math.pi / 2 + 1e-12:
            raise CalcError("|θ| must not exceed 90° for a passive load (it would mean negative real power).")
        S = V * I
        P, Q = S * math.cos(th), S * math.sin(th)
        r.formulas += ["S = V I", "P = V I cos θ", "Q = V I sin θ", "PF = cos θ"]
        r.substitutions.append(f"S = {q(V, 'voltage')} × {q(I, 'current')} = {sig(S)} VA")
        if conv != "rms":
            v_note = f"V and I entered as {CONV_LABEL[conv]} were converted to RMS first."
    elif mode == "PQ":
        P, Q = inp.need("P", nonneg=True), inp.need("Q")
        S = math.hypot(P, Q)
        th = math.atan2(Q, P) if S else 0.0
        r.formulas += ["S = √(P² + Q²)", "PF = P / S", "θ = atan(Q / P)"]
        r.substitutions.append(f"S = √({sig(P)}² + {sig(Q)}²) = {sig(S)} VA")
    else:  # S and PF
        S = inp.need("S", nonneg=True)
        pf = inp.need("PF")
        if not 0 <= pf <= 1:
            raise CalcError("Power factor must be between 0 and 1.")
        th = math.acos(pf) * (1 if inp.options["lag"] == "lag" else -1)
        P, Q = S * pf, S * math.sin(th)
        r.formulas += ["P = S × PF", "Q = S sin(acos PF)  (+ lagging, − leading)"]
        r.substitutions.append(f"P = {sig(S)} × {sig(pf)} = {sig(P)} W")
    pf = P / S if S else 1.0
    r.add("P", "real power", P, "power", primary=True)
    r.add("Q", "reactive power", Q, "reactive_power", primary=True)
    r.add("S", "apparent power", S, "apparent_power", primary=True)
    r.add("PF", "power factor", pf, "ratio", primary=True,
          note="lagging (inductive)" if Q > 0 else "leading (capacitive)" if Q < 0 else "unity")
    r.add("θ", "phase angle (current lags voltage by θ)", th, "angle")
    if mode != "VIθ":
        v = inp.opt("V", nonneg=True)
        if v:
            V = to_rms(v, conv)
            r.add("I", "RMS current S / V", S / V, "current", note="RMS")
    if v_note:
        r.steps.append(v_note)
    r.assumptions.append("Sinusoidal steady state, single phase. Power formulas use RMS values.")
    return r


register(Calculator(
    "ac_power", CAT, "AC power: P, Q, S and power factor", _ac_power,
    fields=[
        Field("V", "V", "voltage amplitude", "voltage", "V", "120", show_if=lambda o, u: True, optional=True),
        Field("I", "I", "current amplitude", "current", "A", "5", show_if=lambda o, u: o["mode"] == "VIθ"),
        Field("theta", "θ", "angle by which current lags voltage (negative if it leads)", "angle", "°", "30",
              show_if=lambda o, u: o["mode"] == "VIθ"),
        Field("P", "P", "real power", "power", "W", "500", show_if=lambda o, u: o["mode"] == "PQ"),
        Field("Q", "Q", "reactive power (+ inductive, − capacitive)", "reactive_power", "var", "300", show_if=lambda o, u: o["mode"] == "PQ"),
        Field("S", "S", "apparent power", "apparent_power", "VA", "1000", show_if=lambda o, u: o["mode"] == "SPF"),
        Field("PF", "PF", "power factor (0 to 1)", "ratio", "", "0.8", show_if=lambda o, u: o["mode"] == "SPF"),
    ],
    options=[Option("mode", "Known values", [("VIθ", "V, I and phase angle θ"), ("PQ", "P and Q"), ("SPF", "S and power factor")]),
             Option("lag", "Power factor is", [("lag", "Lagging (inductive)"), ("lead", "Leading (capacitive)")]),
             VOLTAGE_CONVENTION],
    description="Real, reactive and apparent power and power factor for a sinusoidal single-phase circuit.",
    keywords="power factor real reactive apparent var va watt pf",
))


# ======================================================================= transformer
def _transformer(inp):
    r = Result("Ideal transformer")
    r.symbols = {"Np": "primary turns", "Ns": "secondary turns", "a": "turns ratio Np : Ns (primary over secondary)",
                 "Vp": "primary voltage", "Vs": "secondary voltage", "Ip": "primary current", "Is": "secondary current",
                 "ZL": "load on the secondary", "Zin": "load as seen from the primary"}
    np_, ns = inp.opt("Np", positive=True), inp.opt("Ns", positive=True)
    vp, vs = inp.opt("Vp", nonneg=True), inp.opt("Vs", nonneg=True)
    ip, is_ = inp.opt("Ip", nonneg=True), inp.opt("Is", nonneg=True)
    zl = inp.opt("ZL", positive=True)
    ratios = []
    if np_ and ns:
        ratios.append(("turns", np_ / ns))
    if vp and vs:
        ratios.append(("voltages", vp / vs))
    if ip and is_:
        ratios.append(("currents", is_ / ip))
    if not ratios:
        raise CalcError("Give the turns ratio (Np and Ns), or both voltages, or both currents.")
    a = ratios[0][1]
    for name, val in ratios[1:]:
        if not math.isclose(val, a, rel_tol=1e-6):
            raise CalcError(f"The {ratios[0][0]} give a = {sig(a)} but the {name} give a = {sig(val)}; an ideal transformer "
                            "cannot satisfy both.")
    r.formulas += ["a = Np / Ns = Vp / Vs = Is / Ip  (ratio direction: primary ÷ secondary)", "Zin = a² × ZL"]
    r.add("a = Np:Ns", "turns ratio, primary : secondary", a, "ratio", primary=True,
          note="step-down" if a > 1 else "step-up" if a < 1 else "isolation (1:1)")
    r.add("Ns:Np", "inverse ratio, secondary : primary", 1 / a, "ratio")
    if vp is None and vs is not None:
        vp = vs * a
    if vs is None and vp is not None:
        vs = vp / a
    if vs is not None and zl and is_ is None:
        is_ = vs / zl
        r.formulas.append("Is = Vs / ZL")
    if ip is None and is_ is not None:
        ip = is_ / a
    if is_ is None and ip is not None:
        is_ = ip * a
    for sym, val, qty, meaning in (("Vp", vp, "voltage", "primary voltage"), ("Vs", vs, "voltage", "secondary voltage"),
                                   ("Ip", ip, "current", "primary current"), ("Is", is_, "current", "secondary current")):
        if val is not None:
            r.add(sym, meaning, val, qty, primary=sym in ("Vs", "Is"))
    if vp is not None:
        r.substitutions.append(f"Vs = Vp / a = {q(vp, 'voltage')} / {sig(a)} = {q(vs, 'voltage')}")
    if zl:
        r.add("Zin", "load reflected to the primary", a * a * zl, "impedance")
        r.substitutions.append(f"Zin = {sig(a)}² × {q(zl, 'impedance')} = {q(a * a * zl, 'impedance')}")
    if vp is not None and ip is not None:
        r.add("S", "apparent power (same on both sides)", vp * ip, "apparent_power")
    r.assumptions.append("Ideal transformer: no losses, no magnetising current, perfect coupling; voltages and currents in "
                         "one consistent convention (e.g. all RMS).")
    return r


register(Calculator(
    "transformer", CAT, "Ideal transformer ratios", _transformer,
    fields=[
        Field("Np", "Np", "primary turns", "turns", "turns", "500", optional=True),
        Field("Ns", "Ns", "secondary turns", "turns", "turns", "50", optional=True),
        Field("Vp", "Vp", "primary voltage", "voltage", "V", "120", optional=True),
        Field("Vs", "Vs", "secondary voltage", "voltage", "V", "", optional=True),
        Field("Ip", "Ip", "primary current", "current", "A", "", optional=True),
        Field("Is", "Is", "secondary current", "current", "A", "", optional=True),
        Field("ZL", "ZL", "resistive load on the secondary", "impedance", "Ω", "8", optional=True),
    ],
    description="Turns, voltage and current ratios and reflected impedance (a = Np/Ns, primary ÷ secondary).",
    keywords="transformer turns ratio primary secondary step-down step-up reflected impedance",
    diagram=lambda o, u: {"kind": "transformer"},
))


# ======================================================================= three phase
def _three_phase(inp):
    conn = inp.options["connection"]
    vkind, ikind = inp.options["vgiven"], inp.options["igiven"]
    V, I = inp.need("V", nonneg=True), inp.need("I", nonneg=True)
    pf = inp.need("PF")
    if not 0 <= pf <= 1:
        raise CalcError("Power factor must be between 0 and 1.")
    s3 = math.sqrt(3)
    if conn == "Y":
        vl, vph = (V, V / s3) if vkind == "line" else (V * s3, V)
        il = iph = I
        r = Result("Balanced three-phase (wye / star)")
        r.formulas += ["Wye: VL = √3 × Vph,  IL = Iph"]
    else:
        vl = vph = V
        il, iph = (I, I / s3) if ikind == "line" else (I * s3, I)
        r = Result("Balanced three-phase (delta)")
        r.formulas += ["Delta: VL = Vph,  IL = √3 × Iph"]
    if conn == "Y" and ikind == "phase":
        r.steps.append("In wye, line and phase currents are the same current.")
    if conn == "D" and vkind == "phase":
        r.steps.append("In delta, line and phase voltages are the same voltage.")
    th = math.acos(pf) * (1 if inp.options["lag"] == "lag" else -1)
    S = s3 * vl * il
    P, Q = S * pf, S * math.sin(th)
    r.formulas += ["S = √3 VL IL = 3 Vph Iph", "P = √3 VL IL cos θ", "Q = √3 VL IL sin θ"]
    r.substitutions.append(f"S = √3 × {q(vl, 'voltage')} × {q(il, 'current')} = {sig(S)} VA")
    r.add("P", "total real power", P, "power", primary=True)
    r.add("Q", "total reactive power", Q, "reactive_power", primary=True)
    r.add("S", "total apparent power", S, "apparent_power", primary=True)
    r.add("VL", "line voltage (between two lines)", vl, "voltage", note="line, RMS")
    r.add("Vph", "phase voltage (across one load branch)", vph, "voltage", note="phase, RMS")
    r.add("IL", "line current (in each supply wire)", il, "current", note="line, RMS")
    r.add("Iph", "phase current (in each load branch)", iph, "current", note="phase, RMS")
    if iph:
        r.add("Zph", "impedance magnitude of each branch Vph/Iph", vph / iph, "impedance")
    r.symbols = {"VL": "line-to-line voltage", "Vph": "voltage across one phase of the load", "IL": "current in a line conductor",
                 "Iph": "current in one phase of the load", "P": "total real power", "Q": "total reactive power",
                 "S": "total apparent power", "PF": "power factor cos θ"}
    r.assumptions.append("Balanced, sinusoidal, all values RMS; PF is the per-phase load power factor.")
    return r


register(Calculator(
    "three_phase", CAT, "Balanced three-phase power", _three_phase,
    fields=[
        Field("V", "V", "voltage (line or phase, as selected), RMS", "voltage", "V", "400"),
        Field("I", "I", "current (line or phase, as selected), RMS", "current", "A", "10"),
        Field("PF", "PF", "power factor (0 to 1)", "ratio", "", "0.85"),
    ],
    options=[Option("connection", "Load connection", [("Y", "Wye (star)"), ("D", "Delta")]),
             Option("vgiven", "Voltage entered is", [("line", "Line-to-line VL"), ("phase", "Phase Vph")]),
             Option("igiven", "Current entered is", [("line", "Line IL"), ("phase", "Phase Iph")]),
             Option("lag", "Power factor is", [("lag", "Lagging (inductive)"), ("lead", "Leading (capacitive)")])],
    description="Line and phase values, total P, Q and S for balanced wye or delta loads.",
    keywords="three phase 3-phase wye star delta line phase power",
))


# ======================================================================= frequency / period
def _freq(inp):
    u = inp.unknown
    r = Result("Frequency, period and angular frequency")
    r.symbols = {"f": "frequency", "T": "period (time of one cycle)", "ω": "angular frequency 2πf"}
    if u == "f":
        T = inp.need("T", positive=True)
        f = 1 / T
        r.formulas.append("f = 1 / T")
        r.substitutions.append(f"f = 1 / {q(T, 'time')} = {q(f, 'frequency')}")
    elif u == "T":
        f = inp.need("f", positive=True)
        r.formulas.append("T = 1 / f")
        r.substitutions.append(f"T = 1 / {q(f, 'frequency')} = {q(1 / f, 'time')}")
    else:
        w = inp.need("w", positive=True)
        f = w / TWO_PI
        r.formulas.append("f = ω / (2π)")
        r.substitutions.append(f"f = {sig(w)} / 2π = {q(f, 'frequency')}")
    r.add("f", "frequency", f, "frequency", primary=u != "T")
    r.add("T", "period", 1 / f, "time", primary=u == "T")
    r.add("ω", "angular frequency", TWO_PI * f, "angular_frequency")
    r.formulas.append("ω = 2π f")
    return r


register(Calculator(
    "freq_period", CAT, "Frequency ↔ period ↔ ω", _freq,
    fields=[
        Field("f", "f", "frequency", "frequency", "kHz", "1", show_if=lambda o, u: u == "T"),
        Field("T", "T", "period", "time", "ms", "1", show_if=lambda o, u: u == "f"),
        Field("w", "ω", "angular frequency", "angular_frequency", "rad/s", "6283.19", show_if=lambda o, u: u == "w"),
    ],
    unknowns=[("T", "Period T from f"), ("f", "Frequency f from T"), ("w", "Frequency f from ω")],
    description="Convert between frequency, period and angular frequency.",
    keywords="frequency period omega angular hz seconds convert",
))
