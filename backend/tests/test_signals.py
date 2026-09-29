"""Signal detectors against a dataset built to trip specific thresholds."""

from datetime import UTC, datetime, timedelta

import pytest

from loupe.models import ActivityEvent, ActivityKind, Repository, ReviewState, SyncState, SyncStatus, WorkItem, WorkItemKind
from loupe.signals import compute_signals

START = datetime(2026, 5, 1, tzinfo=UTC)
END = datetime(2026, 5, 29, tzinfo=UTC)  # 28 days; baseline = Apr 3 .. May 1
BASE_START = START - (END - START)


@pytest.fixture
def drifting_repo(session):
    repo = Repository(source="github", owner="acme", name="drift")
    repo.sync_state = SyncState(status=SyncStatus.OK, backfill_from=BASE_START - timedelta(days=30), work_items_synced_to=END, commits_synced_to=END)
    session.add(repo)
    session.flush()
    rid = repo.id
    n = 0

    def pr(author, created, merged, reviews=1):
        nonlocal n
        n += 1
        return WorkItem(repository_id=rid, kind=WorkItemKind.PULL_REQUEST, number=n, title=f"pr {n}", author=author,
                        created_at=created, updated_at=merged, closed_at=merged, merged_at=merged, merged_by="lead", review_count=reviews)

    rows = []
    # Baseline: 20 PRs merged, ~12h each, all reviewed, 6 committers.
    for i in range(20):
        t = BASE_START + timedelta(days=i)
        rows.append(pr(f"dev{i % 6}", t, t + timedelta(hours=12)))
    # Current: 8 PRs merged (-60%), median 60h (5x), 4 of 8 unreviewed, 2 committers.
    for i in range(8):
        t = START + timedelta(days=2 * i)
        rows.append(pr(f"dev{i % 2}", t, t + timedelta(hours=60), reviews=0 if i % 2 else 1))
    # 6 stale open PRs (opened 20 days before END)
    for i in range(6):
        n += 1
        rows.append(WorkItem(repository_id=rid, kind=WorkItemKind.PULL_REQUEST, number=n, title="stale", author="dev0",
                             created_at=END - timedelta(days=20), updated_at=END - timedelta(days=20)))
    session.add_all(rows)

    ev = []
    for i in range(30):  # baseline commits by 6 people
        ev.append(ActivityEvent(repository_id=rid, kind=ActivityKind.COMMIT, external_id=f"b{i}", actor=f"dev{i % 6}", occurred_at=BASE_START + timedelta(hours=6 * i)))
    for i in range(25):  # current commits: 22 by dev0 -> bus factor 1
        ev.append(ActivityEvent(repository_id=rid, kind=ActivityKind.COMMIT, external_id=f"c{i}", actor="dev0" if i < 22 else "dev1", occurred_at=START + timedelta(hours=12 * i)))
    for i in range(12):  # current reviews: 9 by lead -> 75% concentration
        ev.append(ActivityEvent(repository_id=rid, kind=ActivityKind.REVIEW, external_id=f"r{i}", actor="lead" if i < 9 else "dev1",
                                occurred_at=START + timedelta(hours=20 * i), work_item_number=21 + (i % 8), review_state=ReviewState.APPROVED))
    session.add_all(ev)
    session.flush()
    return repo


def test_detects_expected_signals_with_severity_ordering(session, drifting_repo):
    r = compute_signals(session, drifting_repo, START, END)
    by_id = {s.id: s for s in r.signals}
    assert set(by_id) >= {"velocity_change", "time_to_merge_drift", "unreviewed_merges", "review_concentration", "stale_pr_backlog", "commit_bus_factor", "committer_churn"}
    assert by_id["velocity_change"].severity == "critical"  # 8 vs 20 = -60%
    assert by_id["time_to_merge_drift"].severity == "critical"  # 60h vs 12h
    assert by_id["unreviewed_merges"].severity == "critical"  # 4/8
    assert by_id["review_concentration"].severity == "critical"  # 9/12
    assert by_id["stale_pr_backlog"].severity == "warning"  # 6
    assert by_id["commit_bus_factor"].severity == "warning"
    assert by_id["committer_churn"].severity == "critical"  # 2 vs 6
    ranks = ["critical", "warning", "info"]
    assert [ranks.index(s.severity) for s in r.signals] == sorted(ranks.index(s.severity) for s in r.signals)


def test_every_evidence_ref_resolves_to_a_fact(session, drifting_repo):
    r = compute_signals(session, drifting_repo, START, END)
    for s in r.signals:
        missing = [ref for ref in s.evidence_refs if ref not in r.facts]
        assert not missing, f"{s.id} references unknown facts {missing}"


def test_related_items_point_at_real_work(session, drifting_repo):
    r = compute_signals(session, drifting_repo, START, END)
    by_id = {s.id: s for s in r.signals}
    assert len(by_id["unreviewed_merges"].related_items) == 4
    assert len(by_id["stale_pr_backlog"].related_items) == 6
    assert by_id["time_to_merge_drift"].related_items  # slowest merged PRs


def test_churn_names_who_went_missing(session, drifting_repo):
    r = compute_signals(session, drifting_repo, START, END)
    absent = r.facts["cur.committers.absent_vs_baseline"].value
    assert absent == "dev2, dev3, dev4, dev5"


def test_quiet_repo_produces_no_signals(session, seeded_repo):
    from tests.conftest import WINDOW_END, WINDOW_START

    r = compute_signals(session, seeded_repo, WINDOW_START, WINDOW_END)
    assert r.signals == []
