"""Run a plan with ngspice (SPICE engine compatible with Multisim's XSPICE core)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np

from eesimlab.engines.netlist import data_paths, multisim_deck, ngspice_deck
from eesimlab.engines.results import AnalysisResult, compute_measurement, parse_wrdata, write_csv
from eesimlab.plan import Plan


class SimulationError(RuntimeError):
    pass


def deck_name(name: str, variant: str | None) -> str:
    return name if not variant else f"{name}_{variant}"


def run_plan(plan: Plan, ngspice: str, workdir: Path, name: str = "circuit", timeout: float = 600, log=print) -> list[AnalysisResult]:
    """Simulate every analysis. One ngspice run per circuit variant.

    ngspice runs with cwd=workdir and is given *relative* data paths: its wrdata command
    cannot handle spaces in paths (e.g. C:\\Users\\John Smith\\Documents), and plan ids are
    restricted to [A-Za-z0-9_-] so the relative names never contain one.
    """
    workdir = Path(workdir)
    data_dir = workdir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    variants: list[str | None] = [None] + [v.id for v in plan.variants]
    outputs: dict[str | None, str] = {}
    for variant in variants:
        group = [a for a in plan.analyses if (a.variant or None) == variant]
        dname = deck_name(name, variant)
        (workdir / f"{dname}.cir").write_text(multisim_deck(plan, variant), encoding="utf-8")
        if not group:
            continue
        for a in group:
            for rel in data_paths(a):
                (workdir / rel).unlink(missing_ok=True)
        deck_path = workdir / f"{dname}_ngspice.cir"
        deck_path.write_text(ngspice_deck(plan, group, variant), encoding="utf-8")
        log(f"Running ngspice on {deck_path.name} ...")
        outputs[variant] = _run(ngspice, deck_path, workdir, timeout)
        (workdir / f"{dname}_ngspice.log").write_text(outputs[variant], encoding="utf-8")

    results: list[AnalysisResult] = []
    for a in plan.analyses:
        r = AnalysisResult(analysis=a)
        out = outputs.get(a.variant or None, "")
        files = [workdir / rel for rel in data_paths(a)]
        if not all(f.exists() and f.stat().st_size > 0 for f in files):
            r.error = "No data produced. " + _error_excerpt(out, a.id)
            results.append(r)
            continue
        try:
            if a.kind == "ac_points":
                _combine_points(r, files)
            else:
                r.scale_name, r.scale, r.data = parse_wrdata(files[0], a.kind)
            write_csv(r, data_dir / f"{a.id}.csv")
            r.measurements = [compute_measurement(r, m) for m in a.measurements]
        except Exception as e:
            r.error = f"Could not read results: {e}"
        results.append(r)
    if all(r.error for r in results):
        raise SimulationError("ngspice produced no results. Log excerpt:\n" + _error_excerpt("\n".join(outputs.values()), None))
    return results


def _combine_points(r: AnalysisResult, files: list[Path]) -> None:
    freqs, cols = [], {}
    for f in files:
        name, scale, data = parse_wrdata(f, "ac")
        freqs.append(float(scale[0]))
        for k, v in data.items():
            cols.setdefault(k, []).append(complex(v[0]))
    r.scale_name = "frequency"
    r.scale = np.array(freqs)
    r.data = {k: np.array(v, dtype=complex) for k, v in cols.items()}


def _run(ngspice: str, deck_path: Path, workdir: Path, timeout: float) -> str:
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        proc = subprocess.run(
            [ngspice, "-b", str(deck_path.resolve())],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=flags,
        )
    except FileNotFoundError as e:
        raise SimulationError(f"ngspice not found at {ngspice}") from e
    except subprocess.TimeoutExpired as e:
        raise SimulationError(f"ngspice did not finish within {timeout:.0f}s") from e
    return (proc.stdout or "") + "\n" + (proc.stderr or "")


def _error_excerpt(output: str, analysis_id: str | None) -> str:
    lines = output.splitlines()
    if analysis_id:
        try:
            start = next(i for i, ln in enumerate(lines) if ln.strip() == f"EESIMLAB_BEGIN {analysis_id}")
            end = next((i for i, ln in enumerate(lines[start:], start) if ln.strip() == f"EESIMLAB_END {analysis_id}"), len(lines))
            seg = [ln for ln in lines[start + 1 : end] if ln.strip()]
            if seg:
                return " | ".join(seg[-8:])
        except StopIteration:
            pass
    errs = [ln for ln in lines if "error" in ln.lower() or "warning" in ln.lower()]
    return "\n".join((errs or lines)[-15:])
