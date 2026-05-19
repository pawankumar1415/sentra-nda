"""
Creates Analytics Screen Implementation Guide (Parts 7-13) as a Word document.
Run: python create_analytics_guide.py
"""

import pathlib
from docx import Document
from docx.shared import Pt, RGBColor, Inches, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

HERE = pathlib.Path(__file__).parent
ROOT = HERE.parent
OUT  = ROOT / "Analytics_Screen_Guide_Parts7to13.docx"


def _heading(doc, text, level=1):
    p = doc.add_heading(text, level=level)
    return p


def _para(doc, text="", bold=False, italic=False, color=None, size=None):
    p = doc.add_paragraph()
    run = p.add_run(text)
    run.bold = bold
    run.italic = italic
    if color:
        run.font.color.rgb = RGBColor(*color)
    if size:
        run.font.size = Pt(size)
    return p


def _bullet(doc, text, bold_prefix=""):
    p = doc.add_paragraph(style="List Bullet")
    if bold_prefix:
        r = p.add_run(bold_prefix)
        r.bold = True
        p.add_run(" " + text)
    else:
        p.add_run(text)
    return p


def _numbered(doc, text, bold_prefix=""):
    p = doc.add_paragraph(style="List Number")
    if bold_prefix:
        r = p.add_run(bold_prefix + ":")
        r.bold = True
        p.add_run(" " + text)
    else:
        p.add_run(text)
    return p


def _code(doc, text):
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Cm(1)
    run = p.add_run(text)
    run.font.name = "Courier New"
    run.font.size = Pt(9)
    run.font.color.rgb = RGBColor(0x1F, 0x38, 0x64)
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after  = Pt(2)
    return p


def _table_row(table, cells, bold=False, bg=None):
    row = table.add_row()
    for i, text in enumerate(cells):
        cell = row.cells[i]
        cell.text = text
        if bold:
            for run in cell.paragraphs[0].runs:
                run.bold = True
        if bg:
            tc = cell._tc
            tcPr = tc.get_or_add_tcPr()
            shd = OxmlElement("w:shd")
            shd.set(qn("w:val"), "clear")
            shd.set(qn("w:color"), "auto")
            shd.set(qn("w:fill"), bg)
            tcPr.append(shd)
    return row


def _note(doc, text):
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Cm(0.5)
    run = p.add_run("Note: " + text)
    run.italic = True
    run.font.color.rgb = RGBColor(0x4B, 0x55, 0x63)
    run.font.size = Pt(10)
    return p


