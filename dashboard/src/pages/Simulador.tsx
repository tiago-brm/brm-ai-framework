import { useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  type SimCenario,
  type SimPerfil,
  type SimResultado,
  type SimSkill,
  type SimVault,
} from "@/api";
import { Card, Empty } from "@/components/Card";
import { DecisionBadge } from "@/components/DecisionBadge";
import { PageHeader } from "@/components/PageHeader";
import { useLoadable } from "@/lib/useLoadable";

type Execucao = { titulo: string; skill: string; argumentos: Record<string, unknown> };

const ROLE_STYLE: Record<string, string> = {
  viewer: "border-line text-ink/60",
  admin: "border-navy/30 text-navy",
  super_admin: "border-amber/40 text-amber",
};

function short(rotulo: string) {
  return rotulo.split(" · ")[0];
}

export function Simulador() {
  const perfis = useLoadable(() => api.simPerfis());
  const cenarios = useLoadable(() => api.simCenarios());
  const skills = useLoadable(() => api.simSkills());
  const audit = useLoadable(() => api.listAudit());

  const [perfil, setPerfil] = useState<string | null>(null);
  const [execucao, setExecucao] = useState<Execucao | null>(null);
  const [resultado, setResultado] = useState<SimResultado | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [consulta, setConsulta] = useState("hipótese de trabalho autocrítica procrastinação");
  const [vaultView, setVaultView] = useState<SimVault | null>(null);

  const perfilAtual: SimPerfil | undefined = useMemo(
    () => perfis.data?.perfis.find((p) => p.identity === perfil),
    [perfis.data, perfil],
  );
  const ultimoPerfil = useRef<string | null>(null);

  async function executar(email: string, ex: Execucao) {
    setErro(null);
    try {
      setResultado(await api.simExecutar(email, ex.skill, ex.argumentos));
      audit.reload();
    } catch (e) {
      setResultado(null);
      setErro(String(e));
    }
  }

  async function buscarVault(email: string, q: string) {
    try {
      setVaultView(await api.simVault(email, q));
      audit.reload();
    } catch (e) {
      setVaultView(null);
      setErro(String(e));
    }
  }

  function escolherPerfil(email: string) {
    setPerfil(email);
  }

  function escolherCenario(c: SimCenario) {
    const email = c.perfil ?? perfil ?? perfis.data?.perfis[0]?.identity ?? null;
    if (!email) return;
    const ex = { titulo: c.titulo, skill: c.skill, argumentos: c.argumentos };
    setPerfil(email);
    setExecucao(ex);
    ultimoPerfil.current = email;
    void executar(email, ex);
  }

  useEffect(() => {
    if (!perfil || perfil === ultimoPerfil.current) return;
    ultimoPerfil.current = perfil;
    if (execucao) void executar(perfil, execucao);
    if (vaultView) void buscarVault(perfil, consulta);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [perfil]);

  const eventos = [...(audit.data?.events ?? [])].reverse().slice(0, 8);

  return (
    <>
      <PageHeader crumb="simulador" title="Ver como outro perfil" />

      <div
        className={`mb-3 flex flex-wrap items-center justify-between gap-2 rounded-md border px-3 py-2 font-mono text-[11px] ${
          perfilAtual
            ? "border-amber/40 bg-amber/10 text-amber"
            : "border-line bg-surface/50 text-ink/55"
        }`}
      >
        {perfilAtual ? (
          <>
            <span>
              Simulando como <strong>{perfilAtual.rotulo}</strong> · {perfilAtual.role}
            </span>
            <button
              onClick={() => {
                setPerfil(null);
                ultimoPerfil.current = null;
                setResultado(null);
                setVaultView(null);
                setExecucao(null);
              }}
              className="underline"
            >
              voltar à minha sessão
            </button>
          </>
        ) : (
          <span>Escolha um perfil para simular. Nada é enviado de verdade e tudo fica na auditoria.</span>
        )}
      </div>

      <Card title="Perfis">
        {perfis.error && <Empty>{perfis.error}</Empty>}
        <div className="grid grid-cols-2 gap-2 p-3 md:grid-cols-4 xl:grid-cols-7">
          {perfis.data?.perfis.map((p) => {
            const ativo = p.identity === perfil;
            return (
              <button
                key={p.identity}
                onClick={() => escolherPerfil(p.identity)}
                aria-pressed={ativo}
                className={`rounded-md border px-2.5 py-2 text-left transition-colors ${
                  ativo
                    ? "border-navy bg-navy/10"
                    : "border-line bg-surface/50 hover:border-navy/40"
                }`}
              >
                <span className="block text-[12px] font-medium text-ink">{short(p.rotulo)}</span>
                <span className="block truncate text-[10px] text-ink/50">
                  {p.rotulo.includes(" · ") ? p.rotulo.split(" · ")[1] : p.identity}
                </span>
                <span
                  className={`mt-1 inline-block rounded border px-1 font-mono text-[9px] ${
                    ROLE_STYLE[p.role] ?? ROLE_STYLE.viewer
                  }`}
                >
                  {p.role}
                </span>
              </button>
            );
          })}
        </div>
      </Card>

      <div className="mt-3 grid gap-3 lg:grid-cols-[minmax(0,340px)_minmax(0,1fr)]">
        <div className="space-y-3">
          <Card title="Cenários">
            {cenarios.error && <Empty>{cenarios.error}</Empty>}
            {cenarios.data && cenarios.data.cenarios.length === 0 && (
              <Empty>Nenhum cenário em demo_scenarios.yaml.</Empty>
            )}
            <ul className="max-h-[420px] divide-y divide-line/60 overflow-y-auto">
              {cenarios.data?.cenarios.map((c) => (
                <li key={c.id}>
                  <button
                    onClick={() => escolherCenario(c)}
                    className={`block w-full px-3 py-2 text-left text-[12px] transition-colors hover:bg-navy/5 ${
                      execucao?.titulo === c.titulo ? "bg-navy/10" : ""
                    }`}
                  >
                    <span className="block text-ink">{c.titulo}</span>
                    <span className="block font-mono text-[10px] text-ink/45">{c.skill}</span>
                  </button>
                </li>
              ))}
            </ul>
          </Card>

          <FormularioLivre
            skills={skills.data?.skills ?? []}
            disabled={!perfil}
            onRun={(skill, argumentos) => {
              if (!perfil) return;
              const ex = { titulo: `Formulário livre · ${skill}`, skill, argumentos };
              setExecucao(ex);
              void executar(perfil, ex);
            }}
          />
        </div>

        <div className="space-y-3">
          <Card title={execucao ? `Resultado · ${execucao.titulo}` : "Resultado"}>
            {erro && <Empty>{erro}</Empty>}
            {!resultado && !erro && (
              <Empty>Escolha um cenário. Depois clique em outro perfil para repetir o mesmo pedido.</Empty>
            )}
            {resultado && <PainelResultado r={resultado} />}
          </Card>

          <Card title="O que este perfil vê no vault">
            <div className="flex gap-2 p-3">
              <input
                value={consulta}
                onChange={(e) => setConsulta(e.target.value)}
                className="min-w-0 flex-1 rounded-md border border-line bg-surface px-2.5 py-1.5 font-mono text-[11px] focus:border-navy focus:outline-none"
              />
              <button
                disabled={!perfil}
                onClick={() => perfil && void buscarVault(perfil, consulta)}
                className="rounded-md bg-navy px-3 py-1.5 font-mono text-[11px] text-surface disabled:opacity-40"
              >
                buscar
              </button>
            </div>
            {vaultView && (
              <div className="border-t border-line">
                <p className="px-3 py-1.5 font-mono text-[10px] text-ink/50">
                  {vaultView.total} notas recebidas · {vaultView.ocultas} ocultas
                  {vaultView.politicas.length > 0 && ` · políticas: ${vaultView.politicas.join(", ")}`}
                </p>
                <ul className="divide-y divide-line/60">
                  {vaultView.notas.map((n) => (
                    <li key={n.note_id} className="flex items-start gap-2 px-3 py-1.5">
                      <span
                        className={`mt-0.5 shrink-0 rounded border px-1.5 font-mono text-[9px] uppercase ${
                          n.modo === "completa"
                            ? "border-green/30 bg-green/10 text-green"
                            : "border-amber/30 bg-amber/10 text-amber"
                        }`}
                      >
                        {n.modo === "completa" ? "completa" : "só ficha"}
                      </span>
                      <div className="min-w-0">
                        <p className="truncate font-mono text-[11px] text-ink">
                          {n.title} <span className="text-ink/40">· {n.tipo ?? "—"}</span>
                        </p>
                        {n.trecho && <p className="truncate text-[11px] text-ink/50">{n.trecho}</p>}
                      </div>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </Card>

          <Card title="Trilha de auditoria · últimos eventos">
            <ul className="divide-y divide-line/60">
              {eventos.map((e, i) => (
                <li key={i} className="flex items-center gap-2 px-3 py-1.5">
                  <span className="w-[150px] shrink-0 truncate font-mono text-[10px] text-ink/50">
                    {e.occurred_at.slice(11, 19)} · {e.actor.split("@")[0]}
                  </span>
                  <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-ink">{e.action}</span>
                  <DecisionBadge decision={e.decision} />
                </li>
              ))}
              {eventos.length === 0 && <Empty>Sem eventos ainda.</Empty>}
            </ul>
          </Card>
        </div>
      </div>
    </>
  );
}

function PainelResultado({ r }: { r: SimResultado }) {
  const titulo: Record<string, string> = {
    allowed: "Permitido",
    denied: "Negado",
    pending_approval: "Aguardando aprovação humana",
  };
  const cor: Record<string, string> = {
    allowed: "text-green",
    denied: "text-red",
    pending_approval: "text-amber",
  };
  const res = r.resultado;
  return (
    <div className="space-y-2 p-3">
      <div className="flex items-center gap-2">
        <span className={`font-display text-[22px] italic ${cor[r.decisao] ?? "text-ink"}`}>
          {titulo[r.decisao] ?? r.decisao}
        </span>
        <DecisionBadge decision={r.decisao} />
      </div>
      {res.rule_id && (
        <p className="font-mono text-[11px] text-ink/60">
          regra: <span className="text-ink">{res.rule_id}</span>
        </p>
      )}
      {(res.message || res.reason) && res.reason !== "human approval required" && (
        <p className="text-[12px] text-ink">{res.message ?? res.reason}</p>
      )}
      {res.reason === "human approval required" && res.message && (
        <p className="text-[12px] text-ink">{res.message}</p>
      )}
      {res.proximo_passo && (
        <p className="rounded-md border border-line bg-surface/70 px-2.5 py-1.5 text-[12px] text-ink/80">
          <span className="font-mono text-[10px] uppercase text-ink/40">próximo passo </span>
          {res.proximo_passo}
        </p>
      )}
      <div className="flex flex-wrap gap-1.5">
        {Object.entries(r.fatos_derivados).map(([k, v]) => (
          <span key={k} className="rounded border border-line bg-surface px-1.5 py-0.5 font-mono text-[10px] text-ink/70">
            {k} = {String(v)}
          </span>
        ))}
        {Object.entries(r.dlp).map(([label, valores]) => (
          <span key={label} className="rounded border border-red/30 bg-red/10 px-1.5 py-0.5 font-mono text-[10px] text-red">
            DLP {label}: {valores.length}
          </span>
        ))}
      </div>
    </div>
  );
}

function FormularioLivre({
  skills,
  disabled,
  onRun,
}: {
  skills: SimSkill[];
  disabled: boolean;
  onRun: (skill: string, argumentos: Record<string, unknown>) => void;
}) {
  const [nome, setNome] = useState("");
  const [valores, setValores] = useState<Record<string, string | boolean>>({});
  const skill = skills.find((s) => s.name === nome);

  function enviar(e: React.FormEvent) {
    e.preventDefault();
    if (!skill) return;
    const args: Record<string, unknown> = {};
    for (const [chave, param] of Object.entries(skill.parameters)) {
      const v = valores[chave];
      if (v === undefined || v === "") continue;
      args[chave] = param.type === "number" ? Number(v) : v;
    }
    onRun(skill.name, args);
  }

  return (
    <Card title="Formulário livre">
      <form onSubmit={enviar} className="space-y-2 p-3">
        <select
          value={nome}
          onChange={(e) => {
            setNome(e.target.value);
            setValores({});
          }}
          className="w-full rounded-md border border-line bg-surface px-2 py-1.5 font-mono text-[11px] focus:border-navy focus:outline-none"
        >
          <option value="">Escolha uma skill…</option>
          {skills.map((s) => (
            <option key={s.name} value={s.name}>
              {s.name}
            </option>
          ))}
        </select>
        {skill && <p className="text-[11px] text-ink/55">{skill.description}</p>}
        {skill &&
          Object.entries(skill.parameters).map(([chave, param]) =>
            param.type === "boolean" ? (
              <label key={chave} className="flex items-center gap-2 text-[11px] text-ink/70">
                <input
                  type="checkbox"
                  checked={Boolean(valores[chave])}
                  onChange={(e) => setValores({ ...valores, [chave]: e.target.checked })}
                />
                <span className="font-mono">{chave}</span>
              </label>
            ) : (
              <label key={chave} className="block">
                <span className="font-mono text-[9px] uppercase tracking-wide text-ink/40">
                  {chave}
                  {param.required && " *"}
                </span>
                <input
                  type={param.type === "number" ? "number" : "text"}
                  value={String(valores[chave] ?? "")}
                  placeholder={param.description ?? ""}
                  onChange={(e) => setValores({ ...valores, [chave]: e.target.value })}
                  className="mt-0.5 w-full rounded-md border border-line bg-surface px-2 py-1 font-mono text-[11px] placeholder:text-ink/30 focus:border-navy focus:outline-none"
                />
              </label>
            ),
          )}
        <button
          type="submit"
          disabled={disabled || !skill}
          className="rounded-md bg-navy px-3 py-1.5 font-mono text-[11px] text-surface disabled:opacity-40"
        >
          executar como o perfil escolhido
        </button>
      </form>
    </Card>
  );
}
