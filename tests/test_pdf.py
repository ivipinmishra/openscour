"""PDF pages: downloading, extracting, and rejoining lines into sentences."""

import io

import httpx
import pytest
from pypdf import PdfWriter

from openscour.server import fetcher
from openscour.server.fetcher import PDF_TYPE, FetchError, _clean_pdf_text, _download, _extract_pdf


def make_pdf(pages: list[list[str]]) -> bytes:
    """A small, valid PDF with one text line per entry, built by hand (no fixtures)."""
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>"]
    page_ids = [4 + 2 * i for i in range(len(pages))]
    kids = " ".join(f"{i} 0 R" for i in page_ids).encode()
    objects.append(b"<< /Type /Pages /Kids [" + kids + b"] /Count " + str(len(pages)).encode() + b" >>")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for i, lines in enumerate(pages):
        content_id = page_ids[i] + 1
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> "
            b"/Contents " + str(content_id).encode() + b" 0 R >>"
        )
        text = "".join(
            "(" + line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ") Tj T* " for line in lines
        )
        stream = f"BT /F1 11 Tf 14 TL 72 740 Td {text}ET".encode("latin-1")
        objects.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for n, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{n} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


REPORT = [
    [
        "Annual Water Report",
        "The reservoir holds 4,200 million litres when",
        "full, enough for about 90 days of supply. Infor-",
        "mation is updated monthly.",
    ],
    ["Rainfall in 2026 was 12% above the long-term average."],
]


def pdf_client(body: bytes, content_type: str) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": content_type})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_pdf_lines_are_rejoined_into_sentences():
    text = _extract_pdf(make_pdf(REPORT))
    # Lines broken mid-sentence are joined, and the hyphenated word is whole again.
    assert "The reservoir holds 4,200 million litres when full, enough for about 90 days of supply." in text
    assert "Information is updated monthly." in text
    assert "Rainfall in 2026 was 12% above the long-term average." in text


def test_clean_pdf_text_keeps_paragraph_breaks():
    raw = "First line of a\nsentence that ends here.\n\nA new para-\ngraph starts."
    assert _clean_pdf_text(raw) == "First line of a sentence that ends here.\nA new paragraph starts."


def test_only_the_first_pages_are_extracted(monkeypatch):
    monkeypatch.setattr(fetcher.settings, "pdf_max_pages", 1)
    text = _extract_pdf(make_pdf(REPORT))
    assert "reservoir" in text
    assert "Rainfall" not in text


def test_encrypted_and_broken_pdfs_fall_back():
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.encrypt("secret")
    encrypted = io.BytesIO()
    writer.write(encrypted)
    with pytest.raises(FetchError, match="encrypted PDF"):
        _extract_pdf(encrypted.getvalue())

    with pytest.raises(FetchError, match="unreadable PDF"):
        _extract_pdf(b"%PDF-1.4\nthis is not really a PDF")


@pytest.mark.parametrize("content_type", ["application/pdf", "application/octet-stream", ""])
async def test_pdfs_are_downloaded_as_bytes_whatever_their_content_type(content_type):
    pdf = make_pdf(REPORT)
    body, ctype = await _download(pdf_client(pdf, content_type), "https://site.example/report", 5)
    assert (body, ctype) == (pdf, PDF_TYPE)


async def test_binary_files_that_are_not_pdfs_are_refused():
    with pytest.raises(FetchError, match="unsupported content type application/octet-stream"):
        await _download(pdf_client(b"\x00\x01 a zip file", "application/octet-stream"), "https://site.example/f", 5)


async def test_pdf_over_the_size_limit_is_refused_not_cut(monkeypatch):
    # A cut-off PDF can't be parsed, unlike a cut-off HTML page.
    monkeypatch.setattr(fetcher.settings, "max_pdf_bytes", 100)
    pdf = make_pdf(REPORT)
    assert len(pdf) > 100
    with pytest.raises(FetchError, match="PDF larger than MAX_PDF_BYTES"):
        await _download(pdf_client(pdf, "application/pdf"), "https://site.example/report.pdf", 5)


async def test_fetch_page_extracts_pdfs_in_the_worker_pool():
    text = await fetcher.fetch_page(pdf_client(make_pdf(REPORT), "application/pdf"), "https://site.example/r.pdf", 5)
    assert "enough for about 90 days of supply" in text
