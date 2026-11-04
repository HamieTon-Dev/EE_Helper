"""Student-style lab report (.docx) in the classic course format:

  cover page (course, instructor, title, student, date)
  table of contents
  Abstract · Introduction · Procedures (narrative + the handout's parts and numbered steps
  with circuit figures) · Data Presentation & Analysis (numbered tables and plots) ·
  Calculations · Required Screenshots · Conclusion · References

The AI writes a LabReport JSON (schema below). Every number in it is an expression that is
evaluated against the simulation results (see labexpr), so tables, calculations and
in-text values always match the simulated data.

Styling comes from the built-in template, or optionally from any .docx the user picks as a
style template: only its styles and page setup are kept - body text, headers, footers,
pictures and document properties are all removed.
"""
from __future__ import annotations

import copy
import datetime as _dt
import re
from dataclasses import dataclass, field
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.ticker import EngFormatter  # noqa: E402

from eesimlab.labexpr import Context, eval_number, fill_text, render_cell  # noqa: E402

_STR_OR_NULL = {"type": ["string", "null"]}
_STRS = {"type": "array", "items": {"type": "string"}}

LAB_REPORT_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "abstract", "introduction", "procedures", "parts", "data", "calculations",
                 "screenshots", "conclusion", "references"],
    "properties": {
        "title": {"type": "string"},
        "abstract": {"type": "string"},
        "introduction": {"type": "string"},
        "procedures": {"type": "string"},
        "parts": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False, "required": ["heading", "steps"],
                "properties": {
                    "heading": {"type": "string"},
                    "steps": {
                        "type": "array",
                        "items": {
                            "type": "object", "additionalProperties": False,
                            "required": ["text", "sub_steps", "equation", "figure_source", "figure_caption"],
                            "properties": {
                                "text": {"type": "string"},
                                "sub_steps": _STRS,
                                "equation": _STR_OR_NULL,
                                "figure_source": _STR_OR_NULL,
                                "figure_caption": _STR_OR_NULL,
                            },
                        },
                    },
                },
            },
        },
        "data": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["type", "caption", "header_rows", "rows", "plot"],
                "properties": {
                    "type": {"type": "string", "enum": ["table", "plot"]},
                    "caption": {"type": "string"},
                    "header_rows": {"type": "array", "items": _STRS},
                    "rows": {"type": "array", "items": _STRS},
                    "plot": {
                        "type": ["object", "null"], "additionalProperties": False,
                        "required": ["style", "title", "x_label", "y_label", "log_x", "series", "vectors"],
                        "properties": {
                            "style": {"type": "string", "enum": ["xy", "phasor"]},
                            "title": {"type": "string"},
                            "x_label": {"type": "string"},
                            "y_label": {"type": "string"},
                            "log_x": {"type": "boolean"},
                            "series": {
                                "type": "array",
                                "items": {
                                    "type": "object", "additionalProperties": False, "required": ["label", "x", "y"],
                                    "properties": {"label": {"type": "string"}, "x": _STRS, "y": _STRS},
                                },
                            },
                            "vectors": {
                                "type": "array",
                                "items": {
                                    "type": "object", "additionalProperties": False,
                                    "required": ["label", "magnitude", "angle_deg", "start"],
                                    "properties": {"label": {"type": "string"}, "magnitude": {"type": "string"},
                                                   "angle_deg": {"type": "string"},
                                                   "start": {"type": ["string", "null"]}},
                                },
                            },
                        },
                    },
                },
            },
        },
        "calculations": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False, "required": ["label", "lines"],
                "properties": {
                    "label": {"type": "string"},
                    "lines": {
                        "type": "array",
                        "items": {
                            "type": "object", "additionalProperties": False,
                            "required": ["formula", "expr", "unit", "fmt"],
                            "properties": {"formula": {"type": "string"}, "expr": {"type": "string"},
                                           "unit": {"type": "string"}, "fmt": {"type": "string"}},
                        },
                    },
                },
            },
        },
        "screenshots": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False, "required": ["source", "caption"],
                "properties": {"source": {"type": "string"}, "caption": {"type": "string"}},
            },
        },
        "conclusion": {"type": "string"},
        "references": _STRS,
    },
}


