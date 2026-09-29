"""Computes a MetricsReport for one repository over one time window.

Everything here is a read over the canonical tables. Aggregates run in SQL; percentiles run
in Python over the (window-bounded) rows they need, which keeps the code obvious and the
row counts small. Nothing here knows which source the data came from.
"""

from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from loupe.models import ActivityEvent, ActivityKind, Repository, ReviewState, SyncStatus, WorkItem, WorkItemKind
from loupe.schemas import (
    ActorCount,
    ActorLines,
    Concentration,
    Coverage,
    DurationStats,
    Flow,
    Leaderboards,
    MetricsReport,
    OpenState,
    RepoOut,
    ReviewerRow,
    StalePR,
    Totals,
    WeeklyPoint,
    Window,
)
from loupe.timeutil import ensure_utc

LEADERBOARD_SIZE = 10
STALE_PR_DAYS = 14


def compute_metrics(s: Session, repo: Repository, start: datetime, end: datetime) -> MetricsReport:
    rid = repo.id
    merged = _merged_prs(s, rid, start, end)
    reviews = _reviews(s, rid, start, end)
    committers = _committers(s, rid, start, end)
    open_prs = _open_prs_at(s, rid, end)

    prs_opened = _count_items(s, rid, WorkItemKind.PULL_REQUEST, WorkItem.created_at, start, end)
    prs_closed_unmerged = s.scalar(
        select(func.count()).where(
            WorkItem.repository_id == rid,
            WorkItem.kind == WorkItemKind.PULL_REQUEST,
            WorkItem.closed_at >= start,
            WorkItem.closed_at < end,
            WorkItem.merged_at.is_(None),
        )
    ) or 0
    issues_opened = _count_items(s, rid, WorkItemKind.ISSUE, WorkItem.created_at, start, end)
    issue_durations = _issue_turnaround(s, rid, start, end)

    total_commits = sum(c.count for c in committers)
    total_reviews = sum(r.reviews for r in reviews)

    no_review = [p for p in merged if p["review_count"] == 0]
    ttm_hours = [_hours(p["created_at"], p["merged_at"]) for p in merged]

    return MetricsReport(
        repository=RepoOut(source=repo.source, owner=repo.owner, name=repo.name, default_branch=repo.default_branch),
        window=_window(start, end),
        coverage=compute_coverage(repo, start, end),
        totals=Totals(
            commits=total_commits,
            prs_opened=prs_opened,
            prs_merged=len(merged),
            prs_closed_unmerged=prs_closed_unmerged,
            issues_opened=issues_opened,
            issues_closed=len(issue_durations),
            reviews=total_reviews,
            review_comments=sum(r.comments for r in reviews),
            active_committers=len(committers),
            active_reviewers=len(reviews),
        ),
        leaderboards=Leaderboards(
            committers=committers[:LEADERBOARD_SIZE],
            pr_authors=_top_counts([p["author"] for p in merged]),
            lines_changed=_lines_changed(merged),
            reviewers=reviews[:LEADERBOARD_SIZE],
            mergers=_top_counts([p["merged_by"] for p in merged]),
        ),
        flow=Flow(
            time_to_merge=_duration_stats(ttm_hours),
            issue_turnaround=_duration_stats(issue_durations),
            merged_without_review=len(no_review),
            merged_without_review_ratio=round(len(no_review) / len(merged), 4) if merged else None,
        ),
        open_state=_open_state(open_prs, end),
        concentration=_concentration(reviews, committers, total_reviews, total_commits),
        weekly=_weekly(s, rid, start, end, merged),
    )


def compute_coverage(repo: Repository, start: datetime, end: datetime) -> Coverage:
    st = repo.sync_state
    backfill_from = ensure_utc(st.backfill_from) if st else None
    synced_to = ensure_utc(min(filter(None, [st.work_items_synced_to, st.commits_synced_to]), default=None)) if st else None
    if backfill_from is None or synced_to is None:
        ratio = 0.0
    else:
        lo, hi = max(start, backfill_from), min(end, synced_to)
        ratio = max(0.0, (hi - lo).total_seconds()) / max(1.0, (end - start).total_seconds())
    return Coverage(
        backfill_from=backfill_from,
        synced_to=synced_to,
        covered_ratio=round(min(1.0, ratio), 4),
        sync_status=st.status if st else SyncStatus.PENDING,
    )


# ---- queries -------------------------------------------------------------------------


