"""Components & practical: transformer winding simulator, relay simulator, Δ–Y, Thevenin/Norton,
stored energy, wire resistance, resistor colour code, E-series, current divider, LED
resistor, battery life, charge."""
from __future__ import annotations

import cmath
import math

import numpy as np

from eesimlab.eecalc.basic import ladder
from eesimlab.eecalc.core import TWO_PI, CalcError, Calculator, Field, Option, PlotSpec, Result, register
from eesimlab.eecalc.units import fmt, q, sig

CAT = "Components & practical"


def _c(z: complex) -> str:
    return f"{z.real:.6g} {'−' if z.imag < 0 else '+'} j{abs(z.imag):.6g}"


# =========================================================================== transformer
def _transformer_sim(inp):
    vp = inp.need("Vp", positive=True)
    f = inp.need("f", positive=True)
    np_, ns = inp.need("Np", positive=True), inp.need("Ns", positive=True)
    r1, r2 = inp.opt("R1", 0.0, nonneg=True), inp.opt("R2", 0.0, nonneg=True)
    x1, x2 = inp.opt("X1", 0.0, nonneg=True), inp.opt("X2", 0.0, nonneg=True)
    rc, xm = inp.opt("Rc", positive=True), inp.opt("Xm", positive=True)
    rl = inp.need("RL", nonneg=True)
    xl = inp.opt("XL", 0.0)
    t_run = inp.opt("t", positive=True)
    va_rating = inp.opt("S_rated", positive=True)
    a = np_ / ns
    zl = complex(rl, xl)
    if zl == 0:
        raise CalcError("A 0 Ω load short-circuits the secondary; enter the load resistance (and reactance).")
    r = Result("Transformer winding simulation (equivalent circuit, RMS values)")
    r.symbols = {"a": "turns ratio Np/Ns (primary ÷ secondary)", "R1, X1": "primary winding resistance / leakage reactance",
                 "R2, X2": "secondary winding resistance / leakage reactance", "Rc": "core-loss resistance (referred to primary)",
                 "Xm": "magnetising reactance (referred to primary)", "E1, E2": "EMF induced in primary / secondary winding",
                 "V2": "secondary terminal voltage at the load", "I2": "secondary (load) current", "Φmax": "peak core flux",
                 "η": "efficiency", "VR": "voltage regulation"}
    zp = complex(r1, x1)
    zs = complex(r2, x2)
    zm = None
    if rc and xm:
        zm = 1 / (1 / rc + 1 / complex(0, xm))
    elif xm:
        zm = complex(0, xm)
    elif rc:
        zm = complex(rc, 0)
    z2p = a * a * (zs + zl)  # secondary + load referred to primary
    zpar = z2p if zm is None else (zm * z2p) / (zm + z2p)
    zin = zp + zpar
    ip = vp / zin
    e1 = vp - ip * zp
    i2p = e1 / z2p
    i2 = a * i2p
    e2 = e1 / a
    v2 = i2 * zl
    im = 0j if zm is None else e1 / zm
    p_load = abs(i2) ** 2 * rl
    q_load = abs(i2) ** 2 * xl
    p_cu1 = abs(ip) ** 2 * r1
    p_cu2 = abs(i2) ** 2 * r2
    p_core = abs(e1) ** 2 / rc if rc else 0.0
    p_in = (vp * ip.conjugate()).real
    eta = p_load / p_in if p_in > 0 else math.nan
    # no-load secondary voltage (open circuit) for regulation
    if zm is None:
        v2_nl = vp / a
    else:
        v2_nl = abs(vp * zm / (zp + zm)) / a
    reg = (v2_nl - abs(v2)) / abs(v2) * 100 if abs(v2) > 0 else math.inf
    # Thevenin at the secondary terminals -> maximum available power
    vth = (vp if zm is None else vp * zm / (zp + zm)) / a
    zth = ((zp if zm is None else zp * zm / (zp + zm)) / (a * a)) + zs
    pmax = abs(vth) ** 2 / (4 * zth.real) if zth.real > 0 else math.inf
    phi = abs(e1) / (4.44 * f * np_)

    r.add("V2", "secondary terminal voltage", abs(v2), "voltage", primary=True, note="RMS")
    r.add("I2", "secondary (load) current", abs(i2), "current", primary=True, note="RMS")
    r.add("P2", "real power delivered to the load", p_load, "power", primary=True)
    r.add("Q2", "reactive power in the load", q_load, "reactive_power")
    r.add("S2", "apparent power at the secondary", abs(v2) * abs(i2), "apparent_power")
    r.add("E2", "EMF induced in the secondary winding", abs(e2), "voltage", note="RMS")
    r.add("P_cu2", "copper loss in the secondary winding I2²R2", p_cu2, "power")
    r.add("V_drop2", "voltage lost inside the secondary winding |I2·(R2 + jX2)|", abs(i2 * zs), "voltage")
    r.add("Ip", "primary current", abs(ip), "current", note="RMS")
    r.add("Im", "magnetising + core-loss current", abs(im), "current", note="RMS")
    r.add("P_cu1", "copper loss in the primary winding I1²R1", p_cu1, "power")
    r.add("P_core", "core (iron) loss E1²/Rc", p_core, "power")
    r.add("P_in", "real power drawn from the supply", p_in, "power")
    r.add("η", "efficiency P2 / P_in", eta * 100 if math.isfinite(eta) else None, "percent", primary=True)
    r.add("PF_in", "input power factor", p_in / (vp * abs(ip)) if abs(ip) else None, "ratio")
    r.add("V2 no-load", "open-circuit secondary voltage", v2_nl, "voltage")
    r.add("VR", "voltage regulation (V_no-load − V_load)/V_load", reg, "percent")
    r.add("P2 max", "maximum power available at the secondary (conjugate-matched load)", pmax, "power")
    r.add("Zth", "secondary Thevenin impedance (looking back into the transformer)", zth, "impedance")
    r.add("Φmax", "peak core flux from E = 4.44·f·N·Φmax", phi, "number", note="Wb")
    r.add("a = Np:Ns", "turns ratio, primary ÷ secondary", a, "ratio")
    if t_run:
        r.add("W2", f"energy delivered to the load in {fmt(t_run, 'time')}", p_load * t_run, "energy")
        r.add("W_cu2", "heat produced in the secondary winding in that time", p_cu2 * t_run, "energy")
    if va_rating:
        load_pct = abs(v2) * abs(i2) / va_rating * 100
        r.add("Loading", "secondary apparent power as % of rating", load_pct, "percent")
        if load_pct > 100:
            r.warnings.append(f"The transformer is overloaded ({load_pct:.1f} % of its {sig(va_rating)} VA rating).")

    r.formulas += [
        "a = Np / Ns", "Zm = Rc ∥ jXm;  Z2′ = a²(R2 + jX2 + ZL);  Zin = (R1 + jX1) + Zm ∥ Z2′",
        "Ip = Vp / Zin;  E1 = Vp − Ip(R1 + jX1);  I2 = a·E1 / Z2′;  E2 = E1 / a",
        "V2 = I2·ZL;  P2 = |I2|²·RL;  P_cu = I²R;  P_core = E1²/Rc;  η = P2 / P_in",
        "E = 4.44·f·N·Φmax (transformer EMF equation)",
        "P2,max = |Vth|² / (4·Re Zth)  (maximum power transfer)",
    ]
    r.substitutions += [
        f"a = {sig(np_)} / {sig(ns)} = {sig(a)}",
        f"Zin = {_c(zin)} Ω  →  Ip = {sig(vp)} / {_c(zin)} = {sig(abs(ip))} A ∠{math.degrees(cmath.phase(ip)):.3f}°",
        f"E1 = {sig(abs(e1))} V  →  E2 = E1 / a = {sig(abs(e2))} V (secondary EMF)",
        f"I2 = {sig(abs(i2))} A;  V2 = I2 × |ZL| = {sig(abs(i2))} × {sig(abs(zl))} = {sig(abs(v2))} V",
        f"P2 = {sig(abs(i2))}² × {sig(rl)} = {sig(p_load)} W;  P_cu2 = {sig(abs(i2))}² × {sig(r2)} = {sig(p_cu2)} W",
    ]
    r.steps += [
        "Secondary winding, step by step: the core flux induces E2 = E1/a in the secondary; part of it is lost across the "
        f"winding's own R2 + jX2 ({sig(abs(i2 * zs))} V), the rest ({sig(abs(v2))} V) appears at the load.",
        f"Work done by the secondary winding on the load each second = P2 = {sig(p_load)} W; the winding itself turns "
        f"{sig(p_cu2)} W into heat.",
        "Regulation compares the open-circuit and loaded secondary voltages.",
    ]
    r.assumptions += ["Linear (non-saturating) core, sinusoidal supply, RMS values; exact T-equivalent circuit with the "
                      "magnetising branch after the primary winding impedance.",
                      "Leave Rc/Xm blank for an ideal (lossless, zero magnetising current) core, and R/X blank for ideal windings."]
    return r


