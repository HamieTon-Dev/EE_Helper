"""Draw a Result's PlotSpec onto a matplotlib Figure (GUI-independent, testable headless).

Frequency plots always use a logarithmic axis that starts above 0 Hz.
"""
from __future__ import annotations

import math

import numpy as np
from matplotlib.ticker import EngFormatter

from mscopilot.eecalc.core import PlotSpec

MAG_MODES = [("V", "Volts"), ("ratio", "Ratio Vout/Vin"), ("dB", "Decibels")]
COLORS = {"curve": "#2563eb", "phase": "#7c3aed", "marker": "#dc2626", "band": "#bfdbfe", "hline": "#6b7280", "user": "#059669"}


def freq_range(spec: PlotSpec) -> tuple[float, float]:
    lo, hi = spec.f_min, spec.f_max
    xs = [x for _, x in spec.markers if x and x > 0 and math.isfinite(x)]
    if xs:
        lo = min([lo] + [x / 10 for x in xs]) if lo > 0 else min(xs) / 1000
        hi = max([hi] + [x * 10 for x in xs])
    if not lo > 0:  # never put 0 Hz on a log axis
        lo = hi / 1e6 if hi > 0 else 1.0
    return lo, max(hi, lo * 10)


def draw(fig, spec: PlotSpec, mag_mode: str = "dB", angle_unit: str = "°") -> None:
    import warnings

    with warnings.catch_warnings():  # clearing shared log axes warns harmlessly
        warnings.simplefilter("ignore", UserWarning)
        fig.clear()
    if spec.kind == "time":
        _draw_time(fig, spec)
    else:
        _draw_freq(fig, spec, mag_mode, angle_unit)


def _draw_freq(fig, spec: PlotSpec, mode: str, angle_unit: str) -> None:
    lo, hi = freq_range(spec)
    f = np.geomspace(lo, hi, 2000)
    h = np.asarray(spec.func(f), dtype=complex)
    mag = np.abs(h)
    if mode == "V" and spec.vin is None:
        mode = "ratio"
    ax_m = fig.add_subplot(2, 1, 1)
    ax_p = fig.add_subplot(2, 1, 2, sharex=ax_m)

    def conv(y):
        y = np.asarray(y, dtype=float)
        if mode == "dB":
            with np.errstate(divide="ignore"):
                return 20 * np.log10(np.maximum(y, 1e-300))
        return y * (spec.vin if mode == "V" else 1.0)

    ylab = {"dB": "Gain (dB)", "ratio": spec.ratio_label, "V": f"Vout {spec.vin_label}"}[mode]
    ax_m.semilogx(f, conv(mag), color=COLORS["curve"], lw=2)
    ph = np.unwrap(np.angle(h))
    ph = np.degrees(ph) if angle_unit != "rad" else ph
    ax_p.semilogx(f, ph, color=COLORS["phase"], lw=2)
    if spec.band and all(spec.band):
        for ax in (ax_m, ax_p):
            ax.axvspan(spec.band[0], spec.band[1], color=COLORS["band"], alpha=0.5, lw=0)
    for label, y in spec.hlines:
        yy = float(conv([y])[0])
        ax_m.axhline(yy, color=COLORS["hline"], ls=":", lw=1)
        ax_m.annotate(label, (lo, yy), xytext=(4, 2), textcoords="offset points", fontsize=8, color=COLORS["hline"])
    eng = EngFormatter("Hz", places=2)
    for i, (label, x) in enumerate(spec.markers):
        if not (x and x > 0 and lo <= x <= hi):
            continue
        c = COLORS["user"] if label == "f" else COLORS["marker"]
        for ax in (ax_m, ax_p):
            ax.axvline(x, color=c, ls="--", lw=1)
        ax_m.annotate(f"{label} = {eng(x)}", (x, 1.0), xycoords=("data", "axes fraction"), xytext=(3, -12 - 11 * (i % 3)),
                      textcoords="offset points", fontsize=8, color=c)
    ax_m.set_ylabel(ylab)
    ax_p.set_ylabel("Phase (°)" if angle_unit != "rad" else "Phase (rad)")
    ax_p.set_xlabel("Frequency (log scale)")
    ax_p.xaxis.set_major_formatter(EngFormatter("Hz"))
    ax_m.set_xlim(lo, hi)
    for ax in (ax_m, ax_p):
        ax.grid(True, which="both", color="#e5e7eb", lw=0.7)
    ax_m.set_title(spec.title, fontsize=10, loc="left")
    plt_tl(fig)


def _draw_time(fig, spec: PlotSpec) -> None:
    t = np.linspace(0, spec.t_max, 1000)
    y = np.asarray(spec.func(t), dtype=float)
    ax = fig.add_subplot(1, 1, 1)
    ax.plot(t, y, color=COLORS["curve"], lw=2)
    for label, v in spec.hlines:
        ax.axhline(v, color=COLORS["hline"], ls=":", lw=1)
        ax.annotate(label, (0, v), xytext=(4, 2), textcoords="offset points", fontsize=8, color=COLORS["hline"])
    for label, x in spec.markers:
        if x is None or not math.isfinite(x) or x > spec.t_max:
            continue
        c = COLORS["user"] if label == "t" else COLORS["marker"]
        ax.axvline(x, color=c, ls="--", lw=1)
        ax.annotate(label, (x, 1.0), xycoords=("data", "axes fraction"), xytext=(3, -12), textcoords="offset points",
                    fontsize=8, color=c)
    ax.xaxis.set_major_formatter(EngFormatter("s"))
    unit = {"voltage": "V", "current": "A"}.get(spec.y_quantity, "")
    ax.yaxis.set_major_formatter(EngFormatter(unit))
    ax.set_xlabel("Time")
    ax.set_ylabel(spec.y_label)
    ax.grid(True, color="#e5e7eb")
    ax.set_title(spec.title, fontsize=10, loc="left")
    plt_tl(fig)


def plt_tl(fig):
    try:
        fig.tight_layout()
    except Exception:
        pass
