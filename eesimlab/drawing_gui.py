"""Drawing Analysis & Virtual Probing workspace (also the schematic editor).

Presentation only: recognition, reconstruction, validation, solving and instruments live in
eesimlab.drawing.*. Coordinates in the model are pixels of the original drawing; the view
maps them through rotation and zoom, and the canvas scrollbars provide panning.
"""
from __future__ import annotations

import io
import math
import threading
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from PIL import Image, ImageTk

from eesimlab import config as cfg
from eesimlab.drawing import catalog as CAT
from eesimlab.drawing import solver as S
from eesimlab.drawing.analysis import SimSettings, analyse
from eesimlab.drawing.instruments import KINDS, MODES, Probe, measure
from eesimlab.drawing.model import SUPPORTED_KINDS, UNSUPPORTED_KINDS, Component, DrawingModel, SourceParams, parse_value
from eesimlab.drawing.project import Workspace
from eesimlab.drawing.recognize import DrawingError, enhance, load_drawing, pdf_page_count, rotate_point, unrotate_point
from eesimlab.eecalc.units import fmt
from eesimlab.ui_common import ScrollFrame, with_scrollbars

HINT = "#64748b"
FONT = "Segoe UI"
SHEET = (2400, 1600)  # blank schematic sheet size (model px)
GRID = 20
SNAP = 10  # px (model) for joining a pin to an existing node point
NODE_COLORS = ["#2563eb", "#059669", "#7c3aed", "#db2777", "#ea580c", "#0891b2", "#65a30d", "#b45309"]
SEV_COLORS = {"problem": "#dc2626", "concern": "#d97706", "missing": "#2563eb"}
TOOLS = [("select", "Select / move"), ("pan", "Pan"), ("place", "Place part"), ("wire", "Wire"), ("ground", "Ground"),
         ("voltmeter", "Voltmeter"), ("ammeter", "Ammeter"), ("scope", "Scope"), ("ohmmeter", "Ohmmeter"), ("scale", "Scale / measure")]


