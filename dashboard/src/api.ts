const BASE_URL_KEY = "brm.baseUrl";
const TOKEN_KEY = "brm.token";

export function getBaseUrl(): string {
  return localStorage.getItem(BASE_URL_KEY) || "http://localhost:8000";
}

export function setBaseUrl(url: string): void {
  localStorage.setItem(BASE_URL_KEY, url);
}

export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) || "";
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearSession(): void {
  localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${getBaseUrl()}/api${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${getToken()}`,
      ...(init?.headers || {}),
    },
  });

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail || detail;
    } catch {
      // resposta sem corpo JSON — mantém statusText
    }
    throw new ApiError(response.status, detail);
  }

  return (await response.json()) as T;
}

export type Me = { identity: string; role: string };
export type User = { identity: string; role: string };
export type Permission = { role: string; tool_name: string; allowed: boolean };
export type Tool = { name: string; description: string | null };
export type Rule = {
  id: string;
  description: string;
  condition: unknown;
  effect: string;
  message: string | null;
};
export type VaultNote = {
  note_id: string;
  title: string;
  content: string;
  tags: string[];
  updated_at: string;
};
export type AuditEvent = {
  occurred_at: string;
  actor: string;
  action: string;
  decision: string;
  rule_id: string | null;
};

export type SimPerfil = { identity: string; role: string; rotulo: string };
export type SimParam = { type: string; required: boolean; description: string | null };
export type SimSkill = {
  name: string;
  description: string;
  examples: string[];
  parameters: Record<string, SimParam>;
};
export type SimCenario = {
  id: string;
  titulo: string;
  skill: string;
  argumentos: Record<string, unknown>;
  perfil: string | null;
  nota: string | null;
};
export type SimResultado = {
  perfil: { identity: string; role: string };
  decisao: string;
  resultado: {
    allowed: boolean;
    reason: string | null;
    rule_id?: string | null;
    message?: string | null;
    proximo_passo?: string | null;
    result?: string;
  };
  fatos_derivados: Record<string, unknown>;
  dlp: Record<string, string[]>;
};
export type SimVault = {
  perfil: { identity: string; role: string };
  total: number;
  ocultas: number;
  politicas: string[];
  notas: { note_id: string; title: string; tipo: string | null; modo: string; trecho: string }[];
};
export type SimMatriz = {
  perfis: SimPerfil[];
  cenarios: {
    id: string;
    titulo: string;
    celulas: { perfil: string; efeito: string; rule_id: string | null }[];
  }[];
};

export const api = {
  me: () => request<Me>("/me"),
  listUsers: () => request<{ total: number; users: User[] }>("/users"),
  createUser: (email: string, role: string, api_token?: string) =>
    request<{ allowed: boolean; identity: string; role: string; api_token: string }>("/users", {
      method: "POST",
      body: JSON.stringify({ email, role, api_token: api_token || null }),
    }),
  updateUserRole: (email: string, role: string) =>
    request<{ allowed: boolean; identity: string; role: string }>(
      `/users/${encodeURIComponent(email)}/role`,
      { method: "PATCH", body: JSON.stringify({ role }) },
    ),
  listPermissions: () =>
    request<{ total: number; permissions: Permission[] }>("/permissions"),
  updatePermission: (role: string, tool_name: string, allowed: boolean) =>
    request<{ allowed: boolean }>("/permissions", {
      method: "PUT",
      body: JSON.stringify({ role, tool_name, allowed }),
    }),
  listTools: () => request<{ total: number; tools: Tool[] }>("/tools"),
  listRules: () => request<{ total: number; rules: Rule[] }>("/rules"),
  listVaultNotes: () => request<{ total: number; notes: VaultNote[] }>("/vault/notes"),
  listAudit: () => request<{ total: number; events: AuditEvent[] }>("/audit"),
  simPerfis: () => request<{ total: number; perfis: SimPerfil[] }>("/simulador/perfis"),
  simSkills: () => request<{ total: number; skills: SimSkill[] }>("/simulador/skills"),
  simCenarios: () => request<{ total: number; cenarios: SimCenario[] }>("/simulador/cenarios"),
  simExecutar: (perfil_email: string, skill: string, argumentos: Record<string, unknown>) =>
    request<SimResultado>("/simulador/executar", {
      method: "POST",
      body: JSON.stringify({ perfil_email, skill, argumentos }),
    }),
  simVault: (perfil_email: string, consulta: string) =>
    request<SimVault>("/simulador/vault", {
      method: "POST",
      body: JSON.stringify({ perfil_email, consulta }),
    }),
  simMatriz: () => request<SimMatriz>("/simulador/matriz"),
};
