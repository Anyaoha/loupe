import { useState } from "react";
import type { TrackedRepo } from "../types";
import { ago, isoDate } from "../format";

interface Props {
  repos: TrackedRepo[];
  selected: TrackedRepo | null;
  onSelect: (r: TrackedRepo) => void;
  onTrack: (owner: string, name: string) => Promise<void>;
  onSync: () => Promise<void>;
  from: string; to: string;
  onWindow: (from: string, to: string) => void;
}

const PRESETS = [14, 30, 90];

export function RepoBar({ repos, selected, onSelect, onTrack, onSync, from, to, onWindow }: Props) {
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const track = async () => {
    const m = draft.trim().match(/^(?:https:\/\/github\.com\/)?([A-Za-z0-9][A-Za-z0-9-]{0,38})\/([A-Za-z0-9_.-]{1,100})\/?$/);
    if (!m) { setError("Use owner/name or a github.com URL"); return; }
    setBusy(true); setError(null);
    try { await onTrack(m[1], m[2]); setDraft(""); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };

  const preset = (days: number) => {
    const end = new Date();
    const start = new Date(end.getTime() - days * 86400000);
    onWindow(isoDate(start), isoDate(end));
  };

  const sync = selected?.sync;
  return (
    <header className="bar">
      <div className="brand">
        <span className="brand-mark">◎</span>
        <span className="brand-name">Loupe</span>
        <span className="brand-tag">a small lens on team signals</span>
      </div>

      <div className="bar-row">
        <label className="field">
          <span>Repository</span>
          <select value={selected ? `${selected.repository.owner}/${selected.repository.name}` : ""} onChange={(e) => {
            const r = repos.find((x) => `${x.repository.owner}/${x.repository.name}` === e.target.value);
            if (r) onSelect(r);
          }}>
            {repos.length === 0 && <option value="">No repositories yet</option>}
            {repos.map((r) => (
              <option key={`${r.repository.owner}/${r.repository.name}`} value={`${r.repository.owner}/${r.repository.name}`}>
                {r.repository.owner}/{r.repository.name}
              </option>
            ))}
          </select>
        </label>

        <label className="field grow">
          <span>Track another</span>
          <div className="inline">
            <input placeholder="owner/name  e.g. fastapi/fastapi" value={draft} onChange={(e) => setDraft(e.target.value)} onKeyDown={(e) => e.key === "Enter" && track()} />
            <button onClick={track} disabled={busy || !draft.trim()}>{busy ? "…" : "Track"}</button>
          </div>
          {error && <small className="error">{error}</small>}
        </label>

        <label className="field">
          <span>From</span>
          <input type="date" value={from} max={to} onChange={(e) => onWindow(e.target.value, to)} />
        </label>
        <label className="field">
          <span>To</span>
          <input type="date" value={to} min={from} onChange={(e) => onWindow(from, e.target.value)} />
        </label>
        <div className="presets">
          {PRESETS.map((d) => <button key={d} className="ghost" onClick={() => preset(d)}>{d}d</button>)}
        </div>
      </div>

      {sync && (
        <div className={`syncline sync-${sync.status}`}>
          <span className="dot" />
          <span>sync <b>{sync.status}</b></span>
          <span>· data through {ago(sync.work_items_synced_to)}</span>
          {sync.backfill_from && <span>· backfilled from {isoDate(new Date(sync.backfill_from))}</span>}
          {sync.last_error && <span className="error">· {sync.last_error}</span>}
          <button className="ghost small" onClick={onSync} disabled={sync.status === "running"}>sync now</button>
        </div>
      )}
    </header>
  );
}
