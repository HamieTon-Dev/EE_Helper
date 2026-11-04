"""Build the engineering report (self-contained HTML + editable Word .docx)."""
from __future__ import annotations

import base64
import datetime as _dt
import html
import os
from dataclasses import dataclass, field
from pathlib import Path

from mscopilot import __version__
from mscopilot.engines.results import AnalysisResult
from mscopilot.plan import Plan

INSTRUMENT_LABEL = {"multimeter": "Multimeter", "bode": "Bode plotter", "oscilloscope": "Oscilloscope", "table": "DC sweep"}


@dataclass
class RunRecord:
    """Everything a report needs about one design iteration."""

    iteration: int
    plan: Plan
    results: list[AnalysisResult]
    review: dict | None = None
    multisim_screenshots: list[Path] = field(default_factory=list)
    multisim_notes: list[str] = field(default_factory=list)
    folder: Path | None = None


def _img_tag(path: Path, alt: str) -> str:
    data = base64.b64encode(Path(path).read_bytes()).decode("ascii")
    return f'<img src="data:image/png;base64,{data}" alt="{html.escape(alt)}" loading="lazy">'


def _md(text: str) -> str:
    try:
        import markdown

        return markdown.markdown(text, extensions=["tables", "fenced_code"])
    except Exception:
        return "<pre>" + html.escape(text) + "</pre>"


def _fmt(v, unit=""):
    if v is None:
        return "—"
    from matplotlib.ticker import EngFormatter

    if unit.lower() in ("db", "dbv", "dbm", "°", "deg", "%"):
        return f"{v:.4g} {unit}"
    try:
        return EngFormatter(unit=unit, places=4)(v) if unit else f"{v:.6g}"
    except Exception:
        return f"{v:.6g} {unit}"


def overall_status(results: list[AnalysisResult]) -> str:
    statuses = [m.status for r in results for m in r.measurements]
    if any(r.error for r in results) or "error" in statuses:
        return "error"
    if "fail" in statuses:
        return "fail"
    if "pass" in statuses:
        return "pass"
    return "info"


CSS = """
:root{--bg:#f8fafc;--card:#fff;--ink:#0f172a;--muted:#64748b;--line:#e2e8f0;--accent:#2563eb;
--pass:#15803d;--pass-bg:#dcfce7;--fail:#b91c1c;--fail-bg:#fee2e2;--info:#334155;--info-bg:#f1f5f9;--err:#92400e;--err-bg:#fef3c7}
@media (prefers-color-scheme:dark){:root{--bg:#0b1120;--card:#111827;--ink:#e5e7eb;--muted:#94a3b8;--line:#1f2937;
--pass-bg:#052e16;--pass:#86efac;--fail-bg:#450a0a;--fail:#fca5a5;--info-bg:#1e293b;--info:#cbd5e1;--err-bg:#422006;--err:#fcd34d}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 "Segoe UI",system-ui,sans-serif}
main{max-width:1100px;margin:0 auto;padding:28px 16px 64px}
h1{font-size:28px;margin:0 0 4px}h2{font-size:21px;margin:36px 0 12px;border-bottom:1px solid var(--line);padding-bottom:6px}
h3{font-size:17px;margin:22px 0 8px}.muted{color:var(--muted)}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px 18px;margin:12px 0}
.badge{display:inline-block;padding:2px 10px;border-radius:999px;font-weight:600;font-size:13px}
.pass{background:var(--pass-bg);color:var(--pass)}.fail{background:var(--fail-bg);color:var(--fail)}
.info,.review{background:var(--info-bg);color:var(--info)}.error{background:var(--err-bg);color:var(--err)}
table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line)}
td.num{font-variant-numeric:tabular-nums;font-family:Consolas,monospace}
img{max-width:100%;height:auto;border:1px solid var(--line);border-radius:8px;background:#fff;margin:6px 0}
pre{background:var(--info-bg);padding:12px;border-radius:8px;overflow:auto;font-size:13px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px}
.kpi{border:1px solid var(--line);border-radius:8px;padding:10px 12px}.kpi b{display:block;font-size:20px}
a{color:var(--accent)}nav a{margin-right:14px}
"""


