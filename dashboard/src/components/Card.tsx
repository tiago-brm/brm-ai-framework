import type { ReactNode } from "react";
import { CircleAlert, Inbox, type LucideIcon } from "lucide-react";

export function Card({
  title,
  extra,
  className = "",
  children,
}: {
  title?: ReactNode;
  extra?: ReactNode;
  className?: string;
  children?: ReactNode;
}) {
  return (
    <section className={`card overflow-hidden ${className}`}>
      {(title || extra) && (
        <div className="flex flex-wrap items-center justify-between gap-3 px-6 pt-5 pb-3">
          {title && <h2 className="card-title">{title}</h2>}
          {extra}
        </div>
      )}
      {children}
    </section>
  );
}

export function Empty({ icon: Icon = Inbox, children }: { icon?: LucideIcon; children: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-2 px-6 py-8 text-center text-muted-foreground">
      <Icon size={28} aria-hidden="true" />
      <p>{children}</p>
    </div>
  );
}

export function ErrorState({ children }: { children: ReactNode }) {
  return (
    <div role="alert" className="flex flex-col items-center gap-2 px-6 py-8 text-center">
      <CircleAlert size={28} className="text-danger" aria-hidden="true" />
      <p>{children}</p>
    </div>
  );
}
