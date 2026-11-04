"""Complex numbers, conversions and practical calculators against independent references."""
import cmath
import math

import pytest

from mscopilot.eecalc import cexpr
from mscopilot.eecalc.core import CalcError, run


def test_quiz_multiply_polar():
    # A = 8 + j3, B = 29∠-72°, C = A*B = 247.78∠-51.44°
    _, r = run("complex_calc", {"A": "8 + j(3)", "B": "29∠-72", "expr": "A*B"})
    assert round(r.value("Magnitude |z|"), 2) == 247.78
    assert round(math.degrees(r.value("Angle θ")), 2) == -51.44
    ref = complex(8, 3) * cmath.rect(29, math.radians(-72))
    assert r.value("Result (rectangular)") == pytest.approx(ref)


def test_quiz_polar_to_rectangular_sign():
    # 7∠-64° = 3.07 − j6.29 (imaginary part negative)
    _, r = run("complex_calc", {"A": "7∠-64", "expr": "A"})
    assert round(r.value("Real part a"), 2) == 3.07
    assert round(r.value("Imaginary part b"), 2) == -6.29
    _, r = run("complex_convert", {"M": ("7", ""), "theta": ("-64", "°")}, unknown="rect")
    assert round(r.value("b"), 2) == -6.29


@pytest.mark.parametrize("expr,ref", [
    ("(3+j4)/(1-j2)", (3 + 4j) / (1 - 2j)),
    ("A+B-C", (2 + 3j) + cmath.rect(5, math.radians(30)) - 4j),
    ("conj(3+4j)*2", (3 - 4j) * 2),
    ("par(100+j50, 200-j150)", 1 / (1 / (100 + 50j) + 1 / (200 - 150j))),
    ("1/(j*2*pi*1k*100n)", 1 / (1j * 2 * math.pi * 1e3 * 100e-9)),
    ("sqrt(-4)", 2j),
    ("10 ang 90", 10j),
    ("2.2k + j4k7", None),
])
def test_complex_expressions(expr, ref):
    v = {"A": 2 + 3j, "B": cmath.rect(5, math.radians(30)), "C": 4j}
    if ref is None:
        with pytest.raises(cexpr.ExprError):
            cexpr.evaluate(expr, v)
        return
    assert cexpr.evaluate(expr, v) == pytest.approx(ref)


def test_complex_errors_are_explained():
    for bad in ("3 4", "1/0", "foo + 1", "(3+", "A*B"):
        with pytest.raises(cexpr.ExprError):
            cexpr.evaluate(bad, {"A": 1, "B": None})
    with pytest.raises(CalcError, match="Division by zero"):
        run("complex_calc", {"A": "1", "B": "0", "expr": "A/B"})


def test_radians_mode():
    _, r = run("complex_calc", {"A": f"2∠{math.pi / 2}", "expr": "A"}, {"angle": "rad"})
    assert r.value("Imaginary part b") == pytest.approx(2)


def test_prefix_conversion_is_exact():
    _, r = run("prefix_convert", {"value": "1"}, {"unit": "W", "from": "G", "to": "m"})
    assert r.outputs[0].text == "1000000000000 mW"
    _, r = run("prefix_convert", {"value": "0.1"}, {"unit": "F", "from": "µ", "to": "p"})
    assert r.outputs[0].text == "100000 pF"  # no 99999.99999 float artefacts
    _, r = run("prefix_convert", {"value": "3.3"}, {"unit": "V", "from": "_", "to": "q"})
    assert r.outputs[0].text.startswith("33" + "0" * 29)


def test_unit_conversions():
    _, r = run("unit_convert", {"value": "1"}, {"family": "energy", "from_energy": "kWh"})
    assert next(o.text for o in r.outputs if o.symbol == "J") == "3600000 J"
    _, r = run("unit_convert", {"value": "100"}, {"family": "temperature", "from_temperature": "°C"})
    assert next(o.text for o in r.outputs if o.symbol == "°F") == "212 °F"


def test_decibels():
    _, r = run("decibels", {"x": "2"})
    assert r.value("dB") == pytest.approx(10 * math.log10(2))
    _, r = run("decibels", {"db": "30"}, {"kind": "dbm", "dir": "from_db"})
    assert r.value("Value") == pytest.approx(1.0)
    _, r = run("decibels", {"x": "0.775"}, {"kind": "dbu"})
    assert r.value("dB") == pytest.approx(20 * math.log10(0.775 / math.sqrt(0.6)))


