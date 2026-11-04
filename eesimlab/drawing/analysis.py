"""Calculations chosen from what the confirmed model supports. Every number comes from the
MNA solver or a closed-form formula evaluated on the model's values; nothing is generated
by an AI. Each Calculation lists method, inputs, results, assumptions and working."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from eesimlab.drawing import solver as S
from eesimlab.drawing.model import DrawingModel, SourceParams
from eesimlab.eecalc.units import fmt

TWO_PI = 2 * math.pi


@dataclass
class SimSettings:
    analysis_freq: float | None = None  # AC frequency (default: the sine sources' frequency)
    duration: float | None = None  # transient length, s (default: from time constants / period)
    steps: int = 4000
    initial: str = "dc"  # transient start: 'dc' operating point or 'zero' state
    output_node: str | None = None  # frequency-response output
    f_min: float | None = None
    f_max: float | None = None

    def key(self) -> str:
        return repr((self.analysis_freq, self.duration, self.steps, self.initial, self.output_node, self.f_min, self.f_max))


@dataclass
class Calculation:
    title: str
    method: str
    results: list[str] = field(default_factory=list)
    inputs: list[str] = field(default_factory=list)
    working: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    ok: bool = True
    data: dict = field(default_factory=dict)  # numbers for comparison / tests


@dataclass
class AnalysisResult:
    model_hash: str
    settings_key: str
    calculations: list[Calculation]
    findings: list
    operation: list[str]
    recommendations: list[dict]
    dc: object | None = None
    sweep: dict | None = None
    transient: object | None = None


def V(x):
    return fmt(x, "voltage", 4)


def A(x):
    return fmt(x, "current", 4)


def W(x):
    return fmt(x, "power", 4)


def Om(x):
    return fmt(x, "resistance", 4)


def _label(model, nid):
    return model.node_label(nid)


# ================================================================ helpers
def without(model: DrawingModel, cid: str) -> DrawingModel:
    m = model.clone()
    m.components = [c for c in m.components if c.id != cid]
    return m


def equivalent_groups(model: DrawingModel) -> list[tuple[str, list[str], float, str]]:
    """Simple series/parallel groups of same-type ideal parts:
    parallel = same node pair; series = chain through a node touched by exactly two parts."""
    out = []
    for kind in "RLC":
        parts = [c for c in model.components if c.kind == kind and c.value and None not in c.nodes]
        pairs: dict[frozenset, list] = {}
        for c in parts:
            pairs.setdefault(frozenset(c.nodes), []).append(c)
        for key, cs in pairs.items():
            if len(cs) > 1:
                vals = [c.value for c in cs]
                eq = sum(vals) if kind == "C" else 1 / sum(1 / v for v in vals)
                out.append(("parallel", [c.id for c in cs], eq, kind))
        for n in model.nodes:
            terms = model.terminals_at(n.id)
            if len(terms) == 2 and not n.ground:
                cs = [model.comp(t[0]) for t in terms]
                if all(c.kind == kind and c.value for c in cs) and cs[0].id != cs[1].id:
                    vals = [c.value for c in cs]
                    eq = 1 / sum(1 / v for v in vals) if kind == "C" else sum(vals)
                    out.append(("series", [c.id for c in cs], eq, kind))
    return out


def default_duration(model: DrawingModel) -> float:
    taus = []
    for c in model.components:
        if c.kind in ("L", "C") and c.value and None not in c.nodes:
            try:
                r = S.resistance(without(model, c.id), *c.nodes)
            except S.SolverError:
                continue
            if c.kind == "C" and 0 < r < math.inf:
                taus.append(r * c.value)
            if c.kind == "L" and 0 < r < math.inf:
                taus.append(c.value / r)
    per = [1 / c.src.freq for c in model.components if c.kind in ("V", "I") and c.src and c.src.freq and c.src.wave in ("sine", "square")]
    cands = [5 * max(taus)] if taus else []
    if per:
        cands.append(5 * max(per))
    return max(cands) if cands else 1e-3


# ================================================================ analysis
def analyse(model: DrawingModel, settings: SimSettings, run_transient: bool = True) -> AnalysisResult:
    from eesimlab.drawing import validate as VAL

    calcs: list[Calculation] = []
    findings = VAL.static_checks(model)
    blocking = [f for f in findings if f.severity in ("missing",) or (f.severity == "problem" and f.rule in ("short", "loop", "floating"))]
    operation = describe(model)
    res = AnalysisResult(model.electrical_hash(), settings.key(), calcs, findings, operation, [])
    if model.drawing_type not in ("electrical_schematic", "unknown"):
        res.calculations += mechanical(model)
        res.recommendations = recommend(model, findings, res)
        return res
    if any(f.rule in ("unsupported", "unconnected", "ground", "value", "source", "junction", "empty") and f.severity == "missing"
           for f in findings):
        need = [f.title for f in findings if f.severity == "missing"]
        calcs.append(Calculation("Analysis not run", "—", ok=False,
                                 results=["Supply the missing information first:"] + [f"• {t}" for t in need]))
        res.recommendations = recommend(model, findings, res)
        return res

    gnd = model.ground_nodes()[0]
    # ---------------- DC operating point
    try:
        dc = S.dc_op(model)
        res.dc = dc
        findings += VAL.operating_checks(model, dc)
        c = Calculation("DC operating point", "Modified nodal analysis (MNA): G·x = b, solved with LU decomposition; "
                        "capacitors open, inductors short, sources at their DC values.")
        c.inputs = [f"{x.id}: {x.kind_name()} {_val(x)}" for x in model.components]
        c.results.append(f"Reference node: {_label(model, gnd)} = 0 V")
        for n in model.nodes:
            if n.ground:
                continue
            v = dc.v.get(n.id)
            c.results.append(f"V({_label(model, n.id)}) = " + ("undefined (no DC path to ground)" if v is None or math.isnan(v) else V(v)))
        total_src, total_load = 0.0, 0.0
        for x in model.components:
            a, b = x.nodes
            va, vb = dc.v.get(a, math.nan), dc.v.get(b, math.nan)
            i = dc.i[x.id]
            vd = va - vb
            if math.isnan(vd):
                c.results.append(f"{x.id}: voltage undefined (floating node)")
                continue
            p = vd * i
            if x.kind in ("V", "I"):
                delivered = -p
                total_src += delivered
                c.results.append(f"{x.id}: I = {A(-i if x.kind == 'V' else i)} out of its + terminal, delivers {W(delivered)}")
            else:
                total_load += p
                c.results.append(f"{x.id}: V = {V(vd)}, I = {A(i)}, P = {W(p)}")
        c.working += ["Each resistor stamps 1/R into the conductance matrix; each voltage source and inductor adds one "
                      "branch-current unknown.", f"Power balance: sources deliver {W(total_src)}, components absorb {W(total_load)} "
                      f"(difference {W(total_src - total_load)})."]
        if dc.floating:
            c.assumptions.append("Nodes " + ", ".join(dc.floating) + " have no DC path to ground; their DC voltage is undefined.")
        c.data = {"v": {k: float(v) for k, v in dc.v.items()}, "i": {k: float(v) for k, v in dc.i.items()}}
        calcs.append(c)
    except S.SolverError as e:
        calcs.append(Calculation("DC operating point", "MNA", ok=False, results=[f"Not solvable: {e}"]))
        dc = None

    # ---------------- equivalents
    groups = equivalent_groups(model)
    srcs = [x for x in model.components if x.kind in ("V", "I")]
    c = Calculation("Equivalent values", "Series/parallel reduction and ohmmeter-style network solve")
    for kind, ids, eq, k in groups:
        unit = {"R": "resistance", "L": "inductance", "C": "capacitance"}[k]
        c.results.append(f"{' + '.join(ids) if kind == 'series' else ' ∥ '.join(ids)} ({kind}) = {fmt(eq, unit, 4)}")
    for sx in srcs:
        try:
            r = S.resistance(without(model, sx.id), *sx.nodes)
            c.results.append(f"Resistance seen by {sx.id} (other sources removed, C open, L short) = "
                             + ("∞ (open circuit)" if math.isinf(r) else Om(r)))
            c.data[f"Rin_{sx.id}"] = r
        except S.SolverError as e:
            c.results.append(f"Resistance seen by {sx.id}: {e}")
    if c.results:
        c.assumptions.append("Ideal components; resistance seen by a source is the DC resistance with that source removed.")
        calcs.append(c)

    # ---------------- time constants
    react = [x for x in model.components if x.kind in ("L", "C")]
    if react:
        c = Calculation("Time constants", "Thevenin resistance seen by each energy-storage element (sources de-energised)")
        for x in react:
            try:
                r = S.resistance(without(model, x.id), *x.nodes)
            except S.SolverError as e:
                c.results.append(f"{x.id}: {e}")
                continue
            if math.isinf(r):
                c.results.append(f"{x.id}: no resistive discharge path (τ infinite)")
            elif r == 0:
                c.results.append(f"{x.id}: directly across a source or short (τ = 0)")
            else:
                tau = r * x.value if x.kind == "C" else x.value / r
                c.results.append(f"τ({x.id}) = " + (f"Rth × C = {Om(r)} × {fmt(x.value, 'capacitance', 4)}" if x.kind == "C"
                                                    else f"L / Rth = {fmt(x.value, 'inductance', 4)} / {Om(r)}") + f" = {fmt(tau, 'time', 4)}")
                c.data[f"tau_{x.id}"] = tau
        if len(react) == 1:
            c.assumptions.append("Single energy-storage element: the response is exactly first order with this τ.")
        else:
            c.assumptions.append("Several energy-storage elements: each τ is the element's own time constant with the others "
                                 "in place at DC; the true response is higher order (see the transient simulation).")
        calcs.append(c)

    # ---------------- AC at the operating frequency
    freqs = S.ac_frequencies(model)
    f_ac = settings.analysis_freq or (freqs[0] if len(freqs) == 1 else None)
    if len(freqs) > 1 and not settings.analysis_freq:
        calcs.append(Calculation("AC steady state", "—", ok=False, results=[
            "Sources run at different frequencies (" + ", ".join(fmt(f, "frequency") for f in freqs) + "). "
            "Choose one analysis frequency, or use the transient simulation (superposition of frequencies)."]))
    if f_ac:
        try:
            sol = S.ac(model, f_ac)
            c = Calculation(f"AC steady state at {fmt(f_ac, 'frequency')}",
                            "Complex MNA: R → 1/R, C → jωC, L → jωL; sine sources as phasors (peak amplitude)")
            for x in react:
                xr = TWO_PI * f_ac * x.value if x.kind == "L" else 1 / (TWO_PI * f_ac * x.value)
                c.results.append(f"X({x.id}) = " + ("2πfL" if x.kind == "L" else "1/(2πfC)") + f" = {Om(xr)}")
            for n in model.nodes:
                if not n.ground:
                    z = sol.v[n.id]
                    c.results.append(f"V({_label(model, n.id)}) = {V(abs(z))} peak ({V(abs(z) / math.sqrt(2))} RMS) "
                                     f"∠{math.degrees(math.atan2(z.imag, z.real)):.2f}°")
            for sx in srcs:
                vs = sol.v[sx.nodes[0]] - sol.v[sx.nodes[1]]
                i = sol.i[sx.id] * (-1 if sx.kind == "V" else 1)
                if abs(i) > 0 and abs(vs) > 0:
                    z = vs / i
                    c.results.append(f"Impedance seen by {sx.id}: Z = {z.real:.6g} {'+' if z.imag >= 0 else '−'} j{abs(z.imag):.6g} Ω, "
                                     f"|Z| = {Om(abs(z))}, θ = {math.degrees(math.atan2(z.imag, z.real)):.2f}°")
                    c.data[f"Z_{sx.id}"] = z
            c.assumptions.append("Sinusoidal steady state; amplitudes are peak values unless marked RMS.")
            c.data["v"] = {k: complex(v) for k, v in sol.v.items()}
            calcs.append(c)
        except S.SolverError as e:
            calcs.append(Calculation("AC steady state", "complex MNA", ok=False, results=[str(e)]))

    # ---------------- frequency response
    out = settings.output_node or _auto_output(model)
    if react and srcs and out:
        try:
            fmin, fmax = settings.f_min or 1.0, settings.f_max or 1e7
            fs = np.geomspace(fmin, fmax, 600)
            sw = S.sweep(model, fs)
            h = sw["v"][out] - sw["v"].get(gnd, 0)
            if srcs[0].kind == "V":
                pass
            mag = np.abs(h)
            res.sweep = {"f": fs, "h": h, "node": out, "drive": sw["drive"], "vin": sw["v"]}
            c = Calculation(f"Frequency response at {_label(model, out)} (driven by {sw['drive']} = 1 V/1 A peak)",
                            f"Complex MNA solved at {len(fs)} log-spaced frequencies from {fmt(fmin, 'frequency')} to "
                            f"{fmt(fmax, 'frequency')}")
            k = int(np.argmax(mag))
            c.results.append(f"Maximum gain {20 * math.log10(max(mag[k], 1e-300)):.3f} dB at {fmt(fs[k], 'frequency', 4)}")
            target = mag[k] / math.sqrt(2)
            lo = _cross(fs[: k + 1][::-1], mag[: k + 1][::-1], target)
            hi = _cross(fs[k:], mag[k:], target)
            if lo:
                c.results.append(f"Lower −3 dB frequency (relative to the peak): {fmt(lo, 'frequency', 4)}")
                c.data["f_low"] = lo
            if hi:
                c.results.append(f"Upper −3 dB frequency (relative to the peak): {fmt(hi, 'frequency', 4)}")
                c.data["f_high"] = hi
            if not lo and not hi:
                c.results.append("No −3 dB point inside the sweep range.")
            kinds = {x.kind for x in react}
            if kinds == {"L", "C"}:
                # resonance from the impedance seen by the driving source
                d = model.comp(sw["drive"])
                vs = sw["v"][d.nodes[0]] - sw["v"][d.nodes[1]]
                isrc = -sw["i"][d.id] if d.kind == "V" else sw["i"][d.id]
                with np.errstate(divide="ignore", invalid="ignore"):
                    zin = vs / isrc
                ph = np.angle(zin)
                zc = np.where(np.sign(ph[:-1]) != np.sign(ph[1:]))[0]
                for j in zc[:3]:
                    f0 = fs[j] - ph[j] * (fs[j + 1] - fs[j]) / (ph[j + 1] - ph[j])
                    kind = "series-type (|Z| minimum)" if abs(zin[j]) < np.median(np.abs(zin)) else "parallel-type (|Z| maximum)"
                    c.results.append(f"Resonance (input impedance purely resistive) at {fmt(f0, 'frequency', 4)}: {kind}")
                    c.data.setdefault("f_res", []).append(f0)
            c.working.append("Cut-off points are found by interpolating where |H| crosses peak/√2 between sweep points.")
            c.assumptions.append("Linear, ideal components; the first source drives the circuit and the others are switched off.")
            c.data["peak_gain"] = float(mag[k])
            calcs.append(c)
        except S.SolverError as e:
            calcs.append(Calculation("Frequency response", "complex MNA sweep", ok=False, results=[str(e)]))

    # ---------------- transient
    if run_transient and srcs:
        try:
            dur = settings.duration or default_duration(model)
            tr = S.transient(model, dur, settings.steps, settings.initial)
            res.transient = tr
            c = Calculation(f"Transient response over {fmt(dur, 'time')}",
                            f"Backward-Euler integration with companion models, {settings.steps} steps (Δt = {fmt(tr.dt, 'time')}); "
                            f"initial state: {'DC operating point before t = 0' if settings.initial == 'dc' else 'all capacitors 0 V, inductors 0 A'}")
            for n in model.nodes:
                if n.ground:
                    continue
                y = tr.v[n.id]
                c.results.append(f"V({_label(model, n.id)}): start {V(y[0])}, end {V(y[-1])}, min {V(y.min())}, max {V(y.max())}")
            c.assumptions.append("Fixed time step; accuracy improves with more steps (error ∝ Δt/τ).")
            calcs.append(c)
        except S.SolverError as e:
            calcs.append(Calculation("Transient response", "backward Euler", ok=False, results=[str(e)]))
    res.recommendations = recommend(model, findings, res)
    return res


def _cross(f, mag, target):
    for j in range(1, len(f)):
        if (mag[j - 1] - target) * (mag[j] - target) <= 0 and mag[j - 1] != mag[j]:
            lf = math.log10(f[j - 1]) + (target - mag[j - 1]) * (math.log10(f[j]) - math.log10(f[j - 1])) / (mag[j] - mag[j - 1])
            return 10 ** lf
    return None


def _auto_output(model: DrawingModel) -> str | None:
    for n in model.nodes:
        if n.name and n.name.strip().lower() in ("out", "vout", "output", "vo"):
            return n.id
    return None


def _val(c) -> str:
    if c.kind == "SW":
        return f"switch {c.state or '(state unknown)'}"
    if c.kind in ("R", "L", "C"):
        unit = {"R": "resistance", "L": "inductance", "C": "capacitance"}[c.kind]
        return fmt(c.value, unit, 4) if c.value is not None else "(no value)"
    s = c.src
    if not s:
        return "(no settings)"
    if s.wave in ("dc", "step"):
        return f"{s.wave.upper()} {s.dc}"
    return f"{s.wave} {s.amp} peak, {fmt(s.freq or 0, 'frequency')}, offset {s.offset}"


# ================================================================ explanation & recommendations
def describe(model: DrawingModel) -> list[str]:
    """Deterministic description of how the circuit should operate, from its topology."""
    if model.drawing_type not in ("electrical_schematic", "unknown"):
        return [f"Drawing type: {model.drawing_type}. Only dimensions and scaled measurements are interpreted."]
    lines = []
    srcs = [c for c in model.components if c.kind in ("V", "I")]
    rs = [c for c in model.components if c.kind == "R"]
    ls = [c for c in model.components if c.kind == "L"]
    cs = [c for c in model.components if c.kind == "C"]
    if not srcs:
        lines.append("There is no independent source: the network is passive and only its resistance/impedance can be computed.")
    for s in srcs:
        lines.append(f"{s.id} drives the circuit between {model.node_label(s.nodes[0])} (+) and {model.node_label(s.nodes[1])} (−) "
                     f"with {_val(s)}.")
    order = len(ls) + len(cs)
    if order == 0:
        lines.append(f"It is a purely resistive network of {len(rs)} resistor(s): currents and voltages follow Ohm's and Kirchhoff's laws "
                     "and do not depend on frequency.")
    elif order == 1:
        el = (ls + cs)[0]
        lines.append(f"It is a first-order circuit: {el.id} stores energy, so after any change voltages and currents move "
                     "exponentially with one time constant, and AC signals see a single cut-off frequency.")
    else:
        lines.append(f"It contains {order} energy-storage elements, so it is a higher-order circuit"
                     + (" with possible resonance between L and C" if ls and cs else "") + ".")
    unsupported = [c for c in model.components if not c.supported]
    if unsupported:
        lines.append("Parts not modelled: " + ", ".join(f"{c.id} ({c.kind_name()})" for c in unsupported) + ".")
    return lines


def recommend(model: DrawingModel, findings, res: AnalysisResult) -> list[dict]:
    """Specific improvements tied to findings and calculations, with benefit / trade-off /
    information still needed."""
    out = []
    for f in findings:
        if f.rule == "power" and f.severity == "problem":
            out.append({"title": f"Increase the power rating of {f.where[0][1]}", "detail": f.fix,
                        "benefit": "Keeps the part within its rating and temperature limits.",
                        "tradeoff": "Larger, more expensive part.", "needs": "Ambient temperature and enclosure airflow."})
        elif f.rule == "polarity" and f.severity == "problem":
            out.append({"title": f"Reverse {f.where[0][1]}", "detail": f.fix, "benefit": "Removes reverse bias.",
                        "tradeoff": "None.", "needs": "Confirm the '+' marking on the physical part."})
        elif f.rule == "ground":
            out.append({"title": "Define a ground reference", "detail": f.fix,
                        "benefit": "Makes every node voltage defined and measurable.", "tradeoff": "None.",
                        "needs": "Which node the real circuit uses as 0 V."})
        elif f.rule == "dcpath":
            out.append({"title": "Add a DC bias / bleed resistor", "detail": f.fix,
                        "benefit": "Gives the isolated node a defined DC level and discharges stored charge.",
                        "tradeoff": "Adds loading (choose ≥ 10× the impedance of the node at the signal frequency).",
                        "needs": "Desired DC level and acceptable loading."})
        elif f.rule == "open" and f.severity == "concern":
            out.append({"title": f"Model the load at {model.node_label(f.where[0][1])}", "detail": f.fix,
                        "benefit": "Results then reflect the real operating condition.", "tradeoff": "None.",
                        "needs": "The load's input resistance/capacitance."})
    # divider loading: resistor feeding an output node whose load is comparable
    calc_eq = next((c for c in res.calculations if c.title == "Time constants"), None)
    if calc_eq and any("infinite" in r for r in calc_eq.results):
        out.append({"title": "Provide a discharge path for stored energy",
                    "detail": "An energy-storage element has no resistive path; add a bleeder resistor.",
                    "benefit": "Safe discharge after power-off; defined settling.", "tradeoff": "Small standby power loss.",
                    "needs": "Maximum allowed standby current."})
    return out


def mechanical(model: DrawingModel) -> list[Calculation]:
    c = Calculation("Dimensions read from the drawing", "Text recognition only (values as printed)")
    for d in model.dimensions:
        c.results.append(f"{d.id}: {d.text}" + (f" = {d.value:g} {d.unit}" if d.value is not None else " (value unreadable)")
                         + (f"  [confidence {d.confidence:.0%}]" if d.confidence < 1 else ""))
    if model.scale:
        c.assumptions.append(f"Scale set by the user: {model.scale['px_per_unit']:.6g} px per {model.scale['unit']}. "
                             "Distances measured on the image use this scale.")
    else:
        c.assumptions.append("No scale reference was given, so no physical size is inferred from image pixels.")
    c.assumptions.append("Structural, thermal or mechanical analysis is not supported; only printed dimensions are reported.")
    return [c]
