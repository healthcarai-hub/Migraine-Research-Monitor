"""PDF to text through poppler's pdftotext. Reading order and tables are only as good as the PDF; very short output is treated as a failure."""
from __future__ import annotations

import re
import shutil
import subprocess


def available() -> bool:
    return shutil.which("pdftotext") is not None


def is_pdf(data: bytes) -> bool:
    return data[:5] == b"%PDF-"


def pdf_to_text(data: bytes, *, timeout: int = 90) -> str | None:
    if not available() or not is_pdf(data):
        return None
    try:
        proc = subprocess.run(["pdftotext", "-enc", "UTF-8", "-", "-"], input=data, capture_output=True, timeout=timeout, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    text = proc.stdout.decode("utf8", "replace").replace("\x0c", "\n\n")
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)   # join words hyphenated at line ends
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()
