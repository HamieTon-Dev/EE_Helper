"""EE calculator: required verification cases plus independent reference values.

Reference values were computed by hand / independently (plain formulas or brute-force
numerics below), not by calling the code under test.
"""
import cmath
import itertools
import math

import numpy as np
import pytest

from eesimlab import eecalc
from eesimlab.eecalc import core, units
from eesimlab.eecalc.core import CalcError, run

TWO_PI = 2 * math.pi


# ------------------------------------------------------------------ required verification cases
def test_rc_cutoff_1k_4n():
    _, r = run("rc_filter", {"R": ("1", "kΩ"), "C": ("4", "nF")})
    assert r.value("fC") == pytest.approx(39.79e3, abs=5)  # 1/(2π·1000·4e-9) = 39 788.7 Hz


def test_rc_lowpass_cutoff_vs_specified_frequency():
    _, r = run("rc_filter", {"R": ("1", "kΩ"), "C": ("4", "nF"), "Vin": ("3.30", "V"), "f": ("200", "kHz")},
               {"response": "lp"})
    assert r.value("Vout @ fC") == pytest.approx(2.33, abs=0.005)  # 3.3/√2 = 2.3335
    assert r.value("Vout @ f") == pytest.approx(0.64, abs=0.005)  # 3.3/√(1+(200/39.789)²) = 0.6439
    assert r.value("Vout @ f") != pytest.approx(3.3 * 0.707, abs=0.1)  # never 0.707·Vin away from fC
    assert any("not Vin × 0.707" in s for s in r.steps)


def test_rl_cutoff_2k_14m():
    _, r = run("rl_filter", {"R": ("2", "kΩ"), "L": ("14", "mH")})
    assert r.value("fC") == pytest.approx(22.74e3, abs=5)  # 2000/(2π·0.014) = 22 736.4 Hz


def test_series_rlc_bandwidth():
    _, r = run("series_rlc", {"Vin": ("5", "V"), "L": ("95", "mH"), "C": ("100", "nF"), "RL": ("120", "Ω"),
                              "RW": ("40", "Ω"), "Rs": ("0", "Ω")}, {"model": "lossy"})
    assert r.value("BW") == pytest.approx(268.05, abs=0.01)  # 160/(2π·0.095) = 268.050 Hz


def test_series_resonance_output_across_load():
    _, r = run("series_rlc", {"Vin": ("5", "V"), "L": ("95", "mH"), "C": ("100", "nF"), "RL": ("212", "Ω"),
                              "RW": ("21", "Ω"), "Rs": ("0", "Ω")}, {"model": "lossy"})
    assert r.value("Vout @ fr") == pytest.approx(4.55, abs=0.005)  # 5·212/233 = 4.5494 V


# ------------------------------------------------------------------ filters, more references
@pytest.mark.parametrize("kind", ["lp", "hp"])
def test_filter_gain_and_phase_against_complex_math(kind):
    R, C, f = 4700.0, 22e-9, 3000.0
    _, r = run("rc_filter", {"R": ("4.7", "kΩ"), "C": ("22", "nF"), "Vin": ("1", "V"), "f": ("3", "kHz")}, {"response": kind})
    zc = 1 / (1j * TWO_PI * f * C)
    h = zc / (R + zc) if kind == "lp" else R / (R + zc)
    assert r.value("Gain @ f") == pytest.approx(abs(h), rel=1e-12)
    assert r.value("Gain @ f (dB)") == pytest.approx(20 * math.log10(abs(h)), rel=1e-12)
    assert r.value("φ @ f") == pytest.approx(cmath.phase(h), abs=1e-12)


def test_rl_highpass_output_across_inductor_and_solve_for_L():
    _, r = run("rl_filter", {"R": ("2", "kΩ"), "fc_t": ("10", "kHz")}, {"response": "hp"}, unknown="L")
    assert r.value("L") == pytest.approx(2000 / (TWO_PI * 10e3), rel=1e-12)
    assert "inductor L" in next(o.text for o in r.outputs if o.symbol == "Output across")


