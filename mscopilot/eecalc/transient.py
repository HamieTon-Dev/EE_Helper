"""Stage 3a - first-order transients (RC and RL).

  RC: τ = R C,   v(t) = Vf + (V0 − Vf) e^(−t/τ)
  RL: τ = L / R, i(t) = If + (I0 − If) e^(−t/τ)
  time to reach a target x:  t = −τ ln((x − Xf) / (X0 − Xf))
"""
from __future__ import annotations

import math

import numpy as np

from mscopilot.eecalc.basic import ladder
from mscopilot.eecalc.core import CalcError, Calculator, Field, Option, PlotSpec, Result, register
from mscopilot.eecalc.units import fmt, q, sig

CAT = "Transients"


# ======================================================================= time constant
def _tau(inp):
    fam, u = inp.options["family"], inp.unknown
    rs, rq = ("C", "capacitance") if fam == "RC" else ("L", "inductance")
    r = Result(f"{fam} time constant")
    r.symbols = {"τ": "time constant", "R": "resistance", rs: rq, "5τ": "time to practically settle (99.3 %)"}
    if u == "tau":
        R, X = inp.need("R", positive=True), inp.need(rs, positive=True)
        tau = R * X if fam == "RC" else X / R
        r.formulas.append("τ = R × C" if fam == "RC" else "τ = L / R")
        if fam == "RC":
            r.substitutions.append(f"τ = {q(R, 'resistance')} × {q(X, rq)} = {q(tau, 'time')}")
        else:
            r.substitutions.append(f"τ = {q(X, rq)} / {q(R, 'resistance')} = {q(tau, 'time')}")
        r.add("τ", "time constant", tau, "time", primary=True)
    elif u == "R":
        tau, X = inp.need("tau", positive=True), inp.need(rs, positive=True)
        R = tau / X if fam == "RC" else X / tau
        r.formulas.append("R = τ / C" if fam == "RC" else "R = L / τ")
        r.add("R", "resistance", R, "resistance", primary=True)
        r.substitutions.append(f"R = {q(R, 'resistance')}")
    else:
        tau, R = inp.need("tau", positive=True), inp.need("R", positive=True)
        X = tau / R if fam == "RC" else tau * R
        r.formulas.append("C = τ / R" if fam == "RC" else "L = τ × R")
        r.add(rs, rq, X, rq, primary=True)
        r.substitutions.append(f"{rs} = {q(X, rq)}")
    if u != "tau":
        r.add("τ", "time constant", tau, "time")
    r.add("5τ", "settling time (≈ 99.3 % of the change)", 5 * tau, "time")
    r.add("fC", "equivalent first-order cutoff 1/(2πτ)", 1 / (2 * math.pi * tau), "frequency")
    for n in range(1, 6):
        r.steps.append(f"After {n}τ = {fmt(n * tau, 'time')}: {100 * (1 - math.exp(-n)):.2f} % of the total change complete.")
    r.assumptions.append("Single R and a single ideal C or L (first-order circuit).")
    return r


register(Calculator(
    "time_constant", CAT, "Time constant (RC / RL)", _tau,
    fields=[
        Field("R", "R", "resistance", "resistance", "kΩ", "10", show_if=lambda o, u: u != "R"),
        Field("C", "C", "capacitance", "capacitance", "µF", "10", show_if=lambda o, u: o["family"] == "RC" and u != "X"),
        Field("L", "L", "inductance", "inductance", "mH", "10", show_if=lambda o, u: o["family"] == "RL" and u != "X"),
        Field("tau", "τ", "time constant", "time", "ms", "100", show_if=lambda o, u: u != "tau"),
    ],
    options=[Option("family", "Circuit", [("RC", "RC  (τ = R C)"), ("RL", "RL  (τ = L / R)")])],
    unknowns=[("tau", "Time constant τ"), ("R", "Resistance R for a given τ"), ("X", "C or L for a given τ")],
    description="τ = RC or L/R, solved for any variable, with the 1τ…5τ percentages.",
    keywords="time constant tau rc rl settling",
))