@dataclass
class Profile:
    course: str = ""
    instructor: str = ""
    student: str = ""
    references: list[str] = field(default_factory=list)
    template: str = ""  # .docx whose styles/page setup are reused
    date: str = ""  # blank -> today

    def date_text(self) -> str:
        if self.date:
            return self.date
        d = _dt.date.today()
        return f"{d.month}/{d.day}/{d.year}"


# =============================================================== validation / evaluation
def collect_errors(report: dict, ctx: Context, images: dict[str, Path]) -> list[str]:
    """Dry-run every expression and image reference; returns problems for an AI repair pass."""
    errors: list[str] = []
    for key in ("abstract", "introduction", "procedures", "conclusion"):
        fill_text(ctx, report.get(key, ""), errors, key)
    for pi, part in enumerate(report.get("parts", [])):
        for si, st in enumerate(part.get("steps", [])):
            where = f"parts[{pi}].steps[{si}]"
            fill_text(ctx, st.get("text", ""), errors, where)
            for ss in st.get("sub_steps", []) or []:
                fill_text(ctx, ss, errors, where)
            if st.get("equation"):
                fill_text(ctx, st["equation"], errors, where)
            if st.get("figure_source") and st["figure_source"] not in images:
                errors.append(f"{where}: unknown figure_source '{st['figure_source']}'")
    for di, item in enumerate(report.get("data", [])):
        where = f"data[{di}]"
        if item.get("type") == "table":
            for r in item.get("rows", []):
                for c in r:
                    render_cell(ctx, c, errors, where)
        elif item.get("plot"):
            _plot_values(item["plot"], ctx, errors, where)
    for ci, calc in enumerate(report.get("calculations", [])):
        for li, line in enumerate(calc.get("lines", [])):
            eval_number(ctx, line.get("expr", ""), errors, f"calculations[{ci}].lines[{li}]")
    for si, sh in enumerate(report.get("screenshots", [])):
        if sh.get("source") not in images:
            errors.append(f"screenshots[{si}]: unknown source '{sh.get('source')}'")
    return errors


def _plot_values(plot: dict, ctx: Context, errors: list[str], where: str):
    series = []
    for s in plot.get("series", []) or []:
        xs = [eval_number(ctx, x, errors, where) for x in s.get("x", [])]
        ys = [eval_number(ctx, y, errors, where) for y in s.get("y", [])]
        if len(xs) != len(ys):
            errors.append(f"{where}: series '{s.get('label')}' has {len(xs)} x values but {len(ys)} y values")
        pts = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
        series.append((s.get("label", ""), pts))
    vectors = []
    for v in plot.get("vectors", []) or []:
        m = eval_number(ctx, v.get("magnitude", "0"), errors, where)
        a = eval_number(ctx, v.get("angle_deg", "0"), errors, where)
        vectors.append((v.get("label", ""), m or 0.0, a or 0.0, v.get("start")))
    return series, vectors


# =============================================================== figures
def draw_xy(plot: dict, series, path: Path) -> Path:
    """Spreadsheet-chart look (title, markers + smooth line, light grid), like Excel charts."""
    fig, ax = plt.subplots(figsize=(7.5, 4.3), dpi=150)
    colors = ["#4472C4", "#ED7D31", "#A5A5A5", "#FFC000", "#5B9BD5", "#70AD47"]
    for i, (label, pts) in enumerate(series):
        if not pts:
            continue
        pts = sorted(pts)
        x, y = np.array([p[0] for p in pts]), np.array([p[1] for p in pts])
        c = colors[i % len(colors)]
        if len(x) >= 4 and not plot.get("log_x"):
            try:
                from scipy.interpolate import make_interp_spline  # optional

                xs = np.linspace(x.min(), x.max(), 300)
                ax.plot(xs, make_interp_spline(x, y, k=2)(xs), color=c, lw=2)
                ax.plot(x, y, "o", color=c, ms=5, label=label)
                continue
            except Exception:
                pass
        ax.plot(x, y, "-o", color=c, lw=2, ms=5, label=label)
    if plot.get("log_x"):
        ax.set_xscale("log")
    ax.set_title(plot.get("title", ""), fontsize=13, color="#595959")
    ax.set_xlabel(plot.get("x_label", ""), color="#595959")
    ax.set_ylabel(plot.get("y_label", ""), color="#595959")
    ax.grid(True, which="both", color="#D9D9D9", lw=0.8)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    for sp in ("left", "bottom"):
        ax.spines[sp].set_color("#BFBFBF")
    ax.tick_params(colors="#595959")
    if len(series) > 1:
        ax.legend(frameon=False, loc="best")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return Path(path)