def test_rc_solve_for_c_and_r():
    _, r = run("rc_filter", {"R": ("10", "kΩ"), "fc_t": ("1", "kHz")}, unknown="C")
    assert r.value("C") == pytest.approx(1 / (TWO_PI * 1e3 * 1e4), rel=1e-12)
    _, r = run("rc_filter", {"C": ("100", "nF"), "fc_t": ("1", "kHz")}, unknown="R")
    assert r.value("R") == pytest.approx(1 / (TWO_PI * 1e3 * 1e-7), rel=1e-12)


def test_highpass_dc_limit_is_minus_infinity_db_not_a_crash():
    _, r = run("rc_filter", {"R": ("1", "kΩ"), "C": ("4", "nF"), "Vin": ("1", "V"), "f": ("0", "Hz")}, {"response": "hp"})
    assert r.value("Vout @ f") == 0 and r.value("Gain @ f (dB)") == -math.inf


def test_plot_never_starts_at_zero_hz():
    from eesimlab.eecalc.graph import freq_range

    _, r = run("rc_filter", {"R": ("1", "kΩ"), "C": ("4", "nF"), "Vin": ("1", "V"), "f": ("0", "Hz")})
    lo, hi = freq_range(r.plot)
    assert lo > 0 and hi > lo


# ------------------------------------------------------------------ basic
def test_ohms_law_and_short_circuit():
    _, r = run("ohms_law", {"V": ("12", "V"), "R": ("1.2", "kΩ")}, unknown="I")
    assert r.value("I") == pytest.approx(0.01) and r.value("P") == pytest.approx(0.12)
    with pytest.raises(CalcError, match="short circuit"):
        run("ohms_law", {"V": ("12", "V"), "R": ("0", "Ω")}, unknown="I")


def test_power_pairs_consistent():
    for pair, entries in (("PR", {"P": ("2", "W"), "R": ("50", "Ω")}), ("VI", {"V": ("10", "V"), "I": ("200", "mA")})):
        _, r = run("power", entries, {"pair": pair})
        assert r.value("P") == pytest.approx(r.value("V") * r.value("I"))
        assert r.value("R") == pytest.approx(r.value("V") / r.value("I"))


def test_series_parallel_components():
    _, r = run("series_parallel", {"x1": ("10", "kΩ"), "x2": ("15", "kΩ"), "x3": ("30", "kΩ")},
               {"component": "R", "connection": "parallel"})
    assert r.value("Req") == pytest.approx(5000)  # 1/(1/10k+1/15k+1/30k) = 5 kΩ
    _, r = run("series_parallel", {"x1": ("10", "nF"), "x2": ("15", "nF")}, {"component": "C", "connection": "series"})
    assert r.value("Ceq") == pytest.approx(6e-9)
    _, r = run("series_parallel", {"x1": ("10", "mH"), "x2": ("15", "mH")}, {"component": "L", "connection": "series"})
    assert r.value("Leq") == pytest.approx(25e-3)


def test_voltage_divider_loaded_and_solve():
    _, r = run("divider", {"Vin": ("12", "V"), "R1": ("10", "kΩ"), "R2": ("10", "kΩ"), "RL": ("10", "kΩ")})
    assert r.value("Vout (no load)") == pytest.approx(6.0)
    assert r.value("Vout (loaded)") == pytest.approx(12 * 5000 / 15000)  # R2∥RL = 5k
    _, r = run("divider", {"Vin": ("12", "V"), "R1": ("10", "kΩ"), "Vout_t": ("3", "V")}, unknown="R2")
    assert r.value("R2") == pytest.approx(10000 * 3 / 9)
    with pytest.raises(CalcError, match="Impossible with this load"):
        run("divider", {"Vin": ("12", "V"), "R1": ("10", "kΩ"), "Vout_t": ("6", "V"), "RL": ("5", "kΩ")}, unknown="R2")


def test_sine_conversions():
    _, r = run("sine_convert", {"V": ("10", "V")}, {"form": "pp"})
    assert r.value("Vp") == pytest.approx(5) and r.value("Vrms") == pytest.approx(5 / math.sqrt(2))


