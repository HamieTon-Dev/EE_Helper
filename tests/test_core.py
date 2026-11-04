import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from eesimlab import config as cfg
from eesimlab.ai.providers import AnthropicProvider, OpenAIProvider, Prompt
from eesimlab.engines.netlist import clean_netlist, multisim_deck, ngspice_deck
from eesimlab.engines.results import AnalysisResult, compute_measurement, judge, parse_wrdata, MeasurementResult
from eesimlab.inputs import load_package
from eesimlab.plan import PLAN_SCHEMA, Analysis, Measurement, Plan, extract_json_object

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
NGSPICE = shutil.which("ngspice") or shutil.which("ngspice_con")


def example_plan() -> Plan:
    return Plan.load(EXAMPLES / "sallen_key_plan.json")


# ------------------------------------------------------------------ plan
def test_example_plan_is_valid():
    assert example_plan().validate() == []


def test_plan_roundtrip():
    p = example_plan()
    assert Plan.from_json(p.to_json()).to_dict() == p.to_dict()


def test_validation_catches_problems():
    p = Plan(title="x", summary="", netlist=".control\n.endc", analyses=[
        Analysis(id="bad id", title="t", kind="ac", instrument="bode", signals=["out"], f_start=10, f_stop=1),
        Analysis(id="m", title="t", kind="tran", instrument="oscilloscope", signals=["v(o)"], t_step=None, t_stop=1,
                 measurements=[Measurement(name="g", type="gain_db_at", signal="v(o)")]),
    ])
    problems = "\n".join(p.validate())
    for needle in (".control", "letters", "must look like", "f_start", "t_step", "needs 'at'"):
        assert needle in problems


def test_schema_every_property_required():
    def walk(node):
        if node.get("type") == "object":
            assert set(node["required"]) == set(node["properties"]), node
            assert node["additionalProperties"] is False
            for child in node["properties"].values():
                walk(child)
        if node.get("type") == "array":
            walk(node["items"])

    walk(PLAN_SCHEMA)


def test_extract_json_object_from_prose():
    text = 'Sure! Here it is:\n```json\n{"a": {"b": "}"}, "c": [1]}\n```\nthanks'
    assert extract_json_object(text) == {"a": {"b": "}"}, "c": [1]}


# ------------------------------------------------------------------ netlist
def test_clean_netlist_strips_analyses_and_end():
    out = clean_netlist("R1 a 0 1k\n.tran 1u 1m\n.END\n", "T")
    assert out.splitlines()[0] == "* T"
    assert ".tran" not in out and ".END" not in out


def test_decks_contain_analyses():
    p = example_plan()
    ms = multisim_deck(p)
    assert ".op" in ms and ".ac dec 50 10 100000" in ms and ".tran 5e-06 0.01 0.002" in ms and ms.strip().endswith(".end")
    ng = ngspice_deck(p, p.analyses)
    assert ".control" in ng and "wrdata data/bode_out.txt v(out) v(in)" in ng


# ------------------------------------------------------------------ results
def test_parse_wrdata_complex(tmp_path):
    f = tmp_path / "ac.txt"
    f.write_text(" frequency v(out) v(out) v(in) v(in)\n 1 0.5 -0.5 1 0\n 10 0.1 -0.2 1 0\n")
    name, scale, data = parse_wrdata(f, "ac")
    assert name == "frequency" and list(scale) == [1, 10]
    assert data["v(out)"][0] == 0.5 - 0.5j and data["v(in)"][1] == 1


def _tran_result(sig):
    a = Analysis(id="t", title="t", kind="tran", instrument="oscilloscope", signals=["v(o)"], t_step=1e-6, t_stop=1e-2)
    t = np.linspace(0, 1e-2, 20001)
    return AnalysisResult(analysis=a, scale_name="time", scale=t, data={"v(o)": sig(t)})


def test_tran_measurements():
    r = _tran_result(lambda t: 2 * np.sin(2 * np.pi * 1000 * t) + 0.5)
    m = lambda typ, **kw: compute_measurement(r, Measurement(name=typ, type=typ, signal="v(o)", **kw)).value
    assert m("vpp") == pytest.approx(4, rel=1e-3)
    assert m("frequency") == pytest.approx(1000, rel=1e-3)
    assert m("vavg") == pytest.approx(0.5, abs=1e-3)
    assert m("vrms") == pytest.approx(np.sqrt(2 + 0.25), rel=1e-3)


def test_ac_f3db_and_judge():
    a = Analysis(id="a", title="a", kind="ac", instrument="bode", signals=["v(o)"], reference_signal="v(i)", f_start=1, f_stop=1e6)
    f = np.logspace(0, 6, 601)
    h = 1 / (1 + 1j * f / 1000)
    r = AnalysisResult(analysis=a, scale=f, data={"v(o)": 2 * h, "v(i)": np.full_like(f, 2, dtype=complex)})
    res = compute_measurement(r, Measurement(name="fc", type="f_3db", signal="v(o)", expected=1000, tolerance_pct=2))
    assert res.value == pytest.approx(1000, rel=0.01) and res.status == "pass"
    g = compute_measurement(r, Measurement(name="g", type="gain_db_at", signal="v(o)", at=1000))
    assert g.value == pytest.approx(-3.01, abs=0.02) and g.status == "info"


