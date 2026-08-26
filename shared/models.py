"""Shared message and report models.

SearchPlanMessage is A → B. CandidateBatchMessage is B → C. Report is what C
stores and A returns. None of these carry uploaded files or full source text.
"""

from datetime import date
from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from shared.bounds import (
    MAX_CANDIDATES,
    MAX_CLAIM_LIMITATIONS,
    MAX_CONCEPTS,
    MAX_INITIAL_QUERIES,
    MAX_LIMITATION_TEXT_LENGTH,
    MAX_PASSAGE_LENGTH,
    MAX_QUERY_TEXT_LENGTH,
    MAX_SNIPPET_LENGTH,
    MAX_SYNONYMS_PER_CONCEPT,
    MAX_UNCERTAINTY_NOTES,
)

# ASSUMPTION: id/term max lengths are local Field caps, not in bounds.py. Verify if
# limitation ids should be UUIDs vs short labels like "L1".
IdStr = Annotated[str, Field(min_length=1, max_length=32)]
TermStr = Annotated[str, Field(min_length=1, max_length=100)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# -----------------------------------------------------------------------------
# Component A Output: Search Plan 
# -----------------------------------------------------------------------------


class ClaimLimitation(StrictModel):
    id: IdStr
    claim_number: int = Field(ge=1)
    # UNCERTAIN: this is one limitation's text, not the full claim. Confirm Claude
    # should split multi-limitation claims into multiple ClaimLimitation rows.
    text: str = Field(min_length=1, max_length=MAX_LIMITATION_TEXT_LENGTH)


class Concept(StrictModel):
    term: TermStr
    # ASSUMPTION: synonyms may be empty; concepts themselves must still be present.
    synonyms: list[TermStr] = Field(default_factory=list, max_length=MAX_SYNONYMS_PER_CONCEPT)


class SearchQuery(StrictModel):
    id: IdStr
    query_text: str = Field(min_length=1, max_length=MAX_QUERY_TEXT_LENGTH)
    limitation_ids: list[IdStr] = Field(min_length=1, max_length=MAX_CLAIM_LIMITATIONS)


class SearchPlanMessage(StrictModel):
    job_id: str = Field(min_length=1, max_length=64)
    # UNCERTAIN: date-only, no timezone. Not rejected if it is in the future.
    critical_date: date
    limitations: list[ClaimLimitation] = Field(min_length=1, max_length=MAX_CLAIM_LIMITATIONS)
    concepts: list[Concept] = Field(min_length=1, max_length=MAX_CONCEPTS)
    # Spec §6: Component A may publish at most 12 initial queries.
    queries: list[SearchQuery] = Field(min_length=1, max_length=MAX_INITIAL_QUERIES)
    # INCOMPLETE: payload byte size is not checked here; Task 7 messaging should
    # reject bodies over MAX_PUBSUB_PAYLOAD_BYTES.

    @model_validator(mode="after")
    def ids_must_be_unique_and_linked(self) -> Self:
        # Reject two limitations that both claim the same id (e.g. two "L1"s).
        # set() drops duplicates, so a shorter set means an id was repeated.
        limitation_ids = [item.id for item in self.limitations]
        if len(set(limitation_ids)) != len(limitation_ids):
            raise ValueError("limitation ids must be unique")

        # Same uniqueness check for query ids (e.g. two "Q1"s).
        query_ids = [item.id for item in self.queries]
        if len(set(query_ids)) != len(query_ids):
            raise ValueError("query ids must be unique")

        known = set(limitation_ids)
        for query in self.queries:
            # A query should not list the same limitation twice: ["L1", "L1"].
            if len(query.limitation_ids) != len(set(query.limitation_ids)):
                raise ValueError(f"query {query.id} has duplicate limitation ids")
            # Every linked id must exist on this message; "Q1" cannot point at "L9"
            # if there is no limitation with that id.
            unknown = set(query.limitation_ids) - known
            if unknown:
                raise ValueError(f"query {query.id} references unknown limitation ids")
        return self


def _require_unique(items: list[object], label: str) -> None:
    if len(set(items)) != len(items):
        raise ValueError(f"{label} must be unique")


# -----------------------------------------------------------------------------
# Component B Output: Candidate Batch List
# -----------------------------------------------------------------------------

class DateCheck(StrEnum):
    # ASSUMPTION: three states are enough. B may still publish AFTER_CRITICAL_DATE
    # rows instead of dropping them; C can then rank them as non-prior-art.
    VERIFIED = "verified"  # published_on is present and on/before the critical date
    UNKNOWN = "unknown"  # no reliable publication date
    AFTER_CRITICAL_DATE = "after_critical_date"


class Candidate(StrictModel):
    title: str = Field(min_length=1, max_length=300)
    # UNCERTAIN: plain string, not HttpUrl, so unusual Exa URLs still parse.
    url: str = Field(min_length=1, max_length=2048)
    published_on: date | None = None
    snippet: str = Field(min_length=1, max_length=MAX_SNIPPET_LENGTH)
    date_check: DateCheck
    # FOLLOW-UP (spec §8): move this ceiling to Component B's total query budget.
    query_ids: list[IdStr] = Field(min_length=1, max_length=MAX_INITIAL_QUERIES)
    limitation_ids: list[IdStr] = Field(min_length=1, max_length=MAX_CLAIM_LIMITATIONS)
    # INCOMPLETE: ids are not checked against the original SearchPlanMessage here.
    # Component B/C must keep those ids consistent when they build this object.

    @model_validator(mode="after")
    def date_and_ids_must_line_up(self) -> Self:
        _require_unique(self.query_ids, "query ids")
        _require_unique(self.limitation_ids, "limitation ids")
        # VERIFIED / AFTER_CRITICAL_DATE need a date; UNKNOWN may omit it.
        if self.date_check != DateCheck.UNKNOWN and self.published_on is None:
            raise ValueError("published_on is required unless date_check is unknown")
        return self


class SearchCacheTotals(StrictModel):
    # UNCERTAIN: cache_hits + cache_misses is not required to equal searches_run
    # (a search can fail without a cache result).
    # FOLLOW-UP (spec §8): these ceilings should use the total search budget,
    # not the initial-query cap. Using MAX_INITIAL_QUERIES until that bound exists.
    searches_run: int = Field(ge=0, le=MAX_INITIAL_QUERIES)
    cache_hits: int = Field(ge=0, le=MAX_INITIAL_QUERIES)
    cache_misses: int = Field(ge=0, le=MAX_INITIAL_QUERIES)


class CandidateBatchMessage(StrictModel):
    job_id: str = Field(min_length=1, max_length=64)
    # Empty list is allowed: a valid search can find nothing.
    candidates: list[Candidate] = Field(max_length=MAX_CANDIDATES)
    totals: SearchCacheTotals
    # INCOMPLETE: payload byte size is not checked here (same follow-up as Task 7).

    @model_validator(mode="after")
    def urls_must_be_unique(self) -> Self:
        _require_unique([item.url for item in self.candidates], "candidate urls")
        return self


# -----------------------------------------------------------------------------
# Component C Output: Report
# -----------------------------------------------------------------------------

# ASSUMPTION: this wording is enough to satisfy spec §3/§9. Verify against the
# assignment write-up before the demo screenshots.
HUMAN_REVIEW_DISCLAIMER = (
    "This report is a decision-support aid. A human must make any legal "
    "determination about patentability, anticipation, obviousness, or validity."
)


class Citation(StrictModel):
    url: str = Field(min_length=1, max_length=2048)
    passage: str = Field(min_length=1, max_length=MAX_PASSAGE_LENGTH)


class RankedEvidence(StrictModel):
    rank: int = Field(ge=1, le=MAX_CANDIDATES)
    candidate: Candidate
    explanation: str = Field(min_length=1, max_length=1000)
    # ASSUMPTION: citations may be empty when evidence is uncertain.
    citations: list[Citation] = Field(default_factory=list, max_length=5)


class Report(StrictModel):
    job_id: str = Field(min_length=1, max_length=64)
    critical_date: date
    evidence: list[RankedEvidence] = Field(max_length=MAX_CANDIDATES)
    # UNCERTAIN: note strings are unbounded besides list length; no max char cap.
    uncertainty_notes: list[str] = Field(default_factory=list, max_length=MAX_UNCERTAINTY_NOTES)
    disclaimer: str = HUMAN_REVIEW_DISCLAIMER

    @field_validator("disclaimer")
    @classmethod
    def disclaimer_is_fixed(cls, value: str) -> str:
        if value != HUMAN_REVIEW_DISCLAIMER:
            raise ValueError("disclaimer must be the standard human-review statement")
        return value

    @model_validator(mode="after")
    def ranks_and_urls_must_be_unique(self) -> Self:
        # FOLLOW-UP: ranks need not be consecutive (1,3 is allowed if 2 is missing).
        _require_unique([item.rank for item in self.evidence], "evidence ranks")
        _require_unique([item.candidate.url for item in self.evidence], "evidence urls")
        return self
