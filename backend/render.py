"""
Report rendering: one template, both required formats.

    DOCX  <- docxtpl for the scalar fields, then a python-docx pass that
             replaces each [[TABLE:x]] marker with a real table
    PDF   <- LibreOffice headless, converting that same DOCX

Rendering the PDF from the DOCX rather than from a second layout means the
two can never drift apart, and the laboratory can edit templates/r76-2.docx
without touching code.

Why tables are not Jinja
------------------------
Two approaches were tried and rejected:

  * {%tr%} row loops get mangled, because Word splits text across runs and
    two markers in one row confuse the parser;
  * docxtpl subdocuments inline their XML inside a <w:t> run, producing
    nested paragraphs that LibreOffice silently drops -- the tables simply
    vanish from the PDF with no error raised anywhere.

So the template carries a plain [[TABLE:x]] marker and this module swaps
that paragraph for a real table after the Jinja pass. Deterministic, and
the column definitions stay next to the data they format.

Page breaks
-----------
A test report is read as evidence, so a table may not be cut in a way that
changes what it appears to say. Three rules, applied to every table built
here:

  * cantSplit on every row -- a row never straddles a page boundary;
  * tblHeader on row 0 -- a table continuing overleaf reprints its column
    names, so a reviewer can still tell which column carries the verdict;
  * tables of KEEP_TOGETHER_ROWS rows or fewer move to the next page whole
    rather than breaking, which is what keeps the five-row summary of type
    evaluation intact.

Long tables still break, because a fifty-row weighing table has to.
"""

import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from docxtpl import DocxTemplate

from paths import STANDARDS_DIR

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "templates", "r76-2.docx")
STD_DIR = STANDARDS_DIR

SLATE = RGBColor(0x5B, 0x6E, 0x7A)
FAIL_RED = RGBColor(0xB3, 0x32, 0x1C)
PASS_GREEN = RGBColor(0x0F, 0x6B, 0x4F)
MARKER = re.compile(r"\[\[TABLE:(\w+)\]\]")

# A table this short is held on one page whatever it contains: it is the
# "Not recorded." placeholder, and splitting a header from its single row
# says nothing and wastes a page boundary. Deliberately small -- an earlier
# value of 9 held whole eight-row data tables back and left 4 cm of white at
# the foot of the preceding page, which is worse than any split.
KEEP_TOGETHER_ROWS = 3

# A4 less the template's 1.8 cm margins. The per-table widths below are
# proportions rather than absolutes: they are scaled to this so every table
# reaches the right margin, whatever their author made them add up to.
TEXT_WIDTH_CM = 21.0 - 2 * 1.8

TEST_TITLES = {
    "weighing_performance": ("Weighing performance", "A.4.4.1, A.4.4.3"),
    "repeatability": ("Repeatability", "3.6.1"),
    "eccentricity": ("Eccentricity", "A.4.7"),
    "time_dependence": ("Time-dependence (creep)", "A.4.11.1"),
    "electrical_disturbances": ("Electrical disturbances", "B.3"),
}

SOFFICE_CANDIDATES = ("soffice", "libreoffice",
                      "/usr/bin/soffice", "/usr/lib/libreoffice/program/soffice")


# ---------------------------------------------------------------------------
# formatting
# ---------------------------------------------------------------------------

def make_formatter(e_g: float):
    """Format a mass at the instrument's own resolution.

    A report printing 10.0000 kg when the instrument resolves 0.02 kg is
    claiming precision the instrument does not have.
    """
    use_kg = e_g >= 1.0
    step = e_g / 1000.0 if use_kg else e_g
    places, x = 0, step
    while abs(x - round(x)) > 1e-9 and places < 6:
        x *= 10
        places += 1
    unit = "kg" if use_kg else "g"

    def fmt(value: Optional[float]) -> str:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return "\u2014"
        v = float(value) / 1000.0 if use_kg else float(value)
        # avoid printing "-0.000 kg" for a value that rounds to zero
        if abs(v) < 0.5 * 10 ** (-places):
            v = 0.0
        return f"{v:,.{places}f} {unit}"

    return fmt


