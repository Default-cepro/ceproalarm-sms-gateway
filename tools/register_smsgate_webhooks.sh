#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: tools/register_smsgate_webhooks.sh --username <USER> --password <PASS> --webhook-url <URL> [options]

Options:
  --mode <Cloud|Private>        Registration mode (default: Cloud).
  --api-base <URL>              Base endpoint for Private mode (e.g. https://server/3rdparty/v1/webhooks).
  --device-id <ID>              Optional device_id for registration.
  --events <csv>                Comma-separated events (default: sms:received,sms:sent,sms:delivered,sms:failed).
  -h, --help                    Show help.
EOF
}

USERNAME=""
PASSWORD=""
WEBHOOK_URL=""
MODE="Cloud"
API_BASE=""
DEVICE_ID=""
EVENTS=("sms:received" "sms:sent" "sms:delivered" "sms:failed")

while [[ $# -gt 0 ]]; do
  case "$1" in
    --username|-u)
      USERNAME="$2"; shift 2 ;;
    --password|-p)
      PASSWORD="$2"; shift 2 ;;
    --webhook-url)
      WEBHOOK_URL="$2"; shift 2 ;;
    --mode)
      MODE="$2"; shift 2 ;;
    --api-base)
      API_BASE="$2"; shift 2 ;;
    --device-id)
      DEVICE_ID="$2"; shift 2 ;;
    --events)
      IFS=',' read -r -a EVENTS <<<"$2"; shift 2 ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1 ;;
  esac
done

if [[ -z "$USERNAME" || -z "$PASSWORD" || -z "$WEBHOOK_URL" ]]; then
  echo "Error: --username, --password, and --webhook-url are required." >&2
  usage
  exit 1
fi

if [[ "$MODE" == "Cloud" ]]; then
  endpoint="https://api.sms-gate.app/3rdparty/v1/webhooks"
else
  if [[ -z "$API_BASE" ]]; then
    echo "Error: --api-base is required for Private mode." >&2
    exit 1
  fi
  endpoint="${API_BASE%/}"
fi

echo "Endpoint de registro: ${endpoint}"
echo "Webhook destino: ${WEBHOOK_URL}"
echo "Eventos: ${EVENTS[*]}"
if [[ -n "$DEVICE_ID" ]]; then
  echo "device_id: ${DEVICE_ID}"
fi

tmp_resp="$(mktemp)"
for event_name in "${EVENTS[@]}"; do
  if [[ -n "$DEVICE_ID" ]]; then
    body="$(printf '{"url":"%s","event":"%s","device_id":"%s"}' "$WEBHOOK_URL" "$event_name" "$DEVICE_ID")"
  else
    body="$(printf '{"url":"%s","event":"%s"}' "$WEBHOOK_URL" "$event_name")"
  fi

  http_code="$(curl -s -o "$tmp_resp" -w "%{http_code}" -u "$USERNAME:$PASSWORD" \
    -H "Content-Type: application/json" -d "$body" "$endpoint" || true)"

  if [[ "$http_code" =~ ^2 ]]; then
    echo "[OK] Registrado ${event_name} => $(cat "$tmp_resp")"
  else
    echo "[ERROR] Fallo al registrar ${event_name}: HTTP ${http_code} - $(cat "$tmp_resp")"
  fi
done
rm -f "$tmp_resp"
