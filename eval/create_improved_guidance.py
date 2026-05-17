"""
eval/create_improved_guidance.py

Creates NDA Data/good_practice_guidance_v2.docx — an improved copy of the current
good_practice_guidance.docx with all changes identified from the baseline eval run.

Changes made vs v1:
  1. Added explicit Project Description as Sentence 1
  2. DCA RAG — added explicit rule: must use "remains as" / "has changed to", NOT "DCA is"
  3. Project Benefit — broadened from milestone-only to any project benefit statement
  4. Added explicit Baseline Movement as Sentence 9
  5. Highlights/Issues — strengthened with mandatory stem and FN examples
  6. Added Scoring Criteria section defining PASS/WARN/FAIL thresholds

Run:
    cd eval
    pip install python-docx
    python create_improved_guidance.py
"""

import pathlib

from docx import Document
from docx.shared import Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH

HERE      = pathlib.Path(__file__).parent
ROOT      = HERE.parent
NDA_DIR   = ROOT / "NDA Data"
SRC_PATH  = NDA_DIR / "good_practice_guidance.docx"
DEST_PATH = NDA_DIR / "good_practice_guidance_v2.docx"


def _heading(doc, text: str, level: int):
    p = doc.add_heading(text, level=level)
    return p


def _para(doc, text: str, bold_prefix: str = "", indent: bool = False):
    p = doc.add_paragraph()
    if indent:
        p.paragraph_format.left_indent = Pt(18)
    if bold_prefix:
        run = p.add_run(bold_prefix)
        run.bold = True
        p.add_run(" " + text)
    else:
        p.add_run(text)
    return p


def _bullet(doc, text: str, bold_prefix: str = ""):
    p = doc.add_paragraph(style="List Bullet")
    if bold_prefix:
        run = p.add_run(bold_prefix)
        run.bold = True
        p.add_run(" " + text)
    else:
        p.add_run(text)
    return p


def _numbered(doc, text: str, bold_prefix: str = ""):
    p = doc.add_paragraph(style="List Number")
    if bold_prefix:
        run = p.add_run(bold_prefix)
        run.bold = True
        p.add_run(" " + text)
    else:
        p.add_run(text)
    return p


