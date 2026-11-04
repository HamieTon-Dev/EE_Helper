"""Turn a Plan into SPICE decks.

Two decks are written per run:
- ``<name>.cir``: the plain circuit with SPICE dot-commands (.op/.ac/.tran). This is the
  file to open in Multisim (File > Open, file type "SPICE Netlist") - Multisim converts it
  into a schematic you can save as .ms14.
- ``<name>_ngspice.cir``: the same circuit with an ngspice ``.control`` block that writes
  every analysis to a data file the report generator reads.
"""
from __future__ import annotations

import re

from eesimlab.plan import Analysis, Plan

_END_RE = re.compile(r"^\s*\.end\s*$", re.I | re.M)
_ANALYSIS_RE = re.compile(r"^\s*\.(op|ac|tran|dc|noise|tf|four|disto|pz|sens)\b.*$", re.I | re.M)


def clean_netlist(netlist: str, title: str) -> str:
    """Strip analysis/.end lines (they are generated) and make sure a title line exists."""
    body = _END_RE.sub("", netlist)
    body = _ANALYSIS_RE.sub("", body)
    lines = [ln.rstrip() for ln in body.strip().splitlines()]
    if not lines or not lines[0].lstrip().startswith("*"):
        lines.insert(0, f"* {title}")
    return "\n".join(lines)


def analysis_card(a: Analysis) -> str:
    if a.kind == "op":
        return ".op"
    if a.kind == "ac":
        return f".ac dec {int(a.points_per_decade or 50)} {a.f_start:g} {a.f_stop:g}"
    if a.kind == "ac_points":
        f = a.frequencies[0]
        return f".ac lin 1 {f:g} {f:g}"
    if a.kind == "tran":
        start = f" {a.t_start:g}" if a.t_start else ""
        # max step = t_step keeps the waveform smooth enough for scope-style plots
        return f".tran {a.t_step:g} {a.t_stop:g}{start} {a.t_step:g}"
    if a.kind == "dc":
        return f".dc {a.dc_source} {a.dc_start:g} {a.dc_stop:g} {a.dc_step:g}"
    raise ValueError(a.kind)


def _vectors(a: Analysis) -> list[str]:
    vecs = [s.strip() for s in a.signals]
    extra = [a.reference_signal] + [m.signal for m in a.measurements] + [m.reference for m in a.measurements]
    for s in extra:
        s = (s or "").strip()
        if s and s.lower() not in (v.lower() for v in vecs):
            vecs.append(s)
    return vecs


def multisim_deck(plan: Plan, variant: str | None = None) -> str:
    """Circuit + analysis dot-commands, for importing into Multisim / any SPICE tool."""
    title = plan.title if not variant else f"{plan.title} - {plan.variant_title(variant)}"
    out = [clean_netlist(plan.netlist_for(variant), title), "", "* Analyses requested by the plan"]
    seen = set()
    for a in plan.analyses:
        if (a.variant or None) != (variant or None):
            continue
        card = analysis_card(a)
        if card not in seen:
            out.append(card)
            seen.add(card)
    out.append(".end")
    return "\n".join(out) + "\n"


def data_paths(a: Analysis) -> list[str]:
    """Relative data file(s) an analysis writes (one per frequency for ac_points)."""
    if a.kind == "ac_points":
        return [f"data/{a.id}__{k}.txt" for k in range(len(a.frequencies or []))]
    return [f"data/{a.id}.txt"]


def ngspice_deck(plan: Plan, analyses: list[Analysis], variant: str | None = None) -> str:
    """Circuit + .control block writing each analysis to its data file(s)."""
    out = [clean_netlist(plan.netlist_for(variant), plan.title), "", ".control", "set wr_singlescale", "set wr_vecnames", "set noaskquit"]
    for a in analyses:
        vecs = " ".join(_vectors(a))
        out.append(f"echo EESIMLAB_BEGIN {a.id}")
        if a.kind == "ac_points":
            for f, path in zip(a.frequencies, data_paths(a)):
                out.append(f"ac lin 1 {f:g} {f:g}")
                out.append(f"wrdata {_quote(path)} {vecs}")
                out.append("destroy all")
        else:
            out.append(analysis_card(a)[1:])  # same syntax without the dot inside .control
            out.append(f"wrdata {_quote(data_paths(a)[0])} {vecs}")
            out.append("destroy all")
        out.append(f"echo EESIMLAB_END {a.id}")
    out += ["quit", ".endc", ".end"]
    return "\n".join(out) + "\n"


def _quote(path: str) -> str:
    # ngspice accepts forward slashes on Windows. wrdata cannot take quoted paths, so the
    # caller passes relative paths without spaces.
    p = path.replace("\\", "/")
    if " " in p:
        raise ValueError(f"ngspice data path must not contain spaces: {p}")
    return p
