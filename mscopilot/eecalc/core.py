"""Calculator framework: input definitions, results, validation errors and the registry.

Calculation logic never touches the GUI. A calculator receives a CalcInput (all values
already converted to base SI units) and returns a Result made of plain data that the
GUI, the copy-to-clipboard text, the history and the tests all consume.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

from mscopilot.eecalc import units


class CalcError(ValueError):
    """A problem with the inputs that the user can fix (missing, invalid, unsupported)."""


# ---------------------------------------------------------------- definitions
@dataclass
class Field:
    key: str
    symbol: str  # e.g. "R", "fC", "Vin"
    meaning: str  # plain-language definition, e.g. "resistance"
    quantity: str | Callable[[dict], str]  # units quantity (or callable(options) -> quantity)
    default_unit: str | Callable[[dict], str] = ""
    default: str = ""  # default text in the default unit
    optional: bool = False
    show_if: Callable[[dict, str | None], bool] | None = None  # (options, unknown) -> visible?
    hint: str = ""

    def quantity_for(self, options: dict) -> str:
        return self.quantity(options) if callable(self.quantity) else self.quantity

    def unit_for(self, options: dict) -> str:
        if callable(self.default_unit):
            return self.default_unit(options)
        return self.default_unit or units.units_for(self.quantity_for(options))[0]

    def visible(self, options: dict, unknown: str | None) -> bool:
        return self.show_if is None or bool(self.show_if(options, unknown))


@dataclass
class Option:
    key: str
    label: str
    choices: list[tuple[str, str]]  # (value, label)
    default: str = ""

    def __post_init__(self):
        if not self.default:
            self.default = self.choices[0][0]


@dataclass
class Calculator:
    id: str
    category: str
    title: str
    compute: Callable[["CalcInput"], "Result"]
    fields: list[Field]
    options: list[Option] = field(default_factory=list)
    unknowns: list[tuple[str, str]] = field(default_factory=list)  # solve-for choices (key, label)
    description: str = ""
    keywords: str = ""
    diagram: Callable[[dict, str | None], dict | None] | None = None

    def default_options(self) -> dict:
        return {o.key: o.default for o in self.options}

    def default_unknown(self) -> str | None:
        return self.unknowns[0][0] if self.unknowns else None

    def visible_fields(self, options: dict, unknown: str | None) -> list[Field]:
        return [f for f in self.fields if f.visible(options, unknown)]

    def matches(self, text: str) -> bool:
        t = text.lower().strip()
        if not t:
            return True
        hay = " ".join([self.title, self.category, self.description, self.keywords] + [f.symbol + " " + f.meaning for f in self.fields]).lower()
        return all(word in hay for word in t.split())


# ---------------------------------------------------------------- inputs
@dataclass
class CalcInput:
    calc: Calculator
    values: dict[str, float | None]  # base units; None = left blank
    options: dict[str, str]
    unknown: str | None
    raw: dict[str, tuple[str, str]] = field(default_factory=dict)  # key -> (text, unit) as entered

    def field(self, key: str) -> Field:
        return next(f for f in self.calc.fields if f.key == key)

    def label(self, key: str) -> str:
        f = self.field(key)
        return f"{f.symbol} ({f.meaning})"

    def text(self, key: str) -> str:
        return (self.raw.get(key, ("", ""))[0] or "").strip()

    def get(self, key: str, default=None):
        v = self.values.get(key)
        return default if v is None else v

    def need(self, key: str, *, positive=False, nonneg=False, nonzero=False) -> float:
        v = self.values.get(key)
        if v is None:
            raise CalcError(f"Enter {self.label(key)}.")
        check(v, self.label(key), positive=positive, nonneg=nonneg, nonzero=nonzero)
        return v

    def opt(self, key: str, default=None, *, positive=False, nonneg=False) -> float | None:
        v = self.values.get(key)
        if v is None:
            return default
        check(v, self.label(key), positive=positive, nonneg=nonneg)
        return v


def check(v: float, label: str, *, positive=False, nonneg=False, nonzero=False) -> None:
    if positive and not v > 0:
        raise CalcError(f"{label} must be greater than zero.")
    if nonneg and v < 0:
        raise CalcError(f"{label} cannot be negative.")
    if nonzero and v == 0:
        raise CalcError(f"{label} cannot be zero.")


def build_input(calc: Calculator, entries: dict[str, tuple[str, str]], options: dict | None = None,
                unknown: str | None = None) -> CalcInput:
    """Parse the user's text entries {key: (text, unit)} into base units, validating units."""
    opts = calc.default_options()
    opts.update(options or {})
    unknown = unknown if unknown is not None else calc.default_unknown()
    values: dict[str, float | None] = {}
    raw: dict[str, tuple[str, str]] = {}
    for f in calc.visible_fields(opts, unknown):
        text, unit = entries.get(f.key, ("", f.unit_for(opts)))
        raw[f.key] = (text, unit)
        if not (text or "").strip():
            values[f.key] = None
            continue
        qty = f.quantity_for(opts)
        if qty == "text":
            values[f.key] = None  # read from raw by the calculator
            continue
        try:
            num = units.parse_number(text)
        except units.UnitError as e:
            raise CalcError(f"{f.symbol} ({f.meaning}): {e}.") from e
        try:
            values[f.key] = units.to_base(num, qty, unit if unit is not None else "")
        except units.UnitError as e:
            raise CalcError(f"{f.symbol} ({f.meaning}): {e}.") from e
    return CalcInput(calc, values, opts, unknown, raw)


