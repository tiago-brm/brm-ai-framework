import { useEffect, useState } from "react";
import { LoaderCircle, ShieldCheck } from "lucide-react";
import { Shell } from "@/components/Shell";
import { useHashRoute } from "@/lib/router";
import { AcaoPage } from "@/pages/Acao";
import { Auditoria } from "@/pages/Auditoria";
import { Governanca } from "@/pages/Governanca";
import { Overview } from "@/pages/Overview";
import { Permissoes } from "@/pages/Permissoes";
import { Regras } from "@/pages/Regras";
import { Simulador } from "@/pages/Simulador";
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

function acaoDaUrl(): string | null {
  const alvo = window.location.pathname.startsWith("/acao/")
    ? window.location.pathname
    : window.location.hash.replace(/^#/, "");
  const achado = alvo.match(/^\/acao\/([A-Za-z0-9_-]{8,64})\/?$/);
  return achado ? achado[1] : null;
}

export default function App() {
  const acaoId = acaoDaUrl();
  if (acaoId) return <AcaoPage acaoId={acaoId} />;
  return <Painel />;
}

function Painel() {
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
      <div className="grid min-h-screen place-items-center text-muted-foreground" aria-busy="true">
        <span className="flex items-center gap-2">
          <LoaderCircle size={18} className="animate-spin" aria-hidden="true" />
          Carregando…
        </span>
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
    <div className="grid min-h-screen place-items-center p-4">
      <form onSubmit={handleSubmit} className="card animate-fade-in-up w-full max-w-sm">
        <div className="md2-gradient-card -mt-6 mx-4 flex items-center gap-3 p-4 shadow-lg">
          <ShieldCheck size={28} aria-hidden="true" />
          <div className="leading-tight">
            <p className="text-lg font-semibold">BRM Engine</p>
            <p className="text-sm text-white/80">Governança de IA</p>
          </div>
        </div>
        <div className="card-body space-y-4">
          <div>
            <h1 className="text-xl font-semibold">Painel do super_admin</h1>
            <p className="text-muted-foreground">Usuários, permissões, regras e auditoria do motor.</p>
          </div>

          <div>
            <label htmlFor="base-url" className="label">
              URL do BRM Engine
            </label>
            <input
              id="base-url"
              value={baseUrl}
              onChange={(e) => setBaseUrlInput(e.target.value)}
              className="input code"
            />
          </div>
          <div>
            <label htmlFor="token" className="label">
              Token de acesso
            </label>
            <input
              id="token"
              type="password"
              value={token}
              onChange={(e) => setTokenInput(e.target.value)}
              aria-invalid={!!error}
              aria-describedby={error ? "login-error" : undefined}
              className="input"
            />
          </div>

          {error && (
            <p id="login-error" role="alert" className="field-error">
              {error}
            </p>
          )}

          <button type="submit" disabled={loading} className="btn btn-primary w-full">
            {loading && <LoaderCircle size={16} className="animate-spin" aria-hidden="true" />}
            {loading ? "Entrando…" : "Entrar"}
          </button>
        </div>
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
  else if (path === "/simulador") page = <Simulador />;
  else if (path === "/governanca") page = <Governanca />;

  return (
    <Shell me={me} path={path} onNavigate={navigate} onLogout={onLogout}>
      {page}
    </Shell>
  );
}
