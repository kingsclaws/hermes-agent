import base64
import importlib.util
import json
from pathlib import Path

import requests


_OCR_PATH = Path(__file__).resolve().parents[1] / "vendor" / "lexitool" / "lexitool" / "ocr.py"
_SPEC = importlib.util.spec_from_file_location("lexitool_ocr_under_test", _OCR_PATH)
ocr = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(ocr)


def _fake_pdf(tmp_path: Path) -> str:
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    return str(pdf)


def test_precise_api_401_falls_back_to_agent(monkeypatch, tmp_path):
    class LocalMinerUAPI:
        def parse_file(self, *args, **kwargs):
            return None

    class PreciseAPI:
        def __init__(self, token):
            self.token = token

        def parse_file(self, *args, **kwargs):
            raise requests.HTTPError(
                "401 Client Error: Unauthorized for url: https://mineru.net/api/v4/file-urls/batch"
            )

    class AgentAPI:
        def parse_file(self, *args, **kwargs):
            return "# OCR text"

    monkeypatch.setattr(ocr, "LocalMinerUAPI", LocalMinerUAPI)
    monkeypatch.setattr(ocr, "PreciseAPI", PreciseAPI)
    monkeypatch.setattr(ocr, "AgentAPI", AgentAPI)
    monkeypatch.setattr(ocr, "_get_token", lambda: "opaque-but-invalid")
    monkeypatch.setattr(ocr, "_check_tesseract", lambda: False)

    result = ocr.parse_pdf(_fake_pdf(tmp_path), prefer_precise=True)

    assert result["ok"] is True
    assert result["api"] == "agent"
    assert result["markdown"] == "# OCR text"
    assert "Precision API failed" in result["warnings"][0]


def test_expired_precise_token_is_skipped(monkeypatch, tmp_path):
    payload = base64.urlsafe_b64encode(json.dumps({"exp": 1}).encode()).decode().rstrip("=")
    expired_jwt = f"header.{payload}.signature"

    class LocalMinerUAPI:
        def parse_file(self, *args, **kwargs):
            return None

    class PreciseAPI:
        def __init__(self, token):
            raise AssertionError("expired token should not call Precision API")

    class AgentAPI:
        def parse_file(self, *args, **kwargs):
            return "# fallback OCR text"

    monkeypatch.setattr(ocr, "LocalMinerUAPI", LocalMinerUAPI)
    monkeypatch.setattr(ocr, "PreciseAPI", PreciseAPI)
    monkeypatch.setattr(ocr, "AgentAPI", AgentAPI)
    monkeypatch.setattr(ocr, "_get_token", lambda: expired_jwt)
    monkeypatch.setattr(ocr, "_check_tesseract", lambda: False)

    result = ocr.parse_pdf(_fake_pdf(tmp_path), prefer_precise=True)

    assert result["ok"] is True
    assert result["api"] == "agent"
    assert "token appears expired" in result["warnings"][0]


def test_parse_gradio_sse_data_events():
    text = (
        'event: generating\n'
        'data: ["<p>working</p>", null, "", "", "[]", null]\n\n'
        'event: complete\n'
        'data: ["<p>done</p>", null, "", "# OCR text", "[]", null]\n\n'
        'data: [DONE]\n\n'
    )

    events = ocr._parse_sse_data_events(text)

    assert events == [
        ["<p>working</p>", None, "", "", "[]", None],
        ["<p>done</p>", None, "", "# OCR text", "[]", None],
    ]


def test_markdown_from_gradio_content_list_preserves_pages():
    content_list = json.dumps([
        {"type": "text", "page_idx": 0, "text": "第一页第一段"},
        {"type": "text", "page_idx": 0, "text": "第一页第二段"},
        {"type": "text", "page_idx": 1, "text": "第二页"},
    ])

    markdown = ocr._markdown_from_content_list(content_list)

    assert markdown == "<!-- page 1 -->\n第一页第一段\n第一页第二段\n\n<!-- page 2 -->\n第二页"