def build_document(doc: Document):
    # ── Title ─────────────────────────────────────────────────────────────────
    _heading(doc, "NDA Narrative Good Practice Guidelines", level=2)
    _para(doc, (
        "When writing or validating an SRO project narrative, the following structure and "
        "rules apply. Each criterion listed below is an independent check — not all criteria "
        "apply to every project. Where a criterion is not applicable (e.g., no P50 cost "
        "movement in period), it should be omitted rather than forced."
    ))

    # ── Section 1: Sentence Templates ─────────────────────────────────────────
    _heading(doc, "Required Sentence Templates (in order)", level=3)
    _para(doc, (
        "The narrative must cover the applicable sentences below in order. "
        "Inapplicable sentences may be omitted. Each sentence is also a scoring criterion."
    ))

    sentences = [
        (
            "1. Project Description",
            '"This project is delivering [brief description of the project scope and purpose]."',
            (
                "The narrative must open with a clear statement of what the project does. "
                "This sentence must describe the project's scope and purpose in plain English "
                "appropriate for an external/executive audience. "
                "IMPORTANT: This criterion is a PASS if the narrative contains any description "
                "of the project's purpose; it is a FAIL only if the narrative has NO description "
                "whatsoever of what the project is about."
            ),
        ),
        (
            "2. DCA RAG",
            '"The SRO / SPA Delivery Confidence Assessment (DCA) remains as [GREEN/AMBER/RED] '
            'because [reason]." OR "The SRO / SPA DCA has changed to [GREEN/AMBER/RED] because [reason]."',
            (
                "IMPORTANT RULE: The DCA sentence MUST use the phrasing 'remains as' or 'has changed to'. "
                "Writing 'DCA is GREEN' or 'DCA status is AMBER' without 'remains as' or 'has changed to' "
                "is a failure of this criterion. DCA must be expanded to 'Delivery Confidence Assessment (DCA)' "
                "on first use."
            ),
        ),
        (
            "3. Project Benefit",
            '"The [first project benefit milestone / project benefits are] [protected / at risk / on track] '
            '[because reason / no further detail required]."',
            (
                "This criterion is met if the narrative contains ANY statement about the status of "
                "project benefits — not just a milestone statement. Examples that satisfy this criterion: "
                "'project benefits remain on track', 'the first benefit milestone is protected', "
                "'benefits are expected to be delivered on time'. "
                "This criterion is ONLY a FAIL if there is absolutely no mention of benefits or benefit milestones."
            ),
        ),
        (
            "4. P50 Completion Cost",
            '"P50 completion cost has [increased by £Xm / decreased by £Xm / been maintained] '
            'in period as a result of [reason]."',
            (
                "Required when there is P50 cost movement in the reporting period. "
                "The cost figure must exactly match the EAC data from the reporting data. "
                "If there is no cost movement (P50 remains unchanged), this sentence may be omitted."
            ),
        ),
        (
            "5. Action Being Taken",
            '"Action is being taken [describe action being taken to address issues or maintain position]."',
            None,
        ),
        (
            "6. P50 Schedule Position",
            '"P50 schedule position has [improved / deteriorated / been maintained] in period due to [reason]."',
            (
                "Required when there is schedule movement in the reporting period. "
                "If schedule is unchanged, this sentence may be omitted."
            ),
        ),
        (
            "7. Implications to Contingency, Risk and Resources",
            '"The implications of this to contingency, risk and resources are [explain]. '
            'Action being taken [describe] with the following opportunities being pursued [describe]."',
            None,
        ),
        (
            "8. Baseline RAG",
            '"The Baseline RAG status against SL P50 Project Baseline is [GREEN/AMBER/RED] due to [reason]; '
            'additionally (if applicable) this will change when [condition]."',
            (
                "The Baseline RAG sentence must use the stem 'Baseline RAG status against SL P50 Project Baseline is'. "
                "This is a distinct criterion from Baseline Movement (sentence 9 below)."
            ),
        ),
        (
            "9. Baseline Movement",
            '"The P50 baseline completion cost [/ schedule] has [remained unchanged / increased by £Xm / '
            'moved by X months] since [last period / project baseline]."',
            (
                "This sentence is required when there has been movement against the SL P50 project baseline. "
                "It is distinct from the current-period P50 cost movement (sentence 4). "
                "If the baseline has not moved, this sentence may be omitted — it is then N/A, not a failure. "
                "This criterion is a FAIL only if the baseline has moved and the narrative does not mention it."
            ),
        ),
        (
            "10. Highlights / Issues",
            '"Highlights / issues in period: [describe key highlights or issues, separated by semicolons]."',
            (
                "This sentence MUST begin with the stem 'Highlights / issues in period:' (or 'Highlights and issues in period:'). "
                "Any sentence describing highlights or issues that does not use this stem is a failure of this criterion. "
                "This criterion is a FAIL if there are highlights or issues to report but none are stated, "
                "OR if the section exists but does not use the mandatory stem."
            ),
        ),
        (
            "11. Capability & Capacity (Cap/Cap) RAG",
            '"Capability & Capacity RAG status is [GREEN/AMBER/RED] due to [reason]."',
            (
                "Cap/Cap must be expanded to 'Capability & Capacity' on first use. "
                "Required when the project has a Cap/Cap RAG status to report."
            ),
        ),
        (
            "12. Acronym Rule — Standard Acronyms",
            "Standard acronyms listed in the data file row (DCA, EAC, RAG, P50, P80, etc.) do not need to be expanded.",
            None,
        ),
        (
            "13. Acronym Rule — Repeated Acronyms",
            "Repeated acronyms only need to be expanded in the first sentence and not continually through the paragraph.",
            None,
        ),
    ]

    for label, template, note in sentences:
        p = doc.add_paragraph()
        run = p.add_run(label + ": ")
        run.bold = True
        p.add_run(template)
        if note:
            np = doc.add_paragraph()
            np.paragraph_format.left_indent = Pt(24)
            run2 = np.add_run("Validation note: ")
            run2.bold = True
            run2.font.color.rgb = RGBColor(0x1F, 0x38, 0x64)
            np.add_run(note)

    # ── Section 2: Mandatory Formatting Rules ─────────────────────────────────
    _heading(doc, "Mandatory Formatting Rules", level=3)

    formatting_rules = [
        ("Flowing paragraph", "Narrative must read as a flowing paragraph — NOT in bullet points."),
        ("Acronyms on first use", "All project-specific acronyms must be expanded on first use (e.g., 'Delivery Confidence Assessment (DCA)'). Standard acronyms per the data file (DCA, EAC, RAG, P50, P80) do not need expansion."),
        ("Full dates", "All dates must be written in full (e.g., '15th May 2024', NOT 'May-24' or '05/24' or 'Q1 26/27')."),
        ("No building numbers", "Do not include building numbers (e.g., avoid 'Building 204')."),
        ("No document reference numbers", "Do not include internal document reference numbers."),
        ("Exact cost figures", "Any cost figures in the narrative must exactly match the figures from the reporting data."),
        ("External audience", "The narrative must be appropriate for an external/executive audience who may not know project detail."),
        ("GMPP alignment", "Where the project is part of GMPP, alignment must be maintained between messaging and RAGs."),
        ("Official level", "Comments must be at an 'official' level of security — no sensitive operational detail."),
    ]

    for title, rule in formatting_rules:
        _bullet(doc, rule, bold_prefix=title + ":")

    # ── Section 3: Validation Scoring Criteria ─────────────────────────────────
    _heading(doc, "Validation Scoring Criteria", level=3)
    _para(doc, (
        "Each narrative is assessed against the following 9 criteria. Each criterion is scored "
        "independently. The overall compliance score and verdict are determined as follows:"
    ))

    criteria_list = [
        ("1", "Project Description", "Narrative contains a statement of the project's scope and purpose."),
        ("2", "DCA RAG", "DCA sentence uses 'remains as' or 'has changed to' phrasing with a reason."),
        ("3", "Project Benefit", "Narrative contains any statement about the status of project benefits or benefit milestones."),
        ("4", "Completion Cost (P50/P80)", "P50 cost movement stated with correct figures where applicable."),
        ("5", "Schedule Position", "P50 schedule position stated where applicable."),
        ("6", "Baseline RAG", "Baseline RAG sentence uses the mandatory stem."),
        ("7", "Baseline Movement", "Baseline movement stated where the P50 baseline has changed."),
        ("8", "Highlights / Issues", "Highlights/issues section uses the mandatory stem where highlights/issues exist."),
        ("9", "Capability & Capacity RAG", "Cap/Cap RAG status stated where applicable."),
    ]

    for num, name, desc in criteria_list:
        p = doc.add_paragraph(style="List Number")
        run = p.add_run(name + ": ")
        run.bold = True
        p.add_run(desc)

    doc.add_paragraph()
    _para(doc, "Scoring thresholds (compliance score out of 10):", bold_prefix="")
    scoring = [
        ("PASS", "≥ 8 out of 10 — narrative meets all applicable criteria."),
        ("PASS WITH WARNINGS", "6–7 out of 10 — narrative meets most criteria with minor gaps."),
        ("FAIL", "< 6 out of 10 — narrative has significant structural or content gaps."),
    ]
    for verdict, desc in scoring:
        _bullet(doc, desc, bold_prefix=verdict + ":")

    _para(doc, (
        "IMPORTANT: Criteria that are N/A for a given project (e.g., no cost movement in period, "
        "no schedule movement) are excluded from the denominator when calculating the compliance score. "
        "Do NOT penalise a narrative for omitting a sentence that genuinely does not apply."
    ))


def main():
    if not SRC_PATH.exists():
        print(f"ERROR: Source not found: {SRC_PATH}")
        return

    # Use a fresh document so all built-in styles (List Bullet, List Number) are available
    doc = Document()
    build_document(doc)
    doc.save(DEST_PATH)
    print(f"Saved -> {DEST_PATH}")


if __name__ == "__main__":
    main()