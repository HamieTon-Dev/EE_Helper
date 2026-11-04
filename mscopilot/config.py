"""User settings: AI provider, API keys, tool paths.

Settings live in ``%APPDATA%\\MultisimCopilot\\config.json`` (or ``~/.config/MultisimCopilot``
elsewhere). API keys are stored in the OS credential store (Windows Credential Manager) via
``keyring`` when it is available, and never written into the JSON file. Environment variables
(``ANTHROPIC_API_KEY``, ``OPENAI_API_KEY``, ``MSCOPILOT_API_KEY``) always win, so CI and
scripted runs need no stored key.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from mscopilot import APP_NAME

KEYRING_SERVICE = APP_NAME

PROVIDERS = {
    # provider id: (label, default model, env var)
    "anthropic": ("Anthropic Claude", "claude-opus-5-5", "ANTHROPIC_API_KEY"),
    "openai": ("OpenAI ChatGPT", "gpt-5", "OPENAI_API_KEY"),
    "openai_compatible": ("Other (OpenAI-compatible endpoint)", "", "MSCOPILOT_API_KEY"),
}


def config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / APP_NAME


def app_dir() -> Path:
    """Folder holding the running program (the .exe folder when frozen by PyInstaller)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


@dataclass
class Settings:
    provider: str = "anthropic"
    model: str = ""  # empty -> provider default
    base_url: str = ""  # only for openai_compatible
    effort: str = "high"  # Anthropic effort level
    ngspice_path: str = ""  # empty -> auto-detect
    multisim_exe: str = ""  # empty -> auto-detect
    multisim_worker: str = ""  # empty -> auto-detect (bundled MultisimWorker.exe)
    use_multisim_com: bool = True
    use_multisim_gui: bool = True
    gui_settle_seconds: float = 6.0
    max_design_iterations: int = 2
    output_root: str = ""
    # report output: "lab" (course lab-report .docx), "engineering" (HTML + review), "both"
    report_style: str = "both"
    # lab-report profile (cover page + defaults); see labreport.Profile
    course: str = ""
    instructor: str = ""
    student: str = ""
    references: list = field(default_factory=list)
    report_template: str = ""  # optional .docx whose styles/page setup are reused (content stripped)
    style_example: str = ""  # optional .docx whose writing style the AI imitates
    extra: dict = field(default_factory=dict)

    def profile(self):
        from mscopilot.labreport import Profile

        return Profile(course=self.course, instructor=self.instructor, student=self.student,
                       references=list(self.references or []), template=self.report_template)

    # ---- persistence -------------------------------------------------
    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        path = path or config_dir() / "config.json"
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def save(self, path: Path | None = None) -> Path:
        path = path or config_dir() / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        return path

    # ---- derived -----------------------------------------------------
    @property
    def effective_model(self) -> str:
        return self.model or PROVIDERS.get(self.provider, ("", "", ""))[1]

    def resolved_output_root(self) -> Path:
        if self.output_root:
            return Path(self.output_root)
        return Path.home() / "Documents" / APP_NAME


# ---- API keys ----------------------------------------------------------
def _keyring():
    try:
        import keyring  # type: ignore

        return keyring
    except Exception:  # pragma: no cover - keyring missing or no backend
        return None


def get_api_key(provider: str) -> str:
    env_var = PROVIDERS.get(provider, ("", "", "MSCOPILOT_API_KEY"))[2]
    for var in (env_var, "MSCOPILOT_API_KEY"):
        if os.environ.get(var):
            return os.environ[var]
    kr = _keyring()
    if kr is not None:
        try:
            return kr.get_password(KEYRING_SERVICE, provider) or ""
        except Exception:
            return ""
    return ""


def set_api_key(provider: str, key: str) -> bool:
    """Store the key in the OS credential store. Returns False if no store is available."""
    kr = _keyring()
    if kr is None:
        return False
    try:
        if key:
            kr.set_password(KEYRING_SERVICE, provider, key)
        else:
            try:
                kr.delete_password(KEYRING_SERVICE, provider)
            except Exception:
                pass
        return True
    except Exception:
        return False


# ---- tool discovery ----------------------------------------------------
def find_ngspice(settings: Settings) -> str | None:
    candidates: list[Path] = []
    if settings.ngspice_path:
        candidates.append(Path(settings.ngspice_path))
    here = app_dir()
    for sub in ("ngspice/bin", "Spice64/bin", "ngspice/Spice64/bin"):
        candidates += [here / sub / "ngspice_con.exe", here / sub / "ngspice.exe", here / sub / "ngspice"]
    if sys.platform == "win32":
        for root in (r"C:\Spice64\bin", r"C:\Program Files\Spice64\bin", r"C:\Program Files\ngspice\bin"):
            candidates += [Path(root) / "ngspice_con.exe"]
    for c in candidates:
        if c.is_file():
            return str(c)
    for name in ("ngspice_con", "ngspice"):
        found = shutil.which(name)
        if found:
            return found
    return None


def find_multisim_exe(settings: Settings) -> str | None:
    if settings.multisim_exe and Path(settings.multisim_exe).is_file():
        return settings.multisim_exe
    if sys.platform != "win32":
        return None
    roots = [os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), os.environ.get("ProgramFiles", r"C:\Program Files")]
    hits: list[Path] = []
    for root in roots:
        ni = Path(root) / "National Instruments"
        if ni.is_dir():
            hits += list(ni.glob("Circuit Design Suite*/multisim.exe"))
    hits.sort(reverse=True)  # newest version folder name first
    return str(hits[0]) if hits else None


def find_worker(settings: Settings) -> list[str] | None:
    """Command line that starts the 32-bit Multisim COM worker."""
    if settings.multisim_worker:
        p = Path(settings.multisim_worker)
        if p.suffix.lower() == ".py":
            return [sys.executable, str(p)]
        return [str(p)]
    here = app_dir()
    for cand in (here / "MultisimWorker.exe", here / "worker" / "MultisimWorker.exe"):
        if cand.is_file():
            return [str(cand)]
    if sys.platform == "win32" and not getattr(sys, "frozen", False):
        script = Path(__file__).resolve().parent / "multisim" / "worker.py"
        py_launcher = shutil.which("py")
        if py_launcher:
            return [py_launcher, "-3-32", str(script)]
        return [sys.executable, str(script)]
    return None
