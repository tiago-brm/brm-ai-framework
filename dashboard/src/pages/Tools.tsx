import { useMemo, useState } from "react";
import { Wrench } from "lucide-react";
import { api } from "@/api";
import { Card, Empty, ErrorState } from "@/components/Card";
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
        title="Catálogo de tools"
        description="O que o MCP expõe ao chat. As permissões ficam em Permissões."
        icon={Wrench}
        actions={
          <>
            <label htmlFor="tools-filtro" className="sr-only">
              Filtrar tools
            </label>
            <input
              id="tools-filtro"
              placeholder="Filtrar…"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              className="input w-56"
            />
            <span className="badge-outline-muted">somente leitura</span>
          </>
        }
      />

      <Card
        title="Tools expostas pelo MCP"
        extra={
          <span className="text-xs text-muted-foreground">
            {filtered.length} de {data?.total ?? 0}
          </span>
        }
      >
        {error && <ErrorState>{error}</ErrorState>}
        {filtered.length === 0 ? (
          <Empty>Nenhuma tool corresponde ao filtro.</Empty>
        ) : (
          <ul className="preview-list px-6 pb-2">
            {filtered.map((t) => (
              <li key={t.name} className="preview-item">
                <div className="icon-box icon-box-sm icon-box-info">
                  <Wrench size={16} aria-hidden="true" />
                </div>
                <div className="preview-item-content">
                  <p className="preview-subject code">{t.name}</p>
                  <p className="text-muted-foreground">{t.description || "sem descrição"}</p>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </>
  );
}
