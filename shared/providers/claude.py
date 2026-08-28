"""Claude claim-analysis, search-decision, and ranking interfaces.

Real API clients live with their consuming components; only the narrow
interfaces and the deterministic FakeClaude are shared.
"""

from datetime import date
from enum import StrEnum
from typing import Annotated, Protocol, Self

from pydantic import Field, model_validator

from shared.bounds import (
    MAX_CLAIM_LIMITATIONS,
    MAX_CONCEPTS,
    MAX_FOLLOWUP_QUERIES,
    MAX_INITIAL_QUERIES,
    MIN_FOLLOWUP_QUERIES,
)
from shared.models import (
    Candidate,
    ClaimLimitation,
    Concept,
    RankedEvidence,
    Report,
    SearchPlanMessage,
    SearchQuery,
    StrictModel,
)
from shared.providers.exa import DocumentContent


class ClaimAnalysis(StrictModel):
    """Search-plan fields Claude fills in. Component A adds job_id."""

    # INCOMPLETE: does not run SearchPlanMessage's unique-id / link checks.
    # Component A should build SearchPlanMessage from this so those validators run.
    limitations: list[ClaimLimitation] = Field(min_length=1, max_length=MAX_CLAIM_LIMITATIONS)
    concepts: list[Concept] = Field(min_length=1, max_length=MAX_CONCEPTS)
    queries: list[SearchQuery] = Field(min_length=1, max_length=MAX_INITIAL_QUERIES)


class ClaimAnalyzer(Protocol):
    """Interface Component A (intake/) uses for Claude claim analysis."""

    def analyze_claims(
        self, spec_text: str, claims_text: str, critical_date: date
    ) -> ClaimAnalysis: ...


class CandidateRanker(Protocol):
    """Interface Component C (report/) uses for Claude evidence ranking."""

    def rank_candidates(
        self,
        plan: SearchPlanMessage,
        candidates: list[Candidate],
        contents: list[DocumentContent],
    ) -> Report: ...


# -----------------------------------------------------------------------------
# Component B: search continuation decisions (spec §8)
# -----------------------------------------------------------------------------


class SearchAction(StrEnum):
    FINISH = "finish"
    CONTINUE = "continue"


class SearchDecision(StrictModel):
    """Claude's validated finish/continue choice after one search pass.

    Claude only proposes; Component B's ordinary Python loop enforces every
    hard budget (total queries, passes, decisions) from shared/bounds.py.
    """

    action: SearchAction
    # Which limitations still lack good candidates.
    coverage_gaps: list[Annotated[str, Field(min_length=1)]] = Field(
        default_factory=list, max_length=MAX_CLAIM_LIMITATIONS
    )
    followup_queries: list[SearchQuery] = Field(default_factory=list)

    @model_validator(mode="after")
    def followups_match_action(self) -> Self:
        """continue needs 3-6 follow-up queries; finish must supply none."""
        count = len(self.followup_queries)
        if self.action is SearchAction.CONTINUE and not (
            MIN_FOLLOWUP_QUERIES <= count <= MAX_FOLLOWUP_QUERIES
        ):
            raise ValueError(
                f"continue requires {MIN_FOLLOWUP_QUERIES}-{MAX_FOLLOWUP_QUERIES} followup queries"
            )
        if self.action is SearchAction.FINISH and count:
            raise ValueError("finish must not include followup queries")
        return self


class SearchDecider(Protocol):
    """Interface Component B (search/) uses for Claude continuation decisions."""

    def decide_search(
        self,
        plan: SearchPlanMessage,
        tried_queries: list[SearchQuery],
        candidates: list[Candidate],
    ) -> SearchDecision: ...


class FakeClaude:
    """Returns canned valid models. Never opens a network connection.

    Implements ClaimAnalyzer, SearchDecider, and CandidateRanker so one fake
    serves Component A, B, and C tests.
    """

    def __init__(self, continue_rounds: int = 0) -> None:
        # Test knob: how many decide_search calls answer CONTINUE before the
        # fake finishes. Defaults to finishing immediately.
        self.continue_rounds = continue_rounds

    def analyze_claims(
        self, spec_text: str, claims_text: str, critical_date: date
    ) -> ClaimAnalysis:
        """Ignore uploaded text and return a tiny valid claim map.

        spec_text and claims_text are untrusted; this fake does not copy them
        into the result and must not log them.
        """
        # ASSUMPTION: a hardcoded widget claim is enough for tests.
        # INCOMPLETE: production needs a real ClaimAnalyzer that calls Anthropic.
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

    def decide_search(
        self,
        plan: SearchPlanMessage,
        tried_queries: list[SearchQuery],
        candidates: list[Candidate],
    ) -> SearchDecision:
        """Finish immediately, or CONTINUE continue_rounds times first.

        Follow-up ids are numbered after the tried queries (F3, F4, ...) so
        repeated rounds never collide with earlier ids. candidates are
        untrusted snippets and are never copied into the decision.
        """
        if self.continue_rounds <= 0:
            return SearchDecision(action=SearchAction.FINISH)
        self.continue_rounds -= 1
        # ASSUMPTION: the minimum of three follow-ups, all aimed at the plan's
        # first limitation, is enough for tests.
        start = len(tried_queries)
        return SearchDecision(
            action=SearchAction.CONTINUE,
            coverage_gaps=["Canned coverage gap for tests."],
            followup_queries=[
                SearchQuery(
                    id=f"F{start + offset}",
                    query_text=f"widget follow-up {start + offset}",
                    limitation_ids=[plan.limitations[0].id],
                )
                for offset in range(MIN_FOLLOWUP_QUERIES)
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
