"""Simple schematic drawings for the calculator (tk Canvas).

Diagram specs come from the calculators (plain dicts, see eecalc.basic.ladder):
  ladder      source on the left, series parts along the top rail, shunt branches to the
              bottom (ground) rail, output marked across a shunt (= right-hand terminals)
              or across a series part
  chain       series or parallel network of parts between two terminals
  transformer two coupled windings with the ratio direction marked
"""
from __future__ import annotations

import math

INK = "#1f2937"
ACCENT = "#2563eb"
OUT = "#dc2626"
MUTED = "#64748b"
BODY = 34  # component body length in px


def draw(canvas, spec: dict | None, bg: str = "white") -> None:
    canvas.delete("all")
    w = int(canvas.winfo_width() or canvas["width"])
    h = int(canvas.winfo_height() or canvas["height"])
    canvas.create_rectangle(0, 0, w, h, fill=bg, outline="")
    if not spec:
        canvas.create_text(w / 2, h / 2, text="No diagram for this calculation", fill=MUTED, font=("Segoe UI", 9))
        return
    kind = spec.get("kind")
    if kind == "ladder":
        _ladder(canvas, spec, w, h)
    elif kind == "chain":
        _chain(canvas, spec, w, h)
    elif kind == "transformer":
        _transformer(canvas, w, h)


# ---------------------------------------------------------------- primitives
def _line(c, *pts, color=INK, width=2):
    c.create_line(*pts, fill=color, width=width, capstyle="round", joinstyle="round")


def _part(c, kind, x1, y1, x2, y2, label="", label_side=1):
    """Draw a component centred on the segment (x1,y1)-(x2,y2) with leads to both ends."""
    global BODY
    horiz = abs(y2 - y1) < abs(x2 - x1)
    L = math.hypot(x2 - x1, y2 - y1)
    BODY = max(18, min(34, L - 10))
    if L <= BODY:
        _line(c, x1, y1, x2, y2)
        return
    ux, uy = (x2 - x1) / L, (y2 - y1) / L
    nx, ny = -uy, ux  # normal
    mx, my = (x1 + x2) / 2, (y1 + y2) / 2
    a = (mx - ux * BODY / 2, my - uy * BODY / 2)
    b = (mx + ux * BODY / 2, my + uy * BODY / 2)
    if kind == "C":
        gap = 4
        p1 = (mx - ux * gap, my - uy * gap)
        p2 = (mx + ux * gap, my + uy * gap)
        _line(c, x1, y1, *p1)
        _line(c, *p2, x2, y2)
        for p in (p1, p2):
            _line(c, p[0] - nx * 11, p[1] - ny * 11, p[0] + nx * 11, p[1] + ny * 11, width=3)
    elif kind == "SW":
        _line(c, x1, y1, *a)
        _line(c, *b, x2, y2)
        _line(c, *a, b[0] + nx * 12, b[1] + ny * 12)
        for p in (a, b):
            c.create_oval(p[0] - 3, p[1] - 3, p[0] + 3, p[1] + 3, outline=INK, width=2, fill="white")
    else:
        _line(c, x1, y1, *a)
        _line(c, *b, x2, y2)
        if kind == "R":
            pts = [a]
            n = 6
            for i in range(1, n + 1):
                t = (i - 0.5) / n
                s = 7 * (1 if i % 2 else -1)
                pts.append((a[0] + ux * BODY * t + nx * s, a[1] + uy * BODY * t + ny * s))
            pts.append(b)
            _line(c, *[v for p in pts for v in p])
        elif kind == "L":
            n = 4
            r = BODY / (2 * n)
            for i in range(n):
                cx, cy = a[0] + ux * r * (2 * i + 1), a[1] + uy * r * (2 * i + 1)
                start = 0 if horiz else 270
                c.create_arc(cx - r, cy - r, cx + r, cy + r, start=start if horiz else 90, extent=180 if horiz else -180,
                             style="arc", outline=INK, width=2)
        else:  # generic impedance box
            pad = 9
            c.create_rectangle(min(a[0], b[0]) - (0 if horiz else pad), min(a[1], b[1]) - (pad if horiz else 0),
                               max(a[0], b[0]) + (0 if horiz else pad), max(a[1], b[1]) + (pad if horiz else 0),
                               outline=INK, width=2)
    if label:
        if horiz:
            c.create_text(mx, my - 13, text=label, fill=ACCENT, font=("Segoe UI", 9, "bold"), anchor="s")
        else:
            c.create_text(mx + 15, my, text=label, fill=ACCENT, font=("Segoe UI", 9, "bold"), anchor="w")


