import type { ReactNode } from "react";
import type { Me } from "@/api";

export const NAV_ITEMS = [
  { path: "/", icon: "◲", label: "Visão" },
  { path: "/usuarios", icon: "◔", label: "Usuários" },
  { path: "/permissoes", icon: "▦", label: "Matriz" },
  { path: "/tools", icon: "▤", label: "Tools" },
  { path: "/regras", icon: "§", label: "Regras" },
  { path: "/vault", icon: "❏", label: "Vault" },
  { path: "/auditoria", icon: "≣", label: "Auditoria" },
  { path: "/simulador", icon: "◉", label: "Simular" },
  { path: "/governanca", icon: "▥", label: "Governança" },
] as const;

function initials(identity: string): string {
  const local = identity.split("@")[0] || identity;
  const parts = local.split(/[.\-_ ]+/).filter(Boolean);
  const letters = parts.length >= 2 ? parts[0][0] + parts[1][0] : local.slice(0, 2);
  return letters.toUpperCase();
}

export function Shell({
  me,
  path,
  onNavigate,
  onLogout,
  children,
}: {
  me: Me;
  path: string;
  onNavigate: (path: string) => void;
  onLogout: () => void;
  children: ReactNode;
}) {
  return (
    <div className="min-h-screen bg-paper font-sans text-[13px] leading-snug text-ink">
      <header className="sticky top-0 z-30 flex items-center gap-4 border-b border-line bg-paper/95 px-4 py-2 backdrop-blur">
        <div className="flex items-center gap-2">
          <span className="grid size-6 place-items-center bg-navy font-mono text-[11px] text-surface">B</span>
          <span className="font-mono text-[11px] uppercase tracking-[0.18em] text-ink/70">BRM Engine</span>
        </div>
        <div className="relative ml-2 hidden max-w-xs flex-1 md:block">
          <input
            placeholder="Buscar ou comandar…"
            className="w-full rounded-md border border-line bg-surface/60 px-3 py-1.5 font-mono text-[12px] placeholder:text-ink/35 focus:border-navy focus:ring-2 focus:ring-navy/20 focus:outline-none"
          />
          <span className="absolute top-1/2 right-2 -translate-y-1/2 rounded border border-line bg-surface px-1.5 font-mono text-[10px] text-ink/40">
            ⌘K
          </span>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <span className="hidden items-center gap-1.5 rounded-full border border-line bg-surface/50 px-2.5 py-1 font-mono text-[10px] text-ink/60 sm:flex">
            <span className="size-1.5 rounded-full bg-green" /> live
          </span>
          <span className="rounded-md border border-line bg-surface/50 px-2 py-1 font-mono text-[10px] text-ink/60">
            {me.role}
          </span>
          <button
            className="grid size-6 place-items-center rounded-full bg-ink/10 font-mono text-[10px] text-ink/70 transition-colors hover:bg-navy/15 hover:text-navy"
            title="Sair"
            onClick={onLogout}
          >
            {initials(me.identity)}
          </button>
        </div>
      </header>
      <div className="flex">
        <nav className="sticky top-[41px] hidden h-[calc(100vh-41px)] w-[68px] shrink-0 flex-col border-r border-line bg-surface/40 py-3 md:flex">
          {NAV_ITEMS.map((item) => {
            const active = item.path === path;
            return (
              <button
                key={item.path}
                onClick={() => onNavigate(item.path)}
                aria-current={active ? "page" : undefined}
                className={`flex flex-col items-center gap-1 px-1 py-2 text-ink/55 transition-colors hover:bg-navy/5 hover:text-ink ${
                  active ? "!text-navy" : ""
                }`}
              >
                <span className="grid size-8 place-items-center rounded-md font-mono text-[11px]">{item.icon}</span>
                <span className="font-mono text-[9px] uppercase tracking-wide">{item.label}</span>
              </button>
            );
          })}
        </nav>
        <main className="min-w-0 flex-1 p-4">{children}</main>
      </div>
    </div>
  );
}