# ---------------------------------------------------------------- results
@dataclass
class Output:
    symbol: str
    meaning: str
    value: float | complex | None
    quantity: str = "number"
    primary: bool = False
    note: str = ""  # e.g. "RMS", "across C", "exact", "high-Q approximation"
    text: str = ""  # pre-formatted value (used when value is not numeric, e.g. 'never reached')


@dataclass
class PlotSpec:
    """Frequency response (kind='freq') or time response (kind='time')."""

    kind: str
    title: str
    # freq: response(f: ndarray) -> complex ndarray (Vout/Vin);  time: func(t) -> ndarray
    func: Callable
    f_min: float = 0.0
    f_max: float = 0.0
    vin: float | None = None  # input amplitude for 'volts' display
    vin_label: str = "V"  # e.g. "V (RMS)"
    markers: list[tuple[str, float]] = field(default_factory=list)  # (label, x)
    band: tuple[float, float] | None = None  # shaded bandwidth (f1, f2)
    hlines: list[tuple[str, float]] = field(default_factory=list)  # (label, y) in ratio units (freq) / y units (time)
    y_label: str = ""
    y_quantity: str = "voltage"
    ratio_label: str = "Vout/Vin"
    t_max: float = 0.0


@dataclass
class Result:
    title: str
    outputs: list[Output] = field(default_factory=list)
    formulas: list[str] = field(default_factory=list)
    substitutions: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    symbols: dict[str, str] = field(default_factory=dict)
    plot: PlotSpec | None = None

    def add(self, symbol, meaning, value, quantity="number", *, primary=False, note="", text=""):
        self.outputs.append(Output(symbol, meaning, value, quantity, primary, note, text))
        return value

    def value(self, symbol: str):
        for o in self.outputs:
            if o.symbol == symbol:
                return o.value
        raise KeyError(symbol)

    def primary(self) -> list[Output]:
        return [o for o in self.outputs if o.primary] or self.outputs[:1]


def fmt_output(o: Output, decimals: int = 3, angle_unit: str = "°") -> str:
    if o.text:
        return o.text
    return units.fmt(o.value, o.quantity, decimals, angle_unit)


