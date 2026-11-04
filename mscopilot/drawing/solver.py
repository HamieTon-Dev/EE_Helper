"""Modified nodal analysis (MNA) solver for linear circuits of R, L, C and independent
V / I sources.

  dc_op(...)        DC operating point (C open, L short); nodes with no DC path to the
                    reference are reported as floating instead of being given a made-up value
  ac(...)           sinusoidal steady state at one frequency (complex MNA)
  sweep(...)        AC response over many frequencies
  transient(...)    time-domain simulation, backward-Euler companion models, fixed step
  resistance(...)   ohmmeter: sources removed (V shorted, I open), C open, L short,
                    1 A test current injected between two nodes

Unknowns: voltages of every non-reference node plus one branch current for each voltage
source and each inductor (current from the component's first to its second terminal).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from mscopilot.drawing.model import Component, DrawingModel

TWO_PI = 2 * math.pi


class SolverError(RuntimeError):
    """The model cannot be solved; the message says what the user must fix or supply."""


@dataclass
class Circuit:
    """Validated numeric view of a DrawingModel."""

    nodes: list[str]  # non-reference node ids, index = matrix row
    ref: str  # reference (ground) node id
    comps: list[Component]
    idx: dict[str, int] = field(default_factory=dict)
    branch: dict[str, int] = field(default_factory=dict)  # component id -> branch-current unknown
    open_switches: list[str] = field(default_factory=list)

    def n(self, nid: str | None) -> int:
        """Matrix index of a node, -1 for the reference."""
        if nid == self.ref:
            return -1
        return self.idx[nid]

    @property
    def size(self) -> int:
        return len(self.nodes) + len(self.branch)


def build(model: DrawingModel) -> Circuit:
    gnd = model.ground_nodes()
    if not gnd:
        raise SolverError("No ground/reference node is marked. Mark the node all voltages are measured from "
                          "(usually the source's negative terminal) as ground.")
    bad = [c for c in model.components if not c.supported]
    if bad:
        raise SolverError("Unsupported components prevent simulation: " + ", ".join(f"{c.id} ({c.kind_name()})" for c in bad)
                          + ". Remove them or replace them with a supported R/L/C/source model.")
    for c in model.components:
        if None in c.nodes or len(c.nodes) != 2:
            raise SolverError(f"{c.id} has an unconnected terminal. Connect both terminals before simulating.")
        if c.kind in "RLC" and c.value is None:
            raise SolverError(f"{c.id} has no value. Enter its {c.kind_name().lower()} value.")
        if c.kind in "RLC" and c.value <= 0:
            raise SolverError(f"{c.id} must have a positive value (got {c.value:g}).")
        if c.kind in ("V", "I"):
            s = c.src
            if s is None or (s.wave in ("dc", "step") and s.dc is None) or (s.wave in ("sine", "square") and (s.amp is None or not s.freq)):
                raise SolverError(f"{c.id}: the source value is missing. Set its waveform and amplitude "
                                  "(and frequency for sine/square) in the source settings.")
    ref = gnd[0]
    # all ground-flagged nodes are the same node electrically
    comps = []
    open_sw = []
    for c in model.components:
        cc = Component(**{k: getattr(c, k) for k in Component.__dataclass_fields__})
        cc.nodes = [ref if n in gnd else n for n in c.nodes]
        if c.kind == "SW":
            if c.state != "closed":
                open_sw.append(c.id)
                continue
            from mscopilot.drawing.model import SourceParams

            cc.kind, cc.src = "V", SourceParams("dc", dc=0.0)  # closed switch = ideal 0 V branch (short)
        comps.append(cc)
    used = sorted({n for c in comps for n in c.nodes if n != ref})
    cir = Circuit(used, ref, comps, open_switches=open_sw)
    cir.idx = {nid: i for i, nid in enumerate(used)}
    k = len(used)
    for c in comps:
        if c.kind in ("V", "L"):
            cir.branch[c.id] = k
            k += 1
    return cir


# ---------------------------------------------------------------- source values
def src_dc(c: Component) -> float:
    s = c.src
    if s.wave in ("dc", "step"):
        return s.dc
    return s.offset  # DC component of sine / square


def src_phasor(c: Component, f: float) -> complex:
    """AC phasor (peak amplitude) of a source at frequency f; 0 for sources at another
    frequency or DC-only sources."""
    s = c.src
    if s.wave == "sine" and s.freq and math.isclose(s.freq, f, rel_tol=1e-9):
        return s.amp * complex(math.cos(math.radians(s.phase_deg)), math.sin(math.radians(s.phase_deg)))
    return 0j


def src_t(c: Component, t: float) -> float:
    s = c.src
    if s.wave == "dc":
        return s.dc
    if s.wave == "step":
        return s.dc if t >= 0 else 0.0
    if s.wave == "sine":
        return s.offset + s.amp * math.sin(TWO_PI * s.freq * t + math.radians(s.phase_deg))
    ph = (s.freq * t + s.phase_deg / 360.0) % 1.0
    return s.offset + (s.amp if ph < 0.5 else -s.amp)


# ---------------------------------------------------------------- stamping
def _stamp_g(A, i, j, g):
    if i >= 0:
        A[i, i] += g
    if j >= 0:
        A[j, j] += g
    if i >= 0 and j >= 0:
        A[i, j] -= g
        A[j, i] -= g


def _stamp_i(b, i, j, cur):
    """Current `cur` flowing through the element from node i to node j (leaves i, enters j)."""
    if i >= 0:
        b[i] -= cur
    if j >= 0:
        b[j] += cur


def _stamp_vbranch(A, b, k, i, j, v, z=0.0):
    """Branch with V(i) − V(j) − z·I = v ; I flows i -> j through the element."""
    if i >= 0:
        A[i, k] += 1
        A[k, i] += 1
    if j >= 0:
        A[j, k] -= 1
        A[k, j] -= 1
    A[k, k] -= z
    b[k] += v


def _solve(A, b, cir: Circuit, what: str):
    try:
        x = np.linalg.solve(A, b)
    except np.linalg.LinAlgError:
        raise SolverError(_singular_reason(cir, what)) from None
    if not np.all(np.isfinite(x)):
        raise SolverError(_singular_reason(cir, what))
    # condition sanity: a nearly singular system gives meaningless numbers
    if np.linalg.cond(A) > 1e15:
        raise SolverError(_singular_reason(cir, what))
    return x


def _singular_reason(cir: Circuit, what: str) -> str:
    vs = [c for c in cir.comps if c.kind == "V"]
    loops = _voltage_loops(cir)
    if loops:
        return (f"The {what} has no unique solution: voltage sources/inductors form a loop ({', '.join(loops)}). "
                "Ideal voltage sources in parallel or in a loop with only inductors/wires is not solvable.")
    return (f"The {what} has no unique solution. Usually a node is floating (connected only through current sources "
            "or capacitors) or a current source has no return path. Check the connections and the ground node."
            + ("" if vs or any(c.kind == "I" for c in cir.comps) else " (There is also no source.)"))


def _voltage_loops(cir: Circuit) -> list[str]:
    """Components of the first loop made only of V sources and inductors (DC short paths)."""
    parent = {}

    def find(x):
        while parent.get(x, x) != x:
            x = parent.get(x, x)
        return x

    for c in cir.comps:
        if c.kind in ("V", "L"):
            a, b = find(c.nodes[0]), find(c.nodes[1])
            if a == b:
                return [c.id]
            parent[a] = b
    return []


# ---------------------------------------------------------------- results
@dataclass
class Solution:
    kind: str  # 'dc' | 'ac'
    v: dict[str, complex | float]  # node -> voltage (reference = 0)
    i: dict[str, complex | float]  # component -> current from nodes[0] to nodes[1] through it
    freq: float = 0.0
    floating: list[str] = field(default_factory=list)  # nodes with no DC path (dc only)

    def vdiff(self, a: str, b: str):
        return self.v[a] - self.v[b]


def _floating_dc(cir: Circuit) -> set[str]:
    """Nodes with no DC-conducting path (R, L, V) to the reference."""
    adj: dict[str, set[str]] = {}
    for c in cir.comps:
        if c.kind in ("R", "L", "V"):
            a, b = c.nodes
            adj.setdefault(a, set()).add(b)
            adj.setdefault(b, set()).add(a)
    seen, stack = {cir.ref}, [cir.ref]
    while stack:
        x = stack.pop()
        for y in adj.get(x, ()):
            if y not in seen:
                seen.add(y)
                stack.append(y)
    return set(cir.nodes) - seen


def dc_op(model: DrawingModel) -> Solution:
    cir = build(model)
    floating = _floating_dc(cir)
    # a current source feeding a floating island has no DC solution at all
    for c in cir.comps:
        if c.kind == "I" and src_dc(c) != 0 and (set(c.nodes) & floating):
            raise SolverError(f"{c.id} drives current into a part of the circuit with no DC return path "
                              "(only capacitors). The DC operating point does not exist.")
    A = np.zeros((cir.size, cir.size))
    b = np.zeros(cir.size)
    for c in cir.comps:
        i, j = cir.n(c.nodes[0]), cir.n(c.nodes[1])
        if c.kind == "R":
            _stamp_g(A, i, j, 1 / c.value)
        elif c.kind == "L":
            _stamp_vbranch(A, b, cir.branch[c.id], i, j, 0.0)  # short at DC
        elif c.kind == "V":
            _stamp_vbranch(A, b, cir.branch[c.id], i, j, src_dc(c))
        elif c.kind == "I":
            _stamp_i(b, i, j, src_dc(c))
    for nid in floating:  # tie floating islands to the reference so the rest can be solved
        k = cir.idx[nid]
        A[k, k] += 1.0
    x = _solve(A, b, cir, "DC operating point")
    v = {cir.ref: 0.0}
    v.update({nid: float(x[k]) for nid, k in cir.idx.items()})
    for nid in floating:
        v[nid] = math.nan
    cur = {}
    for c in cir.comps:
        a, bnode = c.nodes
        if c.kind == "R":
            cur[c.id] = (v[a] - v[bnode]) / c.value
        elif c.kind in ("L", "V"):
            cur[c.id] = float(x[cir.branch[c.id]])
        elif c.kind == "C":
            cur[c.id] = 0.0
        else:
            cur[c.id] = src_dc(c)
    for sid in cir.open_switches:
        cur[sid] = 0.0
    # map grounded aliases back so every model node has a voltage
    for gid in model.ground_nodes():
        v[gid] = 0.0
    for nid in model.node_ids():  # nodes only touched by open switches
        v.setdefault(nid, math.nan)
    return Solution("dc", v, cur, 0.0, sorted(floating))


def ac(model: DrawingModel, f: float) -> Solution:
    if not f > 0:
        raise SolverError("AC analysis needs a frequency greater than 0 Hz (use DC analysis for 0 Hz).")
    cir = build(model)
    w = TWO_PI * f
    A = np.zeros((cir.size, cir.size), dtype=complex)
    b = np.zeros(cir.size, dtype=complex)
    for c in cir.comps:
        i, j = cir.n(c.nodes[0]), cir.n(c.nodes[1])
        if c.kind == "R":
            _stamp_g(A, i, j, 1 / c.value)
        elif c.kind == "C":
            _stamp_g(A, i, j, 1j * w * c.value)
        elif c.kind == "L":
            _stamp_vbranch(A, b, cir.branch[c.id], i, j, 0.0, z=1j * w * c.value)
        elif c.kind == "V":
            _stamp_vbranch(A, b, cir.branch[c.id], i, j, src_phasor(c, f))
        elif c.kind == "I":
            _stamp_i(b, i, j, src_phasor(c, f))
    x = _solve(A, b, cir, f"AC analysis at {f:g} Hz")
    v = {cir.ref: 0j}
    v.update({nid: complex(x[k]) for nid, k in cir.idx.items()})
    cur = {}
    for c in cir.comps:
        a, bn = c.nodes
        if c.kind == "R":
            cur[c.id] = (v[a] - v[bn]) / c.value
        elif c.kind == "C":
            cur[c.id] = (v[a] - v[bn]) * 1j * w * c.value
        elif c.kind in ("L", "V"):
            cur[c.id] = complex(x[cir.branch[c.id]])
        else:
            cur[c.id] = src_phasor(c, f)
    for sid in cir.open_switches:
        cur[sid] = 0j
    for gid in model.ground_nodes():
        v[gid] = 0j
    for nid in model.node_ids():
        v.setdefault(nid, complex(math.nan, math.nan))
    return Solution("ac", v, cur, f)


def ac_frequencies(model: DrawingModel) -> list[float]:
    return sorted({c.src.freq for c in model.components if c.kind in "VI" and c.src and c.src.wave == "sine" and c.src.freq})


def sweep(model: DrawingModel, freqs, drive: str | None = None) -> dict:
    """AC response over frequency with one source driven at 1 (peak) and others off.

    Returns {'f': array, 'v': {node: complex array}, 'i': {comp: complex array}, 'drive': id}.
    """
    srcs = [c for c in model.components if c.kind in "VI"]
    if not srcs:
        raise SolverError("A frequency response needs an independent source to drive the circuit.")
    drive = drive or srcs[0].id
    m = model.clone()
    from mscopilot.drawing.model import SourceParams

    for c in m.components:
        if c.kind in "VI":
            c.src = SourceParams("sine", amp=1.0 if c.id == drive else 0.0, freq=1.0, offset=0.0)
    freqs = np.asarray(freqs, dtype=float)
    out_v: dict[str, list] = {n: [] for n in m.node_ids()}
    out_i: dict[str, list] = {c.id: [] for c in m.components}
    for f in freqs:
        for c in m.components:
            if c.kind in "VI":
                c.src.freq = float(f)
        sol = ac(m, float(f))
        for n in out_v:
            out_v[n].append(sol.v.get(n, 0j))
        for k in out_i:
            out_i[k].append(sol.i[k])
    return {"f": freqs, "v": {k: np.array(v) for k, v in out_v.items()},
            "i": {k: np.array(v) for k, v in out_i.items()}, "drive": drive}


@dataclass
class TransientResult:
    t: np.ndarray
    v: dict[str, np.ndarray]
    i: dict[str, np.ndarray]
    initial: str
    dt: float


def transient(model: DrawingModel, t_stop: float, steps: int = 4000, initial: str = "dc") -> TransientResult:
    """Backward-Euler transient. initial='dc': start from the DC operating point with the
    sources at their t=0⁻ values (steps start at 0); 'zero': all capacitors at 0 V and
    inductors at 0 A."""
    if not t_stop > 0:
        raise SolverError("The simulation duration must be greater than zero.")
    steps = int(max(50, min(steps, 200000)))
    cir = build(model)
    dt = t_stop / steps
    n = cir.size
    vprev = {nid: 0.0 for nid in cir.nodes}
    vprev[cir.ref] = 0.0
    iL = {c.id: 0.0 for c in cir.comps if c.kind == "L"}
    if initial == "dc":
        m0 = model.clone()
        for c in m0.components:  # values just before t = 0
            if c.kind in "VI" and c.src.wave == "step":
                c.src.dc = 0.0
            elif c.kind in "VI" and c.src.wave in ("sine", "square"):
                from mscopilot.drawing.model import SourceParams

                c.src = SourceParams("dc", dc=src_t(c, 0.0))
        op = dc_op(m0)
        if op.floating:
            raise SolverError("Some nodes have no DC path, so a DC initial state is undefined ("
                              + ", ".join(op.floating) + "). Choose 'zero initial state' instead.")
        vprev.update({k: float(v) for k, v in op.v.items() if k in vprev})
        for c in cir.comps:
            if c.kind == "L":
                iL[c.id] = float(op.i[c.id])
    ts = np.linspace(0.0, t_stop, steps + 1)
    V = {nid: np.zeros(steps + 1) for nid in cir.nodes}
    I = {c.id: np.zeros(steps + 1) for c in cir.comps}
    for nid in cir.nodes:
        V[nid][0] = vprev[nid]
    # base conductance matrix (time-invariant for a fixed step)
    A = np.zeros((n, n))
    for c in cir.comps:
        i, j = cir.n(c.nodes[0]), cir.n(c.nodes[1])
        if c.kind == "R":
            _stamp_g(A, i, j, 1 / c.value)
        elif c.kind == "C":
            _stamp_g(A, i, j, c.value / dt)
        elif c.kind == "L":
            k = cir.branch[c.id]
            if i >= 0:
                A[i, k] += 1
                A[k, i] += 1
            if j >= 0:
                A[j, k] -= 1
                A[k, j] -= 1
            A[k, k] -= c.value / dt
        elif c.kind == "V":
            k = cir.branch[c.id]
            if i >= 0:
                A[i, k] += 1
                A[k, i] += 1
            if j >= 0:
                A[j, k] -= 1
                A[k, j] -= 1
    try:
        lu = np.linalg.inv(A)
    except np.linalg.LinAlgError:
        raise SolverError(_singular_reason(cir, "transient simulation")) from None
    if np.linalg.cond(A) > 1e15:
        raise SolverError(_singular_reason(cir, "transient simulation"))
    # initial currents
    for c in cir.comps:
        if c.kind == "R":
            I[c.id][0] = (vprev[c.nodes[0]] - vprev[c.nodes[1]]) / c.value
        elif c.kind == "L":
            I[c.id][0] = iL[c.id]
        elif c.kind == "I":
            I[c.id][0] = src_t(c, 0.0)
    for s in range(1, steps + 1):
        t = ts[s]
        b = np.zeros(n)
        for c in cir.comps:
            i, j = cir.n(c.nodes[0]), cir.n(c.nodes[1])
            if c.kind == "C":
                vp = vprev[c.nodes[0]] - vprev[c.nodes[1]]
                _stamp_i(b, i, j, -c.value / dt * vp)  # companion current source
            elif c.kind == "L":
                b[cir.branch[c.id]] += -c.value / dt * iL[c.id]
            elif c.kind == "V":
                b[cir.branch[c.id]] += src_t(c, t)
            elif c.kind == "I":
                _stamp_i(b, i, j, src_t(c, t))
        x = lu @ b
        vnow = {cir.ref: 0.0}
        vnow.update({nid: float(x[k]) for nid, k in cir.idx.items()})
        for c in cir.comps:
            a, bb = c.nodes
            if c.kind == "R":
                cur = (vnow[a] - vnow[bb]) / c.value
            elif c.kind == "C":
                cur = c.value / dt * ((vnow[a] - vnow[bb]) - (vprev[a] - vprev[bb]))
            elif c.kind in ("L", "V"):
                cur = float(x[cir.branch[c.id]])
                if c.kind == "L":
                    iL[c.id] = cur
            else:
                cur = src_t(c, t)
            I[c.id][s] = cur
        for nid in cir.nodes:
            V[nid][s] = vnow[nid]
        vprev = vnow
    V[cir.ref] = np.zeros(steps + 1)
    for gid in model.ground_nodes():
        V[gid] = V[cir.ref]
    for sid in cir.open_switches:
        I[sid] = np.zeros(steps + 1)
    for nid in model.node_ids():
        V.setdefault(nid, np.full(steps + 1, np.nan))
    return TransientResult(ts, V, I, initial, dt)


def resistance(model: DrawingModel, a: str, b: str) -> float:
    """Ohmmeter reading between nodes a and b with the circuit de-energised:
    voltage sources replaced by shorts, current sources by opens, capacitors open (DC),
    inductors short (DC). Returns math.inf for no conducting path."""
    if a == b:
        return 0.0
    gnd = set(model.ground_nodes())
    # work on a copy that does not need a ground (the meter defines its own reference)
    shorts = []
    res = []
    unsupported = [c for c in model.components if not c.supported]
    if unsupported:
        raise SolverError("Unsupported components make the resistance undefined: " + ", ".join(c.id for c in unsupported))
    for c in model.components:
        if None in c.nodes:
            continue
        n0, n1 = c.nodes
        if c.kind in ("V", "L") or (c.kind == "SW" and c.state == "closed"):
            shorts.append((n0, n1))
        elif c.kind == "R":
            if c.value is None or c.value <= 0:
                raise SolverError(f"{c.id} has no valid resistance value.")
            res.append((n0, n1, c.value))
    # union nodes joined by shorts (ground nodes are one node too)
    parent = {}

    def find(x):
        while parent.get(x, x) != x:
            x = parent.get(x, x)
        return x

    def union(x, y):
        parent[find(x)] = find(y)

    g = list(gnd)
    for x in g[1:]:
        union(g[0], x)
    for x, y in shorts:
        union(x, y)
    ra, rb = find(a), find(b)
    if ra == rb:
        return 0.0
    nodes = sorted({find(x) for x, y, _ in res} | {find(y) for x, y, _ in res} | {ra, rb})
    # connectivity check
    adj = {k: set() for k in nodes}
    for x, y, _ in res:
        fx, fy = find(x), find(y)
        if fx != fy:
            adj[fx].add(fy)
            adj[fy].add(fx)
    seen, stack = {ra}, [ra]
    while stack:
        u = stack.pop()
        for w in adj[u]:
            if w not in seen:
                seen.add(w)
                stack.append(w)
    if rb not in seen:
        return math.inf
    comp = sorted(seen)
    others = [k for k in comp if k != rb]
    idx = {k: i for i, k in enumerate(others)}
    G = np.zeros((len(others), len(others)))
    for x, y, r in res:
        fx, fy = find(x), find(y)
        if fx == fy or fx not in seen:
            continue
        _stamp_g(G, idx.get(fx, -1), idx.get(fy, -1), 1 / r)
    rhs = np.zeros(len(others))
    rhs[idx[ra]] = 1.0  # 1 A injected at a, returning through b (reference)
    v = np.linalg.solve(G, rhs)
    return float(v[idx[ra]])
