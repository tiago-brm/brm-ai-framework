import { useState } from "react";
import { CircleCheck, CircleX, Grid3x3 } from "lucide-react";
import { api, type Tool } from "@/api";
import { Card, Empty, ErrorState } from "@/components/Card";
import { PageHeader } from "@/components/PageHeader";
import { useLoadable } from "@/lib/useLoadable";

const EDITABLE_ROLES = ["viewer", "admin"] as const;

export function Permissoes() {
  const { data: permData, error: permError, reload: reloadPerms } = useLoadable(() => api.listPermissions());
  const { data: toolsData, error: toolsError } = useLoadable(() => api.listTools());
  const [saving, setSaving] = useState<string | null>(null);

  const permMap = new Map(
    (permData?.permissions ?? []).map((p) => [`${p.role}:${p.tool_name}`, p.allowed]),
  );
  const toolNames = (toolsData?.tools ?? []).map((t: Tool) => t.name).sort();

  async function toggle(role: string, toolName: string, current: boolean) {
    const key = `${role}:${toolName}`;
    setSaving(key);
    try {
      await api.updatePermission(role, toolName, !current);
      reloadPerms();
    } finally {
      setSaving(null);
    }
  }

  return (
    <>
      <PageHeader
        title="Permissões"
        description="Quais tools cada perfil pode ver e chamar. Clique para trocar."
        icon={Grid3x3}
        actions={<span className="badge-outline-muted">{permData?.total ?? 0} entradas</span>}
      />

      <Card
        title="Perfil × tool"
        extra={<span className="text-xs text-muted-foreground">Sem entrada, vale o padrão do cliente (rbac_default)</span>}
      >
        {(permError || toolsError) && <ErrorState>{permError || toolsError}</ErrorState>}
        {toolNames.length === 0 ? (
          <Empty>Catálogo de tools vazio.</Empty>
        ) : (
          <div className="overflow-x-auto px-3 pb-3">
            <table className="data-table min-w-[480px]">
              <thead>
                <tr>
                  <th>Tool</th>
                  {EDITABLE_ROLES.map((r) => (
                    <th key={r} className="w-36 text-center">
                      {r}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {toolNames.map((toolName) => (
                  <tr key={toolName}>
                    <td className="code max-w-0 truncate">{toolName}</td>
                    {EDITABLE_ROLES.map((role) => {
                      const key = `${role}:${toolName}`;
                      const allowed = permMap.get(key) ?? false;
                      const Icon = allowed ? CircleCheck : CircleX;
                      return (
                        <td key={role} className="text-center">
                          <button
                            type="button"
                            disabled={saving === key}
                            onClick={() => toggle(role, toolName, allowed)}
                            aria-label={`${toolName} para ${role}: ${allowed ? "permitido" : "negado"}. Trocar.`}
                            className={`${allowed ? "badge-outline-success" : "badge-outline-muted"} cursor-pointer disabled:opacity-50`}
                          >
                            <Icon size={12} aria-hidden="true" />
                            {allowed ? "Permitido" : "Negado"}
                          </button>
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
    </>
  );
}
