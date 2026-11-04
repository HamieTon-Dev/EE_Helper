"""Simulation result containers, data-file parsing and measurements."""
from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from mscopilot.plan import Analysis, Measurement


@dataclass
class MeasurementResult:
    name: str
    type: str
    signal: str
    value: float | None
    unit: str = ""
    expected: float | None = None
    tolerance_pct: float | None = None
    status: str = "info"  # pass | fail | info | error
    note: str = ""
    extra: dict = field(default_factory=dict)  # e.g. cursor times t1/t2 for scope plots


@dataclass
class AnalysisResult:
    analysis: Analysis
    scale_name: str = ""
    scale: np.ndarray | None = None
    data: dict[str, np.ndarray] = field(default_factory=dict)  # complex for AC, real otherwise
    measurements: list[MeasurementResult] = field(default_factory=list)
    plots: list[Path] = field(default_factory=list)
    screenshots: list[Path] = field(default_factory=list)
    csv_path: Path | None = None
    error: str = ""
    source: str = "ngspice"

    def op_values(self) -> dict[str, float]:
        """Single readings per probe: DC operating point, or AC magnitude at a single
        frequency (what a DMM in AC mode shows)."""
        if self.analysis.kind == "op":
            return {k: float(np.real(v[0])) for k, v in self.data.items() if len(v)}
        if self.analysis.kind == "ac_points" and self.scale is not None and len(self.scale) == 1:
            return {k: float(np.abs(v[0])) for k, v in self.data.items() if len(v)}
        return {}

    def phasor(self, name: str, f: float | None = None) -> complex:
        """Complex value of an AC/ac_points signal at frequency f (nearest point; exact for ac_points)."""
        if self.analysis.kind not in ("ac", "ac_points"):
            raise ValueError(f"{self.analysis.id} is not an AC analysis")
        v = self.signal(name)
        if f is None:
            if len(v) != 1:
                raise ValueError(f"{self.analysis.id} has several frequencies; give one")
            return complex(v[0])
        idx = int(np.argmin(np.abs(np.log10(self.scale) - np.log10(f))))
        if self.analysis.kind == "ac_points" and not np.isclose(self.scale[idx], f, rtol=1e-6):
            raise KeyError(f"{self.analysis.id} has no point at {f:g} Hz (have {', '.join(f'{x:g}' for x in self.scale)})")
        return complex(v[idx])

    def signal(self, name: str) -> np.ndarray:
        key = _find_key(self.data, name)
        if key is None:
            raise KeyError(f"Signal {name} was not recorded (have: {', '.join(self.data) or 'none'})")
        return self.data[key]


def _norm(sig: str) -> str:
    return "".join(sig.lower().split())


def _find_key(data: dict, name: str) -> str | None:
    n = _norm(name)
    for k in data:
        if _norm(k) == n:
            return k
    # ngspice writes currents as i(v1) or v1#branch
    m = n
    if m.startswith("i(") and m.endswith(")"):
        alt = m[2:-1] + "#branch"
        for k in data:
            if _norm(k) == alt:
                return k
    return None


# ---------------------------------------------------------------- parsing
def parse_wrdata(path: Path, analysis_kind: str) -> tuple[str, np.ndarray, dict[str, np.ndarray]]:
    """Parse an ngspice ``wrdata`` file written with wr_singlescale + wr_vecnames.

    The first column is the scale (time/frequency/sweep); each real vector takes one column
    and each complex vector two (re, im) under the same repeated header name.
    """
    text = Path(path).read_text(encoding="utf-8", errors="replace").strip().splitlines()
    if not text:
        raise ValueError(f"{path} is empty")
    header = text[0].split()
    rows = [list(map(float, ln.split())) for ln in text[1:] if ln.strip()]
    arr = np.array(rows, dtype=float) if rows else np.zeros((0, len(header)))
    scale_name = header[0]
    scale = arr[:, 0] if arr.size else np.array([])
    data: dict[str, np.ndarray] = {}
    i = 1
    while i < len(header):
        name = header[i]
        if i + 1 < len(header) and header[i + 1] == name:
            data[name] = arr[:, i] + 1j * arr[:, i + 1]
            i += 2
        else:
            data[name] = arr[:, i]
            i += 1
    return scale_name, scale, data


def write_csv(result: AnalysisResult, path: Path) -> Path:
    path = Path(path)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        cols = [result.scale_name or "index"]
        series = []
        for k, v in result.data.items():
            if np.iscomplexobj(v):
                cols += [f"{k} mag_dB", f"{k} phase_deg"]
                series += [20 * np.log10(np.maximum(np.abs(v), 1e-30)), np.degrees(np.angle(v))]
            else:
                cols.append(k)
                series.append(v)
        w.writerow(cols)
        n = len(result.scale) if result.scale is not None else (len(series[0]) if series else 0)
        for j in range(n):
            row = [result.scale[j] if result.scale is not None and len(result.scale) else j]
            row += [s[j] for s in series]
            w.writerow([f"{x:.9g}" for x in row])
    result.csv_path = path
    return path