def draw_phasor(plot: dict, vectors, path: Path) -> Path:
    """Phasor diagram: arrows from the origin (or from the tip of a named vector), labelled
    with magnitude ∠ angle."""
    fig, ax = plt.subplots(figsize=(6.4, 5.2), dpi=150)
    colors = ["#2563eb", "#dc2626", "#059669", "#7c3aed", "#d97706", "#0891b2"]
    tips: dict[str, complex] = {}
    allpts = [0j]
    for i, (label, mag, ang, start) in enumerate(vectors):
        z = mag * np.exp(1j * np.radians(ang))
        z0 = tips.get(start or "", 0j)
        tips[label] = z0 + z
        allpts += [z0, z0 + z]
        c = colors[i % len(colors)]
        ax.annotate("", xy=(z0.real + z.real, z0.imag + z.imag), xytext=(z0.real, z0.imag),
                    arrowprops=dict(arrowstyle="-|>", color=c, lw=2.2, mutation_scale=16))
        tip = z0 + z / 2 if start else z0 + z
        ax.text(tip.real, tip.imag, f"  {label}\n  {_short(mag)}∠{ang:.2f}°", color=c, fontsize=9,
                ha="left", va="bottom" if z.imag >= 0 else "top")
    xs = [p.real for p in allpts]
    ys = [p.imag for p in allpts]
    span = max(max(xs) - min(xs), max(ys) - min(ys), 1e-12)
    pad = 0.18 * span
    # square window centred on the drawing so arrows keep their true angles
    cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
    half = span / 2 + pad
    ax.set_xlim(cx - half, cx + half)
    ax.set_ylim(cy - half, cy + half)
    ax.axhline(0, color="#6b7280", lw=1)
    ax.axvline(0, color="#6b7280", lw=1)
    ax.set_aspect("equal", adjustable="box")
    ax.xaxis.set_major_formatter(EngFormatter())
    ax.yaxis.set_major_formatter(EngFormatter())
    ax.grid(True, color="#e5e7eb")
    ax.set_title(plot.get("title", ""), fontsize=12)
    ax.set_xlabel(plot.get("x_label", "") or "Real")
    ax.set_ylabel(plot.get("y_label", "") or "Imaginary")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return Path(path)


def _short(v: float) -> str:
    return EngFormatter(places=3, sep="")(v)


# =============================================================== template handling
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _qn(tag: str) -> str:
    from docx.oxml.ns import qn

    return qn(tag)


def open_template(path: str | None):
    """Return a python-docx Document ready to be filled.

    With a user template only styles, numbering and page setup are kept: the body,
    header and footer content (text, logos, pictures), the glossary and the document
    properties (author, company, ...) are removed, and a page-number footer is added.
    Without one, the built-in template is used.
    """
    import docx

    if path and Path(path).is_file():
        doc = docx.Document(str(path))
        body = doc.element.body
        for child in list(body):
            if child.tag != _qn("w:sectPr"):
                body.remove(child)
        from docx.enum.text import WD_ALIGN_PARAGRAPH

        for sec in doc.sections:
            for hf in (sec.header, sec.first_page_header, sec.even_page_header,
                       sec.footer, sec.first_page_footer, sec.even_page_footer):
                if hf.is_linked_to_previous:
                    continue
                el = hf._element
                for child in list(el):
                    el.remove(child)
                el.append(_empty_paragraph())
                _drop_unused_media(hf.part)
            fp = sec.footer.paragraphs[0]
            fp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            _field(fp, "PAGE")
        _drop_unused_media(doc.part)
        _scrub_package(doc)
        _ensure_styles(doc)
        return doc
    doc = docx.Document()
    _builtin_styles(doc)
    return doc


def _empty_paragraph():
    from docx.oxml import OxmlElement

    return OxmlElement("w:p")


