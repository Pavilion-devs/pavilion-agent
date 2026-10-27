#!/usr/bin/env bash
# Checks that Meterless Nightshift is reachable and that a real model answers.
# Reads METERLESS_BASE_URL / METERLESS_RUNTIME_TOKEN / METERLESS_MODEL from zetaris/.env.
set -euo pipefail

cd "$(dirname "$0")/.."
if [[ ! -f .env ]]; then
  echo "No .env found. Copy .env.example to .env and fill in the Nightshift Console values." >&2
  exit 1
fi
set -a; source .env; set +a
: "${METERLESS_BASE_URL:?missing in .env}"
: "${METERLESS_RUNTIME_TOKEN:?missing in .env}"
MODEL="${METERLESS_MODEL:-meterless/auto}"

headers=(-H "Authorization: Bearer $METERLESS_RUNTIME_TOKEN" -H "meterless-app: causeway" -H "meterless-scope: user")

echo "== Models exposed by Nightshift =="
curl -sS --max-time 20 "$METERLESS_BASE_URL/models" "${headers[@]}" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); [print(" -", m.get("id")) for m in d.get("data", [])]'

echo
echo "== Test chat request (model: $MODEL; can take up to ~75s) =="
curl -sS --max-time 180 "$METERLESS_BASE_URL/chat/completions" "${headers[@]}" \
  -H "Content-Type: application/json" \
  -d "{\"model\": \"$MODEL\", \"messages\": [{\"role\": \"user\", \"content\": \"Reply with one sentence confirming this connection works.\"}]}" \
  | python3 -c '
import json, sys
d = json.load(sys.stdin)
if "error" in d:
    sys.exit("Error from Nightshift: " + json.dumps(d["error"]))
print("Answer:", d["choices"][0]["message"]["content"].strip())
print("Model field:", d.get("model"))
extra = {k: v for k, v in d.items() if k not in ("choices", "usage", "id", "object", "created", "model")}
if extra:
    print("Receipt/extra fields:", json.dumps(extra, indent=2)[:2000])
'