register(Calculator(
    "transformer_sim", CAT, "Transformer winding simulator (losses, regulation, secondary)", _transformer_sim,
    fields=[
        Field("Vp", "Vp", "primary supply voltage (RMS)", "voltage", "V", "230"),
        Field("f", "f", "supply frequency", "frequency", "Hz", "60"),
        Field("Np", "Np", "primary turns", "turns", "turns", "1000"),
        Field("Ns", "Ns", "secondary turns", "turns", "turns", "100"),
        Field("R1", "R1", "primary winding resistance", "resistance", "Ω", "2.5", optional=True),
        Field("X1", "X1", "primary leakage reactance", "reactance", "Ω", "4", optional=True),
        Field("R2", "R2", "secondary winding resistance", "resistance", "Ω", "0.025", optional=True),
        Field("X2", "X2", "secondary leakage reactance", "reactance", "Ω", "0.04", optional=True),
        Field("Rc", "Rc", "core-loss resistance (primary side; blank = no core loss)", "resistance", "kΩ", "20", optional=True),
        Field("Xm", "Xm", "magnetising reactance (primary side; blank = ideal core)", "reactance", "kΩ", "5", optional=True),
        Field("RL", "RL", "load resistance on the secondary", "resistance", "Ω", "4"),
        Field("XL", "XL", "load reactance (+ inductive, − capacitive)", "reactance", "Ω", "0", optional=True),
        Field("S_rated", "S rated", "transformer VA rating (optional)", "apparent_power", "VA", "500", optional=True),
        Field("t", "t", "operating time for energy totals (optional)", "time", "s", "3600", optional=True),
    ],
    description="Non-ideal transformer with winding resistances, leakage, core loss and magnetising current: "
                "secondary EMF, voltage, current, power, losses, efficiency, regulation and maximum available power.",
    keywords="transformer winding secondary primary losses copper core efficiency regulation turns emf flux available power",
    diagram=lambda o, u: {"kind": "transformer"},
))


