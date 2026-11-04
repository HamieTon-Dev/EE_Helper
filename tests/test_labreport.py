import json
import math
import shutil
import zipfile
from pathlib import Path

import pytest

from eesimlab import config as cfg
from eesimlab.labexpr import Context, ExprError, fmt_value
from eesimlab.labreport import build_lab_report, collect_errors, open_template, Profile
from eesimlab.plan import Plan

LAB = Path(__file__).resolve().parent.parent / "examples" / "series_rc_lab"
NGSPICE = shutil.which("ngspice") or shutil.which("ngspice_con")
needs_ngspice = pytest.mark.skipif(not NGSPICE, reason="ngspice not installed")


def _simulate(tmp_path):
    from eesimlab import plots
    from eesimlab.engines.ngspice_engine import run_plan

    plan = Plan.load(LAB / "plan.json")
    results = run_plan(plan, NGSPICE, tmp_path, log=lambda *_: None)
    images = {}
    for r in results:
        for f in plots.render(r, tmp_path / "plots"):
            images[f"analysis:{r.analysis.id}"] = f
    return plan, results, images, Context({r.analysis.id: r for r in results}, plan.constants_dict())


def test_example_lab_plan_valid():
    assert Plan.load(LAB / "plan.json").validate() == []


@needs_ngspice
def test_series_rc_values_match_theory(tmp_path):
    plan, results, images, ctx = _simulate(tmp_path)
    # Part I: 1 Vrms, 1k, 0.1u at 1 kHz
    xc = 1 / (2 * math.pi * 1000 * 1e-7)
    vr = 1000 / math.hypot(1000, xc)
    assert ctx.eval("mag('p1_dmm','v(n1,n2)')") == pytest.approx(vr, rel=1e-4)
    assert ctx.eval("mag('p1_sweep','v(n2)',15000)") == pytest.approx(
        (1 / (2 * math.pi * 15000 * 1e-7)) / math.hypot(1000, 1 / (2 * math.pi * 15000 * 1e-7)), rel=1e-4)
    # Part II/III: 6.8k, 0.01u, 500 Hz -> theta = 77.94 deg, lag = 12.06 deg
    theta = math.degrees(math.atan((1 / (2 * math.pi * 500 * 1e-8)) / 6800))
    assert ctx.eval("meas('p3_phase','theta')") == pytest.approx(theta, abs=0.05)
    assert ctx.eval("meas('p3_lag','phi')") == pytest.approx(-(90 - theta), abs=0.05)
    assert ctx.eval("meas('p3_phase','T')") == pytest.approx(2e-3, rel=1e-3)
    assert ctx.eval("meas('p2_scope_vr','VR peak')") == pytest.approx(1.5 * 6800 / math.hypot(6800, 1 / (2 * math.pi * 500 * 1e-8)), rel=1e-3)


@needs_ngspice
def test_example_lab_report_renders(tmp_path):
    plan, results, images, ctx = _simulate(tmp_path)
    report = json.loads((LAB / "lab_report.json").read_text(encoding="utf-8"))
    assert collect_errors(report, ctx, images) == []
    prof = Profile(course="Circuits Lab", instructor="Dr. Example", student="Pat Student",
                   references=["NI. (2024). Oscilloscope. duplicate entry", "Example, A. (2020). Extra source."])
    path, errors = build_lab_report(report, ctx, images, prof, tmp_path / "lab.docx")
    assert errors == []
    import docx

    d = docx.Document(str(path))
    text = "\n".join(p.text for p in d.paragraphs)
    for needle in ("Circuits Lab", "Instructor: Dr. Example", "Pat Student", "Abstract",
                   "Introduction", "Procedures", "Data Presentation & Analysis", "Calculations", "Required Screenshots",
                   "Conclusion", "References", "Table 4: Phase angle and phase lag measurements", "Plot 2: Impedance Phasor",
                   "Figure 4: Screenshot of Waveforms for Part 3 Step 15", "15.\t"):
        assert needle in text, needle
    assert "{{" not in text and "—" not in text
    assert text.count("NI. (2024). Oscilloscope") == 1  # settings reference de-duplicated against the AI's
    assert "Example, A. (2020). Extra source." in text
    assert "pledge" not in text.lower()
    assert "77.94" in text and "Part I step 5: XC = 1/(2πfC) = 1/(2 × π × 1,000 × 1×10⁻⁷) = 1591.55 Ω" in text
    cells = [c.text for t in d.tables for row in t.rows for c in row.cells]
    assert "532.018 mV" in cells and "0.1852" in cells and "12.059°" in cells


