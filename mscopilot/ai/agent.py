"""High-level AI tasks: plan a design, review results, revise a failing design."""
from __future__ import annotations

import json

from mscopilot.ai.prompts import (
    LAB_REPAIR,
    LAB_WRITER_SYSTEM,
    PLANNER_SYSTEM,
    PLANNER_USER_PREFIX,
    REVIEW_SCHEMA,
    REVIEWER_SYSTEM,
    REVISE_SUFFIX,
)
from mscopilot.ai.providers import AIError, Prompt, Provider
from mscopilot.inputs import DesignPackage
from mscopilot.plan import PLAN_SCHEMA, Plan

MAX_DOC_CHARS = 200_000


def package_text(pkg: DesignPackage) -> str:
    parts = [PLANNER_USER_PREFIX]
    if pkg.instructions.strip():
        parts.append("## Instructions typed by the engineer\n" + pkg.instructions.strip())
    for name, text in pkg.documents:
        if len(text) > MAX_DOC_CHARS:
            raise AIError(f"Document {name} is very large ({len(text):,} chars). Split it or trim it to the relevant sections.")
        parts.append(f"## Document: {name}\n{text}")
    for name, text in pkg.netlists:
        parts.append(f"## Netlist supplied by the engineer: {name}\n```spice\n{text}\n```")
    for p in pkg.multisim_files:
        parts.append(f"## Multisim circuit supplied: {p.name} (it will also be opened in Multisim; describe it from the other inputs)")
    if pkg.images:
        parts.append(f"{len(pkg.images)} image(s) are attached (schematics, plots, datasheet excerpts).")
    return "\n\n".join(parts)


def plan_design(provider: Provider, pkg: DesignPackage) -> Plan:
    data = provider.complete_json(Prompt(PLANNER_SYSTEM, package_text(pkg), pkg.images, PLAN_SCHEMA, "simulation_plan"))
    return Plan.from_dict(data)


def results_digest(plan: Plan, results) -> str:
    """Compact, model-friendly summary of a simulation run."""
    lines = [f"# Plan: {plan.title}", plan.summary, "", "## Netlist", "```spice", plan.netlist, "```", "", "## Results"]
    for r in results:
        lines.append(f"### {r.analysis.title} ({r.analysis.kind}, {r.analysis.instrument})")
        if r.error:
            lines.append(f"ERROR: {r.error}")
            continue
        for m in r.measurements:
            exp = "" if m.expected is None else f" expected {m.expected:g}{m.unit} ±{m.tolerance_pct or 0:g}%"
            val = "n/a" if m.value is None else f"{m.value:.6g}{m.unit}"
            lines.append(f"- {m.name}: {val}{exp} -> {m.status}{(' (' + m.note + ')') if m.note else ''}")
        lines += _data_lines(r)
    return "\n".join(lines)


def _data_lines(r) -> list[str]:
    import numpy as np

    a = r.analysis
    out: list[str] = []
    if a.kind == "op":
        out += [f"- {k} = {v:.6g}" for k, v in r.op_values().items()]
    elif a.kind == "ac_points" and r.scale is not None:
        sigs = list(r.data)
        out.append("| f (Hz) | " + " | ".join(f"|{s}| , ∠{s}°" for s in sigs) + " |")
        for i, f in enumerate(r.scale):
            cells = [f"{abs(r.data[s][i]):.6g} , {np.degrees(np.angle(r.data[s][i])):.2f}" for s in sigs]
            out.append(f"| {f:g} | " + " | ".join(cells) + " |")
    return out


def review_results(provider: Provider, plan: Plan, results, images=None) -> dict:
    text = "Review these simulation results against the design brief summarised in the plan.\n\n" + results_digest(plan, results)
    return provider.complete_json(Prompt(REVIEWER_SYSTEM, text, images or [], REVIEW_SCHEMA, "engineering_review"))


