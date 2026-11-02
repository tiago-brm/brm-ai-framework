import { CircleCheck, CircleX, Hourglass, LoaderCircle, ShieldHalf, type LucideIcon } from "lucide-react";
import { api } from "@/api";
import { Card, Empty, ErrorState } from "@/components/Card";
import { PageHeader } from "@/components/PageHeader";
import { useLoadable } from "@/lib/useLoadable";

const EFFECTS: Record<string, { icon: LucideIcon; label: string; cls: string }> = {
  allow: { icon: CircleCheck, label: "Permitido", cls: "badge-outline-success" },
  deny: { icon: CircleX, label: "Negado", cls: "badge-outline-danger" },
  require_approval: { icon: Hourglass, label: "Exige aprovação", cls: "badge-outline-warning" },
};

export function Governanca() {
  const { data, error } = useLoadable(() => api.simMatriz());

  return (
    <>
      <PageHeader
        title="Quem pode o quê"
        description="Cada cenário decidido para cada perfil, calculado agora pelas regras."
        icon={ShieldHalf}
        actions={Object.values(EFFECTS).map((e) => (
          <span key={e.label} className={e.cls}>
            <e.icon size={12} aria-hidden="true" />
            {e.label}
          </span>
        ))}
      />

      <Card title="Cenários por perfil" className="grid-margin">
        {error && <ErrorState>{error}</ErrorState>}
        {!data && !error && <Empty icon={LoaderCircle}>Calculando…</Empty>}
        {data && data.cenarios.length === 0 && <Empty>Nenhum cenário cadastrado em demo_scenarios.yaml.</Empty>}
        {data && data.cenarios.length > 0 && (
          <div className="overflow-x-auto px-3 pb-3">
            <table className="data-table min-w-[820px]">
              <thead>
                <tr>
                  <th>Cenário</th>
                  {data.perfis.map((p) => (
                    <th key={p.identity} className="text-center">
                      <span className="block text-foreground">{p.rotulo.split(" · ")[0]}</span>
                      <span className="block font-normal">{p.role}</span>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.cenarios.map((row) => (
                  <tr key={row.id}>
                    <td>{row.titulo}</td>
                    {row.celulas.map((c) => {
                      const e = EFFECTS[c.efeito] ?? EFFECTS.deny;
                      const descricao = c.rule_id ? `${e.label} · regra ${c.rule_id}` : e.label;
                      return (
                        <td key={c.perfil} className="text-center">
                          <span title={descricao} className={`${e.cls} !px-1.5`}>
                            <e.icon size={14} aria-hidden="true" />
                            <span className="sr-only">{descricao}</span>
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
      <p className="hint max-w-3xl">
        Calculado a partir das regras e dos fatos derivados do cadastro (vínculo, idade, matrícula, frequência). Não
        inclui o DLP, que depende do conteúdo de cada pedido. Passe o mouse sobre uma célula para ver a regra que
        decidiu.
      </p>
    </>
  );
}