# =========================================================================== relay
def _relay(inp):
    vs = inp.need("Vs", positive=True)
    rcoil = inp.need("Rcoil", positive=True)
    lcoil = inp.need("Lcoil", positive=True)
    rser = inp.opt("Rser", 0.0, nonneg=True)
    v_pull = inp.need("Vpull", positive=True)
    v_drop = inp.need("Vdrop", nonneg=True)
    t_on = inp.need("t_on", positive=True)
    t_mech = inp.opt("t_mech", 0.0, nonneg=True)
    i_load, i_rate = inp.opt("Iload", nonneg=True), inp.opt("Irate", positive=True)
    flyback = inp.options["flyback"]
    if v_drop >= v_pull:
        raise CalcError("The drop-out voltage must be lower than the pull-in voltage.")
    rt = rcoil + rser
    i_ss = vs / rt
    i_pull, i_drop = v_pull / rcoil, v_drop / rcoil  # thresholds expressed as coil current
    tau_on = lcoil / rt
    r = Result("Relay simulation")
    r.symbols = {"Vs": "drive voltage", "Rcoil": "coil resistance", "Lcoil": "coil inductance", "τ": "coil time constant",
                 "I_pull": "coil current at which the armature pulls in (V_pull / Rcoil)",
                 "I_drop": "coil current below which it drops out (V_drop / Rcoil)", "t_pick": "pick-up (operate) time",
                 "t_rel": "release time"}
    r.add("I_coil", "steady coil current", i_ss, "current", primary=True)
    r.add("P_coil", "coil power dissipation", i_ss ** 2 * rcoil, "power")
    r.add("V_coil", "steady coil voltage", i_ss * rcoil, "voltage")
    r.add("τ_on", "energising time constant L/(Rcoil + Rser)", tau_on, "time")
    r.formulas += ["i(t) = I_ss (1 − e^(−t/τ)),  τ = L / (Rcoil + Rser)",
                   "t_pick = −τ ln(1 − I_pull / I_ss) + t_mech",
                   "Release with a flyback diode: i(t) = I_ss e^(−t/τ_off), τ_off = L / Rcoil; "
                   "t_rel = τ_off ln(I_ss / I_drop) + t_mech"]
    t_pick = None
    if i_ss <= i_pull:
        r.add("State", "relay state", None, primary=True, text=f"Does NOT pull in: coil gets {sig(i_ss * rcoil)} V, needs {sig(v_pull)} V")
        r.warnings.append("The coil voltage never reaches the pull-in voltage; reduce the series resistance or raise the drive.")
    else:
        t_e = -tau_on * math.log(1 - i_pull / i_ss)
        t_pick = t_e + t_mech
        r.add("t_pick", "pick-up time (electrical + mechanical)", t_pick, "time", primary=True)
        r.substitutions.append(f"t_pick = −{sig(tau_on)} × ln(1 − {sig(i_pull)}/{sig(i_ss)}) + {sig(t_mech)} = {sig(t_pick)} s")
        if t_on <= t_pick:
            r.warnings.append("The drive pulse ends before the relay has picked up; the contacts never close.")
    i_at_off = i_ss * (1 - math.exp(-t_on / tau_on))
    energy = 0.5 * lcoil * i_at_off ** 2
    r.add("E_coil", "magnetic energy stored in the coil when switched off ½LI²", energy, "energy")
    t_rel = None
    if flyback == "diode":
        tau_off = lcoil / rcoil
        r.add("τ_off", "de-energising time constant with flyback diode L/Rcoil", tau_off, "time")
        if i_at_off > i_drop and i_drop > 0:
            t_rel = tau_off * math.log(i_at_off / i_drop) + t_mech
            r.add("t_rel", "release time after switch-off", t_rel, "time", primary=True)
        r.add("V_spike", "voltage at the driver on switch-off", vs + 0.7, "voltage", note="clamped by the diode (≈ Vs + 0.7 V)")
        r.steps.append("The flyback diode clamps the switch-off spike but slows the release, because the coil current decays slowly.")

        def i_t(t):
            t = np.asarray(t, dtype=float)
            on = i_ss * (1 - np.exp(-np.minimum(t, t_on) / tau_on))
            off = i_at_off * np.exp(-(t - t_on) / tau_off)
            return np.where(t <= t_on, on, off)
    else:
        r.add("t_rel", "release time after switch-off", t_mech, "time", primary=True,
              note="current is interrupted almost instantly (mechanical time only)")
        t_rel = t_mech
        r.add("V_spike", "switch-off voltage spike", None, text="very large (L·di/dt with di/dt → ∞) — can destroy the driver",
              primary=False)
        r.warnings.append(f"No flyback protection: the coil's stored {sig(energy)} J is dumped as a high-voltage spike. "
                          "Add a diode, RC snubber or TVS across the coil.")

        def i_t(t):
            t = np.asarray(t, dtype=float)
            return np.where(t <= t_on, i_ss * (1 - np.exp(-t / tau_on)), 0.0)
    if i_load is not None and i_rate is not None:
        if i_load > i_rate:
            r.warnings.append(f"Contact overload: the load draws {sig(i_load)} A but the contacts are rated {sig(i_rate)} A.")
            r.add("Contacts", "contact loading", i_load / i_rate * 100, "percent", note="OVER RATING")
        else:
            r.add("Contacts", "contact loading", i_load / i_rate * 100, "percent")
    r.assumptions.append("Linear coil (constant L), thresholds expressed through the coil resistance; mechanical times "
                         "are added as entered. Contact bounce is not modelled.")
    tmax = t_on + (5 * (lcoil / rcoil) if flyback == "diode" else 0.3 * t_on) + t_mech
    markers = [("switch off", t_on)]
    if t_pick:
        markers.append(("pick-up", t_pick))
    if t_rel is not None and math.isfinite(t_rel):
        markers.append(("release", t_on + t_rel))
    r.plot = PlotSpec("time", "Relay coil current (contacts close above I_pull, open below I_drop)", i_t, t_max=tmax,
                      markers=markers, hlines=[("I_pull", i_pull), ("I_drop", i_drop)], y_label="i_coil (A)",
                      y_quantity="current")
    return r


