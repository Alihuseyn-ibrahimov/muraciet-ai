#!/bin/sh
# Production deploy (Linux server, Docker Compose v2). Təkrar işlədilə bilər: yeniləmə də eyni addımlardır.
#
#   sh scripts/deploy.sh            yoxlama → build → miqrasiya → start → sağlamlıq → ehtiyat nüsxə
#   sh scripts/deploy.sh --check    yalnız yoxlama (heç nə dəyişmir)
#
# Ətraflı: docs/deploy.md. Sirlər ./secrets/ fayllarındadır və heç vaxt ekrana çıxarılmır.
set -eu
cd "$(dirname "$0")/.."

COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"
SECRETS="anthropic_api_key data_encryption_keys hmac_key citizen_bot_token staff_bot_token service_token
admin_api_key app_db_password n8n_db_password app_database_url migration_database_url postgres_password
backup_passphrase"

step() { echo "==> $*"; }
fail() { echo "XƏTA: $*" >&2; exit 1; }

step "Fayllar"
[ -f .env.prod ] || fail ".env.prod yoxdur (nümunə: .env.prod.example)"
missing=""
for name in $SECRETS; do
    [ -s "secrets/$name" ] || missing="$missing $name"
done
[ -z "$missing" ] || fail "secrets/ qovluğunda boş və ya olmayan fayllar:$missing"
if [ -n "$(find secrets -type f -perm -o+r 2>/dev/null)" ]; then
    echo "xəbərdarlıq: bəzi sirr faylları hamı tərəfindən oxunur, düzəltmək: chmod 600 secrets/*"
fi
[ -d backups ] || mkdir -p backups

step "Konfiqurasiya"
$COMPOSE config --quiet
if $COMPOSE config | grep -q "published:"; then
    fail "production konfiqurasiyasında açıq port var: heç bir servis internetə açılmamalıdır"
fi

step "Image"
$COMPOSE build api

step "Sirlərin yoxlanması (konteynerdə)"
$COMPOSE up -d postgres redis ollama
$COMPOSE --profile tools run --rm migrate python -m app.manage preflight

if [ "${1:-}" = "--check" ]; then
    step "Yoxlama bitdi (--check): heç nə dəyişmədi"
    exit 0
fi

step "Miqrasiya"
$COMPOSE --profile tools run --rm migrate

step "Servislər"
$COMPOSE up -d

step "API-nin sağlamlığı"
api=$($COMPOSE ps -q api)
for _ in $(seq 1 30); do
    state=$(docker inspect -f '{{.State.Health.Status}}' "$api" 2>/dev/null || echo yoxdur)
    [ "$state" = "healthy" ] && break
    sleep 4
done
[ "$state" = "healthy" ] || fail "api sağlam vəziyyətə keçmədi (son vəziyyət: $state): $COMPOSE logs api"

step "Ehtiyat nüsxə (deploy-dan sonra)"
$COMPOSE exec -T backup sh /scripts/backup.sh once

step "Hazırdır"
$COMPOSE ps
cat <<'EOF'
İlk quraşdırmadan sonra (bir dəfə, docs/deploy.md):
  - embedding modeli:   docker compose ... exec ollama ollama pull bge-m3
  - bilik bazası:       docker compose ... --profile tools run --rm migrate python -m app.manage ingest
  - ilk admin:          docker compose ... --profile tools run --rm migrate python -m app.manage invite coordinator --admin
  - n8n credential-ları və workflow-lar: docs/deploy.md, "n8n" bölməsi
EOF
