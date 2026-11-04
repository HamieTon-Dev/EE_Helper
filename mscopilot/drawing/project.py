"""Workspace: original drawing + model versions (original and proposed revisions) +
probes + simulation settings + notes, saved as a single .eelab file (zip)."""
from __future__ import annotations

import io
import json
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from mscopilot.drawing.analysis import SimSettings
from mscopilot.drawing.instruments import Probe
from mscopilot.drawing.model import DrawingModel

FORMAT = "eelab-drawing/1"


@dataclass
class Version:
    name: str
    model: DrawingModel
    note: str = ""


@dataclass
class Workspace:
    versions: list[Version] = field(default_factory=lambda: [Version("Original", DrawingModel())])
    current: int = 0
    probes: list[Probe] = field(default_factory=list)
    settings: SimSettings = field(default_factory=SimSettings)
    image_bytes: bytes | None = None  # original upload, untouched
    image_suffix: str = ""
    pdf_page: int = 0
    view: dict = field(default_factory=lambda: {"rotation": 0.0, "enhance": {}})
    assumptions: str = ""  # user notes / assumptions
    findings_snapshot: list = field(default_factory=list)  # last findings, for reopening/reporting
    blank_sheet: bool = False  # schematic editor without an uploaded drawing

    @property
    def model(self) -> DrawingModel:
        return self.versions[self.current].model

    def new_revision(self, name: str | None = None) -> Version:
        v = Version(name or f"Revision {len(self.versions)}", self.model.clone(),
                    note=f"copied from {self.versions[self.current].name}")
        self.versions.append(v)
        self.current = len(self.versions) - 1
        return v

    def next_probe_id(self) -> str:
        n = 1
        ids = {p.id for p in self.probes}
        while f"P{n}" in ids:
            n += 1
        return f"P{n}"

    # ------------------------------------------------------------ persistence
    def save(self, path: str | Path) -> Path:
        path = Path(path)
        doc = {
            "format": FORMAT,
            "versions": [{"name": v.name, "note": v.note, "model": v.model.to_dict()} for v in self.versions],
            "current": self.current,
            "probes": [p.to_dict() for p in self.probes],
            "settings": asdict(self.settings),
            "image_suffix": self.image_suffix,
            "pdf_page": self.pdf_page,
            "view": self.view,
            "assumptions": self.assumptions,
            "findings": self.findings_snapshot,
            "blank_sheet": self.blank_sheet,
        }
        tmp = path.with_suffix(path.suffix + ".tmp")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("project.json", json.dumps(doc, indent=1, default=str))
            if self.image_bytes:
                z.writestr("drawing" + (self.image_suffix or ".png"), self.image_bytes)
        tmp.replace(path)
        return path

    @classmethod
    def load(cls, path: str | Path) -> "Workspace":
        with zipfile.ZipFile(path) as z:
            doc = json.loads(z.read("project.json"))
            if doc.get("format") != FORMAT:
                raise ValueError("Not a drawing-analysis project file.")
            img = None
            for n in z.namelist():
                if n.startswith("drawing"):
                    img = z.read(n)
        ws = cls(
            versions=[Version(v["name"], DrawingModel.from_dict(v["model"]), v.get("note", "")) for v in doc["versions"]],
            current=doc.get("current", 0),
            probes=[Probe(**p) for p in doc.get("probes", [])],
            settings=SimSettings(**doc.get("settings", {})),
            image_bytes=img,
            image_suffix=doc.get("image_suffix", ""),
            pdf_page=doc.get("pdf_page", 0),
            view=doc.get("view", {"rotation": 0.0, "enhance": {}}),
            assumptions=doc.get("assumptions", ""),
            findings_snapshot=doc.get("findings", []),
            blank_sheet=doc.get("blank_sheet", False),
        )
        return ws

    def image(self):
        """The original drawing as a PIL image (PDF pages are re-rendered)."""
        if not self.image_bytes:
            return None
        from mscopilot.drawing.recognize import render_pdf
        from PIL import Image

        if self.image_suffix == ".pdf":
            return render_pdf(self.image_bytes, self.pdf_page)
        return Image.open(io.BytesIO(self.image_bytes)).convert("RGB")