register(Calculator(
    "relay", CAT, "Relay simulator (coil, pick-up, release, contacts)", _relay,
    fields=[
        Field("Vs", "Vs", "coil drive voltage", "voltage", "V", "12"),
        Field("Rcoil", "Rcoil", "coil resistance", "resistance", "Ω", "400"),
        Field("Lcoil", "Lcoil", "coil inductance", "inductance", "H", "0.5"),
        Field("Rser", "Rser", "series resistance / driver on-resistance", "resistance", "Ω", "0", optional=True),
        Field("Vpull", "V_pull", "must-operate (pull-in) voltage", "voltage", "V", "9"),
        Field("Vdrop", "V_drop", "must-release (drop-out) voltage", "voltage", "V", "1.2"),
        Field("t_on", "t_on", "how long the coil is driven", "time", "ms", "50"),
        Field("t_mech", "t_mech", "mechanical armature travel time", "time", "ms", "3", optional=True),
        Field("Iload", "I_load", "current the contacts switch", "current", "A", "5", optional=True),
        Field("Irate", "I_rated", "contact current rating", "current", "A", "10", optional=True),
    ],
    options=[Option("flyback", "Coil protection", [("diode", "Flyback diode across coil"), ("none", "None")])],
    description="Coil current vs time, pick-up and release times, coil power and stored energy, switch-off spike and "
                "contact loading.",
    keywords="relay coil pick-up pull-in drop-out release contact flyback diode solenoid",
    diagram=lambda o, u: ladder("dc", series=[("SW", "drive"), ("R", "Rser")], shunts=[[("R", "Rcoil"), ("L", "Lcoil")]],
                                out={"across": "shunt", "index": 0}, out_label="coil", source_label="Vs"),
))


# =========================================================================== Δ–Y
def _delta_wye(inp):
    r = Result("Δ–Y (delta–wye) transformation")
    r.symbols = {"Ra, Rb, Rc": "wye (star) resistors to the centre", "Rab, Rbc, Rca": "delta resistors between terminals"}
    if inp.unknown == "to_y":
        rab, rbc, rca = (inp.need(k, positive=True) for k in ("Rab", "Rbc", "Rca"))
        s = rab + rbc + rca
        ra, rb, rc = rab * rca / s, rab * rbc / s, rbc * rca / s
        r.formulas += ["Ra = Rab·Rca / (Rab + Rbc + Rca)", "Rb = Rab·Rbc / Σ", "Rc = Rbc·Rca / Σ"]
        r.substitutions.append(f"Σ = {sig(s)} Ω")
        for n, v in (("Ra", ra), ("Rb", rb), ("Rc", rc)):
            r.add(n, f"wye resistor at terminal {n[-1]}", v, "resistance", primary=True)
    else:
        ra, rb, rc = (inp.need(k, positive=True) for k in ("Ra", "Rb", "Rc"))
        p = ra * rb + rb * rc + rc * ra
        r.formulas += ["Rab = (RaRb + RbRc + RcRa) / Rc", "Rbc = Σ / Ra", "Rca = Σ / Rb"]
        r.substitutions.append(f"Σ = RaRb + RbRc + RcRa = {sig(p)} Ω²")
        for n, v in (("Rab", p / rc), ("Rbc", p / ra), ("Rca", p / rb)):
            r.add(n, f"delta resistor between {n[1]} and {n[2]}", v, "resistance", primary=True)
    r.assumptions.append("Works for resistances or, with complex values, impedances at one frequency.")
    return r


register(Calculator(
    "delta_wye", CAT, "Δ–Y / Y–Δ transformation", _delta_wye,
    fields=[Field(k, k, f"delta resistor {k[1:]}", "resistance", "Ω", d, show_if=lambda o, u: u == "to_y")
            for k, d in (("Rab", "30"), ("Rbc", "60"), ("Rca", "90"))]
    + [Field(k, k, f"wye resistor at terminal {k[1]}", "resistance", "Ω", d, show_if=lambda o, u: u == "to_delta")
       for k, d in (("Ra", "15"), ("Rb", "10"), ("Rc", "30"))],
    unknowns=[("to_y", "Delta → Wye"), ("to_delta", "Wye → Delta")],
    description="Convert a delta (Π) network to a wye (T) network and back.",
    keywords="delta wye star pi tee transformation network",
))


