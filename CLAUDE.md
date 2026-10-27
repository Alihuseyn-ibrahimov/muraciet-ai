# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Layihənin dili Azərbaycan dilidir: kod şərhləri, docstring-lər, commit mesajları, sənədlər və istifadəçi ilə
yazışma Azərbaycan dilindədir. Vətəndaşa gedən mətnlər dövlət dilində olmalıdır (Qanunun 7.5-ci maddəsi).

## Nədir

Dövlət qurumu (nümunə: Əmək və Əhalinin Sosial Müdafiəsi Nazirliyi) üçün vətəndaş müraciətləri sistemi.
Vətəndaş Telegram botuna yazır. AI ya bilik bazasından cavab verir, ya da müraciəti rəsmi qeydə alıb
koordinatora yönləndirir. Əməkdaş cavabı ayrıca əməkdaş botunda yazır. Hüquqi əsas: "Vətəndaşların
müraciətləri haqqında" Qanun №1308-IVQ (müddətlər, m. 7.7, 7.8, 8.1 və s. kodda maddə nömrələri ilə qeyd olunub).

Əsas sənədlər: spec `docs/superpowers/specs/2026-10-21-dovlet-muraciet-sistemi-design.md`, plan
`docs/superpowers/plans/2026-10-21-merhele-1-implementasiya.md`, `docs/pilot-checklist.md`.
`findings.md` tapılmış tələlərin və qərarların jurnalıdır: dəyişiklikdən əvvəl ora baxın.
`task_plan.md` və `progress.md` faza-faza iş jurnalıdır (planning-with-files).

## Əmrlər

Windows + Git Bash; venv `.venv/Scripts/python`. Konsol cp1252 olduğu üçün Azərbaycan hərfləri çap edən
əmrlərdə `PYTHONIOENCODING=utf-8` lazımdır.

```bash
docker compose up -d postgres redis ollama          # testlər üçün: 127.0.0.1:55432 / 56379 / 11435
.venv/Scripts/python -m pytest -q                    # unit + integration (eval istisna, pyproject.toml)
.venv/Scripts/python -m pytest tests/unit -q         # yalnız unit
.venv/Scripts/python -m pytest tests/integration/test_e2e.py -q -k privacy   # tək test
.venv/Scripts/python -m eval.run_agent_eval --agent router   # real Claude, API xərci var: əvvəl icazə alın
.venv/Scripts/python -m app.manage migrate | sync-config | ingest | invite coordinator --admin
docker compose build api && docker compose up -d     # bütün servislər (bir image)
docker compose --profile tools run --rm migrate      # konteynerdə migrasiya
docker compose exec backup sh /scripts/backup.sh verify   # ehtiyat nüsxənin bərpa sınağı
```

Lint aləti qurulmayıb. Sətir uzunluğu təxminən 120.

## Arxitektura (bir neçə faylı oxumadan görünməyənlər)

**Proseslər** (`docker-compose.yml`, bir image): `api` (FastAPI, `app/api/`), `worker` (`app/worker.py`:
outbox + agent işləri), `citizen-bot` və `staff-bot` (`bot/runner.py`: Telegram long polling → `POST
/bot/{kind}/update`, mesaj göndərmir), `n8n` (`n8n/v2/*.json`: hər 5 dəqiqə `POST /internal/sla/check` (+ canlı çat),
gündəlik və həftəlik hesabat, xəta bildirişi, saxlama təmizliyi). Botların bütün məntiqi API-dədir:
`app/citizen/controller.py` və `app/staff/controller.py`.

**Dəyişməz qaydalar** (testlər onları qoruyur, pozmayın):
- **AI təklif edir, kod və insan qərar verir.** Müraciətin vəziyyəti yalnız `AppealService`
  (`app/workflow/service.py`) ilə dəyişir: `authorize` → `machine.next_status` → optimistic locking
  (`version`, `expected_version`) → `appeal_events` auditi (tətbiq rolunun UPDATE/DELETE hüququ yoxdur) →
  `notifier.appeal_event`. Agent heç vaxt vəziyyəti dəyişmir və istisna atmır: xəta olanda `None` qaytarır,
  çağıran tərəf təhlükəsiz standarta keçir (daha çox insan nəzarəti: təsdiq, Ümumi şöbə).
