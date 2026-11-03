import { useCallback, useEffect, useState } from "react";
import {
  CircleAlert,
  CircleCheck,
  CircleX,
  Download,
  FileText,
  KeyRound,
  LoaderCircle,
  ShieldCheck,
} from "lucide-react";
import { getBaseUrl, setBaseUrl } from "@/api";
import { Card } from "@/components/Card";

const TOKEN_KEY = "brm.acao.token";

function lerToken(): string {
  try {
    return sessionStorage.getItem(TOKEN_KEY) || "";
  } catch {
    return "";
  }
}

function guardarToken(token: string): void {
  try {
    if (token) sessionStorage.setItem(TOKEN_KEY, token);
    else sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    // armazenamento indisponível: a sessão vale só nesta aba
  }
}

type Falha = { status: number; motivo: string; extra: Record<string, unknown> };

async function chamar<T>(caminho: string, init?: RequestInit): Promise<T> {
  const resposta = await fetch(`${getBaseUrl()}/api/central${caminho}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${lerToken()}`,
      ...(init?.headers || {}),
    },
  });
  if (!resposta.ok) {
    let corpo: { detail?: unknown } = {};
    try {
      corpo = await resposta.json();
    } catch {
      // sem corpo JSON
    }
    const detalhe = corpo.detail;
    const extra =
      detalhe && typeof detalhe === "object" ? (detalhe as Record<string, unknown>) : {};
    const motivo =
      typeof extra.motivo === "string"
        ? extra.motivo
        : typeof detalhe === "string"
          ? detalhe
          : resposta.statusText;
    throw { status: resposta.status, motivo, extra } satisfies Falha;
  }
  return (await resposta.json()) as T;
}

type Eu = { identidade: string; papel: string; pin_definido: boolean };
type CampoFaltante = { nome: string; rotulo: string; tipo: string; editavel: boolean };
type LinhaCampo = {
  campo: string;
  rotulo: string;
  valor: string | null;
  faltante: boolean;
  invalido?: string;
};
type Item = {
  indice: number;
  tipo: string;
  titulo: string;
  resumo: string;
  dados: { campos?: LinhaCampo[]; [k: string]: unknown };
  campos_faltantes: CampoFaltante[];
};
type Acao = {
  acao_id: string;
  status: string;
  resumo_pedido: string;
  voce: string;
  pedido_por: string;
  pedido_em: string;
  expira_em: string;
  sensivel: boolean;
  requer: "pin" | "liberacao_gestor";
  papel_liberacao?: string;
  pin_definido: boolean;
  itens: Item[];
  aviso?: string;
  resultado?: ResultadoItem[];
  motivo?: string;
  confirmado_por?: string;
  liberado_por?: string;
};
type ResultadoItem = { item: number; ok: boolean; resumo: string; documento_id?: string };
type Confirmacao = {
  acao_id: string;
  status: string;
  motivo?: string;
  resultado: ResultadoItem[];
};

function mensagemDeFalha(f: Falha): string {
  const extra = f.extra;
  if (f.status === 401 && typeof extra.tentativas_restantes === "number") {
    return `${f.motivo} Restam ${extra.tentativas_restantes} tentativa(s).`;
  }
  if (f.status === 423 && typeof extra.bloqueado_ate === "string") {
    return `PIN bloqueado até ${new Date(extra.bloqueado_ate).toLocaleTimeString("pt-BR")}.`;
  }
  if (f.status === 410) return "Este link expirou. Peça um novo no chat.";
  if (f.status === 409) return "Esta ação já foi tratada.";
  if (f.status === 404) return "Ação não encontrada.";
  if (f.status === 503) return "O sistema de origem está indisponível. Tente em instantes.";
  return f.motivo || "Não foi possível concluir.";
}

function formatarHora(iso: string): string {
  return new Date(iso).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
}