def test_expression_sandbox():
    ctx = Context({}, {"R1": 1000.0})
    assert ctx.eval("2*R1 + sqrt(16)") == 2004
    for bad in ("__import__('os')", "R1.__class__", "open('x')", "[1,2]", "lambda: 1"):
        with pytest.raises(ExprError):
            ctx.eval(bad)


def test_formatting():
    assert fmt_value(0.000532018, "A", "eng3") == "532.018 µA"
    assert fmt_value(77.9412, "°", ".2f") == "77.94°"
    assert fmt_value(1591.549, "Ω", ".2f") == "1591.55 Ω"
    assert fmt_value(3.1e9, "", "sci3") == "3.100×10⁹"


def _identifying_docx(path: Path):
    """A previous-report-like file full of things that must never reach a new report."""
    import docx
    from docx.shared import Inches
    from PIL import Image

    logo = path.parent / "logo.png"
    Image.new("RGB", (80, 40), "blue").save(logo)
    d = docx.Document()
    hp = d.sections[0].header.paragraphs[0]
    hp.add_run("Example University").bold = True
    hp.add_run().add_picture(str(logo), width=Inches(1))
    d.sections[0].footer.paragraphs[0].add_run("Example University confidential")
    d.core_properties.author = "Jane Doe"
    d.core_properties.title = "Week 9 Lab"
    d.add_paragraph().add_run().add_picture(str(logo), width=Inches(1))
    d.add_paragraph("Instructor: Prof. Somebody")
    d.add_paragraph("Student Name(s):\tJane Doe")
    d.add_heading("Abstract", 1)
    d.add_paragraph("old content")
    d.save(path)


def test_style_template_keeps_nothing_identifying(tmp_path):
    src = tmp_path / "old_report.docx"
    _identifying_docx(src)
    doc = open_template(str(src))
    out = tmp_path / "t.docx"
    doc.save(out)
    z = zipfile.ZipFile(out)
    assert not [n for n in z.namelist() if "media" in n]  # header logo and cover image are gone
    everything = "".join(z.read(n).decode("utf-8", "ignore") for n in z.namelist() if n.endswith(".xml"))
    for needle in ("Example University", "Jane Doe", "Week 9 Lab", "Prof. Somebody", "old content"):
        assert needle not in everything, needle
    assert "PAGE" in z.read("word/footer1.xml").decode()  # page numbers are still there


class LabProvider:
    name = "fake"

    def __init__(self):
        self.calls = []

    def complete_json(self, prompt):
        self.calls.append(prompt)
        if prompt.schema_name == "simulation_plan":
            return json.loads((LAB / "plan.json").read_text(encoding="utf-8"))
        if prompt.schema_name == "lab_report":
            rep = json.loads((LAB / "lab_report.json").read_text(encoding="utf-8"))
            if len([c for c in self.calls if c.schema_name == "lab_report"]) == 1:
                rep["data"][0]["rows"][0][1] = "=mag('nope','v(x)') | V"  # force one repair round
            return rep
        raise AssertionError(prompt.schema_name)


@needs_ngspice
def test_pipeline_lab_mode_with_repair(tmp_path):
    from eesimlab.inputs import load_package
    from eesimlab.pipeline import Pipeline

    s = cfg.Settings(output_root=str(tmp_path), ngspice_path=NGSPICE, use_multisim_com=False, use_multisim_gui=False,
                     report_style="lab", course="Circuits Lab", instructor="Dr. Example", student="Pat")
    fake = LabProvider()
    pkg = load_package([LAB / "handout.txt"])
    out = Pipeline(s, log=lambda *_: None, provider=fake).run(pkg, input_paths=[LAB / "handout.txt"])
    assert out.lab_report and out.lab_report.exists() and out.report_html is None
    assert [c.schema_name for c in fake.calls] == ["simulation_plan", "lab_report", "lab_report"]
    assert "unknown analysis id 'nope'" in fake.calls[-1].text
    assert (out.folder / "lab_report.json").exists()
