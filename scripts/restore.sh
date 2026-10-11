#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "uso: AGE_IDENTITY=<arquivo-chave-privada> $0 <backup.tar.gz.age> <diretorio-destino-vazio>" >&2
  exit 2
fi
ARQUIVO="$1"
DESTINO="$2"
IDENTIDADE="${AGE_IDENTITY:-}"

[[ -f "$ARQUIVO" ]] || { echo "erro: ${ARQUIVO} nao existe" >&2; exit 2; }
[[ -n "$IDENTIDADE" && -f "$IDENTIDADE" ]] || { echo "erro: defina AGE_IDENTITY com o arquivo da chave privada age" >&2; exit 2; }
command -v age >/dev/null || { echo "erro: age nao encontrado" >&2; exit 2; }

mkdir -p "$DESTINO"
if [[ -n "$(ls -A "$DESTINO")" ]]; then
  echo "erro: ${DESTINO} nao esta vazio; restauracao nunca sobrescreve dados" >&2
  exit 2
fi

age -d -i "$IDENTIDADE" "$ARQUIVO" | tar -C "$DESTINO" -xzf -
echo "restaurado em ${DESTINO}"
