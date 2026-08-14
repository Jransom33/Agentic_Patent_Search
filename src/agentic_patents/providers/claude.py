"""Claude claim-analysis and ranking interface. Real API client comes later."""

from datetime import date
from typing import Protocol

from pydantic import Field

from agentic_patents.bounds import MAX_CLAIM_LIMITATIONS, MAX_CONCEPTS, MAX_QUERIES
from agentic_patents.models import (
    Candidate,
    ClaimLimitation,
    Concept,
    RankedEvidence,
    Report,
    SearchPlanMessage,
    SearchQuery,
    StrictModel,
)
from agentic_patents.providers.exa import DocumentContent


class ClaimAnalysis(StrictModel):
    """Search-plan fields Claude fills in. Component A adds job_id."""

    # INCOMPLETE: does not run SearchPlanMessage's unique-id / link checks.
    # Component A should build SearchPlanMessage from this so those validators run.
    limitations: list[ClaimLimitation] = Field(min_length=1, max_length=MAX_CLAIM_LIMITATIONS)
    concepts: list[Concept] = Field(min_length=1, max_length=MAX_CONCEPTS)
    queries: list[SearchQuery] = Field(min_length=1, max_length=MAX_QUERIES)


class ClaudeClient(Protocol):
    """Interface Component A (analyze) and C (rank) use for Claude."""

    def analyze_claims(
        self, spec_text: str, claims_text: str, critical_date: date
    ) -> ClaimAnalysis: ...

    def rank_candidates(
        self,
        plan: SearchPlanMessage,
        candidates: list[Candidate],
        contents: list[DocumentContent],
    ) -> Report: ...


class FakeClaude:
    """Returns canned valid models. Never opens a network connection."""

    def analyze_claims(
        self, spec_text: str, claims_text: str, critical_date: date
    ) -> ClaimAnalysis:
        """Ignore uploaded text and return a tiny valid claim map.

        spec_text and claims_text are untrusted; this fake does not copy them
        into the result and must not log them.
        """
        # ASSUMPTION: a hardcoded widget claim is enough for tests.
        # INCOMPLETE: production needs a real ClaudeClient that calls Anthropic.
        # critical_date is unused here; a real client should pass it in the prompt.
        return ClaimAnalysis(
            limitations=[
                ClaimLimitation(id="L1", claim_number=1, text="a widget"),
            ],
            concepts=[Concept(term="widget", synonyms=["device"])],
            queries=[
                SearchQuery(
                    id="Q1",
                    query_text="widget prior art",
                    limitation_ids=["L1"],
                ),
            ],
        )

    def rank_candidates(
        self,
        plan: SearchPlanMessage,
        candidates: list[Candidate],
        contents: list[DocumentContent],
    ) -> Report:
        """Build a valid Report from the first candidate, if any."""
        # UNCERTAIN: only the first candidate is ranked; extras are dropped.
        # contents are unused except to set an uncertainty note.
        evidence: list[RankedEvidence] = []
        notes: list[str] = []
        if candidates:
            evidence.append(
                RankedEvidence(
                    rank=1,
                    candidate=candidates[0],
                    explanation="Canned test ranking; not a legal conclusion.",
                )
            )
        else:
            notes.append("No candidates were available to rank.")
        if not contents:
            notes.append("No full-text contents were retrieved.")
        return Report(
            job_id=plan.job_id,
            critical_date=plan.critical_date,
            evidence=evidence,
            uncertainty_notes=notes,
        )