def _dot(c, x, y, color=INK):
    c.create_oval(x - 3, y - 3, x + 3, y + 3, fill=color, outline=color)


def _terminal(c, x, y, color=OUT):
    c.create_oval(x - 4, y - 4, x + 4, y + 4, fill="white", outline=color, width=2)


def _ground(c, x, y):
    _line(c, x, y, x, y + 8)
    for i, half in enumerate((10, 6, 2)):
        _line(c, x - half, y + 8 + 4 * i, x + half, y + 8 + 4 * i, width=2)


def _source(c, kind, x, ytop, ybot, label):
    cy = (ytop + ybot) / 2
    r = 15
    _line(c, x, ytop, x, cy - r)
    _line(c, x, cy + r, x, ybot)
    c.create_oval(x - r, cy - r, x + r, cy + r, outline=INK, width=2, fill="white")
    if kind == "ac":
        pts = [(x - 9 + i, cy - 6 * math.sin((i / 18) * 2 * math.pi)) for i in range(0, 19, 2)]
        _line(c, *[v for p in pts for v in p], width=2)
    elif kind == "isrc":
        _line(c, x, cy + 9, x, cy - 9)
        c.create_polygon(x - 5, cy - 4, x + 5, cy - 4, x, cy - 11, fill=INK)
    else:
        c.create_text(x, cy - 6, text="+", fill=INK, font=("Segoe UI", 10, "bold"))
        c.create_text(x, cy + 7, text="−", fill=INK, font=("Segoe UI", 10, "bold"))
    c.create_text(x - r - 4, cy, text=label, anchor="e", fill=ACCENT, font=("Segoe UI", 9, "bold"))


# ---------------------------------------------------------------- layouts
def _ladder(c, spec, w, h):
    top, bot = 34, h - 40
    x = 58
    _source(c, spec.get("source") or "ac", x, top, bot, spec.get("source_label", "Vin"))
    series, shunts = spec.get("series", []), spec.get("shunts", [])
    n_slots = len(series) + len(shunts) + 1
    step = max(40, min(90, (w - x - 70) / max(n_slots, 1)))
    cx = x
    series_pos = []
    for kind, label in series:
        nx_ = cx + step
        _part(c, kind, cx, top, nx_, top, label, label_side=1)
        series_pos.append((cx, nx_))
        cx = nx_
    shunt_x = []
    for branch in shunts:
        nx_ = cx + step
        _line(c, cx, top, nx_, top)
        shunt_x.append(nx_)
        _dot(c, nx_, top)
        _dot(c, nx_, bot)
        seg = (bot - top) / max(len(branch), 1)
        for i, (kind, label) in enumerate(branch):
            _part(c, kind, nx_, top + i * seg, nx_, top + (i + 1) * seg, label, label_side=1)
        cx = nx_
    out = spec.get("out")
    end = cx + (40 if out and out.get("across") in ("shunt", "shunts") else 10)
    _line(c, cx, top, end, top)
    _line(c, x, bot, end, bot)
    _ground(c, x + 22, bot)
    if out:
        lab = spec.get("out_label", "Vout")
        if out.get("across") in ("shunt", "shunts"):
            _terminal(c, end, top)
            _terminal(c, end, bot)
            c.create_text(end + 8, top, text="+", fill=OUT, anchor="w", font=("Segoe UI", 10, "bold"))
            c.create_text(end + 8, bot, text="−", fill=OUT, anchor="w", font=("Segoe UI", 10, "bold"))
            c.create_text(end + 8, (top + bot) / 2, text=lab, fill=OUT, anchor="w", font=("Segoe UI", 10, "bold"))
        elif out.get("across") == "series" and series_pos:
            a, b = series_pos[out.get("index", 0)]
            c.create_line(a + 8, top - 22, b - 8, top - 22, fill=OUT, width=1, arrow="both")
            c.create_text((a + b) / 2, top - 27, text=f"+ {lab} −", fill=OUT, anchor="s", font=("Segoe UI", 9, "bold"))
    if spec.get("note"):
        c.create_text(8, h - 6, text=spec["note"], anchor="sw", fill=MUTED, font=("Segoe UI", 8))


