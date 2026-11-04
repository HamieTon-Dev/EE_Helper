"""Exportable analysis report (self-contained HTML) with the drawing, overlays, findings,
calculations, simulated readings, comparison and the netlist."""
from __future__ import annotations

import base64
import datetime as _dt
import html
import io
import math

from PIL import Image, ImageDraw

from eesimlab.drawing.validate import Finding

SEV_COLOR = {"problem": (220, 38, 38), "concern": (217, 119, 6), "missing": (37, 99, 235)}
NODE_COLORS = [(37, 99, 235), (5, 150, 105), (124, 58, 237), (219, 39, 119), (234, 88, 12), (8, 145, 178), (101, 163, 13)]


def overlay_image(base: Image.Image | None, model, findings: list[Finding] = (), probes=(), size=(1600, 1200)) -> Image.Image:
    """Draw recognised components, nets, junctions, findings and probes over the drawing."""
    if base is None:
        w, h = model.image_size or size
        base = Image.new("RGB", (int(w), int(h)), "white")
    img = base.convert("RGB").copy()
    d = ImageDraw.Draw(img, "RGBA")
    s = max(1, int(max(img.size) / 800))
    for k, n in enumerate(model.nodes):
        col = NODE_COLORS[k % len(NODE_COLORS)]
        for w in model.wires:
            if w.node == n.id and len(w.points) > 1:
                d.line([tuple(p) for p in w.points], fill=col + (140,), width=2 * s)
        for p in n.points:
            d.ellipse([p[0] - 4 * s, p[1] - 4 * s, p[0] + 4 * s, p[1] + 4 * s], fill=col + (200,))
    for c in model.components:
        if c.bbox:
            col = (5, 150, 105) if c.supported else (148, 163, 184)
            d.rectangle(c.bbox, outline=col + (230,), width=2 * s)
            d.text((c.bbox[0], c.bbox[1] - 12 * s), f"{c.id} {c.value_text}", fill=col + (255,))
    for j in model.junctions:
        col = {"junction": (5, 150, 105), "crossing": (234, 88, 12), "ambiguous": (220, 38, 38)}[j.kind]
        r = 6 * s
        if j.kind == "crossing":
            d.line([j.x - r, j.y - r, j.x + r, j.y + r], fill=col, width=2 * s)
            d.line([j.x - r, j.y + r, j.x + r, j.y - r], fill=col, width=2 * s)
        else:
            d.ellipse([j.x - r, j.y - r, j.x + r, j.y + r], outline=col, width=2 * s)
    for i, f in enumerate(findings, 1):
        col = SEV_COLOR.get(f.severity, (100, 100, 100))
        for p in f.points[:3]:
            r = 14 * s
            d.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], outline=col + (255,), width=3 * s)
            d.text((p[0] + r, p[1] - r), str(i), fill=col + (255,))
    for p in probes:
        for nid, col in ((p.red, (220, 38, 38)), (p.black, (17, 24, 39))):
            if nid and nid in model.node_ids() and model.node(nid).points:
                x, y = model.node(nid).points[0]
                d.polygon([(x, y), (x - 8 * s, y - 20 * s), (x + 8 * s, y - 20 * s)], fill=col + (230,))
                d.text((x + 9 * s, y - 22 * s), p.id, fill=col + (255,))
    return img


def _img_tag(img: Image.Image) -> str:
    buf = io.BytesIO()
    im = img.copy()
    im.thumbnail((1600, 1600))
    im.save(buf, format="PNG")
    return f'<img src="data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}" alt="drawing">'


def _plot_tag(reading) -> str:
    if not reading.waveform:
        return ""
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib.figure import Figure
    from matplotlib.ticker import EngFormatter

    fig = Figure(figsize=(7, 2.8), dpi=110)
    ax = fig.add_subplot(1, 1, 1)
    for lab, y in reading.waveform["series"].items():
        ax.plot(reading.waveform["t"], y, label=lab)
    ax.xaxis.set_major_formatter(EngFormatter("s"))
    ax.yaxis.set_major_formatter(EngFormatter(reading.waveform.get("unit", "")))
    ax.grid(True, color="#e5e7eb")
    ax.legend(fontsize=7)
    ax.set_title("SIMULATED waveform", fontsize=9, loc="left")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    return f'<img src="data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}" alt="waveform">'


