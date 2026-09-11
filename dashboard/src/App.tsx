import { useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { useHashRoute } from "@/lib/router";
import { Auditoria } from "@/pages/Auditoria";
import { Overview } from "@/pages/Overview";
import { Permissoes } from "@/pages/Permissoes";
import { Regras } from "@/pages/Regras";
import { Tools } from "@/pages/Tools";
import { Usuarios } from "@/pages/Usuarios";
import { Vault } from "@/pages/Vault";
import {
  api,
  ApiError,
  clearSession,
  getBaseUrl,
  getToken,
  setBaseUrl,
  setToken,
  type Me,
} from "./api";

export default function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [checking, setChecking] = useState(true);
  const [loginError, setLoginError] = useState<string | null>(null);

  useEffect(() => {
    if (!getToken()) {
      setChecking(false);
      return;
    }
    api
      .me()
      .then(setMe)
      .catch(() => clearSession())
      .finally(() => setChecking(false));
  }, []);

  if (checking) {
    return (
      <div className="grid min-h-screen place-items-center bg-paper font-mono text-[12px] text-ink/50">
        Carregando…
      </div>
    );
  }

  if (!me) {
    return (
      <LoginScreen
        onLoggedIn={(profile) => setMe(profile)}
        error={loginError}
        setError={setLoginError}
      />
    );
  }

  return (
    <Dashboard
      me={me}
      onLogout={() => {
        clearSession();
        setMe(null);
      }}
    />
  );
}

function LoginScreen({
  onLoggedIn,
  error,
  setError,
}: {
  onLoggedIn: (me: Me) => void;
  error: string | null;
  setError: (e: string | null) => void;
}) {
  const [baseUrl, setBaseUrlInput] = useState(getBaseUrl());
  const [token, setTokenInput] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    setBaseUrl(baseUrl);
    setToken(token);
    try {
      const me = await api.me();
      onLoggedIn(me);
    } catch (err) {
      clearSession();
      if (err instanceof ApiError && err.status === 403) {
        setError("Este token não tem perfil super_admin — o painel é restrito a ele.");
      } else {
        setError("Não foi possível conectar ao BRM Engine. Confira a URL e o token.");
      }
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="grid min-h-screen place-items-center bg-paper font-sans text-[13px] text-ink">
      <form
        onSubmit={handleSubmit}
        className="rise w-full max-w-sm rounded-md border border-line bg-surface/70 p-5"
      >
        <div className="mb-4 flex items-center gap-2">
          <span className="grid size-6 place-items-center bg-navy font-mono text-[11px] text-surface">B</span>
          <span className="font-mono text-[11px] uppercase tracking-[0.18em] text-ink/70">BRM Engine</span>
        </div>
        <h1 className="font-display text-[24px] italic leading-none text-ink">Painel super_admin</h1>
        <p className="mt-1.5 text-[12px] text-ink/55">
          Gestão de usuários, permissões e visão geral do MCP Engine.
        </p>

        <label className="mt-4 block">
          <span className="font-mono text-[10px] uppercase tracking-wide text-ink/45">URL do BRM Engine</span>
          <input
            value={baseUrl}
            onChange={(e) => setBaseUrlInput(e.target.value)}
            className="mt-1 w-full rounded-md border border-line bg-surface px-2.5 py-1.5 font-mono text-[11px] focus:border-navy focus:ring-2 focus:ring-navy/20 focus:outline-none"
          />
        </label>
        <label className="mt-3 block">
          <span className="font-mono text-[10px] uppercase tracking-wide text-ink/45">Token de acesso (Bearer)</span>
          <input
            type="password"
            value={token}
            onChange={(e) => setTokenInput(e.target.value)}
            placeholder="super-admin-token-demo"
            className="mt-1 w-full rounded-md border border-line bg-surface px-2.5 py-1.5 font-mono text-[11px] placeholder:text-ink/30 focus:border-navy focus:ring-2 focus:ring-navy/20 focus:outline-none"
          />
        </label>

        {error && <p className="mt-3 font-mono text-[10px] text-red">{error}</p>}

        <button
          type="submit"
          disabled={loading}
          className="mt-4 w-full rounded-md bg-navy px-3 py-1.5 font-mono text-[11px] text-surface transition-colors hover:bg-navy/90 disabled:opacity-60"
        >
          {loading ? "Entrando…" : "Entrar"}
        </button>
      </form>
    </div>
  );
}

function Dashboard({ me, onLogout }: { me: Me; onLogout: () => void }) {
  const [path, navigate] = useHashRoute();

  let page = <Overview onNavigate={navigate} />;
  if (path === "/usuarios") page = <Usuarios />;
  else if (path === "/permissoes") page = <Permissoes />;
  else if (path === "/tools") page = <Tools />;
  else if (path === "/regras") page = <Regras />;
  else if (path === "/vault") page = <Vault />;
  else if (path === "/auditoria") page = <Auditoria />;

  return (
    <Shell me={me} path={path} onNavigate={navigate} onLogout={onLogout}>
      {page}
    </Shell>
  );
}
