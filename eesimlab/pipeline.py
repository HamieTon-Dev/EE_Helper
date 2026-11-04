"""End-to-end run: inputs -> AI plan -> simulate -> (revise) -> Multisim capture -> report."""
from __future__ import annotations

import datetime as _dt
import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from eesimlab import config as cfg
from eesimlab import plots
from eesimlab.ai import agent
from eesimlab.ai.providers import AIError, Provider, make_provider
from eesimlab.engines.ngspice_engine import SimulationError, run_plan
from eesimlab.inputs import DesignPackage
from eesimlab.plan import Plan
from eesimlab.report import RunRecord, build_docx, build_html, overall_status

Log = Callable[[str], None]


class Cancelled(Exception):
    pass


@dataclass
class RunOutput:
    folder: Path
    report_html: Path | None = None
    report_docx: Path | None = None
    records: list[RunRecord] = field(default_factory=list)
    files: list[Path] = field(default_factory=list)
    lab_report: Path | None = None


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")[:40] or "design"


class Pipeline:
    def __init__(self, settings: cfg.Settings, log: Log = print, provider: Provider | None = None):
        self.s = settings
        self.log = log
        self._provider = provider
        self.cancelled = False

    # ---------------------------------------------------------------- helpers
    def provider(self) -> Provider:
        if self._provider is None:
            self._provider = make_provider(self.s, cfg.get_api_key(self.s.provider), self.log)
        return self._provider

    def _check(self):
        if self.cancelled:
            raise Cancelled()

    def new_run_folder(self, title: str) -> Path:
        stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        folder = self.s.resolved_output_root() / f"{stamp}_{slug(title)}"
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    # ---------------------------------------------------------------- stages
    def plan(self, pkg: DesignPackage) -> Plan:
        self.log(f"Asking {self.s.provider} ({self.s.effective_model}) to plan simulations from: {pkg.describe()} ...")
        plan = agent.plan_design(self.provider(), pkg)
        problems = plan.validate()
        if problems:
            self.log("AI plan has issues:\n  - " + "\n  - ".join(problems))
        else:
            self.log(f"Plan ready: '{plan.title}' with {len(plan.analyses)} analyses.")
        return plan

    def simulate(self, plan: Plan, folder: Path, use_ai_review: bool) -> RunRecord:
        problems = plan.validate()
        if problems:
            raise SimulationError("Plan is not runnable:\n- " + "\n- ".join(problems))
        ngspice = cfg.find_ngspice(self.s)
        if not ngspice:
            raise SimulationError("ngspice was not found. Set its path in Settings (the release zip bundles it in the 'ngspice' folder).")
        results = run_plan(plan, ngspice, folder, name="circuit", log=self.log)
        for r in results:
            plots.render(r, folder / "plots")
            if r.error:
                self.log(f"  {r.analysis.id}: ERROR {r.error}")
            for m in r.measurements:
                self.log(f"  {r.analysis.id}: {m.name} = {m.value if m.value is not None else 'n/a'} {m.unit} [{m.status}]")
        rec = RunRecord(iteration=0, plan=plan, results=results)
        if use_ai_review:
            self._check()
            self.log("Asking the AI to review the results ...")
            try:
                rec.review = agent.review_results(self.provider(), plan, results)
                self.log(f"AI verdict: {rec.review.get('verdict')}")
            except AIError as e:
                self.log(f"AI review failed: {e}")
        return rec

    def multisim_stage(self, rec: RunRecord, folder: Path, pkg: DesignPackage) -> list[Path]:
        produced: list[Path] = []
        shots_dir = folder / "multisim_screenshots"
        decks = sorted(p for p in folder.glob("circuit*.cir") if not p.stem.endswith("_ngspice"))

        # --- COM (Automation API) ---
        if self.s.use_multisim_com and pkg.multisim_files:
            worker = cfg.find_worker(self.s)
            if not worker:
                rec.multisim_notes.append("Multisim COM worker not found; skipped Automation API steps.")
            else:
                produced += self._com(rec, folder, pkg, worker)

        # --- UI automation + screenshots ---
        if not self.s.use_multisim_gui:
            rec.multisim_notes.append("Multisim UI capture disabled in Settings.")
            return produced
        exe = cfg.find_multisim_exe(self.s)
        if sys.platform != "win32" or not exe:
            rec.multisim_notes.append(
                "Multisim not found on this computer, so no Multisim screenshots were taken. "
                "The SPICE deck circuit.cir can be opened in Multisim (File > Open, SPICE netlist) to review the circuit."
            )
            return produced
        from eesimlab.multisim.gui import MultisimGUI, MultisimGUIError

        targets: list[tuple[Path, str]] = [(p, p.stem) for p in pkg.multisim_files]
        if not targets:
            targets = [(d, d.stem) for d in decks]
        for src, tag in targets:
            self._check()
            gui = None
            try:
                gui = MultisimGUI(exe, self.log, self.s.gui_settle_seconds)
                work = folder / "multisim" / src.name
                work.parent.mkdir(exist_ok=True)
                shutil.copy2(src, work)
                gui.open(work)
                if work.suffix.lower() == ".cir":
                    ms14 = work.with_suffix(".ms14")
                    if gui.save_as(ms14):
                        produced.append(ms14)
                        rec.multisim_notes.append(f"Imported the SPICE deck into Multisim and saved {ms14.name}.")
                    else:
                        rec.multisim_notes.append(f"Opened {work.name} in Multisim but could not save it as .ms14 automatically; use File > Save As.")
                else:
                    produced.append(work)
                gui.run_interactive()
                gui.pause()
                rec.multisim_screenshots += gui.capture_all(shots_dir, f"{tag}_run")
                gui.show_grapher()
                rec.multisim_screenshots += gui.capture_all(shots_dir, f"{tag}_grapher")
                rec.multisim_notes.append(f"Ran interactive simulation of {src.name} and captured {len(rec.multisim_screenshots)} screenshot(s).")
            except MultisimGUIError as e:
                rec.multisim_notes.append(f"Multisim UI automation failed for {src.name}: {e}")
            except Exception as e:  # keep the run alive, the report still has SPICE data
                rec.multisim_notes.append(f"Multisim UI step error for {src.name}: {type(e).__name__}: {e}")
            finally:
                if gui is not None:
                    gui.close(save=False)
        return produced

    def _com(self, rec: RunRecord, folder: Path, pkg: DesignPackage, worker: list[str]) -> list[Path]:
        from eesimlab.multisim.com_client import MultisimCOM, MultisimCOMError, format_api_listing

        produced: list[Path] = []
        try:
            with MultisimCOM(worker) as ms:
                self.log("Connecting to Multisim Automation API ...")
                ms.connect()
                for f in pkg.multisim_files:
                    ms.open(str(f))
                    rec.multisim_notes.append(f"Automation API opened {f.name}.")
                    api_txt = folder / "multisim_api_members.txt"
                    if not api_txt.exists():
                        api_txt.write_text(format_api_listing(ms.introspect()), encoding="utf-8")
                        produced.append(api_txt)
                    # optional user-defined calls (Settings > extra > com_calls), e.g. to run
                    # an analysis method specific to their Multisim version
                    for call in self.s.extra.get("com_calls", []):
                        res = ms.call(call.get("target", "circuit"), call["method"], call.get("args", []))
                        rec.multisim_notes.append(f"COM {call['method']} -> {str(res)[:200]}")
        except MultisimCOMError as e:
            rec.multisim_notes.append(f"Multisim Automation API unavailable: {e}")
        except Exception as e:
            rec.multisim_notes.append(f"Multisim Automation API error: {type(e).__name__}: {e}")
        return produced

    # ---------------------------------------------------------------- full run
    def run(self, pkg: DesignPackage, plan: Plan | None = None, use_ai: bool = True, input_paths: list[Path] | None = None,
            lab_json: dict | None = None) -> RunOutput:
        if plan is None:
            if not use_ai:
                raise ValueError("Without AI you must supply a plan JSON.")
            plan = self.plan(pkg)
        self._check()
        out = RunOutput(folder=self.new_run_folder(plan.title))
        self.log(f"Output folder: {out.folder}")
        inputs_dir = out.folder / "inputs"
        inputs_dir.mkdir(exist_ok=True)
        for p in input_paths or []:
            shutil.copy2(p, inputs_dir / Path(p).name)
        if pkg.instructions.strip():
            (inputs_dir / "instructions.txt").write_text(pkg.instructions, encoding="utf-8")

        lab_only = self.s.report_style == "lab"
        iterations = 1 if (lab_only or not use_ai) else max(1, int(self.s.max_design_iterations))
        for i in range(1, iterations + 1):
            self._check()
            it_dir = out.folder / (f"iteration_{i}" if iterations > 1 else "simulation")
            it_dir.mkdir(exist_ok=True)
            plan.save(it_dir / "plan.json")
            self.log(f"--- Iteration {i}: simulating '{plan.title}' ---")
            rec = self.simulate(plan, it_dir, use_ai_review=use_ai and not lab_only)
            rec.iteration = i
            rec.folder = it_dir
            out.records.append(rec)
            verdict = (rec.review or {}).get("verdict") or overall_status(rec.results)
            if verdict != "fail" or i == iterations or not use_ai:
                break
            self.log("Requirements not met; asking the AI to revise the design ...")
            try:
                plan = agent.revise_plan(self.provider(), pkg, plan, rec.results)
            except AIError as e:
                self.log(f"Revision failed: {e}")
                break

        final = out.records[-1]
        final_dir = final.folder or out.folder
        self._check()
        out.files += self.multisim_stage(final, final_dir, pkg)

        if (use_ai or lab_json) and self.s.report_style in ("lab", "both"):
            self._check()
            try:
                out.lab_report = self.lab_report(final, final_dir, pkg, out.folder, lab_json)
            except AIError as e:
                self.log(f"Lab report failed: {e}")

        for p in sorted(out.folder.rglob("*")):
            if p.is_file() and p.suffix.lower() in {".cir", ".ms14", ".csv", ".json", ".log", ".txt"} and p not in out.files:
                out.files.append(p)
        if self.s.report_style != "lab" or not out.lab_report:
            out.report_html = build_html(out.records, out.folder, out.files, pkg.describe())
            try:
                out.report_docx = build_docx(out.records, out.folder, out.files)
            except Exception as e:
                self.log(f"Word report failed: {e}")
            self.log(f"Report written: {out.report_html}")
        return out

    # ---------------------------------------------------------------- lab report
    def lab_images(self, rec: RunRecord, pkg: DesignPackage, folder: Path) -> tuple[dict[str, Path], dict[str, str]]:
        """Image keys the lab writer may reference -> files, plus a description of each."""
        paths: dict[str, Path] = {}
        desc: dict[str, str] = {}
        for r in rec.results:
            for p in r.plots:
                key = f"analysis:{r.analysis.id}"
                paths[key] = p
                desc[key] = f"{r.analysis.instrument} figure of '{r.analysis.title}' ({r.analysis.kind})"
        for p in rec.multisim_screenshots:
            key = f"multisim:{p.stem}"
            paths[key] = p
            desc[key] = "Multisim window capture"
        img_dir = folder / "inputs" / "images"
        for img in pkg.images:
            img_dir.mkdir(parents=True, exist_ok=True)
            ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp"}.get(img.media_type, ".png")
            f = img_dir / (slug(img.name.replace(":", "_")) + ext)
            f.write_bytes(img.data)
            key = f"input:{img.name}"
            paths[key] = f
            desc[key] = "picture from the inputs (attached as 'Image: " + img.name + "')"
        return paths, desc

    def lab_report(self, rec: RunRecord, sim_dir: Path, pkg: DesignPackage, folder: Path, report: dict | None = None) -> Path:
        from eesimlab.inputs import read_docx
        from eesimlab.labexpr import Context
        from eesimlab.labreport import build_lab_report, collect_errors

        images, desc = self.lab_images(rec, pkg, folder)
        ctx = Context({r.analysis.id: r for r in rec.results}, rec.plan.constants_dict())
        style = ""
        if self.s.style_example and Path(self.s.style_example).is_file():
            try:
                style = read_docx(Path(self.s.style_example))[0]
            except Exception as e:
                self.log(f"Could not read style example: {e}")
        if report is None:
            self.log("Asking the AI to write the lab report ...")
            report, errors = agent.write_lab_report(
                self.provider(), pkg, rec.plan, rec.results, desc,
                check=lambda rep: collect_errors(rep, ctx, images), style_example=style, log=self.log,
            )
        else:
            errors = collect_errors(report, ctx, images)
        (folder / "lab_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        name = slug(report.get("title") or rec.plan.title) + "_Lab_Report.docx"
        path, render_errors = build_lab_report(report, ctx, images, self.s.profile(), folder / name)
        for e in sorted(set(errors + render_errors))[:20]:
            self.log(f"  lab report: {e}")
        self.log(f"Lab report written: {path}")
        return path
