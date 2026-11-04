"""Safe expression evaluation and number formatting for lab-report tables and calculations.

Every "calculated" or "measured" number in a lab report is an expression evaluated here,
against the simulation results, never a number the AI typed. Supported syntax is plain
arithmetic plus these names:

  constants from the plan (R1, C1, L1, ...), pi, e
  sqrt, sin, cos, tan, asin, acos, atan, atan2, degrees, radians, log, log10, exp, abs,
  min, max, round, rms(x) = x/sqrt(2), pk(x) = x*sqrt(2), par(a, b) = a*b/(a+b)
  mag(aid, sig, f=None, ref=None)  |phasor| of an AC/ac_points signal (optionally sig/ref)
  ph(aid, sig, f=None, ref=None)   phase in degrees
  meas(aid, name)                  value of a named measurement
  val(aid, sig)                    operating-point value (op analysis)

Cell/placeholder syntax:  ``=<expr> | <unit> | <fmt>``  (unit and fmt optional)
fmt: ``eng3`` engineering prefix with 3 decimals (default when a unit is given),
``.2f`` fixed, ``.4g`` significant, ``sci3`` scientific.
"""
from __future__ import annotations

import ast
import math
import re
from dataclasses import dataclass

from matplotlib.ticker import EngFormatter

from mscopilot.engines.results import AnalysisResult

_FUNCS = {
    "sqrt": math.sqrt, "sin": math.sin, "cos": math.cos, "tan": math.tan, "asin": math.asin,
    "acos": math.acos, "atan": math.atan, "atan2": math.atan2, "degrees": math.degrees,
    "radians": math.radians, "log": math.log, "log10": math.log10, "exp": math.exp,
    "abs": abs, "min": min, "max": max, "round": round,
    "rms": lambda x: x / math.sqrt(2), "pk": lambda x: x * math.sqrt(2),
    "par": lambda a, b: a * b / (a + b),
}
_BINOPS = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b, ast.Mult: lambda a, b: a * b,
           ast.Div: lambda a, b: a / b, ast.Pow: lambda a, b: a**b, ast.Mod: lambda a, b: a % b}
_OPSYM = {ast.Add: " + ", ast.Sub: " − ", ast.Mult: " × ", ast.Div: "/", ast.Pow: "^", ast.Mod: " mod "}
_PREC = {ast.Add: 1, ast.Sub: 1, ast.Mult: 2, ast.Div: 2, ast.Mod: 2, ast.Pow: 3}
_PRETTY_FN = {"sqrt": "√", "atan": "tan⁻¹", "asin": "sin⁻¹", "acos": "cos⁻¹"}


class ExprError(ValueError):
    pass


@dataclass
class Context:
    results: dict[str, AnalysisResult]
    constants: dict[str, float]

    # ---- data access functions -----------------------------------------
    def _r(self, aid: str) -> AnalysisResult:
        if aid not in self.results:
            raise ExprError(f"unknown analysis id '{aid}'")
        r = self.results[aid]
        if r.error:
            raise ExprError(f"analysis '{aid}' failed: {r.error}")
        return r

    def mag(self, aid, sig, f=None, ref=None):
        r = self._r(aid)
        z = r.phasor(sig, f)
        if ref:
            z = z / r.phasor(ref, f)
        return abs(z)

    def ph(self, aid, sig, f=None, ref=None):
        r = self._r(aid)
        z = r.phasor(sig, f)
        if ref:
            z = z / r.phasor(ref, f)
        return math.degrees(math.atan2(z.imag, z.real))

    def meas(self, aid, name):
        r = self._r(aid)
        for m in r.measurements:
            if m.name == name:
                if m.value is None:
                    raise ExprError(f"measurement '{name}' in '{aid}' has no value ({m.note})")
                return m.value
        raise ExprError(f"no measurement '{name}' in '{aid}' (have: {', '.join(m.name for m in r.measurements)})")

    def val(self, aid, sig):
        r = self._r(aid)
        vals = r.op_values()
        from mscopilot.engines.results import _find_key

        k = _find_key(vals, sig)
        if k is None:
            raise ExprError(f"'{sig}' not recorded in '{aid}'")
        return vals[k]

    def names(self) -> dict:
        d = dict(_FUNCS)
        d.update({"mag": self.mag, "ph": self.ph, "meas": self.meas, "val": self.val, "pi": math.pi, "e": math.e})
        d.update(self.constants)
        return d

    # ---- evaluation ------------------------------------------------------
    def eval(self, expr: str) -> float:
        try:
            tree = ast.parse(expr.strip(), mode="eval")
        except SyntaxError as e:
            raise ExprError(f"syntax error in '{expr}': {e.msg}") from e
        try:
            v = self._eval(tree.body, self.names())
        except ExprError:
            raise
        except (ZeroDivisionError, ValueError, TypeError, OverflowError, KeyError) as e:
            raise ExprError(f"cannot evaluate '{expr}': {e}") from e
        if isinstance(v, complex):
            v = abs(v)
        if not isinstance(v, (int, float)):
            raise ExprError(f"'{expr}' did not produce a number")
        return float(v)

    def _eval(self, node, names):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float, str)):
            return node.value
        if isinstance(node, ast.Name):
            if node.id not in names:
                raise ExprError(f"unknown name '{node.id}'")
            return names[node.id]
        if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
            return _BINOPS[type(node.op)](self._eval(node.left, names), self._eval(node.right, names))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            v = self._eval(node.operand, names)
            return -v if isinstance(node.op, ast.USub) else v
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            fn = names.get(node.func.id)
            if not callable(fn):
                raise ExprError(f"unknown function '{node.func.id}'")
            args = [self._eval(a, names) for a in node.args]
            kwargs = {k.arg: self._eval(k.value, names) for k in node.keywords}
            return fn(*args, **kwargs)
        raise ExprError(f"unsupported syntax: {ast.dump(node)[:60]}")

    # ---- pretty substitution (for the Calculations section) --------------
    def substituted(self, expr: str) -> str:
        """Render the expression with data calls and constants replaced by their values."""
        tree = ast.parse(expr.strip(), mode="eval")
        return self._pretty(tree.body, self.names(), 0)

    def _pretty(self, node, names, parent_prec) -> str:
        if isinstance(node, ast.Constant):
            return _num(node.value) if isinstance(node.value, (int, float)) else str(node.value)
        if isinstance(node, ast.Name):
            if node.id == "pi":
                return "π"
            if node.id in self.constants:
                return _num(self.constants[node.id])
            return node.id
        if isinstance(node, ast.UnaryOp):
            return "−" + self._pretty(node.operand, names, 4)
        if isinstance(node, ast.BinOp):
            if isinstance(node.op, ast.Pow) and isinstance(node.right, ast.Constant) and node.right.value in (2, 3):
                return self._pretty(node.left, names, 4) + ("²" if node.right.value == 2 else "³")
            p = _PREC.get(type(node.op), 1)
            s = self._pretty(node.left, names, p) + _OPSYM.get(type(node.op), "?") + self._pretty(node.right, names, p + 1)
            return f"({s})" if p < parent_prec else s
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            fn = node.func.id
            if fn in ("mag", "ph", "meas", "val"):
                return _num(self._eval(node, names))
            if fn in ("degrees", "abs") and len(node.args) == 1:  # result already shown in °
                inner = self._pretty(node.args[0], names, 0)
                return inner if fn == "degrees" else f"|{inner}|"
            inner = ", ".join(self._pretty(a, names, 0) for a in node.args)
            return f"{_PRETTY_FN.get(fn, fn)}({inner})"
        return "?"


