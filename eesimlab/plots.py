"""Instrument-style figures: Bode plotter, oscilloscope, multimeter, DC sweep table/plot."""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.ticker import EngFormatter  # noqa: E402

from eesimlab.engines.results import AnalysisResult, transfer  # noqa: E402

# Scope channel colours (classic bench-scope convention: CH1 yellow, CH2 cyan, CH3 magenta, CH4 green)
SCOPE_COLORS = ["#f5d90a", "#22d3ee", "#e879f9", "#4ade80", "#fb923c", "#60a5fa"]
LIGHT_SERIES = ["#2563eb", "#dc2626", "#059669", "#7c3aed", "#d97706", "#0891b2"]
DPI = 150


def _unit_for(signal: str) -> str:
    return "A" if signal.strip().lower().startswith("i(") else "V"


def plot_bode(r: AnalysisResult, path: Path) -> Path:
    f = r.scale
    fig, (ax_m, ax_p) = plt.subplots(2, 1, figsize=(9, 6.4), sharex=True, constrained_layout=True)
    ref = r.analysis.reference_signal
    for i, sig in enumerate(s for s in r.analysis.signals if not ref or s.strip().lower() != ref.strip().lower()):
        try:
            h = transfer(r, sig)
        except KeyError:
            continue
        mag = 20 * np.log10(np.maximum(np.abs(h), 1e-30))
        ph = np.degrees(np.unwrap(np.angle(h)))
        label = f"{sig} / {ref}" if ref else sig
        c = LIGHT_SERIES[i % len(LIGHT_SERIES)]
        ax_m.semilogx(f, mag, color=c, lw=2, label=label)
        ax_p.semilogx(f, ph, color=c, lw=2, label=label)
    for m in r.measurements:
        if m.type in ("f_peak", "f_min") and m.value:
            for ax in (ax_m, ax_p):
                ax.axvline(m.value, color="#dc2626", lw=1.2)
            ax_m.annotate(f"cursor: {EngFormatter('Hz', places=2)(m.value)}, {m.extra.get('gain_db', float('nan')):.3f} dB",
                          (m.value, ax_m.get_ylim()[1]), xytext=(6, -16), textcoords="offset points", fontsize=9,
                          color="#991b1b", bbox=dict(boxstyle="round", fc="white", ec="#fca5a5"))
        if m.type == "f_3db" and m.value:
            for ax in (ax_m, ax_p):
                ax.axvline(m.value, color="#6b7280", ls="--", lw=1)
            ax_m.annotate(f"-3 dB @ {EngFormatter('Hz')(m.value)}", (m.value, ax_m.get_ylim()[0]), xytext=(6, 10),
                          textcoords="offset points", fontsize=9, color="#374151")
    ax_m.set_ylabel("Magnitude (dB)")
    ax_p.set_ylabel("Phase (°)")
    ax_p.set_xlabel("Frequency")
    ax_p.xaxis.set_major_formatter(EngFormatter(unit="Hz"))
    for ax in (ax_m, ax_p):
        ax.grid(True, which="both", color="#e5e7eb", lw=0.8)
        ax.spines[["top", "right"]].set_visible(False)
    ax_m.legend(frameon=False, fontsize=9)
    ax_m.set_title(f"Bode plot — {r.analysis.title}", loc="left", fontsize=12, fontweight="bold")
    fig.savefig(path, dpi=DPI)
    plt.close(fig)
    return Path(path)


def _nice_ceil(x: float) -> float:
    """Smallest 1-2-5 step >= x (scope V/div and s/div settings)."""
    if x <= 0 or not np.isfinite(x):
        return 1.0
    exp = np.floor(np.log10(x))
    for m in (1, 2, 5, 10):
        if m * 10**exp >= x * (1 - 1e-9):
            return float(m * 10**exp)
    return float(10 ** (exp + 1))


def cursor_measurement(r: AnalysisResult):
    """Measurement whose T1/T2 cursors the scope capture shows (Δt beats period)."""
    ok = [m for m in r.measurements if m.extra.get("t1") is not None and m.extra.get("t2") is not None]
    for kind in ("delta_t", "phase_shift_deg", "period"):
        for m in ok:
            if m.type == kind:
                return m
    return None