def test_transformer_ideal_limits_and_thevenin():
    _, r = run("transformer_sim", {"Vp": "230", "f": "60", "Np": "1000", "Ns": "100", "RL": "4"})
    assert r.value("V2") == pytest.approx(23) and r.value("I2") == pytest.approx(5.75) and r.value("η") == pytest.approx(100)
    # winding resistances only: secondary sees Rth = R1/a² + R2 in series with the load
    _, r = run("transformer_sim", {"Vp": "230", "f": "60", "Np": "1000", "Ns": "100", "R1": "2", "R2": "0.03", "RL": "4"})
    rth = 2 / 100 + 0.03
    assert r.value("I2") == pytest.approx(23 / (rth + 4))
    assert r.value("P2 max") == pytest.approx(23 ** 2 / (4 * rth))
    assert r.value("P_cu2") == pytest.approx(r.value("I2") ** 2 * 0.03)
    assert r.value("Φmax") == pytest.approx(r.value("E2") * 10 / (4.44 * 60 * 1000))
    with pytest.raises(CalcError, match="short-circuits"):
        run("transformer_sim", {"Vp": "230", "f": "60", "Np": "1000", "Ns": "100", "RL": "0"})


def test_relay_times():
    _, r = run("relay", {"Vs": "12", "Rcoil": "400", "Lcoil": "0.5", "Vpull": "9", "Vdrop": "1.2", "t_on": "50", "t_mech": "3"})
    tau = 0.5 / 400
    assert r.value("t_pick") == pytest.approx(-tau * math.log(1 - 0.0225 / 0.03) + 3e-3)
    assert r.value("t_rel") == pytest.approx(tau * math.log(0.03 / 0.003) + 3e-3)
    _, r = run("relay", {"Vs": "6", "Rcoil": "400", "Lcoil": "0.5", "Vpull": "9", "Vdrop": "1.2", "t_on": "50"})
    assert "NOT pull in" in next(o.text for o in r.outputs if o.symbol == "State")
    _, r = run("relay", {"Vs": "12", "Rcoil": "400", "Lcoil": "0.5", "Vpull": "9", "Vdrop": "1.2", "t_on": "50"}, {"flyback": "none"})
    assert any("No flyback" in w for w in r.warnings)


def test_delta_wye_round_trip():
    _, r = run("delta_wye", {"Rab": "30", "Rbc": "60", "Rca": "90"}, unknown="to_y")
    ra, rb, rc = r.value("Ra"), r.value("Rb"), r.value("Rc")
    assert (ra, rb, rc) == pytest.approx((15, 10, 30))
    _, r = run("delta_wye", {"Ra": str(ra), "Rb": str(rb), "Rc": str(rc)}, unknown="to_delta")
    assert (r.value("Rab"), r.value("Rbc"), r.value("Rca")) == pytest.approx((30, 60, 90))


def test_colour_code_and_e_series():
    _, r = run("colour_code", {"bands": "yellow violet red gold"})
    assert r.value("R") == 4700 and r.value("Tolerance") == 5
    _, r = run("colour_code", {"bands": "brown black black red brown"})
    assert r.value("R") == 10000 and r.value("Tolerance") == 1
    _, r = run("colour_code", {"R": ("4.7", "kΩ")}, unknown="encode")
    assert r.outputs[0].text == "yellow – violet – red – gold"
    _, r = run("eseries", {"x": ("5", "kΩ")})
    assert r.value("Nearest") == pytest.approx(5100) and r.value("Next lower") == pytest.approx(4700)


def test_wire_resistance_matches_awg_table():
    # 14 AWG copper: 8.286 mΩ/m at 20 °C (published table); 10 m round trip = 20 m
    _, r = run("wire", {"length": ("10", "m"), "awg": "14"})
    assert r.value("R") == pytest.approx(20 * 8.286e-3, rel=0.005)


def test_led_and_dividers():
    _, r = run("led", {"Vs": "5", "Vf": "2", "If": "20"})
    assert r.value("R") == pytest.approx(150) and r.value("R (E24, ≥)") == pytest.approx(150)
    _, r = run("current_divider", {"It": "100", "r1": "100", "r2": "300"})
    assert r.value("I1") == pytest.approx(0.075) and r.value("I2") == pytest.approx(0.025)
    _, r = run("thevenin", {"Vth": "12", "Rth": "100", "RL": "100"})
    assert r.value("In") == pytest.approx(0.12) and r.value("PL") == pytest.approx(0.36)
    _, r = run("stored_energy", {"C": "100", "V": "50"})
    assert r.value("E") == pytest.approx(0.125)
    _, r = run("battery", {"cap": "2500", "I": "150", "eff": "80"})
    assert r.value("hours") == pytest.approx(2500 / 150 * 0.8)
