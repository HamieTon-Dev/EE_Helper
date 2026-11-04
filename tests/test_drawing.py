"""Drawing analysis & virtual probing: solver, validation, reconstruction, instruments,
persistence and reporting, checked against independently calculated values."""
import math

import numpy as np
import pytest

from mscopilot.drawing import solver as S
from mscopilot.drawing.analysis import SimSettings, analyse
from mscopilot.drawing.instruments import Probe, measure
from mscopilot.drawing.model import Component, DrawingModel, Junction, Node, SourceParams, parse_value
from mscopilot.drawing.project import Workspace
from mscopilot.drawing.reconstruct import build_model
from mscopilot.drawing.validate import static_checks


def mk(comps, nodes, gnd="0"):
    m = DrawingModel(drawing_type="electrical_schematic")
    m.nodes = [Node(n, name=n, ground=(n == gnd), points=[[10.0 * i, 10.0]]) for i, n in enumerate(nodes)]
    m.components = comps
    return m


def V(i, a, b, **k):
    return Component(i, "V", nodes=[a, b], src=SourceParams(**k))


def R(i, a, b, v, **k):
    return Component(i, "R", value=v, nodes=[a, b], **k)


def C(i, a, b, v, **k):
    return Component(i, "C", value=v, nodes=[a, b], **k)


def L(i, a, b, v):
    return Component(i, "L", value=v, nodes=[a, b])


def divider():
    return mk([V("V1", "in", "0", wave="dc", dc=12.0), R("R1", "in", "out", 10e3), R("R2", "out", "0", 4.7e3)], ["in", "out", "0"])


# ------------------------------------------------------------------ known circuits
def test_voltage_divider():
    s = S.dc_op(divider())
    assert s.v["out"] == pytest.approx(12 * 4.7 / 14.7)
    assert s.i["R1"] == pytest.approx(12 / 14.7e3)
    assert s.i["V1"] == pytest.approx(-12 / 14.7e3)  # branch current + -> - inside the source


def test_series_parallel_network_resistance_and_currents():
    # R1 (100) in series with (R2 220 ∥ R3 330) then R4 (47) to ground; 10 V source
    m = mk([V("V1", "a", "0", wave="dc", dc=10.0), R("R1", "a", "b", 100), R("R2", "b", "c", 220), R("R3", "b", "c", 330),
            R("R4", "c", "0", 47)], ["a", "b", "c", "0"])
    rp = 220 * 330 / 550
    rt = 100 + rp + 47
    assert S.resistance(m, "a", "0") == 0.0  # source shorted when de-energised
    m2 = mk([c for c in m.components if c.id != "V1"], ["a", "b", "c", "0"])
    assert S.resistance(m2, "a", "0") == pytest.approx(rt)
    s = S.dc_op(m)
    i = 10 / rt
    assert s.i["R1"] == pytest.approx(i)
    assert s.i["R2"] == pytest.approx(i * 330 / 550)
    assert s.i["R3"] == pytest.approx(i * 220 / 550)


@pytest.mark.parametrize("initial", ["zero"])
def test_rc_transient_matches_exponential(initial):
    m = mk([V("V1", "in", "0", wave="step", dc=10.0), R("R1", "in", "out", 1e3), C("C1", "out", "0", 1e-6)], ["in", "out", "0"])
    tr = S.transient(m, 5e-3, 10000, initial)
    tau = 1e-3
    for k in (500, 2000, 6000):
        assert tr.v["out"][k] == pytest.approx(10 * (1 - math.exp(-tr.t[k] / tau)), rel=2e-3, abs=2e-3)


def test_rl_transient_matches_exponential():
    m = mk([V("V1", "in", "0", wave="step", dc=10.0), R("R1", "in", "out", 100), L("L1", "out", "0", 0.1)], ["in", "out", "0"])
    tr = S.transient(m, 5e-3, 10000, "zero")
    tau = 1e-3
    for k in (1000, 4000):
        assert tr.i["L1"][k] == pytest.approx(0.1 * (1 - math.exp(-tr.t[k] / tau)), rel=2e-3)


