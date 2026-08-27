"""Input validation for Component A. No network or API server."""

from datetime import date
from io import BytesIO

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from intake.extraction import InputValidationError, validate_and_extract
from shared.bounds import MAX_UPLOAD_BYTES


def text_pdf_bytes(text: str = "A widget specification") -> bytes:
    """Build a tiny PDF whose extract_text() returns `text`."""
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 10 100 Td ({text}) Tj ET".encode("latin-1"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


def blank_pdf_bytes() -> bytes:
    """PDF with a page but no text — stands in for a scanned/image-only file."""
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_valid_inputs_return_normalized_text_and_date():
    """Pass a text PDF, claims, and date; expect collapsed whitespace and a parsed date."""
    result = validate_and_extract(
        text_pdf_bytes("A widget\nspecification"),
        b"  1. A widget.  \n",
        "2020-01-01",
    )
    assert result.spec_text == "A widget specification"
    assert result.claims_text == "1. A widget."
    assert result.critical_date == date(2020, 1, 1)


def test_non_pdf_bytes_are_rejected():
    """Pass garbage instead of a PDF; expect a readable-PDF error, not a parser crash."""
    with pytest.raises(InputValidationError, match="not a readable PDF"):
        validate_and_extract(b"not a pdf", b"1. A widget.", "2020-01-01")


def test_image_only_pdf_is_rejected():
    """Pass a PDF with no extractable text; expect rejection because OCR is out of scope."""
    with pytest.raises(InputValidationError, match="no embedded text"):
        validate_and_extract(blank_pdf_bytes(), b"1. A widget.", "2020-01-01")


def test_oversized_upload_is_rejected_before_parsing():
    """Pass bytes over the combined size cap; expect a size error before PDF parsing."""
    huge = b"x" * (MAX_UPLOAD_BYTES + 1)
    with pytest.raises(InputValidationError, match="size limit"):
        validate_and_extract(huge, b"1. A widget.", "2020-01-01")


def test_non_utf8_claims_are_rejected():
    """Pass claims that are not UTF-8; expect an encoding error instead of garbled text."""
    with pytest.raises(InputValidationError, match="not valid UTF-8"):
        validate_and_extract(text_pdf_bytes(), b"\xff\xfe claims", "2020-01-01")


def test_invalid_critical_date_is_rejected():
    """Pass YYYYMMDD instead of YYYY-MM-DD; expect a date error so the format stays strict."""
    with pytest.raises(InputValidationError, match="YYYY-MM-DD"):
        validate_and_extract(text_pdf_bytes(), b"1. A widget.", "20200101")


def test_validation_errors_do_not_echo_document_text():
    """Pass invalid claims containing secret text; expect the error not to include that text."""
    secret = "CONFIDENTIAL-CLAIM-LANGUAGE"
    with pytest.raises(InputValidationError) as exc:
        validate_and_extract(text_pdf_bytes(), secret.encode("utf-16"), "2020-01-01")
    assert secret not in str(exc.value)
