import { useMemo, useState } from "react";
import { Scale } from "lucide-react";
import { api } from "@/api";
import { Card, Empty, ErrorState } from "@/components/Card";
import { EffectBadge } from "@/components/DecisionBadge";
import { PageHeader } from "@/components/PageHeader";
import { useLoadable } from "@/lib/useLoadable";

const EFFECTS = [
  { value: "todas", label: "Todas" },
  { value: "allow", label: "Permitem" },
  { value: "deny", label: "Negam" },
  { value: "require_approval", label: "Exigem aprovação" },
] as const;

type Effect = (typeof EFFECTS)[number]["value"];

export function Regras() {
  const { data, error } = useLoadable(() => api.listRules());
  const [effect, setEffect] = useState<Effect>("todas");

  const filtered = useMemo(() => {
    const rules = data?.rules ?? [];
    return effect === "todas" ? rules : rules.filter((r) => r.effect === effect);
  }, [data, effect]);

  return (
    <>
      <PageHeader
        title="Regras de negócio"
        description="Regras duras em JSONLogic, avaliadas pelo motor antes de qualquer ação."
        icon={Scale}
        actions={EFFECTS.map((e) => (
          <button
            key={e.value}
            type="button"
            onClick={() => setEffect(e.value)}
            aria-pressed={effect === e.value}
            className="btn btn-ghost btn-sm border"
          >
            {e.label}
          </button>
        ))}
      />

      <Card title="Regras">
        {error && <ErrorState>{error}</ErrorState>}
        {filtered.length === 0 ? (
          <Empty>Nenhuma regra com esse efeito.</Empty>
        ) : (
          <ul className="preview-list px-6 pb-2">
            {filtered.map((r) => (
              <li key={r.id} className="preview-item">
                <div className="preview-item-content">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="preview-subject code">{r.id}</span>
                    <EffectBadge effect={r.effect} />
                  </div>
                  <p className="mt-1 text-muted-foreground">{r.description}</p>
                  <pre className="code mt-2 overflow-x-auto rounded-lg bg-muted px-3 py-2 text-xs">
                    {JSON.stringify(r.condition)}
                  </pre>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </>
  );
}
