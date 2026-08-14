"""Exa search/contents interface. Real HTTP client comes later."""

from dataclasses import dataclass
from datetime import date
from typing import Protocol

from shared.bounds import MAX_CONTENT_FETCHES, MAX_EXA_RESULTS_PER_QUERY


@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str
    published_on: date | None
    snippet: str


@dataclass(frozen=True)
class DocumentContent:
    url: str
    # UNCERTAIN: no max length here. A real client should truncate before ranking.
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
        # INCOMPLETE: production needs a real ExaClient that calls the Exa API.
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