def build(doc):
    # ── Title page ────────────────────────────────────────────────────────────
    t = doc.add_heading("Canvas App Analytics Screen", level=0)
    t.alignment = WD_ALIGN_PARAGRAPH.CENTER

    sub = doc.add_paragraph("Implementation Guide — Parts 7 to 13")
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    sub.runs[0].font.color.rgb = RGBColor(0x4B, 0x55, 0x63)
    sub.runs[0].font.size = Pt(13)

    doc.add_paragraph("Sentra NDA Portal — Power Apps Canvas App").alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_page_break()

    # ── Overview ──────────────────────────────────────────────────────────────
    _heading(doc, "Overview", level=1)
    _para(doc, (
        "This guide covers Parts 7 to 13 of the Analytics screen build. "
        "Parts 1 to 6 (sidebar, header, OnVisible formula) are assumed complete. "
        "The screen should already show the Analytics title, subtitle, and left navigation."
    ))
    _para(doc, "The three main sections being built:")
    _bullet(doc, "Six summary stat cards (Total Scored, Pass, Warnings, Fail, Individual, Batch)")
    _bullet(doc, "Pass Rate horizontal bar with legend")
    _bullet(doc, "Recent Scores table with verdict filter, sort, and expandable row detail")
    doc.add_paragraph()

    # ── Part 7 ────────────────────────────────────────────────────────────────
    _heading(doc, "Part 7 — Six Summary Cards", level=1)
    _para(doc, (
        "Add six card rectangles in a row below the header separator, "
        "each with three stacked labels: title, big number, and subtitle."
    ))

    _heading(doc, "Step 1 — Create the first card (TOTAL SCORED)", level=2)
    _numbered(doc, "Insert -> Rectangle")
    _bullet(doc, "X: 258,  Y: 140,  Width: 170,  Height: 100")
    _bullet(doc, "Fill: White")
    _bullet(doc, "BorderColor: colBorder,  BorderThickness: 1")
    _bullet(doc, "RadiusTopLeft / TopRight / BottomLeft / BottomRight: 8")
    _bullet(doc, "Rename: cardTotal")

    _numbered(doc, "Insert -> Text label  (card title)")
    _bullet(doc, 'Text: "TOTAL SCORED"')
    _bullet(doc, "X: 268,  Y: 148,  Width: 150,  Height: 20")
    _bullet(doc, "Size: 9,  FontWeight: Bold,  Color: colTextSec,  Align: Center")

    _numbered(doc, "Insert -> Text label  (big number)")
    _bullet(doc, "Text: Text(CountRows(colHistory))")
    _bullet(doc, "X: 268,  Y: 168,  Width: 150,  Height: 44")
    _bullet(doc, "Size: 28,  FontWeight: Bold,  Color: colText,  Align: Center")

    _numbered(doc, "Insert -> Text label  (subtitle)")
    _bullet(doc, 'Text: "narratives"')
    _bullet(doc, "X: 268,  Y: 212,  Width: 150,  Height: 20")
    _bullet(doc, "Size: 10,  Color: colTextSec,  Align: Center")

    _heading(doc, "Step 2 — Duplicate for remaining 5 cards", level=2)
    _para(doc, (
        "Select the cardTotal rectangle -> Ctrl+C -> Ctrl+V five times. "
        "Position and update each card as follows:"
    ))

    # Cards table
    tbl = doc.add_table(rows=1, cols=5)
    tbl.style = "Table Grid"
    _table_row(tbl, ["Card name", "X position", "Title text", "Number formula", "Number colour"], bold=True, bg="1F3864")
    for row_data in [
        ("cardPass",       "438", '"PASS"',       'CountRows(Filter(colHistory, overall_verdict = "PASS"))',                                                                      "colPass"),
        ("cardWarn",       "618", '"WARNINGS"',   'CountRows(Filter(colHistory, overall_verdict = "WARN" || overall_verdict = "PASS_WITH_WARNINGS"))',                            "colWarn"),
        ("cardFail",       "798", '"FAIL"',        'CountRows(Filter(colHistory, overall_verdict = "FAIL"))',                                                                     "colFail"),
        ("cardIndividual", "978", '"INDIVIDUAL"', "CountRows(colHistory)",                                                                                                        "colText"),
        ("cardBatch",     "1158", '"BATCH"',       '"0"',                                                                                                                         "colText"),
    ]:
        _table_row(tbl, list(row_data))

    doc.add_paragraph()
    _para(doc, "Subtitle text for each card:", bold=True)

    sub_tbl = doc.add_table(rows=1, cols=2)
    sub_tbl.style = "Table Grid"
    _table_row(sub_tbl, ["Card", "Subtitle"], bold=True, bg="2E75B6")
    for r in [
        ("cardPass",       '"score >= 8"'),
        ("cardWarn",       '"score 6-7"'),
        ("cardFail",       '"score < 6"'),
        ("cardIndividual", '"single validations"'),
        ("cardBatch",      '"batch project entries"'),
    ]:
        _table_row(sub_tbl, list(r))

    doc.add_paragraph()
    _note(doc, (
        "The INDIVIDUAL and BATCH cards currently show the same count because the database "
        "does not yet store a 'source' field. A backend fix will be applied separately to "
        "correctly split these counts."
    ))

    # ── Part 8 ────────────────────────────────────────────────────────────────
    doc.add_page_break()
    _heading(doc, "Part 8 — Pass Rate Bar", level=1)

    _heading(doc, "Step 1 — Card container", level=2)
    _numbered(doc, "Insert -> Rectangle")
    _bullet(doc, "X: 258,  Y: 255,  Width: 1070,  Height: 100")
    _bullet(doc, "Fill: White,  BorderColor: colBorder,  BorderThickness: 1,  Radius all: 8")
    _bullet(doc, "Rename: cardPassRate")

    _numbered(doc, "Insert -> Text label")
    _bullet(doc, 'Text: "Pass Rate"')
    _bullet(doc, "X: 278,  Y: 263,  Width: 150,  Height: 25")
    _bullet(doc, "FontWeight: Bold,  Size: 13,  Color: colText")

    _heading(doc, "Step 2 — Three bar segments  (Y: 295,  Height: 14)", level=2)

    _numbered(doc, "Green segment — rename: rectBarPass")
    _bullet(doc, "X: 278")
    _bullet(doc, "Width: Max(4, 1030 * CountRows(Filter(colHistory, overall_verdict=\"PASS\")) / Max(1, CountRows(colHistory)))")
    _bullet(doc, "Fill: colPass,  RadiusTopLeft: 6,  RadiusBottomLeft: 6")

    _numbered(doc, "Amber segment — rename: rectBarWarn")
    _bullet(doc, "X: 278 + rectBarPass.Width")
    _bullet(doc, "Width: Max(4, 1030 * CountRows(Filter(colHistory, overall_verdict=\"WARN\" || overall_verdict=\"PASS_WITH_WARNINGS\")) / Max(1, CountRows(colHistory)))")
    _bullet(doc, "Fill: colWarn")

    _numbered(doc, "Red segment — rename: rectBarFail")
    _bullet(doc, "X: 278 + rectBarPass.Width + rectBarWarn.Width")
    _bullet(doc, "Width: Max(4, 1030 * CountRows(Filter(colHistory, overall_verdict=\"FAIL\")) / Max(1, CountRows(colHistory)))")
    _bullet(doc, "Fill: colFail,  RadiusTopRight: 6,  RadiusBottomRight: 6")

    _heading(doc, "Step 3 — Legend labels  (Y: 318)", level=2)

    _numbered(doc, "Pass legend")
    _bullet(doc, 'Text: "Pass " & Text(Round(CountRows(Filter(colHistory, overall_verdict="PASS")) / Max(1, CountRows(colHistory)) * 100, 0)) & "%"')
    _bullet(doc, "X: 278,  Width: 120,  Color: colPass,  Size: 11")

    _numbered(doc, "Warn legend")
    _bullet(doc, 'Text: "Warn " & Text(Round(CountRows(Filter(colHistory, overall_verdict="WARN" || overall_verdict="PASS_WITH_WARNINGS")) / Max(1, CountRows(colHistory)) * 100, 0)) & "%"')
    _bullet(doc, "X: 408,  Width: 120,  Color: colWarn,  Size: 11")

    _numbered(doc, "Fail legend")
    _bullet(doc, 'Text: "Fail " & Text(Round(CountRows(Filter(colHistory, overall_verdict="FAIL")) / Max(1, CountRows(colHistory)) * 100, 0)) & "%"')
    _bullet(doc, "X: 538,  Width: 120,  Color: colFail,  Size: 11")

    # ── Part 9 ────────────────────────────────────────────────────────────────
    doc.add_page_break()
    _heading(doc, "Part 9 — Recent Scores Table", level=1)

    _heading(doc, "Step 1 — Section title", level=2)
    _numbered(doc, "Insert -> Text label")
    _bullet(doc, 'Text: "Recent Scores"')
    _bullet(doc, "X: 258,  Y: 368,  Width: 200,  Height: 30")
    _bullet(doc, "FontWeight: Bold,  Size: 14,  Color: colText")

    _heading(doc, "Step 2 — Column header bar", level=2)
    _numbered(doc, "Insert -> Rectangle")
    _bullet(doc, "X: 258,  Y: 402,  Width: 1070,  Height: 36")
    _bullet(doc, "Fill: colBg,  BorderColor: colBorder,  BorderThickness: 1")

    _numbered(doc, "Add 6 text labels on top of the rectangle  (Size: 10, FontWeight: Bold, Color: colTextSec, Fill: Transparent, Y: 402, Height: 36)")

    col_tbl = doc.add_table(rows=1, cols=3)
    col_tbl.style = "Table Grid"
    _table_row(col_tbl, ["Label text", "X", "Width"], bold=True, bg="2E75B6")
    for r in [
        ('"ID"',       "278",  "260"),
        ('"DOCUMENT"', "548",  "200"),
        ('"TYPE"',     "758",   "90"),
        ('"VERDICT"',  "858",   "90"),
        ('"SCORE"',    "958",   "60"),
        ('"NARRATIVE"',"1028", "280"),
    ]:
        _table_row(col_tbl, list(r))

    _heading(doc, "Step 3 — Gallery  (galHistory)", level=2)
    _numbered(doc, "Insert -> Vertical gallery")
    _bullet(doc, "X: 258,  Y: 438,  Width: 1070,  Height: Parent.Height - 448")
    _bullet(doc, "TemplateSize: 60")
    _bullet(doc, "TemplateFill: If(Mod(ThisRecord.index, 2)=0, colBg, colWhite)")
    _bullet(doc, "ShowScrollbar: true")
    _bullet(doc, "Items:")
    _code(doc, "SortByColumns(")
    _code(doc, "    Filter(")
    _code(doc, '        colHistory,')
    _code(doc, '        drpVerdictFilter.Selected.Value = "All verdicts" ||')
    _code(doc, "        overall_verdict = drpVerdictFilter.Selected.Value")
    _code(doc, "    ),")
    _code(doc, '    "validated_at",')
    _code(doc, '    If(drpSortFilter.Selected.Value = "Newest", Descending, Ascending)')
    _code(doc, ")")

    _heading(doc, "Step 4 — Controls inside the gallery template", level=2)
    _para(doc, "Click Edit on the gallery to enter template mode, then add:")

    ctrl_tbl = doc.add_table(rows=1, cols=4)
    ctrl_tbl.style = "Table Grid"
    _table_row(ctrl_tbl, ["Control", "Key property", "Formula / Value", "Position"], bold=True, bg="2E75B6")
    for r in [
        ("Text label\nlblHistId",         "Text",        "ThisItem.period & \" | \" & ThisItem.project_name",                                  "X:20 Y:20 W:260 H:25 Size:12 Bold"),
        ("Text label\nlblHistDoc",        "Text",        "Left(ThisItem.period & \" | \" & ThisItem.project_name, 22) & \"...\"",               "X:290 Y:20 W:200 H:25 Size:12 colTextSec"),
        ("HTML text\nhtmlType",           "HtmlText",    "\"<div style='background:rgba(219,234,254,1);color:rgba(29,78,216,1);\" &\n\"font-size:11px;font-weight:bold;border-radius:4px;\" &\n\"padding:4px 6px;text-align:center;font-family:sans-serif'>\" &\n\"INDIVIDUAL</div>\"",  "X:500 Y:18 W:90 H:26"),
        ("HTML text\nhtmlVerdict",        "HtmlText",    "\"<div style='background:\" &\nSwitch(ThisItem.overall_verdict,\"PASS\",\"#dcfce7\",\"FAIL\",\"#fee2e2\",\"#f0f0f0\") &\n\";color:\" & Switch(ThisItem.overall_verdict,\"PASS\",\"#166534\",\"FAIL\",\"#991b1b\",\"#374151\") &\n\";font-size:11px;font-weight:bold;border-radius:4px;\" &\n\"padding:4px 6px;text-align:center;font-family:sans-serif'>\" &\nThisItem.overall_verdict & \"</div>\"", "X:600 Y:18 W:90 H:26"),
        ("Text label\nlblScore",          "Text",        "If(IsBlank(compliance_score), \"-\", Text(compliance_score))",                         "X:700 Y:20 W:60 H:25 Size:14 Bold\nSwitch colour"),
        ("Text label\nlblNarrative",      "Text",        "Left(ThisItem.narrative, 35) & \"...\"",                                               "X:770 Y:20 W:280 H:25 Size:12 colTextSec"),
        ("Rectangle\nrectRowDivider",     "Fill",        "colBorder",                                                                            "X:0 Y:59 W:Parent.Width H:1"),
    ]:
        _table_row(ctrl_tbl, list(r))

    doc.add_paragraph()
    _para(doc, "Score label colour formula:", bold=True)
    _code(doc, "If(Value(ThisItem.compliance_score) >= 8, colPass,")
    _code(doc, "   If(Value(ThisItem.compliance_score) >= 6, colWarn, colFail))")

    doc.add_paragraph()
    _para(doc, "htmlType HtmlText formula (full):", bold=True)
    _code(doc, '"<div style=\'background:rgba(219,234,254,1);color:rgba(29,78,216,1);')
    _code(doc, "font-size:11px;font-weight:bold;border-radius:4px;")
    _code(doc, "padding:4px 6px;text-align:center;font-family:sans-serif'>\"")
    _code(doc, "& \"INDIVIDUAL</div>\"")

    doc.add_paragraph()
    _para(doc, "htmlVerdict HtmlText formula (full):", bold=True)
    _code(doc, '"<div style=\'background:" &')
    _code(doc, '    Switch(ThisItem.overall_verdict,')
    _code(doc, '        "PASS", "#dcfce7",')
    _code(doc, '        "FAIL", "#fee2e2",')
    _code(doc, '        "#f0f0f0") &')
    _code(doc, '    ";color:" &')
    _code(doc, '    Switch(ThisItem.overall_verdict,')
    _code(doc, '        "PASS", "#166534",')
    _code(doc, '        "FAIL", "#991b1b",')
    _code(doc, '        "#374151") &')
    _code(doc, '    ";font-size:11px;font-weight:bold;border-radius:4px;')
    _code(doc, "    padding:4px 6px;text-align:center;font-family:sans-serif'>\"")
    _code(doc, "& ThisItem.overall_verdict & \"</div>\"")

    # ── Part 10 ───────────────────────────────────────────────────────────────
    doc.add_page_break()
    _heading(doc, "Part 10 — Verdict Filter Dropdowns", level=1)

    _numbered(doc, "Insert -> Dropdown,  rename: drpVerdictFilter")
    _bullet(doc, 'Items: ["All verdicts", "PASS", "WARN", "FAIL"]')
    _bullet(doc, "X: 950,  Y: 368,  Width: 150,  Height: 32")

    _numbered(doc, "Insert -> Dropdown,  rename: drpSortFilter")
    _bullet(doc, 'Items: ["Newest", "Oldest"]')
    _bullet(doc, "X: 1110,  Y: 368,  Width: 130,  Height: 32")

    _numbered(doc, "Update the gallery Items formula (see Part 9 Step 3 above) — it already references these dropdowns")

    # ── Part 11 ───────────────────────────────────────────────────────────────
    _heading(doc, "Part 11 — Row Expand (Narrative Detail Panel)", level=1)

    _para(doc, "Click Edit on the gallery to enter template mode.")

    _numbered(doc, "Update gallery TemplateSize:")
    _code(doc, "If(varHistoryExpandedRow = ThisItem.id, 320, 60)")

    _numbered(doc, "Update gallery OnSelect:")
    _code(doc, "Set(varHistoryExpandedRow,")
    _code(doc, "    If(varHistoryExpandedRow = ThisItem.id, \"\", ThisItem.id))")

    _numbered(doc, "Insert -> Rectangle  (expand background)  — rename: rectExpandBg")
    _bullet(doc, "Visible: varHistoryExpandedRow = ThisItem.id")
    _bullet(doc, "X: 0,  Y: 61,  Width: Parent.Width,  Height: 258")
    _bullet(doc, "Fill: colBg,  BorderColor: colBorder,  BorderThickness: 1")

    _numbered(doc, "Insert -> Rectangle  (left accent bar)  — rename: rectExpandAccent")
    _bullet(doc, "Visible: varHistoryExpandedRow = ThisItem.id")
    _bullet(doc, "X: 0,  Y: 61,  Width: 4,  Height: 258")
    _bullet(doc, "Fill: colTeal")

    _numbered(doc, "Insert -> Text label  (Original Narrative header)")
    _bullet(doc, "Visible: varHistoryExpandedRow = ThisItem.id")
    _bullet(doc, 'Text: "Original Narrative"')
    _bullet(doc, "X: 15,  Y: 70,  Width: 500,  Height: 22")
    _bullet(doc, "FontWeight: Bold,  Color: colTeal,  Size: 12")

    _numbered(doc, "Insert -> Text label  (AI Rewritten Narrative header)")
    _bullet(doc, "Visible: varHistoryExpandedRow = ThisItem.id")
    _bullet(doc, 'Text: "AI Rewritten Narrative"')
    _bullet(doc, "X: 540,  Y: 70,  Width: 500,  Height: 22")
    _bullet(doc, "FontWeight: Bold,  Color: colTeal,  Size: 12")

    _numbered(doc, "Insert -> HTML text  (original narrative viewer)  — rename: htmlHistNarrative")
    _bullet(doc, "Visible: varHistoryExpandedRow = ThisItem.id")
    _bullet(doc, "X: 15,  Y: 94,  Width: 510,  Height: 215")
    _bullet(doc, "HtmlText:")
    _code(doc, '"<div style=\'font-family:sans-serif;font-size:12px;line-height:1.6;')
    _code(doc, "color:#111827;padding:6px;background:#fff;border:1px solid #e5e7eb;")
    _code(doc, "border-radius:6px'>\"")
    _code(doc, "& If(IsBlank(ThisItem.narrative),")
    _code(doc, "     \"<i style='color:#9ca3af'>No narrative recorded</i>\",")
    _code(doc, "     ThisItem.narrative)")
    _code(doc, "& \"</div>\"")

    _numbered(doc, "Insert -> HTML text  (rewritten narrative viewer)  — rename: htmlHistRewritten")
    _bullet(doc, "Visible: varHistoryExpandedRow = ThisItem.id")
    _bullet(doc, "X: 540,  Y: 94,  Width: 510,  Height: 215")
    _bullet(doc, "HtmlText:")
    _code(doc, '"<div style=\'font-family:sans-serif;font-size:12px;line-height:1.6;')
    _code(doc, "color:#111827;padding:6px;background:#fff;border:1px solid #e5e7eb;")
    _code(doc, "border-radius:6px'>\"")
    _code(doc, "& If(IsBlank(ThisItem.rewritten_narrative),")
    _code(doc, "     \"<i style='color:#9ca3af'>No rewritten narrative available</i>\",")
    _code(doc, "     ThisItem.rewritten_narrative)")
    _code(doc, "& \"</div>\"")

    # ── Part 12 ───────────────────────────────────────────────────────────────
    _heading(doc, "Part 12 — Clear History Button", level=1)
    _para(doc, (
        "Matches the Clear History button visible in the top right of the React page. "
        "This clears the local collection only — it does not delete records from the database."
    ))

    _numbered(doc, "Insert -> Button")
    _bullet(doc, 'Text: "Clear History"')
    _bullet(doc, "X: Parent.Width - 200,  Y: 55,  Width: 170,  Height: 34")
    _bullet(doc, "Fill: White,  Color: colText")
    _bullet(doc, "BorderColor: colBorder,  BorderThickness: 1")
    _bullet(doc, "RadiusTopLeft / TopRight / BottomLeft / BottomRight: 6")
    _bullet(doc, "Size: 12")
    _numbered(doc, "Set OnSelect:")
    _code(doc, 'ClearCollect(colHistory, []); Set(varHistoryExpandedRow, "")')

    # ── Part 13 ───────────────────────────────────────────────────────────────
    _heading(doc, "Part 13 — Save and Publish", level=1)

    _numbered(doc, "Top menu -> File -> Save  (Ctrl+S)")
    _numbered(doc, "Top menu -> File -> Publish -> Publish this version")
    _numbered(doc, "Open the published app and navigate to Analytics via the sidebar")
    _numbered(doc, (
        "If colHistory is empty: run a validation from the Validate screen first, "
        "then return to Analytics"
    ))
    _numbered(doc, "Confirm: rows appear with score, verdict badge, and coloured score number")
    _numbered(doc, "Click any row to expand — original narrative and rewritten narrative should appear side by side")
    _numbered(doc, "Test the verdict filter dropdown — select FAIL, confirm only FAIL rows show")
    _numbered(doc, "Test the sort dropdown — switch between Newest and Oldest")

    _heading(doc, "What comes next after Part 13", level=2)
    _bullet(doc, "Add source column to the database to correctly split Individual vs Batch card counts")
    _bullet(doc, "Upload good_practice_guidance_v2.docx to Azure Blob Storage")
    _bullet(doc, "Re-run the eval framework with the improved guidance and review the before/after Excel report")


def main():
    doc = Document()

    # Page margins
    for section in doc.sections:
        section.top_margin    = Cm(2)
        section.bottom_margin = Cm(2)
        section.left_margin   = Cm(2.5)
        section.right_margin  = Cm(2.5)

    build(doc)
    doc.save(OUT)
    print(f"Saved -> {OUT}")


if __name__ == "__main__":
    main()
