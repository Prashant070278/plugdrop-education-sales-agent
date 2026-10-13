#!/usr/bin/env bash
# Builds and starts the whole stack in Docker, then checks that it works end to end.
#
#   cp .env.example .env        # set POSTGRES_PASSWORD, JWT_SECRET and SEED_PASSWORD first
#   ./scripts/docker-smoke-test.sh [path/to/research-workbook.xlsx]
#
# Prints PASS/FAIL for each check. Leaves the stack running (stop it with: docker compose down).
set -uo pipefail
cd "$(dirname "$0")/.."

WEB=http://localhost:3000
API=http://localhost:8000
JAR=$(mktemp)
trap 'rm -f "$JAR"' EXIT
fails=0
pass() { printf '  \033[32mPASS\033[0m %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; fails=$((fails + 1)); }
check() { if eval "$2" >/dev/null 2>&1; then pass "$1"; else fail "$1"; fi; }

command -v docker >/dev/null || { echo "Docker is not installed. Install Docker Desktop first."; exit 1; }
[ -f .env ] || { echo "No .env file. Run: cp .env.example .env  (then set POSTGRES_PASSWORD, JWT_SECRET, SEED_PASSWORD)"; exit 1; }
set -a; . ./.env; set +a
PASSWORD=${SEED_PASSWORD:-PlugDrop-demo-2026}

echo "1/5 Checking docker-compose.yml"
docker compose config -q && pass "compose file is valid" || { fail "compose file is valid"; exit 1; }

echo "2/5 Building and starting (first build takes a few minutes)"
docker compose up --build -d || { fail "docker compose up"; exit 1; }

echo "3/5 Waiting for the API (runs database migrations on start)"
for _ in $(seq 1 60); do curl -fsS "$API/api/health" >/dev/null 2>&1 && break; sleep 3; done
check "API is healthy" "curl -fsS $API/api/health"
for _ in $(seq 1 40); do curl -fsS -o /dev/null "$WEB/login" 2>/dev/null && break; sleep 3; done
check "web app serves the sign-in page" "curl -fsS -o /dev/null $WEB/login"

echo "4/5 Seeding users and demo data"
docker compose exec -T api python -m app.seed >/dev/null && pass "seed script" || fail "seed script"

echo "5/5 Signing in through the web app and calling each module"
code=$(curl -s -o /dev/null -w '%{http_code}' -c "$JAR" -H 'Content-Type: application/json' \
  -d "{\"email\":\"admin@plugdrop.example\",\"password\":\"$PASSWORD\"}" "$WEB/api/auth/login")
[ "$code" = 200 ] && pass "sign in as admin" || fail "sign in as admin (HTTP $code — is SEED_PASSWORD the one used when seeding?)"
for path in /api/dashboard /api/institutions /api/leads /api/research/overview /api/outreach/overview /api/outreach/mailboxes /api/outreach/policy /api/whatsapp/status /api/whatsapp/templates /api/meetings /api/engagement/policy /api/engagement/permissions /api/linkedin/items; do
  check "GET $path" "curl -fsS -b $JAR $WEB$path"
done
mode=$(curl -fsS -b "$JAR" "$WEB/api/outreach/policy" | grep -o '"external_sending_allowed":[a-z]*')
[ "$mode" = '"external_sending_allowed":false' ] && pass "external email sending is switched off" || fail "external email sending should be off until configured ($mode)"
wa=$(curl -fsS -b "$JAR" "$WEB/api/whatsapp/status" | grep -o '"external_sending_allowed":[a-z]*')
[ "$wa" = '"external_sending_allowed":false' ] && pass "external WhatsApp sending is switched off" || fail "external WhatsApp sending should be off until configured ($wa)"
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -d '{}' "$WEB/api/whatsapp/webhook")
[ "$code" = 401 ] && pass "WhatsApp webhook rejects unsigned calls" || fail "WhatsApp webhook should reject unsigned calls (HTTP $code)"

if [ "${1:-}" != "" ]; then
  echo "Extra: previewing the research workbook import (nothing is saved)"
  out=$(curl -fsS -b "$JAR" -F "file=@$1" "$WEB/api/research/import-workbook?dry_run=true") \
    && pass "workbook preview: $(echo "$out" | grep -o '"accounts_in_file":[0-9]*'), $(echo "$out" | grep -o '"results_created":[0-9]*')" \
    || fail "workbook preview"
fi

echo
if [ "$fails" -eq 0 ]; then echo "ALL CHECKS PASSED. Open $WEB and sign in as admin@plugdrop.example."; else echo "$fails check(s) failed. See: docker compose logs api web"; fi
exit "$fails"
