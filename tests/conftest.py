"""Shared builders for valid message and report models."""

from datetime import date

from shared.models import (
    Candidate,
    CandidateBatchMessage,
    ClaimLimitation,
    Concept,
    DateCheck,
    EffectiveSearchPlan,
    RankedEvidence,
    Report,
    SearchCacheTotals,
    SearchPlanMessage,
    SearchQuery,
)


def search_plan(**overrides) -> SearchPlanMessage:
    """Minimal valid A→B search plan. Pass field overrides to vary one piece."""
    data = dict(
        job_id="job1",
        critical_date=date(2020, 1, 1),
        limitations=[ClaimLimitation(id="L1", claim_number=1, text="a widget")],
        concepts=[Concept(term="widget", synonyms=["device"])],
        queries=[
            SearchQuery(id="Q1", query_text="widget prior art", limitation_ids=["L1"]),
        ],
    )
    data.update(overrides)
    return SearchPlanMessage(**data)


def candidate(**overrides) -> Candidate:
    """Minimal valid candidate row used by batch and report tests."""
    data = dict(
        title="Example widget publication",
        url="https://example.com/widget",
        published_on=date(2019, 1, 1),
        snippet="A widget used as prior art in tests.",
        date_check=DateCheck.VERIFIED,
        query_ids=["Q1"],
    )
    data.update(overrides)
    return Candidate(**data)


def effective_plan(**overrides) -> EffectiveSearchPlan:
    """Original plan with no follow-ups unless overridden."""
    data = dict(original=search_plan(), followup_queries=[])
    data.update(overrides)
    return EffectiveSearchPlan(**data)


def candidate_batch(**overrides) -> CandidateBatchMessage:
    """Minimal valid B→C candidate batch. Job id lives on plan.original."""
    data = dict(
        plan=effective_plan(),
        candidates=[candidate()],
        totals=SearchCacheTotals(searches_run=1, cache_hits=0, cache_misses=1),
    )
    data.update(overrides)
    return CandidateBatchMessage(**data)


def report(**overrides) -> Report:
    """Minimal valid stored report. Disclaimer uses the required default."""
    data = dict(
        job_id="job1",
        critical_date=date(2020, 1, 1),
        evidence=[
            RankedEvidence(
                rank=1,
                candidate=candidate(),
                explanation="Canned test ranking; not a legal conclusion.",
            )
        ],
        uncertainty_notes=[],
    )
    data.update(overrides)
    return Report(**data)
