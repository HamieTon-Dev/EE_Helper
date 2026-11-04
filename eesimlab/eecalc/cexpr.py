"""Complex-number expression parser/evaluator for engineering notation.

Accepted input (spaces optional):
  8 + j3        8+3j       j(3)      -j4.7        rectangular
  29∠-72        29<-72     29@-72    29 ang -72   polar (angle in the selected unit)
  4.7k  10u  2.2M  100n  33p  1G  5m            SI prefixes attached to a number
  + - * / ^ ( )   A B C D (variables)   pi  e
  functions: sqrt conj abs mag arg ang re real im imag exp ln log10 sin cos tan polar rect par
             par(a, b, …) = parallel combination 1/(1/a + 1/b + …)
Precedence: ∠ binds tightest (29∠-72 is one number), then ^, unary minus, * /, + -.
Implicit multiplication is not supported (write 2*j3 or 2j3 → use j3*2), so nothing
ambiguous is ever guessed.
"""
from __future__ import annotations

import cmath
import math
import re

PREFIX = {"y": 1e-24, "z": 1e-21, "a": 1e-18, "f": 1e-15, "p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "μ": 1e-6,
          "m": 1e-3, "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9, "T": 1e12, "P": 1e15, "E": 1e18}

_TOKEN = re.compile(r"""
    (?P<num>(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)(?P<pre>[yzafpnuµμmkKMGTPE](?![A-Za-z(]))?
  | (?P<j>[jJ](?![A-Za-z_]))
  | (?P<name>[A-Za-z_][A-Za-z_0-9]*)
  | (?P<op>∠|<|@|\*\*|[-+*/^(),])
  | (?P<ws>\s+)
""", re.X)


class ExprError(ValueError):
    pass


def tokenize(s: str):
    s = s.replace("−", "-").replace("×", "*").replace("÷", "/").replace("·", "*")
    pos, out = 0, []
    while pos < len(s):
        m = _TOKEN.match(s, pos)
        if not m:
            raise ExprError(f"Unexpected character '{s[pos]}' at position {pos + 1}.")
        pos = m.end()
        if m.lastgroup == "ws":
            continue
        if m.group("num") is not None:
            v = float(m.group("num")) * (PREFIX[m.group("pre")] if m.group("pre") else 1.0)
            out.append(("num", v))
        elif m.group("j"):
            out.append(("j", "j"))
        elif m.group("name"):
            name = m.group("name")
            if name.lower() == "ang":
                out.append(("op", "∠"))
            elif re.fullmatch(r"[jJ](\d+\.?\d*|\.\d+)", name):  # j3 tokenised as a name
                out.append(("j", "j"))
                out.append(("num", float(name[1:])))
            else:
                out.append(("name", name))
        else:
            op = m.group("op")
            out.append(("op", {"<": "∠", "@": "∠", "**": "^"}.get(op, op)))
    return out


class _Parser:
    def __init__(self, tokens, variables, angle_unit):
        self.t = tokens
        self.i = 0
        self.vars = {k.upper(): v for k, v in variables.items()}
        self.deg = angle_unit != "rad"

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else (None, None)

    def take(self, kind=None, val=None):
        tok = self.peek()
        if tok[0] is None or (kind and tok[0] != kind) or (val and tok[1] != val):
            want = val or kind or "a value"
            raise ExprError(f"Expected {want} but found {tok[1] if tok[0] else 'the end of the expression'}.")
        self.i += 1
        return tok

    def parse(self):
        v = self.expr()
        if self.peek()[0] is not None:
            raise ExprError(f"Unexpected '{self.peek()[1]}' (missing operator?).")
        return v

    def expr(self):
        v = self.term()
        while self.peek() in (("op", "+"), ("op", "-")):
            op = self.take()[1]
            r = self.term()
            v = v + r if op == "+" else v - r
        return v

    def term(self):
        v = self.unary()
        while self.peek() in (("op", "*"), ("op", "/")):
            op = self.take()[1]
            r = self.unary()
            if op == "/":
                if r == 0:
                    raise ExprError("Division by zero.")
                v = v / r
            else:
                v = v * r
        return v

    def unary(self):
        if self.peek() == ("op", "-"):
            self.take()
            return -self.unary()
        if self.peek() == ("op", "+"):
            self.take()
            return self.unary()
        return self.power()

    def power(self):
        base = self.polar()
        if self.peek() == ("op", "^"):
            self.take()
            exp = self.unary()
            if base == 0 and (exp.real <= 0 if isinstance(exp, complex) else exp <= 0):
                raise ExprError("0 raised to a non-positive power is undefined.")
            return base ** exp
        return base

    def polar(self):
        v = self.primary()
        if self.peek() == ("op", "∠"):
            self.take()
            sign = 1
            while self.peek() in (("op", "-"), ("op", "+")):
                sign *= -1 if self.take()[1] == "-" else 1
            ang = self.primary()
            if isinstance(ang, complex) and ang.imag != 0:
                raise ExprError("A polar angle must be a real number.")
            mag = v.real if isinstance(v, complex) else v
            if isinstance(v, complex) and v.imag != 0:
                raise ExprError("A polar magnitude must be a real number.")
            a = sign * (ang.real if isinstance(ang, complex) else ang)
            return cmath.rect(mag, math.radians(a) if self.deg else a)
        return v

    def primary(self):
        kind, val = self.peek()
        if kind == "num":
            self.take()
            if self.peek()[0] == "j":  # 3j
                self.take()
                return complex(0, val)
            return val
        if kind == "j":
            self.take()
            nxt = self.peek()
            if nxt[0] == "num" or nxt == ("op", "("):
                return 1j * self.primary()
            return 1j
        if kind == "op" and val == "(":
            self.take()
            v = self.expr()
            self.take("op", ")")
            return v
        if kind == "name":
            self.take()
            name = val
            if self.peek() == ("op", "("):
                self.take()
                args = [self.expr()]
                while self.peek() == ("op", ","):
                    self.take()
                    args.append(self.expr())
                self.take("op", ")")
                return self.call(name, args)
            up = name.upper()
            if up in self.vars:
                if self.vars[up] is None:
                    raise ExprError(f"{up} is used but has no value.")
                return self.vars[up]
            if name.lower() == "pi":
                return math.pi
            if name == "e":
                return math.e
            raise ExprError(f"Unknown name '{name}'. Use A–D, pi, e or a function.")
        raise ExprError(f"Expected a number but found {val or 'the end of the expression'}.")

    def call(self, name, args):
        n = name.lower()
        one = args[0]
        ang = (lambda z: math.degrees(cmath.phase(z)) if self.deg else cmath.phase(z))
        fns1 = {
            "sqrt": cmath.sqrt, "conj": lambda z: complex(z).conjugate(), "abs": abs, "mag": abs,
            "arg": lambda z: ang(complex(z)), "ang": lambda z: ang(complex(z)), "re": lambda z: complex(z).real,
            "real": lambda z: complex(z).real, "im": lambda z: complex(z).imag, "imag": lambda z: complex(z).imag,
            "exp": cmath.exp, "ln": cmath.log, "log10": cmath.log10,
            "sin": lambda z: cmath.sin(self._rad(z)), "cos": lambda z: cmath.cos(self._rad(z)),
            "tan": lambda z: cmath.tan(self._rad(z)),
        }
        if n in fns1:
            if len(args) != 1:
                raise ExprError(f"{name}() takes one argument.")
            if n in ("ln", "log10") and one == 0:
                raise ExprError(f"{name}(0) is undefined.")
            return fns1[n](one)
        if n == "polar":
            if len(args) != 2:
                raise ExprError("polar(magnitude, angle) takes two arguments.")
            return cmath.rect(_real(args[0]), math.radians(_real(args[1])) if self.deg else _real(args[1]))
        if n == "rect":
            if len(args) != 2:
                raise ExprError("rect(real, imag) takes two arguments.")
            return complex(_real(args[0]), _real(args[1]))
        if n == "par":
            if len(args) < 2:
                raise ExprError("par() needs at least two impedances.")
            if any(a == 0 for a in args):
                return 0.0
            s = sum(1 / a for a in args)
            if s == 0:
                raise ExprError("par(): the admittances cancel (ideal resonance) - the result is infinite.")
            return 1 / s
        raise ExprError(f"Unknown function '{name}'.")

    def _rad(self, z):
        return math.radians(z) if self.deg and not isinstance(z, complex) else z


def _real(v):
    if isinstance(v, complex):
        if v.imag != 0:
            raise ExprError("Expected a real number.")
        return v.real
    return v


def evaluate(text: str, variables: dict | None = None, angle_unit: str = "°") -> complex:
    if not (text or "").strip():
        raise ExprError("The expression is empty.")
    toks = tokenize(text)
    v = _Parser(toks, variables or {}, angle_unit).parse()
    v = complex(v)
    if any(math.isnan(x) or math.isinf(x) for x in (v.real, v.imag)):
        raise ExprError("The result is not finite.")
    return v


def parse_value(text: str, angle_unit: str = "°") -> complex | None:
    """A single operand such as '8+j3' or '29∠-72' (blank -> None)."""
    if not (text or "").strip():
        return None
    return evaluate(text, {}, angle_unit)


def clean(z: complex, eps: float = 1e-12) -> complex:
    """Remove floating-point dust (e.g. 1e-16 imaginary parts) relative to the magnitude."""
    m = abs(z)
    re_, im_ = z.real, z.imag
    if abs(re_) <= eps * max(m, 1e-300):
        re_ = 0.0
    if abs(im_) <= eps * max(m, 1e-300):
        im_ = 0.0
    return complex(re_, im_)