# ---------------------------------------------------------------- measurements
def _interp(x: np.ndarray, y: np.ndarray, at: float, log_x: bool = False) -> float:
    if log_x:
        return float(np.interp(math.log10(at), np.log10(x), y))
    return float(np.interp(at, x, y))


def _crossing(x: np.ndarray, y: np.ndarray, level: float, log_x: bool = False, start: int = 0) -> float | None:
    for j in range(max(start, 1), len(y)):
        y0, y1 = y[j - 1], y[j]
        if (y0 - level) * (y1 - level) <= 0 and y0 != y1:
            frac = (level - y0) / (y1 - y0)
            if log_x:
                lx = math.log10(x[j - 1]) + frac * (math.log10(x[j]) - math.log10(x[j - 1]))
                return 10**lx
            return float(x[j - 1] + frac * (x[j] - x[j - 1]))
    return None


def transfer(result: AnalysisResult, signal: str, ref_name: str | None = "__analysis__") -> np.ndarray:
    """signal / reference (or the signal itself if there is no reference) for AC data."""
    sig = result.signal(signal)
    if ref_name == "__analysis__":
        ref_name = result.analysis.reference_signal
    if ref_name and _norm(ref_name) != _norm(signal):
        ref = result.signal(ref_name)
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.where(np.abs(ref) > 0, sig / ref, 0)
    return sig


def _ac_at(r: AnalysisResult, x: np.ndarray, h: np.ndarray, at: float) -> complex:
    if r.analysis.kind == "ac_points":
        idx = int(np.argmin(np.abs(x - at)))
        if not np.isclose(x[idx], at, rtol=1e-6):
            raise ValueError(f"no point at {at:g} Hz")
        return complex(h[idx])
    mag = np.interp(math.log10(at), np.log10(x), np.abs(h))
    ph = np.interp(math.log10(at), np.log10(x), np.unwrap(np.angle(h)))
    return complex(mag * np.exp(1j * ph))


def rising_crossings(t: np.ndarray, y: np.ndarray, falling: bool = False) -> list[float]:
    """Times where y crosses its mid level going up (or down) (linear interpolation)."""
    mid = (y.max() + y.min()) / 2
    above = y >= mid
    idx = np.where(above[:-1] & ~above[1:])[0] if falling else np.where(~above[:-1] & above[1:])[0]
    return [float(t[j] + (mid - y[j]) * (t[j + 1] - t[j]) / (y[j + 1] - y[j])) for j in idx if y[j + 1] != y[j]]


def estimate_period(t: np.ndarray, y: np.ndarray) -> float | None:
    rise = rising_crossings(t, y)
    if len(rise) >= 2:
        return float(np.median(np.diff(rise)))
    both = sorted(rise + rising_crossings(t, y, falling=True))
    if len(both) >= 2:
        return float(2 * np.median(np.diff(both)))
    return None


def compute_measurement(result: AnalysisResult, m: Measurement) -> MeasurementResult:
    mr = MeasurementResult(m.name, m.type, m.signal, None, m.unit or "", m.expected, m.tolerance_pct)
    try:
        x = result.scale
        kind = result.analysis.kind
        if kind in ("ac", "ac_points"):
            ref = m.reference or result.analysis.reference_signal
            h = transfer(result, m.signal, ref)
            mag_db = 20 * np.log10(np.maximum(np.abs(h), 1e-30))
            phase = np.degrees(np.unwrap(np.angle(h)))
            if m.type in ("gain_db_at", "phase_at", "mag_at", "value_at"):
                z = _ac_at(result, x, h, m.at)
                mr.value = {"gain_db_at": 20 * math.log10(max(abs(z), 1e-30)), "phase_at": math.degrees(np.angle(z)),
                            "mag_at": abs(z), "value_at": abs(z)}[m.type]
            elif m.type == "max_gain_db":
                mr.value = float(mag_db.max())
            elif m.type in ("f_peak", "f_min"):
                k = int(np.argmax(mag_db) if m.type == "f_peak" else np.argmin(mag_db))
                mr.value = _refine_extremum(x, mag_db, k) if kind == "ac" else float(x[k])
                mr.extra = {"gain_db": float(mag_db[k])}
                if k in (0, len(x) - 1):
                    mr.note = "extremum at the edge of the sweep; widen the frequency range"
            elif m.type == "f_3db":
                peak = int(np.argmax(mag_db))
                level = mag_db[peak] - 3.0103
                f = _crossing(x, mag_db, level, log_x=True, start=peak + 1)
                if f is None and peak > 0:  # high-pass: crossing below the peak
                    f = _crossing(x[: peak + 1], mag_db[: peak + 1], level, log_x=True)
                if f is None:
                    mr.note = "no -3 dB crossing inside the sweep"
                mr.value = f
            elif m.type in ("max", "min"):
                mr.value = float(np.abs(h).max() if m.type == "max" else np.abs(h).min())
            else:
                raise ValueError(f"{m.type} is not meaningful for an AC analysis")
        else:
            y = np.real(result.signal(m.signal))
            if kind == "op" or m.type == "dc_value":
                mr.value = float(y[0]) if kind == "op" else float(np.mean(y))
            elif m.type == "max":
                mr.value = float(y.max())
            elif m.type == "min":
                mr.value = float(y.min())
            elif m.type == "vpp":
                mr.value = float(y.max() - y.min())
            elif m.type == "vpk":
                mr.value = float(np.abs(y).max())
            elif m.type == "vrms":
                mr.value = float(np.sqrt(np.trapezoid(y**2, x) / (x[-1] - x[0]))) if len(x) > 1 else float(abs(y[0]))
            elif m.type == "vavg":
                mr.value = float(np.trapezoid(y, x) / (x[-1] - x[0])) if len(x) > 1 else float(y[0])
            elif m.type == "value_at":
                mr.value = _interp(x, y, m.at)
            elif m.type == "frequency":
                mr.value = _frequency(x, y)
                if mr.value is None:
                    mr.note = "fewer than two cycles in the window"
            elif m.type in ("period", "delta_t", "phase_shift_deg"):
                _timing(result, m, mr, x, y)
            elif m.type == "rise_time":
                lo, hi = y.min(), y.max()
                t10 = _crossing(x, y, lo + 0.1 * (hi - lo))
                t90 = _crossing(x, y, lo + 0.9 * (hi - lo))
                mr.value = (t90 - t10) if (t10 is not None and t90 is not None and t90 > t10) else None
                if mr.value is None:
                    mr.note = "no clean 10-90 % rising edge"
            else:
                raise ValueError(f"{m.type} is not meaningful for a {kind} analysis")
    except Exception as e:
        mr.status, mr.note = "error", str(e)
        return mr
    mr.status = judge(mr)
    return mr