def _merged_prs(s: Session, rid: int, start: datetime, end: datetime) -> list[dict]:
    rows = s.execute(
        select(
            WorkItem.number,
            WorkItem.author,
            WorkItem.merged_by,
            WorkItem.created_at,
            WorkItem.merged_at,
            WorkItem.additions,
            WorkItem.deletions,
            WorkItem.review_count,
        ).where(
            WorkItem.repository_id == rid,
            WorkItem.kind == WorkItemKind.PULL_REQUEST,
            WorkItem.merged_at >= start,
            WorkItem.merged_at < end,
        )
    ).all()
    return [
        {
            "number": r.number,
            "author": r.author,
            "merged_by": r.merged_by,
            "created_at": ensure_utc(r.created_at),
            "merged_at": ensure_utc(r.merged_at),
            "additions": r.additions,
            "deletions": r.deletions,
            "review_count": r.review_count,
        }
        for r in rows
    ]


def _reviews(s: Session, rid: int, start: datetime, end: datetime) -> list[ReviewerRow]:
    rows = s.execute(
        select(
            ActivityEvent.actor,
            func.count().label("reviews"),
            func.sum(case((ActivityEvent.review_state == ReviewState.APPROVED, 1), else_=0)).label("approvals"),
            func.sum(case((ActivityEvent.review_state == ReviewState.CHANGES_REQUESTED, 1), else_=0)).label("changes_requested"),
            func.sum(ActivityEvent.comment_count).label("comments"),
        )
        .where(
            ActivityEvent.repository_id == rid,
            ActivityEvent.kind == ActivityKind.REVIEW,
            ActivityEvent.occurred_at >= start,
            ActivityEvent.occurred_at < end,
            ActivityEvent.actor.is_not(None),
        )
        .group_by(ActivityEvent.actor)
        .order_by(func.count().desc(), ActivityEvent.actor)
    ).all()
    return [
        ReviewerRow(actor=r.actor, reviews=r.reviews, approvals=r.approvals or 0, changes_requested=r.changes_requested or 0, comments=r.comments or 0)
        for r in rows
    ]


def _committers(s: Session, rid: int, start: datetime, end: datetime) -> list[ActorCount]:
    rows = s.execute(
        select(ActivityEvent.actor, func.count().label("n"))
        .where(
            ActivityEvent.repository_id == rid,
            ActivityEvent.kind == ActivityKind.COMMIT,
            ActivityEvent.occurred_at >= start,
            ActivityEvent.occurred_at < end,
            ActivityEvent.actor.is_not(None),
        )
        .group_by(ActivityEvent.actor)
        .order_by(func.count().desc(), ActivityEvent.actor)
    ).all()
    return [ActorCount(actor=r.actor, count=r.n) for r in rows]


def _count_items(s: Session, rid: int, kind: str, col, start: datetime, end: datetime) -> int:
    return s.scalar(select(func.count()).where(WorkItem.repository_id == rid, WorkItem.kind == kind, col >= start, col < end)) or 0


def _issue_turnaround(s: Session, rid: int, start: datetime, end: datetime) -> list[float]:
    rows = s.execute(
        select(WorkItem.created_at, WorkItem.closed_at).where(
            WorkItem.repository_id == rid,
            WorkItem.kind == WorkItemKind.ISSUE,
            WorkItem.closed_at >= start,
            WorkItem.closed_at < end,
        )
    ).all()
    return [_hours(ensure_utc(r.created_at), ensure_utc(r.closed_at)) for r in rows]


def _open_prs_at(s: Session, rid: int, at: datetime) -> list[WorkItem]:
    return list(
        s.scalars(
            select(WorkItem)
            .where(
                WorkItem.repository_id == rid,
                WorkItem.kind == WorkItemKind.PULL_REQUEST,
                WorkItem.created_at < at,
                (WorkItem.closed_at.is_(None)) | (WorkItem.closed_at >= at),
            )
            .order_by(WorkItem.created_at)
        )
    )


# ---- shaping -------------------------------------------------------------------------


def _window(start: datetime, end: datetime) -> Window:
    return Window(start=start, end=end, days=round((end - start).total_seconds() / 86400, 2))


