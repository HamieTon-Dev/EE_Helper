"""Desktop app (tkinter, ships with Python so the .exe needs no extra UI runtime)."""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from mscopilot import __version__
from mscopilot import config as cfg
from mscopilot.inputs import SUPPORTED_EXT
from mscopilot.ui_common import ScrollFrame, with_scrollbars

PLACEHOLDER = ("e.g. Verify this 2nd-order Sallen-Key low-pass: fc = 1 kHz ±5 %, passband gain 0 dB, "
               "show a Bode plot 10 Hz-100 kHz, a scope view of a 500 Hz 1 Vpk sine, and the DC operating point.")

FILETYPES = [
    ("Design inputs", " ".join(f"*{e}" for e in sorted(SUPPORTED_EXT))),
    ("Word documents", "*.docx"),
    ("Images", "*.png *.jpg *.jpeg *.bmp *.gif *.webp"),
    ("Multisim circuits", "*.ms14 *.ms13 *.ms12"),
    ("SPICE netlists", "*.cir *.net *.sp *.spice"),
    ("All files", "*.*"),
]


def open_path(p: Path):
    if sys.platform == "win32":
        os.startfile(str(p))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(p)])
    else:
        subprocess.Popen(["xdg-open", str(p)])


class SettingsDialog(tk.Toplevel):
    def __init__(self, master, settings: cfg.Settings):
        super().__init__(master)
        self.title("Settings")
        self.s = settings
        self.resizable(True, True)
        self.minsize(420, 300)
        self.geometry("700x640")
        self.transient(master)
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        nb = ttk.Notebook(self)
        nb.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        sf1 = ScrollFrame(nb, padding=14)
        nb.add(sf1, text="AI & tools")
        f = sf1.body
        sf2 = ScrollFrame(nb, padding=14)
        nb.add(sf2, text="Lab report")
        lab = sf2.body
        self._build_lab_tab(lab, settings)
        row = 0

        def add(label, widget):
            nonlocal row
            ttk.Label(f, text=label).grid(row=row, column=0, sticky="w", pady=3, padx=(0, 10))
            widget.grid(row=row, column=1, sticky="ew", pady=3)
            row += 1

        self.provider = tk.StringVar(value=settings.provider)
        labels = {k: v[0] for k, v in cfg.PROVIDERS.items()}
        cb = ttk.Combobox(f, state="readonly", values=[labels[k] for k in cfg.PROVIDERS], width=44)
        cb.set(labels.get(settings.provider, labels["anthropic"]))
        cb.bind("<<ComboboxSelected>>", lambda e: self._provider_changed(cb.get(), labels))
        add("AI provider", cb)
        self.model = tk.StringVar(value=settings.model)
        add("Model (blank = default)", ttk.Entry(f, textvariable=self.model, width=46))
        self.model_hint = ttk.Label(f, foreground="#64748b")
        self.model_hint.grid(row=row, column=1, sticky="w")
        row += 1
        self.key = tk.StringVar(value=cfg.get_api_key(settings.provider))
        add("API key", ttk.Entry(f, textvariable=self.key, show="•", width=46))
        self.base_url = tk.StringVar(value=settings.base_url)
        add("Endpoint URL (Other only)", ttk.Entry(f, textvariable=self.base_url, width=46))
        self.effort = tk.StringVar(value=settings.effort)
        add("Claude effort", ttk.Combobox(f, state="readonly", textvariable=self.effort, values=["low", "medium", "high", "xhigh", "max"], width=12))
        ttk.Separator(f).grid(row=row, column=0, columnspan=3, sticky="ew", pady=8)
        row += 1
        self.ngspice = tk.StringVar(value=settings.ngspice_path)
        add("ngspice (blank = auto)", self._file_row(f, self.ngspice))
        self.msexe = tk.StringVar(value=settings.multisim_exe)
        add("multisim.exe (blank = auto)", self._file_row(f, self.msexe))
        self.worker = tk.StringVar(value=settings.multisim_worker)
        add("COM worker (blank = auto)", self._file_row(f, self.worker))
        self.use_com = tk.BooleanVar(value=settings.use_multisim_com)
        add("", ttk.Checkbutton(f, text="Use Multisim Automation API (COM) for .ms14 inputs", variable=self.use_com))
        self.use_gui = tk.BooleanVar(value=settings.use_multisim_gui)
        add("", ttk.Checkbutton(f, text="Drive Multisim UI and capture instrument screenshots", variable=self.use_gui))
        self.settle = tk.DoubleVar(value=settings.gui_settle_seconds)
        add("Simulation settle time (s)", ttk.Spinbox(f, from_=1, to=120, increment=1, textvariable=self.settle, width=8))
        self.iters = tk.IntVar(value=settings.max_design_iterations)
        add("Max AI design iterations", ttk.Spinbox(f, from_=1, to=6, textvariable=self.iters, width=8))
        self.out = tk.StringVar(value=str(settings.resolved_output_root()))
        add("Output folder", self._file_row(f, self.out, folder=True))
        btns = ttk.Frame(self)
        btns.grid(row=1, column=0, sticky="e", padx=12, pady=(0, 12))
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="right", padx=4)
        ttk.Button(btns, text="Save", command=self._save).pack(side="right")
        self._provider_changed(cb.get(), labels)
        self.grab_set()

    REPORT_STYLES = {"both": "Lab report (.docx) + engineering report (HTML)",
                     "lab": "Lab report (.docx) only",
                     "engineering": "Engineering report (HTML + .docx) only"}

    def _build_lab_tab(self, f, s: cfg.Settings):
        row = 0

        def add(label, widget, sticky="ew"):
            nonlocal row
            ttk.Label(f, text=label).grid(row=row, column=0, sticky="nw", pady=3, padx=(0, 10))
            widget.grid(row=row, column=1, sticky=sticky, pady=3)
            row += 1

        self.style_cb = ttk.Combobox(f, state="readonly", values=list(self.REPORT_STYLES.values()), width=44)
        self.style_cb.set(self.REPORT_STYLES.get(s.report_style, self.REPORT_STYLES["both"]))
        add("Report output", self.style_cb)
        self.course = tk.StringVar(value=s.course)
        add("Course", ttk.Entry(f, textvariable=self.course, width=46))
        self.instructor = tk.StringVar(value=s.instructor)
        add("Instructor", ttk.Entry(f, textvariable=self.instructor, width=46))
        self.student = tk.StringVar(value=s.student)
        add("Student name(s)", ttk.Entry(f, textvariable=self.student, width=46))
        self.refs = tk.Text(f, height=5, width=46, wrap="word", font=("Segoe UI", 9))
        self.refs.insert("1.0", "\n".join(s.references or []))
        add("Standard references\n(one per line)", self.refs)
        self.template = tk.StringVar(value=s.report_template)
        add("Style template (.docx)", self._file_row(f, self.template))
        self.style_example = tk.StringVar(value=s.style_example)
        add("Writing-style example (.docx)", self._file_row(f, self.style_example))
        ttk.Label(f, foreground="#64748b", wraplength=420, justify="left",
                  text="Optional. A style template's fonts, headings and margins are reused; all of its text, "
                       "headers, footers, pictures and document properties are removed. A writing-style example is "
                       "any report whose voice and depth the AI imitates (names and organisations in it are never "
                       "copied).").grid(row=row, column=0, columnspan=2, sticky="w", pady=(8, 0))

    def _file_row(self, parent, var, folder=False):
        fr = ttk.Frame(parent)
        ttk.Entry(fr, textvariable=var, width=38).pack(side="left", fill="x", expand=True)

        def browse():
            p = filedialog.askdirectory() if folder else filedialog.askopenfilename()
            if p:
                var.set(p)

        ttk.Button(fr, text="…", width=3, command=browse).pack(side="left", padx=(4, 0))
        return fr

    def _provider_changed(self, label, labels):
        pid = next(k for k, v in labels.items() if v == label)
        self.provider.set(pid)
        default = cfg.PROVIDERS[pid][1]
        self.model_hint.configure(text=f"default: {default}" if default else "required for this provider")
        self.key.set(cfg.get_api_key(pid))

    def _save(self):
        s = self.s
        s.provider = self.provider.get()
        s.model = self.model.get().strip()
        s.base_url = self.base_url.get().strip()
        s.effort = self.effort.get()
        s.ngspice_path = self.ngspice.get().strip()
        s.multisim_exe = self.msexe.get().strip()
        s.multisim_worker = self.worker.get().strip()
        s.use_multisim_com = self.use_com.get()
        s.use_multisim_gui = self.use_gui.get()
        s.gui_settle_seconds = float(self.settle.get())
        s.max_design_iterations = int(self.iters.get())
        s.output_root = self.out.get().strip()
        s.report_style = next(k for k, v in self.REPORT_STYLES.items() if v == self.style_cb.get())
        s.course, s.instructor, s.student = self.course.get().strip(), self.instructor.get().strip(), self.student.get().strip()
        s.references = [ln.strip() for ln in self.refs.get("1.0", "end").splitlines() if ln.strip()]
        s.report_template = self.template.get().strip()
        s.style_example = self.style_example.get().strip()
        s.save()
        if self.key.get().strip() != cfg.get_api_key(s.provider):
            if not cfg.set_api_key(s.provider, self.key.get().strip()):
                messagebox.showwarning("API key", "No secure credential store available; set the key as an environment variable instead.", parent=self)
        self.destroy()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"Multisim Copilot {__version__}")
        self.geometry("1320x840")
        self.minsize(640, 460)
        self.settings = cfg.Settings.load()
        self.files: list[Path] = []
        self.msgs: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.pipeline = None
        self.last_report: Path | None = None
        self._build()
        self.after(100, self._drain)
        self.after(300, self._startup_checks)

    # ------------------------------------------------------------ layout
    def _build(self):
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        top = ttk.Frame(self, padding=(12, 10))
        top.pack(fill="x")
        ttk.Label(top, text="Multisim Copilot", font=("Segoe UI", 16, "bold")).pack(side="left")
        ttk.Button(top, text="Settings", command=self._settings).pack(side="right")
        ttk.Button(top, text="Open output folder", command=lambda: open_path(self.settings.resolved_output_root())).pack(side="right", padx=6)

        self.main_tabs = ttk.Notebook(self)
        self.main_tabs.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        sim_tab = ttk.Frame(self.main_tabs)
        self.main_tabs.add(sim_tab, text="Simulate & report")
        calc_tab = ttk.Frame(self.main_tabs)
        self.main_tabs.add(calc_tab, text="EE Calculator")
        self._calc_tab = calc_tab
        self.calculator = None
        draw_tab = ttk.Frame(self.main_tabs)
        self.main_tabs.add(draw_tab, text="Drawing analysis & schematic")
        self._draw_tab = draw_tab
        self.drawing = None
        self.main_tabs.bind("<<NotebookTabChanged>>", self._on_main_tab)

        panes = ttk.PanedWindow(sim_tab, orient="horizontal")
        panes.pack(fill="both", expand=True, pady=(6, 0))

        left_sf = ScrollFrame(panes, padding=4)
        panes.add(left_sf, weight=1)
        left = left_sf.body
        ttk.Label(left, text="1. Design inputs", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Label(left, text="Briefs (.docx/.txt), schematic images, .cir netlists, .ms14 circuits", foreground="#64748b").pack(anchor="w")
        lf = ttk.Frame(left)
        lf.pack(fill="both", expand=False, pady=4)
        lbf, self.file_list = with_scrollbars(lf, tk.Listbox, height=7, selectmode="extended")
        lbf.pack(side="left", fill="both", expand=True)
        bf = ttk.Frame(lf)
        bf.pack(side="left", fill="y", padx=(6, 0))
        ttk.Button(bf, text="Add files…", command=self._add_files).pack(fill="x")
        ttk.Button(bf, text="Remove", command=self._remove_files).pack(fill="x", pady=4)
        ttk.Button(bf, text="Clear", command=self._clear_files).pack(fill="x")
        ttk.Label(left, text="Instructions / requirements", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(8, 0))
        itf, self.instructions = with_scrollbars(left, tk.Text, x=False, height=10, wrap="word", font=("Segoe UI", 10))
        itf.pack(fill="both", expand=True, pady=4)
        self.instructions.insert("1.0", PLACEHOLDER)
        self.instructions.configure(foreground="#94a3b8")
        self.instructions.bind("<FocusIn>", self._clear_placeholder)

        ttk.Label(left, text="2. Plan  (review or edit before running)", font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(6, 0))
        pf = ttk.Frame(left)
        pf.pack(fill="x", pady=4)
        self.btn_plan = ttk.Button(pf, text="Plan with AI", command=self._plan)
        self.btn_plan.pack(side="left")
        ttk.Button(pf, text="Load plan…", command=self._load_plan).pack(side="left", padx=4)
        ttk.Button(pf, text="Save plan…", command=self._save_plan).pack(side="left")
        ttk.Label(left, text="3. Run", font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(6, 0))
        rf = ttk.Frame(left)
        rf.pack(fill="x", pady=4)
        self.btn_run = ttk.Button(rf, text="▶ Run everything (plan → simulate → review → report)", command=lambda: self._run(use_plan=False))
        self.btn_run.pack(fill="x")
        self.btn_run_plan = ttk.Button(rf, text="Run the plan shown on the right", command=lambda: self._run(use_plan=True))
        self.btn_run_plan.pack(fill="x", pady=4)
        self.btn_cancel = ttk.Button(rf, text="Cancel", command=self._cancel, state="disabled")
        self.btn_cancel.pack(fill="x")
        self.btn_report = ttk.Button(rf, text="Open last report", command=self._open_report, state="disabled")
        self.btn_report.pack(fill="x", pady=4)

        right = ttk.Notebook(panes)
        panes.add(right, weight=2)
        plan_tab = ttk.Frame(right, padding=4)
        log_tab = ttk.Frame(right, padding=4)
        right.add(log_tab, text="Activity log")
        right.add(plan_tab, text="Simulation plan (JSON)")
        self.notebook = right
        ptf, self.plan_text = with_scrollbars(plan_tab, tk.Text, wrap="none", font=("Consolas", 10), undo=True)
        ptf.pack(fill="both", expand=True)
        ltf, self.log_text = with_scrollbars(log_tab, tk.Text, wrap="none", font=("Consolas", 9), state="disabled")
        ltf.pack(fill="both", expand=True)
        self.status = ttk.Label(self, text="Ready", padding=(12, 4), anchor="w")
        self.status.pack(fill="x", side="bottom")
        self.progress = ttk.Progressbar(self, mode="indeterminate")
        self.progress.pack(fill="x", side="bottom", padx=12)

    def _on_main_tab(self, _e=None):
        idx = self.main_tabs.index("current")
        if idx == 1 and self.calculator is None:
            from mscopilot.calc_gui import CalculatorPanel

            self.calculator = CalculatorPanel(self._calc_tab)  # built on first use: keeps start-up fast
            self.calculator.pack(fill="both", expand=True)
        if idx == 2 and self.drawing is None:
            from mscopilot.drawing_gui import DrawingWorkspace

            self.drawing = DrawingWorkspace(self._draw_tab, self.settings)
            self.drawing.pack(fill="both", expand=True)

    def show_drawing(self):
        self.main_tabs.select(2)
        self._on_main_tab()

    def show_calculator(self):
        self.main_tabs.select(1)
        self._on_main_tab()

    # ------------------------------------------------------------ helpers
    def log(self, msg: str):
        self.msgs.put(("log", msg))

    def _drain(self):
        try:
            while True:
                kind, payload = self.msgs.get_nowait()
                if kind == "log":
                    self.log_text.configure(state="normal")
                    self.log_text.insert("end", payload + "\n")
                    self.log_text.see("end")
                    self.log_text.configure(state="disabled")
                    self.status.configure(text=payload.splitlines()[0][:160] if payload else "")
                elif kind == "plan":
                    self.plan_text.delete("1.0", "end")
                    self.plan_text.insert("1.0", payload)
                    self.notebook.select(1)
                elif kind == "done":
                    self._busy(False)
                    if payload:
                        self.last_report = payload
                        self.btn_report.configure(state="normal")
                        open_path(payload)
                elif kind == "error":
                    self._busy(False)
                    messagebox.showerror("Multisim Copilot", payload)
        except queue.Empty:
            pass
        self.after(100, self._drain)

    def _busy(self, on: bool):
        state = "disabled" if on else "normal"
        for b in (self.btn_plan, self.btn_run, self.btn_run_plan):
            b.configure(state=state)
        self.btn_cancel.configure(state="normal" if on else "disabled")
        if on:
            self.progress.start(12)
        else:
            self.progress.stop()

    def _startup_checks(self):
        s = self.settings
        self.log(f"ngspice: {cfg.find_ngspice(s) or 'NOT FOUND - set it in Settings'}")
        self.log(f"Multisim: {cfg.find_multisim_exe(s) or 'not found (SPICE-only reports; set multisim.exe in Settings)'}")
        if not cfg.get_api_key(s.provider):
            self.log(f"No API key for {cfg.PROVIDERS[s.provider][0]}. Open Settings to add one.")

    def _settings(self):
        SettingsDialog(self, self.settings)

    def _add_files(self):
        for p in filedialog.askopenfilenames(filetypes=FILETYPES):
            path = Path(p)
            if path not in self.files:
                self.files.append(path)
                self.file_list.insert("end", path.name)

    def _remove_files(self):
        for i in reversed(self.file_list.curselection()):
            self.file_list.delete(i)
            del self.files[i]

    def _clear_files(self):
        self.file_list.delete(0, "end")
        self.files.clear()

    def _clear_placeholder(self, _event=None):
        if self.instructions.get("1.0", "end").strip() == PLACEHOLDER:
            self.instructions.delete("1.0", "end")
            self.instructions.configure(foreground="black")

    def _package(self):
        from mscopilot.inputs import load_package

        text = self.instructions.get("1.0", "end").strip()
        return load_package(self.files, "" if text == PLACEHOLDER else text)

    def _current_plan(self):
        from mscopilot.plan import Plan

        text = self.plan_text.get("1.0", "end").strip()
        if not text:
            return None
        plan = Plan.from_json(text)
        problems = plan.validate()
        if problems:
            raise ValueError("The plan has problems:\n- " + "\n- ".join(problems))
        return plan

    def _load_plan(self):
        p = filedialog.askopenfilename(filetypes=[("Plan JSON", "*.json")])
        if p:
            self.msgs.put(("plan", Path(p).read_text(encoding="utf-8")))

    def _save_plan(self):
        p = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("Plan JSON", "*.json")])
        if p:
            Path(p).write_text(self.plan_text.get("1.0", "end").strip(), encoding="utf-8")

    def _open_report(self):
        if self.last_report:
            open_path(self.last_report)

    def _cancel(self):
        if self.pipeline:
            self.pipeline.cancelled = True
            self.log("Cancelling after the current step ...")

    # ------------------------------------------------------------ actions
    def _thread(self, fn):
        def wrapper():
            try:
                fn()
            except Exception as e:
                from mscopilot.pipeline import Cancelled

                if isinstance(e, Cancelled):
                    self.log("Cancelled.")
                    self.msgs.put(("done", None))
                    return
                self.log(traceback.format_exc())
                self.msgs.put(("error", str(e)))

        self._busy(True)
        self.worker = threading.Thread(target=wrapper, daemon=True)
        self.worker.start()

    def _plan(self):
        from mscopilot.pipeline import Pipeline

        pkg = self._package()
        if pkg.is_empty():
            messagebox.showinfo("Multisim Copilot", "Add a brief, schematic or instructions first.")
            return
        self.pipeline = Pipeline(self.settings, self.log)

        def job():
            plan = self.pipeline.plan(pkg)
            self.msgs.put(("plan", plan.to_json()))
            self.msgs.put(("done", None))

        self._thread(job)

    def _run(self, use_plan: bool):
        from mscopilot.pipeline import Pipeline

        try:
            pkg = self._package()
            plan = self._current_plan() if use_plan else None
        except Exception as e:
            messagebox.showerror("Multisim Copilot", str(e))
            return
        if use_plan and plan is None:
            messagebox.showinfo("Multisim Copilot", "There is no plan yet. Click 'Plan with AI' or load one.")
            return
        if not use_plan and pkg.is_empty():
            messagebox.showinfo("Multisim Copilot", "Add a brief, schematic or instructions first.")
            return
        use_ai = bool(cfg.get_api_key(self.settings.provider)) or self.settings.provider == "openai_compatible"
        if not use_plan and not use_ai:
            messagebox.showerror("Multisim Copilot", "Add an API key in Settings, or load a plan and use 'Run the plan'.")
            return
        self.pipeline = Pipeline(self.settings, self.log)

        def job():
            out = self.pipeline.run(pkg, plan=plan, use_ai=use_ai, input_paths=list(self.files))
            if out.records:
                self.msgs.put(("plan", out.records[-1].plan.to_json()))
            self.msgs.put(("done", out.lab_report or out.report_html))

        self._thread(job)


def launch(tab: str = "") -> int:
    app = App()
    if tab == "calc":
        app.after(50, app.show_calculator)
    elif tab == "drawing":
        app.after(50, app.show_drawing)
    app.mainloop()
    return 0
