import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError } from "./api";
import type { Insight, LlmTrace, MetricsReport, SignalsReport, TrackedRepo } from "./types";
import { isoDate } from "./format";
import { RepoBar } from "./components/RepoBar";
import { SignalsPanel } from "./components/SignalsPanel";
import { InsightPanel } from "./components/InsightPanel";
import { MetricsPanel } from "./components/MetricsPanel";
import { TracesPanel } from "./components/TracesPanel";

const defaultWindow = () => {
  const end = new Date();
  const start = new Date(end.getTime() - 30 * 86400000);
  return { from: isoDate(start), to: isoDate(end) };
};

export default function App() {
  const [repos, setRepos] = useState<TrackedRepo[]>([]);
  const [selected, setSelected] = useState<TrackedRepo | null>(null);
  const [{ from, to }, setWindow] = useState(defaultWindow);

  const [metrics, setMetrics] = useState<MetricsReport | null>(null);
  const [signals, setSignals] = useState<SignalsReport | null>(null);
  const [insight, setInsight] = useState<Insight | null>(null);
  const [traces, setTraces] = useState<LlmTrace[]>([]);
  const [loading, setLoading] = useState(false);
  const [insightLoading, setInsightLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [insightError, setInsightError] = useState<string | null>(null);
  const [hoverFacts, setHoverFacts] = useState<string[] | null>(null);

  const highlighted = useMemo(() => new Set(hoverFacts ?? []), [hoverFacts]);
  const owner = selected?.repository.owner, name = selected?.repository.name;
  const repoUrl = selected && selected.repository.source === "github" ? `https://github.com/${owner}/${name}` : null;

  const refreshRepos = useCallback(async () => {
    const list = await api.listRepos();
    setRepos(list);
    setSelected((cur) => list.find((r) => r.repository.owner === cur?.repository.owner && r.repository.name === cur?.repository.name) ?? cur ?? list[0] ?? null);
  }, []);

  useEffect(() => { refreshRepos().catch((e) => setError(String(e))); }, [refreshRepos]);

  // Poll sync state while a sync is pending/running so the bar and numbers update live.
  useEffect(() => {
    if (!selected || !["pending", "running"].includes(selected.sync.status)) return;
    const id = setInterval(() => refreshRepos().catch(() => undefined), 3000);
    return () => clearInterval(id);
  }, [selected, refreshRepos]);

  useEffect(() => {
    if (!owner || !name) return;
    let cancelled = false;
    setLoading(true); setError(null); setInsight(null); setInsightError(null);
    Promise.all([api.metrics(owner, name, { from, to }), api.signals(owner, name, { from, to })])
      .then(([m, s]) => { if (!cancelled) { setMetrics(m); setSignals(s); } })
      .catch((e) => !cancelled && setError(e instanceof ApiError ? e.message : String(e)))
      .finally(() => !cancelled && setLoading(false));
    api.traces().then(setTraces).catch(() => undefined);
    return () => { cancelled = true; };
  }, [owner, name, from, to, selected?.sync.last_finished_at]);

  const generate = async (refresh: boolean) => {
    if (!owner || !name) return;
    setInsightLoading(true); setInsightError(null);
    try {
      setInsight(await api.insight(owner, name, { from, to }, refresh));
      setTraces(await api.traces());
    } catch (e) {
      setInsightError(e instanceof ApiError ? `${e.status}: ${e.message}` : String(e));
    } finally { setInsightLoading(false); }
  };

  const track = async (o: string, n: string) => {
    const r = await api.trackRepo(o, n);
    await refreshRepos();
    setSelected(r);
  };

  const sync = async () => {
    if (!owner || !name) return;
    await api.triggerSync(owner, name);
    await refreshRepos();
  };

  return (
    <div className="app">
      <RepoBar repos={repos} selected={selected} onSelect={setSelected} onTrack={track} onSync={sync} from={from} to={to} onWindow={(f, t) => setWindow({ from: f, to: t })} />
      {error && <div className="error-box">{error}</div>}
      {!selected && !error && (
        <div className="empty big">
          <p>No repositories tracked yet. Paste <code>owner/name</code> above, or run <code>python -m loupe.cli seed-demo</code> for a keyless sample.</p>
        </div>
      )}
      {selected && (
        <main className="grid">
          <div className="col">
            <SignalsPanel report={signals} loading={loading} highlighted={highlighted} onHoverFacts={setHoverFacts} repoUrl={repoUrl} />
            <InsightPanel insight={insight} loading={insightLoading} error={insightError} onGenerate={generate} onHoverFacts={setHoverFacts} signals={signals} />
          </div>
          <div className="col">
            <MetricsPanel metrics={metrics} loading={loading} highlighted={highlighted} />
            <TracesPanel traces={traces} />
          </div>
        </main>
      )}
    </div>
  );
}
