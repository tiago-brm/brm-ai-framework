import type { ReactNode } from "react";
import {
  BookOpen,
  Eye,
  Grid3x3,
  LayoutDashboard,
  LogOut,
  Moon,
  Scale,
  ScrollText,
  ShieldCheck,
  ShieldHalf,
  Sun,
  Users,
  Wrench,
  type LucideIcon,
} from "lucide-react";
import type { Me } from "@/api";
import { useTheme } from "@/lib/theme";

type NavItem = { path: string; label: string; icon: LucideIcon };

const GROUPS: { title: string; items: NavItem[] }[] = [
  {
    title: "Operação",
    items: [
      { path: "/", label: "Visão geral", icon: LayoutDashboard },
      { path: "/simulador", label: "Simulador", icon: Eye },
      { path: "/auditoria", label: "Auditoria", icon: ScrollText },
    ],
  },
  {
    title: "Governança",
    items: [
      { path: "/regras", label: "Regras", icon: Scale },
      { path: "/governanca", label: "Quem pode o quê", icon: ShieldHalf },
      { path: "/permissoes", label: "Permissões", icon: Grid3x3 },
      { path: "/tools", label: "Tools", icon: Wrench },
    ],
  },
  {
    title: "Conhecimento",
    items: [{ path: "/vault", label: "Segundo cérebro", icon: BookOpen }],
  },
  {
    title: "Administração",
    items: [{ path: "/usuarios", label: "Usuários", icon: Users }],
  },
];

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
  const { dark, toggle } = useTheme();
  const items = GROUPS.flatMap((g) => g.items);

  return (
    <div className="flex min-h-screen">
      <aside
        className="sidebar-surface sticky top-0 hidden h-screen w-64 shrink-0 flex-col gap-6 overflow-y-auto p-4 md:flex"
        aria-label="Navegação principal"
      >
        <div className="flex items-center gap-3 px-2 pt-2 text-white">
          <div className="icon-box icon-box-sm bg-gradient-primary">
            <ShieldCheck size={20} aria-hidden="true" />
          </div>
          <div className="leading-tight">
            <p className="text-lg font-semibold">BRM Engine</p>
            <p className="text-xs text-white/60">Governança de IA</p>
          </div>
        </div>
        <nav className="flex flex-col gap-5">
          {GROUPS.map((group) => (
            <div key={group.title} className="flex flex-col gap-1">
              <p className="px-3 text-xs tracking-wide text-white/50 uppercase">{group.title}</p>
              {group.items.map(({ path: to, label, icon: Icon }) => (
                <button
                  key={to}
                  type="button"
                  onClick={() => onNavigate(to)}
                  aria-current={to === path ? "page" : undefined}
                  className="sidebar-link"
                >
                  <Icon size={18} aria-hidden="true" />
                  {label}
                </button>
              ))}
            </div>
          ))}
        </nav>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-3 px-4 py-3 sm:px-7">
          <nav className="flex min-w-0 flex-1 gap-1 overflow-x-auto md:hidden" aria-label="Navegação móvel">
            {items.map(({ path: to, label, icon: Icon }) => (
              <button
                key={to}
                type="button"
                onClick={() => onNavigate(to)}
                aria-label={label}
                aria-pressed={to === path}
                className="btn btn-ghost px-2"
              >
                <Icon size={18} aria-hidden="true" />
              </button>
            ))}
          </nav>
          <div className="ml-auto flex shrink-0 items-center gap-2">
            <span className="hidden max-w-56 truncate text-muted-foreground sm:block">{me.identity}</span>
            <span className="badge-outline-primary">{me.role}</span>
            <button
              type="button"
              className="btn btn-ghost px-2"
              onClick={toggle}
              aria-label={dark ? "Usar tema claro" : "Usar tema escuro"}
            >
              {dark ? <Sun size={18} aria-hidden="true" /> : <Moon size={18} aria-hidden="true" />}
            </button>
            <button type="button" className="btn btn-ghost px-2" onClick={onLogout} aria-label="Sair">
              <LogOut size={18} aria-hidden="true" />
            </button>
          </div>
        </header>
        <main className="content-wrapper flex-1 animate-fade-in-up">{children}</main>
      </div>
    </div>
  );
}
