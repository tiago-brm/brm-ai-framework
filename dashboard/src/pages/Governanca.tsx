import { api } from "@/api";
import { Card, Empty } from "@/components/Card";
import { PageHeader } from "@/components/PageHeader";
import { useLoadable } from "@/lib/useLoadable";

const EFFECTS: Record<string, { symbol: string; label: string; cls: string }> = {
  allow: { symbol: "✓", label: "permitido", cls: "bg-green/10 text-green" },
  deny: { symbol: "✕", label: "negado", cls: "bg-red/10 text-red" },
  require_approval: { symbol: "⏸", label: "aprovação", cls: "bg-amber/10 text-amber" },
};

export function Governanca() {
  const { data, error } = useLoadable(() => api.simMatriz());

  return (
    <>
      <PageHeader
        crumb="governança"
        title="Quem pode o quê"
        actions={
          <div className="flex items-center gap-2 font-mono text-[10px]">
            {Object.values(EFFECTS).map((e) => (
              <span key={e.label} className={`rounded border border-line px-1.5 py-0.5 ${e.cls}`}>
                {e.symbol} {e.label}
              </span>
            ))}
          </div>
        }
      />

      <Card title="Cenários por perfil">
        {error && <Empty>{error}</Empty>}
        {!data && !error && <Empty>Calculando…</Empty>}
        {data && data.cenarios.length === 0 && (
          <Empty>Nenhum cenário cadastrado em demo_scenarios.yaml.</Empty>
        )}
        {data && data.cenarios.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[820px] border-collapse">
              <thead>
                <tr className="border-b border-line text-left font-mono text-[9px] uppercase tracking-wide text-ink/45">
                  <th className="px-3 py-2 font-medium">cenário</th>
                  {data.perfis.map((p) => (
                    <th key={p.identity} className="px-2 py-2 text-center font-medium">
                      <span className="block normal-case tracking-normal text-ink/70">
                        {p.rotulo.split(" · ")[0]}
                      </span>
                      <span className="block text-[8px] text-ink/35">{p.role}</span>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-line/60">
                {data.cenarios.map((row) => (
                  <tr key={row.id}>
                    <td className="px-3 py-2 text-[12px] text-ink">{row.titulo}</td>
                    {row.celulas.map((c) => {
                      const e = EFFECTS[c.efeito] ?? EFFECTS.deny;
                      return (
                        <td key={c.perfil} className="px-2 py-1.5 text-center">
                          <span
                            title={c.rule_id ? `${e.label} · regra ${c.rule_id}` : e.label}
                            className={`inline-grid size-7 place-items-center rounded font-mono text-[13px] ${e.cls}`}
                          >
                            {e.symbol}
                          </span>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      <p className="mt-3 max-w-3xl font-mono text-[10px] leading-relaxed text-ink/45">
        Calculado agora a partir das regras e dos fatos derivados do cadastro (vínculo, idade,
        matrícula, frequência). Não inclui o DLP, que depende do conteúdo de cada pedido. Passe o
        mouse sobre uma célula para ver a regra que decidiu.
      </p>
    </>
  );
}
