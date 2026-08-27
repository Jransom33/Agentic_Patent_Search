"""Exa search/contents interface, FakeExa, and the production ExaApi client."""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Protocol

from shared.bounds import MAX_CONTENT_FETCHES, MAX_EXA_RESULTS_PER_QUERY, MAX_SNIPPET_LENGTH


@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str
    published_on: date | None
    snippet: str


@dataclass(frozen=True)
class DocumentContent:
    url: str
    # ExaApi truncates to _MAX_CONTENT_CHARS; FakeExa returns a short canned string.
    text: str


class ExaClient(Protocol):
    """Interface Component B (search) and C (contents) use for Exa."""

    def search(
        self, query: str, published_before: date, num_results: int
    ) -> list[SearchHit]: ...

    def get_contents(self, urls: list[str]) -> list[DocumentContent]: ...


_CANNED_HIT = SearchHit(
    title="Example widget publication",
    url="https://example.com/widget",
    published_on=date(2019, 1, 1),
    snippet="A widget used as prior art in tests.",
)
_CANNED_TEXT = "A widget is disclosed. This is synthetic test content, not a real paper."


class FakeExa:
    """Returns canned hits. Never opens a network connection."""

    def search(
        self, query: str, published_before: date, num_results: int
    ) -> list[SearchHit]:
        """Return up to num_results canned hits dated on/before published_before."""
        # ASSUMPTION: query text is ignored; one example.com hit is enough for tests.
        # UNCERTAIN: date filter is published_on > published_before means exclude;
        # same-day hits are kept.
        count = max(0, min(num_results, MAX_EXA_RESULTS_PER_QUERY))
        if count == 0:
            return []
        if (
            _CANNED_HIT.published_on is not None
            and _CANNED_HIT.published_on > published_before
        ):
            return []
        return [_CANNED_HIT][:count]

    def get_contents(self, urls: list[str]) -> list[DocumentContent]:
        """Return canned text for the example URL; skip unknown URLs."""
        found: list[DocumentContent] = []
        for url in urls[:MAX_CONTENT_FETCHES]:
            if url == _CANNED_HIT.url:
                found.append(DocumentContent(url=url, text=_CANNED_TEXT))
        return found


# Truncate full-page text so ranking prompts stay bounded. Not in bounds.py.
# ASSUMPTION: 10k chars matches Exa's default text cap and is enough for ranking.
_MAX_CONTENT_CHARS = 10_000


def _parse_published_on(value: str | None) -> date | None:
    """Parse Exa's ISO published_date; return None if missing or malformed."""
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _snippet_from(result: object) -> str:
    """Build a bounded snippet: highlights, else text, else title."""
    highlights = getattr(result, "highlights", None)
    if not isinstance(highlights, list):
        highlights = []
    parts = [part.strip() for part in highlights if isinstance(part, str) and part.strip()]
    text = " ".join(parts) or (getattr(result, "text", None) or "").strip()
    if not text:
        text = (getattr(result, "title", None) or "").strip()
    return text[:MAX_SNIPPET_LENGTH]


class ExaApi:
    """Production ExaClient using the official exa-py SDK.

    Tests keep using FakeExa and never construct this class. Provider errors
    propagate to the caller; Component B/C decide whether to retry or fail.
    """

    def __init__(self, api_key: str) -> None:
        # Import here so FakeExa tests do not need the SDK loaded.
        from exa_py import Exa

        self._exa = Exa(api_key=api_key)

    def search(
        self, query: str, published_before: date, num_results: int
    ) -> list[SearchHit]:
        """Search Exa and map hits into SearchHit. Never logs query text."""
        count = max(0, min(num_results, MAX_EXA_RESULTS_PER_QUERY))
        if count == 0:
            return []
        # Exa keeps results published *before* end_published_date. Adding one
        # day makes the critical date inclusive (same-day prior art). Component
        # B still re-validates dates after retrieval.
        # UNCERTAIN: if Exa already treats the cutoff as inclusive, one extra
        # day of hits may appear; B will drop those after the critical date.
        response = self._exa.search(
            query,
            num_results=count,
            end_published_date=(published_before + timedelta(days=1)).isoformat(),
            contents={"highlights": True},
        )
        hits: list[SearchHit] = []
        for item in getattr(response, "results", None) or []:
            url = (getattr(item, "url", None) or "").strip()
            if not url:
                continue
            title = (getattr(item, "title", None) or "").strip() or url
            snippet = _snippet_from(item) or title
            hits.append(
                SearchHit(
                    title=title,
                    url=url,
                    published_on=_parse_published_on(getattr(item, "published_date", None)),
                    snippet=snippet,
                )
            )
        return hits[:count]

    def get_contents(self, urls: list[str]) -> list[DocumentContent]:
        """Fetch full text for a bounded URL list. Skip empty bodies."""
        clipped = urls[:MAX_CONTENT_FETCHES]
        if not clipped:
            return []
        response = self._exa.get_contents(clipped, text=True)
        found: list[DocumentContent] = []
        for item in getattr(response, "results", None) or []:
            url = (getattr(item, "url", None) or "").strip()
            text = (getattr(item, "text", None) or "").strip()
            if url and text:
                found.append(DocumentContent(url=url, text=text[:_MAX_CONTENT_CHARS]))
        return found