def plain_text(calc: Calculator, inp: CalcInput, res: Result, decimals: int = 3, angle_unit: str = "°") -> str:
    """Clean plain-text version of a calculation for the clipboard."""
    lines = [res.title, "=" * len(res.title), ""]
    opts = [(o.label, dict(o.choices).get(inp.options.get(o.key), "")) for o in calc.options]
    if inp.unknown:
        opts.insert(0, ("Solve for", dict(calc.unknowns).get(inp.unknown, inp.unknown)))
    for label, val in opts:
        lines.append(f"{label}: {val}")
    lines.append("")
    lines.append("Given:")
    for f in calc.visible_fields(inp.options, inp.unknown):
        text, unit = inp.raw.get(f.key, ("", ""))
        if text.strip():
            lines.append(f"  {f.symbol} = {text.strip()} {unit}".rstrip() + f"   ({f.meaning})")
    lines.append("")
    lines.append("Result:")
    for o in res.outputs:
        note = f"  [{o.note}]" if o.note else ""
        lines.append(f"  {'* ' if o.primary else '  '}{o.symbol} = {fmt_output(o, decimals, angle_unit)}   ({o.meaning}){note}")
    for title, items in (("Formulas", res.formulas), ("Substituted values", res.substitutions), ("Steps", res.steps),
                         ("Assumptions and limitations", res.assumptions), ("Warnings", res.warnings)):
        if items:
            lines += ["", f"{title}:"] + [f"  {x}" for x in items]
    if res.symbols:
        lines += ["", "Symbols:"] + [f"  {k} = {v}" for k, v in res.symbols.items()]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- registry
REGISTRY: list[Calculator] = []
CATEGORIES = [
    "Complex numbers & conversions",
    "Basic electrical",
    "RC and RL filters",
    "RLC and resonance",
    "Transients",
    "AC tools",
    "Components & practical",
]


def register(calc: Calculator) -> Calculator:
    if any(c.id == calc.id for c in REGISTRY):
        raise ValueError(f"duplicate calculator id {calc.id}")
    REGISTRY.append(calc)
    return calc


def get(calc_id: str) -> Calculator:
    for c in REGISTRY:
        if c.id == calc_id:
            return c
    raise KeyError(calc_id)


def search(text: str) -> list[Calculator]:
    return [c for c in REGISTRY if c.matches(text)]


def run(calc_id: str, entries: dict[str, tuple[str, str] | str | float], options: dict | None = None,
        unknown: str | None = None) -> tuple[CalcInput, Result]:
    """Convenience for tests/scripts: entries may be (text, unit) tuples or plain numbers in
    the field's default unit."""
    calc = get(calc_id)
    opts = calc.default_options()
    opts.update(options or {})
    norm: dict[str, tuple[str, str]] = {}
    for k, v in entries.items():
        f = next((x for x in calc.fields if x.key == k), None)
        if f is None:
            raise KeyError(f"{calc_id} has no field {k}")
        if isinstance(v, tuple):
            norm[k] = (str(v[0]), v[1])
        else:
            norm[k] = (repr(v) if isinstance(v, float) else str(v), f.unit_for(opts))
    inp = build_input(calc, norm, opts, unknown)
    return inp, calc.compute(inp)


# ---------------------------------------------------------------- helpers used by calculators
TWO_PI = 2.0 * math.pi


def db(ratio: float) -> float:
    """20·log10(ratio), returning -inf for a zero ratio instead of raising."""
    if ratio == 0:
        return -math.inf
    if math.isinf(ratio):
        return math.inf
    return 20.0 * math.log10(ratio)


def fmt_db(ratio: float) -> str:
    v = db(ratio)
    return "−∞ dB (no output)" if v == -math.inf else f"{v:.6g} dB"


VOLTAGE_CONVENTION = Option(
    "vconv", "Voltage convention",
    [("rms", "RMS"), ("peak", "Peak (Vp)"), ("pp", "Peak-to-peak (Vpp)")], "rms",
)
CONV_LABEL = {"rms": "RMS", "peak": "peak", "pp": "peak-to-peak"}
CONV_SUFFIX = {"rms": "rms", "peak": "p", "pp": "pp"}


def conv_note(inp: CalcInput) -> str:
    return CONV_LABEL[inp.options.get("vconv", "rms")]


def to_rms(v: float, conv: str) -> float:
    return {"rms": v, "peak": v / math.sqrt(2), "pp": v / (2 * math.sqrt(2))}[conv]


ANGLE_FROM_UNIT_NOTE = "angles are entered in the unit chosen beside each angle field"