def _scrub_package(doc) -> None:
    """Remove anything that could identify where the template came from: document
    properties, the glossary (saved content-control text), custom XML and comments."""
    cp = doc.core_properties
    for attr in ("author", "last_modified_by", "title", "subject", "keywords", "comments", "category",
                 "content_status", "identifier", "language", "version"):
        try:
            setattr(cp, attr, "")
        except Exception:
            pass
    for rid, rel in list(doc.part.rels.items()):
        kind = rel.reltype.rsplit("/", 1)[-1]
        if kind in ("glossaryDocument", "customXml", "comments", "commentsExtended", "commentsIds", "people"):
            del doc.part.rels[rid]
    # footnotes/endnotes: keep only the separator entries (ids -1 and 0) Word requires
    for rid, rel in list(doc.part.rels.items()):
        kind = rel.reltype.rsplit("/", 1)[-1]
        if kind in ("footnotes", "endnotes") and not rel.is_external:
            try:
                from docx.opc.oxml import parse_xml

                root = parse_xml(rel.target_part.blob)
                tag = _qn("w:footnote" if kind == "footnotes" else "w:endnote")
                for note in list(root.iter(tag)):
                    if int(note.get(_qn("w:id"), "0")) > 0:
                        note.getparent().remove(note)
                from lxml import etree

                rel.target_part._blob = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
            except Exception:
                pass
    pkg_rels = doc.part.package.rels
    for rid, rel in list(pkg_rels.items()):
        if rel.reltype.endswith("/extended-properties") or rel.reltype.endswith("/custom-properties"):
            del pkg_rels[rid]


def _drop_unused_media(part) -> None:
    """Remove image/OLE relationships no longer referenced by the part's XML, so the old
    report's pictures (and logos) are not carried inside the new file."""
    xml = part._element.xml if hasattr(part, "_element") else ""
    for rid, rel in list(part.rels.items()):
        kind = rel.reltype.rsplit("/", 1)[-1]
        unused = f'"{rid}"' not in xml
        if unused and (kind in ("image", "oleObject", "package", "chart", "hyperlink") or rel.is_external):
            del part.rels[rid]


def _builtin_styles(doc) -> None:
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Inches, Pt, RGBColor

    sec = doc.sections[0]
    sec.page_width, sec.page_height = Inches(8.5), Inches(11)
    for side in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
        setattr(sec, side, Inches(1))
    normal = doc.styles["Normal"]
    normal.font.name = "Times New Roman"
    normal.font.size = Pt(12)
    _east_asia(normal, "Times New Roman")
    h1 = doc.styles["Heading 1"]
    h1.font.name, h1.font.size, h1.font.bold, h1.font.underline = "Palatino Linotype", Pt(14), True, True
    h1.font.color.rgb = RGBColor(0, 0, 0)
    _east_asia(h1, "Palatino Linotype")
    h2 = doc.styles["Heading 2"]
    h2.font.name, h2.font.size, h2.font.bold, h2.font.underline = "Times New Roman", Pt(12), True, True
    h2.font.color.rgb = RGBColor(0x1F, 0x4E, 0x79)
    _east_asia(h2, "Times New Roman")
    # page number, bottom right
    fp = sec.footer.paragraphs[0]
    fp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    _field(fp, "PAGE")
    _ensure_styles(doc)


def _ensure_styles(doc) -> None:
    from docx.enum.style import WD_STYLE_TYPE

    names = {s.name for s in doc.styles}
    for n in ("Heading 1", "Heading 2"):
        if n not in names:
            doc.styles.add_style(n, WD_STYLE_TYPE.PARAGRAPH)


def _east_asia(style, font: str) -> None:
    rpr = style.element.get_or_add_rPr()
    fonts = rpr.find(_qn("w:rFonts"))
    if fonts is None:
        from docx.oxml import OxmlElement

        fonts = OxmlElement("w:rFonts")
        rpr.append(fonts)
    for a in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        fonts.set(_qn(a), font)


def _field(paragraph, instr: str, placeholder: str = ""):
    from docx.oxml import OxmlElement

    def el(tag, **attrs):
        e = OxmlElement(tag)
        for k, v in attrs.items():
            e.set(_qn(k), v)
        return e

    r1 = paragraph.add_run()
    r1._r.append(el("w:fldChar", **{"w:fldCharType": "begin"}))
    r2 = paragraph.add_run()
    t = el("w:instrText", **{"xml:space": "preserve"})
    t.text = f" {instr} "
    r2._r.append(t)
    r3 = paragraph.add_run()
    r3._r.append(el("w:fldChar", **{"w:fldCharType": "separate"}))
    paragraph.add_run(placeholder)
    r4 = paragraph.add_run()
    r4._r.append(el("w:fldChar", **{"w:fldCharType": "end"}))