class DrawingWorkspace(ttk.Frame):
    def __init__(self, master, settings: cfg.Settings | None = None):
        super().__init__(master, padding=4)
        self.settings = settings or cfg.Settings.load()
        self.ws = Workspace()
        self.base: Image.Image | None = None
        self.zoom = 0.5
        self.rotation = 0.0
        self.enh = {"contrast": 1.0, "brightness": 1.0, "sharpen": False, "grayscale": False, "autocontrast": False,
                    "threshold": None, "invert": False}
        self._photo = None
        self._render_key = None
        self.tool = tk.StringVar(value="select")
        self.layers = {k: tk.BooleanVar(value=True) for k in ("image", "parts", "nets", "junctions", "findings", "probes")}
        self.selected: str | None = None  # component id
        self.pending: list = []  # clicks for multi-click tools
        self.place_part: CAT.Part | None = None
        self.place_rot = 0
        self.result = None
        self.readings: dict = {}
        self.highlight = None
        self._drag = None
        self._build()
        self.new_blank(confirm=False)

    # ================================================================ layout
    def _build(self):
        bar = ttk.Frame(self)
        bar.pack(fill="x")
        for text, cmd in (("Open drawing…", self.open_drawing), ("New blank schematic", self.new_blank),
                          ("Open project…", self.open_project), ("Save project…", self.save_project),
                          ("Export report…", self.export_report)):
            ttk.Button(bar, text=text, command=cmd).pack(side="left", padx=(0, 4))
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=6)
        ttk.Button(bar, text="Recognize with AI", command=self.recognize).pack(side="left")
        self.btn_analyse = ttk.Button(bar, text="✔ Confirm model & analyse", command=self.run_analysis)
        self.btn_analyse.pack(side="left", padx=4)
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=6)
        ttk.Label(bar, text="Version").pack(side="left")
        self.version_cb = ttk.Combobox(bar, state="readonly", width=16)
        self.version_cb.pack(side="left", padx=4)
        self.version_cb.bind("<<ComboboxSelected>>", self._switch_version)
        ttk.Button(bar, text="New revision", command=self.new_revision).pack(side="left")

        tools = ttk.Frame(self)
        tools.pack(fill="x", pady=(4, 2))
        for key, lab in TOOLS:
            ttk.Radiobutton(tools, text=lab, value=key, variable=self.tool, command=self._tool_changed).pack(side="left", padx=2)
        view = ttk.Frame(self)
        view.pack(fill="x", pady=(0, 4))
        ttk.Button(view, text="−", width=3, command=lambda: self.set_zoom(self.zoom / 1.25)).pack(side="left")
        ttk.Button(view, text="+", width=3, command=lambda: self.set_zoom(self.zoom * 1.25)).pack(side="left")
        ttk.Button(view, text="Fit", command=self.fit).pack(side="left", padx=(2, 8))
        ttk.Button(view, text="⟲ 90°", command=lambda: self.set_rotation(self.rotation + 90)).pack(side="left")
        ttk.Button(view, text="⟳ 90°", command=lambda: self.set_rotation(self.rotation - 90)).pack(side="left")
        ttk.Label(view, text=" fine °").pack(side="left")
        self.fine = tk.DoubleVar(value=0.0)
        sp = ttk.Spinbox(view, from_=-45, to=45, increment=0.5, width=6, textvariable=self.fine,
                         command=lambda: self.set_rotation(round(self.rotation / 90) * 90 + self.fine.get()))
        sp.pack(side="left")
        ttk.Button(view, text="Enhance…", command=self.enhance_dialog).pack(side="left", padx=8)
        ttk.Label(view, text="Show:").pack(side="left", padx=(8, 2))
        for k in self.layers:
            ttk.Checkbutton(view, text=k, variable=self.layers[k], command=self.redraw).pack(side="left")

        panes = ttk.PanedWindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True)
        cf, self.canvas = with_scrollbars(panes, tk.Canvas, background="#e5e7eb", highlightthickness=0, cursor="crosshair")
        panes.add(cf, weight=3)
        self.canvas.bind("<Button-1>", self._click)
        self.canvas.bind("<B1-Motion>", self._drag_move)
        self.canvas.bind("<ButtonRelease-1>", self._drag_end)
        self.canvas.bind("<Button-2>", lambda e: self.canvas.scan_mark(e.x, e.y))
        self.canvas.bind("<B2-Motion>", lambda e: self.canvas.scan_dragto(e.x, e.y, gain=1))
        self.canvas.bind("<Motion>", self._hover)
        self.canvas.bind("<Control-MouseWheel>", lambda e: self.set_zoom(self.zoom * (1.15 if e.delta > 0 else 1 / 1.15)))
        self.canvas.bind("<Control-Button-4>", lambda e: self.set_zoom(self.zoom * 1.15))
        self.canvas.bind("<Control-Button-5>", lambda e: self.set_zoom(self.zoom / 1.15))
        self.canvas.bind("<MouseWheel>", lambda e: self.canvas.yview_scroll(-1 if e.delta > 0 else 1, "units"))
        self.canvas.bind("<Shift-MouseWheel>", lambda e: self.canvas.xview_scroll(-1 if e.delta > 0 else 1, "units"))
        self.canvas.bind("<Button-4>", lambda e: self.canvas.yview_scroll(-1, "units"))
        self.canvas.bind("<Button-5>", lambda e: self.canvas.yview_scroll(1, "units"))
        self.canvas.bind("<Delete>", lambda e: self.delete_selected())
        self.canvas.bind("<Key-r>", lambda e: self.rotate_selected())
        self.canvas.bind("<Escape>", lambda e: self._cancel_pending())

        self.nb = ttk.Notebook(panes)
        panes.add(self.nb, weight=2)
        self._build_catalog_tab()
        self._build_components_tab()
        self._build_connections_tab()
        self._build_findings_tab()
        self._build_calc_tab()
        self._build_sim_tab()
        self._build_compare_tab()
        self._build_notes_tab()
        self.status = ttk.Label(self, text="", foreground=HINT, anchor="w")
        self.status.pack(fill="x")

    # ---------------------------------------------------------------- tabs
    def _build_catalog_tab(self):
        f = ttk.Frame(self.nb, padding=6)
        self.nb.add(f, text="Parts catalog")
        top = ttk.Frame(f)
        top.pack(fill="x")
        ttk.Label(top, text=f"{len(CAT.CATALOG)} parts — search").pack(side="left")
        self.cat_search = tk.StringVar()
        e = ttk.Entry(top, textvariable=self.cat_search, width=22)
        e.pack(side="left", padx=4, fill="x", expand=True)
        self.cat_cat = ttk.Combobox(f, state="readonly", values=["All categories"] + CAT.CATEGORIES)
        self.cat_cat.set("All categories")
        self.cat_cat.pack(fill="x", pady=4)
        lf, self.cat_list = with_scrollbars(f, ttk.Treeview, columns=("name", "cat", "sim"), show="headings", selectmode="browse")
        for c, t, w in (("name", "Part", 240), ("cat", "Category", 140), ("sim", "Simulated", 70)):
            self.cat_list.heading(c, text=t)
            self.cat_list.column(c, width=w, stretch=c == "name")
        lf.pack(fill="both", expand=True)
        ttk.Label(f, text="Select a part, then click on the sheet to place it (press 'r' to rotate before placing). "
                          "Parts marked 'no' are drawn but not simulated.", foreground=HINT, wraplength=380).pack(anchor="w")
        self.cat_search.trace_add("write", lambda *_: self._fill_catalog())
        self.cat_cat.bind("<<ComboboxSelected>>", lambda e: self._fill_catalog())
        self.cat_list.bind("<<TreeviewSelect>>", self._catalog_pick)
        self._fill_catalog()

    def _build_components_tab(self):
        f = ttk.Frame(self.nb, padding=6)
        self.nb.add(f, text="Components")
        pw = ttk.PanedWindow(f, orient="vertical")
        pw.pack(fill="both", expand=True)
        tf, self.comp_tree = with_scrollbars(pw, ttk.Treeview, columns=("kind", "value", "nodes", "conf"), show="tree headings",
                                             selectmode="browse")
        self.comp_tree.heading("#0", text="ID")
        for c, t, w in (("kind", "Type", 110), ("value", "Value", 100), ("nodes", "Terminals → nodes", 180), ("conf", "Confidence", 80)):
            self.comp_tree.heading(c, text=t)
            self.comp_tree.column(c, width=w, stretch=c == "nodes")
        self.comp_tree.column("#0", width=70)
        pw.add(tf, weight=1)
        self.comp_tree.bind("<<TreeviewSelect>>", self._comp_pick)
        self.props = ScrollFrame(pw, padding=4)
        pw.add(self.props, weight=2)

    def _build_connections_tab(self):
        f = ttk.Frame(self.nb, padding=6)
        self.nb.add(f, text="Connections")
        pw = ttk.PanedWindow(f, orient="vertical")
        pw.pack(fill="both", expand=True)
        a = ttk.Frame(pw)
        pw.add(a, weight=1)
        ttk.Label(a, text="Nodes (electrically connected points)", font=(FONT, 10, "bold")).pack(anchor="w")
        nf, self.node_tree = with_scrollbars(a, ttk.Treeview, columns=("name", "gnd", "terms"), show="tree headings", selectmode="extended")
        self.node_tree.heading("#0", text="Node")
        for c, t, w in (("name", "Name", 100), ("gnd", "Ground", 60), ("terms", "Terminals", 220)):
            self.node_tree.heading(c, text=t)
            self.node_tree.column(c, width=w)
        nf.pack(fill="both", expand=True)
        nb = ttk.Frame(a)
        nb.pack(fill="x", pady=2)
        ttk.Button(nb, text="Rename…", command=self._rename_node).pack(side="left")
        ttk.Button(nb, text="Toggle ground", command=self._toggle_ground).pack(side="left", padx=4)
        ttk.Button(nb, text="Merge selected", command=self._merge_nodes).pack(side="left")
        b = ttk.Frame(pw)
        pw.add(b, weight=1)
        ttk.Label(b, text="Wire meeting points (junction ●, crossing ✕, ambiguous ?)", font=(FONT, 10, "bold")).pack(anchor="w")
        jf, self.junc_tree = with_scrollbars(b, ttk.Treeview, columns=("kind", "conf", "nodes", "note"), show="tree headings",
                                             selectmode="browse")
        self.junc_tree.heading("#0", text="ID")
        for c, t, w in (("kind", "Type", 90), ("conf", "Confidence", 80), ("nodes", "Nodes", 120), ("note", "Note", 200)):
            self.junc_tree.heading(c, text=t)
            self.junc_tree.column(c, width=w)
        jf.pack(fill="both", expand=True)
        jb = ttk.Frame(b)
        jb.pack(fill="x", pady=2)
        ttk.Button(jb, text="Mark connected (junction)", command=lambda: self._set_junction("junction")).pack(side="left")
        ttk.Button(jb, text="Mark crossing (not connected)", command=lambda: self._set_junction("crossing")).pack(side="left", padx=4)

    def _build_findings_tab(self):
        f = ttk.Frame(self.nb, padding=6)
        self.nb.add(f, text="Findings")
        ff, self.find_tree = with_scrollbars(f, ttk.Treeview, columns=("where",), show="tree headings", selectmode="browse")
        self.find_tree.heading("#0", text="Finding")
        self.find_tree.heading("where", text="Location")
        self.find_tree.column("#0", width=330)
        self.find_tree.column("where", width=200)
        ff.pack(fill="both", expand=True)
        self.find_tree.bind("<<TreeviewSelect>>", self._finding_pick)
        df, self.find_detail = with_scrollbars(f, tk.Text, x=False, height=8, wrap="word", font=(FONT, 9))
        df.pack(fill="x")

    def _build_calc_tab(self):
        f = ttk.Frame(self.nb, padding=6)
        self.nb.add(f, text="Calculations")
        self.outdated = ttk.Label(f, text="", foreground="#b45309", font=(FONT, 9, "bold"))
        self.outdated.pack(anchor="w")
        cf, self.calc_tree = with_scrollbars(f, ttk.Treeview, show="tree", selectmode="browse")
        self.calc_tree.column("#0", width=600, minwidth=300, stretch=True)
        cf.pack(fill="both", expand=True)

    def _build_sim_tab(self):
        outer = ScrollFrame(self.nb, padding=6)
        self.nb.add(outer, text="Simulate & probe")
        f = outer.body
        st = ttk.LabelFrame(f, text="Simulation settings", padding=6)
        st.pack(fill="x")
        self.sv = {k: tk.StringVar() for k in ("freq", "duration", "steps", "fmin", "fmax")}
        self.sv_initial = tk.StringVar(value="dc")
        self.sv_output = tk.StringVar()
        self.sv_mode = tk.StringVar(value="dc")
        rows = [("AC analysis frequency (Hz, blank = source frequency)", "freq"), ("Transient duration (s, blank = auto 5τ / 5 periods)", "duration"),
                ("Transient steps", "steps"), ("Sweep start (Hz)", "fmin"), ("Sweep stop (Hz)", "fmax")]
        for i, (lab, k) in enumerate(rows):
            ttk.Label(st, text=lab).grid(row=i, column=0, sticky="w")
            ttk.Entry(st, textvariable=self.sv[k], width=14).grid(row=i, column=1, sticky="w", padx=4, pady=1)
        ttk.Label(st, text="Transient initial state").grid(row=5, column=0, sticky="w")
        ttk.Combobox(st, state="readonly", values=["dc", "zero"], textvariable=self.sv_initial, width=12).grid(row=5, column=1, sticky="w", padx=4)
        ttk.Label(st, text="Frequency-response output node").grid(row=6, column=0, sticky="w")
        self.out_cb = ttk.Combobox(st, state="readonly", textvariable=self.sv_output, width=18)
        self.out_cb.grid(row=6, column=1, sticky="w", padx=4)
        ttk.Label(st, text="New voltmeter/ammeter mode").grid(row=7, column=0, sticky="w")
        ttk.Combobox(st, state="readonly", values=list(MODES), textvariable=self.sv_mode, width=12).grid(row=7, column=1, sticky="w", padx=4)
        ttk.Button(st, text="Apply settings", command=self._apply_settings).grid(row=8, column=0, sticky="w", pady=4)
        ttk.Label(st, text="Source waveform, amplitude (peak) and frequency are set per source in the Components tab.",
                  foreground=HINT, wraplength=360).grid(row=9, column=0, columnspan=2, sticky="w")
        pr = ttk.LabelFrame(f, text="Virtual instruments  (readings are SIMULATED)", padding=6)
        pr.pack(fill="both", expand=True, pady=6)
        pf, self.probe_tree = with_scrollbars(pr, ttk.Treeview, columns=("kind", "mode", "where", "reading", "status"),
                                              show="tree headings", selectmode="browse", height=6)
        self.probe_tree.heading("#0", text="ID")
        for c, t, w in (("kind", "Instrument", 90), ("mode", "Mode", 60), ("where", "Placed at", 170), ("reading", "Reading", 200),
                        ("status", "Status", 80)):
            self.probe_tree.heading(c, text=t)
            self.probe_tree.column(c, width=w)
        self.probe_tree.column("#0", width=40)
        pf.pack(fill="both", expand=True)
        self.probe_tree.bind("<<TreeviewSelect>>", lambda e: self._show_probe())
        bb = ttk.Frame(pr)
        bb.pack(fill="x", pady=2)
        ttk.Button(bb, text="Measure all", command=self.measure_all).pack(side="left")
        ttk.Button(bb, text="Delete probe", command=self._delete_probe).pack(side="left", padx=4)
        ttk.Button(bb, text="Change mode", command=self._cycle_mode).pack(side="left")
        ttk.Button(bb, text="Scope: set reference…", command=self._scope_ref).pack(side="left", padx=4)
        df, self.probe_detail = with_scrollbars(pr, tk.Text, x=False, height=6, wrap="word", font=(FONT, 9))
        df.pack(fill="x")
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        from matplotlib.figure import Figure

        self.fig = Figure(figsize=(5.5, 3.0), dpi=90)
        self.fig_canvas = FigureCanvasTkAgg(self.fig, master=pr)
        w = self.fig_canvas.get_tk_widget()
        w.configure(width=480, height=260)
        w.pack(fill="both", expand=True)
        ttk.Button(pr, text="Show frequency response", command=self._plot_sweep).pack(anchor="w")

    def _build_compare_tab(self):
        f = ttk.Frame(self.nb, padding=6)
        self.nb.add(f, text="Compare")
        top = ttk.Frame(f)
        top.pack(fill="x")
        ttk.Label(top, text="Before").pack(side="left")
        self.cmp_a = ttk.Combobox(top, state="readonly", width=16)
        self.cmp_a.pack(side="left", padx=4)
        ttk.Label(top, text="After").pack(side="left")
        self.cmp_b = ttk.Combobox(top, state="readonly", width=16)
        self.cmp_b.pack(side="left", padx=4)
        ttk.Button(top, text="Compare", command=self.compare).pack(side="left", padx=4)
        cf, self.cmp_tree = with_scrollbars(f, ttk.Treeview, columns=("q", "a", "b", "d"), show="headings")
        for c, t, w in (("q", "Quantity", 200), ("a", "Before", 140), ("b", "After", 140), ("d", "Change", 140)):
            self.cmp_tree.heading(c, text=t)
            self.cmp_tree.column(c, width=w)
        cf.pack(fill="both", expand=True, pady=4)
        ttk.Label(f, text="DC operating point of both versions and the simulated probe readings, side by side.",
                  foreground=HINT).pack(anchor="w")

    def _build_notes_tab(self):
        f = ttk.Frame(self.nb, padding=6)
        self.nb.add(f, text="Notes")
        ttk.Label(f, text="Your assumptions and notes (saved with the project and printed in the report)").pack(anchor="w")
        nf, self.notes = with_scrollbars(f, tk.Text, wrap="word", font=(FONT, 10))
        nf.pack(fill="both", expand=True)
        ttk.Label(f, text="Recognition warnings", font=(FONT, 10, "bold")).pack(anchor="w", pady=(6, 0))
        wf, self.rec_warn = with_scrollbars(f, tk.Text, x=False, height=6, wrap="word", font=(FONT, 9))
        wf.pack(fill="x")

    # ================================================================ model helpers
    @property
    def model(self) -> DrawingModel:
        return self.ws.model

    def sheet_size(self):
        if self.base is not None:
            return self.base.size
        return SHEET

    def to_canvas(self, x, y):
        w, h = self.sheet_size()
        xr, yr, _, _ = rotate_point(x, y, w, h, self.rotation)
        return xr * self.zoom, yr * self.zoom

    def from_event(self, e):
        cx, cy = self.canvas.canvasx(e.x), self.canvas.canvasy(e.y)
        w, h = self.sheet_size()
        return unrotate_point(cx / self.zoom, cy / self.zoom, w, h, self.rotation)

    def changed(self, what="model"):
        """Any edit invalidates results that depended on the old model."""
        self.model.confirmed = False
        self.redraw()
        self._refresh_lists()
        self._mark_outdated()

    def _mark_outdated(self):
        h = self.model.electrical_hash()
        if self.result and (self.result.model_hash != h or self.result.settings_key != self.ws.settings.key()):
            self.outdated.configure(text="⚠ Results below are OUTDATED: the model or settings changed. Re-run the analysis.")
        else:
            self.outdated.configure(text="")
        self._refresh_probes()

    # ================================================================ file actions
    def new_blank(self, confirm=True):
        if confirm and self.model.components and not messagebox.askyesno("New schematic", "Discard the current work?", parent=self):
            return
        self.ws = Workspace(blank_sheet=True)
        self.ws.model.drawing_type = "electrical_schematic"
        self.ws.model.drawing_type_confidence = 1.0
        self.ws.model.image_size = list(SHEET)
        self.base = None
        self.rotation = 0.0
        self.result = None
        self.readings = {}
        self.zoom = 1.0
        self._after_load()

    def open_drawing(self):
        p = filedialog.askopenfilename(parent=self, filetypes=[("Drawings", "*.png *.jpg *.jpeg *.pdf *.bmp *.gif *.webp *.tif *.tiff"),
                                                                ("All files", "*.*")])
        if not p:
            return
        try:
            page = 0
            if p.lower().endswith(".pdf"):
                n = pdf_page_count(Path(p).read_bytes())
                if n > 1:
                    page = (simpledialog.askinteger("PDF page", f"Page to open (1–{n}):", parent=self, minvalue=1, maxvalue=n) or 1) - 1
            img, data, suf = load_drawing(p, page)
        except (DrawingError, OSError) as e:
            messagebox.showerror("Open drawing", str(e), parent=self)
            return
        self.ws = Workspace(image_bytes=data, image_suffix=suf, pdf_page=page)
        self.base = img
        m = self.ws.model
        m.image_size = list(img.size)
        m.title = Path(p).stem
        self.rotation = 0.0
        self.result = None
        self.readings = {}
        self._after_load()
        self.fit()
        self.status.configure(text=f"Loaded {Path(p).name} ({img.size[0]}×{img.size[1]} px). Use 'Recognize with AI' or "
                                   "place parts manually, correct them, then 'Confirm model & analyse'.")

    def open_project(self):
        p = filedialog.askopenfilename(parent=self, filetypes=[("Sim Lab drawing project", "*.eelab"), ("All files", "*.*")])
        if not p:
            return
        try:
            self.ws = Workspace.load(p)
            self.base = self.ws.image()
        except Exception as e:
            messagebox.showerror("Open project", f"Cannot open the project: {e}", parent=self)
            return
        self.rotation = float(self.ws.view.get("rotation", 0.0))
        self.enh.update(self.ws.view.get("enhance", {}))
        self.result = None
        self.readings = {}
        self.notes.delete("1.0", "end")
        self.notes.insert("1.0", self.ws.assumptions)
        self._after_load()
        self.fit()

    def save_project(self):
        p = filedialog.asksaveasfilename(parent=self, defaultextension=".eelab",
                                         filetypes=[("Sim Lab drawing project", "*.eelab")])
        if not p:
            return
        self.ws.assumptions = self.notes.get("1.0", "end").strip()
        self.ws.view = {"rotation": self.rotation, "enhance": self.enh}
        if self.result:
            self.ws.findings_snapshot = [f.__dict__ for f in self.result.findings]
        self.ws.save(p)
        self.status.configure(text=f"Saved {p}")

    def export_report(self):
        from eesimlab.drawing.report import build_report

        if not self.result:
            if not messagebox.askyesno("Export report", "The model has not been analysed yet. Analyse now?", parent=self):
                return
            self.run_analysis()
        p = filedialog.asksaveasfilename(parent=self, defaultextension=".html", filetypes=[("HTML report", "*.html")])
        if not p:
            return
        self.ws.assumptions = self.notes.get("1.0", "end").strip()
        base = self._display_image(rotated=False)
        html_text = build_report(self.ws, base, self.result, self.readings, getattr(self, "_last_compare", None),
                                 title=self.model.title or "Drawing analysis")
        Path(p).write_text(html_text, encoding="utf-8")
        self.status.configure(text=f"Report written: {p}")

    def _after_load(self):
        self.selected = None
        self.pending = []
        self._refresh_versions()
        self._refresh_lists()
        self.redraw()

    # ================================================================ recognition
    def recognize(self):
        if self.base is None:
            messagebox.showinfo("Recognize", "Open a drawing first (PNG, JPG or PDF).", parent=self)
            return
        if self.model.components and not messagebox.askyesno(
                "Recognize", "Replace the current model of this version with a new AI reading?", parent=self):
            return
        from eesimlab.ai.providers import AIError, make_provider

        try:
            provider = make_provider(self.settings, cfg.get_api_key(self.settings.provider), self._log)
        except AIError as e:
            messagebox.showerror("Recognize", f"{e}\n\nYou can still build the model manually with the tools.", parent=self)
            return
        img = self._display_image(rotated=True)
        w0, h0 = self.base.size
        W, H = img.size
        rot = self.rotation

        def to_image(x1000, y1000):
            return unrotate_point(x1000 / 1000 * W, y1000 / 1000 * H, w0, h0, rot)

        def job():
            from eesimlab.drawing.recognize import recognize
            from eesimlab.drawing.reconstruct import build_model

            try:
                rec = recognize(img, provider, self._log)
                m = build_model(rec, to_image)
                m.image_size = [w0, h0]
                self.after(0, lambda: self._recognized(m))
            except Exception as e:  # noqa: BLE001 - surface any provider/parse failure
                msg = f"Recognition failed: {e}"
                self.after(0, lambda: messagebox.showerror("Recognize", msg, parent=self))

        self.status.configure(text="Recognising… (the drawing is sent to the configured AI provider)")
        threading.Thread(target=job, daemon=True).start()

    def _recognized(self, m: DrawingModel):
        m.title = m.title or self.model.title
        self.ws.versions[self.ws.current].model = m
        self.rec_warn.delete("1.0", "end")
        self.rec_warn.insert("1.0", "\n".join(m.recognition_warnings) or "None reported.")
        self.result = None
        self.changed()
        low = sum(1 for c in m.components if c.confidence < 0.6 or c.value_confidence < 0.6)
        amb = sum(1 for j in m.junctions if j.kind == "ambiguous")
        self.status.configure(text=f"Recognised {len(m.components)} components, {len(m.nodes)} nodes; {low} low-confidence "
                                   f"items, {amb} ambiguous junctions. Check and correct, then confirm & analyse.")
        self.nb.select(1)

    def _log(self, msg):
        self.after(0, lambda: self.status.configure(text=msg))

    # ================================================================ rendering
    def _display_image(self, rotated=True) -> Image.Image | None:
        if self.base is None:
            return None
        e = self.enh
        img = enhance(self.base, e["contrast"], e["brightness"], e["sharpen"], e["grayscale"], e["autocontrast"],
                      e["threshold"], e["invert"])
        if rotated and self.rotation % 360:
            img = img.rotate(self.rotation, expand=True, fillcolor="white", resample=Image.BICUBIC)
        return img

    def redraw(self):
        c = self.canvas
        c.delete("all")
        w, h = self.sheet_size()
        _, _, W, H = rotate_point(0, 0, w, h, self.rotation)
        c.configure(scrollregion=(0, 0, W * self.zoom, H * self.zoom))
        if self.base is not None and self.layers["image"].get():
            key = (id(self.base), self.zoom, self.rotation, tuple(sorted(self.enh.items())))
            if key != self._render_key:
                img = self._display_image(rotated=True)
                img = img.resize((max(1, int(img.width * self.zoom)), max(1, int(img.height * self.zoom))), Image.LANCZOS)
                self._photo = ImageTk.PhotoImage(img)
                self._render_key = key
            c.create_image(0, 0, image=self._photo, anchor="nw")
        elif self.base is None:
            c.create_rectangle(0, 0, W * self.zoom, H * self.zoom, fill="white", outline="#cbd5e1")
            step = GRID * self.zoom
            if step >= 6:
                for gx in range(0, int(w), GRID * 5):
                    x0, y0 = self.to_canvas(gx, 0)
                    x1, y1 = self.to_canvas(gx, h)
                    c.create_line(x0, y0, x1, y1, fill="#f1f5f9")
                for gy in range(0, int(h), GRID * 5):
                    x0, y0 = self.to_canvas(0, gy)
                    x1, y1 = self.to_canvas(w, gy)
                    c.create_line(x0, y0, x1, y1, fill="#f1f5f9")
        m = self.model
        ncol = {n.id: NODE_COLORS[i % len(NODE_COLORS)] for i, n in enumerate(m.nodes)}
        if self.layers["nets"].get():
            for wre in m.wires:
                pts = [v for p in wre.points for v in self.to_canvas(*p)]
                if len(pts) >= 4:
                    c.create_line(*pts, fill=ncol.get(wre.node, "#334155"), width=2 if self.base is None else 3,
                                  stipple="" if self.base is None else "gray50")
            for n in m.nodes:
                for p in n.points:
                    x, y = self.to_canvas(*p)
                    c.create_oval(x - 3, y - 3, x + 3, y + 3, fill=ncol[n.id], outline="")
                if n.points and (n.name or n.ground):
                    x, y = self.to_canvas(*n.points[0])
                    c.create_text(x + 6, y + 8, text=(n.name or "") + (" ⏚" if n.ground else ""), anchor="nw",
                                  fill=ncol[n.id], font=(FONT, 8, "bold"))
                if n.ground and n.points:
                    x, y = self.to_canvas(*n.points[0])
                    for i, hw in enumerate((9, 6, 3)):
                        c.create_line(x - hw, y + 10 + 3 * i, x + hw, y + 10 + 3 * i, fill="#111827", width=2)
        if self.layers["parts"].get():
            for comp in m.components:
                self._draw_component(comp, ncol)
        if self.layers["junctions"].get():
            for j in m.junctions:
                x, y = self.to_canvas(j.x, j.y)
                col = {"junction": "#059669", "crossing": "#ea580c", "ambiguous": "#dc2626"}[j.kind]
                if j.kind == "crossing":
                    c.create_line(x - 6, y - 6, x + 6, y + 6, fill=col, width=2)
                    c.create_line(x - 6, y + 6, x + 6, y - 6, fill=col, width=2)
                else:
                    c.create_oval(x - 7, y - 7, x + 7, y + 7, outline=col, width=2)
                    if j.kind == "ambiguous":
                        c.create_text(x, y, text="?", fill=col, font=(FONT, 9, "bold"))
        if self.layers["findings"].get() and self.result:
            for i, f in enumerate(self.result.findings, 1):
                for p in f.points[:2]:
                    x, y = self.to_canvas(*p)
                    col = SEV_COLORS.get(f.severity, "#334155")
                    r = 16 if self.highlight == i else 12
                    c.create_oval(x - r, y - r, x + r, y + r, outline=col, width=3 if self.highlight == i else 2)
                    c.create_text(x + r + 2, y - r, text=str(i), fill=col, anchor="sw", font=(FONT, 9, "bold"))
        if self.layers["probes"].get():
            self._draw_probes()
        for p in self.pending:
            x, y = self.to_canvas(*p)
            c.create_oval(x - 5, y - 5, x + 5, y + 5, outline="#dc2626", width=2)

    def _draw_component(self, comp: Component, ncol):
        c = self.canvas
        sel = comp.id == self.selected
        col = "#1d4ed8" if sel else ("#111827" if comp.supported else "#64748b")
        p1 = comp.pins[0] if comp.pins else None
        p2 = comp.pins[1] if len(comp.pins) > 1 else None
        drawn = False
        if comp.source == "user" and p1 and p2 and len(comp.pins) == 2:
            a = self.to_canvas(*p1)
            b = self.to_canvas(*p2)
            sym = comp.kind if comp.kind in ("R", "C", "L", "SW") else "Z"
            from eesimlab import calc_diagram as cd

            old_ink = cd.INK
            cd.INK = col
            try:
                if comp.kind in ("V", "I"):
                    self._draw_source(comp, a, b, col)
                else:
                    cd._part(c, sym if comp.kind != "SW" or comp.state == "open" else "R", *a, *b, "")
                    if comp.kind == "SW" and comp.state == "closed":
                        c.create_line(*a, *b, fill=col, width=2)
            finally:
                cd.INK = old_ink
            mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
            lab = f"{comp.id} {comp.value_text}".strip()
            c.create_text(mx + 10, my - 14, text=lab, fill=col, anchor="sw", font=(FONT, 9, "bold" if sel else "normal"))
            drawn = True
        elif comp.source == "user" and len(comp.pins) > 2 and all(comp.pins):
            pts = [self.to_canvas(*p) for p in comp.pins]
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            x0, y0, x1, y1 = min(xs) + 10, min(ys) - 10, max(xs) - 10, max(ys) + 10
            c.create_rectangle(x0, y0, x1, y1, outline=col, width=2, dash=(4, 2) if not comp.supported else None)
            for p, name in zip(pts, (CAT.get(comp.catalog).pins if comp.catalog else [str(i + 1) for i in range(len(pts))])):
                c.create_text(p[0], p[1] - 8, text=name, fill=HINT, font=(FONT, 7))
            c.create_text((x0 + x1) / 2, (y0 + y1) / 2, text=f"{comp.id}\n{comp.value_text or comp.kind_name()}",
                          fill=col, font=(FONT, 8))
            drawn = True
        if comp.bbox and not drawn:
            corners = [self.to_canvas(comp.bbox[0], comp.bbox[1]), self.to_canvas(comp.bbox[2], comp.bbox[3]),
                       self.to_canvas(comp.bbox[0], comp.bbox[3]), self.to_canvas(comp.bbox[2], comp.bbox[1])]
            xs, ys = [p[0] for p in corners], [p[1] for p in corners]
            ok_col = "#059669" if comp.supported else "#94a3b8"
            low = comp.confidence < 0.6 or (comp.kind in ("R", "L", "C") and (comp.value is None or comp.value_confidence < 0.6))
            c.create_rectangle(min(xs), min(ys), max(xs), max(ys), outline="#1d4ed8" if sel else ("#d97706" if low else ok_col),
                               width=3 if sel else 2, dash=(5, 3) if low else None)
            c.create_text(min(xs), min(ys) - 2, anchor="sw", fill="#1d4ed8" if sel else ok_col, font=(FONT, 9, "bold"),
                          text=f"{comp.id} {comp.value_text or '?'}  {comp.confidence:.0%}")
        for i, p in enumerate(comp.pins):
            if p:
                x, y = self.to_canvas(*p)
                nid = comp.nodes[i] if i < len(comp.nodes) else None
                fill = ncol.get(nid, "#ffffff") if nid else "#ffffff"
                c.create_oval(x - 4, y - 4, x + 4, y + 4, fill=fill, outline=col)
                if comp.kind in ("V", "C") and i == 0 and (comp.kind == "V" or comp.polarized):
                    c.create_text(x + 6, y - 6, text="+", fill="#dc2626", font=(FONT, 10, "bold"))

    def _draw_source(self, comp, a, b, col):
        c = self.canvas
        mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
        r = 14 * max(0.6, min(1.5, self.zoom * 2))
        L = math.hypot(b[0] - a[0], b[1] - a[1]) or 1
        ux, uy = (b[0] - a[0]) / L, (b[1] - a[1]) / L
        c.create_line(*a, mx - ux * r, my - uy * r, fill=col, width=2)
        c.create_line(mx + ux * r, my + uy * r, *b, fill=col, width=2)
        c.create_oval(mx - r, my - r, mx + r, my + r, outline=col, width=2, fill="white")
        s = comp.src
        if comp.kind == "I":
            c.create_line(mx - ux * r * 0.6, my - uy * r * 0.6, mx + ux * r * 0.6, my + uy * r * 0.6, fill=col, width=2, arrow="last")
        elif s and s.wave in ("sine",):
            pts = [(mx - r * 0.6 + i * r * 0.12, my - r * 0.4 * math.sin(i / 10 * 2 * math.pi)) for i in range(11)]
            c.create_line(*[v for p in pts for v in p], fill=col, width=2, smooth=True)
        elif s and s.wave == "square":
            c.create_line(mx - r * .6, my + r * .3, mx - r * .6, my - r * .3, mx, my - r * .3, mx, my + r * .3, mx + r * .6,
                          my + r * .3, mx + r * .6, my - r * .3, fill=col, width=2)
        else:
            c.create_text(mx - ux * r * 0.5, my - uy * r * 0.5, text="+", fill=col, font=(FONT, 9, "bold"))
            c.create_text(mx + ux * r * 0.5, my + uy * r * 0.5, text="−", fill=col, font=(FONT, 9, "bold"))

    def _draw_probes(self):
        c = self.canvas
        m = self.model
        for p in self.ws.probes:
            def tip(nid, col, label):
                if nid and nid in m.node_ids() and m.node(nid).points:
                    x, y = self.to_canvas(*m.node(nid).points[0])
                    c.create_polygon(x, y, x - 7, y - 18, x + 7, y - 18, fill=col, outline="white")
                    c.create_text(x + 9, y - 20, text=label, fill=col, anchor="w", font=(FONT, 8, "bold"))
            if p.kind in ("voltmeter", "ohmmeter"):
                tip(p.red, "#dc2626", f"{p.id}+")
                tip(p.black, "#111827", f"{p.id}−")
            elif p.kind == "scope":
                for k, (n, ref) in enumerate(p.channels):
                    tip(n, ["#ca8a04", "#0891b2", "#c026d3", "#16a34a"][k % 4], f"{p.id} CH{k + 1}")
            elif p.kind == "ammeter" and p.component in [x.id for x in m.components]:
                comp = m.comp(p.component)
                pts = [q for q in comp.pins if q]
                if comp.bbox:
                    pts = [[(comp.bbox[0] + comp.bbox[2]) / 2, (comp.bbox[1] + comp.bbox[3]) / 2]]
                if pts:
                    x, y = self.to_canvas(*pts[0])
                    c.create_oval(x - 11, y - 11, x + 11, y + 11, outline="#7c3aed", width=2, fill="white")
                    c.create_text(x, y, text="A", fill="#7c3aed", font=(FONT, 9, "bold"))
                    c.create_text(x + 13, y - 12, text=p.id, fill="#7c3aed", anchor="w", font=(FONT, 8, "bold"))

    # ================================================================ view controls
    def set_zoom(self, z):
        self.zoom = max(0.05, min(8.0, z))
        self.redraw()

    def set_rotation(self, deg):
        self.rotation = float(deg) % 360
        self.redraw()

    def fit(self):
        self.update_idletasks()
        w, h = self.sheet_size()
        _, _, W, H = rotate_point(0, 0, w, h, self.rotation)
        cw, ch = max(200, self.canvas.winfo_width()), max(200, self.canvas.winfo_height())
        self.set_zoom(min(cw / W, ch / H) * 0.98)

    def enhance_dialog(self):
        if self.base is None:
            messagebox.showinfo("Enhance", "Image enhancement applies to uploaded drawings.", parent=self)
            return
        d = tk.Toplevel(self)
        d.title("Image enhancement")
        d.resizable(True, True)
        vars_ = {"contrast": tk.DoubleVar(value=self.enh["contrast"]), "brightness": tk.DoubleVar(value=self.enh["brightness"])}
        for i, (k, lab) in enumerate((("contrast", "Contrast"), ("brightness", "Brightness"))):
            ttk.Label(d, text=lab).grid(row=i, column=0, sticky="w", padx=6)
            ttk.Scale(d, from_=0.3, to=3.0, variable=vars_[k], length=240).grid(row=i, column=1, padx=6, pady=3)
        flags = {k: tk.BooleanVar(value=bool(self.enh[k])) for k in ("sharpen", "grayscale", "autocontrast", "invert")}
        for i, k in enumerate(flags):
            ttk.Checkbutton(d, text=k.title(), variable=flags[k]).grid(row=2 + i, column=0, columnspan=2, sticky="w", padx=6)
        thr_on = tk.BooleanVar(value=self.enh["threshold"] is not None)
        thr = tk.IntVar(value=self.enh["threshold"] or 160)
        ttk.Checkbutton(d, text="Black/white threshold", variable=thr_on).grid(row=6, column=0, sticky="w", padx=6)
        ttk.Scale(d, from_=40, to=240, variable=thr, length=240).grid(row=6, column=1, padx=6)

        def apply():
            self.enh.update({k: v.get() for k, v in vars_.items()})
            self.enh.update({k: v.get() for k, v in flags.items()})
            self.enh["threshold"] = int(thr.get()) if thr_on.get() else None
            self.redraw()

        def reset():
            self.enh.update({"contrast": 1.0, "brightness": 1.0, "sharpen": False, "grayscale": False, "autocontrast": False,
                             "threshold": None, "invert": False})
            d.destroy()
            self.redraw()

        b = ttk.Frame(d)
        b.grid(row=7, column=0, columnspan=2, pady=6)
        ttk.Button(b, text="Apply", command=apply).pack(side="left")
        ttk.Button(b, text="Reset", command=reset).pack(side="left", padx=4)
        ttk.Button(b, text="Close", command=d.destroy).pack(side="left")

    # ================================================================ tools / clicks
    def _tool_changed(self):
        self.pending = []
        cur = {"pan": "fleur", "select": "arrow"}.get(self.tool.get(), "crosshair")
        self.canvas.configure(cursor=cur)
        hints = {
            "select": "Click a component to edit it; drag to move (editor sheet); Delete removes; 'r' rotates.",
            "pan": "Drag to pan (or use the scrollbars / middle mouse button). Ctrl+wheel zooms.",
            "place": "Pick a part in the Parts catalog, then click to place it. 'r' rotates the part before placing.",
            "wire": "Click a terminal/node, then another: they become one electrical node.",
            "ground": "Click a terminal or node to make it the 0 V reference.",
            "voltmeter": "Click the node for the RED probe, then the node for the BLACK probe.",
            "ammeter": "Click a component: the ammeter is inserted in series with it.",
            "scope": "Click nodes to add channels (reference = ground; change it in Simulate & probe).",
            "ohmmeter": "Click two nodes. The circuit is treated as de-energised.",
            "scale": "Click two points of a known distance to set the scale; afterwards two clicks measure a distance.",
        }
        self.status.configure(text=hints.get(self.tool.get(), ""))
        self.canvas.focus_set()
        self.redraw()

    def _cancel_pending(self):
        self.pending = []
        self.redraw()

    def _nearest_node(self, x, y, radius=None):
        """Node whose point (or a component terminal on it) is nearest within the radius."""
        radius = radius or max(SNAP, 12 / self.zoom)
        best, bd = None, radius
        for n in self.model.nodes:
            for p in n.points:
                d = math.hypot(p[0] - x, p[1] - y)
                if d <= bd:
                    best, bd = n.id, d
        for c in self.model.components:
            for i, p in enumerate(c.pins):
                if p and i < len(c.nodes) and c.nodes[i]:
                    d = math.hypot(p[0] - x, p[1] - y)
                    if d <= bd:
                        best, bd = c.nodes[i], d
        return best

    def _component_at(self, x, y):
        best, bd = None, 30 / self.zoom
        for c in self.model.components:
            if c.bbox and c.bbox[0] <= x <= c.bbox[2] and c.bbox[1] <= y <= c.bbox[3]:
                return c.id
            pins = [p for p in c.pins if p]
            if len(pins) >= 2:
                mx, my = sum(p[0] for p in pins) / len(pins), sum(p[1] for p in pins) / len(pins)
                d = math.hypot(mx - x, my - y)
                if d < bd:
                    best, bd = c.id, d
        return best

    def _click(self, e):
        self.canvas.focus_set()
        x, y = self.from_event(e)
        t = self.tool.get()
        if t == "pan":
            self.canvas.scan_mark(e.x, e.y)
            self._drag = ("pan",)
            return
        if t == "select":
            cid = self._component_at(x, y)
            self.select_component(cid)
            if cid and self.model.comp(cid).source == "user" and self.base is None:
                self._drag = ("move", cid, x, y)
            return
        if t == "place":
            self._place(x, y)
            return
        if t == "ground":
            nid = self._nearest_node(x, y)
            if not nid:
                self.status.configure(text="Click closer to a terminal or node.")
                return
            self.model.node(nid).ground = True
            self.changed()
            return
        if t == "wire":
            nid = self._nearest_node(x, y)
            if not nid:
                self.status.configure(text="Click on a terminal or node (coloured dot).")
                return
            self.pending.append([x, y])
            if len(self.pending) == 1:
                self._wire_first = nid
                self.redraw()
                return
            a, b = self._wire_first, nid
            p0, p1 = self.pending
            self.pending = []
            if a != b:
                self.model.merge_nodes(a, b)
            from eesimlab.drawing.model import Wire

            self.model.wires.append(Wire(a, [p0, [p1[0], p0[1]], p1]))
            self.model.node(a).points += [p0, p1]
            self.changed()
            return
        if t in ("voltmeter", "ohmmeter", "scope"):
            nid = self._nearest_node(x, y)
            if not nid:
                self.status.configure(text="Click on a node (coloured dot) or component terminal.")
                return
            if t == "scope":
                gnd = (self.model.ground_nodes() or [None])[0]
                existing = next((p for p in self.ws.probes if p.kind == "scope"), None)
                if existing and len(existing.channels) < 4:
                    existing.channels.append([nid, gnd])
                else:
                    self.ws.probes.append(Probe(self.ws.next_probe_id(), "scope", channels=[[nid, gnd]], mode="tran"))
                self._probe_added()
                return
            self.pending.append([x, y])
            if len(self.pending) == 1:
                self._probe_first = nid
                self.status.configure(text=f"{'Red' if t == 'voltmeter' else 'First'} probe on {self.model.node_label(nid)}; "
                                           "now click the second node.")
                self.redraw()
                return
            self.pending = []
            self.ws.probes.append(Probe(self.ws.next_probe_id(), t, red=self._probe_first, black=nid,
                                        mode=self.sv_mode.get() if t == "voltmeter" else "dc"))
            self._probe_added()
            return
        if t == "ammeter":
            cid = self._component_at(x, y)
            if not cid:
                self.status.configure(text="Click on a component: the ammeter goes in series with it.")
                return
            self.ws.probes.append(Probe(self.ws.next_probe_id(), "ammeter", component=cid, mode=self.sv_mode.get()))
            self._probe_added()
            return
        if t == "scale":
            self.pending.append([x, y])
            if len(self.pending) < 2:
                self.redraw()
                return
            (x0, y0), (x1, y1) = self.pending
            self.pending = []
            dpx = math.hypot(x1 - x0, y1 - y0)
            if self.model.scale:
                sc = self.model.scale
                dist = dpx / sc["px_per_unit"]
                if messagebox.askyesno("Measure", f"Distance = {dist:.4g} {sc['unit']} (from your scale reference).\n\n"
                                                  "Set a new scale instead?", parent=self):
                    self.model.scale = None
                    self.status.configure(text="Scale cleared: click two points of a known distance.")
                self.redraw()
                return
            txt = simpledialog.askstring("Set scale", "Real distance between the two points (e.g. '50 mm'):", parent=self)
            if txt:
                parts = txt.strip().split()
                try:
                    val = float(parts[0])
                    unit = parts[1] if len(parts) > 1 else "mm"
                    if val <= 0 or dpx == 0:
                        raise ValueError
                    self.model.scale = {"px_per_unit": dpx / val, "unit": unit}
                    self.status.configure(text=f"Scale set: {dpx / val:.4g} px per {unit}.")
                except ValueError:
                    messagebox.showerror("Set scale", "Enter a positive number and a unit, e.g. 50 mm.", parent=self)
            self.redraw()

    def _drag_move(self, e):
        if not self._drag:
            return
        if self._drag[0] == "pan":
            self.canvas.scan_dragto(e.x, e.y, gain=1)
            return
        if self._drag[0] == "move":
            _, cid, x0, y0 = self._drag
            x, y = self.from_event(e)
            dx = round((x - x0) / GRID) * GRID
            dy = round((y - y0) / GRID) * GRID
            if dx or dy:
                self._move_component(cid, dx, dy)
                self._drag = ("move", cid, x0 + dx, y0 + dy)
                self.redraw()

    def _drag_end(self, e):
        if self._drag and self._drag[0] == "move":
            self.changed()
        self._drag = None

    def _move_component(self, cid, dx, dy):
        c = self.model.comp(cid)
        for i, p in enumerate(c.pins):
            if not p:
                continue
            old = list(p)
            c.pins[i] = [p[0] + dx, p[1] + dy]
            nid = c.nodes[i] if i < len(c.nodes) else None
            if nid:
                n = self.model.node(nid)
                n.points = [c.pins[i] if q == old else q for q in n.points]
                for w in self.model.wires:
                    if w.node == nid:
                        w.points = [c.pins[i] if q == old else q for q in w.points]

    def _hover(self, e):
        if self.tool.get() in ("pan",):
            return
        x, y = self.from_event(e)
        nid = self._nearest_node(x, y)
        txt = f"x={x:.0f}, y={y:.0f}"
        if nid:
            txt += f"   node {self.model.node_label(nid)}"
        if self.model.scale:
            txt += f"   (scale: {self.model.scale['px_per_unit']:.4g} px/{self.model.scale['unit']})"
        self.status.configure(text=txt)

    # ================================================================ placing / editing parts
    def _fill_catalog(self):
        self.cat_list.delete(*self.cat_list.get_children())
        cat = self.cat_cat.get()
        for p in CAT.search(self.cat_search.get(), None if cat == "All categories" else cat, limit=800):
            self.cat_list.insert("", "end", iid=p.key, values=(p.name, p.category, "yes" if p.simulated else "no"))

    def _catalog_pick(self, _e=None):
        sel = self.cat_list.selection()
        if sel:
            self.place_part = CAT.get(sel[0])
            self.tool.set("place")
            self._tool_changed()
            self.status.configure(text=f"Placing {self.place_part.name}: click on the sheet ('r' rotates).")

    def _place(self, x, y):
        part = self.place_part
        if part is None:
            messagebox.showinfo("Place part", "Choose a part in the Parts catalog first.", parent=self)
            self.nb.select(0)
            return
        if self.base is None:
            x, y = round(x / GRID) * GRID, round(y / GRID) * GRID
        m = self.model
        if part.kind == "GND":
            nid = self._nearest_node(x, y, radius=25 / self.zoom)
            if nid is None:
                nid = m.add_node([x, y]).id
            m.node(nid).ground = True
            self.changed()
            return
        npins = len(part.pins)
        span = 80
        rot = self.place_rot
        if npins == 2:
            offs = [(-span / 2, 0), (span / 2, 0)]
        else:
            left = [(-span / 2 - 20, (i - (npins // 2 - 1) / 2) * 40) for i in range(npins // 2)]
            right = [(span / 2 + 20, (i - (npins - npins // 2 - 1) / 2) * 40) for i in range(npins - npins // 2)]
            offs = left + right
        pins = []
        for ox, oy in offs:
            a = math.radians(rot)
            rx, ry = ox * math.cos(a) - oy * math.sin(a), ox * math.sin(a) + oy * math.cos(a)
            pins.append([round(x + rx, 1), round(y + ry, 1)])
        kind = part.kind if part.kind in SUPPORTED_KINDS or part.kind in UNSUPPORTED_KINDS else "X"
        base_id = {"V": "V", "I": "I", "R": "R", "C": "C", "L": "L", "SW": "S", "D": "D", "LED": "D", "Q": "Q", "M": "M",
                   "U": "U", "F": "F", "T": "T", "LAMP": "LP", "MOTOR": "MOT", "X": "X"}.get(kind, "X")
        n = 1
        ids = {c.id for c in m.components}
        while f"{base_id}{n}" in ids:
            n += 1
        comp = Component(f"{base_id}{n}", kind, label=f"{base_id}{n}", value=part.value, value_text=part.value_text,
                         pins=pins, nodes=[None] * npins, source="user", catalog=part.key, rotation=rot)
        pr = part.params
        comp.polarized = bool(pr.get("polarized"))
        comp.rating_power = pr.get("rating_power")
        comp.rating_voltage = pr.get("rating_voltage")
        comp.state = pr.get("state", "")
        if pr.get("src"):
            comp.src = SourceParams(**pr["src"])
            s = comp.src
            comp.value_text = (f"{s.dc:g} V" if s.wave == "dc" else f"{s.wave} {s.amp:g} Vp {s.freq:g} Hz") if kind == "V" else comp.value_text
        for i, p in enumerate(pins):
            nid = self._nearest_node(p[0], p[1], radius=SNAP)
            if nid is None:
                nid = m.add_node(p).id
            else:
                m.node(nid).points.append(p)
            comp.nodes[i] = nid
        m.components.append(comp)
        self.selected = comp.id
        self.changed()

    def rotate_selected(self):
        if self.tool.get() == "place":
            self.place_rot = (self.place_rot + 90) % 360
            self.status.configure(text=f"Placement rotation {self.place_rot}°")
            return
        if not self.selected:
            return
        c = self.model.comp(self.selected)
        pins = [p for p in c.pins if p]
        if c.source != "user" or len(pins) != len(c.pins):
            return
        cx, cy = sum(p[0] for p in pins) / len(pins), sum(p[1] for p in pins) / len(pins)
        for i, p in enumerate(list(c.pins)):
            nx, ny = cx - (p[1] - cy), cy + (p[0] - cx)
            dx, dy = nx - p[0], ny - p[1]
            old = list(p)
            c.pins[i] = [nx, ny]
            nid = c.nodes[i]
            if nid:
                node = self.model.node(nid)
                node.points = [c.pins[i] if q == old else q for q in node.points]
        c.rotation = (c.rotation + 90) % 360
        self.changed()

    def delete_selected(self):
        if not self.selected:
            return
        cid = self.selected
        self.model.components = [c for c in self.model.components if c.id != cid]
        self.ws.probes = [p for p in self.ws.probes if p.component != cid]
        self.model.remove_unused_nodes()
        self.selected = None
        self.changed()

    def select_component(self, cid):
        self.selected = cid
        self.redraw()
        if cid and self.comp_tree.exists(cid):
            self.comp_tree.selection_set(cid)
            self.comp_tree.see(cid)
            self.nb.select(1)
        self._show_props()

    def _comp_pick(self, _e=None):
        sel = self.comp_tree.selection()
        if sel and sel[0] != self.selected:
            self.selected = sel[0]
            self.redraw()
            self._show_props()

    def _show_props(self):
        body = self.props.body
        for w in body.winfo_children():
            w.destroy()
        if not self.selected:
            ttk.Label(body, text="Select a component on the drawing or in the list to edit it.", foreground=HINT).pack(anchor="w")
            return
        c = self.model.comp(self.selected)
        v = {}
        row = 0

        def add(label, widget):
            nonlocal row
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", padx=(0, 6), pady=1)
            widget.grid(row=row, column=1, sticky="w", pady=1)
            row += 1

        v["id"] = tk.StringVar(value=c.id)
        add("ID", ttk.Entry(body, textvariable=v["id"], width=12))
        kinds = list(SUPPORTED_KINDS) + list(UNSUPPORTED_KINDS)
        v["kind"] = tk.StringVar(value=c.kind)
        add("Type", ttk.Combobox(body, state="readonly", values=kinds, textvariable=v["kind"], width=10))
        ttk.Label(body, text=f"({c.kind_name()}; recognised with {c.confidence:.0%} confidence)" if c.source == "ai" else f"({c.kind_name()})",
                  foreground=HINT).grid(row=row - 1, column=2, sticky="w")
        v["label"] = tk.StringVar(value=c.label)
        add("Label", ttk.Entry(body, textvariable=v["label"], width=16))
        v["value_text"] = tk.StringVar(value=c.value_text)
        add("Value as printed", ttk.Entry(body, textvariable=v["value_text"], width=16))
        ttk.Label(body, text="e.g. 4k7, 10uF, 100mH" + (f"  [read with {c.value_confidence:.0%} confidence]" if c.source == "ai" else ""),
                  foreground=HINT).grid(row=row - 1, column=2, sticky="w")
        nodes = ["(unconnected)"] + [n.id for n in self.model.nodes] + ["(new node)"]
        v["nodes"] = []
        names = CAT.get(c.catalog).pins if c.catalog else [str(i + 1) for i in range(len(c.nodes))]
        for i, nid in enumerate(c.nodes):
            var = tk.StringVar(value=nid or "(unconnected)")
            v["nodes"].append(var)
            add(f"Terminal {names[i] if i < len(names) else i + 1}" + (" (+)" if i == 0 and c.kind in ("V",) else ""),
                ttk.Combobox(body, state="readonly", values=nodes, textvariable=var, width=14))
        if c.kind in ("V", "I"):
            s = c.src or SourceParams()
            v["wave"] = tk.StringVar(value=s.wave)
            add("Waveform", ttk.Combobox(body, state="readonly", values=["dc", "sine", "square", "step"], textvariable=v["wave"], width=10))
            unit = "V" if c.kind == "V" else "A"
            for key, lab in (("dc", f"DC / step value ({unit})"), ("amp", f"Amplitude, PEAK ({unit})"), ("freq", "Frequency (Hz)"),
                             ("offset", f"Offset ({unit})"), ("phase_deg", "Phase (°)")):
                val = getattr(s, key)
                v[key] = tk.StringVar(value="" if val is None else repr(val))
                add(lab, ttk.Entry(body, textvariable=v[key], width=12))
        if c.kind == "SW":
            v["state"] = tk.StringVar(value=c.state or "")
            add("Switch state", ttk.Combobox(body, state="readonly", values=["open", "closed"], textvariable=v["state"], width=10))
        if c.kind == "C":
            v["polarized"] = tk.BooleanVar(value=c.polarized)
            add("Polarised (terminal 1 = +)", ttk.Checkbutton(body, variable=v["polarized"]))
        for key, lab in (("rating_power", "Power rating (W)"), ("rating_voltage", "Voltage rating (V)"), ("rating_current", "Current rating (A)")):
            val = getattr(c, key)
            v[key] = tk.StringVar(value="" if val is None else repr(val))
            add(lab, ttk.Entry(body, textvariable=v[key], width=12))
        ttk.Label(body, text=c.notes, foreground=HINT, wraplength=360).grid(row=row, column=0, columnspan=3, sticky="w")
        row += 1
        b = ttk.Frame(body)
        b.grid(row=row, column=0, columnspan=3, sticky="w", pady=6)
        ttk.Button(b, text="Apply changes", command=lambda: self._apply_props(c.id, v)).pack(side="left")
        ttk.Button(b, text="Delete", command=self.delete_selected).pack(side="left", padx=4)
        ttk.Button(b, text="Rotate 90°", command=self.rotate_selected).pack(side="left")

    def _apply_props(self, cid, v):
        m = self.model
        c = m.comp(cid)
        new_id = v["id"].get().strip()
        if new_id != c.id:
            if not new_id or any(x.id == new_id for x in m.components):
                messagebox.showerror("Component", "The ID must be unique and not empty.", parent=self)
                return
            for p in self.ws.probes:
                if p.component == c.id:
                    p.component = new_id
            c.id = new_id
        c.kind = v["kind"].get()
        c.label = v["label"].get().strip()
        c.value_text = v["value_text"].get().strip()
        c.source = "user" if c.source == "user" else c.source
        c.confidence = 1.0
        c.value_confidence = 1.0
        if c.kind in ("R", "L", "C"):
            val, problem = parse_value(c.value_text, c.kind)
            if val is None and c.value_text:
                messagebox.showwarning("Value", f"{problem}. The value is left blank (not guessed).", parent=self)
            c.value = val
        for i, var in enumerate(v["nodes"]):
            sel = var.get()
            if sel == "(unconnected)":
                c.nodes[i] = None
            elif sel == "(new node)":
                c.nodes[i] = m.add_node(c.pins[i] if c.pins[i] else None).id
            else:
                c.nodes[i] = sel
        if c.kind in ("V", "I"):
            def num(key):
                t = v[key].get().strip() if key in v else ""
                if not t:
                    return None
                return float(t)

            try:
                c.src = SourceParams(v["wave"].get(), dc=num("dc"), amp=num("amp"), freq=num("freq"),
                                     offset=num("offset") or 0.0, phase_deg=num("phase_deg") or 0.0)
            except ValueError:
                messagebox.showerror("Source", "Source values must be plain numbers in V/A/Hz/°.", parent=self)
                return
        if c.kind == "SW" and "state" in v:
            c.state = v["state"].get()
        if "polarized" in v:
            c.polarized = v["polarized"].get()
        for key in ("rating_power", "rating_voltage", "rating_current"):
            t = v[key].get().strip()
            try:
                setattr(c, key, float(t) if t else None)
            except ValueError:
                messagebox.showerror("Rating", f"{key.replace('_', ' ')} must be a number.", parent=self)
                return
        self.selected = c.id
        m.remove_unused_nodes()
        self.changed()
        self._show_props()

    # ================================================================ lists
    def _refresh_versions(self):
        names = [v.name for v in self.ws.versions]
        self.version_cb.configure(values=names)
        self.version_cb.set(names[self.ws.current])
        for cb in (self.cmp_a, self.cmp_b):
            cb.configure(values=names)
        self.cmp_a.set(names[0])
        self.cmp_b.set(names[-1])

    def _switch_version(self, _e=None):
        self.ws.current = self.version_cb.current()
        self.result = None
        self.readings = {}
        self.selected = None
        self.changed()

    def new_revision(self):
        name = simpledialog.askstring("New revision", "Name for the proposed revision:", parent=self,
                                      initialvalue=f"Revision {len(self.ws.versions)}")
        if not name:
            return
        self.ws.new_revision(name)
        self._refresh_versions()
        self.result = None
        self.changed()
        self.status.configure(text=f"Editing '{name}'. The original is kept unchanged; compare them in the Compare tab.")

    def _refresh_lists(self):
        m = self.model
        t = self.comp_tree
        t.delete(*t.get_children())
        for c in m.components:
            val = c.value_text or ("" if c.value is None else f"{c.value:g}")
            if c.kind in ("V", "I") and c.src:
                s = c.src
                val = f"{s.wave} " + (f"{s.dc}" if s.wave in ("dc", "step") else f"{s.amp} pk {s.freq} Hz")
            if c.kind == "SW":
                val = c.state or "state?"
            t.insert("", "end", iid=c.id, text=c.id,
                     values=(c.kind_name(), val or "?", ", ".join(m.node_label(n) for n in c.nodes),
                             f"{min(c.confidence, c.value_confidence):.0%}" if c.source == "ai" else "user"))
        nt = self.node_tree
        nt.delete(*nt.get_children())
        for n in m.nodes:
            terms = ", ".join(f"{cid}.{i + 1}" for cid, i in m.terminals_at(n.id))
            nt.insert("", "end", iid=n.id, text=n.id, values=(n.name, "yes" if n.ground else "", terms))
        jt = self.junc_tree
        jt.delete(*jt.get_children())
        for j in m.junctions:
            jt.insert("", "end", iid=j.id, text=j.id, values=(j.kind, f"{j.confidence:.0%}", ", ".join(j.nodes), j.note))
        nodes = [n.id for n in m.nodes if not n.ground]
        self.out_cb.configure(values=[""] + nodes)
        self.rec_warn.delete("1.0", "end")
        self.rec_warn.insert("1.0", "\n".join(m.recognition_warnings) or "None.")
        self._refresh_probes()

    def _rename_node(self):
        sel = self.node_tree.selection()
        if not sel:
            return
        n = self.model.node(sel[0])
        name = simpledialog.askstring("Rename node", "Name (e.g. Vout):", parent=self, initialvalue=n.name)
        if name is not None:
            n.name = name.strip()
            self.changed()

    def _toggle_ground(self):
        for nid in self.node_tree.selection():
            n = self.model.node(nid)
            n.ground = not n.ground
        self.changed()

    def _merge_nodes(self):
        sel = list(self.node_tree.selection())
        if len(sel) < 2:
            messagebox.showinfo("Merge nodes", "Select two or more nodes to join them electrically.", parent=self)
            return
        for other in sel[1:]:
            self.model.merge_nodes(sel[0], other)
        self.changed()

    def _set_junction(self, kind):
        sel = self.junc_tree.selection()
        if not sel:
            messagebox.showinfo("Junction", "Select a wire meeting point first.", parent=self)
            return
        self.model.set_junction(sel[0], kind)
        self.changed()

    # ================================================================ analysis
    def _apply_settings(self):
        s = self.ws.settings

        def num(k, cast=float):
            t = self.sv[k].get().strip()
            return cast(t) if t else None

        try:
            s.analysis_freq = num("freq")
            s.duration = num("duration")
            s.steps = num("steps", int) or 4000
            s.f_min = num("fmin")
            s.f_max = num("fmax")
        except ValueError:
            messagebox.showerror("Settings", "Use plain numbers (Hz, seconds, steps).", parent=self)
            return
        s.initial = self.sv_initial.get()
        s.output_node = self.sv_output.get() or None
        self._mark_outdated()
        self.status.configure(text="Settings applied. Results computed with the old settings are marked outdated.")

    def run_analysis(self):
        self.model.confirmed = True
        self.config(cursor="watch")
        self.update_idletasks()
        try:
            self.result = analyse(self.model, self.ws.settings)
        except Exception:
            messagebox.showerror("Analysis", "Analysis failed:\n" + traceback.format_exc(limit=4), parent=self)
            return
        finally:
            self.config(cursor="")
        self._show_findings()
        self._show_calcs()
        self.measure_all()
        self.redraw()
        n = {s: sum(1 for f in self.result.findings if f.severity == s) for s in ("problem", "concern", "missing")}
        self.status.configure(text=f"Analysed: {n['problem']} problem(s), {n['concern']} concern(s), {n['missing']} item(s) of "
                                   "missing information.")
        self.nb.select(3)

    def _show_findings(self):
        t = self.find_tree
        t.delete(*t.get_children())
        if not self.result:
            return
        groups = {"problem": "Confirmed problems", "concern": "Possible concerns", "missing": "Missing information"}
        for sev, lab in groups.items():
            items = [(i, f) for i, f in enumerate(self.result.findings, 1) if f.severity == sev]
            node = t.insert("", "end", iid=f"g_{sev}", text=f"{lab} ({len(items)})", open=True)
            for i, f in items:
                t.insert(node, "end", iid=f"f{i}", text=f"#{i} {f.title}", values=(f.location(self.model),), tags=(sev,))
        for sev, col in SEV_COLORS.items():
            t.tag_configure(sev, foreground=col)
        if self.result.recommendations:
            node = t.insert("", "end", iid="g_rec", text=f"Recommended improvements ({len(self.result.recommendations)})", open=True)
            for k, r in enumerate(self.result.recommendations):
                t.insert(node, "end", iid=f"r{k}", text=r["title"])

    def _finding_pick(self, _e=None):
        sel = self.find_tree.selection()
        self.find_detail.delete("1.0", "end")
        if not sel or not self.result:
            return
        iid = sel[0]
        if iid.startswith("f"):
            i = int(iid[1:])
            f = self.result.findings[i - 1]
            self.highlight = i
            self.find_detail.insert("1.0", f"{f.title}\nWhere: {f.location(self.model)}\nCause: {f.cause}\nLikely effect: {f.effect}\n"
                                           f"Recommended correction: {f.fix}")
            if f.points:
                x, y = self.to_canvas(*f.points[0])
                sr = self.canvas.cget("scrollregion").split()
                if sr:
                    W, H = float(sr[2]), float(sr[3])
                    self.canvas.xview_moveto(max(0, (x - self.canvas.winfo_width() / 2) / max(W, 1)))
                    self.canvas.yview_moveto(max(0, (y - self.canvas.winfo_height() / 2) / max(H, 1)))
            self.redraw()
        elif iid.startswith("r"):
            r = self.result.recommendations[int(iid[1:])]
            self.find_detail.insert("1.0", f"{r['title']}\n{r['detail']}\nBenefit: {r['benefit']}\nTrade-off: {r['tradeoff']}\n"
                                           f"Information needed to validate: {r['needs']}\n\nTip: create a revision (toolbar), apply "
                                           "the change there and compare it with the original.")

    def _show_calcs(self):
        t = self.calc_tree
        t.delete(*t.get_children())
        if not self.result:
            return
        self.outdated.configure(text="")
        if self.result.operation:
            op = t.insert("", "end", text="How the circuit should operate (from the confirmed topology)", open=True)
            for line in self.result.operation:
                t.insert(op, "end", text=line)
        for c in self.result.calculations:
            node = t.insert("", "end", text=("" if c.ok else "✖ ") + c.title, open=True)
            t.insert(node, "end", text=f"Method: {c.method}")
            for line in c.results:
                t.insert(node, "end", text=line)
            for title, items in (("Inputs", c.inputs), ("Working", c.working), ("Assumptions", c.assumptions)):
                if items:
                    sub = t.insert(node, "end", text=title, open=False)
                    for line in items:
                        t.insert(sub, "end", text=line)

    # ================================================================ probes
    def _probe_added(self):
        self._refresh_probes()
        self.measure_all()
        self.redraw()
        self.nb.select(5)

    def _probe_where(self, p) -> str:
        m = self.model
        if p.kind in ("voltmeter", "ohmmeter"):
            return f"+{m.node_label(p.red)}  −{m.node_label(p.black)}"
        if p.kind == "ammeter":
            return f"in series with {p.component}"
        return "; ".join(f"CH{k + 1} {m.node_label(n)} vs {m.node_label(r)}" for k, (n, r) in enumerate(p.channels))

    def _refresh_probes(self):
        t = self.probe_tree
        sel = t.selection()
        t.delete(*t.get_children())
        for p in self.ws.probes:
            r = self.readings.get(p.id)
            if r is None:
                reading, status = "", "not measured"
            else:
                reading = (r.lines[1] if len(r.lines) > 1 else "") if r.ok else "needs input"
                status = "OUTDATED" if r.stale(self.model, self.ws.settings) else "current"
            t.insert("", "end", iid=p.id, text=p.id, values=(KINDS[p.kind], p.mode if p.kind in ("voltmeter", "ammeter") else
                                                            ("tran" if p.kind == "scope" else "de-energised"),
                                                            self._probe_where(p), reading, status))
        if sel and t.exists(sel[0]):
            t.selection_set(sel[0])

    def measure_all(self):
        cache = {}
        self.readings = {p.id: measure(self.model, p, self.ws.settings, cache) for p in self.ws.probes}
        self._refresh_probes()
        self._show_probe()

    def _selected_probe(self):
        sel = self.probe_tree.selection()
        return next((p for p in self.ws.probes if sel and p.id == sel[0]), None)

    def _show_probe(self):
        p = self._selected_probe() or (self.ws.probes[-1] if self.ws.probes else None)
        self.probe_detail.delete("1.0", "end")
        self.fig.clear()
        if p is None:
            self.fig_canvas.draw_idle()
            return
        r = self.readings.get(p.id)
        if r is None:
            self.probe_detail.insert("1.0", "Not measured yet: click 'Measure all'.")
        else:
            stale = r.stale(self.model, self.ws.settings)
            txt = ("⚠ OUTDATED — the model or settings changed since this reading. Click 'Measure all'.\n" if stale else "")
            txt += f"{p.id} {KINDS[p.kind]} — {r.reference}\n" + "\n".join(r.lines)
            if r.needs:
                txt += "\nTo get a valid result, supply:\n" + "\n".join(f"• {x}" for x in r.needs)
            txt += "\nAssumptions: " + " ".join(r.assumptions)
            self.probe_detail.insert("1.0", txt)
            if r.waveform:
                from matplotlib.ticker import EngFormatter

                ax = self.fig.add_subplot(1, 1, 1)
                for lab, y in r.waveform["series"].items():
                    ax.plot(r.waveform["t"], y, label=lab)
                ax.xaxis.set_major_formatter(EngFormatter("s"))
                ax.yaxis.set_major_formatter(EngFormatter(r.waveform.get("unit", "")))
                ax.grid(True, color="#e5e7eb")
                ax.legend(fontsize=7)
                ax.set_title("SIMULATED", fontsize=9, loc="left", color="#92400e")
                self.fig.tight_layout()
        self.fig_canvas.draw_idle()

    def _plot_sweep(self):
        if not self.result or not self.result.sweep:
            messagebox.showinfo("Frequency response", "Run the analysis with a source, at least one L or C and an output node "
                                                       "(Simulation settings) first.", parent=self)
            return
        import numpy as np
        from matplotlib.ticker import EngFormatter

        sw = self.result.sweep
        self.fig.clear()
        ax1 = self.fig.add_subplot(2, 1, 1)
        ax2 = self.fig.add_subplot(2, 1, 2, sharex=ax1)
        with np.errstate(divide="ignore"):
            ax1.semilogx(sw["f"], 20 * np.log10(np.maximum(np.abs(sw["h"]), 1e-300)))
        ax2.semilogx(sw["f"], np.degrees(np.unwrap(np.angle(sw["h"]))))
        ax1.set_ylabel("dB")
        ax2.set_ylabel("°")
        ax2.xaxis.set_major_formatter(EngFormatter("Hz"))
        for ax in (ax1, ax2):
            ax.grid(True, which="both", color="#e5e7eb")
        ax1.set_title(f"SIMULATED response at {self.model.node_label(sw['node'])} ({sw['drive']} = 1 peak)", fontsize=9, loc="left")
        self.fig.tight_layout()
        self.fig_canvas.draw_idle()

    def _delete_probe(self):
        p = self._selected_probe()
        if p:
            self.ws.probes.remove(p)
            self.readings.pop(p.id, None)
            self._refresh_probes()
            self.redraw()

    def _cycle_mode(self):
        p = self._selected_probe()
        if p and p.kind in ("voltmeter", "ammeter"):
            order = ["dc", "ac", "tran"]
            p.mode = order[(order.index(p.mode) + 1) % 3]
            self.measure_all()

    def _scope_ref(self):
        p = self._selected_probe()
        if not p or p.kind != "scope":
            messagebox.showinfo("Scope", "Select a scope probe first.", parent=self)
            return
        nid = simpledialog.askstring("Scope reference", "Reference node id for all channels (e.g. N3):", parent=self)
        if nid and nid in self.model.node_ids():
            for ch in p.channels:
                ch[1] = nid
            self.measure_all()

    def compare(self):
        from eesimlab.drawing.report import compare_versions

        a = self.ws.versions[self.cmp_a.current()].model
        b = self.ws.versions[self.cmp_b.current()].model
        rows = compare_versions(a, b, self.ws.settings)
        for p in self.ws.probes:
            ra, rb = measure(a, p, self.ws.settings), measure(b, p, self.ws.settings)
            fa = ra.lines[1] if ra.ok and len(ra.lines) > 1 else "; ".join(ra.needs) or "—"
            fb = rb.lines[1] if rb.ok and len(rb.lines) > 1 else "; ".join(rb.needs) or "—"
            rows.append([f"{p.id} {KINDS[p.kind]} (simulated)", fa, fb, ""])
        self._last_compare = rows
        t = self.cmp_tree
        t.delete(*t.get_children())
        for row in rows:
            t.insert("", "end", values=row)
