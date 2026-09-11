import type { ReactNode } from "react";

export function PageHeader({
  crumb,
  title,
  actions,
}: {
  crumb: string;
  title: string;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-3 flex flex-wrap items-end justify-between gap-4">
      <div>
        <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-ink/40">
          super_admin · <span className="text-navy">{crumb}</span>
        </p>
        <h1 className="font-display text-[28px] leading-none italic text-ink">{title}</h1>
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}
