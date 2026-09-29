"""End-to-end through the HTTP layer with the mock model provider."""

import pytest
from fastapi.testclient import TestClient

from loupe.api.app import create_app
from loupe.config import Settings
from loupe.db import session_scope
from loupe.insights import calibration_report
from tests.conftest import seed_acme_widgets


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path}/api.db",
        llm_provider="mock",
        background_sync_enabled=False,
        github_token=None,
        _env_file=None,
    )
    app = create_app(settings)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def tracked(client):
    """Seed the API's database with the shared hand-verifiable dataset."""
    with session_scope() as s:
        seed_acme_widgets(s)
    return "acme", "widgets"


WINDOW = {"from": "2026-03-01", "to": "2026-03-30"}


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_unknown_repo_is_404(client):
    r = client.get("/api/v1/repos/nobody/nothing/metrics")
    assert r.status_code == 404
    assert "not tracked" in r.json()["detail"]


def test_path_validation_rejects_garbage(client):
    assert client.get("/api/v1/repos/a%2Fb/c/metrics").status_code in (404, 422)
    assert client.get("/api/v1/repos/acme/../../etc/metrics").status_code in (404, 422)
    assert client.post("/api/v1/repos", json={"owner": "acme; DROP TABLE", "name": "x"}).status_code == 422


def test_window_validation(client, tracked):
    owner, name = tracked
    r = client.get(f"/api/v1/repos/{owner}/{name}/metrics", params={"from": "2026-03-10", "to": "2026-03-01"})
    assert r.status_code == 422
    r = client.get(f"/api/v1/repos/{owner}/{name}/metrics", params={"from": "2020-01-01", "to": "2026-03-01"})
    assert r.status_code == 422


def test_metrics_endpoint(client, tracked):
    owner, name = tracked
    r = client.get(f"/api/v1/repos/{owner}/{name}/metrics", params=WINDOW)
    assert r.status_code == 200
    body = r.json()
    assert body["totals"]["prs_merged"] == 5
    assert body["totals"]["commits"] == 10
    assert r.headers["X-Loupe-Coverage"] == "1.000"


def test_signals_endpoint(client, tracked):
    owner, name = tracked
    r = client.get(f"/api/v1/repos/{owner}/{name}/signals", params=WINDOW)
    assert r.status_code == 200
    body = r.json()
    assert "cur.totals.prs_merged" in body["facts"]
    assert body["baseline_window"]["end"] == body["window"]["start"]


def test_insights_created_then_cached(client, tracked):
    owner, name = tracked
    first = client.post(f"/api/v1/repos/{owner}/{name}/insights", params=WINDOW)
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["cached"] is False
    assert 0 <= body["confidence"] <= 1
    assert body["verification"]["claims_failed"] == 0
    assert all(e["verified"] for e in body["evidence"])

    second = client.post(f"/api/v1/repos/{owner}/{name}/insights", params=WINDOW)
    assert second.status_code == 200
    assert second.json()["cached"] is True
    assert second.json()["trace_id"] == body["trace_id"]

    third = client.post(f"/api/v1/repos/{owner}/{name}/insights", params={**WINDOW, "refresh": "true"})
    assert third.status_code == 201
    assert third.json()["trace_id"] != body["trace_id"]


def test_llm_traces_recorded(client, tracked):
    owner, name = tracked
    client.post(f"/api/v1/repos/{owner}/{name}/insights", params=WINDOW)
    r = client.get("/api/v1/llm/traces")
    assert r.status_code == 200
    traces = r.json()
    assert traces and traces[0]["gen_ai_system"] == "mock"
    assert traces[0]["purpose"] == "insight"
    assert traces[0]["status"] == "ok"


def test_feedback_is_upserted_and_feeds_calibration(client, tracked):
    owner, name = tracked
    insight = client.post(f"/api/v1/repos/{owner}/{name}/insights", params=WINDOW).json()
    url = f"/api/v1/insights/{insight['trace_id']}/feedback"

    first = client.put(url, json={"verdict": "confirmed"})
    assert first.status_code == 201, first.text
    assert first.json()["confidence"] == insight["confidence"]
    assert first.json()["prompt_version"] == insight["prompt_version"]

    second = client.put(url, json={"verdict": "rejected", "note": "the reviewer was on leave, not overloaded"})
    assert second.status_code == 200
    assert second.json()["verdict"] == "rejected"

    cal = client.get("/api/v1/insights/calibration").json()
    assert (cal["rated"], cal["confirmed"]) == (1, 0)
    assert cal["brier_score"] == pytest.approx(insight["confidence"] ** 2, abs=1e-4)
    assert sum(b["rated"] for b in cal["buckets"]) == 1
    assert client.get("/api/v1/insights/calibration", params={"prompt_version": "no-such-version"}).json()["rated"] == 0


def test_feedback_rejects_unknown_stale_or_invalid(client, tracked):
    owner, name = tracked
    old = client.post(f"/api/v1/repos/{owner}/{name}/insights", params=WINDOW).json()["trace_id"]
    client.post(f"/api/v1/repos/{owner}/{name}/insights", params={**WINDOW, "refresh": "true"})
    assert client.put(f"/api/v1/insights/{old}/feedback", json={"verdict": "confirmed"}).status_code == 404
    assert client.put("/api/v1/insights/not-a-trace/feedback", json={"verdict": "confirmed"}).status_code == 422
    current = client.post(f"/api/v1/repos/{owner}/{name}/insights", params=WINDOW).json()["trace_id"]
    assert client.put(f"/api/v1/insights/{current}/feedback", json={"verdict": "maybe"}).status_code == 422


def test_calibration_buckets_and_brier():
    report = calibration_report([(1.0, True), (0.0, False), (0.5, True), (0.5, False)], "v-test")
    assert [b.rated for b in report.buckets] == [1, 2, 0, 1]
    assert report.buckets[1].hit_rate == 0.5
    assert report.buckets[2].hit_rate is None
    assert report.brier_score == 0.125
    assert calibration_report([], None).brier_score is None


def test_track_repo_returns_202_then_200(client):
    r = client.post("/api/v1/repos", json={"owner": "acme", "name": "widgets"})
    assert r.status_code == 202
    assert r.headers["Location"].endswith("/repos/acme/widgets/sync")
    r = client.post("/api/v1/repos", json={"owner": "acme", "name": "widgets"})
    assert r.status_code == 200