def plot_scope(r: AnalysisResult, path: Path) -> Path:
    """Bench-scope style capture: per-channel V/div, 10x8 graticule, T1/T2 cursors and a
    readout panel (Time / Channel A / Channel B), like Multisim's XSC oscilloscope."""
    t = r.scale
    t0 = t[0]
    span = (t[-1] - t[0]) if len(t) > 1 else 1.0
    tdiv = span / 10
    chans = []
    for i, sig in enumerate(r.analysis.signals[:4]):
        try:
            y = np.real(r.signal(sig))
        except KeyError:
            continue
        vdiv = _nice_ceil(max(np.abs(y).max(), 1e-12) / 3.5)
        chans.append((chr(ord("A") + i), sig, y, vdiv, SCOPE_COLORS[i % len(SCOPE_COLORS)]))

    fig = plt.figure(figsize=(9.6, 7.4))
    fig.patch.set_facecolor("#e5e7eb")
    ax = fig.add_axes((0.04, 0.27, 0.92, 0.66))
    ax.set_facecolor("#000000")
    for name, sig, y, vdiv, c in chans:
        ax.plot((t - t0) / tdiv, y / vdiv, color=c, lw=1.5)
    ax.set_xlim(0, 10)
    ax.set_ylim(-4, 4)
    ax.set_xticks(np.arange(0, 11))
    ax.set_yticks(np.arange(-4, 5))
    ax.grid(True, color="#4b5563", lw=0.6, ls=(0, (2, 3)))
    ax.axhline(0, color="#9ca3af", lw=0.8)
    ax.axvline(5, color="#9ca3af", lw=0.8)
    ax.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)
    for sp in ax.spines.values():
        sp.set_color("#6b7280")

    cm = cursor_measurement(r)
    t1 = t2 = None
    if cm is not None:
        t1, t2 = cm.extra["t1"], cm.extra["t2"]
        ax.axvline((t1 - t0) / tdiv, color="#ef4444", lw=1.3)
        ax.axvline((t2 - t0) / tdiv, color="#3b82f6", lw=1.3)
        ax.text((t1 - t0) / tdiv, 4.05, "1", color="#ef4444", ha="center", va="bottom", fontsize=9, fontweight="bold", clip_on=False)
        ax.text((t2 - t0) / tdiv, 4.05, "2", color="#3b82f6", ha="center", va="bottom", fontsize=9, fontweight="bold", clip_on=False)
    fig.text(0.04, 0.955, f"Oscilloscope-XSC1  ·  {r.analysis.title}", fontsize=10, color="#111827")

    # readout panel
    eng_t = EngFormatter("s", places=3)
    def val(y, tt):
        return float(np.interp(tt, t, y))
    rows = [["", "Time"] + [f"Channel_{n}" for n, *_ in chans]]
    if t1 is not None:
        rows.append(["T1", eng_t(t1)] + [EngFormatter(_unit_for(s), places=3)(val(y, t1)) for _, s, y, _, _ in chans])
        rows.append(["T2", eng_t(t2)] + [EngFormatter(_unit_for(s), places=3)(val(y, t2)) for _, s, y, _, _ in chans])
        rows.append(["T2-T1", eng_t(t2 - t1)] + [EngFormatter(_unit_for(s), places=3)(val(y, t2) - val(y, t1)) for _, s, y, _, _ in chans])
    rax = fig.add_axes((0.04, 0.02, 0.92, 0.21))
    rax.set_axis_off()
    y0 = 0.92
    for row in rows:
        for j, cell in enumerate(row):
            rax.text(0.0 + j * 0.16, y0, cell, fontsize=9, family="monospace", color="#111827", va="top")
        y0 -= 0.2
    settings = [f"Timebase  Scale: {EngFormatter('s')(float(f'{tdiv:.3g}'))}/Div"]
    for n, sig, _, vdiv, c in chans:
        settings.append(f"Channel {n} ({sig})  Scale: {EngFormatter(_unit_for(sig))(vdiv)}/Div")
    for j, line in enumerate(settings):
        rax.text(0.66, 0.92 - j * 0.2, line, fontsize=8.5, color="#111827", va="top")
    for j, (n, sig, *_rest, c) in enumerate(chans):
        rax.add_patch(plt.Rectangle((0.645, 0.92 - (j + 1) * 0.2 - 0.12), 0.01, 0.1, color=c, transform=rax.transAxes))
    fig.savefig(path, dpi=DPI, facecolor=fig.get_facecolor())
    plt.close(fig)
    return Path(path)


