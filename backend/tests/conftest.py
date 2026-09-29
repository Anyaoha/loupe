"""Shared fixtures.

`seeded_repo` builds a small, hand-verifiable dataset. Every expected number in the metric
tests is derivable by reading this file, which is the point: the tests check that the
engine's numbers add up against the source data, not against a snapshot of itself.

Window under test: 2026-03-01 .. 2026-03-31 (30 days). Baseline: 2026-01-30 .. 2026-03-01.
"""

from datetime import UTC, datetime, timedelta

import pytest

from loupe import db as dbmod
from loupe.models import ActivityEvent, ActivityKind, Repository, ReviewState, SyncState, SyncStatus, WorkItem, WorkItemKind

WINDOW_START = datetime(2026, 3, 1, tzinfo=UTC)
WINDOW_END = datetime(2026, 3, 31, tzinfo=UTC)


def d(day: int, hour: int = 12, month: int = 3) -> datetime:
    return datetime(2026, month, day, hour, tzinfo=UTC)


@pytest.fixture
def engine(tmp_path):
    eng = dbmod.init_engine(f"sqlite:///{tmp_path}/test.db")
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine):
    with dbmod.session_scope() as s:
        yield s


@pytest.fixture
def seeded_repo(session):
    return seed_acme_widgets(session)


def seed_acme_widgets(session):
    repo = Repository(source="github", owner="acme", name="widgets", default_branch="main")
    repo.sync_state = SyncState(
        status=SyncStatus.OK,
        backfill_from=datetime(2025, 10, 1, tzinfo=UTC),
        work_items_synced_to=datetime(2026, 4, 1, tzinfo=UTC),
        commits_synced_to=datetime(2026, 4, 1, tzinfo=UTC),
    )
    session.add(repo)
    session.flush()
    rid = repo.id

    def pr(number, author, created, merged=None, closed=None, adds=0, dels=0, reviews=0, merged_by=None):
        return WorkItem(
            repository_id=rid, kind=WorkItemKind.PULL_REQUEST, number=number, title=f"PR {number}", author=author,
            url=f"https://example.test/pr/{number}", created_at=created, updated_at=merged or closed or created,
            closed_at=closed or merged, merged_at=merged, merged_by=merged_by, additions=adds, deletions=dels, review_count=reviews,
        )

    def issue(number, author, created, closed=None):
        return WorkItem(
            repository_id=rid, kind=WorkItemKind.ISSUE, number=number, title=f"Issue {number}", author=author,
            created_at=created, updated_at=closed or created, closed_at=closed,
        )

    def commit(sha, actor, when):
        return ActivityEvent(repository_id=rid, kind=ActivityKind.COMMIT, external_id=sha, actor=actor, occurred_at=when)

    def review(rid_, actor, when, pr_number, state=ReviewState.APPROVED, comments=0):
        return ActivityEvent(
            repository_id=rid, kind=ActivityKind.REVIEW, external_id=rid_, actor=actor, occurred_at=when,
            work_item_number=pr_number, review_state=state, comment_count=comments,
        )

    # --- Merged PRs inside the window (5) ---
    # time-to-merge hours: 24, 48, 72, 96, 240  -> median 72, p90 = 96 + 0.6*(240-96) = 182.4
    items = [
        pr(101, "alice", d(1), merged=d(2), adds=100, dels=20, reviews=1, merged_by="bob"),        # 24h
        pr(102, "alice", d(3), merged=d(5), adds=50, dels=10, reviews=2, merged_by="bob"),         # 48h
        pr(103, "bob", d(5), merged=d(8), adds=300, dels=100, reviews=1, merged_by="alice"),       # 72h
        pr(104, "carol", d(10), merged=d(14), adds=10, dels=5, reviews=0, merged_by="carol"),      # 96h, no review
        pr(105, "bob", d(15), merged=d(25), adds=20, dels=0, reviews=1, merged_by="alice"),        # 240h
        # opened in window, still open at window end (age at 03-31 = 11 days -> not stale)
        pr(106, "dave", d(20)),
        # opened before window, still open, stale (39.5 days old at window end)
        pr(107, "dave", d(19, month=2)),
        # opened in window, closed without merge inside window
        pr(108, "carol", d(12), closed=d(13)),
        # merged outside the window: must not count
        pr(109, "alice", d(20, month=2), merged=d(25, month=2), adds=999, dels=999, reviews=1, merged_by="bob"),
        # Issues: 3 closed in window (turnaround 48h, 120h, 240h -> median 120), 1 opened & open
        issue(201, "erin", d(1), closed=d(3)),
        issue(202, "erin", d(5), closed=d(10)),
        issue(203, "frank", d(10), closed=d(20)),
        issue(204, "frank", d(28)),
        # closed before window: must not count
        issue(205, "erin", d(1, month=2), closed=d(2, month=2)),
    ]
    session.add_all(items)

    # --- Commits in window: alice 6, bob 3, carol 1 = 10. top1 share 0.6, bus factor 1 ---
    events = [commit(f"a{i}", "alice", d(2 + i)) for i in range(6)]
    events += [commit(f"b{i}", "bob", d(3 + i)) for i in range(3)]
    events += [commit("c0", "carol", d(9))]
    # commit outside window
    events += [commit("z0", "zed", d(28, month=2))]

    # --- Reviews in window: bob 4 (3 approve, 1 changes), alice 2 (2 approve), carol 1 (comment) = 7
    # top1 share 4/7 = 0.5714, top2 6/7 = 0.8571 ; comments total = 2 + 1 + 3 = 6
    events += [
        review("r1", "bob", d(2, 10), 101, comments=2),
        review("r2", "bob", d(4), 102),
        review("r3", "bob", d(4, 13), 102, state=ReviewState.CHANGES_REQUESTED, comments=1),
        review("r4", "bob", d(20), 105),
        review("r5", "alice", d(7), 103),
        review("r6", "alice", d(21), 106),
        review("r7", "carol", d(22), 106, state=ReviewState.COMMENTED, comments=3),
        # review outside window
        review("r8", "bob", d(20, month=2), 109),
    ]
    session.add_all(events)
    session.flush()
    return repo