# =========================================================================== Thevenin / Norton
def _thev(inp):
    r = Result("Thevenin ↔ Norton")
    r.symbols = {"Vth": "Thevenin voltage (open-circuit)", "Rth": "Thevenin/Norton resistance", "In": "Norton current (short-circuit)"}
    if inp.unknown == "to_norton":
        vth, rth = inp.need("Vth"), inp.need("Rth", positive=True)
        r.add("In", "Norton current Vth/Rth", vth / rth, "current", primary=True)
        r.add("Rn", "Norton resistance (= Rth)", rth, "resistance", primary=True)
        r.formulas.append("In = Vth / Rth,  Rn = Rth")
    else:
        i_n, rn = inp.need("In"), inp.need("Rn", positive=True)
        r.add("Vth", "Thevenin voltage In·Rn", i_n * rn, "voltage", primary=True)
        r.add("Rth", "Thevenin resistance (= Rn)", rn, "resistance", primary=True)
        r.formulas.append("Vth = In × Rn,  Rth = Rn")
        vth, rth = i_n * rn, rn
    rl = inp.opt("RL", nonneg=True)
    if rl is not None:
        il = vth / (rth + rl)
        r.add("IL", "load current", il, "current")
        r.add("VL", "load voltage", il * rl, "voltage")
        r.add("PL", "load power", il * il * rl, "power")
        r.add("PL max", "maximum power (RL = Rth)", vth * vth / (4 * rth), "power")
    return r


register(Calculator(
    "thevenin", CAT, "Thevenin ↔ Norton (with load)", _thev,
    fields=[Field("Vth", "Vth", "Thevenin voltage", "voltage", "V", "12", show_if=lambda o, u: u == "to_norton"),
            Field("Rth", "Rth", "Thevenin resistance", "resistance", "Ω", "100", show_if=lambda o, u: u == "to_norton"),
            Field("In", "In", "Norton current", "current", "mA", "120", show_if=lambda o, u: u == "to_thev"),
            Field("Rn", "Rn", "Norton resistance", "resistance", "Ω", "100", show_if=lambda o, u: u == "to_thev"),
            Field("RL", "RL", "load resistance (optional)", "resistance", "Ω", "100", optional=True)],
    unknowns=[("to_norton", "Thevenin → Norton"), ("to_thev", "Norton → Thevenin")],
    description="Source transformation, load voltage/current/power and maximum power transfer.",
    keywords="thevenin norton source transformation maximum power transfer",
))


# =========================================================================== stored energy & charge
def _energy(inp):
    r = Result("Stored energy and charge")
    if inp.options["elem"] == "C":
        C, Vv = inp.need("C", positive=True), inp.need("V")
        r.add("E", "energy ½CV²", 0.5 * C * Vv * Vv, "energy", primary=True)
        r.add("Q", "charge Q = C·V", C * Vv, "charge", primary=True)
        r.formulas += ["E = ½ C V²", "Q = C V"]
        r.substitutions.append(f"E = ½ × {q(C, 'capacitance')} × ({q(Vv, 'voltage')})² = {sig(0.5 * C * Vv * Vv)} J")
    else:
        L, I = inp.need("L", positive=True), inp.need("I")
        r.add("E", "energy ½LI²", 0.5 * L * I * I, "energy", primary=True)
        r.add("Λ", "flux linkage L·I", L * I, "number", note="Wb-turns")
        r.formulas += ["E = ½ L I²", "Λ = L I"]
        r.substitutions.append(f"E = ½ × {q(L, 'inductance')} × ({q(I, 'current')})² = {sig(0.5 * L * I * I)} J")
    return r


register(Calculator(
    "stored_energy", CAT, "Energy stored in C or L, charge", _energy,
    fields=[Field("C", "C", "capacitance", "capacitance", "µF", "100", show_if=lambda o, u: o["elem"] == "C"),
            Field("V", "V", "capacitor voltage", "voltage", "V", "50", show_if=lambda o, u: o["elem"] == "C"),
            Field("L", "L", "inductance", "inductance", "mH", "10", show_if=lambda o, u: o["elem"] == "L"),
            Field("I", "I", "inductor current", "current", "A", "2", show_if=lambda o, u: o["elem"] == "L")],
    options=[Option("elem", "Element", [("C", "Capacitor"), ("L", "Inductor")])],
    description="E = ½CV² / ½LI², charge and flux linkage.",
    keywords="energy stored capacitor inductor joule charge coulomb",
))


# =========================================================================== wire resistance
MATERIALS = {"copper": (1.724e-8, 0.00393), "aluminium": (2.826e-8, 0.00403), "silver": (1.59e-8, 0.0038),
             "gold": (2.44e-8, 0.0034), "nichrome": (1.10e-6, 0.0004), "tungsten": (5.60e-8, 0.0045)}


def awg_diameter_m(awg: int) -> float:
    return 0.127e-3 * 92 ** ((36 - awg) / 39)


