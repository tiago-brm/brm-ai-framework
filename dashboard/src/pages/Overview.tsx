import { api, type Tool } from "@/api";
import { Card, Empty } from "@/components/Card";
import { DecisionBadge, EffectBadge } from "@/components/DecisionBadge";
import { PageHeader } from "@/components/PageHeader";
import { useLoadable } from "@/lib/useLoadable";

function StatCard({
  label,
  value,
  hint,
  hintClass = "text-ink/40",
  delay,
}: {
  label: string;
  value: string | number;
  hint: string;
  hintClass?: string;
  delay: number;
}) {
  return (
    <div className="rise bg-surface/70 p-3" style={{ animationDelay: `${delay}ms` }}>
      <p className="font-mono text-[10px] uppercase tracking-wide text-ink/45">{label}</p>
      <p className="mt-1 font-mono text-[26px] leading-none text-ink">{value}</p>
      <p className={`mt-1 font-mono text-[10px] ${hintClass}`}>{hint}</p>
    </div>
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
        crumb="visão geral"
        title="Visão geral"
        actions={
          <>
            <span className="rounded-md border border-line bg-surface/50 px-2 py-1 font-mono text-[10px] text-ink/50">
              atualizado agora
            </span>
            <button className="rounded-md bg-navy px-3 py-1.5 font-mono text-[11px] text-surface transition-colors hover:bg-navy/90">
              Sincronizar
            </button>
          </>
        }
      />

      <section className="grid grid-cols-2 gap-px overflow-hidden rounded-md border border-line bg-line md:grid-cols-5">
        <StatCard label="Usuários" value={users?.total ?? "—"} hint="perfis do escritório" hintClass="text-green" delay={0} />
        <StatCard label="Tools" value={tools?.total ?? "—"} hint="somente leitura" delay={60} />
        <StatCard
          label="Regras"
          value={rules?.total ?? "—"}
          hint={`${pendingRules} exigem aprovação`}
          hintClass="text-amber"
          delay={120}
        />
        <StatCard label="Notas" value={notes?.total ?? "—"} hint="vault jurídico" delay={180} />
        <StatCard
          label="Eventos"
          value={audit?.total ?? "—"}
          hint={`${deniedEvents} negados`}
          hintClass="text-red"
          delay={240}
        />
      </section>

      <section className="mt-3 grid gap-3 lg:grid-cols-3">
        <div className="rise overflow-hidden rounded-md border border-amber/40 bg-amber/5">
          <div className="flex items-center gap-2 border-b border-amber/20 bg-amber/10 px-3 py-2">
            <span className="size-2 rounded-full bg-amber" />
            <p className="font-mono text-[11px] font-medium uppercase tracking-wide text-amber">Token único</p>
          </div>
          <div className="p-3">
            <p className="text-[12px] text-ink/70">
              O <span className="font-mono text-ink">api_token</span> em texto puro aparece apenas na criação do
              usuário. Copie na hora — ele <span className="font-medium text-amber">não será exibido novamente</span>.
            </p>
            <button
              onClick={() => onNavigate("/usuarios")}
              className="mt-3 inline-block rounded bg-navy px-2.5 py-1 font-mono text-[10px] text-surface transition-colors hover:bg-navy/90"
            >
              Criar usuário
            </button>
          </div>
        </div>

        <Card
          title="Últimos eventos de auditoria"
          extra={
            <button onClick={() => onNavigate("/auditoria")} className="font-mono text-[10px] text-navy hover:underline">
              ver trilha →
            </button>
          }
          className="lg:col-span-2"
        >
          {recentEvents.length === 0 ? (
            <Empty>Nenhum evento registrado.</Empty>
          ) : (
            <div className="divide-y divide-line/70">
              {recentEvents.map((e, i) => (
                <div key={i} className="flex items-center justify-between gap-3 px-3 py-2">
                  <div className="min-w-0">
                    <p className="truncate font-mono text-[11px] text-ink">{e.action}</p>
                    <p className="font-mono text-[9px] text-ink/40">
                      {e.actor} · {e.occurred_at}
                    </p>
                  </div>
                  <DecisionBadge decision={e.decision} />
                </div>
              ))}
            </div>
          )}
        </Card>
      </section>

      <section className="mt-3 grid gap-3 xl:grid-cols-3">
        <Card
          title="Usuários"
          extra={
            <button onClick={() => onNavigate("/usuarios")} className="font-mono text-[10px] text-navy hover:underline">
              + novo
            </button>
          }
        >
          {users && users.users.length === 0 ? (
            <Empty>Nenhum usuário cadastrado.</Empty>
          ) : (
            <div className="divide-y divide-line/60">
              {(users?.users ?? []).slice(0, 6).map((u) => (
                <div key={u.identity} className="flex items-center justify-between gap-2 px-3 py-2">
                  <span className="truncate font-mono text-[11px] text-ink">{u.identity}</span>
                  <span className="shrink-0 rounded bg-ink/10 px-1.5 py-0.5 font-mono text-[9px] text-ink/60">
                    {u.role}
                  </span>
                </div>
              ))}
            </div>
          )}
        </Card>

        <Card title="Matriz de permissões" extra={<span className="font-mono text-[10px] text-ink/40">viewer × admin</span>}>
          {!tools || tools.tools.length === 0 ? (
            <Empty>Sem tools no catálogo.</Empty>
          ) : (
            <div className="divide-y divide-line/60">
              {tools.tools.slice(0, 6).map((t: Tool) => {
                const viewerAllowed =
                  perms?.permissions.find((p) => p.role === "viewer" && p.tool_name === t.name)?.allowed ?? false;
                const adminAllowed =
                  perms?.permissions.find((p) => p.role === "admin" && p.tool_name === t.name)?.allowed ?? false;
                return (
                  <div key={t.name} className="flex items-center justify-between gap-2 px-3 py-2">
                    <span className="truncate font-mono text-[11px] text-ink">{t.name}</span>
                    <span className="flex shrink-0 gap-1">
                      <span className={`rounded px-1.5 py-0.5 font-mono text-[9px] ${viewerAllowed ? "bg-green/10 text-green" : "bg-ink/5 text-ink/35"}`}>
                        v
                      </span>
                      <span className={`rounded px-1.5 py-0.5 font-mono text-[9px] ${adminAllowed ? "bg-green/10 text-green" : "bg-ink/5 text-ink/35"}`}>
                        a
                      </span>
                    </span>
                  </div>
                );
              })}
            </div>
          )}
        </Card>

        <Card title="Regras de negócio" extra={<span className="font-mono text-[10px] text-ink/40">JSONLogic</span>}>
          {rules && rules.rules.length === 0 ? (
            <Empty>Nenhuma regra configurada.</Empty>
          ) : (
            <div className="divide-y divide-line/60">
              {(rules?.rules ?? []).slice(0, 6).map((r) => (
                <div key={r.id} className="px-3 py-2">
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate font-mono text-[11px] text-ink">{r.id}</span>
                    <EffectBadge effect={r.effect} />
                  </div>
                  <p className="mt-0.5 truncate text-[11px] text-ink/50">{r.description}</p>
                </div>
              ))}
            </div>
          )}
        </Card>
      </section>

      <section className="mt-3 grid gap-3 lg:grid-cols-2">
        <Card
          title="Vault jurídico"
          extra={
            <button onClick={() => onNavigate("/vault")} className="font-mono text-[10px] text-navy hover:underline">
              {notes?.total ?? 0} notas
            </button>
          }
        >
          {notes && notes.notes.length === 0 ? (
            <Empty>Vault vazio.</Empty>
          ) : (
            <div className="divide-y divide-line/60">
              {(notes?.notes ?? []).slice(0, 6).map((n) => (
                <div key={n.note_id} className="px-3 py-2">
                  <p className="truncate font-mono text-[11px] text-ink">{n.title}</p>
                  <p className="mt-0.5 font-mono text-[9px] text-ink/40">{n.tags.join(", ") || "sem tags"}</p>
                </div>
              ))}
            </div>
          )}
        </Card>

        <Card title="Catálogo de tools" extra={<span className="rounded bg-ink/10 px-1.5 font-mono text-[9px] text-ink/55">somente leitura</span>}>
          {!tools || tools.tools.length === 0 ? (
            <Empty>Nenhuma tool registrada.</Empty>
          ) : (
            <div className="grid grid-cols-2 gap-px bg-line">
              {tools.tools.slice(0, 8).map((t) => (
                <div key={t.name} className="bg-surface p-2">
                  <p className="truncate font-mono text-[11px] text-ink">{t.name}</p>
                  <p className="mt-0.5 truncate text-[10px] text-ink/45">{t.description || "sem descrição"}</p>
                </div>
              ))}
            </div>
          )}
        </Card>
      </section>
    </>
  );
}
