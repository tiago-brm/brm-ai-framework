import { useEffect, useMemo, useRef, useState } from "react";
import { Eye, Play, Search, UserRoundCheck } from "lucide-react";
import {
  api,
  type SimCenario,
  type SimPerfil,
  type SimResultado,
  type SimSkill,
  type SimVault,
} from "@/api";
import { Card, Empty, ErrorState } from "@/components/Card";
import { DecisionBadge } from "@/components/DecisionBadge";
import { PageHeader } from "@/components/PageHeader";
import { useLoadable } from "@/lib/useLoadable";

type Execucao = { titulo: string; skill: string; argumentos: Record<string, unknown> };

const ROLE_BADGE: Record<string, string> = {
  viewer: "badge-outline-muted",
  admin: "badge-outline-info",
  super_admin: "badge-outline-warning",
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
      <PageHeader
        title="Simulador"
        description="O mesmo pedido, decidido como outro perfil decidiria."
        icon={Eye}
      />

      <div
        role="status"
        className={`grid-margin flex flex-wrap items-center justify-between gap-2 rounded-lg border px-4 py-3 ${
          perfilAtual ? "border-warning/40 bg-warning/10 text-warning" : "bg-card text-muted-foreground"
        }`}
      >
        {perfilAtual ? (
          <>
            <span className="flex items-center gap-2">
              <UserRoundCheck size={16} aria-hidden="true" />
              Simulando como <strong>{perfilAtual.rotulo}</strong> · {perfilAtual.role}
            </span>
            <button
              type="button"
              onClick={() => {
                setPerfil(null);
                ultimoPerfil.current = null;
                setResultado(null);
                setVaultView(null);
                setExecucao(null);
              }}
              className="btn btn-ghost btn-sm underline"
            >
              Voltar à minha sessão
            </button>
          </>
        ) : (
          <span>Escolha um perfil para simular. Nada é enviado de verdade, e tudo fica na auditoria.</span>
        )}
      </div>

      <Card title="Perfis" className="grid-margin">
        {perfis.error && <ErrorState>{perfis.error}</ErrorState>}
        <div className="grid grid-cols-2 gap-3 px-6 pb-6 md:grid-cols-4 xl:grid-cols-7">
          {perfis.data?.perfis.map((p) => {
            const ativo = p.identity === perfil;
            return (
              <button
                key={p.identity}
                type="button"
                onClick={() => escolherPerfil(p.identity)}
                aria-pressed={ativo}
                className={`rounded-lg border p-3 text-left transition-colors ${
                  ativo ? "border-primary bg-primary/10" : "hover:border-primary/50"
                }`}
              >
                <span className="block font-medium">{short(p.rotulo)}</span>
                <span className="block truncate text-xs text-muted-foreground">
                  {p.rotulo.includes(" · ") ? p.rotulo.split(" · ")[1] : p.identity}
                </span>
                <span className={`mt-2 ${ROLE_BADGE[p.role] ?? ROLE_BADGE.viewer}`}>{p.role}</span>
              </button>
            );
          })}
        </div>
      </Card>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,360px)_minmax(0,1fr)]">
        <div className="space-y-6">
          <Card title="Cenários">
            {cenarios.error && <ErrorState>{cenarios.error}</ErrorState>}
            {cenarios.data && cenarios.data.cenarios.length === 0 && (
              <Empty>Nenhum cenário em demo_scenarios.yaml.</Empty>
            )}
            <ul className="max-h-[420px] overflow-y-auto px-3 pb-3">
              {cenarios.data?.cenarios.map((c) => (
                <li key={c.id}>
                  <button
                    type="button"
                    onClick={() => escolherCenario(c)}
                    aria-pressed={execucao?.titulo === c.titulo}
                    className={`block w-full rounded-lg px-3 py-2 text-left transition-colors hover:bg-muted ${
                      execucao?.titulo === c.titulo ? "bg-primary/10" : ""
                    }`}
                  >
                    <span className="block">{c.titulo}</span>
                    <span className="code block text-xs text-muted-foreground">{c.skill}</span>
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

        <div className="space-y-6">
          <Card title={execucao ? `Resultado · ${execucao.titulo}` : "Resultado"}>
            {erro && <ErrorState>{erro}</ErrorState>}
            {!resultado && !erro && (
              <Empty icon={Play}>Escolha um cenário. Depois clique em outro perfil para repetir o mesmo pedido.</Empty>
            )}
            {resultado && <PainelResultado r={resultado} />}
          </Card>

          <Card title="O que este perfil vê no segundo cérebro">
            <form
              className="flex gap-2 px-6 pb-4"
              onSubmit={(e) => {
                e.preventDefault();
                if (perfil) void buscarVault(perfil, consulta);
              }}
            >
              <label htmlFor="sim-consulta" className="sr-only">
                Consulta ao segundo cérebro
              </label>
              <input
                id="sim-consulta"
                value={consulta}
                onChange={(e) => setConsulta(e.target.value)}
                className="input min-w-0 flex-1"
              />
              <button type="submit" disabled={!perfil} className="btn btn-primary">
                <Search size={16} aria-hidden="true" />
                Buscar
              </button>
            </form>
            {vaultView && (
              <div className="border-t px-6 pt-3 pb-2">
                <p className="text-xs text-muted-foreground">
                  {vaultView.total} notas recebidas · {vaultView.ocultas} ocultas
                  {vaultView.politicas.length > 0 && ` · políticas: ${vaultView.politicas.join(", ")}`}
                </p>
                <ul className="preview-list">
                  {vaultView.notas.map((n) => (
                    <li key={n.note_id} className="preview-item">
                      <span className={n.modo === "completa" ? "badge-outline-success" : "badge-outline-warning"}>
                        {n.modo === "completa" ? "completa" : "só ficha"}
                      </span>
                      <div className="preview-item-content">
                        <p className="preview-subject truncate">
                          {n.title} <span className="font-normal text-muted-foreground">· {n.tipo ?? "—"}</span>
                        </p>
                        {n.trecho && <p className="truncate text-xs text-muted-foreground">{n.trecho}</p>}
                      </div>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </Card>

          <Card title="Trilha de auditoria · últimos eventos">
            {eventos.length === 0 ? (
              <Empty>Sem eventos ainda.</Empty>
            ) : (
              <ul className="preview-list px-6 pb-2">
                {eventos.map((e, i) => (
                  <li key={i} className="preview-item items-center">
                    <span className="w-36 shrink-0 truncate text-xs text-muted-foreground">
                      {e.occurred_at.slice(11, 19)} · {e.actor.split("@")[0]}
                    </span>
                    <span className="preview-item-content code truncate">{e.action}</span>
                    <DecisionBadge decision={e.decision} />
                  </li>
                ))}
              </ul>
            )}
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
    allowed: "text-success",
    denied: "text-danger",
    pending_approval: "text-warning",
  };
  const res = r.resultado;
  return (
    <div className="space-y-3 px-6 pb-6">
      <div className="flex flex-wrap items-center gap-3">
        <span className={`text-xl font-semibold ${cor[r.decisao] ?? ""}`}>{titulo[r.decisao] ?? r.decisao}</span>
        <DecisionBadge decision={r.decisao} />
      </div>
      {res.rule_id && (
        <p className="text-muted-foreground">
          Regra: <span className="code text-foreground">{res.rule_id}</span>
        </p>
      )}
      {(res.message || res.reason) && res.reason !== "human approval required" && <p>{res.message ?? res.reason}</p>}
      {res.reason === "human approval required" && res.message && <p>{res.message}</p>}
      {res.proximo_passo && (
        <p className="rounded-lg bg-muted px-3 py-2">
          <span className="font-medium">Próximo passo: </span>
          {res.proximo_passo}
        </p>
      )}
      <div className="flex flex-wrap gap-1.5">
        {Object.entries(r.fatos_derivados).map(([k, v]) => (
          <span key={k} className="badge-outline-muted code">
            {k} = {String(v)}
          </span>
        ))}
        {Object.entries(r.dlp).map(([label, valores]) => (
          <span key={label} className="badge-outline-danger">
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
      <form onSubmit={enviar} className="space-y-3 px-6 pb-6">
        <div>
          <label htmlFor="sim-skill" className="label">
            Skill
          </label>
          <select
            id="sim-skill"
            value={nome}
            onChange={(e) => {
              setNome(e.target.value);
              setValores({});
            }}
            className="input"
          >
            <option value="">Escolha uma skill…</option>
            {skills.map((s) => (
              <option key={s.name} value={s.name}>
                {s.name}
              </option>
            ))}
          </select>
          {skill && <p className="hint">{skill.description}</p>}
        </div>
        {skill &&
          Object.entries(skill.parameters).map(([chave, param]) =>
            param.type === "boolean" ? (
              <label key={chave} className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={Boolean(valores[chave])}
                  onChange={(e) => setValores({ ...valores, [chave]: e.target.checked })}
                />
                <span className="code">{chave}</span>
              </label>
            ) : (
              <div key={chave}>
                <label htmlFor={`sim-${chave}`} className="label code">
                  {chave}
                  {param.required && " *"}
                </label>
                <input
                  id={`sim-${chave}`}
                  type={param.type === "number" ? "number" : "text"}
                  value={String(valores[chave] ?? "")}
                  placeholder={param.description ?? ""}
                  onChange={(e) => setValores({ ...valores, [chave]: e.target.value })}
                  className="input"
                />
              </div>
            ),
          )}
        <button type="submit" disabled={disabled || !skill} className="btn btn-primary w-full">
          <Play size={16} aria-hidden="true" />
          Executar como o perfil escolhido
        </button>
        {disabled && <p className="hint">Escolha um perfil acima para liberar.</p>}
      </form>
    </Card>
  );
}