def build_html(records: list[RunRecord], out_dir: Path, files: list[Path], inputs_desc: str) -> Path:
    final = records[-1]
    status = (final.review or {}).get("verdict") or overall_status(final.results)
    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    p = final.plan
    h: list[str] = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>",
        f"<title>{html.escape(p.title)} — Simulation report</title><style>{CSS}</style></head><body><main>",
        f"<h1>{html.escape(p.title)}</h1>",
        f"<p class='muted'>Simulation report · generated {now} · Multisim Copilot {__version__} · inputs: {html.escape(inputs_desc)}</p>",
        f"<p><span class='badge {status}'>Overall: {status.upper()}</span> &nbsp; design iterations: {len(records)}</p>",
        "<nav><a href='#summary'>Summary</a><a href='#review'>Engineering review</a><a href='#results'>Results</a>"
        "<a href='#multisim'>Multisim</a><a href='#netlist'>Netlist</a><a href='#files'>Files</a></nav>",
        "<h2 id='summary'>Summary</h2>",
        f"<div class='card'>{_md(p.summary)}</div>",
    ]
    # KPI tiles: each measurement in the final iteration
    tiles = [(r, m) for r in final.results for m in r.measurements]
    if tiles:
        h.append("<div class='grid'>")
        for r, m in tiles:
            h.append(f"<div class='kpi'><span class='muted'>{html.escape(m.name)}</span><b>{_fmt(m.value, m.unit)}</b>"
                     f"<span class='badge {m.status}'>{m.status}</span></div>")
        h.append("</div>")
    if p.assumptions:
        h.append("<h3>Assumptions made while modelling</h3><ul>" + "".join(f"<li>{html.escape(a)}</li>" for a in p.assumptions) + "</ul>")
    if p.design_notes:
        h.append("<h3>Design notes</h3><ul>" + "".join(f"<li>{html.escape(a)}</li>" for a in p.design_notes) + "</ul>")

    h.append("<h2 id='review'>Engineering review</h2>")
    if final.review:
        h.append(f"<div class='card'>{_md(final.review.get('narrative_markdown', ''))}</div>")
        recs = final.review.get("recommendations") or []
        if recs:
            h.append("<h3>Recommendations</h3><ol>" + "".join(f"<li>{html.escape(x)}</li>" for x in recs) + "</ol>")
    else:
        h.append("<p class='muted'>No AI review for this run.</p>")

    h.append("<h2 id='results'>Simulation results</h2>")
    for r in final.results:
        a = r.analysis
        h.append(f"<h3>{html.escape(a.title)} <span class='muted'>· {INSTRUMENT_LABEL.get(a.instrument, a.instrument)} · .{a.kind}</span></h3>")
        if r.error:
            h.append(f"<div class='card'><span class='badge error'>error</span> {html.escape(r.error)}</div>")
            continue
        for pth in r.plots:
            h.append(_img_tag(pth, a.title))
        if a.kind == "op":
            h.append("<table><tr><th>Probe</th><th>Reading</th></tr>")
            for k, v in r.op_values().items():
                unit = "A" if k.lower().startswith("i(") or k.endswith("#branch") else "V"
                h.append(f"<tr><td>{html.escape(k)}</td><td class='num'>{_fmt(v, unit)}</td></tr>")
            h.append("</table>")
        if r.measurements:
            h.append("<table><tr><th>Check</th><th>Signal</th><th>Measured</th><th>Target</th><th>Status</th><th>Note</th></tr>")
            for m in r.measurements:
                tgt = "—" if m.expected is None else f"{_fmt(m.expected, m.unit)} ± {m.tolerance_pct if m.tolerance_pct is not None else 5:g}%"
                h.append(f"<tr><td>{html.escape(m.name)}</td><td>{html.escape(m.signal)}</td><td class='num'>{_fmt(m.value, m.unit)}</td>"
                         f"<td class='num'>{tgt}</td><td><span class='badge {m.status}'>{m.status}</span></td><td>{html.escape(m.note)}</td></tr>")
            h.append("</table>")
        if r.csv_path:
            h.append(f"<p class='muted'>Raw data: <a href='{_rel(r.csv_path, out_dir)}'>{r.csv_path.name}</a></p>")

    h.append("<h2 id='multisim'>Multisim</h2>")
    if final.multisim_notes:
        h.append("<ul>" + "".join(f"<li>{html.escape(n)}</li>" for n in final.multisim_notes) + "</ul>")
    if final.multisim_screenshots:
        for s in final.multisim_screenshots:
            h.append(f"<p class='muted'>{html.escape(s.stem)}</p>" + _img_tag(s, s.stem))
    else:
        h.append("<p class='muted'>No Multisim screenshots were captured in this run (see notes above).</p>")

    h.append("<h2 id='netlist'>Netlist</h2><pre>" + html.escape(p.netlist) + "</pre>")

    if len(records) > 1:
        h.append("<h2>Design iterations</h2>")
        for rec in records:
            st = (rec.review or {}).get("verdict") or overall_status(rec.results)
            h.append(f"<h3>Iteration {rec.iteration} <span class='badge {st}'>{st}</span></h3>")
            if rec.plan.design_notes:
                h.append("<ul>" + "".join(f"<li>{html.escape(n)}</li>" for n in rec.plan.design_notes) + "</ul>")
            h.append("<details><summary>Netlist</summary><pre>" + html.escape(rec.plan.netlist) + "</pre></details>")

    h.append("<h2 id='files'>Files</h2><ul>")
    for f in files:
        h.append(f"<li><a href='{_rel(f, out_dir)}'>{html.escape(_rel(f, out_dir))}</a></li>")
    h.append("</ul><p class='muted'>Open the .ms14 (or import the .cir via File &gt; Open in Multisim) to review and modify the circuit.</p>")
    h.append("</main></body></html>")
    path = out_dir / "report.html"
    path.write_text("\n".join(h), encoding="utf-8")
    return path


