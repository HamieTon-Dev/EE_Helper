"""Turn a recognition result (AI JSON on a 0-1000 grid of the sent image) into an editable
DrawingModel in original-image pixel coordinates. Nothing missing is filled in: unknown
values stay None, unconnected pins stay unconnected, ambiguous junctions stay ambiguous."""
from __future__ import annotations

import re

from mscopilot.drawing.model import (
    SUPPORTED_KINDS, UNSUPPORTED_KINDS, Component, Dimension, DrawingModel, Junction, Node, SourceParams, Unreadable, Wire,
    parse_value,
)

KIND_ALIASES = {"RES": "R", "RESISTOR": "R", "CAP": "C", "CAPACITOR": "C", "IND": "L", "INDUCTOR": "L", "COIL": "L",
                "VS": "V", "BATTERY": "V", "BAT": "V", "VDC": "V", "VAC": "V", "SOURCE": "V", "IS": "I", "DIODE": "D",
                "LED": "LED", "BJT": "Q", "NPN": "Q", "PNP": "Q", "FET": "M", "MOSFET": "M", "OPAMP": "U", "IC": "U",
                "SWITCH": "SW", "S": "SW", "FUSE": "F", "TRANSFORMER": "T", "XFMR": "T", "LAMP": "LAMP", "MOTOR": "MOTOR"}


def _kind(k: str) -> str:
    k = (k or "X").strip().upper()
    k = KIND_ALIASES.get(k, k)
    return k if k in SUPPORTED_KINDS or k in UNSUPPORTED_KINDS else "X"


