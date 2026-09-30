"""GitHub adapter over the GraphQL v4 API.

Why GraphQL rather than REST: one PR page returns the PR, its size, and its reviews in a
single round-trip. The REST equivalent is 1 + N calls per page (PR list, then per-PR
detail for additions/deletions, then per-PR reviews). Over a 180-day backfill on an active
repo that is the difference between ~30 calls and ~3,000.

All user-supplied values travel as GraphQL variables, never interpolated into the query.
The endpoint URL comes from settings, not from the request, so there is no SSRF surface.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx

from loupe.adapters.base import (
    ActivityRecord,
    AdapterError,
    AuthError,
    RateLimited,
    RepoInfo,
    RepoNotFound,
    RepoRef,
    WorkItemRecord,
)
from loupe.models import ActivityKind, ReviewState, WorkItemKind
from loupe.timeutil import parse_iso

log = logging.getLogger(__name__)

PAGE_SIZE = 50
REVIEWS_PER_PR = 50
# GitHub answers heavy GraphQL pages with 502/503/504 now and then; the same request usually works a moment later.
RETRY_STATUSES = {502, 503, 504}
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 2.0

_REPO_QUERY = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) { defaultBranchRef { name } }
}
"""

_PRS_QUERY = """
query($owner: String!, $name: String!, $first: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequests(first: $first, after: $after, orderBy: {field: UPDATED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number title url createdAt updatedAt closedAt mergedAt
        additions deletions changedFiles
        author { login }
        mergedBy { login }
        timelineItems(itemTypes: [CLOSED_EVENT], last: 1) { nodes { ... on ClosedEvent { actor { login } } } }
        labels(first: 20) { nodes { name } }
        reviews(first: __REVIEWS_PER_PR__) {
          totalCount
          nodes { id state submittedAt author { login } comments { totalCount } }
        }
      }
    }
  }
}
""".replace("__REVIEWS_PER_PR__", str(REVIEWS_PER_PR))

_ISSUES_QUERY = """
query($owner: String!, $name: String!, $first: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    issues(first: $first, after: $after, orderBy: {field: UPDATED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number title url createdAt updatedAt closedAt
        author { login }
        timelineItems(itemTypes: [CLOSED_EVENT], last: 1) { nodes { ... on ClosedEvent { actor { login } } } }
        labels(first: 20) { nodes { name } }
      }
    }
  }
}
"""

_COMMITS_QUERY = """
query($owner: String!, $name: String!, $since: GitTimestamp!, $first: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    defaultBranchRef {
      target {
        ... on Commit {
          history(since: $since, first: $first, after: $after) {
            pageInfo { hasNextPage endCursor }
            nodes {
              oid committedDate additions deletions
              author { user { login } name }
            }
          }
        }
      }
    }
  }
}
"""

_REVIEW_STATE = {
    "APPROVED": ReviewState.APPROVED,
    "CHANGES_REQUESTED": ReviewState.CHANGES_REQUESTED,
    "COMMENTED": ReviewState.COMMENTED,
    "DISMISSED": ReviewState.DISMISSED,
}


class GitHubAdapter:
    source = "github"

    def __init__(self, token: str, graphql_url: str, timeout: float = 30.0, client: httpx.AsyncClient | None = None):
        if not token:
            raise AuthError("LOUPE_GITHUB_TOKEN is required (GitHub GraphQL does not serve anonymous requests)")
        self._url = graphql_url
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._headers = {
            "Authorization": f"bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "loupe/0.1",
        }

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _post(self, query: str, variables: dict) -> httpx.Response:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                resp = await self._client.post(self._url, json={"query": query, "variables": variables}, headers=self._headers)
            except httpx.TimeoutException:
                if attempt == MAX_ATTEMPTS:
                    raise AdapterError(f"GitHub GraphQL timed out after {MAX_ATTEMPTS} attempts") from None
            else:
                if resp.status_code not in RETRY_STATUSES or attempt == MAX_ATTEMPTS:
                    return resp
            log.warning("GitHub GraphQL transient failure, retrying (attempt %d/%d)", attempt, MAX_ATTEMPTS)
            await asyncio.sleep(RETRY_BACKOFF_SECONDS * attempt)
        raise AssertionError("unreachable")

    async def _graphql(self, query: str, variables: dict) -> dict:
        resp = await self._post(query, variables)
        if resp.status_code == 401:
            raise AuthError("GitHub rejected the token")
        if resp.status_code in (403, 429) and resp.headers.get("x-ratelimit-remaining") == "0":
            reset = resp.headers.get("x-ratelimit-reset")
            raise RateLimited(datetime.fromtimestamp(int(reset), tz=UTC) if reset else None)
        if resp.status_code >= 400:
            raise AdapterError(f"GitHub GraphQL HTTP {resp.status_code}")
        body = resp.json()
        if errors := body.get("errors"):
            types = {e.get("type") for e in errors}
            if "NOT_FOUND" in types:
                raise RepoNotFound(errors[0].get("message", "repository not found"))
            if "RATE_LIMITED" in types:
                raise RateLimited(None)
            raise AdapterError("; ".join(e.get("message", "unknown error") for e in errors))
        return body["data"]

    async def get_repo_info(self, repo: RepoRef) -> RepoInfo:
        data = await self._graphql(_REPO_QUERY, {"owner": repo.owner, "name": repo.name})
        node = data.get("repository")
        if node is None:
            raise RepoNotFound(repo.full_name)
        ref = node.get("defaultBranchRef") or {}
        return RepoInfo(default_branch=ref.get("name") or "main")

    async def iter_work_items(self, repo: RepoRef, updated_since: datetime) -> AsyncIterator[tuple[WorkItemRecord, list[ActivityRecord]]]:
        async for node in self._paginate(_PRS_QUERY, repo, "pullRequests"):
            item, reviews = _pr_to_records(node)
            if item.updated_at < updated_since:
                break
            yield item, reviews
        async for node in self._paginate(_ISSUES_QUERY, repo, "issues"):
            item = _issue_to_record(node)
            if item.updated_at < updated_since:
                return
            yield item, []

    async def iter_commits(self, repo: RepoRef, since: datetime) -> AsyncIterator[ActivityRecord]:
        after: str | None = None
        while True:
            variables = {"owner": repo.owner, "name": repo.name, "since": since.isoformat(), "first": PAGE_SIZE, "after": after}
            data = await self._graphql(_COMMITS_QUERY, variables)
            ref = (data.get("repository") or {}).get("defaultBranchRef")
            if not ref:
                return
            history = ref["target"]["history"]
            for node in history["nodes"]:
                yield _commit_to_record(node)
            if not history["pageInfo"]["hasNextPage"]:
                return
            after = history["pageInfo"]["endCursor"]

    async def _paginate(self, query: str, repo: RepoRef, connection: str) -> AsyncIterator[dict]:
        after: str | None = None
        while True:
            variables = {"owner": repo.owner, "name": repo.name, "first": PAGE_SIZE, "after": after}
            data = await self._graphql(query, variables)
            repo_node = data.get("repository")
            if repo_node is None:
                raise RepoNotFound(repo.full_name)
            conn = repo_node[connection]
            for node in conn["nodes"]:
                if node is not None:
                    yield node
            if not conn["pageInfo"]["hasNextPage"]:
                return
            after = conn["pageInfo"]["endCursor"]