def _wire(inp):
    mat = inp.options["material"]
    rho20, alpha = MATERIALS[mat]
    length = inp.need("length", positive=True)
    temp = inp.opt("T", 20.0)
    if inp.options["size"] == "awg":
        awg = inp.need("awg")
        if awg != int(awg) or not -3 <= awg <= 40:
            raise CalcError("Enter a whole AWG gauge from -3 (0000) to 40.")
        d = awg_diameter_m(int(awg))
        area = math.pi * d * d / 4
    else:
        area = inp.need("area", positive=True)
        d = math.sqrt(4 * area / math.pi)
    rho = rho20 * (1 + alpha * (temp - 20))
    n = 2 if inp.options["path"] == "round" else 1
    R = rho * length * n / area
    r = Result("Wire resistance")
    r.add("R", "conductor resistance" + (" (out and back)" if n == 2 else ""), R, "resistance", primary=True)
    r.add("d", "conductor diameter", d, "length")
    r.add("A", "cross-section area", area * 1e6, "number", note="mm²")
    r.add("ρ", "resistivity at temperature", rho, "number", note="Ω·m")
    r.formulas += ["R = ρ L / A", "ρ(T) = ρ20 [1 + α (T − 20 °C)]", "AWG diameter d = 0.127 mm × 92^((36 − AWG)/39)"]
    r.substitutions.append(f"R = {sig(rho)} × {sig(length * n)} / {sig(area)} = {sig(R)} Ω")
    I = inp.opt("I", nonneg=True)
    if I is not None:
        r.add("V_drop", "voltage drop I·R", I * R, "voltage", primary=True)
        r.add("P_loss", "power lost in the wire I²R", I * I * R, "power")
    r.assumptions.append(f"{mat.title()}: ρ20 = {rho20:g} Ω·m, α = {alpha:g} /°C (copper: IACS annealed standard, as used "
                         "in AWG tables); DC resistance (no skin effect).")
    return r


register(Calculator(
    "wire", CAT, "Wire resistance & voltage drop (AWG / mm²)", _wire,
    fields=[Field("length", "L", "cable length (one way)", "length", "m", "10"),
            Field("awg", "AWG", "wire gauge", "number", "", "14", show_if=lambda o, u: o["size"] == "awg"),
            Field("area", "A", "cross-section", "area", "mm²", "2.5", show_if=lambda o, u: o["size"] == "area"),
            Field("T", "T", "conductor temperature (°C)", "temperature", "°C", "20", optional=True),
            Field("I", "I", "current (for voltage drop)", "current", "A", "10", optional=True)],
    options=[Option("material", "Material", [(k, k.title()) for k in MATERIALS]),
             Option("size", "Size given as", [("awg", "AWG gauge"), ("area", "Area (mm²)")]),
             Option("path", "Path", [("round", "Round trip (out and back)"), ("one", "One conductor")])],
    description="Resistance, voltage drop and loss of copper/aluminium wire with temperature correction.",
    keywords="wire cable awg gauge resistance voltage drop copper aluminium",
))


# =========================================================================== resistor colour code
COLOURS = ["black", "brown", "red", "orange", "yellow", "green", "blue", "violet", "grey", "white"]
MULT = {**{c: 10 ** i for i, c in enumerate(COLOURS)}, "gold": 0.1, "silver": 0.01}
TOL = {"brown": 1, "red": 2, "green": 0.5, "blue": 0.25, "violet": 0.1, "grey": 0.05, "gold": 5, "silver": 10, "none": 20}
TEMPCO = {"black": 250, "brown": 100, "red": 50, "orange": 15, "yellow": 25, "green": 20, "blue": 10, "violet": 5, "grey": 1}


def _colour(inp):
    r = Result("Resistor colour code")
    if inp.unknown == "decode":
        bands = [b.strip().lower().replace("gray", "grey") for b in inp.text("bands").replace(",", " ").split()]
        if len(bands) not in (3, 4, 5, 6):
            raise CalcError("Enter 3, 4, 5 or 6 band colours separated by spaces (e.g. 'yellow violet red gold').")
        nd = 2 if len(bands) <= 4 else 3
        try:
            digits = [COLOURS.index(b) for b in bands[:nd]]
            mult = MULT[bands[nd]]
        except (ValueError, KeyError) as e:
            raise CalcError(f"Unknown or misplaced colour in '{inp.text('bands')}'.") from e
        tol = TOL.get(bands[nd + 1], None) if len(bands) > nd + 1 else 20
        if tol is None:
            raise CalcError(f"'{bands[nd + 1]}' is not a tolerance colour.")
        val = int("".join(map(str, digits))) * mult
        r.add("R", "resistance", val, "resistance", primary=True)
        r.add("Tolerance", "± tolerance", tol, "percent", primary=True)
        r.add("Range", "minimum … maximum", None, text=f"{fmt(val * (1 - tol / 100), 'resistance')} … {fmt(val * (1 + tol / 100), 'resistance')}")
        if len(bands) == 6:
            r.add("Tempco", "temperature coefficient", None, text=f"{TEMPCO.get(bands[5], '?')} ppm/K")
        r.steps.append("Digits: " + " ".join(f"{b}={d}" for b, d in zip(bands, digits)) + f"; multiplier {bands[nd]} = ×{MULT[bands[nd]]:g}")
    else:
        val = inp.need("R", positive=True)
        nd = 2 if inp.options["nb"] == "4" else 3
        e = math.floor(math.log10(val)) - (nd - 1)
        digits = round(val / 10 ** e)
        if digits >= 10 ** nd:
            digits //= 10
            e += 1
        if e < -2 or e > 9:
            raise CalcError("That value cannot be represented with colour bands (multiplier out of range).")
        ds = str(digits).zfill(nd)
        mcol = {v: k for k, v in MULT.items()}.get(10 ** e if e >= 0 else round(10 ** e, 2))
        bands = [COLOURS[int(c)] for c in ds] + [mcol, "gold" if nd == 2 else "brown"]
        exact = int(ds) * 10 ** e
        r.add("Bands", "colour bands", None, primary=True, text=" – ".join(bands))
        r.add("Coded value", "value the bands represent", exact, "resistance", primary=True)
        if not math.isclose(exact, val, rel_tol=1e-9):
            r.warnings.append(f"{fmt(val, 'resistance')} needs more digits than {nd} bands provide; nearest coded value shown.")
    return r


