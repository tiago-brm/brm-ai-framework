#!/usr/bin/env bash
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-backups}"
CLIENT_ID="${CLIENT_ID:-example}"
IDENTIDADE="${AGE_IDENTITY:-}"
AQUI="$(cd "$(dirname "$0")" && pwd)"
PY="${PYTHON:-python3}"

if [[ -n "${1:-}" ]]; then
  ARQUIVO="$1"
else
  ARQUIVO="$(ls -1t "${BACKUP_DIR}/diarios/${CLIENT_ID}-"*.tar.gz.age 2>/dev/null | head -n 1 || true)"
fi
[[ -n "$ARQUIVO" && -f "$ARQUIVO" ]] || { echo "erro: nenhum backup encontrado em ${BACKUP_DIR}/diarios" >&2; exit 2; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

AGE_IDENTITY="$IDENTIDADE" "${AQUI}/restore.sh" "$ARQUIVO" "${TMP}/restaurado"
"$PY" "${AQUI}/verificar_restauracao.py" "${TMP}/restaurado" "$CLIENT_ID"

mkdir -p "$BACKUP_DIR"
date -u +%Y-%m-%dT%H:%M:%SZ > "${BACKUP_DIR}/ULTIMO_TESTE_RESTAURACAO"
echo "teste de restauracao ok: ${ARQUIVO}"
