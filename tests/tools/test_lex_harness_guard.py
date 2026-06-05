from tools.legal_ocr_guard import lex_harness_guard_reason


def test_blocks_lex_cli_and_python_imports():
    for text in (
        "lex-ocr /workingfile/a.pdf",
        'lex_read "a.docx" mode=structure',
        "python3 -c 'from lexitool.markup import lex_read'",
        "import lexitool",
    ):
        assert lex_harness_guard_reason(text)[0] == "native_lex_tool_required"


def test_blocks_generic_ocr_before_lex_ocr():
    assert lex_harness_guard_reason("import pymupdf")[0] == "lex_ocr_required"
    assert lex_harness_guard_reason("marker_single a.pdf")[0] == "lex_ocr_required"


def test_allows_native_lex_ocr_mentions():
    assert lex_harness_guard_reason("call lex_ocr for this legal scan") is None


def test_blocks_manual_project_db_writes():
    text = "python3 -c \"import sqlite3; sqlite3.connect('/root/.hermes/state.db').execute('INSERT INTO projects VALUES (1)')\""
    assert lex_harness_guard_reason(text)[0] == "native_project_tool_required"
