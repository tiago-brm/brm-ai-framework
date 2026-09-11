import { useState } from "react";
import { api, ApiError, type User } from "@/api";
import { Card, Empty } from "@/components/Card";
import { PageHeader } from "@/components/PageHeader";
import { useLoadable } from "@/lib/useLoadable";

const ROLES = ["viewer", "admin", "super_admin"];

export function Usuarios() {
  const { data, error, reload } = useLoadable(() => api.listUsers());
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("viewer");
  const [formError, setFormError] = useState<string | null>(null);
  const [createdToken, setCreatedToken] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setFormError(null);
    setCreatedToken(null);
    setCreating(true);
    try {
      const result = await api.createUser(email, role);
      setCreatedToken(result.api_token);
      setEmail("");
      setRole("viewer");
      reload();
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setCreating(false);
    }
  }

  async function handleRoleChange(identity: string, newRole: string) {
    try {
      await api.updateUserRole(identity, newRole);
      reload();
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : String(err));
    }
  }

  return (
    <>
      <PageHeader
        crumb="usuários"
        title="Usuários"
        actions={
          <span className="rounded-md border border-line bg-surface/50 px-2 py-1 font-mono text-[10px] text-ink/50">
            {data?.total ?? 0} perfis
          </span>
        }
      />

      <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_320px]">
        <Card title="Perfis cadastrados">
          <div className="grid grid-cols-[1fr_120px_160px] gap-2 border-b border-line px-3 py-1 font-mono text-[9px] uppercase tracking-wide text-ink/35">
            <span>Identity</span>
            <span>Role</span>
            <span>Trocar perfil</span>
          </div>
          {error && <Empty>{error}</Empty>}
          {data && data.users.length === 0 ? (
            <Empty>Nenhum usuário cadastrado ainda.</Empty>
          ) : (
            <div className="divide-y divide-line/60">
              {(data?.users ?? []).map((u: User) => (
                <div key={u.identity} className="grid grid-cols-[1fr_120px_160px] items-center gap-2 px-3 py-2">
                  <span className="truncate font-mono text-[11px] text-ink">{u.identity}</span>
                  <span className="rounded bg-ink/10 px-1.5 py-0.5 font-mono text-[9px] text-ink/60 w-fit">
                    {u.role}
                  </span>
                  <select
                    value={u.role}
                    onChange={(e) => handleRoleChange(u.identity, e.target.value)}
                    className="rounded-md border border-line bg-surface px-2 py-1 font-mono text-[10px] focus:border-navy focus:outline-none"
                  >
                    {ROLES.map((r) => (
                      <option key={r} value={r}>
                        {r}
                      </option>
                    ))}
                  </select>
                </div>
              ))}
            </div>
          )}
        </Card>

        <Card title="Novo usuário">
          <form className="space-y-3 p-3" onSubmit={handleCreate}>
            <label className="block">
              <span className="font-mono text-[10px] uppercase tracking-wide text-ink/45">E-mail</span>
              <input
                required
                type="email"
                placeholder="nova@escritorio.com.br"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="mt-1 w-full rounded-md border border-line bg-surface px-2.5 py-1.5 font-mono text-[11px] placeholder:text-ink/30 focus:border-navy focus:ring-2 focus:ring-navy/20 focus:outline-none"
              />
            </label>
            <label className="block">
              <span className="font-mono text-[10px] uppercase tracking-wide text-ink/45">Perfil</span>
              <select
                value={role}
                onChange={(e) => setRole(e.target.value)}
                className="mt-1 w-full rounded-md border border-line bg-surface px-2.5 py-1.5 font-mono text-[11px] focus:border-navy focus:outline-none"
              >
                {ROLES.map((r) => (
                  <option key={r} value={r}>
                    {r}
                  </option>
                ))}
              </select>
            </label>
            {formError && <p className="font-mono text-[10px] text-red">{formError}</p>}
            {createdToken && (
              <p className="rounded-md border border-amber/30 bg-amber/5 px-2 py-1.5 font-mono text-[10px] leading-relaxed text-amber break-all">
                Token gerado (só aparece agora): {createdToken}
              </p>
            )}
            <button
              type="submit"
              disabled={creating}
              className="w-full rounded-md bg-navy px-3 py-1.5 font-mono text-[11px] text-surface transition-colors hover:bg-navy/90 disabled:opacity-60"
            >
              {creating ? "Criando…" : "Criar e gerar token"}
            </button>
            <p className="font-mono text-[10px] leading-relaxed text-ink/40">
              O token em texto puro só aparece na resposta desta criação.
            </p>
          </form>
        </Card>
      </div>
    </>
  );
}