export function AcaoPage({ acaoId }: { acaoId: string }) {
  const [token, setToken] = useState(lerToken());
  const [digitado, setDigitado] = useState("");
  const [baseUrl, setBaseUrlInput] = useState(getBaseUrl());
  const [eu, setEu] = useState<Eu | null>(null);
  const [acao, setAcao] = useState<Acao | null>(null);
  const [carregando, setCarregando] = useState(!!lerToken());
  const [erro, setErro] = useState<string | null>(null);
  const [pin, setPin] = useState("");
  const [pinNovo, setPinNovo] = useState("");
  const [valores, setValores] = useState<Record<string, string>>({});
  const [enviando, setEnviando] = useState(false);
  const [resultado, setResultado] = useState<Confirmacao | null>(null);

  const carregar = useCallback(async () => {
    setCarregando(true);
    setErro(null);
    try {
      const [quem, dados] = await Promise.all([
        chamar<Eu>("/eu"),
        chamar<Acao>(`/acoes/${acaoId}`),
      ]);
      setEu(quem);
      setAcao(dados);
    } catch (e) {
      const f = e as Falha;
      if (f.status === 401) {
        guardarToken("");
        setToken("");
        setErro("Sessão inválida. Entre de novo.");
      } else {
        setErro(mensagemDeFalha(f));
      }
    } finally {
      setCarregando(false);
    }
  }, [acaoId]);

  useEffect(() => {
    if (lerToken()) void carregar();
  }, [carregar]);

  function entrar(e: React.FormEvent) {
    e.preventDefault();
    setBaseUrl(baseUrl);
    const novo = digitado.trim();
    guardarToken(novo);
    setToken(novo);
    setDigitado("");
    void carregar();
  }

  async function definirPin(e: React.FormEvent) {
    e.preventDefault();
    setEnviando(true);
    setErro(null);
    try {
      await chamar("/eu/pin", { method: "PUT", body: JSON.stringify({ pin_novo: pinNovo }) });
      setPinNovo("");
      await carregar();
    } catch (err) {
      setErro(mensagemDeFalha(err as Falha));
    } finally {
      setEnviando(false);
    }
  }

  async function decidir(rota: "confirmar" | "liberar") {
    if (!acao) return;
    setEnviando(true);
    setErro(null);
    const campos = acao.itens.flatMap((item) =>
      item.campos_faltantes
        .filter((c) => c.editavel && valores[`${item.indice}:${c.nome}`]?.trim())
        .map((c) => ({
          item: item.indice,
          nome: c.nome,
          valor: valores[`${item.indice}:${c.nome}`].trim(),
        })),
    );
    try {
      const r = await chamar<Confirmacao>(`/acoes/${acao.acao_id}/${rota}`, {
        method: "POST",
        body: JSON.stringify({ pin, campos }),
      });
      setPin("");
      setResultado(r);
    } catch (err) {
      const f = err as Falha;
      setPin("");
      if (f.status === 422 && Array.isArray(f.extra.faltantes)) {
        setErro("Preencha os campos que faltam antes de confirmar.");
      } else {
        setErro(mensagemDeFalha(f));
      }
    } finally {
      setEnviando(false);
    }
  }

  async function cancelar() {
    if (!acao) return;
    setEnviando(true);
    try {
      await chamar(`/acoes/${acao.acao_id}/cancelar`, { method: "POST" });
      await carregar();
    } catch (err) {
      setErro(mensagemDeFalha(err as Falha));
    } finally {
      setEnviando(false);
    }
  }

  async function baixar(documentoId: string) {
    setErro(null);
    try {
      const resposta = await fetch(
        `${getBaseUrl()}/api/central/documentos/${documentoId}/pdf`,
        { headers: { Authorization: `Bearer ${lerToken()}` } },
      );
      if (!resposta.ok) throw new Error(String(resposta.status));
      const url = URL.createObjectURL(await resposta.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = `documento-${documentoId.slice(0, 8)}.pdf`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      setErro("Não foi possível baixar o PDF. Tente de novo.");
    }
  }

  return (
    <div className="content-wrapper mx-auto w-full max-w-3xl space-y-4 p-4">
      <header className="md2-gradient-card flex items-center gap-3 p-4 shadow-lg">
        <ShieldCheck size={28} aria-hidden="true" />
        <div className="leading-tight">
          <p className="text-lg font-semibold">Central de Ações</p>
          <p className="text-sm text-white/80">Confirme o que foi preparado no chat</p>
        </div>
      </header>

      {erro && (
        <p role="alert" className="field-error flex items-center gap-2">
          <CircleAlert size={16} aria-hidden="true" />
          {erro}
        </p>
      )}

      {!token && (
        <Card title="Entrar">
          <form onSubmit={entrar} className="card-body space-y-4">
            <div>
              <label htmlFor="ac-url" className="label">
                Endereço do sistema
              </label>
              <input
                id="ac-url"
                className="input code"
                value={baseUrl}
                onChange={(e) => setBaseUrlInput(e.target.value)}
              />
            </div>
            <div>
              <label htmlFor="ac-token" className="label">
                Seu código de acesso
              </label>
              <input
                id="ac-token"
                type="password"
                autoComplete="off"
                className="input"
                value={digitado}
                onChange={(e) => setDigitado(e.target.value)}
              />
            </div>
            <button type="submit" className="btn btn-primary w-full" disabled={!digitado.trim()}>
              Entrar
            </button>
          </form>
        </Card>
      )}

      {token && carregando && (
        <p className="flex items-center gap-2 text-muted-foreground" aria-busy="true">
          <LoaderCircle size={18} className="animate-spin" aria-hidden="true" />
          Carregando…
        </p>
      )}

      {token && !carregando && eu && !eu.pin_definido && (
        <Card title="Defina o seu PIN">
          <form onSubmit={definirPin} className="card-body space-y-4">
            <p className="hint">
              O PIN confirma as suas ações aqui. Ele é só seu e nunca é digitado no chat.
            </p>
            <div>
              <label htmlFor="ac-pin-novo" className="label">
                Novo PIN (4 a 8 dígitos)
              </label>
              <input
                id="ac-pin-novo"
                type="password"
                inputMode="numeric"
                autoComplete="new-password"
                className="input"
                value={pinNovo}
                onChange={(e) => setPinNovo(e.target.value)}
              />
            </div>
            <button type="submit" className="btn btn-primary" disabled={enviando || !pinNovo}>
              Salvar PIN
            </button>
          </form>
        </Card>
      )}

      {token && !carregando && eu?.pin_definido && acao && !resultado && (
        <Card title={acao.resumo_pedido}>
          <div className="card-body space-y-4">
            <p className="hint">
              Pedido por {acao.pedido_por} em {formatarHora(acao.pedido_em)}. Vale até{" "}
              {formatarHora(acao.expira_em)}. Você é {acao.voce}.
            </p>

            {acao.aviso && (
              <p role="alert" className="badge-outline-warning">
                <CircleAlert size={14} aria-hidden="true" />
                {acao.aviso}
              </p>
            )}

            {acao.status !== "pendente" ? (
              <p className="flex items-center gap-2">
                {acao.status === "confirmada" ? (
                  <CircleCheck size={18} className="text-success" aria-hidden="true" />
                ) : (
                  <CircleX size={18} className="text-danger" aria-hidden="true" />
                )}
                Ação {acao.status}
                {acao.motivo ? `: ${acao.motivo}` : "."}
              </p>
            ) : (
              <>
                {acao.itens.map((item) => (
                  <div key={item.indice} className="space-y-3 rounded-lg border p-4">
                    <div className="flex items-center gap-2">
                      <FileText size={18} aria-hidden="true" />
                      <h3 className="font-semibold">{item.titulo}</h3>
                    </div>
                    <p>{item.resumo}</p>

                    {item.dados.campos && (
                      <table className="data-table">
                        <tbody>
                          {item.dados.campos.map((c) => (
                            <tr key={c.campo}>
                              <th scope="row" className="w-1/3 text-left">
                                {c.rotulo}
                              </th>
                              <td>
                                {c.valor ??
                                  (c.faltante ? (
                                    <span className="badge-outline-warning">Falta</span>
                                  ) : (
                                    "—"
                                  ))}
                                {c.invalido && (
                                  <span className="badge-outline-danger ml-2">{c.invalido}</span>
                                )}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}

                    {item.campos_faltantes.map((c) => {
                      const chave = `${item.indice}:${c.nome}`;
                      return (
                        <div key={chave}>
                          <label htmlFor={`ac-${chave}`} className="label">
                            {c.rotulo}
                          </label>
                          {c.editavel ? (
                            <input
                              id={`ac-${chave}`}
                              className="input"
                              value={valores[chave] || ""}
                              onChange={(e) =>
                                setValores({ ...valores, [chave]: e.target.value })
                              }
                            />
                          ) : (
                            <p className="hint">
                              Este dado não é preenchido aqui. Informe no chat ou cadastre no
                              sistema de origem.
                            </p>
                          )}
                        </div>
                      );
                    })}
                  </div>
                ))}

                {acao.itens.length > 0 && (
                  <div>
                    <label htmlFor="ac-pin" className="label">
                      <KeyRound size={14} className="mr-1 inline" aria-hidden="true" />
                      {acao.requer === "liberacao_gestor"
                        ? "PIN do gestor para liberar"
                        : "Seu PIN para confirmar"}
                    </label>
                    <input
                      id="ac-pin"
                      type="password"
                      inputMode="numeric"
                      autoComplete="off"
                      className="input"
                      value={pin}
                      onChange={(e) => setPin(e.target.value)}
                    />
                  </div>
                )}

                <div className="flex flex-wrap gap-2">
                  {acao.itens.length > 0 && (
                    <button
                      type="button"
                      className="btn btn-primary"
                      disabled={enviando || !pin}
                      onClick={() =>
                        void decidir(acao.requer === "liberacao_gestor" ? "liberar" : "confirmar")
                      }
                    >
                      {enviando && (
                        <LoaderCircle size={16} className="animate-spin" aria-hidden="true" />
                      )}
                      {acao.requer === "liberacao_gestor" ? "Liberar" : "Confirmar"}
                    </button>
                  )}
                  <button
                    type="button"
                    className="btn btn-secondary"
                    disabled={enviando}
                    onClick={() => void cancelar()}
                  >
                    Cancelar ação
                  </button>
                </div>
              </>
            )}
          </div>
        </Card>
      )}

      {resultado && (
        <Card title={resultado.status === "confirmada" ? "Concluído" : "Não foi possível concluir"}>
          <div className="card-body space-y-3">
            {resultado.motivo && <p>{resultado.motivo}</p>}
            {resultado.resultado.map((r) => (
              <div key={r.item} className="flex flex-wrap items-center gap-3">
                {r.ok ? (
                  <CircleCheck size={18} className="text-success" aria-hidden="true" />
                ) : (
                  <CircleX size={18} className="text-danger" aria-hidden="true" />
                )}
                <span>{r.resumo}</span>
                {r.documento_id && (
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    onClick={() => void baixar(r.documento_id as string)}
                  >
                    <Download size={14} aria-hidden="true" />
                    Baixar PDF
                  </button>
                )}
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}
