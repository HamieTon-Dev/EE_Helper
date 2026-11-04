"""Entry point. With no arguments the desktop app opens; subcommands give a scriptable CLI.

    MultisimCopilot.exe                                   # desktop app
    MultisimCopilot.exe run brief.docx schematic.png -t "extra notes"
    MultisimCopilot.exe plan brief.docx -o plan.json      # AI plan only, for review
    MultisimCopilot.exe simulate plan.json                # run a reviewed plan, no AI
    MultisimCopilot.exe doctor                            # check ngspice / Multisim / API key
    MultisimCopilot.exe run lab_handout.docx --report lab # write the course lab report
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from mscopilot import __version__
from mscopilot import config as cfg


def _pkg(files, text):
    from mscopilot.inputs import load_package

    return load_package([Path(f) for f in files], text or "")


def cmd_run(a) -> int:
    from mscopilot.pipeline import Pipeline
    from mscopilot.plan import Plan

    s = cfg.Settings.load()
    if a.out:
        s.output_root = a.out
    if a.iterations is not None:
        s.max_design_iterations = a.iterations
    if a.no_multisim:
        s.use_multisim_gui = s.use_multisim_com = False
    if a.report:
        s.report_style = a.report
    plan = Plan.load(a.plan) if a.plan else None
    pkg = _pkg(a.files, a.text)
    out = Pipeline(s).run(pkg, plan=plan, use_ai=not a.no_ai, input_paths=[Path(f) for f in a.files])
    _print_outputs(out)
    return 0


def _print_outputs(out) -> None:
    print()
    if out.lab_report:
        print(f"Lab report: {out.lab_report}")
    if out.report_html:
        print(f"Report: {out.report_html}")
    print(f"Folder: {out.folder}")


def cmd_plan(a) -> int:
    from mscopilot.pipeline import Pipeline

    s = cfg.Settings.load()
    plan = Pipeline(s).plan(_pkg(a.files, a.text))
    Path(a.output).write_text(plan.to_json(), encoding="utf-8")
    print(f"Plan written to {a.output}. Review/edit it, then run:  MultisimCopilot simulate {a.output}")
    return 0


def cmd_simulate(a) -> int:
    from mscopilot.inputs import DesignPackage
    from mscopilot.pipeline import Pipeline
    from mscopilot.plan import Plan

    s = cfg.Settings.load()
    if a.out:
        s.output_root = a.out
    if a.no_multisim:
        s.use_multisim_gui = s.use_multisim_com = False
    pkg = _pkg(a.files, "") if a.files else DesignPackage()
    lab_json = None
    if a.lab_json:
        import json

        lab_json = json.loads(Path(a.lab_json).read_text(encoding="utf-8"))
        if s.report_style == "engineering":
            s.report_style = "both"
    out = Pipeline(s).run(pkg, plan=Plan.load(a.plan), use_ai=a.review, input_paths=[Path(f) for f in a.files],
                          lab_json=lab_json)
    _print_outputs(out)
    return 0


def cmd_doctor(a) -> int:
    s = cfg.Settings.load()
    print(f"Multisim Copilot {__version__}  (Python {sys.version.split()[0]}, {'frozen' if getattr(sys, 'frozen', False) else 'source'})")
    print(f"Config file      : {cfg.config_dir() / 'config.json'}")
    print(f"AI provider      : {s.provider}  model={s.effective_model}  key={'set' if cfg.get_api_key(s.provider) else 'MISSING'}")
    print(f"ngspice          : {cfg.find_ngspice(s) or 'NOT FOUND'}")
    print(f"Multisim exe     : {cfg.find_multisim_exe(s) or 'not found'}")
    worker = cfg.find_worker(s)
    print(f"COM worker       : {' '.join(worker) if worker else 'not found'}")
    if a.connect and worker:
        from mscopilot.multisim.com_client import MultisimCOM, format_api_listing

        with MultisimCOM(worker) as ms:
            print("Worker           :", ms.request("ping"))
            ms.connect()
            print("Multisim COM     : connected")
            if a.circuit:
                ms.open(str(Path(a.circuit).resolve()))
            print(format_api_listing(ms.introspect()))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="MultisimCopilot", description="AI-planned Multisim/SPICE simulations with automatic reports.")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd")

    r = sub.add_parser("run", help="plan with AI, simulate, review and report")
    r.add_argument("files", nargs="*", help=".docx/.txt/.md briefs, schematic images, .cir netlists, .ms14 circuits")
    r.add_argument("-t", "--text", help="extra instructions")
    r.add_argument("--plan", help="use this plan JSON instead of asking the AI to plan")
    r.add_argument("--no-ai", action="store_true", help="no AI calls (requires --plan)")
    r.add_argument("--iterations", type=int, help="max AI design iterations (default from settings)")
    r.add_argument("--no-multisim", action="store_true", help="skip Multisim automation")
    r.add_argument("-o", "--out", help="output root folder")
    r.add_argument("--report", choices=["lab", "engineering", "both"], help="which report(s) to write (default from settings)")
    r.set_defaults(func=cmd_run)

    pl = sub.add_parser("plan", help="ask the AI for a simulation plan only")
    pl.add_argument("files", nargs="*")
    pl.add_argument("-t", "--text")
    pl.add_argument("-o", "--output", default="plan.json")
    pl.set_defaults(func=cmd_plan)

    sm = sub.add_parser("simulate", help="run an existing plan JSON")
    sm.add_argument("plan")
    sm.add_argument("files", nargs="*", help="optional .ms14 files to open in Multisim")
    sm.add_argument("--review", action="store_true", help="also ask the AI to review the results")
    sm.add_argument("--lab-json", help="render the lab report from this (edited) lab_report.json - no AI needed")
    sm.add_argument("--no-multisim", action="store_true")
    sm.add_argument("-o", "--out")
    sm.set_defaults(func=cmd_simulate)

    c = sub.add_parser("calc", help="open the app on the EE Calculator tab")
    c.set_defaults(func=lambda a: __import__("mscopilot.gui_app", fromlist=["launch"]).launch("calc"))

    dw = sub.add_parser("drawing", help="open the app on the Drawing analysis & schematic tab")
    dw.set_defaults(func=lambda a: __import__("mscopilot.gui_app", fromlist=["launch"]).launch("drawing"))

    d = sub.add_parser("doctor", help="check tools, API key and Multisim connectivity")
    d.add_argument("--connect", action="store_true", help="connect to Multisim and list its Automation API")
    d.add_argument("--circuit", help="open this .ms14 before listing (shows circuit-level members)")
    d.set_defaults(func=cmd_doctor)
    return p


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        from mscopilot.gui_app import launch

        return launch()
    args = build_parser().parse_args(argv)
    if not getattr(args, "func", None):
        build_parser().print_help()
        return 1
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
