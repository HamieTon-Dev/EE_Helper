"""The simulation plan: what the AI produces and what the engines execute.

A plan is plain JSON so it can be reviewed and edited by hand before anything runs.

A plan has a base ``netlist`` and optional ``variants`` (complete alternative netlists, e.g.
"C swapped to 470 pF" or "source at 5 kHz"). Each analysis runs on the base circuit or on
one variant. ``constants`` hold named component values (R1, C1, L1 ...) that report
formulas can use.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

ANALYSIS_KINDS = ("op", "dc", "ac", "ac_points", "tran")
INSTRUMENTS = ("multimeter", "bode", "oscilloscope", "table")
MEASUREMENT_TYPES = (
    "dc_value",  # value of signal in an op analysis (or the mean in others)
    "max",
    "min",
    "vpp",
    "vpk",  # peak (max |v|) (tran)
    "vrms",
    "vavg",
    "frequency",  # fundamental frequency from zero crossings (tran)
    "period",  # T between successive rising crossings (tran)
    "delta_t",  # time from reference's rising crossing to signal's rising crossing (tran)
    "phase_shift_deg",  # delta_t/T*360, positive = signal leads reference (tran)
    "rise_time",  # 10-90 % (tran)
    "gain_db_at",  # |sig/ref| in dB at frequency `at` (ac, ac_points)
    "phase_at",  # phase(sig/ref) in degrees at frequency `at` (ac, ac_points)
    "mag_at",  # |sig/ref| (linear) at frequency `at` (ac, ac_points)
    "max_gain_db",  # peak gain (ac)
    "f_peak",  # frequency of maximum |sig/ref| - series resonance (ac)
    "f_min",  # frequency of minimum |sig/ref| - parallel resonance / notch (ac)
    "f_3db",  # first -3 dB crossing relative to the passband peak (ac)
    "value_at",  # interpolated value at sweep/time point `at`
)
CURSOR_MEASUREMENTS = ("delta_t", "phase_shift_deg", "period")

_NUM = {"type": ["number", "null"]}
_STR_OR_NULL = {"type": ["string", "null"]}

_MEAS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "type", "signal", "reference", "at", "expected", "tolerance_pct", "unit"],
    "properties": {
        "name": {"type": "string"},
        "type": {"type": "string", "enum": list(MEASUREMENT_TYPES)},
        "signal": {"type": "string"},
        "reference": _STR_OR_NULL,
        "at": _NUM,
        "expected": _NUM,
        "tolerance_pct": _NUM,
        "unit": {"type": "string"},
    },
}

# JSON schema used for AI structured output. Every property is required and nullable values
# are expressed as ["type","null"] so it works with strict structured-output modes.
PLAN_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "summary", "assumptions", "constants", "netlist", "variants", "analyses", "design_notes"],
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "constants": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "value", "unit"],
                "properties": {"name": {"type": "string"}, "value": {"type": "number"}, "unit": {"type": "string"}},
            },
        },
        "netlist": {"type": "string"},
        "variants": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "title", "netlist"],
                "properties": {"id": {"type": "string"}, "title": {"type": "string"}, "netlist": {"type": "string"}},
            },
        },
        "design_notes": {"type": "array", "items": {"type": "string"}},
        "analyses": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "id", "title", "variant", "kind", "instrument", "signals", "reference_signal",
                    "f_start", "f_stop", "points_per_decade", "frequencies",
                    "t_step", "t_stop", "t_start",
                    "dc_source", "dc_start", "dc_stop", "dc_step",
                    "measurements",
                ],
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "variant": _STR_OR_NULL,
                    "kind": {"type": "string", "enum": list(ANALYSIS_KINDS)},
                    "instrument": {"type": "string", "enum": list(INSTRUMENTS)},
                    "signals": {"type": "array", "items": {"type": "string"}},
                    "reference_signal": _STR_OR_NULL,
                    "f_start": _NUM,
                    "f_stop": _NUM,
                    "points_per_decade": {"type": ["integer", "null"]},
                    "frequencies": {"type": ["array", "null"], "items": {"type": "number"}},
                    "t_step": _NUM,
                    "t_stop": _NUM,
                    "t_start": _NUM,
                    "dc_source": _STR_OR_NULL,
                    "dc_start": _NUM,
                    "dc_stop": _NUM,
                    "dc_step": _NUM,
                    "measurements": {"type": "array", "items": _MEAS_SCHEMA},
                },
            },
        },
    },
}


@dataclass
class Measurement:
    name: str
    type: str
    signal: str
    at: float | None = None
    expected: float | None = None
    tolerance_pct: float | None = None
    unit: str = ""
    reference: str | None = None


@dataclass
class Analysis:
    id: str
    title: str
    kind: str
    instrument: str
    signals: list[str]
    reference_signal: str | None = None
    f_start: float | None = None
    f_stop: float | None = None
    points_per_decade: int | None = None
    t_step: float | None = None
    t_stop: float | None = None
    t_start: float | None = None
    dc_source: str | None = None
    dc_start: float | None = None
    dc_stop: float | None = None
    dc_step: float | None = None
    measurements: list[Measurement] = field(default_factory=list)
    variant: str | None = None
    frequencies: list[float] | None = None


@dataclass
class Variant:
    id: str
    title: str
    netlist: str


@dataclass
class Constant:
    name: str
    value: float
    unit: str = ""


def _pick(cls, d: dict, skip=()) -> dict:
    return {k: d.get(k) for k in cls.__dataclass_fields__ if k in d and k not in skip}


@dataclass
class Plan:
    title: str
    summary: str
    netlist: str
    analyses: list[Analysis]
    assumptions: list[str] = field(default_factory=list)
    design_notes: list[str] = field(default_factory=list)
    variants: list[Variant] = field(default_factory=list)
    constants: list[Constant] = field(default_factory=list)

    # ---- (de)serialisation --------------------------------------------
    @classmethod
    def from_dict(cls, d: dict) -> "Plan":
        analyses = []
        for a in d.get("analyses", []):
            ms = [Measurement(**_pick(Measurement, m)) for m in a.get("measurements", []) or []]
            analyses.append(Analysis(**_pick(Analysis, a, skip=("measurements",)), measurements=ms))
        return cls(
            title=d.get("title", "Untitled design"),
            summary=d.get("summary", ""),
            netlist=d.get("netlist", ""),
            analyses=analyses,
            assumptions=list(d.get("assumptions", []) or []),
            design_notes=list(d.get("design_notes", []) or []),
            variants=[Variant(**_pick(Variant, v)) for v in d.get("variants", []) or []],
            constants=[Constant(**_pick(Constant, c)) for c in d.get("constants", []) or []],
        )

    @classmethod
    def from_json(cls, text: str) -> "Plan":
        return cls.from_dict(json.loads(text))

    @classmethod
    def load(cls, path: str | Path) -> "Plan":
        return cls.from_json(Path(path).read_text(encoding="utf-8"))

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.write_text(self.to_json(), encoding="utf-8")
        return path

    # ---- helpers -------------------------------------------------------
    def netlist_for(self, variant: str | None) -> str:
        if not variant:
            return self.netlist
        for v in self.variants:
            if v.id == variant:
                return v.netlist
        raise KeyError(f"Unknown variant '{variant}'")

    def variant_title(self, variant: str | None) -> str:
        if not variant:
            return "Base circuit"
        return next((v.title for v in self.variants if v.id == variant), variant)

    def constants_dict(self) -> dict[str, float]:
        return {c.name: float(c.value) for c in self.constants}

    # ---- validation ----------------------------------------------------
    def validate(self) -> list[str]:
        """Return a list of human-readable problems (empty list == runnable)."""
        problems: list[str] = []
        nets = [("netlist", self.netlist)] + [(f"variant '{v.id}'", v.netlist) for v in self.variants]
        for label, net in nets:
            if not net.strip():
                problems.append(f"{label}: netlist is empty.")
            if re.search(r"^\s*\.control\b", net, re.I | re.M):
                problems.append(f"{label}: must not contain a .control block; analyses are generated from the plan.")
        vids = set()
        for v in self.variants:
            if not _ID_RE.fullmatch(v.id or ""):
                problems.append(f"Variant '{v.id}': id must be letters/digits/_/- only.")
            if v.id in vids:
                problems.append(f"Variant '{v.id}': duplicate id.")
            vids.add(v.id)
        for c in self.constants:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", c.name or ""):
                problems.append(f"Constant '{c.name}': must be a valid identifier (e.g. R1, C_new).")
        if not self.analyses:
            problems.append("Plan has no analyses.")
        seen: set[str] = set()
        for a in self.analyses:
            tag = f"Analysis '{a.id}'"
            if not _ID_RE.fullmatch(a.id or ""):
                problems.append(f"{tag}: id must be letters/digits/_/- only.")
            if a.id in seen:
                problems.append(f"{tag}: duplicate id.")
            seen.add(a.id)
            if a.variant and a.variant not in vids:
                problems.append(f"{tag}: unknown variant '{a.variant}'.")
            if a.kind not in ANALYSIS_KINDS:
                problems.append(f"{tag}: unknown kind '{a.kind}'.")
            if a.instrument not in INSTRUMENTS:
                problems.append(f"{tag}: unknown instrument '{a.instrument}'.")
            if not a.signals:
                problems.append(f"{tag}: no signals to record.")
            refs = [a.reference_signal] + [m.reference for m in a.measurements]
            for s in a.signals + [r for r in refs if r]:
                if not _SIGNAL_RE.fullmatch(s.strip()):
                    problems.append(f"{tag}: signal '{s}' must look like v(node), v(n1,n2) or i(Vsource).")
            if a.kind == "ac":
                if not (a.f_start and a.f_stop and a.f_stop > a.f_start > 0):
                    problems.append(f"{tag}: AC needs 0 < f_start < f_stop.")
            if a.kind == "ac_points":
                if not a.frequencies or any((f or 0) <= 0 for f in a.frequencies):
                    problems.append(f"{tag}: ac_points needs a list of positive frequencies.")
            if a.kind == "tran":
                if not (a.t_step and a.t_stop and a.t_stop > a.t_step > 0):
                    problems.append(f"{tag}: transient needs 0 < t_step < t_stop.")
            if a.kind == "dc":
                if not a.dc_source or a.dc_start is None or a.dc_stop is None or not a.dc_step:
                    problems.append(f"{tag}: DC sweep needs dc_source, dc_start, dc_stop, dc_step.")
            for m in a.measurements:
                if m.type not in MEASUREMENT_TYPES:
                    problems.append(f"{tag}: measurement '{m.name}' has unknown type '{m.type}'.")
                if m.type in ("gain_db_at", "phase_at", "value_at", "mag_at") and m.at is None:
                    problems.append(f"{tag}: measurement '{m.name}' ({m.type}) needs 'at'.")
                if m.type in ("delta_t", "phase_shift_deg") and not (m.reference or a.reference_signal):
                    problems.append(f"{tag}: measurement '{m.name}' ({m.type}) needs a reference signal.")
        return problems


_ID_RE = re.compile(r"[A-Za-z0-9_\-]+")
_SIGNAL_RE = re.compile(r"(?i)(v\(\s*[\w.:+\-]+\s*(,\s*[\w.:+\-]+\s*)?\)|i\(\s*[\w.:+\-]+\s*\))")


def extract_json_object(text: str) -> dict:
    """Pull the first JSON object out of a model reply (handles ```json fences and prose)."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    start = text.find("{")
    if start < 0:
        raise ValueError("No JSON object found in AI reply.")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])
    raise ValueError("Unterminated JSON object in AI reply.")