# ======================================================================= RC capacitor voltage
def _time_to(target, x0, xf, tau, what, qty):
    if xf == x0:
        raise CalcError(f"The initial and final {what} are equal: nothing changes, so the time is undefined.")
    ratio = (target - xf) / (x0 - xf)
    if math.isclose(target, xf, rel_tol=1e-15, abs_tol=0.0):
        return math.inf
    if not (0 < ratio <= 1):
        lo, hi = sorted((x0, xf))
        raise CalcError(f"The target {what} {sig(target)} must lie between the initial value {sig(x0)} and the final value "
                        f"{sig(xf)} (it approaches the final value but never passes it).")
    return -tau * math.log(ratio)


def _rc_transient(inp):
    u = inp.unknown
    R, C = inp.need("R", positive=True), inp.need("C", positive=True)
    v0, vf = inp.need("V0"), inp.need("Vf")
    tau = R * C
    r = Result("Capacitor voltage (RC transient)")
    r.symbols = {"τ": "time constant R×C", "V0": "capacitor voltage at t = 0", "Vf": "final (steady-state) capacitor voltage",
                 "vC(t)": "capacitor voltage at time t", "i(t)": "charging current at time t", "t": "time after switching"}
    r.add("τ", "time constant", tau, "time")
    r.formulas += ["τ = R C", "vC(t) = Vf + (V0 − Vf) e^(−t/τ)", "i(t) = (Vf − V0)/R × e^(−t/τ)"]
    r.substitutions.append(f"τ = {q(R, 'resistance')} × {q(C, 'capacitance')} = {q(tau, 'time')}")
    mode = "charging" if abs(vf) > abs(v0) else "discharging" if vf == 0 else "changing"
    if u == "v":
        t = inp.need("t", nonneg=True)
        v = vf + (v0 - vf) * math.exp(-t / tau)
        i = (vf - v0) / R * math.exp(-t / tau)
        r.substitutions.append(f"vC({fmt(t, 'time')}) = {sig(vf)} + ({sig(v0)} − {sig(vf)}) e^(−{sig(t)}/{sig(tau)}) = {q(v, 'voltage')}")
        r.add("vC(t)", f"capacitor voltage at t = {fmt(t, 'time')}", v, "voltage", primary=True)
        r.add("i(t)", "capacitor current at t", i, "current")
        r.add("vR(t)", "resistor voltage at t", i * R, "voltage")
        r.add("t/τ", "elapsed time in time constants", t / tau, "ratio")
        done = 100 * (1 - math.exp(-t / tau))
        r.add("Complete", "fraction of the total change completed", done, "percent")
        mark = t
    else:
        if u == "t_v":
            target = inp.need("Vt")
        else:
            p = inp.need("pct", nonneg=True)
            if p >= 100:
                r.add("t", "time to reach 100 %", math.inf, "time", primary=True,
                      text="∞ — an exponential never reaches 100 % (use 99 % or 5τ ≈ 99.3 %)")
                r.plot = _rc_plot(r, v0, vf, tau, None)
                return r
            target = v0 + p / 100 * (vf - v0)
            r.add("v target", f"voltage at {sig(p)} % of the change", target, "voltage")
        t = _time_to(target, v0, vf, tau, "voltage", "voltage")
        r.formulas.append("t = −τ ln((v − Vf) / (V0 − Vf))")
        if math.isinf(t):
            r.add("t", "time to reach the target", t, "time", primary=True, text="∞ — the final value is only approached asymptotically")
            mark = None
        else:
            r.substitutions.append(f"t = −{sig(tau)} × ln(({sig(target)} − {sig(vf)}) / ({sig(v0)} − {sig(vf)})) = {q(t, 'time')}")
            r.add("t", f"time to reach {fmt(target, 'voltage')}", t, "time", primary=True)
            r.add("t/τ", "in time constants", t / tau, "ratio")
            mark = t
    r.add("5τ", "practically settled after", 5 * tau, "time")
    r.steps.append(f"This is a {mode} transient from {fmt(v0, 'voltage')} toward {fmt(vf, 'voltage')}.")
    r.assumptions.append("Single R and ideal C driven by a step from an ideal DC source (Vf = source voltage for charging, "
                         "0 V for discharging through R).")
    r.plot = _rc_plot(r, v0, vf, tau, mark)
    return r