def prettify(code: Optional[str]) -> str:
    if not code:
        return "\u2014"
    return str(code).replace("_", " ").strip().capitalize()


def _standard_meta(standard_id: str) -> Dict[str, str]:
    try:
        with open(os.path.join(STD_DIR, "mpe-bands.json")) as f:
            na = json.load(f).get("national_adoption", {})
        return {
            "standard_title": "OIML R 76-1:2006 (E) Non-automatic weighing instruments",
            "national_clause": f"{na.get('instrument', '')} \u2014 {na.get('clause', '')}",
        }
    except Exception:
        return {"standard_title": standard_id, "national_clause": ""}


# ---------------------------------------------------------------------------
# table specifications
# ---------------------------------------------------------------------------

def table_specs(computed: Dict[str, Any], summary_rows: List[Dict[str, Any]],
                fmt) -> Dict[str, Dict[str, Any]]:
    """Columns, rows and widths for every table in the report."""
    T = computed["tests"]

    def spec(columns, rows, widths, verdict_col=None, keep_together=False):
        return {"columns": columns, "rows": rows, "widths": widths,
                "verdict_col": verdict_col, "keep_together": keep_together}

    return {
        "specification": spec(
            ["Result", "Clause", "Code", "Finding"],
            [[f["level"], f["clause"], f["code"], f["message"]]
             for f in computed["specification"]["findings"]],
            [1.8, 2.4, 3.4, 9.4], verdict_col=0, keep_together=True),

        "summary": spec(
            ["No.", "Test", "Clause", "Observations", "Result"],
            [[r["no"], r["title"], r["clause"], r["count"], r["verdict"]]
             for r in summary_rows],
            [1.2, 6.4, 3.0, 3.0, 3.4], verdict_col=4, keep_together=True),

        "weighing": spec(
            ["No.", "Load L", "Direction", "Indication I", "\u0394L", "P",
             "Error Ec", "mpe", "Result"],
            [[k, fmt(r["load_g"]), r["direction"], fmt(r["indication_g"]),
              fmt(r["delta_load_g"]), fmt(r["P_g"]), fmt(r["Ec_g"]),
              "\u00b1 " + fmt(r["mpe_g"]), r["verdict"]]
             for k, r in enumerate(T["weighing_performance"]["rows"], 1)],
            [1.0, 2.2, 2.1, 2.2, 1.7, 2.0, 2.0, 2.0, 1.8], verdict_col=8),

        "repeatability": spec(
            ["Test load", "Weighings", "Spread", "Spread in e", "Limit", "Result"],
            [[fmt(r["load_g"]), r["n_weighings"], fmt(r["spread_g"]),
              f"{r['spread_e']:.2f} e", fmt(r["limit_g"]), r["verdict"]]
             for r in T["repeatability"]["series"]],
            [3.0, 2.2, 2.8, 2.6, 2.8, 2.0], verdict_col=5),

        "eccentricity": spec(
            ["Position", "Load", "Indication", "Error Ec", "mpe", "Result"],
            [[r["position"], fmt(r["load_g"]), fmt(r["indication_g"]),
              fmt(r["Ec_g"]), "\u00b1 " + fmt(r["mpe_g"]), r["verdict"]]
             for r in T["eccentricity"]["rows"]],
            [3.2, 2.8, 2.8, 2.6, 2.6, 2.4], verdict_col=5),

        "creep": spec(
            ["Elapsed", "Indication", "Deviation", "Deviation in e"],
            [[f"{r['elapsed_minutes']} min", fmt(r["indication_g"]),
              fmt(r["deviation_g"]), f"{r['deviation_e']:.2f} e"]
             for r in T["time_dependence"]["rows"]],
            [3.4, 4.0, 4.0, 4.0]),

        "disturbances": spec(
            ["Disturbance", "Without", "With", "Difference", "Limit",
             "Fault detected", "Result"],
            [[prettify(r["disturbance"]), fmt(r["indication_without_g"]),
              fmt(r["indication_with_g"]), fmt(r["difference_g"]),
              fmt(r["limit_g"]),
              "yes" if r["significant_fault_detected"] else "no", r["verdict"]]
             for r in T["electrical_disturbances"]["rows"]],
            [3.6, 2.2, 2.2, 2.2, 2.0, 2.2, 1.8], verdict_col=6),
    }


