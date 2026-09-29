"""Canonical storage model.

Every source system (GitHub today; Jira, Linear, GitLab tomorrow) is normalized into two
source-agnostic shapes before anything downstream sees it:

  * WorkItem       - stateful things with a lifecycle: pull requests, issues, tickets.
  * ActivityEvent  - point-in-time things: commits, reviews, review comments.

Metrics, signals and insights only ever read these tables. Adding a source means writing
one adapter that emits these shapes; nothing downstream changes.
"""

from datetime import datetime

from loupe.timeutil import utcnow
from enum import StrEnum

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class WorkItemKind(StrEnum):
    PULL_REQUEST = "pull_request"
    ISSUE = "issue"


class ActivityKind(StrEnum):
    COMMIT = "commit"
    REVIEW = "review"


class ReviewState(StrEnum):
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"
    COMMENTED = "commented"
    DISMISSED = "dismissed"


class SyncStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    OK = "ok"
    ERROR = "error"


class Repository(Base):
    __tablename__ = "repositories"
    __table_args__ = (UniqueConstraint("source", "owner", "name", name="uq_repo_source_owner_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    owner: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    default_branch: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    sync_state: Mapped["SyncState"] = relationship(back_populates="repository", uselist=False, cascade="all, delete-orphan")

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


class SyncState(Base):
    __tablename__ = "sync_states"

    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default=SyncStatus.PENDING)
    backfill_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # High-water marks: everything updated before these timestamps is known to be stored.
    work_items_synced_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    commits_synced_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    last_run_stats: Mapped[dict | None] = mapped_column(JSON)

    repository: Mapped[Repository] = relationship(back_populates="sync_state")


class WorkItem(Base):
    __tablename__ = "work_items"
    __table_args__ = (
        UniqueConstraint("repository_id", "kind", "number", name="uq_work_item"),
        Index("ix_work_items_repo_created", "repository_id", "kind", "created_at"),
        Index("ix_work_items_repo_closed", "repository_id", "kind", "closed_at"),
        Index("ix_work_items_repo_merged", "repository_id", "merged_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, default="")
    author: Mapped[str | None] = mapped_column(String(255))
    url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    merged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    merged_by: Mapped[str | None] = mapped_column(String(255))
    closed_by: Mapped[str | None] = mapped_column(String(255))
    additions: Mapped[int] = mapped_column(Integer, default=0)
    deletions: Mapped[int] = mapped_column(Integer, default=0)
    changed_files: Mapped[int] = mapped_column(Integer, default=0)
    review_count: Mapped[int] = mapped_column(Integer, default=0)
    labels: Mapped[list | None] = mapped_column(JSON)


class ActivityEvent(Base):
    __tablename__ = "activity_events"
    __table_args__ = (
        UniqueConstraint("repository_id", "kind", "external_id", name="uq_activity_event"),
        Index("ix_activity_repo_kind_time", "repository_id", "kind", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    actor: Mapped[str | None] = mapped_column(String(255))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    work_item_number: Mapped[int | None] = mapped_column(Integer)
    review_state: Mapped[str | None] = mapped_column(String(32))
    additions: Mapped[int] = mapped_column(Integer, default=0)
    deletions: Mapped[int] = mapped_column(Integer, default=0)
    comment_count: Mapped[int] = mapped_column(Integer, default=0)


class Insight(Base):
    """Cached LLM syntheses. Keyed by repo + window + prompt version + model so a changed
    prompt or model never serves a stale narrative."""

    __tablename__ = "insights"
    __table_args__ = (
        UniqueConstraint("repository_id", "window_start", "window_end", "prompt_version", "model", name="uq_insight"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    body: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FeedbackVerdict(StrEnum):
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class InsightFeedback(Base):
    """A human verdict on one synthesized narrative, keyed by its trace id. Confidence, prompt
    version and model are snapshotted so a later refresh of the cached insight cannot rewrite
    what was actually judged. This is the ground truth confidence gets calibrated against."""

    __tablename__ = "insight_feedback"

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    verdict: Mapped[str] = mapped_column(String(16), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class LlmTrace(Base):
    """One row per model call. Column names follow the OpenTelemetry GenAI semantic
    conventions (gen_ai.*) so this table can be exported to any OTel backend unchanged."""

    __tablename__ = "llm_traces"

    id: Mapped[int] = mapped_column(primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    purpose: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    gen_ai_system: Mapped[str] = mapped_column(String(32), nullable=False)
    gen_ai_request_model: Mapped[str] = mapped_column(String(128), nullable=False)
    gen_ai_response_model: Mapped[str | None] = mapped_column(String(128))
    gen_ai_usage_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    gen_ai_usage_output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    gen_ai_response_finish_reason: Mapped[str | None] = mapped_column(String(32))
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(16), default="ok")
    error_type: Mapped[str | None] = mapped_column(String(128))
    error_message: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
