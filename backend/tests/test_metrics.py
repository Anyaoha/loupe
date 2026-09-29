from loupe.metrics import compute_metrics
from tests.conftest import WINDOW_END, WINDOW_START


def test_totals_add_up(session, seeded_repo):
    m = compute_metrics(session, seeded_repo, WINDOW_START, WINDOW_END)
    t = m.totals
    assert t.commits == 10
    assert t.prs_opened == 7  # 101-106 and 108
    assert t.prs_merged == 5
    assert t.prs_closed_unmerged == 1
    assert t.issues_opened == 4
    assert t.issues_closed == 3
    assert t.reviews == 7
    assert t.review_comments == 6
    assert t.active_committers == 3
    assert t.active_reviewers == 3


def test_leaderboards(session, seeded_repo):
    m = compute_metrics(session, seeded_repo, WINDOW_START, WINDOW_END)
    lb = m.leaderboards
    assert [(c.actor, c.count) for c in lb.committers] == [("alice", 6), ("bob", 3), ("carol", 1)]
    assert [(c.actor, c.count) for c in lb.pr_authors] == [("alice", 2), ("bob", 2), ("carol", 1)]
    assert [(c.actor, c.count) for c in lb.mergers] == [("alice", 2), ("bob", 2), ("carol", 1)]
    assert [(c.actor, c.count) for c in lb.closers] == [("bob", 2), ("carol", 1), ("erin", 1)]
    top = lb.lines_changed[0]
    assert (top.actor, top.additions, top.deletions, top.total, top.prs) == ("bob", 320, 100, 420, 2)
    r = lb.reviewers[0]
    assert (r.actor, r.reviews, r.approvals, r.changes_requested, r.comments) == ("bob", 4, 3, 1, 3)


def test_flow_percentiles(session, seeded_repo):
    m = compute_metrics(session, seeded_repo, WINDOW_START, WINDOW_END)
    assert m.flow.time_to_merge.sample == 5
    assert m.flow.time_to_merge.median_hours == 72.0
    assert m.flow.time_to_merge.p90_hours == 182.4
    assert m.flow.issue_turnaround.median_hours == 120.0
    assert m.flow.merged_without_review == 1
    assert m.flow.merged_without_review_ratio == 0.2


def test_open_state_at_window_end(session, seeded_repo):
    m = compute_metrics(session, seeded_repo, WINDOW_START, WINDOW_END)
    assert m.open_state.open_prs == 2
    assert m.open_state.stale_prs == 1
    assert m.open_state.oldest_open[0].number == 107
    assert m.open_state.oldest_open[0].age_days == 39.5  # opened 12:00 Feb 19, window ends 00:00 Mar 31


def test_concentration(session, seeded_repo):
    m = compute_metrics(session, seeded_repo, WINDOW_START, WINDOW_END)
    c = m.concentration
    assert c.review_top1_share == 0.5714
    assert c.review_top2_share == 0.8571
    assert c.commit_top1_share == 0.6
    assert c.commit_bus_factor == 1


def test_weekly_buckets_cover_window(session, seeded_repo):
    m = compute_metrics(session, seeded_repo, WINDOW_START, WINDOW_END)
    assert len(m.weekly) == 5  # 30 days -> 5 buckets of 7
    assert sum(w.commits for w in m.weekly) == 10
    assert sum(w.prs_merged for w in m.weekly) == 5
    assert sum(w.reviews for w in m.weekly) == 7


def test_coverage_full_when_window_inside_sync_range(session, seeded_repo):
    m = compute_metrics(session, seeded_repo, WINDOW_START, WINDOW_END)
    assert m.coverage.covered_ratio == 1.0


def test_empty_window_is_zeros_not_errors(session, seeded_repo):
    from datetime import UTC, datetime

    m = compute_metrics(session, seeded_repo, datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 31, tzinfo=UTC))
    assert m.totals.commits == 0
    assert m.flow.time_to_merge.median_hours is None
    assert m.leaderboards.reviewers == []
    assert m.concentration.commit_bus_factor is None
    assert m.coverage.covered_ratio == 0.0
