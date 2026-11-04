"""Problem detection on a (corrected) drawing model.

Findings are split into:
  problem  - confirmed by the model / solver (e.g. a shorted source, an overloaded resistor)
  concern  - possible issue the user should check (low recognition confidence, derating)
  missing  - information that must be supplied before a reliable result exists
Every finding names its location (components / nodes / junctions / image region), cause,
likely effect and a recommended correction, and carries image points for highlighting.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from eesimlab.drawing.model import SUPPORTED_KINDS, DrawingModel

LOW_CONF = 0.6
OUTPUT_NAME = re.compile(r"(?i)^(v?out|output|vo|tp\d*|test|probe)")


@dataclass
class Finding:
    severity: str  # problem | concern | missing
    title: str
    where: list = field(default_factory=list)  # [('component', id) | ('node', id) | ('junction', id) | ('region', [x,y,w,h])]
    cause: str = ""
    effect: str = ""
    fix: str = ""
    points: list = field(default_factory=list)  # [[x, y], ...] image px to highlight
    rule: str = ""

    def location(self, model: DrawingModel) -> str:
        parts = []
        for kind, ref in self.where:
            if kind == "component":
                parts.append(f"component {ref}")
            elif kind == "node":
                parts.append(f"node {model.node_label(ref)}")
            elif kind == "junction":
                parts.append(f"wire meeting point {ref}")
            elif kind == "region":
                parts.append(f"image region at ({ref[0]:.0f}, {ref[1]:.0f})")
        return ", ".join(parts) or "whole drawing"


def _cpoints(model, cid):
    c = model.comp(cid)
    if c.bbox:
        x0, y0, x1, y1 = c.bbox
        return [[(x0 + x1) / 2, (y0 + y1) / 2]]
    return [p for p in c.pins if p]


def _npoints(model, nid):
    try:
        return list(model.node(nid).points[:3])
    except KeyError:
        return []


def static_checks(model: DrawingModel) -> list[Finding]:
    F: list[Finding] = []
    add = F.append
    if model.drawing_type not in ("electrical_schematic", "unknown"):
        add(Finding("concern", f"Drawing classified as '{model.drawing_type}'", cause="Circuit analysis applies to electrical "
                    "schematics only.", effect="Only recognised dimensions and user-scaled measurements are available.",
                    fix="If this is a schematic, change the drawing type and confirm the components.", rule="type"))
        return F
    if not model.components:
        add(Finding("missing", "No components in the model", cause="Nothing was recognised or entered.",
                    effect="Nothing can be analysed.", fix="Run recognition or add components manually on the drawing.",
                    rule="empty"))
        return F

    for u in model.unreadable:
        add(Finding("missing", "Unreadable detail on the drawing", [("region", [u.x, u.y, u.w, u.h])],
                    cause=f"Recognition could not read this area{': ' + repr(u.text_seen) if u.text_seen else ''}. {u.note}".strip(),
                    effect="A value or connection here may be missing from the model.",
                    fix="Zoom in, then enter the value or connection manually.", points=[[u.x + u.w / 2, u.y + u.h / 2]],
                    rule="unreadable"))
    for j in model.junctions:
        if j.kind == "ambiguous":
            add(Finding("missing", f"Unclear wire meeting at {j.id}", [("junction", j.id)],
                        cause="It is not clear whether the wires are joined (junction dot) or only cross.",
                        effect="Joined and crossing wires give different circuits; results would be unreliable.",
                        fix="Mark it as 'connected junction' or 'crossing (not connected)'.", points=[[j.x, j.y]],
                        rule="junction"))
        elif j.confidence < LOW_CONF:
            add(Finding("concern", f"Low-confidence {j.kind} at {j.id}", [("junction", j.id)],
                        cause=f"Recognised as a {j.kind} with confidence {j.confidence:.0%}.",
                        effect="If wrong, nodes are joined or split incorrectly.", fix="Confirm the connection type.",
                        points=[[j.x, j.y]], rule="junction"))

    labels: dict[str, list] = {}
    for c in model.components:
        pts = _cpoints(model, c.id)
        if not c.supported:
            add(Finding("missing", f"{c.id}: {c.kind_name()} is not supported by the solver", [("component", c.id)],
                        cause="Only resistors, capacitors, inductors and independent voltage/current sources are simulated.",
                        effect="The circuit cannot be simulated while it contains this part.",
                        fix="Replace it with an equivalent supported model, or remove it if it is not part of the analysis.",
                        points=pts, rule="unsupported"))
        if None in c.nodes:
            add(Finding("missing", f"{c.id} has an unconnected terminal", [("component", c.id)],
                        cause="At least one terminal is not attached to any node.",
                        effect="The circuit is open at this component.", fix="Connect the terminal to the correct node.",
                        points=pts, rule="unconnected"))
        elif len(c.nodes) == 2 and c.nodes[0] == c.nodes[1]:
            sev = "problem"
            if c.kind == "V":
                add(Finding(sev, f"Voltage source {c.id} is short-circuited", [("component", c.id)],
                            cause="Both terminals are on the same node.", effect="An ideal source would deliver infinite current; "
                            "a real one would overheat or trip.", fix="Check the wiring; the two terminals must go to different nodes.",
                            points=pts, rule="short"))
            else:
                add(Finding(sev, f"{c.id} is shorted (both ends on one node)", [("component", c.id)],
                            cause="A wire connects both terminals.", effect=f"{c.id} carries no current and has no effect.",
                            fix="Remove the bypass wire or the component.", points=pts, rule="short"))
        if c.supported and c.kind in ("R", "L", "C"):
            if c.value is None:
                add(Finding("missing", f"{c.id}: value missing", [("component", c.id)],
                            cause=f"No readable {c.kind_name().lower()} value ({c.value_text or 'nothing printed'}).",
                            effect="Calculations involving this part cannot be done.", fix="Enter the value.", points=pts,
                            rule="value"))
            else:
                lim = {"R": (1e-3, 1e9), "C": (1e-15, 1.0), "L": (1e-12, 1e3)}[c.kind]
                if not lim[0] <= c.value <= lim[1]:
                    add(Finding("concern", f"{c.id}: unusual value {c.value:g} {SUPPORTED_KINDS[c.kind][1]}",
                                [("component", c.id)], cause="Outside the usual range for this part.",
                                effect="Probably a misread prefix (e.g. m vs M, u vs n).", fix="Check the value on the drawing.",
                                points=pts, rule="value"))
                if c.source == "ai" and c.value_confidence < LOW_CONF:
                    add(Finding("concern", f"{c.id}: value read with low confidence", [("component", c.id)],
                                cause=f"'{c.value_text}' read with {c.value_confidence:.0%} confidence.",
                                effect="A misread value changes every result that uses it.", fix="Confirm or correct the value.",
                                points=pts, rule="value"))
        if c.kind == "SW" and c.state not in ("open", "closed"):
            add(Finding("missing", f"{c.id}: switch position unknown", [("component", c.id)],
                        cause="The drawing does not say whether the switch is open or closed.",
                        effect="The circuit differs completely between the two positions.",
                        fix="Set the switch state (open or closed).", points=pts, rule="source"))
        if c.supported and c.kind in ("V", "I"):
            s = c.src
            ok = s is not None and ((s.wave in ("dc", "step") and s.dc is not None) or (s.wave in ("sine", "square") and s.amp is not None and s.freq))
            if not ok:
                add(Finding("missing", f"{c.id}: source settings incomplete", [("component", c.id)],
                            cause="Waveform, amplitude or frequency is not known.", effect="The circuit has no defined excitation.",
                            fix="Set the source waveform and value in the source settings.", points=pts, rule="source"))
        if c.source == "ai" and c.confidence < LOW_CONF:
            add(Finding("concern", f"{c.id}: symbol recognised with low confidence", [("component", c.id)],
                        cause=f"Identified as a {c.kind_name().lower()} with {c.confidence:.0%} confidence.",
                        effect="A wrong symbol type changes the circuit.", fix="Confirm the component type.", points=pts,
                        rule="symbol"))
        if c.label:
            labels.setdefault(c.label.strip().upper(), []).append(c)
    for lab, cs in labels.items():
        vals = {c.value for c in cs if c.value is not None}
        if len(cs) > 1 and len(vals) > 1:
            add(Finding("concern", f"Label {lab} used for parts with different values", [("component", c.id) for c in cs],
                        cause="Several components carry the same reference designator but different values.",
                        effect="The drawing is internally inconsistent; at least one value is probably wrong.",
                        fix="Give each part a unique designator or correct the values.",
                        points=[p for c in cs for p in _cpoints(model, c.id)], rule="labels"))

    gnd = model.ground_nodes()
    if not gnd:
        add(Finding("missing", "No ground / reference node", cause="No node is marked as ground.",
                    effect="Node voltages have no reference, so no voltage can be calculated.",
                    fix="Mark the reference node (usually the source's negative terminal) as ground.", rule="ground"))

    # dangling (single-terminal) nodes
    for n in model.nodes:
        terms = model.terminals_at(n.id)
        if len(terms) == 1 and not n.ground:
            cid = terms[0][0]
            if OUTPUT_NAME.match(n.name or ""):
                add(Finding("concern", f"Output terminal {model.node_label(n.id)} is unloaded", [("node", n.id), ("component", cid)],
                            cause="Only one component connects here; it looks like an output/test point.",
                            effect="Results assume no load; a real load would change them.",
                            fix="Add the load resistance if one will be connected.", points=_npoints(model, n.id), rule="open"))
            else:
                add(Finding("problem", f"Open circuit at node {model.node_label(n.id)}", [("node", n.id), ("component", cid)],
                            cause=f"Only {cid} connects to this node, so its other end goes nowhere.",
                            effect=f"No current can flow through {cid}.",
                            fix="Connect the node to the rest of the circuit, or remove the dangling part.",
                            points=_npoints(model, n.id) or _cpoints(model, cid), rule="open"))
        elif not terms and not n.ground:
            add(Finding("concern", f"Node {model.node_label(n.id)} connects to nothing", [("node", n.id)],
                        cause="A wire segment with no components.", effect="None.", fix="Delete it or connect it.",
                        points=_npoints(model, n.id), rule="open"))

    # islands without ground
    if gnd:
        adj: dict[str, set] = {}
        for c in model.components:
            ns = [n for n in c.nodes if n]
            for a in ns:  # every terminal of a part is (topologically) linked to its others
                for b in ns:
                    if a != b:
                        adj.setdefault(a, set()).add(b)
            for a in ns:
                adj.setdefault(a, set())
        seen = set(gnd)
        stack = list(gnd)
        while stack:
            x = stack.pop()
            for y in adj.get(x, ()):
                if y not in seen:
                    seen.add(y)
                    stack.append(y)
        floating = [n for n in adj if n not in seen]
        if floating:
            add(Finding("problem", "Part of the circuit is floating (not connected to ground)", [("node", n) for n in floating],
                        cause="These nodes have no connection, through any component, to the reference node.",
                        effect="Their voltages are undefined; a solver cannot assign them.",
                        fix="Connect that section to the rest of the circuit or give it its own reference.",
                        points=[p for n in floating for p in _npoints(model, n)], rule="floating"))
        # DC path check (capacitor-isolated nodes)
        dc_adj: dict[str, set] = {}
        for c in model.components:
            if None in c.nodes or c.kind not in ("R", "L", "V") or len(c.nodes) != 2:
                continue
            a, b = c.nodes
            dc_adj.setdefault(a, set()).add(b)
            dc_adj.setdefault(b, set()).add(a)
        dseen, stack = set(gnd), list(gnd)
        while stack:
            x = stack.pop()
            for y in dc_adj.get(x, ()):
                if y not in dseen:
                    dseen.add(y)
                    stack.append(y)
        iso = [n for n in seen if n not in dseen and n not in floating]
        if iso:
            add(Finding("concern", "Nodes with no DC path to ground", [("node", n) for n in iso],
                        cause="They connect to the rest of the circuit only through capacitors or current sources.",
                        effect="Their DC voltage is undefined (set by leakage in a real circuit).",
                        fix="Add a DC bias/bleed resistor to ground if a defined DC level is required.",
                        points=[p for n in iso for p in _npoints(model, n)], rule="dcpath"))
    # voltage-source / inductor loops
    parent: dict = {}

    def find(x):
        while parent.get(x, x) != x:
            x = parent.get(x, x)
        return x

    loop_seen = set()
    for c in model.components:
        if c.kind in ("V", "L") and len(c.nodes) == 2 and None not in c.nodes and c.nodes[0] != c.nodes[1]:
            a, b = find(c.nodes[0]), find(c.nodes[1])
            if a == b and any(x.kind == "V" for x in model.components if x.kind == "V"):
                if c.id not in loop_seen:
                    loop_seen.add(c.id)
                    add(Finding("problem", f"Loop of voltage sources/inductors closed by {c.id}", [("component", c.id)],
                                cause="Ideal voltage sources (and inductors at DC) form a closed loop with no resistance.",
                                effect="Conflicting or infinite currents; the circuit has no valid solution.",
                                fix="Remove the parallel source or add the real series resistance.",
                                points=_cpoints(model, c.id), rule="loop"))
            parent[a] = b
    return F


def operating_checks(model: DrawingModel, dc) -> list[Finding]:
    """Checks that need the DC operating point (ratings, polarity, power flow)."""
    F: list[Finding] = []
    for c in model.components:
        if not c.supported or None in c.nodes:
            continue
        a, b = c.nodes
        va, vb = dc.v.get(a, math.nan), dc.v.get(b, math.nan)
        if math.isnan(va) or math.isnan(vb):
            continue
        v = va - vb
        i = dc.i.get(c.id, 0.0)
        p = v * i if c.kind not in "VI" else -v * i if c.kind == "V" else v * i
        pts = _cpoints(model, c.id)
        if c.kind == "R" and c.rating_power:
            if p > c.rating_power:
                F.append(Finding("problem", f"{c.id} dissipates {p:.3g} W, above its {c.rating_power:g} W rating",
                                 [("component", c.id)], cause=f"I = {i:.4g} A through {c.value:g} Ω.",
                                 effect="The resistor will overheat and may fail or drift.",
                                 fix=f"Use a resistor rated ≥ {2 * p:.2g} W (2× margin) or reduce the current.", points=pts,
                                 rule="power"))
            elif p > 0.5 * c.rating_power:
                F.append(Finding("concern", f"{c.id} runs at {100 * p / c.rating_power:.0f} % of its power rating",
                                 [("component", c.id)], cause=f"{p:.3g} W of {c.rating_power:g} W.",
                                 effect="Little margin; hot running shortens life.",
                                 fix="Common practice is to stay below 50 % of the rating.", points=pts, rule="power"))
        if c.kind == "C" and c.rating_voltage and abs(v) > c.rating_voltage:
            F.append(Finding("problem", f"{c.id} sees {abs(v):.3g} V DC, above its {c.rating_voltage:g} V rating",
                             [("component", c.id)], cause="The DC voltage across it exceeds the rating.",
                             effect="Dielectric breakdown / failure.", fix="Use a higher voltage rating.", points=pts, rule="rating"))
        if c.kind == "C" and c.polarized and v < -0.05:
            F.append(Finding("problem", f"{c.id} (polarised) is reverse biased by {abs(v):.3g} V", [("component", c.id)],
                             cause="Its '+' terminal is at a lower DC voltage than its '−' terminal.",
                             effect="Electrolytic capacitors leak, heat and can rupture when reverse biased.",
                             fix="Turn the capacitor around (or check which terminal is marked '+').", points=pts,
                             rule="polarity"))
        if c.kind in ("R", "L") and c.rating_current and abs(i) > c.rating_current:
            F.append(Finding("problem", f"{c.id} carries {abs(i):.3g} A, above its {c.rating_current:g} A rating",
                             [("component", c.id)], cause="Current exceeds the rating.", effect="Overheating / saturation.",
                             fix="Use a part with a higher current rating or limit the current.", points=pts, rule="rating"))
        if c.kind == "V":
            delivered = v * (-i)  # branch current flows + -> - through the source; delivering means it flows out of +
            if c.rating_current and abs(i) > c.rating_current:
                F.append(Finding("problem", f"{c.id} must supply {abs(i):.3g} A, above its {c.rating_current:g} A rating",
                                 [("component", c.id)], cause="Load current exceeds the source rating.",
                                 effect="The source will current-limit, sag or shut down.",
                                 fix="Increase the load resistance or use a stronger source.", points=pts, rule="rating"))
            if delivered < -1e-12 and sum(1 for x in model.components if x.kind in "VI") > 1:
                F.append(Finding("concern", f"{c.id} absorbs {abs(delivered):.3g} W (current flows into its + terminal)",
                                 [("component", c.id)], cause="Another source drives current backwards through it.",
                                 effect="A battery would be charged; a power supply may not tolerate reverse current.",
                                 fix="Check the source polarities on the drawing.", points=pts, rule="polarity"))
    return F
