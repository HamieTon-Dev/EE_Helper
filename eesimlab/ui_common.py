"""Shared Tk helpers: scrollable panes and scrollbar wiring used by every view."""
from __future__ import annotations

import sys
import tkinter as tk
from tkinter import ttk


def with_scrollbars(parent, widget_cls, *, x=True, y=True, **kw):
    """Create ``widget_cls(frame, **kw)`` inside a frame with scrollbars; returns (frame, widget).

    Works for Text, Listbox, Treeview and Canvas."""
    frame = ttk.Frame(parent)
    w = widget_cls(frame, **kw)
    w.grid(row=0, column=0, sticky="nsew")
    frame.rowconfigure(0, weight=1)
    frame.columnconfigure(0, weight=1)
    if y:
        sy = ttk.Scrollbar(frame, orient="vertical", command=w.yview)
        sy.grid(row=0, column=1, sticky="ns")
        w.configure(yscrollcommand=sy.set)
    if x:
        sx = ttk.Scrollbar(frame, orient="horizontal", command=w.xview)
        sx.grid(row=1, column=0, sticky="ew")
        w.configure(xscrollcommand=sx.set)
    return frame, w


class ScrollFrame(ttk.Frame):
    """A frame whose content scrolls both ways. Put children in ``.body``.

    The body stretches to the visible width/height but never below its own requested
    size, so a small window shows scrollbars instead of clipping or crushing widgets."""

    def __init__(self, parent, *, x=True, y=True, padding=0, **kw):
        super().__init__(parent, **kw)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        bg = ttk.Style(self).lookup("TFrame", "background")
        if bg:
            self.canvas.configure(background=bg)
        self.body = ttk.Frame(self.canvas, padding=padding)
        self._win = self.canvas.create_window(0, 0, window=self.body, anchor="nw")
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self._x, self._y = x, y
        if y:
            self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
            self.vsb.grid(row=0, column=1, sticky="ns")
            self.canvas.configure(yscrollcommand=self.vsb.set)
        if x:
            self.hsb = ttk.Scrollbar(self, orient="horizontal", command=self.canvas.xview)
            self.hsb.grid(row=1, column=0, sticky="ew")
            self.canvas.configure(xscrollcommand=self.hsb.set)
        self.body.bind("<Configure>", self._refit)
        self.canvas.bind("<Configure>", self._refit)
        for w in (self.canvas, self.body):
            w.bind("<Enter>", lambda e: self._wheel(True), add="+")
            w.bind("<Leave>", lambda e: self._wheel(False), add="+")

    def _refit(self, _e=None):
        req_w, req_h = self.body.winfo_reqwidth(), self.body.winfo_reqheight()
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        self.canvas.itemconfigure(self._win, width=max(req_w, cw) if self._x else cw,
                                  height=max(req_h, ch) if self._y else ch)
        self.canvas.configure(scrollregion=(0, 0, max(req_w, cw), max(req_h, ch)))

    def _wheel(self, on):
        c = self.canvas
        if on:
            c.bind_all("<MouseWheel>", self._on_wheel)
            c.bind_all("<Shift-MouseWheel>", lambda e: c.xview_scroll(-1 if e.delta > 0 else 1, "units"))
            c.bind_all("<Button-4>", lambda e: c.yview_scroll(-1, "units"))
            c.bind_all("<Button-5>", lambda e: c.yview_scroll(1, "units"))
        else:
            for ev in ("<MouseWheel>", "<Shift-MouseWheel>", "<Button-4>", "<Button-5>"):
                c.unbind_all(ev)

    def _on_wheel(self, e):
        # let widgets with their own scrolling (Text, Treeview, Listbox) keep the wheel
        w = e.widget
        if isinstance(w, (tk.Text, tk.Listbox, ttk.Treeview)) or (isinstance(w, tk.Canvas) and w is not self.canvas):
            return
        step = int(-e.delta / 120) if sys.platform == "win32" or abs(e.delta) >= 120 else (-1 if e.delta > 0 else 1)
        self.canvas.yview_scroll(step or (-1 if e.delta > 0 else 1), "units")

    def scroll_top(self):
        self.canvas.yview_moveto(0)
        self.canvas.xview_moveto(0)
