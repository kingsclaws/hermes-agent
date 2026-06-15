import shutil
import sys
import zipfile
from pathlib import Path

from docx import Document
from lxml import etree

sys.path.insert(0, str((Path(__file__).resolve().parents[1] / "vendor" / "lexitool").resolve()))

from lexitool.diff import summary
from lexitool.edit_ops import replace_text
from lexitool.markup import lex_read
from hermes_cli.project_commands import (
    edit_verification_record,
    legal_handoff_record,
    legal_harness_migrate,
    legal_harness_workflow,
    legal_review_plan,
    legal_scorecard,
    lex_convention_profile,
)
from tools.lexitool_tool import _handle_edit, _handle_revision_guard, _handle_scan


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


def test_direct_replace_preserves_unmatched_runs_when_match_crosses_runs(tmp_path):
    path = tmp_path / "share-mortgage.docx"
    doc = Document()
    p = doc.add_paragraph()
    p.add_run("The ")
    p.add_run("Security").bold = True
    p.add_run(" Trustee").italic = True
    p.add_run(" may act.")
    doc.save(path)

    result = replace_text(str(path), 0, "Security Trustee", "Chargee", tc=False)

    assert result.ok is True

    with zipfile.ZipFile(path, "r") as zf:
        root = etree.fromstring(zf.read("word/document.xml"))
    para = next(root.iter(f"{W}p"))
    text_nodes = list(para.iter(f"{W}t"))
    texts = [t.text or "" for t in text_nodes]

    assert "".join(texts) == "The Chargee may act."
    assert texts == ["The ", "Chargee", "", " may act."]
    assert para.find(f".//{W}b") is not None
    assert para.find(f".//{W}i") is not None


def test_revision_guard_blocks_unresolved_final_view_residuals(tmp_path):
    path = tmp_path / "share-mortgage.docx"
    doc = Document()
    doc.add_paragraph("“Secured Assets” means assets created in favour of the Security  Trustee.")
    doc.add_paragraph("The Chargee may enforce the Security.")
    doc.save(path)

    scan = _handle_scan({"path": str(path), "query": "Security Trustee"})
    assert '"total_matches": 1' in scan
    assert '"paragraph_targets": [1]' in scan

    failed = _handle_revision_guard(
        {
            "path": str(path),
            "required_absent": ["Security Trustee"],
            "required_present": ["Chargee"],
        }
    )
    assert '"ok": false' in failed
    assert '"failure_count": 1' in failed
    assert '"paragraph_targets": [1]' in failed

    passed = _handle_revision_guard(
        {
            "path": str(path),
            "required_absent": ["Security Agent"],
            "required_present": ["Chargee"],
        }
    )
    assert '"ok": true' in passed
    assert '"failure_count": 0' in passed


def test_legal_harness_primitives_persist_project_state(tmp_path):
    project = tmp_path / "matter"
    hp = project / ".hermes-project"
    hp.mkdir(parents=True)
    (hp / "project-meta.json").write_text(
        '{"name":"测试项目","client":"客户","goal":"测试"}',
        encoding="utf-8",
    )
    doc_path = project / "support.docx"
    doc = Document()
    doc.add_paragraph("支持函")
    doc.add_paragraph("一、本公司承诺")
    doc.add_paragraph("本函不构成担保或债务承担，但本公司承诺提供流动性支持。")
    doc.save(doc_path)

    migrated = legal_harness_migrate(project_dirs=[str(project)])
    profile = lex_convention_profile(str(doc_path), project_dir=str(project))
    plan = legal_review_plan(str(doc_path), project_dir=str(project))
    record = edit_verification_record(
        str(project),
        str(doc_path),
        target="§3",
        edit_summary="确认非担保表述和支持义务并存",
        before_text="本函不构成担保。",
        after_text="本函不构成担保或债务承担，但本公司承诺提供流动性支持。",
        checks=["lex_read readback", "liability/security", "obligations/support"],
        status="passed",
    )

    assert migrated["migrated_count"] == 1
    assert profile["ok"] is True
    assert profile["profile"]["xref_convention"]["summary"] in {"unknown", "第X条"}
    assert plan["ok"] is True
    assert plan["plan"]["steps"]
    assert record["ok"] is True
    assert (hp / "convention-profiles.json").exists()
    assert (hp / "legal-review-plans.json").exists()
    assert (hp / "edit-verification-records.json").exists()
    assert "Edit verification passed" in (hp / "project-context.md").read_text(encoding="utf-8")


