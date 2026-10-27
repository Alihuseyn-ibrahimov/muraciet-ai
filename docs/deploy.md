# Production deploy

Bir Linux serveri (Docker Engine + Compose v2), 4 vCPU / 8 GB RAM kifayətdir (Ollama embedding modeli ~1.2 GB).
Heç bir port internetə açılmır: botlar Telegram-a özləri qoşulur (polling), n8n və API yalnız daxili şəbəkədədir.
Əvvəlcə `docs/pilot-checklist.md`-dəki hüquqi bəndlər bağlanmalıdır.

## 1. Kod və parametrlər

```bash
git clone <repo> muraciet-ai && cd muraciet-ai
cp .env.prod.example .env.prod      # ORG_NAME, HOTLINE_TEXT, bot adları, RETENTION_YEARS
mkdir -p secrets && chmod 700 secrets
```

## 2. Sirlər (`secrets/<ad>`, hər faylda bir dəyər, sətir sonu olmadan)

Təsadüfi dəyərlər:

```bash
for n in hmac_key service_token admin_api_key app_db_password n8n_db_password postgres_password backup_passphrase; do
  openssl rand -base64 33 | tr -d '\n/+=' > secrets/$n
done
docker compose -f docker-compose.yml -f docker-compose.prod.yml build api
docker run --rm muraciet-ai python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode(), end='')" > secrets/data_encryption_keys
```

Əl ilə: `anthropic_api_key`, `citizen_bot_token` və `staff_bot_token` (@BotFather, iki ayrı bot). Baza URL-ləri
yuxarıdakı parollarla:

```bash
printf 'postgresql+psycopg://muraciet_app:%s@postgres:5432/muraciet_v2' "$(cat secrets/app_db_password)" > secrets/app_database_url
printf 'postgresql+psycopg://muraciet:%s@postgres:5432/muraciet_v2' "$(cat secrets/postgres_password)" > secrets/migration_database_url
chmod 600 secrets/*
```

**`data_encryption_keys` və `backup_passphrase`-in nüsxəsini serverdən kənarda (məs. seyfdə) saxlayın.** Biri
itsə, şəxsi məlumat, digəri itsə, ehtiyat nüsxələr oxunmaz.

## 3. Deploy

```bash
sh scripts/deploy.sh --check    # yalnız yoxlama: fayllar, açıq port yoxdur, sirlərin formatı (dəyərlər çap olunmur)
sh scripts/deploy.sh            # build → miqrasiya → start → api sağlamlığı → ilk ehtiyat nüsxə
```

İlk dəfə (skriptin sonunda da göstərilir), `C="docker compose -f docker-compose.yml -f docker-compose.prod.yml"`:

```bash
$C exec ollama ollama pull bge-m3
$C --profile tools run --rm migrate python -m app.manage ingest
$C --profile tools run --rm migrate python -m app.manage invite coordinator --admin   # link ilk admin üçün
```

## 4. n8n

n8n-in portu açılmır; workflow-lar CLI ilə idxal olunur:

```bash
N=$($C ps -q n8n)
for f in n8n/v2/*.json; do docker cp "$f" "$N":/tmp/w.json && $C exec -T n8n n8n import:workflow --input=/tmp/w.json; done
```

Credential-lar (bir dəfə, sonra müvəqqəti faylı silin): "Müraciət API servis tokeni" (Header Auth,
`X-Service-Token` = `secrets/service_token`) və "Müraciət bazası (yalnız oxuma)" (Postgres, `postgres:5432`,
`muraciet_v2`, istifadəçi `muraciet_n8n`, parol `secrets/n8n_db_password`). Id-lər workflow-lardakı ilə eyni
olmalıdır: `muracSvcToken001`, `muracPgReadonly1`. UI lazım olsa, SSH tuneli ilə müvəqqəti port açın.
Sonra hər workflow üçün `$C exec -T n8n n8n publish:workflow --id=<id>` və `$C restart n8n`.

## 5. Yeniləmə və geri qayıtma

- Yeniləmə: `git pull && sh scripts/deploy.sh` (miqrasiyalar idempotentdir, deploy-dan sonra nüsxə alınır).
- Geri qayıtma: əvvəlki versiyaya `git checkout <tag> && sh scripts/deploy.sh`. Sxem dəyişibsə, deploy-dan
  əvvəlki ehtiyat nüsxədən bərpa: README, "Ehtiyat nüsxə və bərpa".
- `./backups/` qovluğunu mütəmadi olaraq serverdən kənara köçürün; ayda bir dəfə `backup.sh verify`.
