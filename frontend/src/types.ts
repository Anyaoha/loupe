// Mirrors backend/loupe/schemas.py. Kept by hand; small enough that codegen is not worth the moving part.

export interface RepoOut { source: string; owner: string; name: string; default_branch: string | null }
export interface SyncStateOut {
  status: string; backfill_from: string | null; work_items_synced_to: string | null; commits_synced_to: string | null;
  last_started_at: string | null; last_finished_at: string | null; last_error: string | null; last_run_stats: Record<string, unknown> | null;
}
export interface TrackedRepo { repository: RepoOut; sync: SyncStateOut }

export interface Window { start: string; end: string; days: number }
export interface Coverage { backfill_from: string | null; synced_to: string | null; covered_ratio: number; sync_status: string }

export interface Totals {
  commits: number; prs_opened: number; prs_merged: number; prs_closed_unmerged: number; issues_opened: number; issues_closed: number;
  reviews: number; review_comments: number; active_committers: number; active_reviewers: number;
}
export interface ActorCount { actor: string; count: number }
export interface ActorLines { actor: string; additions: number; deletions: number; total: number; prs: number }
export interface ReviewerRow { actor: string; reviews: number; approvals: number; changes_requested: number; comments: number }
export interface DurationStats { median_hours: number | null; p90_hours: number | null; sample: number }
export interface StalePR { number: number; title: string; author: string | null; age_days: number; url: string | null }
export interface WeeklyPoint { week_start: string; commits: number; prs_opened: number; prs_merged: number; issues_closed: number; reviews: number; median_time_to_merge_hours: number | null }

export interface MetricsReport {
  repository: RepoOut; window: Window; coverage: Coverage; totals: Totals;
  leaderboards: { committers: ActorCount[]; pr_authors: ActorCount[]; lines_changed: ActorLines[]; reviewers: ReviewerRow[]; mergers: ActorCount[] };
  flow: { time_to_merge: DurationStats; issue_turnaround: DurationStats; merged_without_review: number; merged_without_review_ratio: number | null };
  open_state: { open_prs: number; stale_prs: number; stale_threshold_days: number; oldest_open: StalePR[] };
  concentration: { review_top1_share: number | null; review_top2_share: number | null; commit_top1_share: number | null; commit_bus_factor: number | null };
  weekly: WeeklyPoint[];
}

export type Severity = "info" | "warning" | "critical";
export interface Signal {
  id: string; kind: string; severity: Severity; title: string; summary: string; value: number | null; baseline: number | null;
  direction: "up" | "down" | "flat" | null; evidence_refs: string[]; related_items: number[];
}
export interface Fact { label: string; value: number | string | null; unit: string | null }
export interface SignalsReport { repository: RepoOut; window: Window; baseline_window: Window; coverage: Coverage; signals: Signal[]; facts: Record<string, Fact> }

export interface EvidenceClaim { fact_id: string; claim: string; quoted_value: number | string | null; verified: boolean; actual_value: number | string | null; note: string | null }
export interface Insight {
  repository: RepoOut; window: Window; headline: string; narrative: string;
  root_cause: { hypothesis: string; model_confidence: number } | null;
  confidence: number;
  confidence_breakdown: { model_confidence: number; after_verification: number; coverage_cap: number; final: number };
  evidence: EvidenceClaim[]; signals_considered: string[];
  verification: { claims_total: number; claims_verified: number; claims_failed: number; unknown_actors: string[]; penalty: number };
  prompt_version: string; model: string; trace_id: string; cached: boolean; generated_at: string;
}

export interface LlmTrace {
  trace_id: string; purpose: string; prompt_version: string; gen_ai_system: string; gen_ai_request_model: string; gen_ai_response_model: string | null;
  gen_ai_usage_input_tokens: number; gen_ai_usage_output_tokens: number; latency_ms: number; estimated_cost_usd: number; status: string; error_type: string | null; created_at: string;
}
