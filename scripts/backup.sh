#!/usr/bin/env bash
set -euo pipefail

CLIENT_ID="${CLIENT_ID:-example}"
DATA_ROOT="${DATA_ROOT:-data}"
VAULT_DIR="${VAULT_DIR:-clients/${CLIENT_ID}/vault}"
BACKUP_DIR="${BACKUP_DIR:-backups}"
RETENCAO_DIARIOS="${RETENCAO_DIARIOS:-14}"
RETENCAO_MENSAIS="${RETENCAO_MENSAIS:-6}"
SEGUNDO_DESTINO="${SEGUNDO_DESTINO:-}"
SEGUNDO_DIR="${SEGUNDO_DIR:-/backups2}"
RECIPIENT="${BRM_BACKUP_AGE_RECIPIENT:-}"

if [[ -z "$RECIPIENT" ]]; then
  echo "erro: defina BRM_BACKUP_AGE_RECIPIENT (chave publica age); backup sem cifra e proibido" >&2
  exit 2
fi
command -v age >/dev/null || { echo "erro: age nao encontrado" >&2; exit 2; }
command -v sqlite3 >/dev/null || { echo "erro: sqlite3 nao encontrado" >&2; exit 2; }

SRC="${DATA_ROOT}/${CLIENT_ID}"
[[ -d "$SRC" ]] || { echo "erro: ${SRC} nao existe" >&2; exit 2; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
PACOTE="${TMP}/${CLIENT_ID}"
mkdir -p "${PACOTE}/dados" "${PACOTE}/vault" "$BACKUP_DIR"

bancos=0
for f in "$SRC"/*.db "$SRC"/*.sqlite "$SRC"/*.sqlite3 ${USERS_DB:+"$USERS_DB"}; do
  [[ -f "$f" ]] || continue
  destino="${PACOTE}/dados/$(basename "$f")"
  [[ -e "$destino" ]] && continue
  sqlite3 "$f" ".timeout 10000" ".backup '${destino}'"
  [[ "$(sqlite3 "$destino" 'PRAGMA integrity_check;')" == "ok" ]] \
    || { echo "erro: integridade falhou em $(basename "$f")" >&2; exit 1; }
  bancos=$((bancos + 1))
done

if [[ -f "${SRC}/audit.jsonl" ]]; then
  cp "${SRC}/audit.jsonl" "${PACOTE}/dados/audit.jsonl"
fi
if [[ -d "$VAULT_DIR" ]]; then
  cp -R "${VAULT_DIR}/." "${PACOTE}/vault/"
  rm -f "${PACOTE}/vault/.silverbullet.auth.json"
fi

DATA="$(date -u +%Y%m%dT%H%M%SZ)"
printf 'cliente=%s\ncriado_em=%s\nbancos=%s\n' "$CLIENT_ID" "$DATA" "$bancos" > "${PACOTE}/MANIFESTO.txt"

mkdir -p "${BACKUP_DIR}/diarios" "${BACKUP_DIR}/mensais"
ALVO="${BACKUP_DIR}/diarios/${CLIENT_ID}-${DATA}.tar.gz.age"
tar -C "$TMP" -czf - "$CLIENT_ID" | age -r "$RECIPIENT" -o "${ALVO}.parcial"
mv "${ALVO}.parcial" "$ALVO"
chmod 600 "$ALVO"

MES="$(date -u +%Y%m)"
if ! ls "${BACKUP_DIR}/mensais/${CLIENT_ID}-${MES}"* >/dev/null 2>&1; then
  cp "$ALVO" "${BACKUP_DIR}/mensais/${CLIENT_ID}-${MES}.tar.gz.age"
fi

podar() {
  local pasta="$1" manter="$2"
  ls -1t "${pasta}/${CLIENT_ID}-"*.tar.gz.age 2>/dev/null | tail -n +"$((manter + 1))" | while read -r velho; do
    rm -f -- "$velho"
  done
}
podar "${BACKUP_DIR}/diarios" "$RETENCAO_DIARIOS"
podar "${BACKUP_DIR}/mensais" "$RETENCAO_MENSAIS"

if [[ -n "$SEGUNDO_DESTINO" && -d "$SEGUNDO_DIR" ]]; then
  cp "$ALVO" "${SEGUNDO_DIR}/"
fi

date -u +%Y-%m-%dT%H:%M:%SZ > "${BACKUP_DIR}/ULTIMO_BACKUP"
echo "backup ok: ${ALVO} (${bancos} bancos)"