def _update_fields_on_open(doc) -> None:
    from docx.oxml import OxmlElement

    settings = doc.settings.element
    el = settings.find(_qn("w:updateFields"))
    if el is None:
        el = OxmlElement("w:updateFields")
        settings.append(el)
    el.set(_qn("w:val"), "true")


# =============================================================== writer
class _Writer:
    def __init__(self, doc, ctx: Context, images: dict[str, Path], work: Path):
        from docx.shared import Pt

        self.doc, self.ctx, self.images, self.work = doc, ctx, images, work
        self.errors: list[str] = []
        self.fig_no = self.table_no = self.plot_no = 0
        self.Pt = Pt

    # ---- primitives -------------------------------------------------------
    def para(self, text: str = "", *, font=None, size=None, bold=None, italic=None, align=None, style=None,
             indent_left=None, first_line=None, space_after=None, markdown_bold=True):
        from docx.shared import Inches, Pt

        p = self.doc.add_paragraph(style=style) if style else self.doc.add_paragraph()
        if align is not None:
            p.alignment = align
        pf = p.paragraph_format
        if indent_left is not None:
            pf.left_indent = Inches(indent_left)
        if first_line is not None:
            pf.first_line_indent = Inches(first_line)
        if space_after is not None:
            pf.space_after = Pt(space_after)
        chunks = re.split(r"(\*\*[^*]+\*\*)", text) if markdown_bold else [text]
        for ch in chunks:
            if not ch:
                continue
            is_b = ch.startswith("**") and ch.endswith("**") and len(ch) > 4
            r = p.add_run(ch[2:-2] if is_b else ch)
            if font:
                r.font.name = font
                rpr = r._r.get_or_add_rPr()
                f = rpr.find(_qn("w:rFonts"))
                if f is not None:
                    f.set(_qn("w:cs"), font)
            if size:
                r.font.size = Pt(size)
            r.bold = True if is_b else bold
            if italic is not None:
                r.italic = italic
        return p

    def text(self, s: str, where: str) -> str:
        return fill_text(self.ctx, s, self.errors, where)

    def caption(self, text: str, font="Arial", size=None):
        from docx.enum.text import WD_ALIGN_PARAGRAPH

        p = self.para(text, font=font, size=size, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=10)
        p.paragraph_format.space_before = self.Pt(6)
        return p

    def image(self, source: str, width_in: float):
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.shared import Inches

        p = self.doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        path = self.images.get(source)
        if path is None or not Path(path).exists():
            p.add_run(f"[image not available: {source}]").italic = True
            return p
        try:
            from PIL import Image

            with Image.open(path) as im:
                w, h = im.size
            # keep tall images within ~6.5 in of height
            width_in = min(width_in, 6.5 * w / h) if h else width_in
        except Exception:
            pass
        p.add_run().add_picture(str(path), width=Inches(width_in))
        p.paragraph_format.keep_with_next = True
        return p

    def page_break(self):
        from docx.enum.text import WD_BREAK

        self.doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

    # ---- sections -----------------------------------------------------------
    def cover(self, title: str, prof: Profile):
        from docx.enum.text import WD_ALIGN_PARAGRAPH as A

        for _ in range(6):
            self.para("", align=A.CENTER)
        if prof.course:
            self.para(prof.course, font="Times New Roman", size=12, align=A.CENTER)
        self.para(f"Instructor: {prof.instructor}".rstrip(), font="Times New Roman", size=12, align=A.CENTER)
        self.para("", align=A.CENTER)
        self.para(title, font="Arial", size=12, bold=True, align=A.CENTER)
        for _ in range(6):
            self.para("", align=A.CENTER)
        p = self.para("Student Name(s):\t", font="Times New Roman", size=12, bold=True)
        r = p.add_run(prof.student)
        r.font.name, r.font.size = "Times New Roman", self.Pt(12)
        self.para("")
        p = self.para("Date: ", font="Arial", size=12, bold=True, align=A.RIGHT)
        r = p.add_run(prof.date_text())
        r.font.name, r.font.size = "Arial", self.Pt(12)
        self.page_break()

    def toc(self):
        p = self.para("Contents", font="Times New Roman", size=12, bold=True)
        p.runs[0].underline = True
        _field(self.doc.add_paragraph(), 'TOC \\o "1-3" \\h \\z \\u',
               "Right-click and choose Update Field to build the table of contents.")
        _update_fields_on_open(self.doc)
        self.page_break()

    def heading(self, text: str, level: int):
        return self.doc.add_heading(text, level)

    def body(self, text: str, where: str):
        from docx.enum.text import WD_ALIGN_PARAGRAPH as A

        for chunk in re.split(r"\n\s*\n", self.text(text, where).strip()):
            if chunk.strip():
                self.para(chunk.strip().replace("\n", " "), font="Times New Roman", size=12, align=A.JUSTIFY, space_after=8)

    def parts(self, parts: list[dict]):
        from docx.enum.text import WD_ALIGN_PARAGRAPH as A

        n = 0
        for pi, part in enumerate(parts):
            self.para("")
            p = self.para(part.get("heading", ""), font="Arial", size=11, bold=True)
            for r in p.runs:
                r.underline = True
            for si, st in enumerate(part.get("steps", [])):
                n += 1
                where = f"parts[{pi}].steps[{si}]"
                self.para(f"{n}.\t{self.text(st.get('text', ''), where)}", font="Arial", size=10.5,
                          indent_left=0.5, first_line=-0.25, align=A.JUSTIFY, space_after=4)
                for k, ss in enumerate(st.get("sub_steps") or []):
                    self.para(f"{chr(97 + k)}.\t{self.text(ss, where)}", font="Arial", size=10.5,
                              indent_left=0.9, first_line=-0.25, space_after=2)
                if st.get("equation"):
                    self.para(self.text(st["equation"], where), font="Arial", size=10.5, align=A.CENTER, space_after=6)
                if st.get("figure_source"):
                    self.image(st["figure_source"], 4.3)
                    self.fig_no += 1
                    self.caption(f"Figure {self.fig_no}: {st.get('figure_caption') or ''}".rstrip(": "))

    def data(self, items: list[dict]):
        for di, item in enumerate(items):
            where = f"data[{di}]"
            if item.get("type") == "table":
                self.table(item, where)
                self.table_no += 1
                self.caption(f"Table {self.table_no}: {item.get('caption', '')}")
            elif item.get("plot"):
                plot = item["plot"]
                series, vectors = _plot_values(plot, self.ctx, self.errors, where)
                self.plot_no += 1
                path = self.work / f"labplot_{self.plot_no}.png"
                (draw_phasor(plot, vectors, path) if plot.get("style") == "phasor" else draw_xy(plot, series, path))
                self.images[f"labplot:{self.plot_no}"] = path
                self.image(f"labplot:{self.plot_no}", 5.8 if plot.get("style") != "phasor" else 4.6)
                self.caption(f"Plot {self.plot_no}: {item.get('caption', '')}")
            self.para("")

    def table(self, item: dict, where: str):
        from docx.enum.table import WD_TABLE_ALIGNMENT
        from docx.enum.text import WD_ALIGN_PARAGRAPH as A

        header_rows = [list(r) for r in item.get("header_rows") or []]
        rows = [[render_cell(self.ctx, c, self.errors, where) for c in r] for r in item.get("rows") or []]
        ncols = max([len(r) for r in header_rows + rows] or [1])
        t = self.doc.add_table(rows=0, cols=ncols)
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        try:
            t.style = self.doc.styles["Table Grid"]
        except KeyError:
            _grid_borders(t)
        for hi, hr in enumerate(header_rows):
            cells = t.add_row().cells
            for j in range(ncols):
                txt = hr[j] if j < len(hr) else ""
                _cell_text(cells[j], txt, bold=True, align=A.CENTER, size=10)
            # merge equal neighbours (e.g. "Calculated" spanning several columns)
            j = 0
            while j < ncols:
                k = j
                while k + 1 < ncols and k + 1 < len(hr) and hr[k + 1] == hr[j] and hr[j]:
                    k += 1
                if k > j:
                    merged = cells[j].merge(cells[k])
                    _cell_text(merged, hr[j], bold=True, align=A.CENTER, size=10, replace=True)
                j = k + 1
        for r in rows:
            cells = t.add_row().cells
            for j in range(ncols):
                _cell_text(cells[j], r[j] if j < len(r) else "", align=A.CENTER, size=10)
        # keep the table on one page and attached to its caption
        for row in t.rows:
            for c in row.cells:
                for p in c.paragraphs:
                    p.paragraph_format.keep_with_next = True

    def calculations(self, calcs: list[dict]):
        for ci, calc in enumerate(calcs):
            self.para("")
            lines = calc.get("lines", [])
            for li, line in enumerate(lines):
                where = f"calculations[{ci}].lines[{li}]"
                v = eval_number(self.ctx, line.get("expr", ""), self.errors, where)
                try:
                    sub = self.ctx.substituted(line.get("expr", "")) if v is not None else "—"
                except Exception:
                    sub = line.get("expr", "")
                from eesimlab.labexpr import fmt_value

                res = fmt_value(v, line.get("unit", ""), line.get("fmt", "")) if v is not None else "—"
                prefix = f"{calc.get('label', '')}: " if li == 0 and calc.get("label") else ""
                formula = line.get("formula", "").strip()
                text = f"{prefix}{formula} = {sub} = {res}" if formula else f"{prefix}{sub} = {res}"
                self.para(text, font="Times New Roman", size=12, bold=True, markdown_bold=False, space_after=2)

    def screenshots(self, shots: list[dict]):
        for sh in shots:
            self.image(sh.get("source", ""), 6.0)
            self.fig_no += 1
            self.caption(f"Figure {self.fig_no}: {sh.get('caption', '')}", font="Times New Roman", size=12)

    def references(self, refs: list[str]):
        from docx.enum.text import WD_ALIGN_PARAGRAPH as A

        for i, ref in enumerate(refs, 1):
            self.para(f"{i}.\t{ref}", font="Times New Roman", size=12, indent_left=0.5, first_line=-0.5,
                      align=A.LEFT, markdown_bold=False, space_after=6)


