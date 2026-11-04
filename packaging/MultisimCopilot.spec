# PyInstaller spec: builds MultisimCopilot.exe (desktop app) and mscopilot-cli.exe (console)
# into one folder that shares the Python runtime.  Run from the repository root:
#   pyinstaller packaging/MultisimCopilot.spec
import os

root = os.path.abspath(os.path.join(SPECPATH, ".."))
common = dict(
    pathex=[root],
    hiddenimports=["mscopilot.gui_app", "mscopilot.multisim.gui", "mscopilot.multisim.com_client",
                   "keyring.backends.Windows", "markdown.extensions.tables", "markdown.extensions.fenced_code"],
    excludes=["pytest", "IPython", "PyQt5", "PySide6"],
    datas=[(os.path.join(root, "examples"), "examples")],
)

gui = Analysis([os.path.join(SPECPATH, "gui_entry.py")], **common)
gui_exe = EXE(PYZ(gui.pure), gui.scripts, [], exclude_binaries=True, name="MultisimCopilot",
              console=False, upx=False)

cli = Analysis([os.path.join(SPECPATH, "cli_entry.py")], **common)
cli_exe = EXE(PYZ(cli.pure), cli.scripts, [], exclude_binaries=True, name="mscopilot-cli",
              console=True, upx=False)

coll = COLLECT(gui_exe, gui.binaries, gui.datas, cli_exe, cli.binaries, cli.datas,
               name="MultisimCopilot", upx=False)
