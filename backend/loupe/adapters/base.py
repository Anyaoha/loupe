"""Source adapter contract.

An adapter turns one external system's API into canonical records. It knows nothing about
storage, metrics or LLMs. To add Jira/Linear/GitLab: implement this protocol, register it in
adapters/__init__.py, done.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class RepoRef:
    owner: str
    name: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


@dataclass
class WorkItemRecord:
    kind: str  # WorkItemKind
    number: int
    title: str
    author: str | None
    url: str | None
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None
    merged_at: datetime | None
    merged_by: str | None = None
    closed_by: str | None = None
    additions: int = 0
    deletions: int = 0
    changed_files: int = 0
    review_count: int = 0
    labels: list[str] = field(default_factory=list)


@dataclass
class ActivityRecord:
    kind: str  # ActivityKind
    external_id: str
    actor: str | None
    occurred_at: datetime
    work_item_number: int | None = None
    review_state: str | None = None
    additions: int = 0
    deletions: int = 0
    comment_count: int = 0


@dataclass
class RepoInfo:
    default_branch: str


class SourceAdapter(Protocol):
    source: str

    async def get_repo_info(self, repo: RepoRef) -> RepoInfo: ...

    def iter_work_items(self, repo: RepoRef, updated_since: datetime) -> AsyncIterator[tuple[WorkItemRecord, list[ActivityRecord]]]:
        """Yield work items updated on/after `updated_since`, newest first, with the
        activity (reviews) attached to each. Newest-first lets the caller stop paging
        as soon as it crosses its high-water mark."""
        ...

    def iter_commits(self, repo: RepoRef, since: datetime) -> AsyncIterator[ActivityRecord]: ...


class AdapterError(RuntimeError):
    pass


class RepoNotFound(AdapterError):
    pass


class AuthError(AdapterError):
    pass


class RateLimited(AdapterError):
    def __init__(self, reset_at: datetime | None):
        super().__init__(f"rate limited until {reset_at}")
        self.reset_at = reset_at
