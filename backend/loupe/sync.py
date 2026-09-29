"""Pulls canonical records from an adapter into the store.

Incremental strategy: each repo keeps two high-water marks (work items, commits). A run
asks the adapter for everything updated since the mark minus a small overlap, upserts, and
advances the mark to the run's start time. The overlap absorbs clock skew and in-flight
updates; upserts make re-processing idempotent.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session

from loupe.adapters.base import ActivityRecord, RepoRef, SourceAdapter, WorkItemRecord
from loupe.db import session_scope
from loupe.models import ActivityEvent, Repository, SyncState, SyncStatus, WorkItem
from loupe.timeutil import ensure_utc, utcnow

log = logging.getLogger(__name__)

OVERLAP = timedelta(minutes=10)
BATCH = 200


@dataclass
class SyncStats:
    work_items: int = 0
    activity_events: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"work_items": self.work_items, "activity_events": self.activity_events, "errors": self.errors}


async def sync_repository(repository_id: int, adapter: SourceAdapter, backfill_days: int) -> SyncStats:
    started = utcnow()
    stats = SyncStats()
    with session_scope() as s:
        repo = s.get(Repository, repository_id)
        if repo is None:
            raise ValueError(f"repository {repository_id} not found")
        state = repo.sync_state or SyncState(repository_id=repo.id)
        state.status = SyncStatus.RUNNING
        state.last_started_at = started
        state.last_error = None
        if state.backfill_from is None:
            state.backfill_from = started - timedelta(days=backfill_days)
        s.add(state)
        ref = RepoRef(owner=repo.owner, name=repo.name)
        backfill_from = ensure_utc(state.backfill_from)
        wi_mark = ensure_utc(state.work_items_synced_to)
        c_mark = ensure_utc(state.commits_synced_to)

    try:
        info = await adapter.get_repo_info(ref)
        wi_since = (wi_mark - OVERLAP) if wi_mark else backfill_from
        c_since = (c_mark - OVERLAP) if c_mark else backfill_from

        await _sync_work_items(repository_id, adapter, ref, wi_since, stats)
        await _sync_commits(repository_id, adapter, ref, c_since, stats)

        with session_scope() as s:
            repo = s.get(Repository, repository_id)
            repo.default_branch = info.default_branch
            st = repo.sync_state
            st.status = SyncStatus.OK
            st.work_items_synced_to = started
            st.commits_synced_to = started
            st.last_finished_at = utcnow()
            st.last_run_stats = stats.as_dict()
    except Exception as exc:  # noqa: BLE001 - we persist and re-raise for the caller
        log.exception("sync failed for repository %s", repository_id)
        record_sync_error(repository_id, exc, stats)
        raise
    return stats


def record_sync_error(repository_id: int, exc: Exception, stats: SyncStats | None = None) -> None:
    """Persist a failed run so the API and UI show why, including failures before any fetch."""
    with session_scope() as s:
        st = s.get(SyncState, repository_id) or SyncState(repository_id=repository_id)
        st.status = SyncStatus.ERROR
        st.last_error = f"{type(exc).__name__}: {exc}"
        st.last_finished_at = utcnow()
        st.last_run_stats = (stats or SyncStats()).as_dict()
        s.add(st)


async def _sync_work_items(repository_id: int, adapter: SourceAdapter, ref: RepoRef, since: datetime, stats: SyncStats) -> None:
    items: list[WorkItemRecord] = []
    events: list[ActivityRecord] = []
    async for item, activity in adapter.iter_work_items(ref, since):
        items.append(item)
        events.extend(activity)
        if len(items) >= BATCH:
            await asyncio.to_thread(_upsert_batch, repository_id, items, events)
            stats.work_items += len(items)
            stats.activity_events += len(events)
            items, events = [], []
    if items or events:
        await asyncio.to_thread(_upsert_batch, repository_id, items, events)
        stats.work_items += len(items)
        stats.activity_events += len(events)


async def _sync_commits(repository_id: int, adapter: SourceAdapter, ref: RepoRef, since: datetime, stats: SyncStats) -> None:
    events: list[ActivityRecord] = []
    async for ev in adapter.iter_commits(ref, since):
        events.append(ev)
        if len(events) >= BATCH:
            await asyncio.to_thread(_upsert_batch, repository_id, [], events)
            stats.activity_events += len(events)
            events = []
    if events:
        await asyncio.to_thread(_upsert_batch, repository_id, [], events)
        stats.activity_events += len(events)


def _upsert_batch(repository_id: int, items: list[WorkItemRecord], events: list[ActivityRecord]) -> None:
    with session_scope() as s:
        if items:
            _upsert_work_items(s, repository_id, items)
        if events:
            _upsert_events(s, repository_id, events)


def _upsert_work_items(s: Session, repository_id: int, items: list[WorkItemRecord]) -> None:
    rows = [
        {
            "repository_id": repository_id,
            "kind": i.kind,
            "number": i.number,
            "title": i.title,
            "author": i.author,
            "url": i.url,
            "created_at": i.created_at,
            "updated_at": i.updated_at,
            "closed_at": i.closed_at,
            "merged_at": i.merged_at,
            "merged_by": i.merged_by,
            "closed_by": i.closed_by,
            "additions": i.additions,
            "deletions": i.deletions,
            "changed_files": i.changed_files,
            "review_count": i.review_count,
            "labels": i.labels,
        }
        for i in items
    ]
    s.execute(_upsert(s, WorkItem, rows, key=("repository_id", "kind", "number")))


def _upsert_events(s: Session, repository_id: int, events: list[ActivityRecord]) -> None:
    rows = [
        {
            "repository_id": repository_id,
            "kind": e.kind,
            "external_id": e.external_id,
            "actor": e.actor,
            "occurred_at": e.occurred_at,
            "work_item_number": e.work_item_number,
            "review_state": e.review_state,
            "additions": e.additions,
            "deletions": e.deletions,
            "comment_count": e.comment_count,
        }
        for e in events
    ]
    s.execute(_upsert(s, ActivityEvent, rows, key=("repository_id", "kind", "external_id")))


def _upsert(s: Session, model, rows: list[dict], key: tuple[str, ...]):
    """Dialect-aware INSERT ... ON CONFLICT DO UPDATE. SQLite for local, Postgres for prod."""
    dialect = s.get_bind().dialect.name
    insert = postgresql.insert if dialect == "postgresql" else sqlite.insert
    stmt = insert(model).values(rows)
    update_cols = {c: getattr(stmt.excluded, c) for c in rows[0] if c not in key}
    return stmt.on_conflict_do_update(index_elements=list(key), set_=update_cols)


def get_or_create_repository(s: Session, source: str, owner: str, name: str) -> tuple[Repository, bool]:
    repo = s.scalar(select(Repository).where(Repository.source == source, Repository.owner == owner, Repository.name == name))
    if repo:
        return repo, False
    repo = Repository(source=source, owner=owner, name=name)
    repo.sync_state = SyncState(status=SyncStatus.PENDING)
    s.add(repo)
    s.flush()
    return repo, True
