from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Request, Response, status
from sqlalchemy import select

from loupe.api.deps import DbDep, RepoDep, SettingsDep, WindowDep
from loupe.insights import PROMPT_VERSION, InsightParseError, calibration_report, synthesize
from loupe.llm.base import LLMError
from loupe.metrics import compute_metrics
from loupe.models import FeedbackVerdict, Insight, InsightFeedback, LlmTrace, Repository, SyncStatus
from loupe.schemas import (
    CalibrationReport,
    FeedbackIn,
    FeedbackOut,
    InsightOut,
    LlmTraceOut,
    MetricsReport,
    RepoOut,
    SignalsReport,
    SyncStateOut,
    TrackedRepoOut,
    TrackRepoIn,
)
from loupe.signals import compute_signals
from loupe.sync import get_or_create_repository
from loupe.timeutil import ensure_utc

router = APIRouter(prefix="/api/v1")


def _tracked(repo: Repository) -> TrackedRepoOut:
    st = repo.sync_state
    return TrackedRepoOut(
        repository=RepoOut(source=repo.source, owner=repo.owner, name=repo.name, default_branch=repo.default_branch),
        sync=SyncStateOut(
            status=st.status,
            backfill_from=ensure_utc(st.backfill_from),
            work_items_synced_to=ensure_utc(st.work_items_synced_to),
            commits_synced_to=ensure_utc(st.commits_synced_to),
            last_started_at=ensure_utc(st.last_started_at),
            last_finished_at=ensure_utc(st.last_finished_at),
            last_error=st.last_error,
            last_run_stats=st.last_run_stats,
        ),
    )


# ---- repositories ---------------------------------------------------------------------


@router.get("/repos", response_model=list[TrackedRepoOut], summary="List tracked repositories")
def list_repos(db: DbDep):
    return [_tracked(r) for r in db.scalars(select(Repository).order_by(Repository.owner, Repository.name))]


@router.post(
    "/repos",
    response_model=TrackedRepoOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Track a repository and start syncing it",
    responses={202: {"description": "Sync scheduled"}, 200: {"description": "Already tracked; sync re-scheduled"}},
)
def track_repo(body: TrackRepoIn, request: Request, response: Response, db: DbDep):
    repo, created = get_or_create_repository(db, body.source, body.owner, body.name)
    db.commit()
    request.app.state.sync_manager.schedule(repo.id)
    response.status_code = status.HTTP_202_ACCEPTED if created else status.HTTP_200_OK
    response.headers["Location"] = f"/api/v1/repos/{repo.owner}/{repo.name}/sync"
    return _tracked(repo)


@router.get("/repos/{owner}/{name}/sync", response_model=TrackedRepoOut, summary="Sync status")
def sync_status(repo: RepoDep):
    return _tracked(repo)


@router.post("/repos/{owner}/{name}/sync", response_model=TrackedRepoOut, status_code=status.HTTP_202_ACCEPTED, summary="Trigger a sync now")
def trigger_sync(repo: RepoDep, request: Request):
    request.app.state.sync_manager.schedule(repo.id)
    return _tracked(repo)


# ---- insights ---------------------------------------------------------------------------


@router.get("/repos/{owner}/{name}/metrics", response_model=MetricsReport, summary="Metrics over a window")
def metrics(repo: RepoDep, window: WindowDep, db: DbDep, response: Response):
    report = compute_metrics(db, repo, window.start, window.end)
    _coverage_headers(response, report.coverage.covered_ratio, repo)
    return report


@router.get("/repos/{owner}/{name}/signals", response_model=SignalsReport, summary="Detected signals over a window vs the preceding window")
def signals(repo: RepoDep, window: WindowDep, db: DbDep, response: Response):
    report = compute_signals(db, repo, window.start, window.end)
    _coverage_headers(response, report.coverage.covered_ratio, repo)
    return report


