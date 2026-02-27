#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: tools/send_signed_test_event.sh --signing-key <KEY> [options]

Options:
  --url <URL>           Webhook URL (default: http://127.0.0.1:80/webhook/sms/events)
  --signing-key <KEY>   Signing key (required).
  --phone-number <NUM>  Phone number (default: 4243616194).
  --message <TEXT>      Message body (default: STATUS OK).
  -h, --help            Show help.
EOF
}

URL="http://127.0.0.1:80/webhook/sms/events"
SIGNING_KEY=""
PHONE_NUMBER="4243616194"
MESSAGE="STATUS OK"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --url)
      URL="$2"; shift 2 ;;
    --signing-key)
      SIGNING_KEY="$2"; shift 2 ;;
    --phone-number)
      PHONE_NUMBER="$2"; shift 2 ;;
    --message)
      MESSAGE="$2"; shift 2 ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1 ;;
  esac
done

if [[ -z "$SIGNING_KEY" ]]; then
  echo "Error: --signing-key is required." >&2
  usage
  exit 1
fi

export SIGNING_KEY PHONE_NUMBER MESSAGE

mapfile -t lines < <(python - <<'PY'
import os
import json
import time
import uuid
import hmac
import hashlib
import datetime

timestamp = str(int(time.time()))
event_id = "evt-" + uuid.uuid4().hex[:12]
message_id = "msg-" + uuid.uuid4().hex[:12]

payload = {
    "deviceId": "manual-test-device",
    "event": "sms:received",
    "id": event_id,
    "payload": {
        "messageId": message_id,
        "message": os.environ["MESSAGE"],
        "phoneNumber": os.environ["PHONE_NUMBER"],
        "simNumber": 1,
        "receivedAt": datetime.datetime.now().astimezone().isoformat(),
    },
    "webhookId": "manual-test-webhook",
}

raw_body = json.dumps(payload, separators=(",", ":"))
signature = hmac.new(
    os.environ["SIGNING_KEY"].encode("utf-8"),
    (raw_body + timestamp).encode("utf-8"),
    hashlib.sha256,
).hexdigest()

print(timestamp)
print(event_id)
print(message_id)
print(raw_body)
print(signature)
PY
)

timestamp="${lines[0]}"
event_id="${lines[1]}"
message_id="${lines[2]}"
raw_body="${lines[3]}"
signature="${lines[4]}"

echo "POST $URL"
echo "event id: $event_id"
echo "message id: $message_id"

response="$(curl -sS -H "Content-Type: application/json" \
  -H "X-Timestamp: $timestamp" \
  -H "X-Signature: $signature" \
  -d "$raw_body" \
  "$URL")"

echo "Respuesta OK:"
echo "$response"