register(Calculator(
    "colour_code", CAT, "Resistor colour code (decode / encode)", _colour,
    fields=[Field("bands", "Bands", "colours, e.g. yellow violet red gold", "text", "", "yellow violet red gold",
                  show_if=lambda o, u: u == "decode"),
            Field("R", "R", "resistance to encode", "resistance", "kΩ", "4.7", show_if=lambda o, u: u == "encode")],
    options=[Option("nb", "Bands (encode)", [("4", "4-band (2 digits, 5 %)"), ("5", "5-band (3 digits, 1 %)")])],
    unknowns=[("decode", "Colours → value"), ("encode", "Value → colours")],
    description="Decode 3–6 band resistor colour codes or find the bands for a value.",
    keywords="resistor colour color code bands tolerance",
))


# =========================================================================== E-series
E_SERIES = {
    "E6": [1.0, 1.5, 2.2, 3.3, 4.7, 6.8],
    "E12": [1.0, 1.2, 1.5, 1.8, 2.2, 2.7, 3.3, 3.9, 4.7, 5.6, 6.8, 8.2],
    "E24": [1.0, 1.1, 1.2, 1.3, 1.5, 1.6, 1.8, 2.0, 2.2, 2.4, 2.7, 3.0, 3.3, 3.6, 3.9, 4.3, 4.7, 5.1, 5.6, 6.2, 6.8, 7.5, 8.2, 9.1],
    "E48": [1.00, 1.05, 1.10, 1.15, 1.21, 1.27, 1.33, 1.40, 1.47, 1.54, 1.62, 1.69, 1.78, 1.87, 1.96, 2.05, 2.15, 2.26, 2.37, 2.49,
            2.61, 2.74, 2.87, 3.01, 3.16, 3.32, 3.48, 3.65, 3.83, 4.02, 4.22, 4.42, 4.64, 4.87, 5.11, 5.36, 5.62, 5.90, 6.19, 6.49,
            6.81, 7.15, 7.50, 7.87, 8.25, 8.66, 9.09, 9.53],
    "E96": [1.00, 1.02, 1.05, 1.07, 1.10, 1.13, 1.15, 1.18, 1.21, 1.24, 1.27, 1.30, 1.33, 1.37, 1.40, 1.43, 1.47, 1.50, 1.54, 1.58,
            1.62, 1.65, 1.69, 1.74, 1.78, 1.82, 1.87, 1.91, 1.96, 2.00, 2.05, 2.10, 2.15, 2.21, 2.26, 2.32, 2.37, 2.43, 2.49, 2.55,
            2.61, 2.67, 2.74, 2.80, 2.87, 2.94, 3.01, 3.09, 3.16, 3.24, 3.32, 3.40, 3.48, 3.57, 3.65, 3.74, 3.83, 3.92, 4.02, 4.12,
            4.22, 4.32, 4.42, 4.53, 4.64, 4.75, 4.87, 4.99, 5.11, 5.23, 5.36, 5.49, 5.62, 5.76, 5.90, 6.04, 6.19, 6.34, 6.49, 6.65,
            6.81, 6.98, 7.15, 7.32, 7.50, 7.68, 7.87, 8.06, 8.25, 8.45, 8.66, 8.87, 9.09, 9.31, 9.53, 9.76],
}


def nearest_standard(value: float, series: str) -> tuple[float, float, float]:
    """(nearest, next lower-or-equal, next higher-or-equal) standard values."""
    exp = math.floor(math.log10(value))
    cands = [m * 10 ** e for e in (exp - 1, exp, exp + 1) for m in E_SERIES[series]]
    cands = sorted(set(round(c, 12) for c in cands))
    nearest = min(cands, key=lambda c: abs(math.log(c / value)))
    lower = max(c for c in cands if c <= value * (1 + 1e-12))
    upper = min(c for c in cands if c >= value * (1 - 1e-12))
    return nearest, lower, upper


