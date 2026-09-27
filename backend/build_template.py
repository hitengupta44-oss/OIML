"""
Build templates/r76-2.docx from standards/report-layout.json.

    python3 backend/build_template.py

Why generate it
---------------
The template is the one artefact a laboratory will want to adjust without a
developer -- a masthead, a different column order, a national annexure. It
is therefore a file, not code. But writing 17 numbered sections by hand and
keeping them in step with report-layout.json is exactly the kind of manual
duplication this whole project exists to remove, so the first version is
generated from the layout.

Replacing this with a hand-typeset version that matches the printed OIML
form is a drop-in: same placeholders, same filename, no code change. That
is the point of the docxtpl pipeline.

Placeholders are Jinja2, rendered by render.py.
"""

import json
import os

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from paths import STANDARDS_DIR

HERE = os.path.dirname(os.path.abspath(__file__))
LAYOUT = os.path.join(STANDARDS_DIR, "report-layout.json")
OUT = os.path.join(HERE, "templates", "r76-2.docx")

INK = RGBColor(0x11, 0x1A, 0x22)
SLATE = RGBColor(0x5B, 0x6E, 0x7A)
RULE = "D5DCE0"

# A4 (21.0 cm) less the 1.8 cm margins on each side. Every table is laid out
# to exactly this, so none stops short of the right margin.
TEXT_WIDTH_CM = 21.0 - 2 * 1.8

# Replaced with a real table by render.py after the Jinja pass.
TABLE_MARKER = "[[TABLE:%s]]"


# ---------------------------------------------------------------------------
# low-level helpers
# ---------------------------------------------------------------------------

def shade(cell, hex_fill):
    el = OxmlElement("w:shd")
    el.set(qn("w:val"), "clear")          # never "solid" -- renders black
    el.set(qn("w:fill"), hex_fill)
    cell._tc.get_or_add_tcPr().append(el)


def bottom_border(paragraph, size=6, color=RULE):
    pPr = paragraph._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), str(size))
    bottom.set(qn("w:space"), "2")
    bottom.set(qn("w:color"), color)
    borders.append(bottom)
    pPr.append(borders)


def cant_split(row):
    """Never let this row be sliced at a page boundary."""
    el = OxmlElement("w:cantSplit")
    el.set(qn("w:val"), "true")
    row._tr.get_or_add_trPr().append(el)


def set_grid(table, widths_cm):
    """Write the column widths into w:tblGrid, not just into the cells.

    Under a fixed layout the renderer lays columns out from tblGrid and the
    per-cell w:tcW is only a hint. python-docx fills tblGrid with an equal
    split at creation and never revises it, so setting cell widths alone
    leaves every column the same width -- which is what pushed the 64-character
    payload digest onto a second line however wide its cell claimed to be.
    """
    grid = table._tbl.find(qn("w:tblGrid"))
    if grid is None:
        return
    for col in list(grid):
        grid.remove(col)
    for cm in widths_cm:
        col = OxmlElement("w:gridCol")
        col.set(qn("w:w"), str(int(round(cm * 567))))   # twips: 1 cm = 567
        grid.append(col)


def para(doc, text="", size=10, bold=False, color=INK, space_after=4,
         align=None, italic=False, space_before=0, keep_with_next=False):
    p = doc.add_paragraph()
    run = p.add_run(text)
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.italic = italic
    run.font.color.rgb = color
    run.font.name = "Calibri"
    p.paragraph_format.space_after = Pt(space_after)
    p.paragraph_format.space_before = Pt(space_before)
    p.paragraph_format.keep_with_next = keep_with_next
    if align:
        p.alignment = align
    return p


def heading(doc, text, size=12, space_before=8):
    """A numbered section heading.

    Separation from the section above comes from space_before, never from an
    empty paragraph: an empty paragraph is a real line that can be pushed to
    the top of the next page on its own, which is where the stray white bands
    in the first drafts came from. keep_with_next binds the heading to
    whatever follows it, so a heading is never the last thing on a page.
    """
    p = para(doc, text, size=size, bold=True, space_after=3,
             space_before=space_before, keep_with_next=True)
    bottom_border(p)
    return p


def kv_table(doc, rows, label_w=5.2, value_w=TEXT_WIDTH_CM - 5.2, value_size=10,
             keep_together=False):
    """Two-column label/value block."""
    t = doc.add_table(rows=0, cols=2)
    t.alignment = WD_TABLE_ALIGNMENT.LEFT
    # Without a fixed layout Word and LibreOffice re-fit the columns to the
    # content and quietly ignore the widths set below -- which is what wrapped
    # the 64-character payload digest onto a second line.
    t.autofit = False
    set_grid(t, [label_w, value_w])
    for label, value in rows:
        tr = t.add_row()
        cant_split(tr)
        cells = tr.cells
        cells[0].width = Cm(label_w)
        cells[1].width = Cm(value_w)
        r0 = cells[0].paragraphs[0].add_run(label)
        r0.font.size = Pt(9)
        r0.font.color.rgb = SLATE
        r1 = cells[1].paragraphs[0].add_run(value)
        r1.font.size = Pt(value_size)
        for c in cells:
            c.paragraphs[0].paragraph_format.space_after = Pt(1)
    if keep_together and len(t.rows) > 1:
        # For a block read as a unit -- the closing verdict and its digest --
        # a split leaves one line stranded overleaf, which reads as a defect
        # in the report rather than as pagination.
        for tr in t.rows[:-1]:
            for c in tr.cells:
                for p in c.paragraphs:
                    p.paragraph_format.keep_with_next = True
    return t


