import { CircleCheck, CircleX, Hourglass, type LucideIcon } from "lucide-react";

type Visual = { label: string; cls: string; icon: LucideIcon };

const DECISIONS: Record<string, Visual> = {
  allowed: { label: "Permitido", cls: "badge-outline-success", icon: CircleCheck },
  denied: { label: "Negado", cls: "badge-outline-danger", icon: CircleX },
  pending_approval: { label: "Aguarda aprovação", cls: "badge-outline-warning", icon: Hourglass },
};

const EFFECTS: Record<string, Visual> = {
  allow: { label: "Permite", cls: "badge-outline-success", icon: CircleCheck },
  deny: { label: "Nega", cls: "badge-outline-danger", icon: CircleX },
  require_approval: { label: "Exige aprovação", cls: "badge-outline-warning", icon: Hourglass },
};

function Badge({ value, map }: { value: string; map: Record<string, Visual> }) {
  const v = map[value];
  if (!v) return <span className="badge-outline-muted">{value}</span>;
  const Icon = v.icon;
  return (
    <span className={v.cls} title={value}>
      <Icon size={12} aria-hidden="true" />
      {v.label}
    </span>
  );
}

export function DecisionBadge({ decision }: { decision: string }) {
  return <Badge value={decision} map={DECISIONS} />;
}

export function EffectBadge({ effect }: { effect: string }) {
  return <Badge value={effect} map={EFFECTS} />;
}