def _num(v: float) -> str:
    if isinstance(v, int) or (isinstance(v, float) and v.is_integer() and abs(v) < 1e7):
        return f"{int(v):,}"
    a = abs(v)
    if a != 0 and (a < 1e-3 or a >= 1e7):
        mant, exp = f"{v:.6e}".split("e")
        mant = mant.rstrip("0").rstrip(".")
        return f"{mant}×10{_sup(int(exp))}"
    s = f"{v:.6g}"
    if "e" in s:
        mant, exp = s.split("e")
        return f"{mant}×10{_sup(int(exp))}"
    return f"{v:,.6g}" if a >= 1000 else s


_SUP = str.maketrans("-0123456789", "⁻⁰¹²³⁴⁵⁶⁷⁸⁹")


def _sup(n: int) -> str:
    return str(n).translate(_SUP)


def fmt_value(v: float, unit: str = "", fmt: str = "") -> str:
    unit = unit or ""
    fmt = (fmt or ("eng3" if unit and unit not in ("°", "deg", "%", "dB") else ".4g")).strip()
    if fmt.startswith("eng"):
        places = int(fmt[3:] or 3)
        s = EngFormatter(unit=unit.strip(), places=places, sep=" " if unit else "")(v)
        return s.replace("-", "−") if v < 0 else s
    if fmt.startswith("sci"):
        places = int(fmt[3:] or 3)
        s = f"{v:.{places}e}"
        mant, exp = s.split("e")
        out = f"{mant}×10{_sup(int(exp))}"
    else:
        try:
            out = format(v, fmt)
        except ValueError:
            out = f"{v:.4g}"
    if not unit:
        return out
    return f"{out}{unit}" if unit in ("°", "%") else f"{out} {unit}"


_CELL_RE = re.compile(r"^\s*=(?P<expr>[^|]+?)\s*(\|\s*(?P<unit>[^|]*?)\s*)?(\|\s*(?P<fmt>[^|]*?)\s*)?$")
_PLACEHOLDER_RE = re.compile(r"\{\{(.+?)\}\}")


def render_cell(ctx: Context, cell: str, errors: list[str], where: str) -> str:
    if not isinstance(cell, str) or not cell.strip().startswith("="):
        return "" if cell is None else str(cell)
    m = _CELL_RE.match(cell)
    if not m:
        errors.append(f"{where}: cannot parse cell '{cell}'")
        return cell
    try:
        return fmt_value(ctx.eval(m.group("expr")), m.group("unit") or "", m.group("fmt") or "")
    except ExprError as e:
        errors.append(f"{where}: {e}")
        return "—"


def fill_text(ctx: Context, text: str, errors: list[str], where: str) -> str:
    """Replace {{expr | unit | fmt}} placeholders in prose."""

    def rep(mm):
        return render_cell(ctx, "=" + mm.group(1), errors, where)

    return _PLACEHOLDER_RE.sub(rep, text or "")


def eval_number(ctx: Context, s, errors: list[str], where: str) -> float | None:
    if isinstance(s, (int, float)):
        return float(s)
    s = str(s).strip()
    if s.startswith("="):
        s = _CELL_RE.match(s).group("expr") if _CELL_RE.match(s) else s[1:]
    try:
        return ctx.eval(s)
    except ExprError as e:
        errors.append(f"{where}: {e}")
        return None