def _rc_plot(r, v0, vf, tau, mark):
    tmax = 5 * tau if mark is None or math.isinf(mark) else max(5 * tau, 1.2 * mark)
    return PlotSpec("time", "Capacitor voltage vs time", lambda t: vf + (v0 - vf) * np.exp(-np.asarray(t) / tau),
                    t_max=tmax, markers=[("τ", tau), ("5τ", 5 * tau)] + ([("t", mark)] if mark else []),
                    hlines=[("Vf", vf)], y_label="vC (V)", y_quantity="voltage")


register(Calculator(
    "rc_transient", CAT, "Capacitor charge / discharge (RC)", _rc_transient,
    fields=[
        Field("R", "R", "series resistance", "resistance", "kΩ", "10"),
        Field("C", "C", "capacitance", "capacitance", "µF", "10"),
        Field("V0", "V0", "initial capacitor voltage (at t = 0)", "voltage", "V", "0"),
        Field("Vf", "Vf", "final steady-state voltage (source voltage when charging, 0 when discharging)", "voltage", "V", "12"),
        Field("t", "t", "time after switching", "time", "ms", "100", show_if=lambda o, u: u == "v"),
        Field("Vt", "v", "target capacitor voltage", "voltage", "V", "6", show_if=lambda o, u: u == "t_v"),
        Field("pct", "%", "target percentage of the total change", "percent", "%", "90", show_if=lambda o, u: u == "t_p"),
    ],
    unknowns=[("v", "Voltage vC(t) at a time t"), ("t_v", "Time to reach a voltage"), ("t_p", "Time to reach a percentage")],
    description="vC(t) = Vf + (V0 − Vf)e^(−t/RC) with any initial and final values.",
    keywords="capacitor charging discharging rc transient exponential voltage time",
    diagram=lambda o, u: ladder("dc", series=[("SW", "t=0"), ("R", "R")], shunts=[[("C", "C")]],
                                out={"across": "shunt", "index": 0}, out_label="vC", source_label="Vs"),
))


