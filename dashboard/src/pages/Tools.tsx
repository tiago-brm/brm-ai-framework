import { useMemo, useState } from "react";
import { api } from "@/api";
import { Card, Empty } from "@/components/Card";
import { PageHeader } from "@/components/PageHeader";
import { useLoadable } from "@/lib/useLoadable";

export function Tools() {
  const { data, error } = useLoadable(() => api.listTools());
  const [filter, setFilter] = useState("");

  const filtered = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return data?.tools ?? [];
    return (data?.tools ?? []).filter(
      (t) => t.name.toLowerCase().includes(q) || (t.description ?? "").toLowerCase().includes(q),
    );
  }, [data, filter]);

  return (
    <>
      <PageHeader
        crumb="tools"
        title="Catálogo de tools"
        actions={
          <>
            <input
              placeholder="filtrar…"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              className="rounded-md border border-line bg-surface px-2.5 py-1 font-mono text-[11px] placeholder:text-ink/30 focus:border-navy focus:ring-2 focus:ring-navy/20 focus:outline-none"
            />
            <span className="rounded bg-ink/10 px-1.5 py-1 font-mono text-[9px] text-ink/55">somente leitura</span>
          </>
        }
      />

      <Card
        title="Tools expostas pelo MCP"
        extra={
          <span className="font-mono text-[10px] text-ink/40">
            {filtered.length} de {data?.total ?? 0}
          </span>
        }
      >
        {error && <Empty>{error}</Empty>}
        {filtered.length === 0 ? (
          <Empty>Nenhuma tool corresponde ao filtro.</Empty>
        ) : (
          <div className="divide-y divide-line/60">
            {filtered.map((t) => (
              <div key={t.name} className="px-3 py-2">
                <p className="font-mono text-[11px] text-ink">{t.name}</p>
                <p className="mt-0.5 text-[11px] text-ink/50">{t.description || "sem descrição"}</p>
              </div>
            ))}
          </div>
        )}
      </Card>
    </>
  );
}
