"""Shared job states and numeric bounds for all three components.

Spec §11 requires every external call, message, query, result set, concurrent
work, and upload to be bounded. Later pydantic models should import these
constants instead of inventing their own limits.
"""

from enum import StrEnum

# Visible job lifecycle from spec §11. Components A and C persist these.
# ASSUMPTION: five states only; no "queued" or "retrying" state. Verify if
# failed jobs need a reason enum beyond a free-form error_code later.
class JobStatus(StrEnum):
    ANALYZING = "analyzing"
    SEARCHING = "searching"
    RANKING = "ranking"
    COMPLETED = "completed"
    FAILED = "failed"


# --- Claim-analysis bounds (Component A / search-plan messages) ---
# ASSUMPTION: all numeric ceilings below are class-demo guesses, not measured
# production limits. Verify against expected claim counts and Exa/Claude cost.
MAX_CLAIM_LIMITATIONS = 20
MAX_LIMITATION_TEXT_LENGTH = 500
MAX_CONCEPTS = 30
MAX_SYNONYMS_PER_CONCEPT = 8
# Component A's initial-query cap. Do not reuse this as Component B's total
# search budget (MAX_TOTAL_QUERIES below).
MAX_INITIAL_QUERIES = 12
MAX_QUERY_TEXT_LENGTH = 300

# --- Retrieval bounds (Component B / candidate messages) ---
MAX_CANDIDATES = 25
MAX_SNIPPET_LENGTH = 500
MAX_EXA_RESULTS_PER_QUERY = 10
MAX_CONCURRENCY = 4
# Spec §8: Component B hard ceilings. Claude may finish early but cannot
# override these. Follow-up count is per continuation decision, not total.
MAX_TOTAL_QUERIES = 40
MAX_SEARCH_PASSES = 8
MAX_CONTINUATION_DECISIONS = 7
MIN_FOLLOWUP_QUERIES = 3
MAX_FOLLOWUP_QUERIES = 6
# ASSUMPTION: 24h is enough for a class demo to show miss-then-hit on a
# repeated search. Not a production cache policy.
REDIS_CACHE_TTL_SECONDS = 86_400

# --- Ranking bounds (Component C / reports) ---
MAX_CONTENT_FETCHES = 8
MAX_UNCERTAINTY_NOTES = 20
MAX_PASSAGE_LENGTH = 800

# --- Transport and upload bounds ---
# UNCERTAIN: GCP Pub/Sub allows up to 10MB; 256KB is a tighter self-imposed cap.
MAX_PUBSUB_PAYLOAD_BYTES = 256_000
# UNCERTAIN: this is combined spec PDF + claims size, not a per-file limit.
MAX_UPLOAD_BYTES = 5_000_000
MAX_RETRIES = 3

# INCOMPLETE: no PDF page cap or per-file size yet.
# FOLLOW-UP: later models should enforce the new search budgets; this module
# only defines them.
