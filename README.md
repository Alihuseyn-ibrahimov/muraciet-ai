# Müraciət AI — dövlət qurumu üçün vətəndaş müraciətləri sistemi

Vətəndaş Telegram botuna yazır. AI qurumun bilik bazasından cavab verə bilirsə, mənbə ilə cavablandırır.
Araşdırma tələb olunursa, müraciəti rəsmi qeydə alır və koordinatora yönləndirir. Əməkdaş cavabı əməkdaş
botunda yazır, bot onu vətəndaşa çatdırır. Müddətlər "Vətəndaşların müraciətləri haqqında" Qanuna
(№1308-IVQ) uyğun hesablanır və avtomatik izlənilir.

Ətraflı dizayn: [spec](docs/superpowers/specs/2026-10-21-dovlet-muraciet-sistemi-design.md) ·
[implementasiya planı](docs/superpowers/plans/2026-10-21-merhele-1-implementasiya.md) ·
[pilot yoxlama siyahısı](docs/pilot-checklist.md)

## Prinsiplər

- **Bir agent, bir iş.** 10 agentin hər birinin öz promptu, sxemi və modeli var (`config/agents.yaml`).
  9-cu agent (kurator) vətəndaşın 👍 verdiyi cavabdan bilik bazası üçün ümumi sual-cavab təklif edir;
  bilik bazasına yalnız admin təsdiqindən sonra düşür. 10-cu agent (hesabat analitiki) rəhbər üçün həftəlik
  göstəriciləri şərh edir; rəqəmləri kod hesablayır, agent yeni rəqəm yaza bilməz.
- **AI təklif edir, kod və insan qərar verir.** Agentlər müraciətin vəziyyətini dəyişmir. Vəziyyət
  maşını, icazələr və audit jurnalı koddadır. AI xəta verərsə, sistem daha çox insan nəzarəti ilə davam edir.
- **Şəxsi məlumat LLM-ə getmir.** Bütün LLM çağırışları `PrivateLLM`-dən keçir: ad, FİN, telefon, ünvan
  əvəzedicilərlə ([FIN_1]) dəyişdirilir və cavabda bərpa olunur. FİN, telefon, ünvan bazada şifrəlidir.
- **Mesaj itmir.** Telegram-a gedən hər mesaj outbox cədvəlinə eyni tranzaksiyada yazılır, worker
  təkrar cəhdlərlə göndərir.

## Arxitektura

```
 Vətəndaş ─► citizen-bot ─┐                    ┌─ staff-bot ◄─ koordinator, icraçı, rəhbər
                          ▼                    ▼
                    ┌──────────────── api (FastAPI) ────────────────┐
                    │ privacy: maskalama, şifrələmə                 │
                    │ agentlər: LangGraph (vətəndaş qrafı, triaj)   │
                    │ workflow: vəziyyət maşını, icazələr, audit    │
                    │ notifier: kartlar, outbox                     │
                    └──────┬──────────────────────┬─────────────────┘
              PostgreSQL + pgvector            Redis           worker: outbox + agent işləri
                     ▲
          n8n: SLA və canlı çat (hər 5 dəqiqə), gündəlik hesabat, xəta bildirişi, saxlama təmizliyi
```

| Agent | Model | Vəzifəsi |
|---|---|---|
| 1. Təhlükəsizlik | qaydalar (+ NeMo) | prompt injection, təhqir, çox uzun mesaj |
| 2. Marşrutlaşdırıcı | Haiku 5.5 | niyyət: sual, müraciət, status, aydınlaşdırma, operator... |
| 3. Məlumat (RAG) | Sonnet 5.5 | yalnız bilik bazasına əsaslanan cavab, mənbə ilə |
| 4. Qəbul | Sonnet 5.5 | problemi ən çoxu 3 sualla dəqiqləşdirir |
| 5. Triaj | Haiku 5.5 | kateqoriya, prioritet, hüquqi məsələ |
| 6. Yönləndirmə | Haiku 5.5 | şöbə (`config/departments.yaml`) |
| 7. Cavab layihəsi (ReAct) | Sonnet 5.5 | əməkdaş üçün layihə; bilmədiyini `[...]` ilə boş saxlayır |
| 8. Keyfiyyət | Sonnet 5.5 | cavabı göndərməzdən əvvəl yoxlayır (m. 7.7, 7.8, ziddiyyət, ton) |

## Quraşdırma (Docker)

```bash
cp .env.example .env          # açarları doldurun (fayldakı izahlara baxın)
docker compose up -d postgres redis ollama
docker compose exec ollama ollama pull bge-m3
docker compose --profile tools run --rm migrate                               # baza, rollar, şöbələr
docker compose --profile tools run --rm migrate python -m app.manage ingest    # bilik bazası
docker compose up -d                                                           # api, worker, iki bot, n8n
docker compose --profile tools run --rm migrate python -m app.manage invite coordinator --admin
```

Sonuncu əmr ilk admin koordinator üçün birdəfəlik dəvət linki çap edir. Qalan əməkdaşları admin əməkdaş
botunda `/devet` ilə dəvət edir.

**n8n.** Panel: http://127.0.0.1:5678. Workflow-lar `n8n/v2/`-dədir (sirr yoxdur). Credential-lar
("Müraciət API servis tokeni": `X-Service-Token`; "Müraciət bazası (yalnız oxuma)": `muraciet_n8n` rolu,
host `postgres`) paneldə yaradılır, workflow-lar import edilib aktivləşdirilir.

**Production.** `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d`. Sirlər
`./secrets/` fayllarındadır (siyahı `docker-compose.prod.yml`-də), heç bir port açılmır.

