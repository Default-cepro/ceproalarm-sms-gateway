#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: tools/setup_adb_local_webhooks.sh --password <LOCAL_PASSWORD> [options]

Options:
  --adb-serial <serial>        Use specific device serial (adb -s).
  --username <user>            Local server username (default: sms).
  --password <pass>            Local server password (required).
  --device-api-port <port>     Device local API port (default: 8080).
  --forward-port <port>        Forward port PC -> phone (default: 18080).
  --reverse-port <port>        Reverse port phone -> PC (default: 9876).
  --server-port <port>         FastAPI server port on PC (default: 8000).
  --webhook-path <path>        Webhook path on server (default: /webhook/sms/events).
  --events <csv>               Comma-separated events (default: sms:received,sms:sent,sms:delivered,sms:failed).
  --adb-bin <path>             Override adb binary (default: auto-detect adb/adb.exe).
  --skip-env-update            Do not update .env.
  -h, --help                   Show help.
EOF
}

ADB_SERIAL=""
USERNAME="sms"
PASSWORD=""
DEVICE_API_PORT=8080
FORWARD_PORT=18080
REVERSE_PORT=9876
SERVER_PORT=8000
WEBHOOK_PATH="/webhook/sms/events"
EVENTS=("sms:received" "sms:sent" "sms:delivered" "sms:failed")
SKIP_ENV_UPDATE=0
ADB_BIN="${ADB_BIN:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --adb-serial|-s)
      ADB_SERIAL="$2"; shift 2 ;;
    --username|-u)
      USERNAME="$2"; shift 2 ;;
    --password|-p)
      PASSWORD="$2"; shift 2 ;;
    --device-api-port)
      DEVICE_API_PORT="$2"; shift 2 ;;
    --forward-port)
      FORWARD_PORT="$2"; shift 2 ;;
    --reverse-port)
      REVERSE_PORT="$2"; shift 2 ;;
    --server-port)
      SERVER_PORT="$2"; shift 2 ;;
    --webhook-path)
      WEBHOOK_PATH="$2"; shift 2 ;;
    --events)
      IFS=',' read -r -a EVENTS <<<"$2"; shift 2 ;;
    --adb-bin)
      ADB_BIN="$2"; shift 2 ;;
    --skip-env-update)
      SKIP_ENV_UPDATE=1; shift ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1 ;;
  esac
done

if [[ -z "$PASSWORD" ]]; then
  echo "Error: --password is required." >&2
  usage
  exit 1
fi

if [[ -z "$ADB_BIN" ]]; then
  if command -v adb >/dev/null 2>&1; then
    ADB_BIN="adb"
  elif command -v adb.exe >/dev/null 2>&1; then
    ADB_BIN="adb.exe"
  else
    echo "Error: adb not found. Install adb or pass --adb-bin." >&2
    exit 1
  fi
fi

adb_cmd=("$ADB_BIN")
if [[ -n "$ADB_SERIAL" ]]; then
  adb_cmd+=("-s" "$ADB_SERIAL")
fi

run_adb() {
  "${adb_cmd[@]}" "$@"
}

try_get_endpoint() {
  local base_url="$1"
  local url=""
  local code=""
  for path in "/webhooks" "/3rdparty/v1/webhooks"; do
    url="${base_url}${path}"
    code="$(curl -s -o /dev/null -w "%{http_code}" -u "$USERNAME:$PASSWORD" --connect-timeout 8 "$url" || true)"
    if [[ "$code" == "200" || "$code" == "401" || "$code" == "403" ]]; then
      echo "$url"
      return 0
    fi
  done
  return 1
}

echo "1) Verificando ADB y dispositivo..."
run_adb start-server
run_adb get-state
run_adb devices -l

echo "2) Configurando ADB forward (PC -> telefono API local)..."
run_adb forward --remove-all
run_adb forward "tcp:${FORWARD_PORT}" "tcp:${DEVICE_API_PORT}"

echo "3) Configurando ADB reverse (telefono -> servidor local PC)..."
run_adb reverse --remove-all
run_adb reverse "tcp:${REVERSE_PORT}" "tcp:${SERVER_PORT}"

webhook_url="http://127.0.0.1:${REVERSE_PORT}${WEBHOOK_PATH}"
forward_base="http://127.0.0.1:${FORWARD_PORT}"

echo "4) Detectando endpoint API del telefono por forward..."
endpoint="$(try_get_endpoint "$forward_base" || true)"
if [[ -z "$endpoint" ]]; then
  endpoint="${forward_base}/webhooks"
  echo "No se pudo autodetectar endpoint, usando fallback: ${endpoint}"
fi
echo "Endpoint API detectado: ${endpoint}"

echo "4.1) Precheck de disponibilidad local API..."
health_code="$(curl -s -o /dev/null -w "%{http_code}" -u "$USERNAME:$PASSWORD" "${forward_base}/" || true)"
if [[ "$health_code" == "200" ]]; then
  echo "Local API reachable: 200"
else
  echo "Precheck retorno HTTP ${health_code}, se continua con registro para confirmar."
fi

echo "5) Registrando webhooks locales en telefono..."
tmp_resp="$(mktemp)"
for event_name in "${EVENTS[@]}"; do
  body="$(printf '{"url":"%s","event":"%s"}' "$webhook_url" "$event_name")"
  http_code="$(curl -s -o "$tmp_resp" -w "%{http_code}" -u "$USERNAME:$PASSWORD" \
    -H "Content-Type: application/json" -d "$body" "$endpoint" || true)"
  if [[ "$http_code" =~ ^2 ]]; then
    echo "[OK] event=${event_name} response=$(cat "$tmp_resp")"
  else
    echo "[ERROR] event=${event_name} http=${http_code} detail=$(cat "$tmp_resp")"
  fi
done
rm -f "$tmp_resp"

echo "6) Consultando lista actual de webhooks..."
if ! curl -s -u "$USERNAME:$PASSWORD" "$endpoint"; then
  echo "No se pudo listar webhooks." >&2
fi

escape_sed() {
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//&/\\&}"
  s="${s//|/\\|}"
  echo "$s"
}

upsert_env() {
  local key="$1"
  local value="$2"
  local env_path="$3"
  local escaped
  escaped="$(escape_sed "$value")"
  if grep -qE "^${key}=" "$env_path"; then
    sed -i -e "s|^${key}=.*|${key}=${escaped}|" "$env_path"
  else
    printf '%s=%s\n' "$key" "$value" >> "$env_path"
  fi
}

if [[ "$SKIP_ENV_UPDATE" -eq 0 ]]; then
  echo "7) Actualizando .env para modo local API..."
  env_path=".env"
  touch "$env_path"
  upsert_env "SMS_GATE_LOCAL_API_ENABLED" "1" "$env_path"
  upsert_env "SMS_GATE_LOCAL_API_BASE_URL" "http://127.0.0.1:${FORWARD_PORT}" "$env_path"
  upsert_env "SMS_GATE_LOCAL_API_USERNAME" "$USERNAME" "$env_path"
  upsert_env "SMS_GATE_LOCAL_API_PASSWORD" "$PASSWORD" "$env_path"
  upsert_env "SMS_GATE_SERVER_PORT" "${SERVER_PORT}" "$env_path"
  echo "Archivo .env actualizado para este dispositivo."
fi

echo ""
echo "Configuracion completada."
echo "Webhook URL usada por el telefono: ${webhook_url}"
echo "Asegurate de tener tu FastAPI activo en localhost:${SERVER_PORT}."
