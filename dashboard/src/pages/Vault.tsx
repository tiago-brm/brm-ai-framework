import { useMemo, useState } from "react";
import { BookOpen, FileText } from "lucide-react";
import { api, type VaultNote } from "@/api";
import { Card, Empty, ErrorState } from "@/components/Card";
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
        title="Segundo cérebro"
        description="Procedimentos, políticas, modelos e memória do cliente."
        icon={BookOpen}
        actions={
          <>
            <label htmlFor="vault-busca" className="sr-only">
              Buscar nota ou tag
            </label>
            <input
              id="vault-busca"
              placeholder="Buscar nota ou tag…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              className="input w-56"
            />
            <span className="badge-outline-muted">{data?.total ?? 0} notas</span>
          </>
        }
      />

      <div className="grid gap-6 lg:grid-cols-[minmax(0,380px)_minmax(0,1fr)]">
        <Card title="Notas">
          {error && <ErrorState>{error}</ErrorState>}
          {filtered.length === 0 ? (
            <Empty>Nenhuma nota encontrada.</Empty>
          ) : (
            <ul className="px-3 pb-3">
              {filtered.map((n) => (
                <li key={n.note_id}>
                  <button
                    type="button"
                    onClick={() => setSelected(n)}
                    aria-pressed={selected?.note_id === n.note_id}
                    className={`block w-full rounded-lg px-3 py-2 text-left transition-colors hover:bg-muted ${
                      selected?.note_id === n.note_id ? "bg-primary/10" : ""
                    }`}
                  >
                    <p className="truncate font-medium">{n.title}</p>
                    <p className="text-xs text-muted-foreground">{n.tags.join(", ") || "sem tags"}</p>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card title={selected?.title ?? "Nota"}>
          {!selected ? (
            <Empty icon={FileText}>Selecione uma nota para ler.</Empty>
          ) : (
            <div className="px-6 pb-6">
              <div className="mb-3 flex flex-wrap gap-1">
                {selected.tags.map((t) => (
                  <span key={t} className="badge-outline-muted">
                    {t}
                  </span>
                ))}
              </div>
              <p className="leading-relaxed whitespace-pre-wrap">{selected.content}</p>
              <p className="hint mt-4">Atualizada em {selected.updated_at}</p>
            </div>
          )}
        </Card>
      </div>
    </>
  );
}