# ------------------------------------------------------------------ RLC
def test_reactance_dc_limits():
    _, r = run("reactance", {"f": ("0", "Hz"), "C": ("1", "µF")}, unknown="XC")
    assert math.isinf(r.value("XC"))
    _, r = run("reactance", {"f": ("0", "Hz"), "L": ("1", "mH")}, unknown="XL")
    assert r.value("XL") == 0


def test_resonance_round_trip():
    _, r = run("resonance", {"L": ("100", "mH"), "C": ("0.22", "µF")})
    fr = 1 / (TWO_PI * math.sqrt(0.1 * 0.22e-6))
    assert r.value("fr") == pytest.approx(fr) == pytest.approx(1073.02, abs=0.01)
    _, r = run("resonance", {"fr": (str(fr), "Hz"), "C": ("0.22", "µF")}, unknown="L")
    assert r.value("L") == pytest.approx(0.1)


def test_rlc_impedance_series_and_ideal_parallel_resonance():
    _, r = run("rlc_impedance", {"f": ("1", "kHz"), "R": ("100", "Ω"), "L": ("10", "mH"), "C": ("1", "µF")})
    w = TWO_PI * 1e3
    z = complex(100, w * 0.01 - 1 / (w * 1e-6))
    assert r.value("Z") == pytest.approx(z)
    fr = 1 / (TWO_PI * math.sqrt(0.1 * 0.22e-6))
    _, r = run("rlc_impedance", {"f": (repr(fr), "Hz"), "L": ("100", "mH"), "C": ("0.22", "µF")}, {"connection": "parallel"})
    assert "∞" in next(o.text for o in r.outputs if o.symbol == "Z")


def test_series_rlc_exact_cutoffs_by_brute_force():
    L, C, rtot, rl = 0.095, 100e-9, 160.0, 120.0
    _, r = run("series_rlc", {"Vin": ("5", "V"), "L": ("95", "mH"), "C": ("100", "nF"), "RL": ("120", "Ω"),
                              "RW": ("40", "Ω"), "Rs": ("0", "Ω")})
    f = np.geomspace(1000, 3000, 2_000_001)
    w = TWO_PI * f
    h = np.abs(rl / (rtot + 1j * (w * L - 1 / (w * C))))
    k = h.argmax()
    above = np.where(h >= h[k] / math.sqrt(2))[0]
    assert r.value("f1") == pytest.approx(f[above[0]], rel=1e-5)
    assert r.value("f2") == pytest.approx(f[above[-1]], rel=1e-5)
    assert r.value("fr") == pytest.approx(f[k], rel=1e-5)
    assert r.value("f1") * r.value("f2") == pytest.approx(r.value("fr") ** 2)


def test_parallel_tank_unity_pf_matches_lab_formula_and_numeric_bw():
    for rw, expected in ((1, 2321.51), (100, 2316.05), (500, 2180.86)):  # fr = √(1/LC − (RW/L)²)/2π
        _, r = run("parallel_rlc", {"Vin": ("10", "V"), "Rs": ("1", "Ω"), "L": ("100", "mH"), "RW": (str(rw), "Ω"),
                                    "C": ("47", "nF")}, {"arrangement": "tank_v", "output": "tank"})
        assert r.value("fr (unity pf)") == pytest.approx(expected, abs=0.01)
    _, r = run("parallel_rlc", {"Vin": ("10", "V"), "Rs": ("10", "kΩ"), "L": ("100", "mH"), "RW": ("10", "Ω"),
                                "C": ("47", "nF")})
    f = np.geomspace(1000, 5000, 2_000_001)
    w = TWO_PI * f
    zt = 1 / (1 / (10 + 1j * w * 0.1) + 1j * w * 47e-9)
    h = np.abs(zt / (1e4 + zt))
    above = np.where(h >= h.max() / math.sqrt(2))[0]
    assert r.value("BW") == pytest.approx(f[above[-1]] - f[above[0]], rel=1e-4)


def test_parallel_requires_arrangement_details():
    with pytest.raises(CalcError, match="Rs > 0"):
        run("parallel_rlc", {"Vin": ("10", "V"), "Rs": ("0", "Ω"), "L": ("100", "mH"), "C": ("47", "nF")})