def _refine_extremum(x: np.ndarray, y: np.ndarray, k: int) -> float:
    """Parabolic interpolation of a peak/dip on a log-frequency axis."""
    if 0 < k < len(x) - 1:
        lx = np.log10(x[k - 1 : k + 2])
        a, b, _ = np.polyfit(lx, y[k - 1 : k + 2], 2)
        if a != 0:
            v = -b / (2 * a)
            if lx[0] <= v <= lx[2]:
                return float(10**v)
    return float(x[k])


def _timing(result: AnalysisResult, m: Measurement, mr: MeasurementResult, t: np.ndarray, y: np.ndarray) -> None:
    """Scope-cursor style timing: period, delta-t and phase shift from rising crossings."""
    sig_x = rising_crossings(t, y)
    period = estimate_period(t, y)
    if period is None or not sig_x:
        raise ValueError("less than one full cycle in the window; lengthen the transient")
    if m.type == "period":
        mr.value = period
        mr.extra = {"t1": sig_x[0], "t2": sig_x[0] + period, "ch_a": m.signal}
        return
    ref_name = m.reference or result.analysis.reference_signal
    ref = np.real(result.signal(ref_name))
    ref_x = rising_crossings(t, ref)
    if not ref_x:
        raise ValueError(f"reference {ref_name} has no rising crossing")
    # pick the signal crossing nearest to the first reference crossing that has one after it
    t_ref = next((tr for tr in ref_x if tr + period / 2 <= t[-1]), ref_x[0])
    t_sig = min(sig_x, key=lambda ts: abs(ts - t_ref))
    dt = t_ref - t_sig  # > 0 when the signal crosses first, i.e. leads the reference
    # wrap into (-T/2, T/2]
    dt = (dt + period / 2) % period - period / 2
    mr.extra = {"t1": min(t_ref, t_sig), "t2": max(t_ref, t_sig), "period": period, "ch_a": m.signal, "ch_b": ref_name}
    mr.value = abs(dt) if m.type == "delta_t" else dt / period * 360.0
    if m.type == "delta_t":
        mr.note = "signal leads reference" if dt > 0 else "signal lags reference"


def _frequency(t: np.ndarray, y: np.ndarray) -> float | None:
    mid = (y.max() + y.min()) / 2
    s = np.sign(y - mid)
    idx = np.where((s[:-1] < 0) & (s[1:] >= 0))[0]
    if len(idx) < 3:
        return None
    times = [t[j] + (mid - y[j]) * (t[j + 1] - t[j]) / (y[j + 1] - y[j]) for j in idx]
    periods = np.diff(times)
    return float(1.0 / np.median(periods))


def judge(mr: MeasurementResult) -> str:
    if mr.value is None:
        return "error" if mr.expected is not None else "info"
    if mr.expected is None:
        return "info"
    pct = abs(mr.tolerance_pct if mr.tolerance_pct is not None else 5.0)
    # A percentage of zero is meaningless, so for a zero target the tolerance is absolute
    # (in the measurement's own unit, e.g. ±0.5 dB or ±10 mV written as 0.5 / 0.01).
    band = pct if mr.expected == 0 else abs(mr.expected) * pct / 100
    return "pass" if abs(mr.value - mr.expected) <= band else "fail"