def test_ac_rc_lowpass_and_series_resonance():
    m = mk([V("V1", "in", "0", wave="sine", amp=1.0, freq=1000.0), R("R1", "in", "out", 1e3),
            C("C1", "out", "0", 1 / (2 * math.pi * 1e3 * 1e3))], ["in", "out", "0"])
    s = S.ac(m, 1000.0)
    assert abs(s.v["out"]) == pytest.approx(1 / math.sqrt(2), rel=1e-9)
    assert math.degrees(np.angle(s.v["out"])) == pytest.approx(-45, abs=1e-6)
    # series RLC: current maximum = V/R at f0
    m = mk([V("V1", "a", "0", wave="sine", amp=1.0, freq=1.0), R("R1", "a", "b", 10), L("L1", "b", "c", 0.1),
            C("C1", "c", "0", 1e-6)], ["a", "b", "c", "0"])
    f0 = 1 / (2 * math.pi * math.sqrt(0.1e-6))
    m.components[0].src.freq = f0
    assert abs(S.ac(m, f0).i["R1"]) == pytest.approx(0.1, rel=1e-6)


def test_switch_states():
    m = mk([V("V1", "a", "0", wave="dc", dc=5.0), Component("S1", "SW", nodes=["a", "b"], state="closed"), R("R1", "b", "0", 100)],
           ["a", "b", "0"])
    assert S.dc_op(m).i["R1"] == pytest.approx(0.05)
    m.components[1].state = "open"
    findings = static_checks(m)
    sol = S.dc_op(m)
    assert sol.i["S1"] == 0 and sol.i["R1"] == pytest.approx(0.0)
    assert not any(f.rule == "unsupported" for f in findings)


# ------------------------------------------------------------------ validation & failure handling
def test_floating_circuit_detected_and_solver_refuses_cleanly():
    m = mk([V("V1", "a", "0", wave="dc", dc=5.0), R("R1", "a", "0", 1e3), R("R2", "x", "y", 1e3), R("R3", "y", "x", 2e3)],
           ["a", "x", "y", "0"])
    f = [x for x in static_checks(m) if x.rule == "floating"]
    assert f and f[0].severity == "problem"
    sol = S.dc_op(m)  # floating island is reported, not given an invented voltage
    assert math.isnan(sol.v["x"]) and set(sol.floating) == {"x", "y"}


def test_open_circuit_and_missing_ground():
    m = mk([V("V1", "a", "0", wave="dc", dc=5.0), R("R1", "a", "b", 1e3)], ["a", "b", "0"])
    assert any(f.rule == "open" and f.severity == "problem" for f in static_checks(m))
    m.nodes[2].ground = False
    assert any(f.rule == "ground" and f.severity == "missing" for f in static_checks(m))
    with pytest.raises(S.SolverError, match="No ground"):
        S.dc_op(m)


def test_voltage_source_loop_and_short():
    m = mk([V("V1", "a", "0", wave="dc", dc=5.0), V("V2", "a", "0", wave="dc", dc=3.0)], ["a", "0"])
    assert any(f.rule == "loop" for f in static_checks(m))
    with pytest.raises(S.SolverError, match="loop"):
        S.dc_op(m)
    m = mk([V("V1", "a", "a", wave="dc", dc=5.0), R("R1", "a", "0", 1)], ["a", "0"])
    assert any("short-circuited" in f.title for f in static_checks(m))


def test_current_source_without_dc_path():
    m = mk([Component("I1", "I", nodes=["0", "a"], src=SourceParams("dc", dc=1e-3)), C("C1", "a", "0", 1e-6)], ["a", "0"])
    with pytest.raises(S.SolverError, match="no DC return path"):
        S.dc_op(m)


def test_unreadable_value_never_guessed():
    assert parse_value("4k7", "R") == (4700.0, "")
    v, why = parse_value("4?7", "R")
    assert v is None and "cannot read" in why
    m = divider()
    m.components[1].value = None
    m.components[1].value_text = "4?7"
    assert any(f.rule == "value" and f.severity == "missing" for f in static_checks(m))
    res = analyse(m, SimSettings())
    assert res.calculations[0].title == "Analysis not run" and res.dc is None


def test_ambiguous_crossing_blocks_until_resolved():
    m = divider()
    m.nodes.append(Node("x", points=[[5, 5]]))
    m.junctions = [Junction("J1", 5, 5, "ambiguous", 0.4, ["out", "x"])]
    assert any(f.rule == "junction" and f.severity == "missing" for f in static_checks(m))
    assert analyse(m, SimSettings()).dc is None
    m.set_junction("J1", "crossing")
    assert not any(f.rule == "junction" and f.severity == "missing" for f in static_checks(m))
    m2 = divider()
    m2.nodes.append(Node("x", points=[[5, 5]]))
    m2.components.append(R("R9", "x", "0", 1e3))
    m2.junctions = [Junction("J1", 5, 5, "ambiguous", 0.4, ["out", "x"])]
    m2.set_junction("J1", "junction")  # joining puts R9 in parallel with R2
    assert S.dc_op(m2).v["out"] == pytest.approx(12 * (4.7e3 * 1e3 / 5.7e3) / (10e3 + 4.7e3 * 1e3 / 5.7e3))