def test_ideal_parallel_current_source_exact_bw():
    _, r = run("parallel_rlc", {"I": ("1", "mA"), "R": ("10", "kΩ"), "L": ("100", "mH"), "C": ("47", "nF")},
               {"arrangement": "ideal_i"})
    assert r.value("BW") == pytest.approx(1 / (TWO_PI * 1e4 * 47e-9))
    assert r.value("V @ fr") == pytest.approx(10.0)


# ------------------------------------------------------------------ transients
def test_rc_charge_and_time_to_target():
    _, r = run("rc_transient", {"R": ("10", "kΩ"), "C": ("10", "µF"), "V0": ("0", "V"), "Vf": ("12", "V"), "t": ("100", "ms")})
    assert r.value("vC(t)") == pytest.approx(12 * (1 - math.exp(-1)))  # t = τ
    _, r = run("rc_transient", {"R": ("10", "kΩ"), "C": ("10", "µF"), "V0": ("12", "V"), "Vf": ("0", "V"), "Vt": ("3", "V")},
               unknown="t_v")
    assert r.value("t") == pytest.approx(-0.1 * math.log(3 / 12))
    _, r = run("rc_transient", {"R": ("10", "kΩ"), "C": ("10", "µF"), "V0": ("0", "V"), "Vf": ("12", "V"), "pct": ("100", "%")},
               unknown="t_p")
    assert math.isinf(r.value("t"))
    with pytest.raises(CalcError, match="must lie between"):
        run("rc_transient", {"R": ("10", "kΩ"), "C": ("10", "µF"), "V0": ("0", "V"), "Vf": ("12", "V"), "Vt": ("13", "V")},
            unknown="t_v")


def test_rl_rise_with_initial_current():
    _, r = run("rl_transient", {"R": ("100", "Ω"), "L": ("50", "mH"), "Vs": ("10", "V"), "I0": ("20", "mA"), "t": ("0.5", "ms")})
    tau = 0.05 / 100
    assert r.value("iL(t)") == pytest.approx(0.1 + (0.02 - 0.1) * math.exp(-0.5e-3 / tau))
    _, r = run("rl_transient", {"R": ("100", "Ω"), "L": ("50", "mH"), "Vs": ("10", "V"), "I0": ("0", "mA"), "pct": ("63.2120558828558", "%")},
               unknown="t_p")
    assert r.value("t") == pytest.approx(tau)


def test_time_constant_solve_for_component():
    _, r = run("time_constant", {"R": ("10", "kΩ"), "tau": ("1", "ms")}, {"family": "RC"}, unknown="X")
    assert r.value("C") == pytest.approx(1e-7)


# ------------------------------------------------------------------ tools
def test_complex_conversions():
    _, r = run("complex_convert", {"a": ("-3", ""), "b": ("-4", "")}, unknown="polar")
    assert r.value("M") == 5 and r.value("θ") == pytest.approx(math.atan2(-4, -3))
    _, r = run("complex_convert", {"M": ("2", ""), "theta": (str(math.pi / 2), "rad")}, unknown="rect")
    assert r.value("a") == pytest.approx(0, abs=1e-12) and r.value("b") == pytest.approx(2)


def test_impedance_network_parallel():
    _, r = run("impedance_network", {"r1": ("100", "Ω"), "x1": ("50", "Ω"), "r2": ("200", "Ω"), "x2": ("-150", "Ω")},
               {"connection": "parallel", "form": "rect"})
    z1, z2 = complex(100, 50), complex(200, -150)
    assert r.value("Zeq") == pytest.approx(z1 * z2 / (z1 + z2))


def test_ac_power_and_rms_conversion():
    _, r = run("ac_power", {"V": ("169.7056", "V"), "I": ("7.0711", "A"), "theta": ("30", "°")},
               {"mode": "VIθ", "vconv": "peak"})
    assert r.value("S") == pytest.approx(120 * 5, rel=1e-4)
    assert r.value("P") == pytest.approx(600 * math.cos(math.radians(30)), rel=1e-4)


