"""Load the user's design package: instructions (.docx/.txt/.md), schematic images, netlists,
and existing Multisim circuit files."""
from __future__ import annotations

import mimetypes
from dataclasses import dataclass, field
from pathlib import Path

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
TEXT_EXT = {".txt", ".md", ".markdown"}
NETLIST_EXT = {".cir", ".net", ".sp", ".spice", ".ckt"}
MULTISIM_EXT = {".ms14", ".ms13", ".ms12", ".ms11", ".ms10", ".ms9", ".ms8"}
SUPPORTED_EXT = IMAGE_EXT | TEXT_EXT | NETLIST_EXT | MULTISIM_EXT | {".docx"}

# Anthropic and OpenAI accept these image types directly; others are converted to PNG.
_NATIVE_IMAGE_MIME = {"image/png", "image/jpeg", "image/gif", "image/webp"}
_MAX_IMAGE_EDGE = 2000


@dataclass
class ImageInput:
    name: str
    media_type: str
    data: bytes


@dataclass
class DesignPackage:
    instructions: str = ""
    documents: list[tuple[str, str]] = field(default_factory=list)  # (name, text)
    images: list[ImageInput] = field(default_factory=list)
    netlists: list[tuple[str, str]] = field(default_factory=list)  # (name, text)
    multisim_files: list[Path] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.instructions.strip() or self.documents or self.images or self.netlists or self.multisim_files)

    def describe(self) -> str:
        parts = []
        if self.instructions.strip():
            parts.append("typed instructions")
        parts += [f"document {n}" for n, _ in self.documents]
        parts += [f"image {i.name}" for i in self.images]
        parts += [f"netlist {n}" for n, _ in self.netlists]
        parts += [f"Multisim file {p.name}" for p in self.multisim_files]
        return ", ".join(parts) or "nothing"


def _normalise_image(name: str, data: bytes, media_type: str | None) -> ImageInput:
    """Downscale very large images and convert exotic formats to PNG for the AI APIs."""
    try:
        from io import BytesIO

        from PIL import Image

        img = Image.open(BytesIO(data))
        needs_convert = (media_type not in _NATIVE_IMAGE_MIME) or max(img.size) > _MAX_IMAGE_EDGE
        if needs_convert:
            img.thumbnail((_MAX_IMAGE_EDGE, _MAX_IMAGE_EDGE))
            if img.mode not in ("RGB", "RGBA", "L"):
                img = img.convert("RGBA")
            buf = BytesIO()
            img.save(buf, format="PNG")
            return ImageInput(name=name, media_type="image/png", data=buf.getvalue())
    except Exception:
        pass
    return ImageInput(name=name, media_type=media_type or "image/png", data=data)


def read_docx(path: Path) -> tuple[str, list[ImageInput]]:
    """Text (paragraphs and tables, in document order) plus embedded images.

    Each image is named ``<file stem>:fig<N>`` in the order it appears, and a marker
    ``[image <name>]`` is placed in the text where it sits, so the AI can tell which picture
    is "Figure 2". Auto-numbered list paragraphs get their number (1., 2., a., ...) back.
    """
    import docx  # python-docx
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = docx.Document(str(path))
    lines: list[str] = []
    images: list[ImageInput] = []
    seen_rids: dict[str, str] = {}
    counters: dict[tuple[str, int], int] = {}

    def images_in(el) -> list[str]:
        names = []
        for blip in el.iter(qn("a:blip")):
            rid = blip.get(qn("r:embed"))
            if not rid or rid not in doc.part.rels:
                continue
            if rid not in seen_rids:
                part = doc.part.rels[rid].target_part
                name = f"{path.stem}:fig{len(images) + 1}"
                try:
                    images.append(_normalise_image(name, part.blob, part.content_type))
                    seen_rids[rid] = name
                except Exception:
                    continue
            names.append(seen_rids[rid])
        return names

    def numbering(p) -> str:
        num_pr = p._p.pPr.numPr if p._p.pPr is not None else None
        if num_pr is None or num_pr.numId is None:
            return ""
        num_id = str(num_pr.numId.val)
        lvl = int(num_pr.ilvl.val) if num_pr.ilvl is not None else 0
        key = (num_id, lvl)
        counters[key] = counters.get(key, 0) + 1
        for (nid, l2) in list(counters):  # deeper levels restart under a new parent item
            if nid == num_id and l2 > lvl:
                counters.pop((nid, l2))
        n = counters[key]
        return f"{n}. " if lvl == 0 else f"{'   ' * lvl}{chr(96 + n) if n <= 26 else n}. "

    for child in doc.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            p = Paragraph(child, doc)
            text = p.text.strip()
            math = " ".join("".join(t.text or "" for t in m.iter(qn("m:t"))) for m in child.iter(qn("m:oMath"))).strip()
            if math:
                text = f"{text} [equation: {math}]".strip()
            imgs = images_in(child)
            if text:
                style = (p.style.name or "") if p.style is not None else ""
                prefix = "#" * int(style[-1]) + " " if style.startswith("Heading") and style[-1:].isdigit() else ""
                lines.append(prefix + numbering(p) + text)
            lines += [f"[image {n}]" for n in imgs]
        elif tag == "tbl":
            t = Table(child, doc)
            for row in t.rows:
                lines.append(" | ".join(c.text.strip() for c in row.cells))
            lines += [f"[image {n}]" for n in images_in(child)]
            lines.append("")
        else:  # content controls, etc.
            text = "".join(x.text or "" for x in child.iter(qn("w:t"))).strip()
            if text:
                lines.append(text)
            lines += [f"[image {n}]" for n in images_in(child)]
    return "\n".join(lines), images


def load_package(paths: list[str | Path], instructions: str = "") -> DesignPackage:
    pkg = DesignPackage(instructions=instructions or "")
    for raw in paths:
        p = Path(raw)
        ext = p.suffix.lower()
        if not p.is_file():
            raise FileNotFoundError(p)
        if ext == ".docx":
            text, imgs = read_docx(p)
            pkg.documents.append((p.name, text))
            pkg.images.extend(imgs)
        elif ext in TEXT_EXT:
            pkg.documents.append((p.name, p.read_text(encoding="utf-8", errors="replace")))
        elif ext in IMAGE_EXT:
            mt = mimetypes.guess_type(p.name)[0]
            pkg.images.append(_normalise_image(p.name, p.read_bytes(), mt))
        elif ext in NETLIST_EXT:
            pkg.netlists.append((p.name, p.read_text(encoding="utf-8", errors="replace")))
        elif ext in MULTISIM_EXT:
            pkg.multisim_files.append(p.resolve())
        else:
            raise ValueError(f"Unsupported file type: {p.name}")
    return pkg