def test_unsupported_component_is_reported_not_simulated():
    m = divider()
    m.components.append(Component("Q1", "Q", nodes=["in", "out", "0"]))
    assert any(f.rule == "unsupported" for f in static_checks(m))
    with pytest.raises(S.SolverError, match="Unsupported"):
        S.dc_op(m)


def test_ratings_and_polarity_findings():
    m = divider()
    m.components[2].rating_power = 0.001
    m.components.append(C("C1", "0", "out", 1e-6, polarized=True))  # '+' on ground: reverse biased
    res = analyse(m, SimSettings(), run_transient=False)
    rules = {(f.rule, f.severity) for f in res.findings}
    assert ("power", "problem") in rules and ("polarity", "problem") in rules
    assert res.recommendations


# ------------------------------------------------------------------ instruments
def test_ammeter_is_branch_current_not_a_voltage():
    m = divider()
    st = SimSettings()
    am = measure(m, Probe("P1", "ammeter", component="R2"), st)
    vm = measure(m, Probe("P2", "voltmeter", red="out", black="0"), st)
    assert am.unit == "A" and am.value == pytest.approx(12 / 14.7e3)
    assert vm.unit == "V" and vm.value == pytest.approx(12 * 4.7 / 14.7)
    assert "in series with R2" in am.reference and all(r.lines[0].startswith("SIMULATED") for r in (am, vm))


def test_ohmmeter_deenergised_and_scope():
    m = divider()
    om = measure(m, Probe("P1", "ohmmeter", red="out", black="0"), SimSettings())
    assert om.value == pytest.approx(10e3 * 4.7e3 / 14.7e3)
    assert any("DE-ENERGISED" in a for a in om.assumptions)
    m = mk([V("V1", "in", "0", wave="sine", amp=2.0, freq=1000.0), R("R1", "in", "0", 1e3)], ["in", "0"])
    sc = measure(m, Probe("P2", "scope", channels=[["in", "0"]]), SimSettings(duration=2e-3, initial="zero"))
    y = list(sc.waveform["series"].values())[0]
    assert max(y) == pytest.approx(2.0, rel=1e-3)
    ac = measure(m, Probe("P3", "voltmeter", red="in", black="0", mode="ac"), SimSettings())
    assert abs(ac.value) == pytest.approx(2.0) and "1.4142 V RMS" in ac.lines[1]


def test_missing_reference_reported():
    r = measure(divider(), Probe("P1", "voltmeter", red="out", black=None), SimSettings())
    assert not r.ok and r.needs


def test_results_invalidate_on_change():
    m = divider()
    st = SimSettings()
    r = measure(m, Probe("P1", "voltmeter", red="out", black="0"), st)
    assert not r.stale(m, st)
    m.components[1].value = 20e3
    assert r.stale(m, st)
    r2 = measure(m, Probe("P1", "voltmeter", red="out", black="0"), st)
    st.duration = 1.0
    assert r2.stale(m, st)


