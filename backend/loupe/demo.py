"""Synthetic demo dataset so the service is useful with no GitHub token.

`python -m loupe.cli seed-demo` creates `demo/sample-service`: 150 days of activity for a
seven-person team where, in the last five weeks, two engineers stop committing, the tech
lead absorbs most reviews, and merge times stretch. Deterministic (fixed seed) so the
numbers in the README stay true.
"""

import random
from datetime import timedelta

from sqlalchemy.orm import Session

from loupe.models import ActivityEvent, ActivityKind, Repository, ReviewState, SyncState, SyncStatus, WorkItem, WorkItemKind
from loupe.sync import get_or_create_repository
from loupe.timeutil import utcnow

DEMO_OWNER, DEMO_NAME = "demo", "sample-service"
TEAM = ["priya", "marcus", "lena", "tomas", "aisha", "kenji", "sofia"]
LEAD = "priya"
DEPARTED = {"kenji", "sofia"}
DAYS = 150
DRIFT_START_DAY = DAYS - 35


def seed_demo(s: Session) -> Repository:
    repo, created = get_or_create_repository(s, "github", DEMO_OWNER, DEMO_NAME)
    if not created:
        return repo
    rng = random.Random(7)
    now = utcnow().replace(minute=0, second=0, microsecond=0)
    t0 = now - timedelta(days=DAYS)
    repo.default_branch = "main"
    synced = utcnow()
    repo.sync_state = SyncState(status=SyncStatus.OK, backfill_from=t0, work_items_synced_to=synced, commits_synced_to=synced, last_finished_at=synced, last_run_stats={"note": "synthetic demo data"})

    items: list[WorkItem] = []
    events: list[ActivityEvent] = []
    pr_no, issue_no, sha = 0, 0, 0

    for day in range(DAYS):
        drift = day >= DRIFT_START_DAY
        active = [p for p in TEAM if not (drift and p in DEPARTED)]
        date = t0 + timedelta(days=day)
        if date.weekday() >= 5:
            continue

        for _ in range(rng.randint(2, 4)):
            sha += 1
            events.append(ActivityEvent(repository_id=repo.id, kind=ActivityKind.COMMIT, external_id=f"{sha:07x}", actor=rng.choice(active), occurred_at=date + timedelta(hours=rng.randint(9, 18))))

        for _ in range(rng.choices([0, 1, 2], weights=[2, 5, 3])[0]):
            pr_no += 1
            author = rng.choice(active)
            opened = date + timedelta(hours=rng.randint(9, 17))
            ttm_hours = rng.lognormvariate(3.9, 0.5) if drift else rng.lognormvariate(2.9, 0.5)
            merged = opened + timedelta(hours=ttm_hours)
            reviewers = [p for p in active if p != author]
            n_reviews = 0 if (drift and rng.random() < 0.3) else rng.randint(1, 2)
            for _ in range(n_reviews):
                reviewer = LEAD if (drift and rng.random() < 0.7) else rng.choice(reviewers)
                events.append(ActivityEvent(
                    repository_id=repo.id, kind=ActivityKind.REVIEW, external_id=f"rev-{pr_no}-{len(events)}", actor=reviewer,
                    occurred_at=opened + timedelta(hours=rng.uniform(1, max(2, ttm_hours - 1))), work_item_number=pr_no,
                    review_state=rng.choice([ReviewState.APPROVED, ReviewState.APPROVED, ReviewState.CHANGES_REQUESTED]), comment_count=rng.randint(0, 4),
                ))
            still_open = merged > now
            items.append(WorkItem(
                repository_id=repo.id, kind=WorkItemKind.PULL_REQUEST, number=pr_no, title=f"{rng.choice(['feat', 'fix', 'chore', 'refactor'])}: {rng.choice(['billing', 'auth', 'ingest', 'search', 'notifications'])} #{pr_no}",
                author=author, url=f"https://github.com/{DEMO_OWNER}/{DEMO_NAME}/pull/{pr_no}", created_at=opened, updated_at=min(merged, now),
                closed_at=None if still_open else merged, merged_at=None if still_open else merged, merged_by=None if still_open else (LEAD if drift else rng.choice(reviewers)),
                additions=rng.randint(5, 400), deletions=rng.randint(0, 150), changed_files=rng.randint(1, 12), review_count=n_reviews,
            ))

        if drift and rng.random() < 0.25:
            pr_no += 1
            opened = date + timedelta(hours=10)
            items.append(WorkItem(repository_id=repo.id, kind=WorkItemKind.PULL_REQUEST, number=pr_no, title=f"wip: {rng.choice(['migration', 'flaky test', 'perf'])} #{pr_no}", author=rng.choice(active),
                                  url=f"https://github.com/{DEMO_OWNER}/{DEMO_NAME}/pull/{pr_no}", created_at=opened, updated_at=opened, additions=rng.randint(50, 900), deletions=rng.randint(0, 200), changed_files=rng.randint(3, 20)))

        if rng.random() < 0.5:
            issue_no += 1
            opened = date + timedelta(hours=rng.randint(8, 18))
            turnaround = rng.lognormvariate(4.6, 0.6) if drift else rng.lognormvariate(3.9, 0.6)
            closed = opened + timedelta(hours=turnaround)
            items.append(WorkItem(repository_id=repo.id, kind=WorkItemKind.ISSUE, number=10000 + issue_no, title=f"bug: {rng.choice(['timeout', '500 on save', 'wrong total'])} #{issue_no}", author=rng.choice(TEAM),
                                  url=f"https://github.com/{DEMO_OWNER}/{DEMO_NAME}/issues/{10000 + issue_no}", created_at=opened, updated_at=min(closed, now), closed_at=None if closed > now else closed))

    s.add_all(items)
    s.add_all(events)
    s.flush()
    return repo
