import type { ReactNode } from "react";

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
    <div className={`rise overflow-hidden rounded-md border border-line bg-surface/60 ${className}`}>
      {(title || extra) && (
        <div className="flex items-center justify-between gap-3 border-b border-line px-3 py-2">
          {title && <p className="font-mono text-[11px] uppercase tracking-wide text-ink/55">{title}</p>}
          {extra}
        </div>
      )}
      {children}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="px-3 py-6 text-center font-mono text-[11px] text-ink/40">{children}</p>;
}