def _eseries(inp):
    v = inp.need("x", positive=True)
    ser = inp.options["series"]
    qty = inp.options["qty"]
    n, lo, hi = nearest_standard(v, ser)
    r = Result(f"Nearest {ser} standard value")
    r.add("Nearest", "closest standard value", n, qty, primary=True)
    r.add("Error", "nearest vs requested", (n - v) / v * 100, "percent")
    r.add("Next lower", "standard value at or below", lo, qty)
    r.add("Next higher", "standard value at or above", hi, qty)
    r.assumptions.append("IEC 60063 preferred-number series; closeness measured as a ratio (log scale).")
    return r


register(Calculator(
    "eseries", CAT, "Nearest standard value (E6 … E96)", _eseries,
    fields=[Field("x", "x", "required value", lambda o: o["qty"], lambda o: {"resistance": "kΩ", "capacitance": "nF",
                                                                                "inductance": "µH"}[o["qty"]], "5")],
    options=[Option("series", "Series", [(k, k) for k in E_SERIES], "E24"),
             Option("qty", "Component", [("resistance", "Resistor"), ("capacitance", "Capacitor"), ("inductance", "Inductor")])],
    description="Closest preferred value and the neighbours either side.",
    keywords="standard value e12 e24 e96 preferred nearest resistor capacitor",
))


# =========================================================================== current divider
def _idiv(inp):
    it = inp.need("It")
    rs = [inp.values.get(f"r{i}") for i in range(1, 5)]
    rs = [x for x in rs if x is not None]
    if len(rs) < 2:
        raise CalcError("Enter at least two branch resistances.")
    if any(x <= 0 for x in rs):
        raise CalcError("Branch resistances must be greater than zero.")
    g = sum(1 / x for x in rs)
    r = Result("Current divider")
    r.formulas.append("In = It × (1/Rn) / Σ(1/Rk)")
    for i, x in enumerate(rs, 1):
        r.add(f"I{i}", f"current in branch {i}", it * (1 / x) / g, "current", primary=True)
    r.add("V", "voltage across the branches", it / g, "voltage")
    r.add("Req", "parallel equivalent", 1 / g, "resistance")
    return r


register(Calculator(
    "current_divider", CAT, "Current divider (parallel branches)", _idiv,
    fields=[Field("It", "It", "total current entering the parallel group", "current", "mA", "100")]
    + [Field(f"r{i}", f"R{i}", f"branch {i} resistance", "resistance", "Ω", d, optional=i > 2)
       for i, d in ((1, "100"), (2, "220"), (3, ""), (4, ""))],
    description="Share of a total current in up to four parallel resistors.",
    keywords="current divider parallel branch",
))


# =========================================================================== LED resistor
def _led(inp):
    vs, vf, i_f = inp.need("Vs", positive=True), inp.need("Vf", nonneg=True), inp.need("If", positive=True)
    n = int(inp.opt("n", 1) or 1)
    vdrop = vs - n * vf
    if vdrop <= 0:
        raise CalcError(f"The supply ({sig(vs)} V) is not above the total LED forward voltage ({sig(n * vf)} V).")
    R = vdrop / i_f
    from eesimlab.eecalc.practical import nearest_standard as ns_  # noqa

    _, lo, hi = ns_(R, "E24")
    r = Result("LED series resistor")
    r.add("R", "exact series resistance", R, "resistance", primary=True)
    r.add("R (E24, ≥)", "next standard value up (current slightly lower)", hi, "resistance", primary=True)
    r.add("I with E24", "LED current with that resistor", vdrop / hi, "current")
    r.add("P_R", "resistor dissipation", vdrop * vdrop / hi, "power")
    r.add("P_LED", "power in the LED(s)", n * vf * vdrop / hi, "power")
    r.formulas.append("R = (Vs − n·Vf) / If")
    r.substitutions.append(f"R = ({sig(vs)} − {n}×{sig(vf)}) / {sig(i_f)} = {sig(R)} Ω")
    return r


register(Calculator(
    "led", CAT, "LED series resistor", _led,
    fields=[Field("Vs", "Vs", "supply voltage", "voltage", "V", "5"),
            Field("Vf", "Vf", "LED forward voltage", "voltage", "V", "2"),
            Field("If", "If", "LED current", "current", "mA", "20"),
            Field("n", "n", "LEDs in series", "number", "", "1", optional=True)],
    description="Current-limiting resistor, nearest standard value and dissipation.",
    keywords="led resistor current limiting forward voltage",
))


# =========================================================================== battery life
def _battery(inp):
    cap = inp.need("cap", positive=True)
    i = inp.need("I", positive=True)
    derate = inp.opt("eff", 100.0, positive=True)
    t = cap / i * derate / 100
    r = Result("Battery runtime")
    r.add("t", "estimated runtime", t, "time", primary=True)
    r.add("hours", "runtime in hours", t / 3600, "number", note="h")
    r.formulas.append("t = capacity / current × usable fraction")
    r.assumptions.append("Constant current; real capacity falls at high discharge rates (Peukert effect) and low temperature.")
    return r


register(Calculator(
    "battery", CAT, "Battery runtime", _battery,
    fields=[Field("cap", "C", "battery capacity", "charge", "mAh", "2500"),
            Field("I", "I", "average load current", "current", "mA", "150"),
            Field("eff", "usable", "usable fraction of capacity (%)", "percent", "%", "80", optional=True)],
    description="Runtime from capacity (mAh/Ah) and load current.",
    keywords="battery runtime capacity mah life",
))