def build_model(rec: dict, to_image) -> DrawingModel:
    """to_image(x1000, y1000) -> (x, y) in original image pixels."""

    def pt(p):
        if not p or len(p) < 2:
            return None
        x, y = to_image(float(p[0]), float(p[1]))
        return [round(x, 1), round(y, 1)]

    m = DrawingModel(drawing_type=rec.get("drawing_type", "unknown"),
                     drawing_type_confidence=float(rec.get("drawing_type_confidence", 0) or 0),
                     title=rec.get("title", ""))
    m.recognition_warnings = list(rec.get("warnings", []))
    used_ids = set()
    pin_ref: dict[str, tuple[Component, int]] = {}
    for raw in rec.get("components", []):
        kind = _kind(raw.get("kind"))
        cid = re.sub(r"[^A-Za-z0-9_]", "", raw.get("id") or "") or kind
        base, n = cid, 2
        while cid in used_ids:
            cid = f"{base}_{n}"
            n += 1
        used_ids.add(cid)
        pins = [pt(p) for p in raw.get("pins", [])]
        npins = max(2, len(pins)) if kind not in SUPPORTED_KINDS else 2
        pins = (pins + [None] * npins)[:npins]
        bb = raw.get("bbox") or []
        bbox = None
        if len(bb) == 4:
            a, b = pt(bb[:2]), pt(bb[2:])
            bbox = [min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])]
        c = Component(cid, kind, label=raw.get("label", "") or cid, value_text=raw.get("value_text", "") or "",
                      pins=pins, nodes=[None] * npins, bbox=bbox, confidence=float(raw.get("kind_confidence", 0.5) or 0),
                      value_confidence=float(raw.get("value_confidence", 0.5) or 0), source="ai",
                      notes=raw.get("notes", "") or "")
        if kind in SUPPORTED_KINDS:
            val, problem = parse_value(c.value_text, kind)
            if kind in "RLC":
                c.value = val
                if problem and c.value_text:
                    c.notes = (c.notes + f" Value not understood: {problem}.").strip()
            else:
                c.src = SourceParams("dc", dc=val) if val is not None else SourceParams("dc", dc=None)
                if re.search(r"(?i)ac|~|sin|hz|rms", c.value_text + " " + c.notes):
                    c.src = SourceParams("sine", amp=None, freq=None)  # AC: amplitude/frequency must be confirmed
                    c.notes = (c.notes + " AC source: confirm amplitude (peak) and frequency.").strip()
        pol = (raw.get("polarity") or "").lower()
        if kind == "C" and pol in ("pin1", "pin2"):
            c.polarized = True
        if kind in ("V", "C") and pol == "pin2":  # model convention: nodes[0] is '+'
            c.pins = [c.pins[1], c.pins[0]] + c.pins[2:]
            c.notes = (c.notes + " (pins reordered so pin 1 is '+')").strip()
            swap = True
        else:
            swap = False
        m.components.append(c)
        for i in range(npins):
            j = i
            if swap and i < 2:
                j = 1 - i
            pin_ref[f"{raw.get('id')}.{i + 1}"] = (c, j)
            pin_ref[f"{cid}.{i + 1}"] = (c, j)
    for k, net in enumerate(rec.get("nets", [])):
        nid = f"N{k + 1}"
        node = Node(nid, name=net.get("name", "") or "", points=[p for p in (pt(x) for x in net.get("points", [])) if p],
                    ground=bool(net.get("is_ground")), confidence=float(net.get("confidence", 0.5) or 0))
        m.nodes.append(node)
        pts = node.points
        if len(pts) >= 2:
            m.wires.append(Wire(nid, pts))
        for ref in net.get("pins", []):
            hit = pin_ref.get(ref)
            if not hit:
                m.recognition_warnings.append(f"Net {node.name or nid} refers to unknown terminal '{ref}'.")
                continue
            comp, idx = hit
            if comp.nodes[idx] not in (None, nid):
                m.recognition_warnings.append(f"Terminal {ref} was listed in two nets; it was kept in the first and the "
                                              "conflict needs checking.")
                continue
            comp.nodes[idx] = nid
            if comp.pins[idx]:
                node.points.append(comp.pins[idx])
    name_to_node = {n.name: n.id for n in m.nodes if n.name}
    for k, j in enumerate(rec.get("junctions", [])):
        p = pt([j.get("x"), j.get("y")])
        nets = [name_to_node.get(x, x) for x in j.get("nets", [])]
        m.junctions.append(Junction(f"J{k + 1}", p[0], p[1], j.get("kind", "ambiguous"), float(j.get("confidence", 0.5) or 0),
                                    [n for n in nets if n in m.node_ids()], j.get("note", "")))
    for u in rec.get("unreadable", []):
        bb = u.get("bbox") or [0, 0, 0, 0]
        if len(bb) == 4:
            a, b = pt(bb[:2]), pt(bb[2:])
            m.unreadable.append(Unreadable(min(a[0], b[0]), min(a[1], b[1]), abs(b[0] - a[0]), abs(b[1] - a[1]),
                                           u.get("text_seen", ""), u.get("note", "")))
    for k, d in enumerate(rec.get("dimensions", [])):
        try:
            val = float(re.sub(r"[^0-9.\-eE]", "", d.get("value_text", "")))
        except ValueError:
            val = None
        m.dimensions.append(Dimension(f"D{k + 1}", d.get("text", ""), val, d.get("unit", ""), pt(d.get("p1")), pt(d.get("p2")),
                                      float(d.get("confidence", 0.5) or 0)))
    m.texts = [{"text": t.get("text", ""), "x": pt([t.get("x"), t.get("y")])[0], "y": pt([t.get("x"), t.get("y")])[1]}
               for t in rec.get("texts", [])]
    # terminals the AI did not place in any net get their own (dangling) node so they are visible and flagged
    for c in m.components:
        for i, n in enumerate(c.nodes):
            if n is None and c.pins[i] is not None:
                node = m.add_node(c.pins[i])
                node.confidence = 0.0
                c.nodes[i] = node.id
                m.recognition_warnings.append(f"{c.id} terminal {i + 1} was not connected to any wire in the reading.")
    m.confirmed = False
    return m