def revise_plan(provider: Provider, pkg: DesignPackage, plan: Plan, results) -> Plan:
    text = (
        package_text(pkg)
        + "\n\n## Previous plan\n```json\n"
        + json.dumps(plan.to_dict(), indent=1)
        + "\n```\n\n"
        + REVISE_SUFFIX
        + "\n"
        + results_digest(plan, results)
    )
    data = provider.complete_json(Prompt(PLANNER_SYSTEM, text, pkg.images, PLAN_SCHEMA, "simulation_plan"))
    return Plan.from_dict(data)


def lab_digest(plan: Plan, results, image_keys: dict[str, str]) -> str:
    """Everything the lab writer needs: plan (constants, variants, analyses) + data + images."""
    lines = [f"# Plan: {plan.title}", plan.summary, "", "## Assumptions"] + [f"- {a}" for a in plan.assumptions]
    lines += ["", "## Constants (usable by name in expressions)"]
    lines += [f"- {c.name} = {c.value:g} {c.unit}" for c in plan.constants] or ["- (none)"]
    lines += ["", "## Circuits", "### base", "```spice", plan.netlist, "```"]
    for v in plan.variants:
        lines += [f"### variant {v.id}: {v.title}", "```spice", v.netlist, "```"]
    lines += ["", "## Analyses and results (aid = analysis id for mag/ph/meas/val)"]
    for r in results:
        a = r.analysis
        desc = f"### aid '{a.id}': {a.title} - kind {a.kind}, instrument {a.instrument}, circuit {plan.variant_title(a.variant)}"
        if a.kind == "ac_points":
            desc += f", frequencies {', '.join(f'{f:g}' for f in a.frequencies or [])}"
        if a.reference_signal:
            desc += f", reference {a.reference_signal}"
        lines.append(desc)
        lines.append(f"signals recorded: {', '.join(r.data) if r.data else ', '.join(a.signals)}")
        if r.error:
            lines.append(f"ERROR: {r.error}")
            continue
        for m in r.measurements:
            val = "n/a" if m.value is None else f"{m.value:.6g} {m.unit}"
            lines.append(f"- measurement '{m.name}' ({m.type} of {m.signal}): {val} {m.note}")
        lines += _data_lines(r)
    lines += ["", "## Available image keys"]
    lines += [f"- {k}: {desc}" for k, desc in image_keys.items()]
    return "\n".join(lines)


def write_lab_report(provider: Provider, pkg: DesignPackage, plan: Plan, results, image_keys: dict[str, str],
                     check, style_example: str = "", log=print, max_repairs: int = 2) -> tuple[dict, list[str]]:
    """Ask the AI for the LabReport JSON; ``check(report) -> errors`` drives up to
    ``max_repairs`` correction rounds."""
    from mscopilot.labreport import LAB_REPORT_SCHEMA

    base = [
        "Write the lab report for this lab. Return only the JSON object.",
        package_text(pkg).replace(PLANNER_USER_PREFIX, ""),
        "# Simulation data",
        lab_digest(plan, results, image_keys),
    ]
    if style_example.strip():
        base.append("# Style example (a previous report by the same student - match its voice and depth, not its numbers)\n"
                    + style_example[:60_000])
    text = "\n\n".join(base)
    report = provider.complete_json(Prompt(LAB_WRITER_SYSTEM, text, pkg.images, LAB_REPORT_SCHEMA, "lab_report"))
    errors = check(report)
    for attempt in range(max_repairs):
        if not errors:
            break
        log(f"Lab report has {len(errors)} unresolved expression(s); asking the AI to fix them (round {attempt + 1}) ...")
        fix = (text + "\n\n# Your previous answer\n```json\n" + json.dumps(report) + "\n```\n\n" + LAB_REPAIR
               + "\n".join(f"- {e}" for e in errors[:60]))
        report = provider.complete_json(Prompt(LAB_WRITER_SYSTEM, fix, [], LAB_REPORT_SCHEMA, "lab_report"))
        errors = check(report)
    return report, errors
