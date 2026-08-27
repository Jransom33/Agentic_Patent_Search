"""Input validation and text extraction for Component A (spec §7).

Pure functions over bytes the API layer already received: they either return
normalized inputs or raise InputValidationError with a safe message. Error
messages must never echo uploaded document content (spec §15).
"""

from dataclasses import dataclass
from datetime import date, datetime
from io import BytesIO

from pypdf import PdfReader

from shared.bounds import MAX_UPLOAD_BYTES


class InputValidationError(ValueError):
    """Client-input problem whose message is safe to return over the API."""


# The three validated inputs the rest of the pipeline consumes.
@dataclass(frozen=True)
class ExtractedInputs:
    spec_text: str
    claims_text: str
    critical_date: date


def _normalize(text: str) -> str:
    # Collapse all runs of whitespace so PDF line breaks and layout artifacts
    # do not leak into the text handed to Claude.
    return " ".join(text.split())


def extract_pdf_text(pdf_bytes: bytes) -> str:
    """Extract embedded text from a specification PDF.

    Rejects non-PDF bytes and image-only PDFs (no OCR in scope, spec §3).
    """
    try:
        pages = PdfReader(BytesIO(pdf_bytes)).pages
        # extract_text() returns "" for image-only pages, so joining and
        # normalizing yields an empty string for a scanned PDF.
        text = _normalize(" ".join(page.extract_text() or "" for page in pages))
    except Exception:
        # ASSUMPTION: any pypdf failure means a malformed upload, not a bug
        # worth surfacing to the client; details stay out of the response.
        raise InputValidationError("specification is not a readable PDF")
    if not text:
        raise InputValidationError("specification PDF contains no embedded text")
    return text


def decode_claims(claims_bytes: bytes) -> str:
    """Decode the claims file as UTF-8 and require non-empty text."""
    try:
        text = _normalize(claims_bytes.decode("utf-8"))
    except UnicodeDecodeError:
        raise InputValidationError("claims file is not valid UTF-8 text")
    if not text:
        raise InputValidationError("claims file is empty")
    return text


def parse_critical_date(date_text: str) -> date:
    """Parse a strict YYYY-MM-DD critical date."""
    # strptime enforces the exact format; date.fromisoformat is looser in
    # Python 3.11+ (accepts e.g. YYYYMMDD), which the spec does not allow.
    try:
        return datetime.strptime(date_text, "%Y-%m-%d").date()
    except ValueError:
        raise InputValidationError("critical date must be a valid YYYY-MM-DD date")
    # UNCERTAIN: future or very old critical dates are accepted; verify
    # whether a plausibility range check is wanted.


def validate_and_extract(
    pdf_bytes: bytes, claims_bytes: bytes, date_text: str
) -> ExtractedInputs:
    """Validate all three inputs and return them normalized for the pipeline."""
    # Size check first: cheapest rejection, before any parsing work.
    # ASSUMPTION: MAX_UPLOAD_BYTES is a combined cap (per shared/bounds.py),
    # not per-file.
    if len(pdf_bytes) + len(claims_bytes) > MAX_UPLOAD_BYTES:
        raise InputValidationError("uploaded files exceed the combined size limit")
    return ExtractedInputs(
        spec_text=extract_pdf_text(pdf_bytes),
        claims_text=decode_claims(claims_bytes),
        critical_date=parse_critical_date(date_text),
    )
