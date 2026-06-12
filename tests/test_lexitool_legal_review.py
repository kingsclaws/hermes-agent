import shutil
import sys
import zipfile
from pathlib import Path

from docx import Document
from lxml import etree

sys.path.insert(0, str((Path(__file__).resolve().parents[1] / "vendor" / "lexitool").resolve()))

from lexitool.diff import summary
from lexitool.markup import lex_read
from tools.lexitool_tool import _handle_edit


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = f"{{{W_NS}}}"


def _inject_tracked_change(path: Path, paragraph_index: int, inserted: str, deleted: str = "") -> None:
    tmp = path.with_suffix(".tmp.docx")
    with zipfile.ZipFile(path, "r") as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        doc_xml = zin.read("word/document.xml")
        root = etree.fromstring(doc_xml)
        paras = list(root.iter(f"{W}p"))
        para = paras[paragraph_index]

        if deleted:
            del_el = etree.Element(f"{W}del")
            del_el.set(f"{W}id", "42")
            del_el.set(f"{W}author", "JT")
            run = etree.SubElement(del_el, f"{W}r")
            t = etree.SubElement(run, f"{W}delText")
            t.text = deleted
            para.append(del_el)

        ins_el = etree.Element(f"{W}ins")
        ins_el.set(f"{W}id", "43")
        ins_el.set(f"{W}author", "JT")
        run = etree.SubElement(ins_el, f"{W}r")
        t = etree.SubElement(run, f"{W}t")
        t.text = inserted
        para.append(ins_el)

        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "word/document.xml":
                data = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
            zout.writestr(item, data)
    shutil.move(tmp, path)


def test_legal_structure_detects_chinese_legal_headings(tmp_path):
    path = tmp_path / "support.docx"
    doc = Document()
    doc.add_paragraph("支持函")
    doc.add_paragraph("一、本公司承诺")
    doc.add_paragraph("本公司将维持对项目运营主体的控制权。")
    doc.add_paragraph("二、本函义务的履行")
    doc.save(path)

    outline = lex_read(str(path), mode="legal_structure")

    assert "§2 [L1] 一、本公司承诺" in outline
    assert "§4 [L1] 二、本函义务的履行" in outline


def test_review_digest_reports_tc_hotspots_and_next_reads(tmp_path):
    path = tmp_path / "review.docx"
    doc = Document()
    doc.add_paragraph("一、本公司承诺")
    doc.add_paragraph("本公司对项目运营主体提供支持。")
    doc.save(path)
    _inject_tracked_change(path, 1, "本公司对此承担不可撤销的流动性支持义务。", "本公司不承担支持义务。")

    digest = lex_read(str(path), mode="review")

    assert "[legal-review-digest]" in digest
    assert "Tracked-change paragraphs: 1" in digest
    assert "obligations/support" in digest
    assert "lex_read paras=[2]" in digest


def test_diff_summary_classifies_legal_revision_intent(tmp_path):
    original = tmp_path / "original.docx"
    revised = tmp_path / "revised.docx"
    doc = Document()
    doc.add_paragraph("本函不构成担保和债务承担。")
    doc.save(original)
    doc = Document()
    doc.add_paragraph("本函不构成担保或债务承担，但不影响本公司按照本函承诺对项目运营主体提供支持。")
    doc.save(revised)

    result = summary(str(original), str(revised))

    assert result["ok"] is True
    assert result["changes_total"] == 1
    assert "liability/security" in result["changes"][0]["categories"]
    assert "obligations/support" in result["changes"][0]["categories"]


def test_replace_all_requires_confirmation_for_high_risk_bulk_legal_edits(tmp_path):
    path = tmp_path / "bulk.docx"
    doc = Document()
    for i in range(10):
        doc.add_paragraph(f"本公司第{i}项承诺。")
    doc.save(path)

    result = _handle_edit({
        "path": str(path),
        "op": "replace_all",
        "targets": list(range(1, 10)),
        "old_text": "本",
        "new_text": "该",
    })

    assert "LEGAL_BULK_REPLACE_REVIEW_REQUIRED" in result
