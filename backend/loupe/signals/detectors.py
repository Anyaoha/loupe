"""Signal detection: turns two MetricsReports (current window, preceding baseline window)
into a short list of things worth a human's attention.

Design notes
  * Baseline = the period of equal length immediately before the window. Simple to explain,
    no hidden seasonality model. The trade-off (holiday weeks look like drift) is called out
    in NOTES.md.
  * Every signal points at fact ids in the facts table and at the concrete PR / issue numbers
    behind it. Nothing is asserted that cannot be clicked through to.
  * Thresholds are module constants, not magic numbers scattered through the code, so they
    are the first thing a reviewer sees and the easiest thing to argue about.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from loupe.metrics import compute_metrics
from loupe.models import ActivityEvent, ActivityKind, Repository, WorkItem, WorkItemKind
from loupe.schemas import Fact, MetricsReport, Signal, SignalsReport

# ---- thresholds ------------------------------------------------------------------------

MIN_REVIEWS_FOR_CONCENTRATION = 10
REVIEW_TOP1_WARN, REVIEW_TOP1_CRIT = 0.50, 0.70

MIN_SAMPLE_FOR_DURATION_DRIFT = 5
DURATION_RATIO_WARN, DURATION_RATIO_CRIT, DURATION_RATIO_IMPROVED = 1.5, 2.5, 0.5

MIN_BASELINE_FOR_VELOCITY = 5
VELOCITY_DROP_WARN, VELOCITY_DROP_CRIT, VELOCITY_RISE_INFO = -0.40, -0.60, 0.60

MIN_MERGED_FOR_UNREVIEWED = 8
UNREVIEWED_WARN, UNREVIEWED_CRIT = 0.25, 0.50

STALE_WARN, STALE_CRIT = 5, 15

MIN_COMMITS_FOR_BUS_FACTOR = 20

MIN_BASELINE_COMMITTERS = 5
COMMITTER_DROP_WARN = -0.40

TOP_N_FACTS = 5
RELATED_ITEMS = 8

SEVERITY_RANK = {"critical": 0, "warning": 1, "info": 2}


@dataclass
class Context:
    session: Session
    repo: Repository
    start: datetime
    end: datetime
    base_start: datetime
    cur: MetricsReport
    base: MetricsReport
    facts: dict[str, Fact]


def compute_signals(s: Session, repo: Repository, start: datetime, end: datetime) -> SignalsReport:
    base_start = start - (end - start)
    cur = compute_metrics(s, repo, start, end)
    base = compute_metrics(s, repo, base_start, start)
    facts = build_facts(cur, base)
    ctx = Context(session=s, repo=repo, start=start, end=end, base_start=base_start, cur=cur, base=base, facts=facts)

    signals = [sig for det in DETECTORS if (sig := det(ctx)) is not None]
    signals.sort(key=lambda x: (SEVERITY_RANK[x.severity], x.id))
    return SignalsReport(
        repository=cur.repository,
        window=cur.window,
        baseline_window=base.window,
        coverage=cur.coverage,
        signals=signals,
        facts=facts,
    )


# ---- facts ---------------------------------------------------------------------------


def build_facts(cur: MetricsReport, base: MetricsReport) -> dict[str, Fact]:
    """Flat, id-addressable view of the numbers. The LLM must cite these ids; the verifier
    checks every citation against this table."""
    facts: dict[str, Fact] = {}

    def add(fid: str, label: str, value, unit: str | None = None):
        facts[fid] = Fact(label=label, value=value, unit=unit)

    for prefix, m in (("cur", cur), ("base", base)):
        t, f, o, c = m.totals, m.flow, m.open_state, m.concentration
        add(f"{prefix}.totals.commits", "commits to default branch", t.commits)
        add(f"{prefix}.totals.prs_opened", "PRs opened", t.prs_opened)
        add(f"{prefix}.totals.prs_merged", "PRs merged", t.prs_merged)
        add(f"{prefix}.totals.prs_closed_unmerged", "PRs closed without merge", t.prs_closed_unmerged)
        add(f"{prefix}.totals.issues_opened", "issues opened", t.issues_opened)
        add(f"{prefix}.totals.issues_closed", "issues closed", t.issues_closed)
        add(f"{prefix}.totals.reviews", "reviews submitted", t.reviews)
        add(f"{prefix}.totals.active_committers", "distinct committers", t.active_committers)
        add(f"{prefix}.totals.active_reviewers", "distinct reviewers", t.active_reviewers)
        add(f"{prefix}.flow.time_to_merge_median", "median time to merge", f.time_to_merge.median_hours, "hours")
        add(f"{prefix}.flow.time_to_merge_p90", "p90 time to merge", f.time_to_merge.p90_hours, "hours")
        add(f"{prefix}.flow.issue_turnaround_median", "median issue turnaround", f.issue_turnaround.median_hours, "hours")
        add(f"{prefix}.flow.merged_without_review", "PRs merged with zero reviews", f.merged_without_review)
        add(f"{prefix}.flow.merged_without_review_ratio", "share of merged PRs with zero reviews", f.merged_without_review_ratio, "ratio")
        add(f"{prefix}.open.open_prs", "PRs open at window end", o.open_prs)
        add(f"{prefix}.open.stale_prs", f"open PRs older than {o.stale_threshold_days} days at window end", o.stale_prs)
        add(f"{prefix}.concentration.review_top1_share", "share of reviews by the single busiest reviewer", c.review_top1_share, "ratio")
        add(f"{prefix}.concentration.review_top2_share", "share of reviews by the two busiest reviewers", c.review_top2_share, "ratio")
        add(f"{prefix}.concentration.commit_top1_share", "share of commits by the single busiest committer", c.commit_top1_share, "ratio")
        add(f"{prefix}.concentration.commit_bus_factor", "fewest committers covering 50% of commits", c.commit_bus_factor)

    for r in cur.leaderboards.reviewers[:TOP_N_FACTS]:
        add(f"cur.reviewer.{r.actor}.reviews", f"reviews by {r.actor}", r.reviews)
        add(f"cur.reviewer.{r.actor}.approvals", f"approvals by {r.actor}", r.approvals)
    for cmt in cur.leaderboards.committers[:TOP_N_FACTS]:
        add(f"cur.committer.{cmt.actor}.commits", f"commits by {cmt.actor}", cmt.count)
    for a in cur.leaderboards.pr_authors[:TOP_N_FACTS]:
        add(f"cur.pr_author.{a.actor}.prs_merged", f"PRs merged authored by {a.actor}", a.count)
    for mg in cur.leaderboards.mergers[:TOP_N_FACTS]:
        add(f"cur.merger.{mg.actor}.prs_merged", f"PRs merged by {mg.actor}", mg.count)
    for cl in cur.leaderboards.closers[:TOP_N_FACTS]:
        add(f"cur.closer.{cl.actor}.closed", f"issues and unmerged PRs closed by {cl.actor}", cl.count)
    for i, w in enumerate(cur.weekly, start=1):
        add(f"cur.week.{i}.prs_merged", f"PRs merged in week {i} of window", w.prs_merged)
        add(f"cur.week.{i}.commits", f"commits in week {i} of window", w.commits)
        add(f"cur.week.{i}.time_to_merge_median", f"median time to merge in week {i}", w.median_time_to_merge_hours, "hours")
    add("cur.coverage.covered_ratio", "fraction of window covered by synced data", cur.coverage.covered_ratio, "ratio")
    add("cur.window.days", "window length", cur.window.days, "days")
    return facts


# ---- detectors -----------------------------------------------------------------------


def _pct_change(cur: float | None, base: float | None) -> float | None:
    if cur is None or base is None or base == 0:
        return None
    return (cur - base) / base


def _severity_from_ratio(ratio: float, warn: float, crit: float) -> str | None:
    if ratio >= crit:
        return "critical"
    if ratio >= warn:
        return "warning"
    return None


def review_concentration(ctx: Context) -> Signal | None:
    c, t = ctx.cur.concentration, ctx.cur.totals
    if t.reviews < MIN_REVIEWS_FOR_CONCENTRATION or c.review_top1_share is None:
        return None
    sev = _severity_from_ratio(c.review_top1_share, REVIEW_TOP1_WARN, REVIEW_TOP1_CRIT)
    if sev is None:
        return None
    top = ctx.cur.leaderboards.reviewers[0]
    prs = ctx.session.scalars(
        select(ActivityEvent.work_item_number)
        .where(
            ActivityEvent.repository_id == ctx.repo.id,
            ActivityEvent.kind == ActivityKind.REVIEW,
            ActivityEvent.actor == top.actor,
            ActivityEvent.occurred_at >= ctx.start,
            ActivityEvent.occurred_at < ctx.end,
            ActivityEvent.work_item_number.is_not(None),
        )
        .distinct()
        .limit(RELATED_ITEMS)
    ).all()
    return Signal(
        id="review_concentration",
        kind="review_concentration",
        severity=sev,
        title=f"{top.actor} handled {c.review_top1_share:.0%} of all reviews",
        summary=f"{top.actor} submitted {top.reviews} of {t.reviews} reviews. A single absence stalls review throughput.",
        value=c.review_top1_share,
        baseline=ctx.base.concentration.review_top1_share,
        direction="up" if (ctx.base.concentration.review_top1_share or 0) < c.review_top1_share else "flat",
        evidence_refs=["cur.concentration.review_top1_share", f"cur.reviewer.{top.actor}.reviews", "cur.totals.reviews", "base.concentration.review_top1_share"],
        related_items=list(prs),
    )


def time_to_merge_drift(ctx: Context) -> Signal | None:
    cur, base = ctx.cur.flow.time_to_merge, ctx.base.flow.time_to_merge
    if cur.sample < MIN_SAMPLE_FOR_DURATION_DRIFT or base.sample < MIN_SAMPLE_FOR_DURATION_DRIFT or not base.median_hours:
        return None
    ratio = cur.median_hours / base.median_hours
    sev = _severity_from_ratio(ratio, DURATION_RATIO_WARN, DURATION_RATIO_CRIT)
    if sev is None and ratio > DURATION_RATIO_IMPROVED:
        return None
    merged_rows = ctx.session.execute(
        select(WorkItem.number, WorkItem.created_at, WorkItem.merged_at).where(
            WorkItem.repository_id == ctx.repo.id,
            WorkItem.kind == WorkItemKind.PULL_REQUEST,
            WorkItem.merged_at >= ctx.start,
            WorkItem.merged_at < ctx.end,
        )
    ).all()
    slowest = [r.number for r in sorted(merged_rows, key=lambda r: r.merged_at - r.created_at, reverse=True)[:RELATED_ITEMS]]
    improved = sev is None
    return Signal(
        id="time_to_merge_drift",
        kind="time_to_merge_drift",
        severity="info" if improved else sev,
        title=f"Median time to merge {'fell' if improved else 'rose'} {abs(ratio - 1):.0%} vs the previous period",
        summary=f"Median {cur.median_hours:.0f}h now vs {base.median_hours:.0f}h before (n={cur.sample} vs {base.sample}). p90 is {cur.p90_hours:.0f}h.",
        value=cur.median_hours,
        baseline=base.median_hours,
        direction="down" if improved else "up",
        evidence_refs=["cur.flow.time_to_merge_median", "base.flow.time_to_merge_median", "cur.flow.time_to_merge_p90", "cur.totals.prs_merged"],
        related_items=slowest,
    )


def issue_turnaround_drift(ctx: Context) -> Signal | None:
    cur, base = ctx.cur.flow.issue_turnaround, ctx.base.flow.issue_turnaround
    if cur.sample < MIN_SAMPLE_FOR_DURATION_DRIFT or base.sample < MIN_SAMPLE_FOR_DURATION_DRIFT or not base.median_hours:
        return None
    ratio = cur.median_hours / base.median_hours
    sev = _severity_from_ratio(ratio, DURATION_RATIO_WARN, DURATION_RATIO_CRIT)
    if sev is None:
        return None
    return Signal(
        id="issue_turnaround_drift",
        kind="issue_turnaround_drift",
        severity=sev,
        title=f"Issues are taking {ratio:.1f}x longer to close",
        summary=f"Median issue turnaround {cur.median_hours:.0f}h vs {base.median_hours:.0f}h in the previous period.",
        value=cur.median_hours,
        baseline=base.median_hours,
        direction="up",
        evidence_refs=["cur.flow.issue_turnaround_median", "base.flow.issue_turnaround_median", "cur.totals.issues_closed"],
    )


def velocity_change(ctx: Context) -> Signal | None:
    cur, base = ctx.cur.totals.prs_merged, ctx.base.totals.prs_merged
    if base < MIN_BASELINE_FOR_VELOCITY:
        return None
    change = _pct_change(cur, base)
    if change is None:
        return None
    if change <= VELOCITY_DROP_CRIT:
        sev = "critical"
    elif change <= VELOCITY_DROP_WARN:
        sev = "warning"
    elif change >= VELOCITY_RISE_INFO:
        sev = "info"
    else:
        return None
    weeks = [w.prs_merged for w in ctx.cur.weekly]
    return Signal(
        id="velocity_change",
        kind="velocity_change",
        severity=sev,
        title=f"Merge throughput {'down' if change < 0 else 'up'} {abs(change):.0%} vs the previous period",
        summary=f"{cur} PRs merged vs {base} before. Weekly merges this period: {weeks}.",
        value=float(cur),
        baseline=float(base),
        direction="down" if change < 0 else "up",
        evidence_refs=["cur.totals.prs_merged", "base.totals.prs_merged", "cur.totals.commits", "base.totals.commits"]
        + [f"cur.week.{i}.prs_merged" for i in range(1, len(weeks) + 1)],
    )


def unreviewed_merges(ctx: Context) -> Signal | None:
    f, t = ctx.cur.flow, ctx.cur.totals
    if t.prs_merged < MIN_MERGED_FOR_UNREVIEWED or f.merged_without_review_ratio is None:
        return None
    sev = _severity_from_ratio(f.merged_without_review_ratio, UNREVIEWED_WARN, UNREVIEWED_CRIT)
    if sev is None:
        return None
    numbers = ctx.session.scalars(
        select(WorkItem.number)
        .where(
            WorkItem.repository_id == ctx.repo.id,
            WorkItem.kind == WorkItemKind.PULL_REQUEST,
            WorkItem.merged_at >= ctx.start,
            WorkItem.merged_at < ctx.end,
            WorkItem.review_count == 0,
        )
        .order_by(WorkItem.merged_at.desc())
        .limit(RELATED_ITEMS)
    ).all()
    return Signal(
        id="unreviewed_merges",
        kind="unreviewed_merges",
        severity=sev,
        title=f"{f.merged_without_review_ratio:.0%} of merged PRs had no review",
        summary=f"{f.merged_without_review} of {t.prs_merged} merged PRs recorded zero reviews.",
        value=f.merged_without_review_ratio,
        baseline=ctx.base.flow.merged_without_review_ratio,
        direction="up" if (ctx.base.flow.merged_without_review_ratio or 0) < f.merged_without_review_ratio else "flat",
        evidence_refs=["cur.flow.merged_without_review", "cur.flow.merged_without_review_ratio", "cur.totals.prs_merged", "base.flow.merged_without_review_ratio"],
        related_items=list(numbers),
    )


def stale_pr_backlog(ctx: Context) -> Signal | None:
    o = ctx.cur.open_state
    if o.stale_prs >= STALE_CRIT:
        sev = "critical"
    elif o.stale_prs >= STALE_WARN:
        sev = "warning"
    else:
        return None
    return Signal(
        id="stale_pr_backlog",
        kind="stale_pr_backlog",
        severity=sev,
        title=f"{o.stale_prs} PRs have been open for more than {o.stale_threshold_days} days",
        summary=f"{o.stale_prs} of {o.open_prs} open PRs are older than {o.stale_threshold_days} days. Oldest is {o.oldest_open[0].age_days:.0f} days." if o.oldest_open else "",
        value=float(o.stale_prs),
        baseline=float(ctx.base.open_state.stale_prs),
        direction="up" if o.stale_prs > ctx.base.open_state.stale_prs else "flat",
        evidence_refs=["cur.open.stale_prs", "cur.open.open_prs", "base.open.stale_prs"],
        related_items=[p.number for p in o.oldest_open[:RELATED_ITEMS]],
    )


def commit_bus_factor(ctx: Context) -> Signal | None:
    c, t = ctx.cur.concentration, ctx.cur.totals
    if t.commits < MIN_COMMITS_FOR_BUS_FACTOR or c.commit_bus_factor != 1:
        return None
    top = ctx.cur.leaderboards.committers[0]
    return Signal(
        id="commit_bus_factor",
        kind="commit_bus_factor",
        severity="warning",
        title=f"Bus factor of 1: {top.actor} authored {c.commit_top1_share:.0%} of commits",
        summary=f"{top.actor} made {top.count} of {t.commits} commits to the default branch.",
        value=c.commit_top1_share,
        baseline=ctx.base.concentration.commit_top1_share,
        direction="up" if (ctx.base.concentration.commit_top1_share or 0) < (c.commit_top1_share or 0) else "flat",
        evidence_refs=["cur.concentration.commit_bus_factor", "cur.concentration.commit_top1_share", f"cur.committer.{top.actor}.commits", "cur.totals.commits"],
    )


def committer_churn(ctx: Context) -> Signal | None:
    cur_n, base_n = ctx.cur.totals.active_committers, ctx.base.totals.active_committers
    if base_n < MIN_BASELINE_COMMITTERS:
        return None
    change = _pct_change(cur_n, base_n)
    if change is None or change > COMMITTER_DROP_WARN:
        return None
    cur_actors = set(_committer_logins(ctx, ctx.start, ctx.end))
    base_actors = set(_committer_logins(ctx, ctx.base_start, ctx.start))
    gone = sorted(base_actors - cur_actors)
    ctx.facts["cur.committers.absent_vs_baseline"] = Fact(label="baseline committers with no commits this period", value=", ".join(gone) or "none")
    return Signal(
        id="committer_churn",
        kind="committer_churn",
        severity="warning" if change > -0.6 else "critical",
        title=f"Active committers down {abs(change):.0%}: {cur_n} vs {base_n}",
        summary=f"{len(gone)} people who committed in the previous period have no commits in this one: {', '.join(gone[:6])}{f' and {len(gone) - 6} more' if len(gone) > 6 else ''}.",
        value=float(cur_n),
        baseline=float(base_n),
        direction="down",
        evidence_refs=["cur.totals.active_committers", "base.totals.active_committers", "cur.committers.absent_vs_baseline"],
    )


def _committer_logins(ctx: Context, start: datetime, end: datetime) -> list[str]:
    return list(
        ctx.session.scalars(
            select(ActivityEvent.actor)
            .where(
                ActivityEvent.repository_id == ctx.repo.id,
                ActivityEvent.kind == ActivityKind.COMMIT,
                ActivityEvent.occurred_at >= start,
                ActivityEvent.occurred_at < end,
                ActivityEvent.actor.is_not(None),
            )
            .distinct()
        )
    )


DETECTORS = (
    velocity_change,
    committer_churn,
    time_to_merge_drift,
    review_concentration,
    unreviewed_merges,
    stale_pr_backlog,
    commit_bus_factor,
    issue_turnaround_drift,
)
