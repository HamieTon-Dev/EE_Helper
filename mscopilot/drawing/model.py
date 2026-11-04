"""Editable circuit / drawing model reconstructed from an uploaded drawing.

Coordinates are pixels of the ORIGINAL image (before any view rotation). Every recognised
item keeps its confidence and where it came from ('ai' or 'user'), and nothing missing
is filled in silently: unknown values stay ``None`` and are reported by validation.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field

SUPPORTED_KINDS = {
    "R": ("Resistor", "Ω"),
    "C": ("Capacitor", "F"),
    "L": ("Inductor", "H"),
    "V": ("Voltage source", "V"),
    "I": ("Current source", "A"),
}
# recognised but not simulated: analysis refuses circuits containing them
UNSUPPORTED_KINDS = {
    "D": "Diode", "LED": "LED", "Q": "Transistor", "M": "MOSFET", "U": "IC / op-amp", "SW": "Switch",
    "F": "Fuse", "T": "Transformer", "LAMP": "Lamp", "MOTOR": "Motor", "X": "Unknown symbol",
}
WAVES = ("dc", "sine", "square", "step")


@dataclass
class SourceParams:
    """Independent source settings. DC sources use ``dc``; others use amp/freq/offset/phase.

    sine:   offset + amp·sin(2πft + phase)          (amp = PEAK)
    square: offset ± amp, 50 % duty at freq        (amp = PEAK)
    step:   0 for t < 0, ``dc`` for t ≥ 0 (transient); behaves as DC for DC analysis
    """

    wave: str = "dc"
    dc: float | None = None
    amp: float | None = None
    freq: float | None = None
    offset: float = 0.0
    phase_deg: float = 0.0


@dataclass
class Component:
    id: str
    kind: str  # R C L V I, or an UNSUPPORTED_KINDS key
    label: str = ""
    value: float | None = None  # base SI units; None = unknown
    value_text: str = ""  # as read from the drawing
    nodes: list = field(default_factory=lambda: [None, None])  # [n+, n-] node ids
    pins: list = field(default_factory=lambda: [None, None])  # [[x, y], [x, y]] image px
    bbox: list | None = None  # [x0, y0, x1, y1] image px
    confidence: float = 1.0  # overall recognition confidence 0..1
    value_confidence: float = 1.0
    source: str = "user"  # 'ai' | 'user'
    polarized: bool = False  # electrolytic capacitor: nodes[0] is '+'
    rating_power: float | None = None  # W
    rating_voltage: float | None = None  # V
    rating_current: float | None = None  # A
    src: SourceParams | None = None  # only for V / I
    notes: str = ""

    @property
    def supported(self) -> bool:
        return self.kind in SUPPORTED_KINDS

    def kind_name(self) -> str:
        return SUPPORTED_KINDS.get(self.kind, (UNSUPPORTED_KINDS.get(self.kind, self.kind),))[0]


@dataclass
class Node:
    id: str
    name: str = ""
    points: list = field(default_factory=list)  # [[x, y], ...] where the node appears on the image
    ground: bool = False
    confidence: float = 1.0

    def display(self) -> str:
        return f"{self.id}" + (f" ({self.name})" if self.name and self.name != self.id else "") + (" ⏚" if self.ground else "")


@dataclass
class Junction:
    """A place where wires meet or cross. 'junction' = electrically joined (dot),
    'crossing' = wires pass over each other without connecting, 'ambiguous' = unclear."""

    id: str
    x: float
    y: float
    kind: str = "ambiguous"
    confidence: float = 0.5
    nodes: list = field(default_factory=list)  # node ids meeting here
    note: str = ""


@dataclass
class Wire:
    node: str
    points: list  # polyline [[x, y], ...]


@dataclass
class Dimension:
    id: str
    text: str
    value: float | None = None
    unit: str = ""
    p1: list | None = None
    p2: list | None = None
    confidence: float = 1.0


@dataclass
class Unreadable:
    x: float
    y: float
    w: float
    h: float
    text_seen: str = ""
    note: str = ""


@dataclass
class DrawingModel:
    drawing_type: str = "unknown"  # electrical_schematic | mechanical | architectural | other | unknown
    drawing_type_confidence: float = 0.0
    title: str = ""
    components: list[Component] = field(default_factory=list)
    nodes: list[Node] = field(default_factory=list)
    junctions: list[Junction] = field(default_factory=list)
    wires: list[Wire] = field(default_factory=list)
    dimensions: list[Dimension] = field(default_factory=list)
    unreadable: list[Unreadable] = field(default_factory=list)
    texts: list[dict] = field(default_factory=list)  # free labels [{'text','x','y'}]
    recognition_warnings: list[str] = field(default_factory=list)
    scale: dict | None = None  # {'px_per_unit': float, 'unit': 'mm'} from a user-given reference
    confirmed: bool = False
    image_size: list | None = None  # [w, h]

    # ---------------------------------------------------------- lookups
    def comp(self, cid: str) -> Component:
        for c in self.components:
            if c.id == cid:
                return c
        raise KeyError(cid)

    def node(self, nid: str) -> Node:
        for n in self.nodes:
            if n.id == nid:
                return n
        raise KeyError(nid)

    def node_ids(self) -> list[str]:
        return [n.id for n in self.nodes]

    def ground_nodes(self) -> list[str]:
        return [n.id for n in self.nodes if n.ground]

    def terminals_at(self, nid: str) -> list[tuple[str, int]]:
        return [(c.id, i) for c in self.components for i, n in enumerate(c.nodes) if n == nid]

    def node_label(self, nid: str | None) -> str:
        if nid is None:
            return "unconnected"
        try:
            return self.node(nid).display()
        except KeyError:
            return nid

    # ---------------------------------------------------------- ids
    def next_component_id(self, kind: str) -> str:
        n = 1
        used = {c.id for c in self.components}
        while f"{kind}{n}" in used:
            n += 1
        return f"{kind}{n}"

    def next_node_id(self) -> str:
        n = 1
        used = set(self.node_ids())
        while f"N{n}" in used:
            n += 1
        return f"N{n}"

    # ---------------------------------------------------------- edits
    def add_node(self, point=None, name="", ground=False) -> Node:
        n = Node(self.next_node_id(), name=name, points=[list(point)] if point else [], ground=ground)
        self.nodes.append(n)
        self.confirmed = False
        return n

    def merge_nodes(self, keep: str, gone: str) -> None:
        """Join two nodes (draw a wire between them)."""
        if keep == gone:
            return
        a, b = self.node(keep), self.node(gone)
        a.points += b.points
        a.ground = a.ground or b.ground
        a.name = a.name or b.name
        for c in self.components:
            c.nodes = [keep if n == gone else n for n in c.nodes]
        for w in self.wires:
            if w.node == gone:
                w.node = keep
        for j in self.junctions:
            j.nodes = sorted(set(keep if n == gone else n for n in j.nodes))
        self.nodes = [n for n in self.nodes if n.id != gone]
        self.confirmed = False

    def remove_unused_nodes(self) -> None:
        used = {n for c in self.components for n in c.nodes if n}
        self.nodes = [n for n in self.nodes if n.id in used or n.ground]

    def set_junction(self, jid: str, kind: str) -> None:
        """Resolve a wire meeting point. 'junction' joins all its nodes into one."""
        j = next(x for x in self.junctions if x.id == jid)
        j.kind = kind
        j.confidence = 1.0
        if kind == "junction" and len(j.nodes) > 1:
            keep = j.nodes[0]
            for other in j.nodes[1:]:
                if other in self.node_ids():
                    self.merge_nodes(keep, other)
            j.nodes = [keep]
        self.confirmed = False

    # ---------------------------------------------------------- persistence
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "DrawingModel":
        d = copy.deepcopy(d)

        def comp(x):
            src = x.pop("src", None)
            c = Component(**{k: v for k, v in x.items() if k in Component.__dataclass_fields__})
            c.src = SourceParams(**src) if src else None
            return c

        return cls(
            drawing_type=d.get("drawing_type", "unknown"),
            drawing_type_confidence=d.get("drawing_type_confidence", 0.0),
            title=d.get("title", ""),
            components=[comp(x) for x in d.get("components", [])],
            nodes=[Node(**x) for x in d.get("nodes", [])],
            junctions=[Junction(**x) for x in d.get("junctions", [])],
            wires=[Wire(**x) for x in d.get("wires", [])],
            dimensions=[Dimension(**x) for x in d.get("dimensions", [])],
            unreadable=[Unreadable(**x) for x in d.get("unreadable", [])],
            texts=d.get("texts", []),
            recognition_warnings=d.get("recognition_warnings", []),
            scale=d.get("scale"),
            confirmed=d.get("confirmed", False),
            image_size=d.get("image_size"),
        )

    def clone(self) -> "DrawingModel":
        return DrawingModel.from_dict(self.to_dict())

    def electrical_hash(self) -> str:
        """Hash of everything that affects calculations (not positions/confidence), used to
        invalidate outdated results."""
        data = {
            "components": sorted(
                [(c.id, c.kind, c.value, list(c.nodes), c.polarized, c.rating_power, c.rating_voltage, c.rating_current,
                  asdict(c.src) if c.src else None) for c in self.components], key=lambda t: t[0]),
            "ground": sorted(self.ground_nodes()),
            "junctions": sorted((j.id, j.kind) for j in self.junctions),
        }
        return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()[:16]

    # ---------------------------------------------------------- netlist
    def to_spice(self, title: str = "") -> str:
        """SPICE netlist of the supported part of the model (ground node -> 0)."""
        gnd = set(self.ground_nodes())

        def nn(n):
            return "0" if n in gnd else (n or "NC")

        lines = [f"* {title or self.title or 'Circuit reconstructed from drawing'}"]
        for c in self.components:
            if not c.supported:
                lines.append(f"* unsupported: {c.id} ({c.kind_name()}) between {nn(c.nodes[0])} and {nn(c.nodes[1])}")
                continue
            name = c.id if c.id[:1].upper() == c.kind else f"{c.kind}{c.id}"
            if c.kind in "RLC":
                val = "?" if c.value is None else f"{c.value:.6g}"
                lines.append(f"{name} {nn(c.nodes[0])} {nn(c.nodes[1])} {val}")
            else:
                s = c.src or SourceParams()
                if s.wave == "sine":
                    spec = f"DC {s.offset:.6g} AC {s.amp or 0:.6g} {s.phase_deg:.6g} SIN({s.offset:.6g} {s.amp or 0:.6g} {s.freq or 0:.6g} 0 0 {s.phase_deg:.6g})"
                elif s.wave == "square":
                    per = 1 / s.freq if s.freq else 0
                    lo, hi = s.offset - (s.amp or 0), s.offset + (s.amp or 0)
                    spec = f"PULSE({lo:.6g} {hi:.6g} 0 1n 1n {per / 2:.6g} {per:.6g})"
                else:
                    spec = f"DC {s.dc if s.dc is not None else '?'}"
                lines.append(f"{name} {nn(c.nodes[0])} {nn(c.nodes[1])} {spec}")
        lines.append(".end")
        return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- value parsing
_PREFIX = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "μ": 1e-6, "m": 1e-3, "": 1.0, "k": 1e3, "K": 1e3,
           "M": 1e6, "meg": 1e6, "MEG": 1e6, "G": 1e9, "R": 1.0, "r": 1.0}
_UNIT_FOR_KIND = {"R": ("Ω", "ohm", "ohms", "Ohm", "Ohms", "R"), "C": ("F",), "L": ("H",), "V": ("V", "Vdc", "VDC", "Vac", "VAC", "Vrms", "Vp", "Vpp"),
                  "I": ("A",)}


def parse_value(text: str, kind: str) -> tuple[float | None, str]:
    """Parse a value as printed on a drawing for a component kind.

    Handles 4.7k, 4k7 (RKM code), 10uF, 10µ, 2.2 kΩ, 100n, 1M, 330R, 12 V, 1.5mA.
    Returns (value, problem). value is None with a reason when it cannot be read with
    certainty - it is never guessed.
    """
    s = (text or "").strip().replace(" ", "").replace(",", ".")
    if not s:
        return None, "no value printed"
    units = _UNIT_FOR_KIND.get(kind, ())
    low = s
    for u in sorted(units, key=len, reverse=True):
        if low.endswith(u) and len(low) > len(u):
            low = low[: -len(u)]
            break
    low = low.replace("Ω", "")
    m = re.fullmatch(r"([0-9]*\.?[0-9]+)(meg|MEG|[pnuµμmkKMGRr]?)", low)
    if m:
        num, pre = m.groups()
        if kind == "R" and pre in ("m",) and units:
            pass
        return float(num) * _PREFIX[pre], ""
    m = re.fullmatch(r"([0-9]+)([pnuµμmkKMGRr])([0-9]+)", low)  # RKM: 4k7 = 4.7k, 2R2 = 2.2
    if m:
        a, pre, b = m.groups()
        return float(f"{a}.{b}") * _PREFIX[pre], ""
    m = re.fullmatch(r"([0-9]*\.?[0-9]+)e([-+]?[0-9]+)", low)
    if m:
        return float(low), ""
    return None, f"cannot read the value '{text}'"
