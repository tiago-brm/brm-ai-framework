const COLORS: Record<string, string> = {
  allowed: "border-green/30 bg-green/10 text-green",
  denied: "border-red/30 bg-red/10 text-red",
  pending_approval: "border-amber/30 bg-amber/10 text-amber",
};

export function DecisionBadge({ decision }: { decision: string }) {
  const cls = COLORS[decision] ?? "border-line bg-ink/5 text-ink/55";
  return (
    <span className={`shrink-0 rounded border px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-wide ${cls}`}>
      {decision}
    </span>
  );
}

const EFFECT_COLORS: Record<string, string> = {
  allow: "border-green/30 bg-green/10 text-green",
  deny: "border-red/30 bg-red/10 text-red",
  require_approval: "border-amber/30 bg-amber/10 text-amber",
};

export function EffectBadge({ effect }: { effect: string }) {
  const cls = EFFECT_COLORS[effect] ?? "border-line bg-ink/5 text-ink/55";
  return (
    <span className={`shrink-0 rounded border px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-wide ${cls}`}>
      {effect}
    </span>
  );
}