def _hours(a: datetime, b: datetime) -> float:
    return max(0.0, (b - a).total_seconds() / 3600)


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    xs = sorted(values)
    k = (len(xs) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return round(xs[lo] + (xs[hi] - xs[lo]) * (k - lo), 2)


def _duration_stats(hours: list[float]) -> DurationStats:
    return DurationStats(median_hours=_percentile(hours, 0.5), p90_hours=_percentile(hours, 0.9), sample=len(hours))


def _top_counts(actors: list[str | None]) -> list[ActorCount]:
    counts: dict[str, int] = defaultdict(int)
    for a in actors:
        if a:
            counts[a] += 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [ActorCount(actor=a, count=n) for a, n in ranked[:LEADERBOARD_SIZE]]


def _lines_changed(merged: list[dict]) -> list[ActorLines]:
    acc: dict[str, dict] = defaultdict(lambda: {"additions": 0, "deletions": 0, "prs": 0})
    for p in merged:
        if not p["author"]:
            continue
        a = acc[p["author"]]
        a["additions"] += p["additions"]
        a["deletions"] += p["deletions"]
        a["prs"] += 1
    rows = [ActorLines(actor=k, additions=v["additions"], deletions=v["deletions"], total=v["additions"] + v["deletions"], prs=v["prs"]) for k, v in acc.items()]
    rows.sort(key=lambda r: (-r.total, r.actor))
    return rows[:LEADERBOARD_SIZE]


def _open_state(open_prs: list[WorkItem], at: datetime) -> OpenState:
    stale_cutoff = at - timedelta(days=STALE_PR_DAYS)
    stale = [p for p in open_prs if ensure_utc(p.created_at) < stale_cutoff]
    oldest = [
        StalePR(number=p.number, title=p.title, author=p.author, age_days=round((at - ensure_utc(p.created_at)).total_seconds() / 86400, 1), url=p.url)
        for p in open_prs[:LEADERBOARD_SIZE]
    ]
    return OpenState(open_prs=len(open_prs), stale_prs=len(stale), stale_threshold_days=STALE_PR_DAYS, oldest_open=oldest)


def _concentration(reviews: list[ReviewerRow], committers: list[ActorCount], total_reviews: int, total_commits: int) -> Concentration:
    def share(n: int, total: int) -> float | None:
        return round(n / total, 4) if total else None

    bus_factor = None
    if total_commits:
        running, bus_factor = 0, 0
        for c in committers:
            running += c.count
            bus_factor += 1
            if running * 2 >= total_commits:
                break
    return Concentration(
        review_top1_share=share(reviews[0].reviews, total_reviews) if reviews else None,
        review_top2_share=share(sum(r.reviews for r in reviews[:2]), total_reviews) if reviews else None,
        commit_top1_share=share(committers[0].count, total_commits) if committers else None,
        commit_bus_factor=bus_factor,
    )


def _weekly(s: Session, rid: int, start: datetime, end: datetime, merged: list[dict]) -> list[WeeklyPoint]:
    """One point per 7-day bucket from `start`. Buckets are anchored to the window start
    rather than calendar weeks so a 30-day query is always 5 comparable points."""
    n_buckets = max(1, -(-int((end - start).total_seconds()) // (7 * 86400)))
    buckets: list[dict] = [
        {"week_start": start + timedelta(days=7 * i), "commits": 0, "prs_opened": 0, "prs_merged": 0, "issues_closed": 0, "reviews": 0, "ttm": []}
        for i in range(n_buckets)
    ]

    def idx(dt: datetime) -> int | None:
        i = int((dt - start).total_seconds() // (7 * 86400))
        return i if 0 <= i < n_buckets else None

    for kind, key in ((ActivityKind.COMMIT, "commits"), (ActivityKind.REVIEW, "reviews")):
        for (occurred_at,) in s.execute(
            select(ActivityEvent.occurred_at).where(
                ActivityEvent.repository_id == rid, ActivityEvent.kind == kind, ActivityEvent.occurred_at >= start, ActivityEvent.occurred_at < end
            )
        ):
            if (i := idx(ensure_utc(occurred_at))) is not None:
                buckets[i][key] += 1

    for (created_at,) in s.execute(
        select(WorkItem.created_at).where(
            WorkItem.repository_id == rid, WorkItem.kind == WorkItemKind.PULL_REQUEST, WorkItem.created_at >= start, WorkItem.created_at < end
        )
    ):
        if (i := idx(ensure_utc(created_at))) is not None:
            buckets[i]["prs_opened"] += 1

    for (closed_at,) in s.execute(
        select(WorkItem.closed_at).where(
            WorkItem.repository_id == rid, WorkItem.kind == WorkItemKind.ISSUE, WorkItem.closed_at >= start, WorkItem.closed_at < end
        )
    ):
        if (i := idx(ensure_utc(closed_at))) is not None:
            buckets[i]["issues_closed"] += 1

    for p in merged:
        if (i := idx(p["merged_at"])) is not None:
            buckets[i]["prs_merged"] += 1
            buckets[i]["ttm"].append(_hours(p["created_at"], p["merged_at"]))

    return [
        WeeklyPoint(
            week_start=b["week_start"],
            commits=b["commits"],
            prs_opened=b["prs_opened"],
            prs_merged=b["prs_merged"],
            issues_closed=b["issues_closed"],
            reviews=b["reviews"],
            median_time_to_merge_hours=_percentile(b["ttm"], 0.5),
        )
        for b in buckets
    ]
