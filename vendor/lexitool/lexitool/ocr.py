"""
ocr.py — MinerU PDF OCR client for lex-hermes.

Three API tiers (tried in order):
  - LocalMinerU: self-hosted MinerU instance (MINERU_LOCAL_URL), no token
  - PreciseAPI: token required (MINERU_API_KEY), VLM/pipeline models
  - AgentAPI: free, no token required, suitable for AI agent use

Fallback: local Tesseract OCR (if tesseract + pdftoppm are installed).
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import tempfile
import time
import requests


AGENT_API_BASE = "https://mineru.net/api/v1/agent"
PRECISE_API_BASE = "https://mineru.net/api/v4"
_DEFAULT_TIMEOUT = 300
_DEFAULT_INTERVAL = 3


def _get_token() -> str | None:
    return (
        os.environ.get("MINERU_API_KEY")
        or os.environ.get("MINERU_TOKEN")
        or os.environ.get("MINERU_API_TOKEN")
    )


def _safe_error(exc: Exception) -> str:
    """Return a concise error string without exposing request headers/tokens."""
    message = str(exc).strip()
    if not message:
        return exc.__class__.__name__
    return message.replace("\n", " ")[:500]


def _token_appears_expired(token: str) -> bool:
    """Best-effort JWT expiry check. Opaque tokens are treated as unknown."""
    parts = token.split(".")
    if len(parts) < 2:
        return False
    payload = parts[1] + ("=" * (-len(parts[1]) % 4))
    try:
        data = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
    except Exception:
        return False
    exp = data.get("exp")
    return isinstance(exp, (int, float)) and exp <= time.time() + 60


def _poll(url: str, headers: dict, field: str = "state",
          timeout: int = _DEFAULT_TIMEOUT, interval: int = _DEFAULT_INTERVAL):
    """Poll MinerU task until done/failed/timeout. Returns (state, data)."""
    start = time.time()
    while True:
        elapsed = int(time.time() - start)
        r = requests.get(url, headers=headers, timeout=30)
        result = r.json()
        data = result.get("data", {})
        state = data.get(field, "?")
        if state in ("done", "success"):
            return "done", data
        elif state in ("failed", "error"):
            return "failed", data
        elif elapsed >= timeout:
            return "timeout", data
        time.sleep(interval)


# ── Local MinerU API (self-hosted, no token) ─────────────────────────────────

LOCAL_MINERU_URL = os.environ.get("MINERU_LOCAL_URL", "http://192.168.11.5:9987")


class LocalMinerUAPI:
    """Self-hosted MinerU FastAPI instance — synchronous, no polling needed."""

    def __init__(self, base_url: str = None):
        self.base_url = (base_url or LOCAL_MINERU_URL).rstrip("/")

    def parse_file(self, file_path: str, language: str = "ch",
                   page_range: str = None, timeout: int = _DEFAULT_TIMEOUT) -> str | None:
        """POST file to /file_parse, return markdown text or None."""
        start_page = 0
        end_page = 99999
        if page_range:
            parts = page_range.split("-")
            start_page = int(parts[0]) - 1  # local API uses 0-based
            end_page = int(parts[-1]) - 1

        try:
            with open(file_path, "rb") as f:
                r = requests.post(
                    f"{self.base_url}/file_parse",
                    files={"files": (os.path.basename(file_path), f)},
                    data={
                        "lang_list": language,
                        "start_page_id": str(start_page),
                        "end_page_id": str(end_page),
                        "return_md": "true",
                        "return_images": "false",
                        "return_content_list": "false",
                    },
                    timeout=timeout,
                )
            r.raise_for_status()
            result = r.json()
            results = result.get("results", {})
            for filename, data in results.items():
                md = data.get("md_content", "")
                if md:
                    return md
            return None
        except Exception:
            return None


# ── Agent API (free, no token) ──────────────────────────────────────────────

class AgentAPI:
    """Lightweight MinerU Agent API — no token required."""

    def __init__(self):
        self.headers = {"Content-Type": "application/json"}

    def submit_file(self, file_path: str, language: str = "ch",
                    page_range: str = None) -> str:
        """Upload local file, return task_id."""
        file_name = os.path.basename(file_path)
        data = {"file_name": file_name, "language": language}
        if page_range:
            data["page_range"] = page_range

        r1 = requests.post(f"{AGENT_API_BASE}/parse/file",
                           headers=self.headers, json=data, timeout=30)
        r1.raise_for_status()
        resp = r1.json()
        if resp.get("code") != 0:
            raise RuntimeError(f"Agent API submit failed [{resp.get('code')}]: {resp.get('msg')}")
        task_id = resp["data"]["task_id"]
        upload_url = resp["data"]["file_url"]

        with open(file_path, "rb") as f:
            r2 = requests.put(upload_url, data=f, timeout=60)
        r2.raise_for_status()
        return task_id

    def poll_result(self, task_id: str, timeout: int = _DEFAULT_TIMEOUT) -> dict:
        state, data = _poll(f"{AGENT_API_BASE}/parse/{task_id}",
                            self.headers, field="state", timeout=timeout)
        return {"state": state, "data": data}

    def parse_file(self, file_path: str, language: str = "ch",
                   page_range: str = None, timeout: int = _DEFAULT_TIMEOUT) -> str | None:
        """Upload file + poll + download markdown. Returns markdown text or None."""
        task_id = self.submit_file(file_path, language=language, page_range=page_range)
        result = self.poll_result(task_id, timeout=timeout)
        if result["state"] == "done":
            markdown_url = result["data"].get("markdown_url")
            if markdown_url:
                md_resp = requests.get(markdown_url, timeout=30)
                md_resp.raise_for_status()
                return md_resp.text
        return None


# ── Precise API (token required) ────────────────────────────────────────────

class PreciseAPI:
    """Precision MinerU API — requires MINERU_API_KEY token."""

    def __init__(self, token: str):
        self.token = token
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }

    def submit_file(self, file_path: str, model_version: str = "vlm",
                    language: str = "ch", enable_formula: bool = True,
                    enable_table: bool = True, page_ranges: str = None,
                    is_ocr: bool = False) -> str:
        """Upload local file, return batch_id (auto-submits parse task)."""
        file_name = os.path.basename(file_path)
        r1 = requests.post(
            f"{PRECISE_API_BASE}/file-urls/batch",
            headers=self.headers,
            json={
                "files": [{"name": file_name}],
                "model_version": model_version,
                "enable_formula": enable_formula,
                "enable_table": enable_table,
                "is_ocr": is_ocr,
                "language": language,
            },
            timeout=30,
        )
        r1.raise_for_status()
        resp = r1.json()
        if resp.get("code") != 0:
            raise RuntimeError(f"Precise API submit failed [{resp.get('code')}]: {resp.get('msg')}")
        batch_id = resp["data"]["batch_id"]
        upload_url = resp["data"]["file_urls"][0]

        with open(file_path, "rb") as f:
            r2 = requests.put(upload_url, data=f, timeout=60)
        r2.raise_for_status()
        return batch_id

    def poll_result(self, batch_id: str, timeout: int = _DEFAULT_TIMEOUT) -> dict:
        """Poll batch task. Returns {"state": "done"|"failed", "data": {...}}."""
        url = f"{PRECISE_API_BASE}/extract-results/batch/{batch_id}"
        start = time.time()
        while True:
            elapsed = int(time.time() - start)
            r = requests.get(url, headers=self.headers, timeout=30)
            result = r.json()
            items = result.get("data", {}).get("extract_result", [])
            if items:
                item = items[0]
                s = item.get("state", "?")
                if s == "done":
                    return {"state": "done", "data": item}
                elif s == "failed":
                    return {"state": "failed", "data": item}
            if elapsed >= timeout:
                return {"state": "timeout", "data": items[0] if items else {}}
            time.sleep(_DEFAULT_INTERVAL)

    def parse_file(self, file_path: str, model_version: str = "vlm",
                   language: str = "ch", page_ranges: str = None,
                   timeout: int = _DEFAULT_TIMEOUT) -> str | None:
        """Submit + poll + download markdown. Returns markdown text or None."""
        batch_id = self.submit_file(
            file_path, model_version=model_version, language=language,
            page_ranges=page_ranges,
        )
        result = self.poll_result(batch_id, timeout=timeout)
        if result["state"] == "done":
            full_zip_url = result["data"].get("full_zip_url")
            if full_zip_url:
                import io
                import zipfile
                r = requests.get(full_zip_url, timeout=60)
                r.raise_for_status()
                z = zipfile.ZipFile(io.BytesIO(r.content))
                for name in z.namelist():
                    if name.endswith(".md"):
                        return z.read(name).decode("utf-8")
        return None


# ── Tesseract local fallback ──────────────────────────────────────────────────

def _check_tesseract() -> bool:
    """Return True if tesseract and pdftoppm are available."""
    return shutil.which("tesseract") is not None and shutil.which("pdftoppm") is not None


def _tesseract_pdf(file_path: str, language: str = "chi_sim",
                   page_range: str = None) -> str | None:
    """Convert PDF to text using local tesseract + pdftoppm.

    Args:
        file_path: Path to PDF.
        language: Tesseract language code ('chi_sim', 'chi_tra', 'eng', 'chi_sim+eng').
        page_range: e.g. '1-5' or None for all pages.

    Returns markdown-ish text, or None on failure.
    """
    lang_map = {"ch": "chi_sim", "en": "eng"}
    tess_lang = lang_map.get(language, language)

    with tempfile.TemporaryDirectory(prefix="lexocr_tess_") as tmpdir:
        # Convert PDF pages to images
        page_arg = []
        if page_range:
            page_arg = ["-f", page_range.split("-")[0],
                        "-l", page_range.split("-")[-1] if "-" in page_range else page_range.split("-")[0]]

        cmd = ["pdftoppm", "-png", "-r", "300", file_path, f"{tmpdir}/page"] + page_arg
        try:
            subprocess.run(cmd, capture_output=True, check=True, timeout=120)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
            return None

        # OCR each page
        pages = sorted(os.listdir(tmpdir))
        if not pages:
            return None

        results = []
        for i, png in enumerate(pages):
            png_path = os.path.join(tmpdir, png)
            out_base = os.path.join(tmpdir, f"ocr_{i}")
            try:
                subprocess.run(
                    ["tesseract", png_path, out_base, "-l", tess_lang],
                    capture_output=True, check=True, timeout=60,
                )
                txt_path = out_base + ".txt"
                if os.path.exists(txt_path):
                    with open(txt_path, "r") as f:
                        results.append(f.read().strip())
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                results.append(f"[page {i+1}: OCR failed]")

        return "\n\n---\n\n".join(results)


# ── Entry point ─────────────────────────────────────────────────────────────

def parse_pdf(
    file_path: str,
    language: str = "ch",
    page_range: str = None,
    model_version: str = "vlm",
    prefer_precise: bool = False,
    timeout: int = _DEFAULT_TIMEOUT,
) -> dict:
    """Convert PDF to markdown via MinerU API.

    Tries: LocalMinerU → PreciseAPI → AgentAPI → Tesseract.

    Args:
        file_path: Path to PDF file.
        language: Document language ('ch', 'en', etc.).
        page_range: Page range string, e.g. '1-10' or None for all pages.
        model_version: Model for PreciseAPI ('vlm', 'pipeline', 'MinerU-HTML').
        prefer_precise: Use PreciseAPI if token is available.
        timeout: Maximum seconds to wait.

    Returns:
        {"ok": True, "markdown": "...", "api": "local"|"precise"|"agent"|"tesseract",
         "file": "...", "char_count": N}
        or {"ok": False, "error": "..."}
    """
    if not os.path.exists(file_path):
        return {"ok": False, "error": f"File not found: {file_path}"}

    file_path = file_path.rstrip("/")
    if os.path.isdir(file_path):
        return {"ok": False, "error": f"Path is a directory, not a file: {file_path}"}

    if os.path.basename(file_path) == "":
        return {"ok": False, "error": f"Cannot determine filename from path: {file_path}"}

    token = _get_token()
    api_used = "agent"
    warnings: list[str] = []

    try:
        md_text = None

        # 1) Local self-hosted MinerU (fastest, no token)
        local = LocalMinerUAPI()
        md_text = local.parse_file(file_path, language=language,
                                   page_range=page_range, timeout=timeout)
        if md_text:
            api_used = "local"

        # 2) Remote PreciseAPI (best quality, needs token)
        if md_text is None and token and prefer_precise:
            if _token_appears_expired(token):
                warnings.append("MinerU Precision API token appears expired; skipped Precision API and used fallback OCR.")
            else:
                try:
                    api = PreciseAPI(token)
                    md_text = api.parse_file(
                        file_path, model_version=model_version,
                        language=language, page_ranges=page_range, timeout=timeout,
                    )
                    if md_text:
                        api_used = "precise"
                except Exception as exc:
                    warnings.append(f"MinerU Precision API failed; falling back: {_safe_error(exc)}")

        # 3) Remote AgentAPI (free, no token)
        if md_text is None:
            try:
                api = AgentAPI()
                md_text = api.parse_file(
                    file_path, language=language,
                    page_range=page_range, timeout=timeout,
                )
                if md_text:
                    api_used = "agent"
            except Exception as exc:
                warnings.append(f"MinerU Agent API failed; falling back: {_safe_error(exc)}")

        # 4) Tesseract fallback
        if md_text is None and _check_tesseract():
            md_text = _tesseract_pdf(file_path, language=language, page_range=page_range)
            if md_text:
                api_used = "tesseract"

        if md_text:
            result = {
                "ok": True,
                "api": api_used,
                "file": os.path.basename(file_path),
                "page_range": page_range,
                "markdown": md_text,
                "char_count": len(md_text),
            }
            if warnings:
                result["warnings"] = warnings
            return result
        else:
            result = {
                "ok": False,
                "error": f"MinerU {api_used} API returned no content (timeout or parse failure)",
                "api": api_used,
            }
            if warnings:
                result["warnings"] = warnings
            return result
    except Exception as e:
        # Last resort: try tesseract if MinerU threw an exception
        if _check_tesseract():
            try:
                md_text = _tesseract_pdf(file_path, language=language, page_range=page_range)
                if md_text:
                    result = {
                        "ok": True,
                        "api": "tesseract",
                        "file": os.path.basename(file_path),
                        "page_range": page_range,
                        "markdown": md_text,
                        "char_count": len(md_text),
                    }
                    if warnings:
                        result["warnings"] = warnings
                    return result
            except Exception:
                pass
        result = {"ok": False, "error": _safe_error(e), "api": api_used}
        if warnings:
            result["warnings"] = warnings
        return result