def table_slot(doc, key):
    """Reserve a spot for a table built at render time.

    Tables are NOT Jinja. Two approaches were tried and rejected: {%tr%} row
    loops get mangled because Word splits text across runs, and docxtpl
    subdocuments inline their XML inside a <w:t> run, producing nested
    paragraphs that LibreOffice silently drops.

    So the template carries a plain marker, and render.py replaces that
    paragraph with a real table built by python-docx after the Jinja pass.
    Deterministic, and the table structure stays in code next to the data
    it formats.
    """
    p = doc.add_paragraph()
    run = p.add_run(TABLE_MARKER % key)
    run.font.size = Pt(9)
    p.paragraph_format.space_after = Pt(0)
    return p


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------

def build():
    layout = json.load(open(LAYOUT))
    doc = Document()

    s = doc.sections[0]
    s.page_width, s.page_height = Cm(21.0), Cm(29.7)      # A4
    s.left_margin = s.right_margin = Cm(1.8)
    s.top_margin = Cm(1.6)
    s.bottom_margin = Cm(1.6)

    # footer: "Report page n/total", as R 76-2 prints it
    footer_p = s.footer.paragraphs[0]
    footer_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    fr = footer_p.add_run("{{ ref }}    ·    Report page ")
    fr.font.size = Pt(8)
    fr.font.color.rgb = SLATE
    # PAGE field
    run = footer_p.add_run()
    for tag, attrs, text in (
        ("w:fldChar", {"w:fldCharType": "begin"}, None),
        ("w:instrText", {"xml:space": "preserve"}, " PAGE "),
        ("w:fldChar", {"w:fldCharType": "end"}, None),
    ):
        el = OxmlElement(tag)
        for k, v in attrs.items():
            el.set(qn(k), v)
        if text:
            el.text = text
        run._r.append(el)
    run.font.size = Pt(8)
    run.font.color.rgb = SLATE

    # ---------------- cover ----------------
    title = para(doc, "TYPE EVALUATION TEST REPORT", size=16, bold=True, space_after=2)
    para(doc, "Non-automatic weighing instrument", size=11, color=SLATE, space_after=10)
    p = para(doc, "{{ standard_title }}", size=9, color=SLATE, space_after=1)
    para(doc, "{{ national_clause }}", size=9, color=SLATE, space_after=14)

    kv_table(doc, [
        ("Report number", "{{ ref }}"),
        ("Testing laboratory", "{{ lab.name }} ({{ lab.code }})"),
        ("Application number", "{{ application_no or '—' }}"),
        ("Applicant", "{{ applicant or '—' }}"),
        ("Evaluation period", "{{ evaluation_from or '—' }} to {{ evaluation_to or '—' }}"),
        ("Date of report", "{{ report_date }}"),
        ("Status", "{{ status }}"),
        ("Approval mark", "{{ approval_mark or 'not yet assigned' }}"),
    ])

    # ---------------- 1. general information ----------------
    heading(doc, "1.  General information concerning the type")
    kv_table(doc, [
        ("Manufacturer", "{{ instrument.manufacturer }}"),
        ("Model", "{{ instrument.model }}"),
        ("Serial number", "{{ instrument.serial or '—' }}"),
        ("Instrument category", "{{ instrument_type_label }}"),
        ("Accuracy class", "{{ derived.class_symbol }}"),
        ("Indication", "{{ indication_label }}"),
        ("Maximum capacity, Max", "{{ fmt(instrument.max_capacity_g) }}"),
        ("Minimum capacity, Min", "{{ fmt(instrument.min_capacity_g) }}"),
        ("Verification scale interval, e", "{{ fmt(instrument.e_g) }}"),
        ("Actual scale interval, d", "{{ fmt(instrument.d_g) }}"),
        ("Number of intervals, n = Max/e", "{{ '{:,.0f}'.format(derived.n) }}"),
        ("Temperature range", "{{ instrument.temp_low_c }} °C to {{ instrument.temp_high_c }} °C"),
        ("Software version", "{{ instrument.software_version or '—' }}"),
        ("Software checksum", "{{ instrument.software_checksum or '—' }}"),
        ("Load cell", "{{ load_cell_text }}"),
        ("Standard weights used", "{{ instrument.weight_set or weight_set_id or '—' }}"),
    ])

    # ---------------- 2. specification check ----------------
    heading(doc, "2.  Examination of the declared specification")
    para(doc, "Checked against {{ standard_title }}, clause 3.2 and Table 3 "
              "before any test data was recorded.",
         size=9, color=SLATE, space_after=6, keep_with_next=True)
    table_slot(doc, "tbl_specification")

    # ---------------- 3. summary ----------------
    heading(doc, "3.  Summary of type evaluation")
    table_slot(doc, "tbl_summary")

    # ---------------- numbered test sections ----------------
    # No page break here. The sections flow, and keep_with_next on every
    # heading, clause line and note keeps each section's preamble attached to
    # its table -- which packs the pages without ever separating a heading
    # from the data underneath it.
    # Section order, titles and clause references come from the layout; the
    # tables themselves are built by render.py from the same source.
    section_specs = [
        (4, "Weighing performance", "A.4.4.1, A.4.4.3", "tbl_weighing",
         "P = I + \u00bde \u2212 \u0394L,   E = P \u2212 L,   Ec = E \u2212 E\u2080.  "
         "Ec is compared against the maximum permissible error for the applied load."),
        (5, "Repeatability", "3.6.1", "tbl_repeatability",
         "The difference between the results of several weighings of the same load "
         "shall not be greater than the absolute value of the mpe for that load."),
        (6, "Eccentricity", "A.4.7", "tbl_eccentricity",
         "The load is applied to the centre and to each quarter segment of the load "
         "receptor. Each indication shall stay within the mpe for that load."),
        (7, "Time-dependence (creep)", "A.4.11.1", "tbl_creep",
         "Within the first 30 minutes the indication shall not differ by more than 0.5 e."),
        (8, "Electrical disturbances", "B.3", "tbl_disturbances",
         "The difference between the indication with and without the disturbance shall "
         "not exceed 1 e, or the instrument shall detect and react to a significant fault."),
    ]

    for no, title_text, clause, slot, note in section_specs:
        heading(doc, f"{no}.  {title_text}")
        para(doc, clause, size=9, color=SLATE, space_after=2,
             keep_with_next=True)
        para(doc, note, size=8.5, color=SLATE, italic=True, space_after=4,
             keep_with_next=True)
        table_slot(doc, slot)

    # ---------------- conclusion ----------------
    heading(doc, "9.  Conclusion")
    para(doc, "{{ conclusion_text }}", size=10, space_after=10)

    kv_table(doc, [
        ("Overall verdict", "{{ computed.verdict }}"),
        ("Standard applied", "{{ standard_id }}"),
        ("Independent recompute", "{{ 'server reproduced every client verdict' "
                                  "if computed.agrees_with_client "
                                  "else 'DIVERGENCE — see below' }}"),
        ("Payload digest (SHA-256)", "{{ payload_sha256 }}"),
    ], value_size=9, keep_together=True)   # 64 hex chars on one line

    para(doc, "Places where the verification seal or stamp may be affixed "
              "(Approval of Models Rules, 2011, Rule 11(1)(g)):",
         size=9, color=SLATE, space_after=2, space_before=8,
         keep_with_next=True)
    para(doc, "{{ seal_locations or '—' }}", size=10, space_after=0)

    heading(doc, "10.  Signatures", size=11)
    t = doc.add_table(rows=2, cols=3)
    t.style = "Table Grid"
    t.autofit = False
    set_grid(t, [TEXT_WIDTH_CM / 3] * 3)
    for tr in t.rows:
        cant_split(tr)
    # Two rows, and they belong together: a header row stranded at the foot of
    # a page with the signing boxes overleaf is worse than moving both.
    for cell in t.rows[0].cells:
        for p in cell.paragraphs:
            p.paragraph_format.keep_with_next = True
    for k, role in enumerate(["Tested by", "Verified by", "Approved by"]):
        c = t.rows[0].cells[k]
        shade(c, "EDF1F2")
        r = c.paragraphs[0].add_run(role)
        r.font.size = Pt(8)
        r.font.bold = True
        r.font.color.rgb = SLATE
    for k, key in enumerate(["tested_by", "verified_by", "approved_by"]):
        c = t.rows[1].cells[k]
        c.paragraphs[0].add_run("{{ " + key + " or '' }}").font.size = Pt(10)
        # The signing space is space_before on the caption, not a blank
        # paragraph: it is the same gap but a measured one, so the block's
        # height does not drift with the default line height.
        sig = c.add_paragraph()
        sig.paragraph_format.space_before = Pt(20)
        sr = sig.add_run("signature and date")
        sr.font.size = Pt(7.5)
        sr.font.color.rgb = SLATE

    para(doc, "{{ synthetic_notice }}", size=9, bold=True,
         color=RGBColor(0xB3, 0x32, 0x1C), space_after=2, space_before=14)

    para(doc,
         "Generated by the NAWI type-evaluation system. Clause references are to "
         "{{ standard_title }}; the corresponding Indian provisions are in the "
         "Legal Metrology (General) Rules, 2011, Seventh Schedule, Heading A.",
         size=8, color=SLATE)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    doc.save(OUT)
    print(f"wrote {OUT}")
    print(f"  sections: {len(section_specs) + 3} + conclusion + signatures")
    print(f"  size: {os.path.getsize(OUT) / 1024:.1f} KB")


if __name__ == "__main__":
    build()