def test_legal_harness_workflows_handoffs_and_scorecard(tmp_path):
    project = tmp_path / "matter"
    hp = project / ".hermes-project"
    hp.mkdir(parents=True)
    (hp / "project-meta.json").write_text('{"name":"测试项目"}', encoding="utf-8")
    doc_path = project / "support.docx"
    doc = Document()
    doc.add_paragraph("支持函")
    doc.add_paragraph("一、本公司承诺")
    doc.add_paragraph("本函不构成担保或债务承担，但本公司承诺提供流动性支持。")
    doc.save(doc_path)

    migrated = legal_harness_migrate(project_dirs=[str(project)])
    workflows = legal_harness_workflow(str(project))
    workflow = legal_harness_workflow(str(project), action="get", workflow_id="contract_revision")

    assert migrated["ok"] is True
    assert (hp / "workflows" / "contract_revision.yaml").exists()
    assert workflows["count"] >= 4
    assert workflow["workflow"]["nodes"]

    failed_handoff = legal_handoff_record(
        str(project),
        workflow_id="contract_revision",
        node_id="revise",
        handoff={"status": "completed", "evidence": {}},
    )
    assert failed_handoff["ok"] is False
    assert "modified_files" in failed_handoff["error"]

    good_handoff = legal_handoff_record(
        str(project),
        workflow_id="contract_revision",
        node_id="revise",
        handoff={
            "status": "completed",
            "modified_files": [str(doc_path)],
            "modified_locations": ["§3"],
            "guards": {"revision_guard": {"ok": True}},
            "evidence": {"readback": "§3 checked"},
        },
    )
    assert good_handoff["ok"] is True
    run_id = good_handoff["record"]["run_id"]
    assert (hp / "harness-runs" / run_id / "nodes" / "revise.json").exists()

    failing_scorecard = legal_scorecard(
        str(project),
        document_path=str(doc_path),
        workflow_id="contract_revision",
        run_id=run_id,
    )
    assert failing_scorecard["ok"] is False
    assert any(item["check"] == "convention_profile" for item in failing_scorecard["failures"])

    lex_convention_profile(str(doc_path), project_dir=str(project))
    legal_review_plan(str(doc_path), project_dir=str(project))
    edit_verification_record(
        str(project),
        str(doc_path),
        target="§3",
        edit_summary="确认支持义务",
        before_text="本函不构成担保。",
        after_text="本函不构成担保或债务承担，但本公司承诺提供流动性支持。",
        checks=["lex_read readback", "lex_revision_guard"],
        status="passed",
    )
    passing_scorecard = legal_scorecard(
        str(project),
        document_path=str(doc_path),
        workflow_id="contract_revision",
        run_id=run_id,
    )
    assert passing_scorecard["ok"] is True
    assert passing_scorecard["status"] == "passed"


def test_legal_harness_tools_are_exposed_in_lex_toolsets():
    from toolsets import resolve_toolset

    lex_tools = set(resolve_toolset("lexitool"))
    coordinator_tools = set(resolve_toolset("lex-docx-coordinator"))
    worker_tools = set(resolve_toolset("lex-docx-worker"))

    for name in {"legal_harness_workflow", "legal_handoff_record", "legal_scorecard"}:
        assert name in lex_tools
        assert name in coordinator_tools
        assert name in worker_tools
