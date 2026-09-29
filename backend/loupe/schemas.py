"""Pydantic models for the HTTP API. These are the contract; storage models are not exposed."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RepoOut(BaseModel):
    source: str
    owner: str
    name: str
    default_branch: str | None = None


class SyncStateOut(BaseModel):
    status: str
    backfill_from: datetime | None = None
    work_items_synced_to: datetime | None = None
    commits_synced_to: datetime | None = None
    last_started_at: datetime | None = None
    last_finished_at: datetime | None = None
    last_error: str | None = None
    last_run_stats: dict | None = None


class TrackedRepoOut(BaseModel):
    repository: RepoOut
    sync: SyncStateOut


class TrackRepoIn(BaseModel):
    source: Literal["github"] = "github"
    owner: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}$")
    name: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,100}$")


# ---- metrics -------------------------------------------------------------------------


class Window(BaseModel):
    start: datetime
    end: datetime
    days: float


class Coverage(BaseModel):
    """How much of the requested window the local store can actually vouch for."""

    backfill_from: datetime | None
    synced_to: datetime | None
    covered_ratio: float = Field(ge=0, le=1)
    sync_status: str


class Totals(BaseModel):
    commits: int
    prs_opened: int
    prs_merged: int
    prs_closed_unmerged: int
    issues_opened: int
    issues_closed: int
    reviews: int
    review_comments: int
    active_committers: int
    active_reviewers: int


class ActorCount(BaseModel):
    actor: str
    count: int


class ActorLines(BaseModel):
    actor: str
    additions: int
    deletions: int
    total: int
    prs: int


class ReviewerRow(BaseModel):
    actor: str
    reviews: int
    approvals: int
    changes_requested: int
    comments: int


class Leaderboards(BaseModel):
    committers: list[ActorCount]
    pr_authors: list[ActorCount]
    lines_changed: list[ActorLines]
    reviewers: list[ReviewerRow]
    mergers: list[ActorCount]


class DurationStats(BaseModel):
    median_hours: float | None
    p90_hours: float | None
    sample: int


class Flow(BaseModel):
    time_to_merge: DurationStats
    issue_turnaround: DurationStats
    merged_without_review: int
    merged_without_review_ratio: float | None


class StalePR(BaseModel):
    number: int
    title: str
    author: str | None
    age_days: float
    url: str | None


class OpenState(BaseModel):
    open_prs: int
    stale_prs: int
    stale_threshold_days: int
    oldest_open: list[StalePR]


class WeeklyPoint(BaseModel):
    week_start: datetime
    commits: int
    prs_opened: int
    prs_merged: int
    issues_closed: int
    reviews: int
    median_time_to_merge_hours: float | None


class Concentration(BaseModel):
    review_top1_share: float | None
    review_top2_share: float | None
    commit_top1_share: float | None
    commit_bus_factor: int | None = Field(description="Fewest committers whose commits sum to >= 50% of the window's commits")


class MetricsReport(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    repository: RepoOut
    window: Window
    coverage: Coverage
    totals: Totals
    leaderboards: Leaderboards
    flow: Flow
    open_state: OpenState
    concentration: Concentration
    weekly: list[WeeklyPoint]


# ---- signals -------------------------------------------------------------------------


class Signal(BaseModel):
    id: str
    kind: str
    severity: Literal["info", "warning", "critical"]
    title: str
    summary: str
    value: float | None
    baseline: float | None
    direction: Literal["up", "down", "flat"] | None = None
    evidence_refs: list[str] = Field(default_factory=list, description="Fact ids from the facts table")
    related_items: list[int] = Field(default_factory=list, description="PR / issue numbers behind this signal")


class SignalsReport(BaseModel):
    repository: RepoOut
    window: Window
    baseline_window: Window
    coverage: Coverage
    signals: list[Signal]
    facts: dict[str, "Fact"]


class Fact(BaseModel):
    label: str
    value: float | int | str | None
    unit: str | None = None


# ---- insights ------------------------------------------------------------------------


class EvidenceClaim(BaseModel):
    fact_id: str
    claim: str
    quoted_value: float | int | str | None = None
    verified: bool
    actual_value: float | int | str | None = None
    note: str | None = None


class RootCause(BaseModel):
    hypothesis: str
    model_confidence: float = Field(ge=0, le=1)


class Verification(BaseModel):
    claims_total: int
    claims_verified: int
    claims_failed: int
    unknown_actors: list[str]
    penalty: float


class ConfidenceBreakdown(BaseModel):
    model_confidence: float
    after_verification: float
    coverage_cap: float
    final: float


class InsightOut(BaseModel):
    repository: RepoOut
    window: Window
    headline: str
    narrative: str
    root_cause: RootCause | None
    confidence: float = Field(ge=0, le=1)
    confidence_breakdown: ConfidenceBreakdown
    evidence: list[EvidenceClaim]
    signals_considered: list[str]
    verification: Verification
    prompt_version: str
    model: str
    trace_id: str
    cached: bool
    generated_at: datetime


class FeedbackIn(BaseModel):
    verdict: Literal["confirmed", "rejected"]
    note: str | None = Field(default=None, max_length=1000)


class FeedbackOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    trace_id: str
    verdict: str
    note: str | None
    confidence: float
    prompt_version: str
    model: str
    created_at: datetime
    updated_at: datetime


class CalibrationBucket(BaseModel):
    lower: float
    upper: float
    rated: int
    confirmed: int
    hit_rate: float | None = Field(description="Share of rated insights in this band that a human confirmed")
    mean_confidence: float | None


class CalibrationReport(BaseModel):
    prompt_version: str | None
    rated: int
    confirmed: int
    brier_score: float | None = Field(description="Mean squared gap between confidence and outcome (0 = perfect, 0.25 = coin flip at 50%)")
    buckets: list[CalibrationBucket]


class LlmTraceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    trace_id: str
    purpose: str
    prompt_version: str
    gen_ai_system: str
    gen_ai_request_model: str
    gen_ai_response_model: str | None
    gen_ai_usage_input_tokens: int
    gen_ai_usage_output_tokens: int
    gen_ai_response_finish_reason: str | None
    latency_ms: float
    estimated_cost_usd: float
    status: str
    error_type: str | None
    created_at: datetime

