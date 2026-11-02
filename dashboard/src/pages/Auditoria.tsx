import { useMemo, useState } from "react";
import { RotateCcw, ScrollText } from "lucide-react";
import { api } from "@/api";
import { Card, Empty, ErrorState } from "@/components/Card";
import { DecisionBadge } from "@/components/DecisionBadge";
import { PageHeader } from "@/components/PageHeader";
import { useLoadable } from "@/lib/useLoadable";

const DECISIONS = [
  { value: "todas", label: "Todas" },
  { value: "allowed", label: "Permitidas" },
  { value: "denied", label: "Negadas" },
  { value: "pending_approval", label: "Aguardando aprovação" },
] as const;

type Decision = (typeof DECISIONS)[number]["value"];

export function Auditoria() {
  const { data, error } = useLoadable(() => api.listAudit());
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [action, setAction] = useState("");
  const [decision, setDecision] = useState<Decision>("todas");

  const filtered = useMemo(() => {
    let events = data?.events ?? [];
    if (startDate) events = events.filter((e) => e.occurred_at >= startDate);
    if (endDate) events = events.filter((e) => e.occurred_at <= endDate + "T23:59:59");
    if (action.trim()) events = events.filter((e) => e.action.toLowerCase().includes(action.trim().toLowerCase()));
    if (decision !== "todas") events = events.filter((e) => e.decision === decision);
    return events;
  }, [data, startDate, endDate, action, decision]);

  function clearFilters() {
    setStartDate("");
    setEndDate("");
    setAction("");
    setDecision("todas");
  }

  return (
    <>
      <PageHeader
        title="Trilha de auditoria"
        description="Cada pedido, quem fez, qual regra decidiu e o resultado."
        icon={ScrollText}
        actions={<span className="badge-outline-muted">{filtered.length} eventos</span>}
      />

      <Card
        title="Filtros"
        className="grid-margin"
        extra={
          <button type="button" onClick={clearFilters} className="btn btn-ghost btn-sm">
            <RotateCcw size={14} aria-hidden="true" />
            Limpar
          </button>
        }
      >
        <div className="flex flex-wrap items-end gap-4 px-6 pb-6">
          <div>
            <label htmlFor="f-inicio" className="label">
              De
            </label>
            <input id="f-inicio" type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} className="input" />
          </div>
          <div>
            <label htmlFor="f-fim" className="label">
              Até
            </label>
            <input id="f-fim" type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} className="input" />
          </div>
          <div className="min-w-48 flex-1">
            <label htmlFor="f-acao" className="label">
              Ação
            </label>
            <input
              id="f-acao"
              placeholder="ex.: registrar_recebimento"
              value={action}
              onChange={(e) => setAction(e.target.value)}
              className="input"
            />
          </div>
          <fieldset>
            <legend className="label">Decisão</legend>
            <div className="flex flex-wrap gap-1">
              {DECISIONS.map((d) => (
                <button
                  key={d.value}
                  type="button"
                  onClick={() => setDecision(d.value)}
                  aria-pressed={decision === d.value}
                  className="btn btn-ghost btn-sm border"
                >
                  {d.label}
                </button>
              ))}
            </div>
          </fieldset>
        </div>
      </Card>

      <Card title="Eventos">
        {error && <ErrorState>{error}</ErrorState>}
        <div className="overflow-x-auto px-3 pb-3">
          <table className="data-table min-w-[760px]">
            <thead>
              <tr>
                <th className="w-48">Quando</th>
                <th>Quem</th>
                <th>Ação</th>
                <th className="w-44">Regra</th>
                <th className="w-44 text-right">Decisão</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((e, i) => (
                <tr key={i}>
                  <td className="code text-muted-foreground">{e.occurred_at}</td>
                  <td className="max-w-0 truncate">{e.actor}</td>
                  <td className="code max-w-0 truncate">{e.action}</td>
                  <td className="code max-w-0 truncate text-muted-foreground">{e.rule_id ?? "—"}</td>
                  <td className="text-right">
                    <DecisionBadge decision={e.decision} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {filtered.length === 0 && <Empty>Nenhum evento para esses filtros.</Empty>}
        </div>
      </Card>
    </>
  );
}
