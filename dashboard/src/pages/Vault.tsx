import { useMemo, useState } from "react";
import { api, type VaultNote } from "@/api";
import { Card, Empty } from "@/components/Card";
import { PageHeader } from "@/components/PageHeader";
import { useLoadable } from "@/lib/useLoadable";

export function Vault() {
  const { data, error } = useLoadable(() => api.listVaultNotes());
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<VaultNote | null>(null);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const notes = data?.notes ?? [];
    if (!q) return notes;
    return notes.filter(
      (n) => n.title.toLowerCase().includes(q) || n.tags.some((t) => t.toLowerCase().includes(q)),
    );
  }, [data, query]);

  return (
    <>
      <PageHeader
        crumb="vault"
        title="Vault jurídico"
        actions={
          <>
            <input
              placeholder="buscar nota, tag…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              className="rounded-md border border-line bg-surface px-2.5 py-1 font-mono text-[11px] placeholder:text-ink/30 focus:border-navy focus:ring-2 focus:ring-navy/20 focus:outline-none"
            />
            <span className="rounded-md border border-line bg-surface/50 px-2 py-1 font-mono text-[10px] text-ink/50">
              {data?.total ?? 0} notas
            </span>
          </>
        }
      />

      <div className="grid gap-3 lg:grid-cols-[minmax(0,380px)_minmax(0,1fr)]">
        <Card title="Notas">
          {error && <Empty>{error}</Empty>}
          {filtered.length === 0 ? (
            <Empty>Nenhuma nota encontrada.</Empty>
          ) : (
            <div className="divide-y divide-line/60">
              {filtered.map((n) => (
                <button
                  key={n.note_id}
                  onClick={() => setSelected(n)}
                  className={`block w-full px-3 py-2 text-left transition-colors hover:bg-navy/5 ${
                    selected?.note_id === n.note_id ? "bg-navy/10" : ""
                  }`}
                >
                  <p className="truncate font-mono text-[11px] text-ink">{n.title}</p>
                  <p className="mt-0.5 font-mono text-[9px] text-ink/40">{n.tags.join(", ") || "sem tags"}</p>
                </button>
              ))}
            </div>
          )}
        </Card>

        <Card title={selected?.title ?? "nota"}>
          {!selected ? (
            <Empty>Selecione uma nota para ler.</Empty>
          ) : (
            <div className="p-3">
              <div className="mb-2 flex flex-wrap gap-1">
                {selected.tags.map((t) => (
                  <span key={t} className="rounded bg-ink/10 px-1.5 py-0.5 font-mono text-[9px] text-ink/55">
                    {t}
                  </span>
                ))}
              </div>
              <p className="whitespace-pre-wrap text-[12px] leading-relaxed text-ink/80">{selected.content}</p>
              <p className="mt-3 font-mono text-[9px] text-ink/35">atualizado em {selected.updated_at}</p>
            </div>
          )}
        </Card>
      </div>
    </>
  );
}