def _chain(c, spec, w, h):
    items = spec.get("items") or [(spec.get("comp", "R"), f"{spec.get('comp', 'R')}{i + 1}") for i in range(spec.get("count", 3))]
    items = [it for it in items] + ([("…", "")] if spec.get("count") else [])
    mode = spec.get("mode", "series")
    if mode == "series":
        y = h / 2
        x0, x1 = 40, w - 40
        _terminal(c, x0, y, INK)
        _terminal(c, x1, y, INK)
        step = (x1 - x0) / len(items)
        for i, (kind, label) in enumerate(items):
            a, b = x0 + i * step, x0 + (i + 1) * step
            if kind == "…":
                _line(c, a, y, b, y)
                c.create_text((a + b) / 2, y - 12, text="…", fill=MUTED)
            else:
                _part(c, kind, a, y, b, y, label)
        c.create_text(w / 2, h - 10, text="series: the same current flows through every part", fill=MUTED, font=("Segoe UI", 8))
    else:
        top, bot = 30, h - 40
        x0 = 50
        step = (w - 100) / len(items)
        _terminal(c, 20, top, INK)
        _terminal(c, 20, bot, INK)
        last = x0 + (len(items) - 1) * step
        _line(c, 20, top, last, top)
        _line(c, 20, bot, last, bot)
        for i, (kind, label) in enumerate(items):
            x = x0 + i * step
            if kind == "…":
                c.create_text(x, (top + bot) / 2, text="…", fill=MUTED)
                continue
            _part(c, kind, x, top, x, bot, label)
        c.create_text(w / 2, h - 10, text="parallel: every part has the same voltage", fill=MUTED, font=("Segoe UI", 8))


def _transformer(c, w, h):
    top, bot = 30, h - 36
    xp, xs = w * 0.38, w * 0.62
    _source(c, "ac", 50, top, bot, "Vp")
    _line(c, 50, top, xp, top)
    _line(c, 50, bot, xp, bot)
    _part(c, "L", xp, top, xp, bot, "")
    _part(c, "L", xs, top, xs, bot, "")
    for dx in (-4, 4):
        _line(c, w / 2 + dx, top + 10, w / 2 + dx, bot - 10, width=2)
    _line(c, xs, top, w - 60, top)
    _line(c, xs, bot, w - 60, bot)
    _part(c, "R", w - 60, top, w - 60, bot, "ZL")
    c.create_text(xp - 12, (top + bot) / 2, text="Np", anchor="e", fill=ACCENT, font=("Segoe UI", 9, "bold"))
    c.create_text(xs + 12, (top + bot) / 2, text="Ns", anchor="w", fill=ACCENT, font=("Segoe UI", 9, "bold"))
    c.create_text(w / 2, 12, text="a = Np : Ns  (primary ÷ secondary)", fill=OUT, font=("Segoe UI", 9, "bold"))
    c.create_text(w * 0.25, h - 10, text="primary: Vp, Ip", fill=MUTED, font=("Segoe UI", 8))
    c.create_text(w * 0.78, h - 10, text="secondary: Vs, Is", fill=MUTED, font=("Segoe UI", 8))
