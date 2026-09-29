# ADR 0003: Canonical data model instead of mirroring GitHub

- Status: accepted
- Code: `backend/loupe/models.py`, `backend/loupe/adapters/base.py`

## Context

The first source is GitHub, but delivery data also lives in Jira, Linear and GitLab. If metrics, signals and the prompt read GitHub-shaped tables (`pull_requests`, `reviews`, `issues`), every new source would mean new metrics code, new detectors and a new prompt.

## Decision

Normalise every source into two source-agnostic tables:

- **`WorkItem`**: anything with a lifecycle. Kind (`pull_request`, `issue`), number, author, created / updated / merged / closed timestamps, merger, closer, size, labels. Unique on (repository, kind, number).
- **`ActivityEvent`**: anything point-in-time. Kind (`commit`, `review`), actor, timestamp, the work item number it belongs to. Unique on (repository, kind, `external_id`), where `external_id` is the source's own id (commit SHA, review id).

Adapters implement the `SourceAdapter` protocol and emit `WorkItemRecord` / `ActivityRecord` shapes. The metrics engine, signal detectors, facts table, prompt and UI read only the canonical tables. The unique keys make upserts idempotent, so re-syncing the same range changes nothing.

## Consequences

- Adding a source is one adapter. Detectors, prompt and UI do not change.
- Metrics are SQL aggregates over two tables, which keeps them simple to test against hand-computed fixtures.
- Some source detail is dropped (review comment bodies, CI status, linked issues). Adding a field means extending the canonical model deliberately, not leaking one source's schema.
- Mapping decisions are made once, in the adapter: for example, a merged PR credits the merger and not a closer, and a reopened item drops its old closer.
- It is the same approach as a shared telemetry attribute contract: the platform owns the vocabulary and sources conform to it.

## Alternatives considered

- **Mirror each source's schema and translate at query time:** faster to start; every metric then has one branch per source.
- **Store raw JSON and query it:** flexible, but not typed or indexable, and harder to verify.
