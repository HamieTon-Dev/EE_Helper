"""Drive the Multisim desktop UI to run the interactive simulation and capture screenshots
of the schematic, the instrument faces (oscilloscope, Bode plotter, multimeter) and the
Grapher. Windows only; uses pywinauto (UI Automation) and Pillow's screen grabber.

This is the "what you would see on screen" path. Multisim's menus differ a little
between versions and languages, so every step is best-effort: a failed step is logged
and the run continues with whatever was captured.
"""
from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path

INSTRUMENT_HINTS = ("oscilloscope", "bode", "multimeter", "grapher", "xsc", "xbp", "xmm", "function generator", "wattmeter", "spectrum")


class MultisimGUIError(RuntimeError):
    pass


class MultisimGUI:
    def __init__(self, exe: str, log=print, settle_seconds: float = 6.0):
        if sys.platform != "win32":
            raise MultisimGUIError("Multisim UI automation only runs on Windows.")
        try:
            import pywinauto  # noqa: F401
        except ImportError as e:  # pragma: no cover
            raise MultisimGUIError("pywinauto is not installed.") from e
        self.exe = exe
        self.log = log
        self.settle = settle_seconds
        self.app = None
        self.main = None

    # ------------------------------------------------------------ session
    def open(self, circuit: Path, timeout: float = 180):
        from pywinauto import Application

        circuit = Path(circuit).resolve()
        self.log(f"Opening {circuit.name} in Multisim ...")
        subprocess.Popen([self.exe, str(circuit)])
        deadline = time.time() + timeout
        last_err = None
        while time.time() < deadline:
            try:
                self.app = Application(backend="uia").connect(path=self.exe, timeout=5)
                win = self.app.top_window()
                title = win.window_text()
                if "multisim" in title.lower() and circuit.stem.lower() in title.lower():
                    self.main = win
                    break
            except Exception as e:  # still loading / splash screen
                last_err = e
            time.sleep(2)
        if self.main is None:
            raise MultisimGUIError(f"Multisim window for {circuit.name} did not appear ({last_err}).")
        self._dismiss_dialogs()
        self.main.set_focus()
        time.sleep(1.5)

    def _dismiss_dialogs(self):
        """Close modal pop-ups (missing parts warnings, tips) that block automation."""
        if self.app is None:
            return
        for w in self.app.windows():
            try:
                if w.is_dialog() and w != self.main:
                    for label in ("OK", "Close", "Yes", "Continue"):
                        btn = w.child_window(title=label, control_type="Button")
                        if btn.exists(timeout=0.2):
                            btn.click_input()
                            break
            except Exception:
                continue

    def _menu(self, path: str) -> bool:
        try:
            self.main.menu_select(path)
            return True
        except Exception as e:
            self.log(f"Menu '{path}' not available: {e}")
            return False

    # ------------------------------------------------------------ actions
    def run_interactive(self) -> bool:
        """Simulate > Run (F5). Instruments update live while it runs."""
        from pywinauto.keyboard import send_keys

        self.main.set_focus()
        if not self._menu("Simulate->Run"):
            send_keys("{F5}")
        self.log(f"Simulation running; waiting {self.settle:g}s for instruments to settle ...")
        time.sleep(self.settle)
        self._dismiss_dialogs()
        return True

    def pause(self):
        """Pause (not stop) so instrument displays keep their last trace for the screenshot."""
        from pywinauto.keyboard import send_keys

        if not self._menu("Simulate->Pause"):
            send_keys("{F6}")
        time.sleep(1.0)

    def stop(self):
        self._menu("Simulate->Stop")
        time.sleep(1.0)

    def show_grapher(self):
        if self._menu("View->Grapher"):
            time.sleep(1.5)

    def save_as(self, target: Path) -> bool:
        """File > Save As... (used to turn an imported SPICE netlist into a .ms14 file)."""
        from pywinauto.keyboard import send_keys

        target = Path(target).resolve()
        if not self._menu("File->Save as..."):
            if not self._menu("File->Save As..."):
                return False
        time.sleep(1.5)
        send_keys(str(target).replace("{", "{{}").replace("}", "{}}"), with_spaces=True)
        send_keys("{ENTER}")
        time.sleep(2)
        self._dismiss_dialogs()
        return target.exists()

    # ------------------------------------------------------------ capture
    def capture_all(self, out_dir: Path, prefix: str) -> list[Path]:
        """Screenshot the main window and every instrument/Grapher window."""
        out_dir.mkdir(parents=True, exist_ok=True)
        shots: list[Path] = []
        if self.main is not None:
            p = self._grab(self.main, out_dir / f"{prefix}_multisim_main.png")
            if p:
                shots.append(p)
        for w in self.app.windows() if self.app else []:
            try:
                if w == self.main or not w.is_visible():
                    continue
                title = w.window_text() or ""
                r = w.rectangle()
                if r.width() < 120 or r.height() < 80:
                    continue
                if not any(h in title.lower() for h in INSTRUMENT_HINTS):
                    continue
                slug = re.sub(r"[^A-Za-z0-9]+", "_", title).strip("_")[:40] or "window"
                p = self._grab(w, out_dir / f"{prefix}_{slug}.png")
                if p:
                    shots.append(p)
            except Exception:
                continue
        return shots

    def _grab(self, win, path: Path) -> Path | None:
        from PIL import ImageGrab

        try:
            win.set_focus()
            time.sleep(0.6)
            r = win.rectangle()
            img = ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom), all_screens=True)
            img.save(path)
            return Path(path)
        except Exception as e:
            self.log(f"Screenshot failed for '{win.window_text()}': {e}")
            return None

    def close(self, save: bool = False):
        from pywinauto.keyboard import send_keys

        try:
            self.stop()
            self.main.set_focus()
            self._menu("File->Close")
            time.sleep(1)
            for w in self.app.windows():
                if w.is_dialog():
                    btn = w.child_window(title="Yes" if save else "No", control_type="Button")
                    if btn.exists(timeout=0.5):
                        btn.click_input()
            send_keys("")
        except Exception:
            pass
