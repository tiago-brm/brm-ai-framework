import { ArrowRight, BookOpen, KeyRound, LayoutDashboard, Scale, ScrollText, Users, Wrench, type LucideIcon } from "lucide-react";
import { api, type Tool } from "@/api";
import { Card, Empty } from "@/components/Card";
import { DecisionBadge, EffectBadge } from "@/components/DecisionBadge";
import { PageHeader } from "@/components/PageHeader";
import { useLoadable } from "@/lib/useLoadable";

function StatCard({
  label,
  value,
  hint,
  hintClass = "text-muted-foreground",
  icon: Icon,
  tone,
}: {
  label: string;
  value: string | number;
  hint: string;
  hintClass?: string;
  icon: LucideIcon;
  tone: string;
}) {
  return (
    <div className="card mt-4">
      <div className="card-body pt-0">
        <div className="flex items-start justify-between">
          <div className={`icon-box icon-box-floating icon-box-${tone} -mt-5`}>
            <Icon size={24} aria-hidden="true" />
          </div>
          <div className="pt-3 text-right">
            <p className="text-muted-foreground">{label}</p>
            <p className="text-2xl font-semibold">{value}</p>
          </div>
        </div>
        <div className="stat-divider" />
        <p className={hintClass}>{hint}</p>
      </div>
    </div>
  );
}

function LinkButton({ onClick, children }: { onClick: () => void; children: React.ReactNode }) {
  return (
    <button type="button" onClick={onClick} className="btn btn-ghost btn-sm text-primary">
      {children}
      <ArrowRight size={14} aria-hidden="true" />
    </button>
  );
}