- **LLM-ə yeganə yol `PrivateLLM`-dir** (`app/privacy/llm.py`): mesajları `Pseudonymizer` ilə maskalayır
  ([AD_1], [FIN_1]...; başqa şəxslərin adları lokal NER ilə [SEXS_1], `app/privacy/ner.py`),
  cavabı bərpa edir. LangSmith izi də yalnız buradan (`app/observability.py`): LangChain-in avtomatik izləməsi
  `app/config.py`-də söndürülür, çünki qrafın xam vəziyyətini göndərərdi. Agentlər provayder kitabxanalarını import etmir
  (`tests/unit/test_private_llm.py`). Bilik bazasındakı qurum əlaqələri ictimaidir, maskalanmır.
  `tests/integration/test_e2e.py` bütün ssenari boyu LLM-lərə FİN/telefon/ünvan getmədiyini yoxlayır.
- **Şəxsi məlumat** (FİN, telefon, ünvan) bazada Fernet ilə şifrəlidir (`app/privacy/crypto.py`), axtarış
  HMAC blind index ilədir. LangGraph `config["configurable"]`-dəki sadə dəyərləri checkpoint-ə yazır: PII
  qrafa yalnız `Pseudonymizer` obyekti kimi ötürülür. chat_logs-a forma məlumatı yox, "[forma: fin]" düşür.
- **Əlavələr** (foto/PDF, `attachments` cədvəli): vətəndaş botu faylı yükləyir, məzmun və ad Fernet ilə
  şifrəlidir, müraciət yarananda ona bağlanır. LLM faylları görmür. Əməkdaşa outbox `send_document` ilə gedir:
  növbədə yalnız `attachment_id`, worker göndərmə anında açır (botların file_id-si bir-birinə keçmir). Baxış
  `Action.view_attachments` ilə yoxlanılır və auditə düşür.
- **Canlı çat** (`app/livechat.py`, `live_chats`): vəziyyət bazadadır (Redis sessiyasında yox), bir vətəndaşın
  və bir operatorun yalnız bir açıq çatı ola bilər (qismən unikal indekslər). Açıq çat olanda vətəndaşın mətni
  dialoq qrafına (LLM-ə) getmir, operatora ötürülür. Gözləmə/yazışmasızlıq müddəti `sla/check`-də yoxlanılır.
- **MCP müraciəti dəyişmir.** Yeganə yazma `/admin/proposals/assign`-dır: auditə `assignment_proposed` yazılır,
  koordinatorlara adi `ASSIGN` callback-li kart gedir (icazə, versiya, audit adi təyinat axınındadır).
- **Telegram mesajı outbox-a eyni tranzaksiyada yazılır** (`app/notifier/outbox.py`), worker göndərir
  (təkrar cəhd, 429, 403). Kartlar `telegram_messages` vasitəsilə hər hadisədən sonra yenilənir
  (`refresh_cards`). Callback-lər `kod:müraciət:versiya[:əlavə]` formatındadır (`app/notifier/cards.py`).
- **LLM çağırışı DB tranzaksiyasından kənardadır** (kontrollerlər və `app/agent_jobs.py`: oxu → LLM → yaz).
- **Kritik qaydalar deterministikdir, LLM-ə etibar edilmir:** başqa şəxsin PII-si və rədd cavabında
  şikayət qaydası (`app/agents/qa.py`), `[...]` olan cavab göndərilmir (`service.has_placeholder`), təhlükəsizlik
  qaydaları (`app/agents/guard.py`), qəbul agentinin sual limiti.