def _grid_borders(table) -> None:
    from docx.oxml import OxmlElement

    tbl_pr = table._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        e = OxmlElement(f"w:{edge}")
        e.set(_qn("w:val"), "single")
        e.set(_qn("w:sz"), "4")
        e.set(_qn("w:color"), "000000")
        borders.append(e)
    tbl_pr.append(borders)


def _cell_text(cell, text: str, bold=False, align=None, size=10, replace=False):
    from docx.shared import Pt

    if replace:
        for p in cell.paragraphs[1:]:
            p._element.getparent().remove(p._element)
    p = cell.paragraphs[0]
    for r in list(p.runs):
        r._r.getparent().remove(r._r)
    if align is not None:
        p.alignment = align
    lines = str(text).split("\n")
    for i, line in enumerate(lines):
        r = p.add_run(line)
        r.bold = bold
        r.font.name = "Arial"
        r.font.size = Pt(size)
        if i < len(lines) - 1:
            r.add_break()


def _ref_key(ref: str) -> str:
    """Author + year signature used to avoid listing the same source twice."""
    head = ref.split(")")[0] if ")" in ref[:80] else ref[:40]
    return re.sub(r"[^a-z0-9]", "", head.lower())


def build_lab_report(report: dict, ctx: Context, images: dict[str, Path], profile: Profile, out_path: Path) -> tuple[Path, list[str]]:
    """Render the LabReport JSON to .docx. Returns (path, evaluation problems)."""
    doc = open_template(profile.template)
    work = out_path.parent / "lab_report_assets"
    work.mkdir(parents=True, exist_ok=True)
    w = _Writer(doc, ctx, dict(images), work)
    title = report.get("title", "")
    w.cover(title, profile)
    w.toc()
    w.heading("Abstract", 1)
    w.body(report.get("abstract", ""), "abstract")
    w.heading("Introduction", 1)
    w.body(report.get("introduction", ""), "introduction")
    w.heading("Procedures", 1)
    w.body(report.get("procedures", ""), "procedures")
    w.parts(report.get("parts", []))
    w.heading("Data Presentation & Analysis", 1)
    w.data(report.get("data", []))
    if report.get("calculations"):
        w.heading("Calculations", 2)
        w.calculations(report["calculations"])
    if report.get("screenshots"):
        w.heading("Required Screenshots", 2)
        w.screenshots(report["screenshots"])
    w.heading("Conclusion", 1)
    w.body(report.get("conclusion", ""), "conclusion")
    refs = list(report.get("references") or [])
    keys = {_ref_key(r) for r in refs}
    for r in profile.references:
        if r.strip() and _ref_key(r) not in keys:
            refs.append(r.strip())
            keys.add(_ref_key(r))
    if refs:
        w.heading("References", 1)
        w.references(refs)
    doc.save(str(out_path))
    return out_path, w.errors