export function Overview({ onNavigate }: { onNavigate: (path: string) => void }) {
  const { data: users } = useLoadable(() => api.listUsers());
  const { data: tools } = useLoadable(() => api.listTools());
  const { data: rules } = useLoadable(() => api.listRules());
  const { data: notes } = useLoadable(() => api.listVaultNotes());
  const { data: audit } = useLoadable(() => api.listAudit());
  const { data: perms } = useLoadable(() => api.listPermissions());

  const pendingRules = rules?.rules.filter((r) => r.effect === "require_approval").length ?? 0;
  const deniedEvents = audit?.events.filter((e) => e.decision === "denied").length ?? 0;
  const recentEvents = audit?.events.slice(0, 6) ?? [];

  return (
    <>
      <PageHeader
        title="Visão geral"
        description="O que o motor governa agora: pessoas, tools, regras, conhecimento e auditoria."
        icon={LayoutDashboard}
      />

      <section className="grid-margin grid gap-6 sm:grid-cols-2 xl:grid-cols-5">
        <StatCard label="Usuários" value={users?.total ?? "—"} hint="perfis com acesso" icon={Users} tone="dark" />
        <StatCard label="Tools" value={tools?.total ?? "—"} hint="expostas pelo MCP" icon={Wrench} tone="info" />
        <StatCard
          label="Regras"
          value={rules?.total ?? "—"}
          hint={`${pendingRules} exigem aprovação`}
          hintClass="text-warning"
          icon={Scale}
          tone="warning"
        />
        <StatCard label="Notas" value={notes?.total ?? "—"} hint="no segundo cérebro" icon={BookOpen} tone="success" />
        <StatCard
          label="Eventos"
          value={audit?.total ?? "—"}
          hint={`${deniedEvents} negados`}
          hintClass="text-danger"
          icon={ScrollText}
          tone="primary"
        />
      </section>

      <section className="grid-margin grid gap-6 lg:grid-cols-3">
        <div className="md2-gradient-card flex flex-col gap-3 p-6">
          <div className="flex items-center gap-2">
            <KeyRound size={20} aria-hidden="true" />
            <h2 className="text-base font-semibold">Token mostrado uma única vez</h2>
          </div>
          <p className="text-white/90">
            O token de acesso aparece só na criação do usuário. Copie na hora: ele não será exibido de novo.
          </p>
          <button
            type="button"
            onClick={() => onNavigate("/usuarios")}
            className="btn mt-auto self-start bg-white/90 text-[hsl(243_75%_40%)] hover:bg-white"
          >
            Criar usuário
          </button>
        </div>

        <Card
          title="Últimos eventos de auditoria"
          extra={<LinkButton onClick={() => onNavigate("/auditoria")}>Ver trilha</LinkButton>}
          className="lg:col-span-2"
        >
          {recentEvents.length === 0 ? (
            <Empty>Nenhum evento registrado.</Empty>
          ) : (
            <ul className="preview-list px-6 pb-2">
              {recentEvents.map((e, i) => (
                <li key={i} className="preview-item items-center">
                  <div className="preview-item-content">
                    <p className="preview-subject code truncate">{e.action}</p>
                    <p className="text-xs text-muted-foreground">
                      {e.actor} · {e.occurred_at}
                    </p>
                  </div>
                  <DecisionBadge decision={e.decision} />
                </li>
              ))}
            </ul>
          )}
        </Card>
      </section>

      <section className="grid-margin grid gap-6 xl:grid-cols-3">
        <Card title="Usuários" extra={<LinkButton onClick={() => onNavigate("/usuarios")}>Novo</LinkButton>}>
          {users && users.users.length === 0 ? (
            <Empty>Nenhum usuário cadastrado.</Empty>
          ) : (
            <ul className="preview-list px-6 pb-2">
              {(users?.users ?? []).slice(0, 6).map((u) => (
                <li key={u.identity} className="preview-item items-center">
                  <span className="preview-item-content truncate">{u.identity}</span>
                  <span className="badge-outline-muted">{u.role}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card title="Permissões" extra={<span className="text-xs text-muted-foreground">viewer · admin</span>}>
          {!tools || tools.tools.length === 0 ? (
            <Empty>Sem tools no catálogo.</Empty>
          ) : (
            <ul className="preview-list px-6 pb-2">
              {tools.tools.slice(0, 6).map((t: Tool) => {
                const viewerAllowed =
                  perms?.permissions.find((p) => p.role === "viewer" && p.tool_name === t.name)?.allowed ?? false;
                const adminAllowed =
                  perms?.permissions.find((p) => p.role === "admin" && p.tool_name === t.name)?.allowed ?? false;
                return (
                  <li key={t.name} className="preview-item items-center">
                    <span className="preview-item-content code truncate">{t.name}</span>
                    <span className="flex shrink-0 gap-1">
                      <span
                        className={viewerAllowed ? "badge-outline-success" : "badge-outline-muted"}
                        title={`viewer: ${viewerAllowed ? "permitido" : "negado"}`}
                      >
                        viewer
                      </span>
                      <span
                        className={adminAllowed ? "badge-outline-success" : "badge-outline-muted"}
                        title={`admin: ${adminAllowed ? "permitido" : "negado"}`}
                      >
                        admin
                      </span>
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
        </Card>

        <Card title="Regras de negócio" extra={<LinkButton onClick={() => onNavigate("/regras")}>Ver todas</LinkButton>}>
          {rules && rules.rules.length === 0 ? (
            <Empty>Nenhuma regra configurada.</Empty>
          ) : (
            <ul className="preview-list px-6 pb-2">
              {(rules?.rules ?? []).slice(0, 6).map((r) => (
                <li key={r.id} className="preview-item">
                  <div className="preview-item-content">
                    <p className="preview-subject code truncate">{r.id}</p>
                    <p className="truncate text-xs text-muted-foreground">{r.description}</p>
                  </div>
                  <EffectBadge effect={r.effect} />
                </li>
              ))}
            </ul>
          )}
        </Card>
      </section>

      <section className="grid gap-6 lg:grid-cols-2">
        <Card
          title="Segundo cérebro"
          extra={<LinkButton onClick={() => onNavigate("/vault")}>{notes?.total ?? 0} notas</LinkButton>}
        >
          {notes && notes.notes.length === 0 ? (
            <Empty>Nenhuma nota ainda.</Empty>
          ) : (
            <ul className="preview-list px-6 pb-2">
              {(notes?.notes ?? []).slice(0, 6).map((n) => (
                <li key={n.note_id} className="preview-item">
                  <div className="preview-item-content">
                    <p className="preview-subject truncate">{n.title}</p>
                    <p className="text-xs text-muted-foreground">{n.tags.join(", ") || "sem tags"}</p>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card title="Catálogo de tools" extra={<span className="badge-outline-muted">somente leitura</span>}>
          {!tools || tools.tools.length === 0 ? (
            <Empty>Nenhuma tool registrada.</Empty>
          ) : (
            <div className="grid gap-3 px-6 pb-6 sm:grid-cols-2">
              {tools.tools.slice(0, 8).map((t) => (
                <div key={t.name} className="rounded-lg border p-3">
                  <p className="code truncate font-medium">{t.name}</p>
                  <p className="mt-0.5 truncate text-xs text-muted-foreground">{t.description || "sem descrição"}</p>
                </div>
              ))}
            </div>
          )}
        </Card>
      </section>
    </>
  );
}
