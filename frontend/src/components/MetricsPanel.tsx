import type { MetricsReport } from "../types";
import { hours, pct, shortDate } from "../format";

interface Props { metrics: MetricsReport | null; loading: boolean; highlighted: Set<string> }

export function MetricsPanel({ metrics, loading, highlighted }: Props) {
  if (loading && !metrics) return <section className="panel"><h2>Metrics</h2><p className="muted">Computing…</p></section>;
  if (!metrics) return null;
  const { totals: t, flow, open_state: o, concentration: c, leaderboards: lb, coverage } = metrics;
  const lit = (id: string) => (highlighted.has(id) ? "lit" : "");

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>Metrics</h2>
        <span className="muted">coverage {pct(coverage.covered_ratio)}{coverage.covered_ratio < 0.95 && <b className="warn"> · partial</b>}</span>
      </div>

      <div className="tiles">
        <Tile label="PRs merged" value={t.prs_merged} cls={lit("cur.totals.prs_merged")} />
        <Tile label="Commits" value={t.commits} cls={lit("cur.totals.commits")} />
        <Tile label="Reviews" value={t.reviews} cls={lit("cur.totals.reviews")} />
        <Tile label="Issues closed" value={t.issues_closed} cls={lit("cur.totals.issues_closed")} />
        <Tile label="Median time to merge" value={hours(flow.time_to_merge.median_hours)} sub={`p90 ${hours(flow.time_to_merge.p90_hours)} · n=${flow.time_to_merge.sample}`} cls={lit("cur.flow.time_to_merge_median")} />
        <Tile label="Issue turnaround" value={hours(flow.issue_turnaround.median_hours)} sub={`median · n=${flow.issue_turnaround.sample}`} cls={lit("cur.flow.issue_turnaround_median")} />
        <Tile label="Merged w/o review" value={pct(flow.merged_without_review_ratio)} sub={`${flow.merged_without_review} PRs`} cls={lit("cur.flow.merged_without_review_ratio")} />
        <Tile label="Stale open PRs" value={o.stale_prs} sub={`of ${o.open_prs} open · >${o.stale_threshold_days}d`} cls={lit("cur.open.stale_prs")} />
        <Tile label="Top reviewer share" value={pct(c.review_top1_share)} cls={lit("cur.concentration.review_top1_share")} />
        <Tile label="Commit bus factor" value={c.commit_bus_factor ?? "–"} sub={`top committer ${pct(c.commit_top1_share)}`} cls={lit("cur.concentration.commit_bus_factor")} />
      </div>

      <Weekly metrics={metrics} />

      <div className="boards">
        <Board title="Reviewers" rows={lb.reviewers.map((r) => [r.actor, r.reviews, `${r.approvals} ✓ · ${r.changes_requested} ✎`])} head={["who", "reviews", ""]} litRow={(a) => highlighted.has(`cur.reviewer.${a}.reviews`)} />
        <Board title="Committers" rows={lb.committers.map((r) => [r.actor, r.count, ""])} head={["who", "commits", ""]} litRow={(a) => highlighted.has(`cur.committer.${a}.commits`)} />
        <Board title="PR authors (merged)" rows={lb.pr_authors.map((r) => [r.actor, r.count, ""])} head={["who", "PRs", ""]} litRow={(a) => highlighted.has(`cur.pr_author.${a}.prs_merged`)} />
        <Board title="Lines changed" rows={lb.lines_changed.map((r) => [r.actor, r.total, `+${r.additions} −${r.deletions}`])} head={["who", "total", ""]} />
      </div>

      {o.oldest_open.length > 0 && (
        <div className="stale">
          <div className="label">Oldest open PRs</div>
          <table>
            <tbody>
              {o.oldest_open.slice(0, 6).map((p) => (
                <tr key={p.number}>
                  <td>{p.url ? <a href={p.url} target="_blank" rel="noreferrer">#{p.number}</a> : `#${p.number}`}</td>
                  <td className="title">{p.title}</td>
                  <td className="muted">{p.author ?? "–"}</td>
                  <td className={p.age_days > o.stale_threshold_days ? "warn" : ""}>{p.age_days.toFixed(0)}d</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function Tile({ label, value, sub, cls }: { label: string; value: string | number; sub?: string; cls?: string }) {
  return (
    <div className={`tile ${cls ?? ""}`}>
      <span className="tile-label">{label}</span>
      <span className="tile-value">{value}</span>
      {sub && <span className="tile-sub">{sub}</span>}
    </div>
  );
}

function Board({ title, head, rows, litRow }: { title: string; head: string[]; rows: (string | number)[][]; litRow?: (actor: string) => boolean }) {
  return (
    <div className="board">
      <div className="label">{title}</div>
      {rows.length === 0 ? <p className="muted">none</p> : (
        <table>
          <thead><tr>{head.map((h, i) => <th key={i}>{h}</th>)}</tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={String(r[0])} className={litRow?.(String(r[0])) ? "lit" : ""}>
                {r.map((cell, i) => <td key={i} className={i === 1 ? "n" : ""}>{cell}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function Weekly({ metrics }: { metrics: MetricsReport }) {
  const w = metrics.weekly;
  if (w.length < 2) return null;
  const W = 640, H = 120, pad = 24;
  const maxMerged = Math.max(1, ...w.map((p) => p.prs_merged));
  const ttm = w.map((p) => p.median_time_to_merge_hours ?? 0);
  const maxTtm = Math.max(1, ...ttm);
  const bw = (W - pad * 2) / w.length;
  const y = (v: number, max: number) => H - pad - (v / max) * (H - pad * 2);
  const line = ttm.map((v, i) => `${i === 0 ? "M" : "L"}${pad + bw * i + bw / 2},${y(v, maxTtm)}`).join(" ");
  return (
    <div className="weekly">
      <div className="label">Per week · <span className="k bars">PRs merged</span> · <span className="k line">median time to merge</span></div>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
        {w.map((p, i) => (
          <g key={i}>
            <rect x={pad + bw * i + 4} y={y(p.prs_merged, maxMerged)} width={bw - 8} height={H - pad - y(p.prs_merged, maxMerged)} className="bar" />
            <text x={pad + bw * i + bw / 2} y={H - 6} textAnchor="middle" className="axis">{shortDate(p.week_start)}</text>
            <text x={pad + bw * i + bw / 2} y={y(p.prs_merged, maxMerged) - 4} textAnchor="middle" className="val">{p.prs_merged}</text>
          </g>
        ))}
        <path d={line} className="line" />
        {ttm.map((v, i) => <circle key={i} cx={pad + bw * i + bw / 2} cy={y(v, maxTtm)} r={3} className="pt"><title>{hours(v)}</title></circle>)}
      </svg>
    </div>
  );
}
