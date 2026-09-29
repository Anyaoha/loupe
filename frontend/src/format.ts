export const pct = (v: number | null | undefined, digits = 0) => (v == null ? "–" : `${(v * 100).toFixed(digits)}%`);
export const hours = (h: number | null | undefined) => {
  if (h == null) return "–";
  if (h < 48) return `${h.toFixed(h < 10 ? 1 : 0)}h`;
  return `${(h / 24).toFixed(1)}d`;
};
export const num = (v: number | string | null | undefined) => (v == null ? "–" : typeof v === "number" ? (Number.isInteger(v) ? String(v) : v.toFixed(2)) : v);
export const isoDate = (d: Date) => d.toISOString().slice(0, 10);
export const shortDate = (s: string) => new Date(s).toLocaleDateString(undefined, { month: "short", day: "numeric" });
export const ago = (s: string | null) => {
  if (!s) return "never";
  const mins = Math.round((Date.now() - new Date(s).getTime()) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  if (mins < 60 * 24) return `${Math.round(mins / 60)}h ago`;
  return `${Math.round(mins / 1440)}d ago`;
};