# ------------------------------------------------------------------ reconstruction from recognition
def test_reconstruct_keeps_unknowns_and_crossings():
    rec = {
        "drawing_type": "electrical_schematic", "drawing_type_confidence": 0.9, "title": "t",
        "components": [
            {"id": "V1", "kind": "battery", "kind_confidence": 0.95, "label": "V1", "value_text": "9V", "value_confidence": 0.9,
             "pins": [[100, 500], [100, 700]], "bbox": [80, 480, 120, 720], "polarity": "pin2", "rating_text": "", "notes": ""},
            {"id": "R1", "kind": "R", "kind_confidence": 0.9, "label": "R1", "value_text": "4k7", "value_confidence": 0.8,
             "pins": [[200, 300], [400, 300]], "bbox": [200, 280, 400, 320], "polarity": None, "rating_text": "", "notes": ""},
            {"id": "R2", "kind": "R", "kind_confidence": 0.5, "label": "R2", "value_text": "", "value_confidence": 0.1,
             "pins": [[500, 400], [500, 600]], "bbox": [480, 400, 520, 600], "polarity": None, "rating_text": "", "notes": ""},
        ],
        "nets": [
            {"name": "top", "pins": ["V1.2", "R1.1"], "points": [[100, 300], [200, 300]], "is_ground": False, "confidence": 0.9},
            {"name": "mid", "pins": ["R1.2", "R2.1"], "points": [[400, 300], [500, 400]], "is_ground": False, "confidence": 0.9},
            {"name": "gnd", "pins": ["V1.1", "R2.2"], "points": [[100, 700], [500, 600]], "is_ground": True, "confidence": 0.9},
        ],
        "junctions": [{"x": 450, "y": 350, "kind": "crossing", "confidence": 0.7, "nets": ["mid", "top"], "note": ""},
                      {"x": 300, "y": 500, "kind": "ambiguous", "confidence": 0.3, "nets": [], "note": "smudged"}],
        "unreadable": [{"bbox": [480, 380, 560, 420], "text_seen": "?", "note": "value of R2"}],
        "dimensions": [], "texts": [], "warnings": ["photo is blurry"],
    }
    m = build_model(rec, lambda x, y: (x * 2, y * 2))  # 0-1000 grid -> 2000 px image
    v1 = m.comp("V1")
    assert v1.src.dc == 9.0 and v1.pins[0] == [200.0, 1400.0]  # pin 2 was '+', reordered to pin 1
    assert v1.nodes[0] == next(n.id for n in m.nodes if n.name == "top")  # pin 2 ('+') was on the 'top' net
    assert m.comp("R1").value == 4700.0
    assert m.comp("R2").value is None  # never guessed
    kinds = {j.kind for j in m.junctions}
    assert kinds == {"crossing", "ambiguous"}
    rules = {f.rule for f in static_checks(m)}
    assert {"value", "junction", "unreadable", "symbol"} <= rules


# ------------------------------------------------------------------ persistence & report
def test_project_roundtrip_and_report(tmp_path):
    from PIL import Image

    from mscopilot.drawing.report import build_report, compare_versions

    img = Image.new("RGB", (400, 300), "white")
    import io

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    ws = Workspace(image_bytes=buf.getvalue(), image_suffix=".png")
    ws.versions[0].model = divider()
    ws.probes.append(Probe("P1", "voltmeter", red="out", black="0"))
    ws.assumptions = "Bench supply set to 12.0 V."
    rev = ws.new_revision("Lower R1")
    rev.model.comp("R1").value = 4.7e3
    p = ws.save(tmp_path / "w.eelab")
    ws2 = Workspace.load(p)
    assert [v.name for v in ws2.versions] == ["Original", "Lower R1"]
    assert ws2.versions[0].model.comp("R1").value == 10e3 and ws2.versions[1].model.comp("R1").value == 4.7e3
    assert ws2.probes[0].red == "out" and ws2.image().size == (400, 300)
    rows = compare_versions(ws2.versions[0].model, ws2.versions[1].model, ws2.settings)
    vout = next(r for r in rows if r[0].startswith("V(out"))
    assert vout[1] != vout[2]
    res = analyse(ws2.model, ws2.settings)
    readings = {"P1": measure(ws2.model, ws2.probes[0], ws2.settings)}
    html = build_report(ws2, ws2.image(), res, readings, rows)
    assert "SIMULATED" in html and "Netlist" in html and "Bench supply" in html and "data:image/png;base64" in html


def test_pdf_and_rotation_geometry(tmp_path):
    from PIL import Image

    from mscopilot.drawing.recognize import load_drawing, rotate_point, unrotate_point

    for ang in (0, 90, 33.5, 180, 270):
        x, y, W, H = rotate_point(120, 45, 400, 300, ang)
        assert unrotate_point(x, y, 400, 300, ang) == pytest.approx((120, 45))
    pdf = tmp_path / "d.pdf"
    Image.new("RGB", (300, 200), "white").save(pdf)
    img, data, suf = load_drawing(pdf)
    assert suf == ".pdf" and img.size[0] > 0


def test_catalog_is_large_and_consistent():
    from mscopilot.drawing import catalog as CAT

    assert len(CAT.CATALOG) > 1500
    assert len({p.key for p in CAT.CATALOG}) == len(CAT.CATALOG)
    r = CAT.search("4.7k 1/4")[0]
    assert r.value == pytest.approx(4700) and r.params["rating_power"] == 0.25
    assert all(p.kind in ("R", "C", "L", "V", "I", "SW", "GND") for p in CAT.CATALOG if p.simulated)