def _rel(p: Path, base: Path) -> str:
    try:
        return Path(os.path.relpath(p, base)).as_posix()
    except ValueError:
        return Path(p).as_posix()


def build_docx(records: list[RunRecord], out_dir: Path, files: list[Path]) -> Path:
    import docx
    from docx.shared import Inches, Pt

    final = records[-1]
    p = final.plan
    d = docx.Document()
    d.styles["Normal"].font.name = "Calibri"
    d.styles["Normal"].font.size = Pt(10.5)
    d.add_heading(p.title, 0)
    status = (final.review or {}).get("verdict") or overall_status(final.results)
    d.add_paragraph(f"Simulation report · {_dt.date.today().isoformat()} · Overall: {status.upper()} · iterations: {len(records)}")
    d.add_heading("Summary", 1)
    d.add_paragraph(p.summary)
    for title, items in (("Assumptions", p.assumptions), ("Design notes", p.design_notes)):
        if items:
            d.add_heading(title, 2)
            for it in items:
                d.add_paragraph(it, style="List Bullet")
    if final.review:
        d.add_heading("Engineering review", 1)
        for block in final.review.get("narrative_markdown", "").split("\n"):
            s = block.strip()
            if not s:
                continue
            if s.startswith("#"):
                d.add_heading(s.lstrip("#").strip(), min(3, s.count("#", 0, s.find(" ") if " " in s else len(s))) + 1)
            elif s[:2] in ("- ", "* "):
                d.add_paragraph(s[2:], style="List Bullet")
            else:
                d.add_paragraph(s)
        recs = final.review.get("recommendations") or []
        if recs:
            d.add_heading("Recommendations", 2)
            for x in recs:
                d.add_paragraph(x, style="List Number")
    d.add_heading("Simulation results", 1)
    for r in final.results:
        a = r.analysis
        d.add_heading(f"{a.title} ({INSTRUMENT_LABEL.get(a.instrument, a.instrument)})", 2)
        if r.error:
            d.add_paragraph(f"Error: {r.error}")
            continue
        for pth in r.plots:
            d.add_picture(str(pth), width=Inches(6.3))
        if r.measurements:
            t = d.add_table(rows=1, cols=5)
            t.style = "Light Grid Accent 1"
            for c, txt in zip(t.rows[0].cells, ("Check", "Signal", "Measured", "Target", "Status")):
                c.text = txt
            for m in r.measurements:
                row = t.add_row().cells
                row[0].text, row[1].text, row[2].text = m.name, m.signal, _fmt(m.value, m.unit)
                row[3].text = "—" if m.expected is None else f"{_fmt(m.expected, m.unit)} ± {m.tolerance_pct if m.tolerance_pct is not None else 5:g}%"
                row[4].text = m.status
    d.add_heading("Multisim", 1)
    for n in final.multisim_notes:
        d.add_paragraph(n, style="List Bullet")
    for s in final.multisim_screenshots:
        d.add_paragraph(s.stem)
        d.add_picture(str(s), width=Inches(6.3))
    d.add_heading("Netlist", 1)
    para = d.add_paragraph()
    run = para.add_run(p.netlist)
    run.font.name = "Consolas"
    run.font.size = Pt(9)
    d.add_heading("Files", 1)
    for f in files:
        d.add_paragraph(_rel(f, out_dir), style="List Bullet")
    path = out_dir / "report.docx"
    d.save(str(path))
    return path
