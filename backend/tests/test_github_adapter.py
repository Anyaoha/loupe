"""GitHub adapter against a mocked GraphQL endpoint: pagination, high-water-mark stop, and
error mapping. No network."""

from datetime import UTC, datetime

import httpx
import pytest
import respx

from loupe.adapters import github as github_module
from loupe.adapters.base import AdapterError, AuthError, RateLimited, RepoNotFound, RepoRef
from loupe.adapters.github import GitHubAdapter, _issue_to_record

URL = "https://api.github.test/graphql"
REPO = RepoRef(owner="acme", name="widgets")


def _pr(number: int, updated: str, reviews=()):
    return {
        "number": number, "title": f"PR {number}", "url": f"https://github.test/acme/widgets/pull/{number}",
        "createdAt": "2026-03-01T00:00:00Z", "updatedAt": updated, "closedAt": None, "mergedAt": None,
        "additions": 10, "deletions": 2, "changedFiles": 1,
        "author": {"login": "alice"}, "mergedBy": None, "labels": {"nodes": []},
        "reviews": {"totalCount": len(reviews), "nodes": [
            {"id": f"R{number}-{i}", "state": st, "submittedAt": "2026-03-02T00:00:00Z", "author": {"login": who}, "comments": {"totalCount": 1}}
            for i, (who, st) in enumerate(reviews)
        ]},
    }


def _page(connection: str, nodes: list, has_next: bool, cursor: str | None):
    return {"data": {"repository": {connection: {"pageInfo": {"hasNextPage": has_next, "endCursor": cursor}, "nodes": nodes}}}}


@pytest.fixture
def adapter():
    a = GitHubAdapter(token="test-token", graphql_url=URL, client=httpx.AsyncClient())
    yield a


@respx.mock
async def test_paginates_prs_and_stops_at_watermark(adapter):
    calls = []

    def responder(request: httpx.Request):
        body = request.read().decode()
        calls.append(body)
        assert "Authorization" in request.headers and request.headers["Authorization"].startswith("bearer ")
        if "pullRequests" in body:
            if '"after":null' in body.replace(" ", ""):
                return httpx.Response(200, json=_page("pullRequests", [_pr(3, "2026-03-20T00:00:00Z", [("bob", "APPROVED")]), _pr(2, "2026-03-15T00:00:00Z")], True, "c1"))
            return httpx.Response(200, json=_page("pullRequests", [_pr(1, "2026-02-01T00:00:00Z")], True, "c2"))
        if "issues" in body:
            return httpx.Response(200, json=_page("issues", [], False, None))
        raise AssertionError("unexpected query")

    respx.post(URL).mock(side_effect=responder)

    items = [x async for x in adapter.iter_work_items(REPO, updated_since=datetime(2026, 3, 10, tzinfo=UTC))]
    numbers = [item.number for item, _ in items]
    assert numbers == [3, 2]  # PR 1 is older than the watermark: iteration stops, no third page is fetched
    assert items[0][1][0].actor == "bob" and items[0][1][0].review_state == "approved"
    assert sum("pullRequests" in c for c in calls) == 2
    assert sum('"issues"' in c or "issues(" in c for c in calls) == 1


@respx.mock
async def test_repo_not_found_maps_to_typed_error(adapter):
    respx.post(URL).mock(return_value=httpx.Response(200, json={"data": {"repository": None}, "errors": [{"type": "NOT_FOUND", "message": "Could not resolve"}]}))
    with pytest.raises(RepoNotFound):
        await adapter.get_repo_info(REPO)


@respx.mock
async def test_bad_token_maps_to_auth_error(adapter):
    respx.post(URL).mock(return_value=httpx.Response(401, json={"message": "Bad credentials"}))
    with pytest.raises(AuthError):
        await adapter.get_repo_info(REPO)


@respx.mock
async def test_rate_limit_is_surfaced_with_reset(adapter):
    respx.post(URL).mock(return_value=httpx.Response(403, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1780000000"}, json={}))
    with pytest.raises(RateLimited) as exc:
        await adapter.get_repo_info(REPO)
    assert exc.value.reset_at is not None


@respx.mock
async def test_gateway_timeout_is_retried_then_succeeds(adapter, monkeypatch):
    monkeypatch.setattr(github_module, "RETRY_BACKOFF_SECONDS", 0)
    route = respx.post(URL).mock(side_effect=[
        httpx.Response(504),
        httpx.Response(502),
        httpx.Response(200, json={"data": {"repository": {"defaultBranchRef": {"name": "main"}}}}),
    ])
    info = await adapter.get_repo_info(REPO)
    assert info.default_branch == "main"
    assert route.call_count == 3


@respx.mock
async def test_gateway_timeout_gives_up_after_max_attempts(adapter, monkeypatch):
    monkeypatch.setattr(github_module, "RETRY_BACKOFF_SECONDS", 0)
    route = respx.post(URL).mock(return_value=httpx.Response(504))
    with pytest.raises(AdapterError, match="HTTP 504"):
        await adapter.get_repo_info(REPO)
    assert route.call_count == github_module.MAX_ATTEMPTS


@respx.mock
async def test_client_errors_are_not_retried(adapter, monkeypatch):
    monkeypatch.setattr(github_module, "RETRY_BACKOFF_SECONDS", 0)
    route = respx.post(URL).mock(return_value=httpx.Response(401))
    with pytest.raises(AuthError):
        await adapter.get_repo_info(REPO)
    assert route.call_count == 1


def _issue(closed_at, closers):
    return {
        "number": 7, "title": "bug", "url": "u", "createdAt": "2026-03-01T00:00:00Z", "updatedAt": "2026-03-05T00:00:00Z",
        "closedAt": closed_at, "author": {"login": "erin"}, "labels": {"nodes": []},
        "timelineItems": {"nodes": [{"actor": {"login": who} if who else None} for who in closers]},
    }


def test_closer_comes_from_last_closed_event():
    assert _issue_to_record(_issue("2026-03-05T00:00:00Z", ["bob", "carol"])).closed_by == "carol"


def test_reopened_issue_has_no_closer_and_ghost_actor_is_none():
    assert _issue_to_record(_issue(None, ["bob"])).closed_by is None  # closed once, then reopened
    assert _issue_to_record(_issue("2026-03-05T00:00:00Z", [None])).closed_by is None  # deleted account
    assert _issue_to_record(_issue("2026-03-05T00:00:00Z", [])).closed_by is None


def test_adapter_refuses_to_start_without_token():
    with pytest.raises(AuthError):
        GitHubAdapter(token="", graphql_url=URL)


@respx.mock
async def test_user_input_travels_as_variables_not_query_text(adapter):
    seen = {}

    def responder(request: httpx.Request):
        seen["body"] = request.read().decode()
        return httpx.Response(200, json={"data": {"repository": {"defaultBranchRef": {"name": "main"}}}})

    respx.post(URL).mock(side_effect=responder)
    info = await adapter.get_repo_info(RepoRef(owner="acme", name='x") { __typename }'))
    assert info.default_branch == "main"
    import json

    payload = json.loads(seen["body"])
    assert payload["variables"]["name"] == 'x") { __typename }'
    assert '__typename' not in payload["query"]
