import { useState } from "react";
import { api, type Tool } from "@/api";
import { Card, Empty } from "@/components/Card";
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
        crumb="matriz"
        title="Matriz de permissões"
        actions={
          <span className="rounded-md border border-line bg-surface/50 px-2 py-1 font-mono text-[10px] text-ink/50">
            {permData?.total ?? 0} entradas
          </span>
        }
      />

      <Card
        title="Perfil × tool"
        extra={<span className="font-mono text-[10px] text-ink/40">sem entrada = aberta a qualquer perfil</span>}
      >
        {(permError || toolsError) && <Empty>{permError || toolsError}</Empty>}
        {toolNames.length === 0 ? (
          <Empty>Catálogo de tools vazio.</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[480px] border-collapse">
              <thead>
                <tr className="border-b border-line text-left font-mono text-[9px] uppercase tracking-wide text-ink/35">
                  <th className="px-3 py-1.5 font-medium">tool</th>
                  {EDITABLE_ROLES.map((r) => (
                    <th key={r} className="w-[120px] px-3 py-1.5 text-center font-medium">
                      {r}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-line/60">
                {toolNames.map((toolName) => (
                  <tr key={toolName}>
                    <td className="truncate px-3 py-2 font-mono text-[11px] text-ink">{toolName}</td>
                    {EDITABLE_ROLES.map((role) => {
                      const key = `${role}:${toolName}`;
                      const allowed = permMap.get(key) ?? false;
                      return (
                        <td key={role} className="px-3 py-2 text-center">
                          <button
                            disabled={saving === key}
                            onClick={() => toggle(role, toolName, allowed)}
                            className={`rounded-md border px-2 py-1 font-mono text-[10px] uppercase tracking-wide transition-colors disabled:opacity-50 ${
                              allowed
                                ? "border-green/30 bg-green/10 text-green hover:bg-green/20"
                                : "border-line bg-surface/50 text-ink/45 hover:text-ink"
                            }`}
                          >
                            {allowed ? "permitido" : "negado"}
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