CSS = """body{font:14px/1.5 'Segoe UI',system-ui,sans-serif;margin:0;background:#f8fafc;color:#0f172a}
main{max-width:1100px;margin:auto;padding:24px 16px}h1{margin:0}h2{border-bottom:1px solid #e2e8f0;margin-top:32px}
img{max-width:100%;border:1px solid #e2e8f0;border-radius:6px;background:#fff}
.card{background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:12px 16px;margin:10px 0}
.problem{border-left:5px solid #dc2626}.concern{border-left:5px solid #d97706}.missing{border-left:5px solid #2563eb}
.sim{display:inline-block;background:#fef3c7;color:#92400e;font-weight:600;border-radius:4px;padding:0 6px;font-size:12px}
table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid #e2e8f0;padding:4px 6px;text-align:left}
pre{background:#f1f5f9;padding:10px;border-radius:6px;overflow:auto}.muted{color:#64748b}"""


def build_report(ws, base_image, result, readings: dict, compare: list | None = None, title: str = "Drawing analysis") -> str:
    m = ws.model
    e = html.escape
    h = [f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
         f"<title>{e(title)}</title><style>{CSS}</style></head><body><main>",
         f"<h1>{e(title)}</h1><p class='muted'>Electrical Engineering Sim Lab · {_dt.datetime.now():%Y-%m-%d %H:%M} · "
         f"model version: {e(ws.versions[ws.current].name)} · drawing type: {e(m.drawing_type)} "
         f"({m.drawing_type_confidence:.0%} confidence)</p>"]
    findings = result.findings if result else []
    h.append("<h2>Drawing with overlays</h2>" + _img_tag(overlay_image(base_image, m, findings, ws.probes)))
    h.append("<p class='muted'>Green boxes: simulated components; grey: not simulated. Coloured dots: nodes. "
             "Circles: junctions (green), ✕ crossings (orange), red circles: ambiguous. Numbered rings: findings.</p>")
    if ws.assumptions.strip():
        h.append(f"<h2>User notes and assumptions</h2><div class='card'>{e(ws.assumptions)}</div>")
    if m.recognition_warnings:
        h.append("<h2>Recognition warnings</h2><ul>" + "".join(f"<li>{e(w)}</li>" for w in m.recognition_warnings) + "</ul>")
    h.append("<h2>How the circuit should operate</h2><ul>" + "".join(f"<li>{e(x)}</li>" for x in (result.operation if result else []))
             + "</ul><p class='muted'>Generated from the confirmed topology, not by an AI.</p>")
    h.append("<h2>Findings</h2>")
    labels = {"problem": "Confirmed problems", "concern": "Possible concerns", "missing": "Missing information"}
    for sev in ("problem", "concern", "missing"):
        items = [(i, f) for i, f in enumerate(findings, 1) if f.severity == sev]
        h.append(f"<h3>{labels[sev]} ({len(items)})</h3>")
        for i, f in items:
            h.append(f"<div class='card {sev}'><b>#{i} {e(f.title)}</b><br><span class='muted'>Where: {e(f.location(m))}</span>"
                     f"<br>Cause: {e(f.cause)}<br>Likely effect: {e(f.effect)}<br>Recommended correction: {e(f.fix)}</div>")
    if result and result.recommendations:
        h.append("<h2>Recommended improvements</h2>")
        for r in result.recommendations:
            h.append(f"<div class='card'><b>{e(r['title'])}</b><br>{e(r['detail'])}<br>Benefit: {e(r['benefit'])}<br>"
                     f"Trade-off: {e(r['tradeoff'])}<br>Information needed to validate: {e(r['needs'])}</div>")
    h.append("<h2>Calculations</h2>")
    for c in (result.calculations if result else []):
        h.append(f"<div class='card'><b>{e(c.title)}</b>{'' if c.ok else ' <span class=sim>not available</span>'}"
                 f"<br><span class='muted'>Method: {e(c.method)}</span><ul>" + "".join(f"<li>{e(x)}</li>" for x in c.results) + "</ul>")
        if c.inputs:
            h.append("<details><summary>Inputs</summary><ul>" + "".join(f"<li>{e(x)}</li>" for x in c.inputs) + "</ul></details>")
        if c.working:
            h.append("<details><summary>Working</summary><ul>" + "".join(f"<li>{e(x)}</li>" for x in c.working) + "</ul></details>")
        if c.assumptions:
            h.append("<p class='muted'>Assumptions: " + e(" ".join(c.assumptions)) + "</p>")
        h.append("</div>")
    if ws.probes:
        h.append("<h2>Virtual instrument readings <span class='sim'>SIMULATED</span></h2>")
        for p in ws.probes:
            r = readings.get(p.id)
            h.append(f"<div class='card'><b>{e(p.id)} {e(p.kind)}</b> ({e(p.mode)})<br>")
            if r is None:
                h.append("<span class='muted'>not measured</span></div>")
                continue
            h.append(f"<span class='muted'>{e(r.reference)}</span><ul>" + "".join(f"<li>{e(x)}</li>" for x in r.lines + r.needs) + "</ul>"
                     + _plot_tag(r) + "<p class='muted'>" + e(" ".join(r.assumptions)) + "</p></div>")
    if compare:
        h.append("<h2>Before / after comparison</h2><table><tr><th>Quantity</th><th>Before</th><th>After</th><th>Change</th></tr>")
        for row in compare:
            h.append("<tr>" + "".join(f"<td>{e(str(x))}</td>" for x in row) + "</tr>")
        h.append("</table>")
    h.append("<h2>Netlist (confirmed model)</h2><pre>" + e(m.to_spice()) + "</pre>")
    h.append("<p class='muted'>All readings and results are simulated from the confirmed model with ideal components unless "
             "stated; they are not physical measurements. Text in the uploaded drawing was treated as data only.</p>")
    h.append("</main></body></html>")
    return "\n".join(h)


