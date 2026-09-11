import { useMemo, useState } from "react";
import { api } from "@/api";
import { Card, Empty } from "@/components/Card";
import { DecisionBadge } from "@/components/DecisionBadge";
import { PageHeader } from "@/components/PageHeader";
import { useLoadable } from "@/lib/useLoadable";

const DECISIONS = ["todas", "allowed", "denied", "pending_approval"] as const;

export function Auditoria() {
  const { data, error } = useLoadable(() => api.listAudit());
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [action, setAction] = useState("");
  const [decision, setDecision] = useState<(typeof DECISIONS)[number]>("todas");

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
        crumb="auditoria"
        title="Trilha de auditoria"
        actions={
          <span className="rounded-md border border-line bg-surface/50 px-2 py-1 font-mono text-[10px] text-ink/50">
            {filtered.length} eventos
          </span>
        }
      />

      <Card title="Filtros" extra={<button onClick={clearFilters} className="font-mono text-[10px] text-navy hover:underline">limpar</button>}>
        <div className="flex flex-wrap items-end gap-3 p-3">
          <label className="block">
            <span className="font-mono text-[9px] uppercase tracking-wide text-ink/40">start_date</span>
            <input
              type="date"
              value={startDate}
              onChange={(e) => setStartDate(e.target.value)}
              className="mt-1 block rounded-md border border-line bg-surface px-2 py-1 font-mono text-[11px] focus:border-navy focus:outline-none"
            />
          </label>
          <label className="block">
            <span className="font-mono text-[9px] uppercase tracking-wide text-ink/40">end_date</span>
            <input
              type="date"
              value={endDate}
              onChange={(e) => setEndDate(e.target.value)}
              className="mt-1 block rounded-md border border-line bg-surface px-2 py-1 font-mono text-[11px] focus:border-navy focus:outline-none"
            />
          </label>
          <label className="block">
            <span className="font-mono text-[9px] uppercase tracking-wide text-ink/40">action</span>
            <input
              placeholder="deletar_banco"
              value={action}
              onChange={(e) => setAction(e.target.value)}
              className="mt-1 block rounded-md border border-line bg-surface px-2 py-1 font-mono text-[11px] placeholder:text-ink/30 focus:border-navy focus:outline-none"
            />
          </label>
          <div>
            <span className="font-mono text-[9px] uppercase tracking-wide text-ink/40">decision</span>
            <div className="mt-1 flex gap-1">
              {DECISIONS.map((d) => (
                <button
                  key={d}
                  onClick={() => setDecision(d)}
                  className={`rounded-md border px-2 py-1 font-mono text-[10px] transition-colors ${
                    decision === d ? "border-navy/40 bg-navy/10 text-navy" : "border-line bg-surface/50 text-ink/55 hover:text-ink"
                  }`}
                >
                  {d}
                </button>
              ))}
            </div>
          </div>
        </div>
      </Card>

      <div className="mt-3">
        <Card title="Eventos">
          {error && <Empty>{error}</Empty>}
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] border-collapse">
              <thead>
                <tr className="border-b border-line text-left font-mono text-[9px] uppercase tracking-wide text-ink/35">
                  <th className="w-[180px] px-3 py-1.5 font-medium">occurred_at</th>
                  <th className="px-3 py-1.5 font-medium">actor</th>
                  <th className="px-3 py-1.5 font-medium">action</th>
                  <th className="w-[180px] px-3 py-1.5 font-medium">rule_id</th>
                  <th className="w-[150px] px-3 py-1.5 text-right font-medium">decision</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line/60">
                {filtered.map((e, i) => (
                  <tr key={i}>
                    <td className="px-3 py-1.5 font-mono text-[11px] text-ink/70">{e.occurred_at}</td>
                    <td className="truncate px-3 py-1.5 font-mono text-[11px] text-ink">{e.actor}</td>
                    <td className="truncate px-3 py-1.5 font-mono text-[11px] text-ink">{e.action}</td>
                    <td className="truncate px-3 py-1.5 font-mono text-[11px] text-ink/50">{e.rule_id ?? "—"}</td>
                    <td className="px-3 py-1.5 text-right">
                      <DecisionBadge decision={e.decision} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {filtered.length === 0 && <Empty>Nenhum evento para esses filtros.</Empty>}
          </div>
        </Card>
      </div>
    </>
  );
}
