# PyInstaller spec: builds EESimLab.exe (desktop app) and eesimlab-cli.exe (console)
# into one folder that shares the Python runtime.  Run from the repository root:
#   pyinstaller packaging/EESimLab.spec
import os

root = os.path.abspath(os.path.join(SPECPATH, ".."))
common = dict(
    pathex=[root],
    hiddenimports=["eesimlab.gui_app", "eesimlab.calc_gui", "eesimlab.drawing_gui", "eesimlab.multisim.gui",
                   "eesimlab.multisim.com_client", "eesimlab.eecalc", "eesimlab.drawing.recognize",
                   "eesimlab.drawing.reconstruct", "matplotlib.backends.backend_tkagg", "pypdfium2",
                   "keyring.backends.Windows", "markdown.extensions.tables", "markdown.extensions.fenced_code"],
    excludes=["pytest", "IPython", "PyQt5", "PySide6"],
    datas=[(os.path.join(root, "examples"), "examples")],
)

gui = Analysis([os.path.join(SPECPATH, "gui_entry.py")], **common)
gui_exe = EXE(PYZ(gui.pure), gui.scripts, [], exclude_binaries=True, name="EESimLab",
              console=False, upx=False)

cli = Analysis([os.path.join(SPECPATH, "cli_entry.py")], **common)
cli_exe = EXE(PYZ(cli.pure), cli.scripts, [], exclude_binaries=True, name="eesimlab-cli",
              console=True, upx=False)

coll = COLLECT(gui_exe, gui.binaries, gui.datas, cli_exe, cli.binaries, cli.datas,
               name="EESimLab", upx=False)
