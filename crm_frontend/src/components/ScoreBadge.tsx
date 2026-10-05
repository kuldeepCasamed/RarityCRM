const STYLE = {
  hot: "bg-red-100 text-red-700", warm: "bg-amber-100 text-amber-700", cold: "bg-sky-100 text-sky-700",
} as const;

export default function ScoreBadge({ score, band }: { score?: number; band?: "hot" | "warm" | "cold" }) {
  if (score === undefined || !band) return null;
  return (
    <span title={`Lead score ${score}/100 (${band})`} className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${STYLE[band]}`}>
      {band === "hot" ? "🔥" : band === "warm" ? "◐" : "❄"} {score}
    </span>
  );
}