# ---------------------------------------------------------------------------
# table construction and marker replacement
# ---------------------------------------------------------------------------

def _shade(cell, fill: str):
    el = OxmlElement("w:shd")
    el.set(qn("w:val"), "clear")          # never "solid" -- renders black
    el.set(qn("w:fill"), fill)
    cell._tc.get_or_add_tcPr().append(el)


def _cant_split(row):
    """Forbid Word/LibreOffice from breaking this row across a page.

    Without this a row whose cells wrap to two lines can be sliced at the
    page boundary, leaving a stripe of table on the next page with one word
    in it -- which is what put a lone "disturbances" fragment on page 2.
    """
    pr = row._tr.get_or_add_trPr()
    el = OxmlElement("w:cantSplit")
    el.set(qn("w:val"), "true")
    pr.append(el)


def _repeat_header(row):
    """Mark the row as a header, so it reprints at the top of every page.

    A continuation table with no column names is unreadable, and a reviewer
    cannot tell which column carries the verdict.
    """
    pr = row._tr.get_or_add_trPr()
    el = OxmlElement("w:tblHeader")
    el.set(qn("w:val"), "true")
    pr.append(el)


def _set_grid(table, widths_cm):
    """Write the column widths into w:tblGrid as well as into the cells.

    Under a fixed layout the renderer lays columns out from tblGrid; the
    per-cell w:tcW is only a hint. python-docx writes an equal split into
    tblGrid at creation and never revises it, so cell widths alone leave every
    column identical and wide text wraps where it should not.
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


def _keep_with_next(paragraph):
    """Stop a heading or a header row being orphaned at the foot of a page."""
    paragraph.paragraph_format.keep_with_next = True


def _build_table(doc, spec: Dict[str, Any]):
    columns = spec["columns"]
    widths = spec.get("widths")
    verdict_col = spec.get("verdict_col")

    if widths:
        total = float(sum(widths))
        if total > 0:
            scale = TEXT_WIDTH_CM / total
            widths = [w * scale for w in widths]

    t = doc.add_table(rows=1, cols=len(columns))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.LEFT
    if widths:
        t.autofit = False
        _set_grid(t, widths)

    header = t.rows[0]
    _repeat_header(header)
    _cant_split(header)

    for k, name in enumerate(columns):
        c = header.cells[k]
        _shade(c, "EDF1F2")
        p = c.paragraphs[0]
        p.paragraph_format.space_after = Pt(1)
        _keep_with_next(p)
        run = p.add_run(str(name))
        run.font.size = Pt(8)
        run.font.bold = True
        run.font.color.rgb = SLATE
        if widths:
            c.width = Cm(widths[k])

    if not spec["rows"]:
        empty = t.add_row()
        _cant_split(empty)
        cells = empty.cells
        cells[0].merge(cells[-1])
        r = cells[0].paragraphs[0].add_run("Not recorded.")
        r.font.size = Pt(9)
        r.font.color.rgb = SLATE
        return t

    for row in spec["rows"]:
        tr = t.add_row()
        _cant_split(tr)
        cells = tr.cells
        for k, value in enumerate(row):
            p = cells[k].paragraphs[0]
            p.paragraph_format.space_after = Pt(1)
            run = p.add_run("" if value is None else str(value))
            run.font.size = Pt(9)
            if verdict_col is not None and k == verdict_col:
                run.font.bold = True
                if str(value) == "FAIL":
                    run.font.color.rgb = FAIL_RED
                elif str(value) == "PASS":
                    run.font.color.rgb = PASS_GREEN
            if widths:
                cells[k].width = Cm(widths[k])

    # Chaining keep-with-next down every row but the last moves the whole
    # table to the next page as a unit. That is right for the summary -- a
    # verdict table read at a glance must not be cut -- and for a placeholder
    # of two rows. It is wrong for everything else: forcing a data table to
    # move whole buys a clean table at the cost of a half-empty page, and
    # cantSplit plus the repeating header already make a split safe to read.
    if spec.get("keep_together") or len(t.rows) <= KEEP_TOGETHER_ROWS:
        for tr in t.rows[:-1]:
            for cell in tr.cells:
                for p in cell.paragraphs:
                    _keep_with_next(p)
    return t


def replace_markers(path: str, specs: Dict[str, Dict[str, Any]]) -> int:
    """Swap every [[TABLE:x]] paragraph for the corresponding table."""
    doc = Document(path)
    replaced = 0

    for paragraph in list(doc.paragraphs):
        m = MARKER.search(paragraph.text or "")
        if not m:
            continue
        # the template writes [[TABLE:tbl_weighing]]; specs are keyed
        # without the prefix
        key = m.group(1)
        if key.startswith("tbl_"):
            key = key[4:]
        spec = specs.get(key)
        if spec is None:
            # leave the marker visible rather than dropping data silently
            continue
        table = _build_table(doc, spec)
        # A section heading sitting alone at the foot of a page with its table
        # overleaf reads as an empty section. Bind the two.
        prev = paragraph._p.getprevious()
        if prev is not None and prev.tag == qn("w:p"):
            for para in doc.paragraphs:
                if para._p is prev:
                    _keep_with_next(para)
                    break
        # python-docx appends at the end of the body; move it into position,
        # then drop the marker paragraph.
        paragraph._p.addprevious(table._tbl)
        paragraph._p.getparent().remove(paragraph._p)
        replaced += 1

    doc.save(path)
    return replaced


# ---------------------------------------------------------------------------
# context
# ---------------------------------------------------------------------------

def build_context(ev: Dict[str, Any], computed: Dict[str, Any],
                  payload_sha256: str) -> Dict[str, Any]:
    inst = dict(ev["instrument"])
    fmt = make_formatter(float(inst["e_g"]))
    meta = _standard_meta(ev.get("standard_id", ""))

    summary_rows = []
    for no, (code, (title, clause)) in enumerate(TEST_TITLES.items(), start=1):
        t = computed["tests"].get(code, {})
        count = len(t.get("rows") or t.get("series") or [])
        summary_rows.append({
            "no": no, "title": title, "clause": clause,
            "count": count if count else "not recorded",
            "verdict": t.get("verdict", "PENDING"),
        })

    lc = inst.get("load_cell") or ev.get("load_cell") or {}
    if isinstance(lc, str):
        try:
            lc = json.loads(lc)
        except Exception:
            lc = {}
    load_cell_text = " ".join(
        str(lc.get(k, "")) for k in
        ("manufacturer", "type", "capacity", "classification_symbol")
    ).strip() or "\u2014"

    v = computed["verdict"]
    if v == "PASS":
        conclusion = (f"The instrument conforms to the requirements of "
                      f"{meta['standard_title']} for the tests recorded in "
                      f"this report.")
    elif v == "FAIL":
        conclusion = (f"The instrument does not conform to the requirements of "
                      f"{meta['standard_title']}. The non-conforming results "
                      f"are marked FAIL in the sections above.")
    else:
        conclusion = ("Testing is incomplete. This report is provisional and "
                      "shall not be used as the basis for a certificate of "
                      "approval.")

    notice = ""
    if ev.get("is_synthetic"):
        notice = ("SYNTHETIC DATA \u2014 generated for demonstration and "
                  "testing. This report has no legal effect and cannot "
                  "support a certificate of approval.")

    return {
        **meta,
        "ref": ev["ref"],
        "status": prettify(ev.get("status", "draft")),
        "standard_id": ev.get("standard_id"),
        "lab": ev.get("lab") or {"name": "\u2014", "code": "\u2014"},
        "applicant": ev.get("applicant"),
        "application_no": ev.get("application_no"),
        "evaluation_from": ev.get("evaluation_from"),
        "evaluation_to": ev.get("evaluation_to"),
        "report_date": date.today().strftime("%d %B %Y"),
        "approval_mark": ev.get("approval_mark"),
        "instrument": inst,
        "instrument_type_label": prettify(inst.get("instrument_type")
                                          or inst.get("type")),
        "indication_label": prettify(inst.get("indication_type")),
        "derived": computed["derived"],
        "computed": computed,
        "summary_rows": summary_rows,
        "load_cell_text": load_cell_text,
        "weight_set_id": ev.get("weight_set_id") or inst.get("weight_set"),
        "conclusion_text": conclusion,
        "seal_locations": ev.get("seal_locations"),
        "payload_sha256": payload_sha256,
        "synthetic_notice": notice,
        "tested_by": ev.get("tested_by"),
        "verified_by": ev.get("verified_by"),
        "approved_by": ev.get("approved_by"),
        "fmt": fmt,
    }


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

def render_docx(ev: Dict[str, Any], computed: Dict[str, Any],
                payload_sha256: str, out_path: str,
                template: str = TEMPLATE) -> str:
    if not os.path.exists(template):
        raise FileNotFoundError(
            f"template missing: {template}. Run python3 backend/build_template.py")

    tpl = DocxTemplate(template)
    ctx = build_context(ev, computed, payload_sha256)
    tpl.render(ctx)
    tpl.save(out_path)

    specs = table_specs(computed, ctx["summary_rows"], ctx["fmt"])
    replaced = replace_markers(out_path, specs)
    if replaced != len(specs):
        raise RuntimeError(
            f"expected {len(specs)} table markers in the template, replaced "
            f"{replaced}. The template and render.py have drifted apart.")
    return out_path


def _soffice() -> Optional[str]:
    for c in SOFFICE_CANDIDATES:
        p = shutil.which(c) if not c.startswith("/") else (
            c if os.path.exists(c) else None)
        if p:
            return p
    return None


def docx_to_pdf(docx_path: str, out_dir: Optional[str] = None) -> Optional[str]:
    """Convert with LibreOffice headless.

    Returns None rather than raising when LibreOffice is unavailable, so the
    caller can still deliver the DOCX and the frontend can fall back to
    window.print() for a PDF.
    """
    soffice = _soffice()
    if not soffice:
        return None
    out_dir = out_dir or os.path.dirname(docx_path)

    # LibreOffice refuses to run two instances against one user profile,
    # which is exactly what concurrent requests on a Space would do.
    with tempfile.TemporaryDirectory() as profile:
        cmd = [soffice, "--headless", "--norestore", "--invisible",
               f"-env:UserInstallation=file://{profile}",
               "--convert-to", "pdf:writer_pdf_Export",
               "--outdir", out_dir, docx_path]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=180)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            stderr = getattr(exc, "stderr", b"") or b""
            raise RuntimeError(
                f"LibreOffice conversion failed: {stderr.decode()[:400]}") from exc

    pdf = os.path.join(out_dir,
                       os.path.splitext(os.path.basename(docx_path))[0] + ".pdf")
    return pdf if os.path.exists(pdf) else None


def safe_name(ref: str) -> str:
    """RRSL-FBD/NAWI/2026/0042  ->  RRSL-FBD_NAWI_2026_0042"""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", ref).strip("_")


def render(ev: Dict[str, Any], computed: Dict[str, Any], payload_sha256: str,
           out_dir: str) -> Tuple[str, Optional[str]]:
    """Render both formats. Returns (docx_path, pdf_path_or_None)."""
    os.makedirs(out_dir, exist_ok=True)
    stem = safe_name(ev["ref"])
    docx_path = render_docx(ev, computed, payload_sha256,
                            os.path.join(out_dir, f"{stem}.docx"))
    return docx_path, docx_to_pdf(docx_path, out_dir)
