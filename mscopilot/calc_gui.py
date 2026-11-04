"""EE Calculator tab (tkinter). All maths lives in mscopilot.eecalc; this module only
collects inputs, shows results, draws diagrams/graphs and manages history."""
from __future__ import annotations

import math
import tkinter as tk
import traceback
from tkinter import filedialog, messagebox, simpledialog, ttk

from mscopilot import calc_diagram
from mscopilot import config as cfg
from mscopilot import eecalc
from mscopilot.eecalc import core, graph, units
from mscopilot.eecalc.store import Entry, Store
from mscopilot.ui_common import ScrollFrame, with_scrollbars

HINT = "#64748b"
FONT = "Segoe UI"


class CalculatorPanel(ttk.Frame):
    def __init__(self, master, store_path=None):
        super().__init__(master, padding=6)
        self.store = Store(store_path or (cfg.config_dir() / "calculator.json"))
        self.calc: core.Calculator | None = None
        self.vars: dict[str, tuple[tk.StringVar, tk.StringVar]] = {}
        self.texts: dict[str, dict[str, tuple[str, str]]] = {}  # remembered entries per calculator
        self.opt_vars: dict[str, tk.StringVar] = {}
        self.unknown_var = tk.StringVar()
        self.decimals = tk.IntVar(value=3)
        self.angle_unit = tk.StringVar(value="°")
        self.mag_mode = tk.StringVar(value="dB")
        self.show_steps = tk.BooleanVar(value=True)
        self.last: tuple[core.CalcInput, core.Result] | None = None
        self._build()
        self._fill_tree()
        self.select(core.REGISTRY[0].id)

    # ================================================================ layout
    def _build(self):
        panes = ttk.PanedWindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True)

        # ---- left: search + categories
        left = ttk.Frame(panes, padding=(0, 0, 6, 0))
        panes.add(left, weight=0)
        ttk.Label(left, text="Find a calculation", font=(FONT, 10, "bold")).pack(anchor="w")
        self.search_var = tk.StringVar()
        se = ttk.Entry(left, textvariable=self.search_var, width=28)
        se.pack(fill="x", pady=(2, 6))
        self.search_var.trace_add("write", lambda *_: self._fill_tree())
        tf, self.tree = with_scrollbars(left, ttk.Treeview, show="tree", selectmode="browse")
        self.tree.column("#0", width=260, minwidth=260, stretch=True)
        tf.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree)

        # ---- middle: inputs (scrollable)
        self.mid_sf = ScrollFrame(panes, padding=(6, 0))
        panes.add(self.mid_sf, weight=2)
        self.mid = self.mid_sf.body
        self.mid_sf.canvas.bind("<Configure>", self._on_mid_resize, add="+")
        self._hints = []

        self.title_lbl = ttk.Label(self.mid, text="", font=(FONT, 13, "bold"))
        self.title_lbl.pack(anchor="w")
        self.desc_lbl = ttk.Label(self.mid, text="", foreground=HINT, wraplength=420, justify="left")
        self.desc_lbl.pack(anchor="w", pady=(0, 4))
        bg = ttk.Style(self).lookup("TFrame", "background") or "#f0f0f0"
        self.diagram = tk.Canvas(self.mid, height=190, highlightthickness=1, highlightbackground="#cbd5e1", background="white")
        self.diagram.pack(fill="x", pady=(0, 6))
        self.diagram.bind("<Configure>", lambda e: self._draw_diagram())
        self.setup = ttk.Frame(self.mid)
        self.setup.pack(fill="x")
        self.form = ttk.Frame(self.mid)
        self.form.pack(fill="x", pady=(6, 0))
        self.form.columnconfigure(1, minsize=110)
        self.form.columnconfigure(3, weight=1)

        disp = ttk.Frame(self.mid)
        disp.pack(fill="x", pady=(8, 0))
        ttk.Label(disp, text="Decimals").pack(side="left")
        ttk.Spinbox(disp, from_=0, to=12, width=4, textvariable=self.decimals, command=self._rerender).pack(side="left", padx=(4, 12))
        self.decimals.trace_add("write", lambda *_: self._rerender())
        ttk.Label(disp, text="Angles").pack(side="left")
        for val, lab in (("°", "degrees"), ("rad", "radians")):
            ttk.Radiobutton(disp, text=lab, value=val, variable=self.angle_unit, command=self._rerender).pack(side="left", padx=2)
        ttk.Checkbutton(disp, text="Show steps", variable=self.show_steps, command=self._rerender).pack(side="left", padx=(12, 0))

        btns = ttk.Frame(self.mid)
        btns.pack(fill="x", pady=8)
        ttk.Button(btns, text="Calculate  (Enter)", command=self.calculate).pack(side="left")
        ttk.Button(btns, text="Copy result", command=self.copy).pack(side="left", padx=4)
        ttk.Button(btns, text="Save…", command=self.save_current).pack(side="left")
        btns2 = ttk.Frame(self.mid)
        btns2.pack(fill="x")
        ttk.Button(btns2, text="Reset to examples", command=self.reset).pack(side="left")
        ttk.Button(btns2, text="Clear all values", command=lambda: self.reset(blank=True)).pack(side="left", padx=4)

        # ---- right: results / graph / history / saved
        right = ttk.Notebook(panes)
        panes.add(right, weight=3)
        self.nb = right
        self.after(100, lambda: self._init_sashes(panes))
        res = ttk.Frame(right, padding=6)
        right.add(res, text="Result")
        self.answer = ttk.Label(res, text="", font=(FONT, 15, "bold"), foreground="#1d4ed8", wraplength=520, justify="left")
        self.answer.pack(anchor="w", fill="x")
        self.answer.bind("<Configure>", lambda e: self.answer.configure(wraplength=max(200, e.width - 10)))
        rf, self.out = with_scrollbars(res, tk.Text, wrap="word", font=("Consolas", 10), relief="flat", padx=6, pady=6,
                                       state="disabled")
        rf.pack(fill="both", expand=True, pady=(6, 0))
        self.out.tag_configure("h", font=(FONT, 10, "bold"), spacing1=8, spacing3=2)
        self.out.tag_configure("key", font=("Consolas", 10, "bold"))
        self.out.tag_configure("note", foreground=HINT)
        self.out.tag_configure("warn", foreground="#b45309")
        self.out.tag_configure("err", foreground="#b91c1c", font=(FONT, 10, "bold"))

        gr = ttk.Frame(right, padding=6)
        right.add(gr, text="Graph")
        gbar = ttk.Frame(gr)
        gbar.pack(fill="x")
        ttk.Label(gbar, text="Magnitude in").pack(side="left")
        for val, lab in graph.MAG_MODES:
            ttk.Radiobutton(gbar, text=lab, value=val, variable=self.mag_mode, command=self._draw_graph).pack(side="left", padx=3)
        ttk.Button(gbar, text="Export graph…", command=self.export_graph).pack(side="right")
        self.graph_sf = ScrollFrame(gr)
        self.graph_sf.pack(fill="both", expand=True, pady=(4, 0))
        self.graph_host = self.graph_sf.body
        self.graph_msg = ttk.Label(self.graph_host, text="Graphs appear here for filters, resonant circuits and transients.",
                                   foreground=HINT)
        self.graph_msg.pack(pady=20)
        self.fig = None
        self.fig_canvas = None

        hi = ttk.Frame(right, padding=6)
        right.add(hi, text="History")
        hf, self.hist = with_scrollbars(hi, ttk.Treeview, columns=("time", "calc", "answer"), show="headings", selectmode="browse")
        for col, w in (("time", 130), ("calc", 180), ("answer", 260)):
            self.hist.heading(col, text=col.title())
            self.hist.column(col, width=w, minwidth=w // 2, stretch=col == "answer")
        hf.pack(fill="both", expand=True)
        self.hist.bind("<Double-1>", lambda e: self._restore(self.store.history, self.hist))
        hb = ttk.Frame(hi)
        hb.pack(fill="x", pady=(4, 0))
        ttk.Button(hb, text="Restore selected", command=lambda: self._restore(self.store.history, self.hist)).pack(side="left")
        ttk.Button(hb, text="Clear history", command=self._clear_history).pack(side="right")

        sv = ttk.Frame(right, padding=6)
        right.add(sv, text="Saved")
        svf, self.saved = with_scrollbars(sv, ttk.Treeview, columns=("name", "calc", "answer"), show="headings", selectmode="browse")
        for col, w in (("name", 150), ("calc", 180), ("answer", 240)):
            self.saved.heading(col, text=col.title())
            self.saved.column(col, width=w, minwidth=w // 2, stretch=col == "answer")
        svf.pack(fill="both", expand=True)
        self.saved.bind("<Double-1>", lambda e: self._restore(self.store.saved, self.saved))
        sbx = ttk.Frame(sv)
        sbx.pack(fill="x", pady=(4, 0))
        ttk.Button(sbx, text="Open selected", command=lambda: self._restore(self.store.saved, self.saved)).pack(side="left")
        ttk.Button(sbx, text="Delete selected", command=self._delete_saved).pack(side="right")
        self._refresh_lists()
        self.bind_all("<Return>", self._on_enter, add="+")

    def _init_sashes(self, panes):
        try:
            w = panes.winfo_width()
            if w > 900:
                panes.sashpos(0, 240)
                panes.sashpos(1, 240 + int((w - 240) * 0.45))
        except tk.TclError:
            pass

    def _on_mid_resize(self, e):
        self.desc_lbl.configure(wraplength=max(200, e.width - 20))
        for lab in self._hints:
            try:
                lab.configure(wraplength=max(120, e.width - 300))
            except tk.TclError:
                pass

    def _on_enter(self, event):
        w = event.widget
        try:
            if str(w).startswith(str(self)) and self.winfo_viewable() and not isinstance(w, tk.Text):
                self.calculate()
        except tk.TclError:
            pass

    # ================================================================ tree
    def _fill_tree(self):
        self.tree.delete(*self.tree.get_children())
        hits = core.search(self.search_var.get())
        for cat in core.CATEGORIES:
            items = [c for c in hits if c.category == cat]
            if not items:
                continue
            node = self.tree.insert("", "end", iid=f"cat:{cat}", text=cat, open=True)
            for c in items:
                self.tree.insert(node, "end", iid=c.id, text=c.title)
        if self.calc and self.tree.exists(self.calc.id):
            self.tree.selection_set(self.calc.id)
            self.tree.see(self.calc.id)

    def _on_tree(self, _e=None):
        sel = self.tree.selection()
        if sel and not sel[0].startswith("cat:") and (not self.calc or sel[0] != self.calc.id):
            self.select(sel[0])

    # ================================================================ form
    def select(self, calc_id: str, options=None, unknown=None, entries=None):
        if self.calc:
            self._remember()
        self.calc = eecalc.get(calc_id)
        self.title_lbl.configure(text=self.calc.title)
        self.desc_lbl.configure(text=self.calc.description)
        for w in self.setup.winfo_children():
            w.destroy()
        self.opt_vars = {}
        row = 0
        if self.calc.unknowns:
            ttk.Label(self.setup, text="Solve for", font=(FONT, 9, "bold")).grid(row=row, column=0, sticky="w", padx=(0, 8))
            labels = [lab for _, lab in self.calc.unknowns]
            cb = ttk.Combobox(self.setup, state="readonly", values=labels, width=44)
            key = unknown or self.calc.default_unknown()
            cb.set(dict(self.calc.unknowns)[key])
            self.unknown_var.set(key)
            cb.bind("<<ComboboxSelected>>", lambda e, cb=cb: self._set_unknown(cb.get()))
            cb.grid(row=row, column=1, sticky="w", pady=2)
            row += 1
        for o in self.calc.options:
            ttk.Label(self.setup, text=o.label, font=(FONT, 9, "bold")).grid(row=row, column=0, sticky="w", padx=(0, 8))
            var = tk.StringVar(value=(options or {}).get(o.key, o.default))
            self.opt_vars[o.key] = var
            cb = ttk.Combobox(self.setup, state="readonly", values=[lab for _, lab in o.choices], width=44)
            cb.set(dict(o.choices)[var.get()])
            cb.bind("<<ComboboxSelected>>", lambda e, o=o, var=var, cb=cb: self._set_option(o, var, cb.get()))
            cb.grid(row=row, column=1, sticky="w", pady=2)
            row += 1
        if entries is not None:
            self.texts[calc_id] = {k: tuple(v) for k, v in entries.items()}
        self._build_fields()
        self.last = None
        self._show_result(None)
        if self.tree.exists(calc_id) and self.tree.selection() != (calc_id,):
            self.tree.selection_set(calc_id)
            self.tree.see(calc_id)

    def _set_unknown(self, label):
        self._remember()
        self.unknown_var.set(next(k for k, lab in self.calc.unknowns if lab == label))
        self._build_fields()

    def _set_option(self, o, var, label):
        self._remember()
        var.set(next(k for k, lab in o.choices if lab == label))
        self._build_fields()

    def options(self) -> dict:
        return {k: v.get() for k, v in self.opt_vars.items()}

    def unknown(self):
        return self.unknown_var.get() if self.calc.unknowns else None

    def _remember(self):
        store = self.texts.setdefault(self.calc.id, {})
        for k, (tv, uv) in self.vars.items():
            store[k] = (tv.get(), uv.get())

    def _build_fields(self):
        for w in self.form.winfo_children():
            w.destroy()
        self.vars = {}
        self._hints = []
        opts, unk = self.options(), self.unknown()
        remembered = self.texts.get(self.calc.id, {})
        fields = self.calc.visible_fields(opts, unk)
        ttk.Label(self.form, text="Known values", font=(FONT, 10, "bold")).grid(row=0, column=0, columnspan=4, sticky="w")
        first = None
        for i, f in enumerate(fields, start=1):
            qty = f.quantity_for(opts)
            unit_choices = units.units_for(qty)
            text, unit = remembered.get(f.key, (f.default, f.unit_for(opts)))
            if unit not in unit_choices:
                unit = f.unit_for(opts) if f.unit_for(opts) in unit_choices else unit_choices[0]
            tv, uv = tk.StringVar(value=text), tk.StringVar(value=unit)
            self.vars[f.key] = (tv, uv)
            ttk.Label(self.form, text=f.symbol, font=(FONT, 10, "bold"), width=6).grid(row=i, column=0, sticky="w", pady=2)
            e = ttk.Entry(self.form, textvariable=tv, width=13)
            e.grid(row=i, column=1, sticky="ew", pady=2)
            first = first or e
            if len(unit_choices) > 1 or unit_choices[0]:
                ttk.Combobox(self.form, state="readonly", values=unit_choices, textvariable=uv, width=6).grid(
                    row=i, column=2, sticky="w", padx=4)
            meaning = f.meaning + ("  (optional)" if f.optional and "blank" not in f.meaning else "")
            lab = ttk.Label(self.form, text=meaning, foreground=HINT, wraplength=220, justify="left")
            lab.grid(row=i, column=3, sticky="w", padx=(4, 0))
            self._hints.append(lab)
        self._draw_diagram()

    def _draw_diagram(self):
        if not self.calc:
            return
        spec = self.calc.diagram(self.options(), self.unknown()) if self.calc.diagram else None
        try:
            calc_diagram.draw(self.diagram, spec)
        except tk.TclError:
            pass

    def entries(self) -> dict:
        return {k: (tv.get(), uv.get()) for k, (tv, uv) in self.vars.items()}

    def reset(self, blank=False):
        self.texts.pop(self.calc.id, None)
        if blank:
            self.texts[self.calc.id] = {k: ("", uv.get()) for k, (tv, uv) in self.vars.items()}
        self._build_fields()
        self.last = None
        self._show_result(None)

    # ================================================================ calculate
    def calculate(self):
        if not self.calc:
            return
        try:
            inp = core.build_input(self.calc, self.entries(), self.options(), self.unknown())
            res = self.calc.compute(inp)
        except core.CalcError as e:
            self.last = None
            self._show_result(None, error=str(e))
            return
        except (ZeroDivisionError, OverflowError, ValueError) as e:
            self.last = None
            self._show_result(None, error=f"These values cannot be evaluated ({e}). Check for zero or extreme values.")
            return
        except Exception:
            self.last = None
            self._show_result(None, error="Unexpected error:\n" + traceback.format_exc(limit=3))
            return
        self.last = (inp, res)
        self._show_result(res)
        self.store.add_history(Entry(self.calc.id, self.calc.title, self.options(), self.unknown(),
                                     {k: list(v) for k, v in self.entries().items()}, self._headline(res)))
        self._refresh_lists()

    def _headline(self, res) -> str:
        d, a = int(self.decimals.get() or 3), self.angle_unit.get()
        return ";  ".join(f"{o.symbol} = {core.fmt_output(o, d, a)}" for o in res.primary())

    def _rerender(self):
        try:
            int(self.decimals.get())
        except (tk.TclError, ValueError):
            return
        if self.last:
            self._show_result(self.last[1])

    def _show_result(self, res, error=""):
        self.out.configure(state="normal")
        self.out.delete("1.0", "end")
        if error:
            self.answer.configure(text="")
            self.out.insert("end", error + "\n", "err")
        elif res is None:
            self.answer.configure(text="")
            self.out.insert("end", "Enter the known values and press Calculate.\n", "note")
        else:
            d, a = int(self.decimals.get()), self.angle_unit.get()
            self.answer.configure(text="\n".join(
                f"{o.symbol} = {core.fmt_output(o, d, a)}" + (f"   ({o.note})" if o.note else "") for o in res.primary()))
            self.out.insert("end", res.title + "\n", "h")
            for o in res.outputs:
                self.out.insert("end", f"{o.symbol} = ", "key")
                self.out.insert("end", core.fmt_output(o, d, a))
                self.out.insert("end", f"   {o.meaning}" + (f"  [{o.note}]" if o.note else "") + "\n", "note")
            sections = [("Formulas", res.formulas, None), ("Substituted values", res.substitutions, None)]
            if self.show_steps.get():
                sections.append(("Step-by-step", res.steps, None))
            sections += [("Assumptions and model limits", res.assumptions, "note"), ("Warnings", res.warnings, "warn")]
            for title, items, tag in sections:
                if items:
                    self.out.insert("end", title + "\n", "h")
                    for it in items:
                        self.out.insert("end", f"• {it}\n", tag or ())
            if res.symbols:
                self.out.insert("end", "Symbols\n", "h")
                for k, v in res.symbols.items():
                    self.out.insert("end", f"{k}", "key")
                    self.out.insert("end", f" = {v}\n", "note")
        self.out.configure(state="disabled")
        self._draw_graph()

    # ================================================================ graph
    def _draw_graph(self):
        spec = self.last[1].plot if self.last else None
        if spec is None:
            if self.fig_canvas:
                self.fig_canvas.get_tk_widget().pack_forget()
            self.graph_msg.pack(pady=20)
            return
        if self.fig is None:
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
            from matplotlib.figure import Figure

            self.fig = Figure(figsize=(6, 5), dpi=96)
            self.fig_canvas = FigureCanvasTkAgg(self.fig, master=self.graph_host)
        self.graph_msg.pack_forget()
        tkw = self.fig_canvas.get_tk_widget()
        tkw.configure(width=560, height=440)  # minimum size; scrollbars appear below this
        tkw.pack(fill="both", expand=True)
        try:
            graph.draw(self.fig, spec, self.mag_mode.get(), self.angle_unit.get())
        except Exception as e:
            self.fig.clear()
            self.fig.text(0.05, 0.5, f"Graph unavailable: {e}")
        self.fig_canvas.draw_idle()

    def export_graph(self):
        if not (self.last and self.last[1].plot and self.fig):
            messagebox.showinfo("Export graph", "Calculate something that has a graph first.", parent=self)
            return
        p = filedialog.asksaveasfilename(parent=self, defaultextension=".png",
                                         filetypes=[("PNG image", "*.png"), ("SVG vector", "*.svg"), ("PDF", "*.pdf")])
        if p:
            self.fig.savefig(p, dpi=200)

    # ================================================================ copy / save / history
    def copy(self):
        if not self.last:
            messagebox.showinfo("Copy", "Calculate something first.", parent=self)
            return
        inp, res = self.last
        text = core.plain_text(self.calc, inp, res, int(self.decimals.get()), self.angle_unit.get())
        self.clipboard_clear()
        self.clipboard_append(text)

    def save_current(self):
        if not self.last:
            messagebox.showinfo("Save", "Calculate something first.", parent=self)
            return
        name = simpledialog.askstring("Save calculation", "Name for this calculation:", parent=self,
                                      initialvalue=self.calc.title)
        if not name:
            return
        self.store.add_saved(Entry(self.calc.id, self.calc.title, self.options(), self.unknown(),
                                   {k: list(v) for k, v in self.entries().items()}, self._headline(self.last[1]), name=name))
        self._refresh_lists()
        self.nb.select(3)

    def _refresh_lists(self):
        for tv, items, first in ((self.hist, self.store.history, "time"), (self.saved, self.store.saved, "name")):
            tv.delete(*tv.get_children())
            for i, e in enumerate(items):
                tv.insert("", "end", iid=str(i), values=(getattr(e, first), e.title, e.headline))

    def _restore(self, items, tv):
        sel = tv.selection()
        if not sel:
            return
        e = items[int(sel[0])]
        try:
            self.select(e.calc_id, e.options, e.unknown, e.entries)
        except KeyError:
            messagebox.showwarning("Restore", "That calculator no longer exists.", parent=self)
            return
        self.calculate()
        self.nb.select(0)

    def _delete_saved(self):
        sel = self.saved.selection()
        if sel and messagebox.askyesno("Delete", "Delete the selected saved calculation?", parent=self):
            self.store.delete_saved(int(sel[0]))
            self._refresh_lists()

    def _clear_history(self):
        if messagebox.askyesno("Clear history", "Remove all calculation history?", parent=self):
            self.store.clear_history()
            self._refresh_lists()


def open_window(master=None) -> tk.Toplevel | tk.Tk:
    """Stand-alone calculator window (used by `MultisimCopilot calc`)."""
    win = tk.Toplevel(master) if master else tk.Tk()
    win.title("EE Calculator")
    win.geometry("1280x800")
    CalculatorPanel(win).pack(fill="both", expand=True)
    return win