**10 agent** (`app/agents/`, promptlar `prompts/*.md`, modellər və `effort` `config/agents.yaml`):
guard, router, faq (RAG, mənbəsiz cavab qəbul olunmur), intake, triage, routing, drafter (ReAct),
qa, curator (vətəndaş 👍 verəndə iş növbəsi ilə bilik bazası təklifi; `kb_entries`, admin əməkdaş botunda
təsdiqləyir, `KnowledgeBase.add_entry` dərhal yazır, `ingest` təsdiqlənmişləri yenidən əlavə edir), analyst (rəhbərin həftəlik hesabatı, `app/reports/weekly.py`:
rəqəmləri SQL hesablayır, mətndə göstəricilərdə olmayan rəqəm varsa təhlil atılır). Vətəndaş tərəfi LangGraph supervisor qrafıdır (`citizen_graph.py`, Postgres checkpointer), triaj və
yönləndirmə paralel qrafdır (`staff_pipeline.py`).

**Müddətlər** `app/workflow/calendar.py` (iş günü, bayramlar `config/holidays.yaml`, iş saatı) və
`app/workflow/sla.py` (təkrarsızlıq `appeal_events`-dəki `sla_notified` açarı ilə).

**Konfiqurasiya kodda deyil, YAML-dadır** (`app/yaml_config.py` yükləyir və yoxlayır): şöbələr, təsdiq
qaydaları, SLA, bayramlar, agentlər, vətəndaşa gedən bütün mətnlər (`config/templates.yaml`; yeni açar
`TEMPLATE_FIELDS`-ə də əlavə olunmalıdır), razılıq mətni (`config/consent.md`).

**Bazalar:** `muraciet_v2` (iş), `muraciet_test` (hər pytest sessiyasında sıfırdan yaradılır və
`manage.migrate` ilə qurulur). Rollar: sahib `muraciet` (migrasiya), `muraciet_app` (tətbiq),
`muraciet_n8n` (yalnız `v_daily_stats`). `.env`-dəki ünvanlar host üçündür (127.0.0.1), konteynerlər
compose-dakı `postgres:5432`, `ollama:11434` ünvanlarını alır. Prod: `docker-compose.prod.yml`, sirlər
`/run/secrets/<sahə_adı>` faylları (`env_ignore_empty`).

## Testlər

- Markerlər: `unit`, `integration` (Postgres + Redis lazımdır, yoxdursa skip), `eval` (real LLM, standartda
  işləmir). Redis testləri 15-ci bazanı işlədir və təmizləyir.
- LLM əvəzinə `tests/fakes.py::FakeChatModel`: ssenari üzrə cavab verir (dict → structured output,
  `AIMessage` tool_calls ilə, `Exception` → provayder xətası) və alınan mesajları `all_text()` ilə verir.
- İnteqrasiya ortamı `tests/integration/helpers.py` (`build_world`: koordinatorlar, icraçılar, rəhbər, 2
  vətəndaş; sabit saat `MONDAY_10`). İş növbəsi `next_attempt_at`-ı bazanın real saatı ilə yazır: işləri
  emal edən testlərdə canlı saat lazımdır (`test_e2e.py`).
- İş qaydası: əvvəl qırmızı test, sonra kod. Təhlükəsizlik qaydalarını mutasiya ilə yoxlamaq qəbul olunub
  (kodu müvəqqəti sındırıb testin qırıldığını görmək, sonra bərpa).

## Tələlər

- Claude 5.5 modelləri: `temperature` qəbul etmir; Sonnet 5.5 məcburi `tool_choice`-u rədd edir, ona görə
  strukturlaşdırılmış çıxış `method="json_schema"` ilədir; thinking defolt açıqdır və `max_tokens`-ə daxildir
  (`app/llm.py`: 8192, `effort` agents.yaml-dan).
- Python-un `lower()` funksiyası "I"-ni Azərbaycan dilinə uyğun çevirmir: `masking.az_lower` istifadə edin.
- n8n 2.x CLI: `n8n execute` üçün workflow-da Manual Trigger lazımdır, işləyən instansiya ilə yanaşı
  `N8N_RUNNERS_BROKER_PORT=5690` verin, aktivləşdirmə `publish:workflow` + restart. Git Bash-də konteyner
  yolları üçün `MSYS_NO_PATHCONV=1`. Connector-un n8n-i (n8n.prdostu.com) uzaqdadır, sistem lokal n8n-də işləyir.
- Real API ilə eval və ya Telegram-a mesaj göndərmək xərc/yan təsir yaradır: əvvəl istifadəçidən icazə alın.