def _login(node: dict | None) -> str | None:
    return (node or {}).get("login")


def _closer(node: dict) -> str | None:
    """Actor of the most recent ClosedEvent, only while the item is actually closed (a
    reopened issue keeps its old ClosedEvent in the timeline)."""
    if not node.get("closedAt"):
        return None
    events = (node.get("timelineItems") or {}).get("nodes") or []
    return _login((events[-1] or {}).get("actor")) if events else None


def _pr_to_records(node: dict) -> tuple[WorkItemRecord, list[ActivityRecord]]:
    number = node["number"]
    reviews = node.get("reviews") or {}
    activity: list[ActivityRecord] = []
    for r in reviews.get("nodes") or []:
        if not r or not r.get("submittedAt"):
            continue
        activity.append(
            ActivityRecord(
                kind=ActivityKind.REVIEW,
                external_id=r["id"],
                actor=_login(r.get("author")),
                occurred_at=parse_iso(r["submittedAt"]),
                work_item_number=number,
                review_state=_REVIEW_STATE.get(r.get("state"), ReviewState.COMMENTED),
                comment_count=(r.get("comments") or {}).get("totalCount", 0),
            )
        )
    if reviews.get("totalCount", 0) > REVIEWS_PER_PR:
        log.warning("PR #%s has %s reviews; only first %s captured", number, reviews["totalCount"], REVIEWS_PER_PR)
    item = WorkItemRecord(
        kind=WorkItemKind.PULL_REQUEST,
        number=number,
        title=node.get("title") or "",
        author=_login(node.get("author")),
        url=node.get("url"),
        created_at=parse_iso(node["createdAt"]),
        updated_at=parse_iso(node["updatedAt"]),
        closed_at=parse_iso(node.get("closedAt")),
        merged_at=parse_iso(node.get("mergedAt")),
        merged_by=_login(node.get("mergedBy")),
        closed_by=_closer(node),
        additions=node.get("additions") or 0,
        deletions=node.get("deletions") or 0,
        changed_files=node.get("changedFiles") or 0,
        review_count=reviews.get("totalCount", 0),
        labels=[lbl["name"] for lbl in (node.get("labels") or {}).get("nodes") or []],
    )
    return item, activity


def _issue_to_record(node: dict) -> WorkItemRecord:
    return WorkItemRecord(
        kind=WorkItemKind.ISSUE,
        number=node["number"],
        title=node.get("title") or "",
        author=_login(node.get("author")),
        url=node.get("url"),
        created_at=parse_iso(node["createdAt"]),
        updated_at=parse_iso(node["updatedAt"]),
        closed_at=parse_iso(node.get("closedAt")),
        merged_at=None,
        closed_by=_closer(node),
        labels=[lbl["name"] for lbl in (node.get("labels") or {}).get("nodes") or []],
    )


def _commit_to_record(node: dict) -> ActivityRecord:
    author = node.get("author") or {}
    login = _login(author.get("user")) or author.get("name")
    return ActivityRecord(
        kind=ActivityKind.COMMIT,
        external_id=node["oid"],
        actor=login,
        occurred_at=parse_iso(node["committedDate"]),
        additions=node.get("additions") or 0,
        deletions=node.get("deletions") or 0,
    )
