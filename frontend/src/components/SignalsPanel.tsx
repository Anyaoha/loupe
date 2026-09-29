import type { Fact, Signal, SignalsReport } from "../types";
import { num, shortDate } from "../format";

interface Props {
  report: SignalsReport | null;
  loading: boolean;
  highlighted: Set<string>;
  onHoverFacts: (ids: string[] | null) => void;
  repoUrl: string | null;
}

const ARROW = { up: "↑", down: "↓", flat: "→" } as const;

function fmtValue(kind: string, v: number | null) {
  if (v == null) return "–";
  if (kind.includes("share") || kind === "review_concentration" || kind === "unreviewed_merges" || kind === "commit_bus_factor") return `${(v * 100).toFixed(0)}%`;
  if (kind.includes("drift")) return `${v.toFixed(0)}h`;
  return num(v);
}

export function SignalsPanel({ report, loading, highlighted, onHoverFacts, repoUrl }: Props) {
  return (
    <section className="panel">
      <div className="panel-head">
        <h2>Signals</h2>
        {report && (
          <span className="muted">
            {shortDate(report.window.start)} – {shortDate(report.window.end)} vs {shortDate(report.baseline_window.start)} – {shortDate(report.baseline_window.end)}
          </span>
        )}
      </div>
      {loading && <p className="muted">Detecting…</p>}
      {!loading && report && report.signals.length === 0 && (
        <div className="empty">Nothing drifted. Throughput, review load and merge times are within range of the previous period.</div>
      )}
      <div className="signals">
        {report?.signals.map((s) => (
          <SignalCard key={s.id} s={s} facts={report.facts} lit={s.evidence_refs.some((r) => highlighted.has(r))} onHoverFacts={onHoverFacts} repoUrl={repoUrl} />
        ))}
      </div>
    </section>
  );
}

function SignalCard({ s, facts, lit, onHoverFacts, repoUrl }: { s: Signal; facts: Record<string, Fact>; lit: boolean; onHoverFacts: Props["onHoverFacts"]; repoUrl: string | null }) {
  return (
    <article className={`signal sev-${s.severity} ${lit ? "lit" : ""}`} onMouseEnter={() => onHoverFacts(s.evidence_refs)} onMouseLeave={() => onHoverFacts(null)}>
      <div className="signal-top">
        <span className={`badge sev-${s.severity}`}>{s.severity}</span>
        <span className="kind">{s.kind.replace(/_/g, " ")}</span>
        {s.direction && <span className="dir">{ARROW[s.direction]}</span>}
      </div>
      <h3>{s.title}</h3>
      <p>{s.summary}</p>
      <div className="signal-meta">
        <span>now <b>{fmtValue(s.kind, s.value)}</b></span>
        <span>before <b>{fmtValue(s.kind, s.baseline)}</b></span>
      </div>
      <div className="refs">
        {s.evidence_refs.filter((r) => facts[r]).slice(0, 4).map((r) => (
          <span key={r} className={`chip ${lit ? "lit" : ""}`} title={r}>{facts[r].label}: {num(facts[r].value)}{facts[r].unit === "hours" ? "h" : ""}</span>
        ))}
      </div>
      {s.related_items.length > 0 && (
        <div className="related">
          {s.related_items.slice(0, 8).map((n) => (
            repoUrl ? <a key={n} href={`${repoUrl}/pull/${n}`} target="_blank" rel="noreferrer">#{n}</a> : <span key={n}>#{n}</span>
          ))}
        </div>
      )}
    </article>
  );
}
