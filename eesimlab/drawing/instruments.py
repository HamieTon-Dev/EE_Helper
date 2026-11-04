"""Virtual instruments. Every reading comes from the MNA solver run on the confirmed model,
is labelled SIMULATED, names its reference points and assumptions, and records the model
and settings it was computed from so that it can be invalidated when either changes.

  voltmeter  red and black probes on two nodes: V(red) − V(black)
  ammeter    inserted in series with one chosen component (branch current, never a
             voltage between nodes)
  scope      transient waveforms of up to four channels, each node vs a reference node
  ohmmeter   resistance between two nodes with the circuit DE-ENERGISED
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

import numpy as np

from eesimlab.drawing import solver as S
from eesimlab.drawing.analysis import SimSettings, default_duration
from eesimlab.drawing.model import DrawingModel
from eesimlab.eecalc.units import fmt

KINDS = {"voltmeter": "Voltmeter", "ammeter": "Ammeter", "scope": "Oscilloscope", "ohmmeter": "Ohmmeter"}
MODES = {"dc": "DC", "ac": "AC (sinusoidal steady state)", "tran": "Transient"}


@dataclass
class Probe:
    id: str
    kind: str  # voltmeter | ammeter | scope | ohmmeter
    red: str | None = None  # node ids
    black: str | None = None
    component: str | None = None  # ammeter
    channels: list = field(default_factory=list)  # scope: [[node, ref_node], ...]
    mode: str = "dc"  # dc | ac | tran (voltmeter / ammeter)
    label: str = ""

    def to_dict(self):
        return asdict(self)


@dataclass
class Reading:
    probe_id: str
    simulated: bool = True
    ok: bool = True
    lines: list[str] = field(default_factory=list)
    value: float | complex | None = None
    unit: str = ""
    reference: str = ""
    assumptions: list[str] = field(default_factory=list)
    needs: list[str] = field(default_factory=list)  # what the user must supply for a valid result
    waveform: dict | None = None  # {'t': ndarray, 'series': {label: ndarray}, 'unit': 'V'}
    model_hash: str = ""
    settings_key: str = ""

    def stale(self, model: DrawingModel, settings: SimSettings) -> bool:
        return self.model_hash != model.electrical_hash() or self.settings_key != settings.key()


def _need_nodes(model, *nids):
    missing = [n for n in nids if not n]
    if missing:
        return ["Choose both probe points."]
    bad = [n for n in nids if n not in model.node_ids()]
    return [f"Probe point {n} no longer exists in the model." for n in bad]


def measure(model: DrawingModel, probe: Probe, settings: SimSettings, cache: dict | None = None) -> Reading:
    r = Reading(probe.id, model_hash=model.electrical_hash(), settings_key=settings.key())
    r.lines.append("SIMULATED reading (not a real measurement)")
    cache = cache if cache is not None else {}
    try:
        if probe.kind == "voltmeter":
            _voltmeter(model, probe, settings, r, cache)
        elif probe.kind == "ammeter":
            _ammeter(model, probe, settings, r, cache)
        elif probe.kind == "scope":
            _scope(model, probe, settings, r, cache)
        elif probe.kind == "ohmmeter":
            _ohmmeter(model, probe, r)
        else:
            r.ok, r.needs = False, [f"Unknown instrument '{probe.kind}'."]
    except S.SolverError as e:
        r.ok = False
        r.needs.append(str(e))
    if r.needs:
        r.ok = False
    return r


def _solve(model, settings, mode, cache):
    key = (mode, settings.key())
    if key in cache:
        return cache[key]
    if mode == "dc":
        sol = S.dc_op(model)
    elif mode == "ac":
        f = settings.analysis_freq or (S.ac_frequencies(model) or [None])[0]
        if not f:
            raise S.SolverError("AC mode needs a sine source (or an analysis frequency in the simulation settings).")
        sol = S.ac(model, f)
    else:
        sol = S.transient(model, settings.duration or default_duration(model), settings.steps, settings.initial)
    cache[key] = sol
    return sol


def _voltmeter(model, p, settings, r, cache):
    r.needs = _need_nodes(model, p.red, p.black)
    if r.needs:
        return
    red, black = model.node_label(p.red), model.node_label(p.black)
    r.reference = f"red probe on {red}, black probe on {black}: reading = V({red}) − V({black})"
    r.unit = "V"
    sol = _solve(model, settings, p.mode, cache)
    if p.mode == "dc":
        v = sol.v[p.red] - sol.v[p.black]
        if math.isnan(v):
            r.needs.append("One probe point has no DC path to ground, so its DC voltage is undefined.")
            return
        r.value = v
        r.lines.append(f"DC voltage: {fmt(v, 'voltage', 4)}")
        r.assumptions.append("Ideal voltmeter (infinite input resistance); DC operating point (C open, L short).")
    elif p.mode == "ac":
        z = sol.v[p.red] - sol.v[p.black]
        r.value = z
        r.lines += [f"AC voltage at {fmt(sol.freq, 'frequency')}: {fmt(abs(z) / math.sqrt(2), 'voltage', 4)} RMS "
                    f"({fmt(abs(z), 'voltage', 4)} peak), phase {math.degrees(math.atan2(z.imag, z.real)):.2f}°"]
        r.assumptions.append("Ideal true-RMS voltmeter, sinusoidal steady state; phase relative to the source phase.")
    else:
        y = sol.v[p.red] - sol.v[p.black]
        r.value = float(y[-1])
        n = len(y)
        tail = y[n // 2:]
        r.lines += [f"At end of run: {fmt(y[-1], 'voltage', 4)}",
                    f"Second half: mean {fmt(float(tail.mean()), 'voltage', 4)}, RMS {fmt(float(np.sqrt((tail ** 2).mean())), 'voltage', 4)}, "
                    f"peak-to-peak {fmt(float(tail.max() - tail.min()), 'voltage', 4)}"]
        r.waveform = {"t": sol.t, "series": {f"V({red}) − V({black})": y}, "unit": "V"}
        r.assumptions.append(f"Transient run, initial state: {sol.initial}; Δt = {fmt(sol.dt, 'time')}.")


def _ammeter(model, p, settings, r, cache):
    if not p.component or p.component not in [c.id for c in model.components]:
        r.needs.append("Choose the component (branch) the ammeter is in series with.")
        return
    c = model.comp(p.component)
    a, b = model.node_label(c.nodes[0]), model.node_label(c.nodes[1])
    r.reference = f"in series with {c.id}; positive = current flowing through {c.id} from {a} to {b}"
    r.unit = "A"
    sol = _solve(model, settings, p.mode, cache)
    if p.mode == "dc":
        i = sol.i[c.id]
        r.value = i
        r.lines.append(f"DC current: {fmt(i, 'current', 4)}")
    elif p.mode == "ac":
        z = sol.i[c.id]
        r.value = z
        r.lines.append(f"AC current at {fmt(sol.freq, 'frequency')}: {fmt(abs(z) / math.sqrt(2), 'current', 4)} RMS "
                       f"({fmt(abs(z), 'current', 4)} peak), phase {math.degrees(math.atan2(z.imag, z.real)):.2f}°")
    else:
        y = sol.i[c.id]
        r.value = float(y[-1])
        tail = y[len(y) // 2:]
        r.lines += [f"At end of run: {fmt(y[-1], 'current', 4)}",
                    f"Second half: mean {fmt(float(tail.mean()), 'current', 4)}, RMS {fmt(float(np.sqrt((tail ** 2).mean())), 'current', 4)}"]
        r.waveform = {"t": sol.t, "series": {f"I({c.id})": y}, "unit": "A"}
    r.assumptions.append("Ideal ammeter (zero resistance) inserted in series with the branch; it measures the branch "
                         "current itself, not a voltage between nodes.")


def _scope(model, p, settings, r, cache):
    chans = [ch for ch in p.channels if ch and ch[0]]
    if not chans:
        r.needs.append("Add at least one channel (click a node with the scope tool).")
        return
    for node, ref in chans:
        r.needs += _need_nodes(model, node, ref)
    if r.needs:
        return
    sol = _solve(model, settings, "tran", cache)
    series = {}
    for k, (node, ref) in enumerate(chans[:4]):
        y = sol.v[node] - sol.v[ref]
        series[f"CH{k + 1}: {model.node_label(node)} vs {model.node_label(ref)}"] = y
        tail = y[len(y) // 2:]
        r.lines.append(f"CH{k + 1} {model.node_label(node)} − {model.node_label(ref)}: end {fmt(y[-1], 'voltage', 4)}, "
                       f"Vpp {fmt(float(tail.max() - tail.min()), 'voltage', 4)} (second half)")
    r.waveform = {"t": sol.t, "series": series, "unit": "V"}
    r.reference = "each channel: node vs its reference node"
    r.unit = "V"
    r.assumptions.append(f"Ideal scope (infinite input impedance), transient run of {fmt(sol.t[-1], 'time')}, "
                         f"initial state: {sol.initial}.")


def _ohmmeter(model, p, r):
    r.needs = _need_nodes(model, p.red, p.black)
    if r.needs:
        return
    a, b = model.node_label(p.red), model.node_label(p.black)
    r.reference = f"between {a} and {b}"
    r.unit = "Ω"
    val = S.resistance(model, p.red, p.black)
    r.value = val
    r.lines.append("Resistance: " + ("OL (open circuit, no conducting path)" if math.isinf(val) else fmt(val, "resistance", 4)))
    r.assumptions += ["Circuit DE-ENERGISED: every voltage source replaced by a short, every current source removed (open).",
                      "DC ohmmeter: capacitors read as open, inductors as their (zero) DC resistance.",
                      "Never measure resistance on a powered circuit; the real meter would read nonsense or be damaged."]
