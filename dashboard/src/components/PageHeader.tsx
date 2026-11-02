import { useEffect, type ReactNode } from "react";
import type { LucideIcon } from "lucide-react";

export function PageHeader({
  title,
  description,
  icon: Icon,
  actions,
}: {
  title: string;
  description?: string;
  icon: LucideIcon;
  actions?: ReactNode;
}) {
  useEffect(() => {
    document.title = `${title} · BRM Engine`;
  }, [title]);

  return (
    <div className="grid-margin flex flex-wrap items-center justify-between gap-4">
      <div className="flex items-center gap-4">
        <div className="icon-box icon-box-sm icon-box-dark">
          <Icon size={20} aria-hidden="true" />
        </div>
        <div>
          <h1 className="text-2xl leading-tight font-semibold">{title}</h1>
          {description && <p className="text-muted-foreground">{description}</p>}
        </div>
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}
