import { useState } from "react";
import { KeyRound, LoaderCircle, UserPlus, Users } from "lucide-react";
import { api, ApiError, type User } from "@/api";
import { Card, Empty, ErrorState } from "@/components/Card";
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
        title="Usuários"
        description="Quem acessa o motor e com qual perfil."
        icon={Users}
        actions={<span className="badge-outline-muted">{data?.total ?? 0} perfis</span>}
      />

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
        <Card title="Perfis cadastrados">
          {error && <ErrorState>{error}</ErrorState>}
          {data && data.users.length === 0 ? (
            <Empty>Nenhum usuário cadastrado ainda.</Empty>
          ) : (
            <div className="overflow-x-auto px-3 pb-3">
              <table className="data-table min-w-[520px]">
                <thead>
                  <tr>
                    <th>Identidade</th>
                    <th className="w-32">Perfil</th>
                    <th className="w-44">Trocar perfil</th>
                  </tr>
                </thead>
                <tbody>
                  {(data?.users ?? []).map((u: User) => (
                    <tr key={u.identity}>
                      <td className="max-w-0 truncate">{u.identity}</td>
                      <td>
                        <span className="badge-outline-muted">{u.role}</span>
                      </td>
                      <td>
                        <select
                          value={u.role}
                          onChange={(e) => handleRoleChange(u.identity, e.target.value)}
                          aria-label={`Perfil de ${u.identity}`}
                          className="input py-1.5"
                        >
                          {ROLES.map((r) => (
                            <option key={r} value={r}>
                              {r}
                            </option>
                          ))}
                        </select>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <Card title="Novo usuário">
          <form className="space-y-4 px-6 pb-6" onSubmit={handleCreate}>
            <div>
              <label htmlFor="novo-email" className="label">
                E-mail
              </label>
              <input
                id="novo-email"
                required
                type="email"
                placeholder="pessoa@empresa.com.br"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="input"
              />
            </div>
            <div>
              <label htmlFor="novo-perfil" className="label">
                Perfil
              </label>
              <select id="novo-perfil" value={role} onChange={(e) => setRole(e.target.value)} className="input">
                {ROLES.map((r) => (
                  <option key={r} value={r}>
                    {r}
                  </option>
                ))}
              </select>
            </div>
            {formError && (
              <p role="alert" className="field-error">
                {formError}
              </p>
            )}
            {createdToken && (
              <div role="status" className="rounded-lg border border-warning/40 bg-warning/10 p-3 text-warning">
                <p className="flex items-center gap-2 font-medium">
                  <KeyRound size={16} aria-hidden="true" />
                  Token gerado: copie agora, ele não aparece de novo.
                </p>
                <p className="code mt-1 break-all text-foreground">{createdToken}</p>
              </div>
            )}
            <button type="submit" disabled={creating} className="btn btn-primary w-full">
              {creating ? (
                <LoaderCircle size={16} className="animate-spin" aria-hidden="true" />
              ) : (
                <UserPlus size={16} aria-hidden="true" />
              )}
              {creating ? "Criando…" : "Criar e gerar token"}
            </button>
            <p className="hint">O token em texto puro só aparece na resposta desta criação.</p>
          </form>
        </Card>
      </div>
    </>
  );
}
