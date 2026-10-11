import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "@/api";

export type Loadable<T> = {
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
};

// Carrega dados da API uma vez ao montar e expõe `reload` para atualizar após
// uma mutação. `fetcher` é lido por ref: passar uma arrow nova a cada render
// não dispara nova requisição.
export function useLoadable<T>(fetcher: () => Promise<T>): Loadable<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const fetcherRef = useRef(fetcher);
  useEffect(() => {
    fetcherRef.current = fetcher;
  });
  const requestId = useRef(0);

  const load = useCallback(() => {
    const id = ++requestId.current;
    setLoading(true);
    fetcherRef
      .current()
      .then((result) => {
        if (id !== requestId.current) return; // resposta de uma chamada antiga
        setData(result);
        setError(null);
      })
      .catch((err: unknown) => {
        if (id !== requestId.current) return;
        setError(err instanceof ApiError || err instanceof Error ? err.message : "Erro inesperado");
      })
      .finally(() => {
        if (id === requestId.current) setLoading(false);
      });
  }, []);

  useEffect(() => {
    load();
    const requests = requestId;
    return () => {
      requests.current++; // ignora respostas depois de desmontar
    };
  }, [load]);

  return { data, error, loading, reload: load };
}
