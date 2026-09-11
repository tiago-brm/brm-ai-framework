import { useMemo, useState } from "react";
import { api } from "@/api";
import { Card, Empty } from "@/components/Card";
import { EffectBadge } from "@/components/DecisionBadge";
import { PageHeader } from "@/components/PageHeader";
import { useLoadable } from "@/lib/useLoadable";

const EFFECTS = ["todas", "allow", "deny", "require_approval"] as const;

export function Regras() {
  const { data, error } = useLoadable(() => api.listRules());
  const [effect, setEffect] = useState<(typeof EFFECTS)[number]>("todas");

  const filtered = useMemo(() => {
    const rules = data?.rules ?? [];
    return effect === "todas" ? rules : rules.filter((r) => r.effect === effect);
  }, [data, effect]);

  return (
    <>
      <PageHeader
        crumb="regras"
        title="Regras de negócio"
        actions={EFFECTS.map((e) => (
          <button
            key={e}
            onClick={() => setEffect(e)}
            className={`rounded-md border px-2 py-1 font-mono text-[10px] transition-colors ${
              effect === e ? "border-navy/40 bg-navy/10 text-navy" : "border-line bg-surface/50 text-ink/55 hover:text-ink"
            }`}
          >
            {e}
          </button>
        ))}
      />

      <Card title="Regras">
        {error && <Empty>{error}</Empty>}
        {filtered.length === 0 ? (
          <Empty>Nenhuma regra com esse efeito.</Empty>
        ) : (
          <div className="divide-y divide-line/60">
            {filtered.map((r) => (
              <div key={r.id} className="px-3 py-2.5">
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate font-mono text-[11px] text-ink">{r.id}</span>
                  <EffectBadge effect={r.effect} />
                </div>
                <p className="mt-0.5 text-[11px] text-ink/55">{r.description}</p>
                <pre className="mt-1.5 overflow-x-auto rounded bg-ink/5 px-2 py-1.5 font-mono text-[10px] text-ink/60">
                  {JSON.stringify(r.condition)}
                </pre>
              </div>
            ))}
          </div>
        )}
      </Card>
    </>
  );
}