# ======================================================================= RL inductor current
def _rl_transient(inp):
    u = inp.unknown
    R, L = inp.need("R", positive=True), inp.need("L", positive=True)
    i0 = inp.need("I0")
    vs = inp.opt("Vs")
    i_f = inp.opt("If")
    if i_f is None:
        if vs is None:
            raise CalcError("Enter the source voltage Vs (If = Vs/R) or the final current If.")
        i_f = vs / R
    tau = L / R
    r = Result("Inductor current (RL transient)")
    r.symbols = {"τ": "time constant L/R", "I0": "inductor current at t = 0", "If": "final steady-state current",
                 "iL(t)": "inductor current at time t", "vL(t)": "inductor voltage at time t", "t": "time after switching"}
    r.add("τ", "time constant", tau, "time")
    r.formulas += ["τ = L / R", "iL(t) = If + (I0 − If) e^(−t/τ)", "vL(t) = L diL/dt = R (If − I0) e^(−t/τ)"]
    r.substitutions.append(f"τ = {q(L, 'inductance')} / {q(R, 'resistance')} = {q(tau, 'time')}")
    if inp.values.get("If") is None:
        r.formulas.append("If = Vs / R")
        r.substitutions.append(f"If = {q(vs, 'voltage')} / {q(R, 'resistance')} = {q(i_f, 'current')}")
    r.add("If", "final current", i_f, "current")
    if u == "i":
        t = inp.need("t", nonneg=True)
        i = i_f + (i0 - i_f) * math.exp(-t / tau)
        vl = R * (i_f - i0) * math.exp(-t / tau)
        r.substitutions.append(f"iL({fmt(t, 'time')}) = {sig(i_f)} + ({sig(i0)} − {sig(i_f)}) e^(−{sig(t)}/{sig(tau)}) = {q(i, 'current')}")
        r.add("iL(t)", f"inductor current at t = {fmt(t, 'time')}", i, "current", primary=True)
        r.add("vL(t)", "inductor voltage at t", vl, "voltage")
        r.add("vR(t)", "resistor voltage at t", i * R, "voltage")
        r.add("Complete", "fraction of the total change completed", 100 * (1 - math.exp(-t / tau)), "percent")
        mark = t
    else:
        if u == "t_i":
            target = inp.need("It")
        else:
            p = inp.need("pct", nonneg=True)
            if p >= 100:
                r.add("t", "time to reach 100 %", math.inf, "time", primary=True,
                      text="∞ — an exponential never reaches 100 % (use 99 % or 5τ ≈ 99.3 %)")
                return r
            target = i0 + p / 100 * (i_f - i0)
            r.add("i target", f"current at {sig(p)} % of the change", target, "current")
        t = _time_to(target, i0, i_f, tau, "current", "current")
        r.formulas.append("t = −τ ln((i − If) / (I0 − If))")
        if math.isinf(t):
            r.add("t", "time to reach the target", t, "time", primary=True, text="∞ — the final value is only approached asymptotically")
            mark = None
        else:
            r.substitutions.append(f"t = −{sig(tau)} × ln(({sig(target)} − {sig(i_f)}) / ({sig(i0)} − {sig(i_f)})) = {q(t, 'time')}")
            r.add("t", f"time to reach {fmt(target, 'current')}", t, "time", primary=True)
            mark = t
    r.add("5τ", "practically settled after", 5 * tau, "time")
    r.assumptions.append("Single R and ideal L (winding resistance included in R) switched to an ideal DC source at t = 0; "
                         "for decay set Vs = 0 (or If = 0).")
    tmax = 5 * tau if mark is None else max(5 * tau, 1.2 * mark)
    r.plot = PlotSpec("time", "Inductor current vs time", lambda t: i_f + (i0 - i_f) * np.exp(-np.asarray(t) / tau),
                      t_max=tmax, markers=[("τ", tau), ("5τ", 5 * tau)] + ([("t", mark)] if mark else []),
                      hlines=[("If", i_f)], y_label="iL (A)", y_quantity="current")
    return r


register(Calculator(
    "rl_transient", CAT, "Inductor current rise / decay (RL)", _rl_transient,
    fields=[
        Field("R", "R", "total series resistance (including winding)", "resistance", "Ω", "100"),
        Field("L", "L", "inductance", "inductance", "mH", "50"),
        Field("Vs", "Vs", "DC source voltage after switching (0 for decay)", "voltage", "V", "10", optional=True),
        Field("If", "If", "final current (blank = Vs/R)", "current", "mA", "", optional=True),
        Field("I0", "I0", "inductor current at t = 0", "current", "mA", "0"),
        Field("t", "t", "time after switching", "time", "ms", "1", show_if=lambda o, u: u == "i"),
        Field("It", "i", "target inductor current", "current", "mA", "50", show_if=lambda o, u: u == "t_i"),
        Field("pct", "%", "target percentage of the total change", "percent", "%", "90", show_if=lambda o, u: u == "t_p"),
    ],
    unknowns=[("i", "Current iL(t) at a time t"), ("t_i", "Time to reach a current"), ("t_p", "Time to reach a percentage")],
    description="iL(t) = If + (I0 − If)e^(−tR/L) with any initial and final values.",
    keywords="inductor current rise decay rl transient exponential time",
    diagram=lambda o, u: ladder("dc", series=[("SW", "t=0"), ("R", "R")], shunts=[[("L", "L")]],
                                out={"across": "shunt", "index": 0}, out_label="vL", source_label="Vs"),
))
