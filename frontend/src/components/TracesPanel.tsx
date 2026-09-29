import { useState } from "react";
import type { LlmTrace } from "../types";
import { ago } from "../format";

export function TracesPanel({ traces }: { traces: LlmTrace[] }) {
  const [open, setOpen] = useState(false);
  const cost = traces.reduce((a, t) => a + t.estimated_cost_usd, 0);
  return (
    <section className="panel traces">
      <div className="panel-head clickable" onClick={() => setOpen(!open)}>
        <h2>Model calls <span className="muted">· {traces.length} recent · ${cost.toFixed(4)} · OTel gen_ai.* attributes</span></h2>
        <span className="muted">{open ? "hide" : "show"}</span>
      </div>
      {open && (
        <table className="tracetable">
          <thead>
            <tr><th>when</th><th>purpose</th><th>system</th><th>model</th><th>prompt</th><th>in</th><th>out</th><th>latency</th><th>cost</th><th>status</th></tr>
          </thead>
          <tbody>
            {traces.map((t) => (
              <tr key={t.trace_id} className={t.status !== "ok" ? "bad" : ""}>
                <td>{ago(t.created_at)}</td><td>{t.purpose}</td><td>{t.gen_ai_system}</td><td>{t.gen_ai_response_model ?? t.gen_ai_request_model}</td>
                <td>{t.prompt_version}</td><td className="n">{t.gen_ai_usage_input_tokens}</td><td className="n">{t.gen_ai_usage_output_tokens}</td>
                <td className="n">{t.latency_ms.toFixed(0)}ms</td><td className="n">${t.estimated_cost_usd.toFixed(5)}</td><td>{t.status}{t.error_type ? ` (${t.error_type})` : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