def test_transformer_ratio_direction_and_conflict():
    _, r = run("transformer", {"Np": ("500", "turns"), "Ns": ("50", "turns"), "Vp": ("120", "V"), "ZL": ("8", "Ω")})
    assert r.value("a = Np:Ns") == 10 and r.value("Vs") == pytest.approx(12)
    assert r.value("Is") == pytest.approx(1.5) and r.value("Ip") == pytest.approx(0.15) and r.value("Zin") == pytest.approx(800)
    with pytest.raises(CalcError, match="cannot satisfy both"):
        run("transformer", {"Np": ("500", "turns"), "Ns": ("50", "turns"), "Vp": ("120", "V"), "Vs": ("24", "V")})


def test_three_phase_line_vs_phase():
    _, r = run("three_phase", {"V": ("400", "V"), "I": ("10", "A"), "PF": ("0.8", "")}, {"connection": "Y"})
    assert r.value("Vph") == pytest.approx(400 / math.sqrt(3)) and r.value("P") == pytest.approx(math.sqrt(3) * 400 * 10 * 0.8)
    _, r = run("three_phase", {"V": ("400", "V"), "I": ("10", "A"), "PF": ("0.8", "")}, {"connection": "D", "igiven": "phase"})
    assert r.value("IL") == pytest.approx(10 * math.sqrt(3))


def test_frequency_period():
    _, r = run("freq_period", {"f": ("2", "kHz")}, unknown="T")
    assert r.value("T") == pytest.approx(0.5e-3)


# ------------------------------------------------------------------ units, validation, framework
def test_units_and_formatting():
    assert units.to_base(4.7, "capacitance", "nF") == pytest.approx(4.7e-9)
    assert units.fmt(39788.735, "frequency", 2) == "39.79 kHz"
    assert units.fmt(999.9996, "voltage", 3) == "1.000 kV"
    assert units.fmt(math.inf, "impedance") == "∞ Ω"
    with pytest.raises(units.UnitError):
        units.factor("capacitance", "kΩ")
    with pytest.raises(units.UnitError):
        units.parse_number("4k7")


def test_validation_messages():
    with pytest.raises(CalcError, match="Enter R"):
        run("rc_filter", {"C": ("4", "nF")})
    with pytest.raises(CalcError, match="greater than zero"):
        run("rc_filter", {"R": ("-1", "kΩ"), "C": ("4", "nF")})
    with pytest.raises(CalcError, match="not a unit of capacitance"):
        run("rc_filter", {"R": ("1", "kΩ"), "C": ("4", "kΩ")})
    with pytest.raises(CalcError, match="not a number"):
        run("rc_filter", {"R": ("abc", "kΩ"), "C": ("4", "nF")})


def test_every_calculator_runs_on_its_examples_for_every_option():
    for c in core.REGISTRY:
        combos = [dict(zip([o.key for o in c.options], vals))
                  for vals in itertools.product(*[[k for k, _ in o.choices] for o in c.options])] or [{}]
        for opts in combos:
            for u in ([k for k, _ in c.unknowns] or [None]):
                o = c.default_options() | opts
                entries = {f.key: (f.default, f.unit_for(o)) for f in c.visible_fields(o, u)}
                try:
                    _, res = run(c.id, entries, opts, u)
                except CalcError:
                    continue  # a clean, explained refusal is acceptable
                text = core.plain_text(c, core.build_input(c, entries, opts, u), res)
                assert res.title in text and "Result:" in text


def test_search_and_categories():
    assert {c.category for c in core.REGISTRY} <= set(core.CATEGORIES)
    assert any(c.id == "rc_filter" for c in eecalc.search("cutoff"))
    assert any(c.id == "three_phase" for c in eecalc.search("delta"))


def test_store_roundtrip(tmp_path):
    from eesimlab.eecalc.store import Entry, Store

    s = Store(tmp_path / "calc.json")
    s.add_history(Entry("rc_filter", "RC", {"response": "lp"}, "fc", {"R": ["1", "kΩ"]}, "fC = 39.789 kHz"))
    s.add_saved(Entry("rc_filter", "RC", {}, "fc", {}, "x", name="mine"))
    s2 = Store(tmp_path / "calc.json")
    assert s2.history[0].headline == "fC = 39.789 kHz" and s2.saved[0].name == "mine"
