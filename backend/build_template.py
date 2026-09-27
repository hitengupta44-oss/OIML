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


def para(doc, text="", size=10, bold=False, color=INK, space_after=4,
         align=None, italic=False):
    p = doc.add_paragraph()
    run = p.add_run(text)
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.italic = italic
    run.font.color.rgb = color
    run.font.name = "Calibri"
    p.paragraph_format.space_after = Pt(space_after)
    if align:
        p.alignment = align
    return p


def heading(doc, text, size=12):
    p = para(doc, text, size=size, bold=True, space_after=3)
    bottom_border(p)
    return p


def kv_table(doc, rows, label_w=5.2, value_w=10.8):
    """Two-column label/value block."""
    t = doc.add_table(rows=0, cols=2)
    t.alignment = WD_TABLE_ALIGNMENT.LEFT
    for label, value in rows:
        cells = t.add_row().cells
        cells[0].width = Cm(label_w)
        cells[1].width = Cm(value_w)
        r0 = cells[0].paragraphs[0].add_run(label)
        r0.font.size = Pt(9)
        r0.font.color.rgb = SLATE
        r1 = cells[1].paragraphs[0].add_run(value)
        r1.font.size = Pt(10)
        for c in cells:
            c.paragraphs[0].paragraph_format.space_after = Pt(1)
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
    p.paragraph_format.space_after = Pt(6)
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
    for el, txt in (("begin", "PAGE"), ("end", None)):
        fld = OxmlElement("w:fldChar") if el != "instrText" else None
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
    doc.add_paragraph()

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
    doc.add_paragraph()

    # ---------------- 2. specification check ----------------
    heading(doc, "2.  Examination of the declared specification")
    para(doc, "Checked against {{ standard_title }}, clause 3.2 and Table 3 "
              "before any test data was recorded.",
         size=9, color=SLATE, space_after=6)
    table_slot(doc, "tbl_specification")
    doc.add_paragraph()

    # ---------------- 3. summary ----------------
    heading(doc, "3.  Summary of type evaluation")
    table_slot(doc, "tbl_summary")
    doc.add_paragraph()

    doc.add_page_break()

    # ---------------- numbered test sections ----------------
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
        para(doc, clause, size=9, color=SLATE, space_after=2)
        para(doc, note, size=8.5, color=SLATE, italic=True, space_after=6)
        table_slot(doc, slot)
        doc.add_paragraph()

    # ---------------- conclusion ----------------
    doc.add_page_break()
    heading(doc, "9.  Conclusion")
    para(doc, "{{ conclusion_text }}", size=10, space_after=10)

    kv_table(doc, [
        ("Overall verdict", "{{ computed.verdict }}"),
        ("Standard applied", "{{ standard_id }}"),
        ("Independent recompute", "{{ 'server reproduced every client verdict' "
                                  "if computed.agrees_with_client "
                                  "else 'DIVERGENCE — see below' }}"),
        ("Payload digest (SHA-256)", "{{ payload_sha256 }}"),
    ])
    doc.add_paragraph()

    para(doc, "Places where the verification seal or stamp may be affixed "
              "(Approval of Models Rules, 2011, Rule 11(1)(g)):",
         size=9, color=SLATE, space_after=2)
    para(doc, "{{ seal_locations or '—' }}", size=10, space_after=14)

    heading(doc, "10.  Signatures", size=11)
    t = doc.add_table(rows=2, cols=3)
    t.style = "Table Grid"
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
        for _ in range(2):
            c.add_paragraph()
        sig = c.add_paragraph()
        sr = sig.add_run("signature and date")
        sr.font.size = Pt(7.5)
        sr.font.color.rgb = SLATE

    doc.add_paragraph()
    p = para(doc, "{{ synthetic_notice }}", size=9, bold=True,
             color=RGBColor(0xB3, 0x32, 0x1C), space_after=2)

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
