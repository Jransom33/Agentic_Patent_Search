"""Component C ranking pipeline: fetch contents, rank, and validate the report.

Implements the fetch/rank/validate stretch of spec §10. The worker owns
idempotency and job status; this module never talks to Pub/Sub or JobStore.
"""

from pydantic import ValidationError

from shared.bounds import MAX_RETRIES, MAX_UNCERTAINTY_NOTES
from shared.models import CandidateBatchMessage, Report
from shared.providers.claude import CandidateRanker
from shared.providers.exa import DocumentContent, ExaClient

# Safe token the worker persists as the failed-job error_code.
RANKING_FAILED = "ranking_failed"
# Short note appended when at least one URL exhausted its retries. Ranking
# still uses any bodies that did arrive. Not a legal conclusion.
FETCH_FAILED_NOTE = (
    "Some full-text retrievals failed; missing documents were ranked from snippets only."
)


class RankingFailedError(Exception):
    """Claude ranking failed after bounded retries.

    The worker catches this, marks the job failed with RANKING_FAILED, and
    does not store a report.
    """

    def __init__(self) -> None:
        super().__init__(RANKING_FAILED)
        self.error_code = RANKING_FAILED


def _fetch_one(exa: ExaClient, url: str) -> tuple[DocumentContent | None, bool]:
    """Fetch one URL up to MAX_RETRIES times.

    Returns (body, exhausted). exhausted is True only when every attempt
    raised. A successful call with no usable text returns (None, False) and
    is not retried.
    """
    # ASSUMPTION: MAX_RETRIES is total attempts per URL, matching
    # search/executor.py's per-query Exa retries.
    # INCOMPLETE: no sleep/backoff between attempts; URLs are fetched
    # sequentially, so a 25-candidate batch is one Exa call per URL.
    for _attempt in range(MAX_RETRIES):
        try:
            found = exa.get_contents([url])
            for item in found:
                if item.url == url:
                    return item, False
            return None, False
        except Exception:
            continue
    return None, True


def _fetch_contents(
    exa: ExaClient, urls: list[str]
) -> tuple[list[DocumentContent], list[str]]:
    """Fetch full text for each candidate URL, independently.

    An empty URL list skips Exa. Each URL gets its own MAX_RETRIES attempts,
    so a failure on one does not skip the rest. Bodies that arrive are kept;
    URLs that exhaust retries add FETCH_FAILED_NOTE and are ranked from snippets.
    """
    if not urls:
        return [], []
    clipped: list[DocumentContent] = []
    any_failed = False
    for url in urls:
        body, exhausted = _fetch_one(exa, url)
        if body is not None:
            clipped.append(body)
        if exhausted:
            any_failed = True
    return clipped, [FETCH_FAILED_NOTE] if any_failed else []


def _check_report(report: Report, batch: CandidateBatchMessage) -> None:
    """Reject reports that invent sources or break source-linked citations."""
    plan = batch.plan.original
    if report.job_id != plan.job_id:
        raise ValueError("report job_id does not match the batch")
    if report.critical_date != plan.critical_date:
        raise ValueError("report critical_date does not match the batch")
    known_urls = {item.url for item in batch.candidates}
    for item in report.evidence:
        if item.candidate.url not in known_urls:
            raise ValueError("ranked evidence url is not in the candidate batch")
        for citation in item.citations:
            if citation.url != item.candidate.url:
                raise ValueError("citation url must match its candidate url")


def _with_notes(report: Report, extra: list[str]) -> Report:
    """Append pipeline notes without exceeding MAX_UNCERTAINTY_NOTES."""
    if not extra:
        return report
    notes = list(report.uncertainty_notes)
    for note in extra:
        if note not in notes and len(notes) < MAX_UNCERTAINTY_NOTES:
            notes.append(note)
    return report.model_copy(update={"uncertainty_notes": notes})


def _rank_with_retries(
    ranker: CandidateRanker,
    batch: CandidateBatchMessage,
    contents: list[DocumentContent],
) -> Report:
    """Ask Claude to rank; retry invalid or failed answers up to MAX_RETRIES.

    Invalid structured output (wrong job id, invented URLs, mismatched
    citations) shares the same retry budget as provider errors. Raises
    RankingFailedError when every attempt fails.
    """
    plan = batch.plan.original
    for _attempt in range(MAX_RETRIES):
        try:
            report = ranker.rank_candidates(plan, batch.candidates, contents)
            # Ranker may return a non-Report (adapter refusal); treat as invalid.
            if not isinstance(report, Report):
                raise ValueError("ranker did not return a Report")
            _check_report(report, batch)
            return report
        except (ValidationError, ValueError):
            continue
        except Exception:
            # UNCERTAIN: one retry budget covers both invalid output and
            # provider errors, matching search/loop.py.
            continue
    raise RankingFailedError()


def run_ranking(
    batch: CandidateBatchMessage,
    *,
    ranker: CandidateRanker,
    exa: ExaClient,
) -> Report:
    """Fetch contents for the batch, rank, and return a validated Report.

    Empty candidate lists still go to the ranker (no Exa call). Per-URL fetch
    failures degrade those documents to snippets with FETCH_FAILED_NOTE.
    Ranking failure after retries raises RankingFailedError.
    """
    # FOLLOW-UP: the worker must not call this for terminal-failure batches
    # (error_code set). Those skip ranking and mark the job failed.
    urls = [item.url for item in batch.candidates]
    contents, fetch_notes = _fetch_contents(exa, urls)
    report = _rank_with_retries(ranker, batch, contents)
    return _with_notes(report, fetch_notes)
