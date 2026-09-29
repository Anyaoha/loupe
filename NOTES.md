# NOTES

## 1. Running it

See the README quickstart. Shortest path with no keys:

```bash
cp .env.example .env && docker compose up --build   # then open http://localhost:8080
```

Prerequisites: Docker, or Python 3.11+ and Node 20+ for local dev. Env vars are all prefixed `LOUPE_` and documented in `.env.example`. The only secrets are `LOUPE_GITHUB_TOKEN` (read-only PAT) and `LOUPE_ANTHROPIC_API_KEY` (or the AWS credential chain for Bedrock). Both are `SecretStr` in config, never logged, and a log filter redacts anything shaped like a token as a second line of defence.

## 2. Architecture and the decisions behind it

Loupe is four layers with one direction of dependency: **adapter → canonical store → analysis → API**. The adapter knows GitHub. Nothing below it does.

**Canonical model over raw mirroring.** GitHub PRs, reviews, commits and issues are normalised into two source-agnostic tables: `WorkItem` (anything with a lifecycle: PRs, issues) and `ActivityEvent` (anything point-in-time: commits, reviews). Metrics, signals and the LLM prompt only read those. Adding Jira or Linear is one adapter emitting the same shapes; the detectors and the UI do not change. This is the same move as defining a shared telemetry attribute contract that several teams emit against: the platform owns the vocabulary, sources conform to it.

**GraphQL, not REST, for GitHub.** One PR page returns the PR, its size and its reviews. REST needs 1 + 2N calls for the same data. Over a 180-day backfill on an active repo that is ~30 calls versus ~3,000, which is the difference between "syncs in seconds" and "hits the rate limit". Cost: GraphQL refuses anonymous requests, so a token is mandatory even for public repos. I decided the honesty of "you need a read-only token" beat the fragility of a REST fan-out.

**Background sync into SQLite, not fetch-on-request.** Each repo keeps two high-water marks (work items, commits). A sync asks for everything updated since the mark minus a ten-minute overlap and upserts. Re-runs are idempotent, incremental syncs are cheap, and metrics queries never touch GitHub. The upsert helper picks the SQLite or Postgres dialect from the engine, so moving to Postgres is a `LOUPE_DATABASE_URL` change and nothing else.

**Facts table between the numbers and the model.** The LLM does not see raw rows or the MetricsReport. It sees a flat `{fact_id: {label, value, unit}}` table plus the pre-computed signals, and is told those are the only numbers that exist. That makes the output verifiable: every claim must cite an id, and the verifier checks id existence, value match (with tolerance for rounding and percent-vs-ratio), and that any `@person` named appears in the data. Confidence is then computed from the verification result and the data coverage, not copied from the model. The full breakdown is returned so a reader can see why a number is 0.61 and not 0.85.

**Signals before synthesis.** The deterministic layer finds the drift and the LLM explains it, not the other way round. That keeps the expensive, non-deterministic call small (one call, ~2k tokens in) and keeps "what happened" reproducible even if the narrative varies. Baseline is the equal-length period immediately before the window, chosen for explainability over sophistication.

**HTTP semantics that mean something.** `POST /repos` returns `202` with a `Location` header because sync is asynchronous; `200` when already tracked. `POST /insights` because it triggers a paid call; `201` on a fresh synthesis, `200` on a cache hit keyed by (window, prompt version, model), so a prompt bump never serves a stale narrative. Every metrics response carries `X-Loupe-Coverage` and a `Warning` header while a sync is running, so a client can tell a real quiet week from a half-synced one.

**Tracing with OTel GenAI attribute names.** `LlmTrace` columns are literally `gen_ai_system`, `gen_ai_request_model`, `gen_ai_usage_input_tokens`, and so on. It is a table today; exporting to an OTel backend is a sink swap, not a rename.

## 3. Trade-offs I'd revisit, and what I'd do with another day

**Baseline model.** "Previous equal period" is explainable but naive: a window that spans a holiday will look like drift. Next step is a trailing 8-week weekly series per metric with a robust z-score (median/MAD), which the `weekly` buckets already set up. The threshold constants at the top of `detectors.py` are opinions, not calibrated values; with real usage I would tune them against labelled examples and expose them per repo.

**Confidence is heuristic, not calibrated.** The penalty and cap formula is defensible and transparent, but it is not empirically calibrated. The right next step is to store human feedback ("was this insight right?") next to each insight and periodically fit displayed confidence to observed hit rate, per prompt version. The `Insight` table and trace ids are already there to join on.

**Sync runs inside the API process.** One asyncio worker, in-memory de-dup, no retry with backoff on rate limits (it logs and waits for the next tick). Fine for a demo and for one instance; for more than one replica I would move sync to a separate worker with a real queue and per-repo locks, and persist the rate-limit reset time so restarts do not re-hammer the API.

**Review capture cap.** The GraphQL query takes the first 50 reviews per PR. PRs with more than that log a warning and undercount. A follow-up page per PR would close it; I judged it not worth the extra call for the typical case.

**Closer identity.** GitHub's GraphQL does not expose who closed an issue without walking the timeline, so there is no "top closers" leaderboard. "Top mergers" (from `mergedBy`) is there instead. Honest omission over a fabricated metric.

**DB calls on the event loop.** Metrics endpoints are sync `def` routes, so FastAPI runs them in a threadpool. The insights route is `async` and does its handful of small SQLite reads inline; with Postgres and real concurrency I would move those to `run_in_threadpool` or switch to the async engine.

**UI.** It does the job for a demo (signals, insight with evidence drilldown, metrics, traces, hover-to-highlight between evidence and the numbers). No routing, no state library, no tests; the surface area did not justify them yet. With another day: an evidence click that scrolls and pins the metric, a per-signal "show the PRs" drawer, and a visual for the confidence breakdown that explains itself without a tooltip.

**Second integration.** The adapter protocol is there and the tests show the shape. A GitLab adapter (same concepts, different GraphQL) would be the cheapest proof that the canonical model holds.

## 4. What I used AI for

Claude Code was the pair for the whole build. The way I worked it:

- I wrote the design first (canonical model, facts table, verifier, HTTP semantics, what the tests should prove) and used the model to draft code against that design, module by module.
- I read every file before it went in. Things I pushed back on or fixed on re-read: a `return` that would have stopped issue pagination after the PR loop; a SQLite-only `INSERT ... ON CONFLICT` that contradicted the "Postgres is a config change" claim (now dialect-aware); a datetime subtraction in an `ORDER BY` that SQLite would have silently mis-sorted; a naive-vs-aware datetime mismatch on the SQLite read path; a first pass of the verifier that scanned prose for bare words and would have flagged ordinary English as "unknown people".
- Tests were written from hand-computed expectations, not from the engine's own output. One test "failed" on first run because my expectation was wrong (39.5 days, not 40) and the engine was right. That is the reason the fixtures are hand-built.
- The eval harness caught a real gap on its first run: the mock provider ignored the "acknowledge partial coverage" rule in the prompt. Fixed the mock, kept the check.
- The narrative text in this file and the README is mine; the model helped tighten it.

Nothing in the repo was generated and left unread. If something here surprises you, I would like to hear it.