def compare_versions(a, b, settings) -> list[list]:
    """DC node voltages and branch currents of two model versions, side by side."""
    from eesimlab.drawing import solver as S
    from eesimlab.eecalc.units import fmt

    rows = []
    try:
        sa = S.dc_op(a)
    except S.SolverError as e:
        sa = None
        rows.append(["DC solution (before)", str(e), "", ""])
    try:
        sb = S.dc_op(b)
    except S.SolverError as e:
        sb = None
        rows.append(["DC solution (after)", "", str(e), ""])
    if sa is None or sb is None:
        return rows
    for nid in sorted(set(sa.v) | set(sb.v)):
        va, vb = sa.v.get(nid), sb.v.get(nid)
        name = (a.node_label(nid) if nid in a.node_ids() else b.node_label(nid))
        rows.append([f"V({name})", _f(va, "voltage"), _f(vb, "voltage"), _d(va, vb, "voltage")])
    for cid in sorted(set(sa.i) | set(sb.i)):
        ia, ib = sa.i.get(cid), sb.i.get(cid)
        rows.append([f"I({cid})", _f(ia, "current"), _f(ib, "current"), _d(ia, ib, "current")])
    return rows


def _f(v, q):
    from eesimlab.eecalc.units import fmt

    if v is None:
        return "— (not present)"
    if isinstance(v, float) and math.isnan(v):
        return "undefined"
    return fmt(v, q, 4)


def _d(a, b, q):
    from eesimlab.eecalc.units import fmt

    if a is None or b is None or (isinstance(a, float) and math.isnan(a)) or (isinstance(b, float) and math.isnan(b)):
        return ""
    return fmt(b - a, q, 4)