@router.post(
    "/repos/{owner}/{name}/insights",
    response_model=InsightOut,
    summary="LLM narrative with verified evidence chain",
    description="POST because it triggers a paid model call. Results are cached per (window, prompt version, model); "
    "pass refresh=true to regenerate. 201 on a fresh synthesis, 200 on a cache hit.",
    responses={201: {"description": "Fresh insight generated"}, 200: {"description": "Served from cache"}, 502: {"description": "Model unavailable or returned an unusable response"}},
)
async def insights(
    repo: RepoDep,
    window: WindowDep,
    db: DbDep,
    settings: SettingsDep,
    request: Request,
    response: Response,
    refresh: Annotated[bool, Query()] = False,
):
    provider = request.app.state.llm_provider
    key = dict(repository_id=repo.id, window_start=window.start, window_end=window.end, prompt_version=PROMPT_VERSION, model=provider.model)
    if not refresh:
        cached = db.scalar(select(Insight).filter_by(**key))
        if cached:
            response.status_code = status.HTTP_200_OK
            return InsightOut.model_validate({**cached.body, "cached": True})

    report = compute_signals(db, repo, window.start, window.end)
    try:
        insight = await synthesize(report, provider, max_tokens=settings.llm_max_tokens)
    except InsightParseError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"model returned an unusable response: {exc}") from exc
    except LLMError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"model call failed: {exc}") from exc

    body = insight.model_dump(mode="json")
    existing = db.scalar(select(Insight).filter_by(**key))
    if existing:
        existing.body = body
    else:
        db.add(Insight(**key, body=body))
    db.commit()
    response.status_code = status.HTTP_201_CREATED
    _coverage_headers(response, report.coverage.covered_ratio, repo)
    return insight


TraceIdParam = Annotated[str, Path(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", description="trace_id of the insight")]


@router.put(
    "/insights/{trace_id}/feedback",
    response_model=FeedbackOut,
    summary="Record whether an insight was right",
    description="One verdict per narrative; PUT again to change it. 201 on the first verdict, 200 on an update.",
    responses={201: {"description": "Verdict recorded"}, 200: {"description": "Verdict updated"}, 404: {"description": "No current insight has this trace id"}},
)
def insight_feedback(trace_id: TraceIdParam, body: FeedbackIn, db: DbDep, response: Response):
    insight = db.scalar(select(Insight).where(Insight.body["trace_id"].as_string() == trace_id))
    if insight is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no current insight has this trace id (unknown, or superseded by a refresh)")
    feedback = db.scalar(select(InsightFeedback).filter_by(trace_id=trace_id))
    created = feedback is None
    if created:
        feedback = InsightFeedback(
            repository_id=insight.repository_id,
            trace_id=trace_id,
            confidence=insight.body["confidence"],
            prompt_version=insight.prompt_version,
            model=insight.model,
        )
        db.add(feedback)
    feedback.verdict = body.verdict
    feedback.note = body.note
    db.commit()
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    out = FeedbackOut.model_validate(feedback)
    return out.model_copy(update={"created_at": ensure_utc(out.created_at), "updated_at": ensure_utc(out.updated_at)})


@router.get("/insights/calibration", response_model=CalibrationReport, summary="Displayed confidence vs human verdicts")
def insight_calibration(
    db: DbDep,
    prompt_version: Annotated[str, Query(pattern=r"^[A-Za-z0-9._-]{1,32}$", description="Defaults to the current prompt; `all` pools every version")] = PROMPT_VERSION,
):
    query = select(InsightFeedback.confidence, InsightFeedback.verdict)
    if prompt_version != "all":
        query = query.where(InsightFeedback.prompt_version == prompt_version)
    ratings = ((conf, verdict == FeedbackVerdict.CONFIRMED) for conf, verdict in db.execute(query))
    return calibration_report(ratings, None if prompt_version == "all" else prompt_version)


# ---- observability ----------------------------------------------------------------------


@router.get("/llm/traces", response_model=list[LlmTraceOut], summary="Recent model calls (OTel gen_ai.* attributes)")
def llm_traces(db: DbDep, limit: Annotated[int, Query(ge=1, le=200)] = 50):
    return list(db.scalars(select(LlmTrace).order_by(LlmTrace.created_at.desc()).limit(limit)))


def _coverage_headers(response: Response, covered_ratio: float, repo: Repository) -> None:
    response.headers["X-Loupe-Coverage"] = f"{covered_ratio:.3f}"
    if repo.sync_state and repo.sync_state.status in (SyncStatus.PENDING, SyncStatus.RUNNING):
        response.headers["X-Loupe-Sync-Status"] = repo.sync_state.status
        response.headers["Warning"] = '110 - "sync in progress; numbers may be incomplete"'
