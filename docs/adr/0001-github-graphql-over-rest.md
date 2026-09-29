# ADR 0001: GitHub GraphQL API instead of REST

- Status: accepted
- Code: `backend/loupe/adapters/github.py`

## Context

Loupe backfills up to 180 days of pull requests, reviews, commits and issues per repository, then syncs incrementally. For every pull request it needs the PR itself, its size and its reviews.

With REST that is one list call per page, plus one call for the PR's files/size and one for its reviews: roughly `1 + 2N` calls. On an active repository with ~1,500 PRs in the window that is about 3,000 requests, which runs into the 5,000/hour authenticated rate limit after one or two repositories.

## Decision

Use the GraphQL v4 API. One paginated query returns a page of PRs with `additions`, `deletions`, `changedFiles`, the first 50 reviews and the last `ClosedEvent`. Issues use the same pattern, and commits come from the default branch's `history(since:)`. PR and issue pagination walks `UPDATED_AT` descending and stops at the high-water mark minus a ten-minute overlap.

## Consequences

- About 30 calls for the same backfill instead of about 3,000. A sync finishes in seconds and leaves rate-limit headroom.
- A token is required even for public repositories, because GraphQL refuses anonymous requests. The README asks for a read-only fine-grained token.
- Nested connections are capped: reviews beyond the first 50 on one PR are undercounted and logged. A follow-up page per PR would close this; it was not worth the extra call for the typical case.
- GraphQL has no `closedBy` field, so the closer comes from `timelineItems(itemTypes: [CLOSED_EVENT], last: 1)`. The query costs more points but needs no extra calls.
- Only the adapter knows about GraphQL. Everything downstream reads the canonical model (ADR 0003), so switching an endpoint to REST later would be a local change.

## Alternatives considered

- **REST with concurrency:** same call count, only faster to hit the limit.
- **REST with conditional requests (ETags):** helps re-syncs, not the initial backfill.
- **GitHub webhooks:** needs a public endpoint and app installation; out of scope for a locally run tool. It would complement incremental sync, not replace backfill.
