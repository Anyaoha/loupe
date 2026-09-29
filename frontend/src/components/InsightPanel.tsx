import type { Insight, SignalsReport } from "../types";
import { num } from "../format";

interface Props {
  insight: Insight | null;
  loading: boolean;
  error: string | null;
  onGenerate: (refresh: boolean) => void;
  onHoverFacts: (ids: string[] | null) => void;
  signals: SignalsReport | null;
}

export function InsightPanel({ insight, loading, error, onGenerate, onHoverFacts, signals }: Props) {
  return (
    <section className="panel insight">
      <div className="panel-head">
        <h2>What's going on</h2>
        <div className="inline">
          <button onClick={() => onGenerate(false)} disabled={loading || !signals}>{loading ? "Thinking…" : insight ? "Explain again" : "Explain this period"}</button>
          {insight && <button className="ghost small" onClick={() => onGenerate(true)} disabled={loading} title="Bypass cache">regenerate</button>}
        </div>
      </div>

      {error && <div className="error-box">{error}</div>}
      {!insight && !loading && !error && <p className="muted">One model call. The narrative is checked claim by claim against the numbers before it is shown.</p>}

      {insight && (
        <div className="insight-body">
          <h3 className="headline">{insight.headline}</h3>
          <p className="narrative">{insight.narrative}</p>
          {insight.root_cause && (
            <div className="rootcause">
              <span className="label">Root-cause hypothesis</span>
              <p>{insight.root_cause.hypothesis}</p>
              <small>model's own confidence in this hypothesis: {(insight.root_cause.model_confidence * 100).toFixed(0)}%</small>
            </div>
          )}

          <ConfidenceBar insight={insight} />

          <div className="evidence">
            <div className="label">
              Evidence chain · {insight.verification.claims_verified}/{insight.verification.claims_total} verified
              {insight.verification.unknown_actors.length > 0 && <span className="error"> · unknown people: {insight.verification.unknown_actors.join(", ")}</span>}
            </div>
            <ul>
              {insight.evidence.map((e, i) => (
                <li key={i} className={e.verified ? "ok" : "bad"} onMouseEnter={() => onHoverFacts([e.fact_id])} onMouseLeave={() => onHoverFacts(null)}>
                  <span className="mark">{e.verified ? "✓" : "✗"}</span>
                  <span className="claim">{e.claim}</span>
                  <code className="factid" title={signals?.facts[e.fact_id]?.label ?? "unknown fact"}>{e.fact_id}</code>
                  {!e.verified && <small className="why">{e.note}{e.actual_value != null ? ` (actual: ${num(e.actual_value)})` : ""}</small>}
                </li>
              ))}
            </ul>
          </div>

          <footer className="insight-foot muted">
            {insight.model} · {insight.prompt_version} · trace {insight.trace_id.slice(0, 8)} · {insight.cached ? "cached" : "fresh"} ·
            signals used: {insight.signals_considered.join(", ") || "none"}
          </footer>
        </div>
      )}
    </section>
  );
}

function ConfidenceBar({ insight }: { insight: Insight }) {
  const b = insight.confidence_breakdown;
  const steps: [string, number, string][] = [
    ["model said", b.model_confidence, "raw self-reported confidence"],
    ["after verification", b.after_verification, `penalty ${(insight.verification.penalty * 100).toFixed(0)}% for failed claims / unknown people`],
    ["coverage cap", b.coverage_cap, "partial data can never yield high confidence"],
    ["final", b.final, "min of the above, and 0.5 if fewer than two claims verified"],
  ];
  return (
    <div className="confidence">
      <div className="label">Confidence <b>{(b.final * 100).toFixed(0)}%</b> <span className="muted">(computed, not copied from the model)</span></div>
      {steps.map(([name, v, why]) => (
        <div key={name} className={`confrow ${name === "final" ? "final" : ""}`} title={why}>
          <span className="confname">{name}</span>
          <span className="track"><span className="fill" style={{ width: `${Math.max(2, v * 100)}%` }} /></span>
          <span className="confval">{(v * 100).toFixed(0)}%</span>
        </div>
      ))}
    </div>
  );
}