def test_judge_zero_target_is_absolute():
    assert judge(MeasurementResult("x", "gain_db_at", "v", 0.3, "dB", expected=0, tolerance_pct=0.5)) == "pass"
    assert judge(MeasurementResult("x", "gain_db_at", "v", 0.7, "dB", expected=0, tolerance_pct=0.5)) == "fail"


# ------------------------------------------------------------------ inputs
def test_load_docx_with_image(tmp_path):
    import docx
    from PIL import Image

    img = tmp_path / "sch.png"
    Image.new("RGB", (64, 32), "white").save(img)
    d = docx.Document()
    d.add_heading("Brief", 1)
    d.add_paragraph("fc = 1 kHz")
    t = d.add_table(rows=1, cols=2)
    t.rows[0].cells[0].text, t.rows[0].cells[1].text = "R1", "10k"
    d.add_picture(str(img))
    path = tmp_path / "brief.docx"
    d.save(path)
    pkg = load_package([path, img], "extra")
    name, text = pkg.documents[0]
    assert "# Brief" in text and "fc = 1 kHz" in text and "R1 | 10k" in text
    assert len(pkg.images) == 2 and all(i.media_type == "image/png" for i in pkg.images)


# ------------------------------------------------------------------ providers (fake clients)
class _FakeStream:
    def __init__(self, msg):
        self.msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.msg


def test_anthropic_provider_request_shape():
    calls = []
    msg = SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text='{"ok": 1}')])

    def stream(**kw):
        calls.append(kw)
        return _FakeStream(msg)

    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=stream)))
    from eesimlab.inputs import ImageInput

    p = AnthropicProvider("k", "claude-opus-5-5", "high", log=lambda *_: None, client=client)
    out = p.complete_json(Prompt("sys", "hi", [ImageInput("a.png", "image/png", b"\x89PNG")], {"type": "object"}, "x"))
    assert out == {"ok": 1}
    kw = calls[0]
    assert kw["model"] == "claude-opus-5-5" and kw["thinking"] == {"type": "adaptive"}
    assert kw["output_config"]["format"]["type"] == "json_schema" and kw["fallbacks"] == "default"
    blocks = kw["messages"][0]["content"]
    assert blocks[1]["type"] == "image" and blocks[-1] == {"type": "text", "text": "hi"}


def test_openai_provider_falls_back_on_unsupported_format():
    seen = []

    def create(**kw):
        seen.append(kw.get("response_format"))
        if kw.get("response_format", {}).get("type") == "json_schema":
            raise RuntimeError("response_format json_schema unsupported")
        m = SimpleNamespace(content='{"v": 2}', refusal=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=m, finish_reason="stop")])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    p = OpenAIProvider("k", "local-model", "http://localhost:1234/v1", log=lambda *_: None, client=client)
    assert p.complete_json(Prompt("s", "t", [], {"type": "object"}, "x")) == {"v": 2}
    assert [f and f["type"] for f in seen] == ["json_schema", "json_object"]


# ------------------------------------------------------------------ end to end with ngspice
class ScriptedProvider:
    """Plans a deliberately mistuned filter, reviews it as failing, then supplies the fix."""

    name = "fake"

    def __init__(self):
        good = json.loads((EXAMPLES / "sallen_key_plan.json").read_text())
        bad = json.loads(json.dumps(good))
        bad["netlist"] = bad["netlist"].replace("R1 in n1 10k", "R1 in n1 20k").replace("R2 n1 nplus 10k", "R2 n1 nplus 20k")
        self.replies = [bad, {"verdict": "fail", "narrative_markdown": "fc too low", "recommendations": ["R = 10k"]},
                        good, {"verdict": "pass", "narrative_markdown": "## OK\nAll met.", "recommendations": []}]
        self.prompts = []

    def complete_json(self, prompt):
        self.prompts.append(prompt)
        return self.replies.pop(0)


@pytest.mark.skipif(not NGSPICE, reason="ngspice not installed")
def test_full_pipeline_with_revision(tmp_path):
    from eesimlab.pipeline import Pipeline

    s = cfg.Settings(output_root=str(tmp_path), ngspice_path=NGSPICE, max_design_iterations=3, use_multisim_com=False,
                     use_multisim_gui=False, report_style="engineering")
    brief = EXAMPLES / "sallen_key_brief.txt"
    pkg = load_package([brief])
    fake = ScriptedProvider()
    out = Pipeline(s, log=lambda *_: None, provider=fake).run(pkg, input_paths=[brief])
    assert len(out.records) == 2
    first = {m.name: m for r in out.records[0].results for m in r.measurements}
    assert first["Corner frequency"].status == "fail" and first["Corner frequency"].value == pytest.approx(500, rel=0.03)
    final = {m.name: m for r in out.records[-1].results for m in r.measurements}
    assert all(m.status in ("pass", "info") for m in final.values()), final
    html = out.report_html.read_text()
    assert "Overall: PASS" in html and "Design iterations" in html and "data:image/png;base64" in html
    assert out.report_docx and out.report_docx.exists()
    assert (out.folder / "iteration_1" / "circuit.cir").exists() and (out.folder / "inputs" / brief.name).exists()
    assert "Previous plan" in fake.prompts[2].text  # revision prompt carried the failing results