## Production

Serverdə: `docs/deploy.md` (sirlərin yaradılması, `sh scripts/deploy.sh --check`, `sh scripts/deploy.sh`, n8n).
`python -m app.manage preflight` sirlərin dolu və düzgün formatda olduğunu yoxlayır, dəyərləri çap etmir.

## Ehtiyat nüsxə və bərpa

`backup` servisi (`scripts/backup.sh`) hər gün saat `BACKUP_HOUR`-da (standart 02:00, Bakı) `muraciet_v2`-nin
şifrəli nüsxəsini `./backups/`-a yazır (`pg_dump -Fc` + AES-256, parol `secrets/backup_passphrase`) və
`BACKUP_KEEP_DAYS` (standart 14) gündən köhnələri silir. Nüsxələri serverdən kənara da köçürün.

```bash
docker compose exec backup sh /scripts/backup.sh once     # indi bir nüsxə
docker compose exec backup sh /scripts/backup.sh verify   # bərpa sınağı: müvəqqəti bazaya bərpa + sətir sayları
```

Parol və `DATA_ENCRYPTION_KEYS` itsə, nüsxə və ya şəxsi məlumat oxunmaz: hər ikisini ayrıca, təhlükəsiz yerdə
saxlayın. Real bərpa (xidmətlər dayandırılıb): faylı `openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000` ilə açıb
`pg_restore --clean --no-owner -d muraciet_v2`, sonra `docker compose --profile tools run --rm migrate`.

## Konfiqurasiya (kodu dəyişmədən)

| Fayl | Nə |
|---|---|
| `config/departments.yaml` | şöbələr və səlahiyyətləri (`python -m app.manage sync-config`) |
| `config/approval_rules.yaml` | hansı cavablar koordinator təsdiqinə düşür |
| `config/sla.yaml` | iş saatları, qanuni müddətlər, eskalasiya |
| `config/holidays.yaml` | bayram və köçürülmüş iş günləri |
| `config/agents.yaml` | hər agentin provayderi, modeli, effort |
| `config/templates.yaml` | vətəndaşa gedən bütün mətnlər |
| `config/consent.md` | şəxsi məlumatların emalına razılıq (hüquqşünas təsdiqləməlidir) |
| `data/knowledge/*.md` | bilik bazası (`python -m app.manage ingest`) |

## Testlər və qiymətləndirmə

```bash
docker compose up -d postgres redis
pytest -q                                    # unit + inteqrasiya + uçdan-uca (saxta LLM)
python -m eval.run_agent_eval --agent all    # real modellə agent eval (API xərci var)
```

Uçdan-uca test (`tests/integration/test_e2e.py`) iki botu, 8 agenti, outbox-u və iş növbəsini HTTP
səviyyəsində işlədir. Bütün LLM-lərə gedən mətnlərdə vətəndaşın FİN-i, telefonu və ünvanı olmadığını
yoxlayır. LLM-siz rejimdə müraciətin yenə çatdırıldığını da yoxlayır.

## Admin API və MCP

`/admin/stats`, `/admin/appeals`, `/admin/appeals/overdue`, `/admin/appeals/{nömrə}`, `/admin/report` (həftəlik
hesabat, LLM çağırır), `/admin/staff`, `/admin/knowledge-gaps`
(AI-nin cavab tapmadığı suallar, sayı ilə: bilik bazasına nə əlavə olunmalıdır) (`X-API-Key`).
Mətnlər maskalanır, çünki MCP cavabları LLM-ə gedir. Yeganə yazma əməliyyatı təklifdir: MCP-nin
`propose_assignment` aləti (`POST /admin/proposals/assign`) müraciəti dəyişmir, koordinatorlara əməkdaş botunda
adi "✅ Təyin et" düyməsi olan kart göndərir; qərarı koordinator verir. Claude Desktop üçün:

```json
{"mcpServers": {"muraciet-ai": {"command": "python", "args": ["-m", "mcp_server.server"],
  "env": {"MURACIET_API_URL": "http://127.0.0.1:8000", "ADMIN_API_KEY": "..."}}}}
```

## Kurs mövzularının xəritəsi

| Mövzu | Harada |
|---|---|
| #1–6 Python | bütün kod |
| #7–9, #15 Docker, n8n, retry/error | `docker-compose*.yml`, `n8n/v2/` |
| #10 SQLAlchemy, Alembic | `app/db/`, `migrations/` |
| #12 FastAPI, Pydantic | `app/api/`, `app/agents/schemas.py`, `app/yaml_config.py` |
| #13 Redis | sessiyalar, limit (`app/citizen/session.py`) |
| #14 RAG, tool-calling | `app/knowledge/`, `app/agents/faq.py`, `drafter.py` |
| #16 prompt engineering | `app/agents/prompts/` |
| #17–18, #25–26 LangChain, structured output, tool dizaynı | `app/privacy/llm.py`, `app/agents/` |
| #20 MCP | `mcp_server/` |
| #27, #30, #32 yaddaş, LangGraph checkpoint, supervisor | `app/agents/citizen_graph.py`, `staff_pipeline.py` |
| #33 eval | `eval/` |
| #37 NeMo Guardrails | `app/agents/guard.py`, `app/guardrails/config/` |
| #21, #34–35, #38 Ollama, PyTorch, LoRA, vLLM | `finetune/` (Mərhələ 4), embedding Ollama-da |

Növbəti mərhələlər (LangSmith, web scraping ilə bilik bazası, səs, WhatsApp): spec, 15-ci bölmə.
