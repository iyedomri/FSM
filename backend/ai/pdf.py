"""PDF extraction subprocess. Raw bytes enter through a pipe, never a temp file."""
import io
import json
import resource
import sys


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024**2, 512 * 1024**2))
    resource.setrlimit(resource.RLIMIT_CPU, (15, 15))
    from pypdf import PdfReader

    raw = sys.stdin.buffer.read(10 * 1024 * 1024 + 1)
    if len(raw) > 10 * 1024 * 1024 or not raw.startswith(b"%PDF-"):
        raise ValueError("Invalid PDF")
    reader = PdfReader(io.BytesIO(raw), strict=False)
    if reader.is_encrypted or not 1 <= len(reader.pages) <= 80:
        raise ValueError("Use an unencrypted PDF with at most 80 pages")
    pages, count = [], 0
    for index, page in enumerate(reader.pages):
        text = (page.extract_text() or "").strip()
        count += len(text)
        if count > 120000:
            raise ValueError("PDF contains too much text; split it into chapters")
        if text:
            pages.append({"page": index + 1, "text": text})
    if count < 150:
        raise ValueError("Not enough text; scanned PDFs require OCR before upload")
    sys.stdout.write(json.dumps({"pages": pages}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(2)
