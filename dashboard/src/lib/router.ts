import { useCallback, useEffect, useState } from "react";

// Roteamento por hash (#/simulador): o painel é servido como arquivo estático
// e não precisa de configuração de fallback no servidor.
function currentPath(): string {
  const hash = window.location.hash.replace(/^#/, "");
  return hash.startsWith("/") ? hash : "/";
}

export function useHashRoute(): [string, (path: string) => void] {
  const [path, setPath] = useState(currentPath);

  useEffect(() => {
    const onChange = () => setPath(currentPath());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);

  const navigate = useCallback((next: string) => {
    window.location.hash = next;
  }, []);

  return [path, navigate];
}