def plot_multimeter(r: AnalysisResult, path: Path) -> Path:
    vals = list(r.op_values().items())
    n = max(len(vals), 1)
    cols = min(3, n)
    rows = (n + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(3.3 * cols, 1.9 * rows), squeeze=False)
    fig.patch.set_facecolor("white")
    eng_v, eng_a = EngFormatter("V", places=4), EngFormatter("A", places=4)
    for k, ax in enumerate(axes.flat):
        ax.set_axis_off()
        if k >= len(vals):
            continue
        name, v = vals[k]
        is_i = name.lower().startswith("i(") or name.lower().endswith("#branch")
        ax.add_patch(plt.Rectangle((0.03, 0.05), 0.94, 0.9, transform=ax.transAxes, facecolor="#f3c623", edgecolor="#4b5563", lw=1.5))
        ax.add_patch(plt.Rectangle((0.1, 0.32), 0.8, 0.42, transform=ax.transAxes, facecolor="#c8d5b9", edgecolor="#374151", lw=1))
        ax.text(0.5, 0.53, (eng_a if is_i else eng_v)(v), transform=ax.transAxes, ha="center", va="center",
                fontsize=15, family="monospace", fontweight="bold", color="#111827")
        mode = "AC" if r.analysis.kind == "ac_points" else "DC"
        ax.text(0.5, 0.82, f"{mode} {'A' if is_i else 'V'}", transform=ax.transAxes, ha="center", fontsize=8, color="#111827")
        ax.text(0.5, 0.15, name, transform=ax.transAxes, ha="center", fontsize=9, color="#111827")
    fig.suptitle(f"Multimeter readings — {r.analysis.title}", x=0.02, ha="left", fontsize=11, fontweight="bold")
    fig.tight_layout()
    fig.savefig(path, dpi=DPI)
    plt.close(fig)
    return Path(path)


def plot_sweep(r: AnalysisResult, path: Path) -> Path:
    x = r.scale
    fig, ax = plt.subplots(figsize=(9, 5), constrained_layout=True)
    for i, sig in enumerate(r.analysis.signals):
        try:
            y = np.real(r.signal(sig))
        except KeyError:
            continue
        ax.plot(x, y, lw=2, color=LIGHT_SERIES[i % len(LIGHT_SERIES)], label=sig)
    ax.set_xlabel(f"{r.analysis.dc_source or r.scale_name}")
    ax.grid(True, color="#e5e7eb")
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False)
    ax.set_title(f"DC sweep — {r.analysis.title}", loc="left", fontsize=12, fontweight="bold")
    fig.savefig(path, dpi=DPI)
    plt.close(fig)
    return Path(path)


def plot_points(r: AnalysisResult, path: Path) -> Path:
    f = r.scale
    fig, ax = plt.subplots(figsize=(9, 5), constrained_layout=True)
    for i, sig in enumerate(r.analysis.signals):
        try:
            y = np.abs(r.signal(sig))
        except KeyError:
            continue
        ax.plot(f, y, marker="o", lw=2, color=LIGHT_SERIES[i % len(LIGHT_SERIES)], label=f"|{sig}|")
    ax.set_xscale("log")
    ax.xaxis.set_major_formatter(EngFormatter(unit="Hz"))
    ax.grid(True, which="both", color="#e5e7eb")
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False)
    ax.set_title(f"Readings vs frequency — {r.analysis.title}", loc="left", fontsize=12, fontweight="bold")
    fig.savefig(path, dpi=DPI)
    plt.close(fig)
    return Path(path)


def render(r: AnalysisResult, out_dir: Path) -> list[Path]:
    if r.error or r.scale is None:
        return []
    out_dir.mkdir(parents=True, exist_ok=True)
    a = r.analysis
    target = out_dir / f"{a.id}.png"
    if a.kind == "op" or (a.kind == "ac_points" and len(r.scale) == 1):
        p = plot_multimeter(r, target)
    elif a.kind == "ac_points":
        p = plot_points(r, target)
    elif a.kind == "ac":
        p = plot_bode(r, target)
    elif a.kind == "tran":
        p = plot_scope(r, target)
    else:
        p = plot_sweep(r, target)
    r.plots.append(p)
    return [p]
