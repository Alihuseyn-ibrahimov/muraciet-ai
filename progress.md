# Progress Log

## Session: 2026-10-21

### Brainstorming və spec
- **Status:** complete
- Görülən işlər:
  - Layihənin vəziyyəti öyrənildi: 6 konteyner, ~1350 sətir kod, Telegram-dan real dialoq gəlib.
  - Beş sual verildi və cavablandı:
    - yönləndirmə: koordinator;
    - təsdiq: seçmə;
    - data: hibrid;
    - şəxsiyyət: FİN ilə;
    - yanaşma: A, state machine.
  - İstifadəçinin əlavə tələbi: hər iş üçün ayrıca agent (8 agent).
  - Qanunun mətni oxundu, maddələr dizayna daxil edildi.
  - Dizayn 5 hissədə təqdim olundu və hər biri təsdiqləndi.
  - Spec yazıldı, yoxlandı, git-də commit edildi.
- Yaradılan və dəyişən fayllar:
  - `docs/superpowers/specs/2026-10-21-dovlet-muraciet-sistemi-design.md` (yaradıldı)
  - git repo: `main` ilkin versiya, `merhele-1` spec

### Planlaşdırma
- **Status:** complete
- Görülən işlər:
  - `writing-plans` skill-i mövcud deyil, `planning-with-files` istifadə olundu.
  - 10 fazalı detallı plan yazıldı.
- Yaradılan fayllar:
  - `docs/superpowers/plans/2026-10-21-merhele-1-implementasiya.md`
  - `task_plan.md`, `findings.md`, `progress.md`

### Faza 0: Hazırlıq
- **Status:** complete
- Görülən işlər:
  - `alembic`, `cryptography` və `pyyaml` `requirements.txt`-ə əlavə olundu.
  - `pytest` ayrıca `requirements-dev.txt`-dədir ki, production image-ə düşməsin (plandan kiçik fərq).
  - `pyproject.toml`: `unit`, `integration`, `eval`, `legacy` markerləri. Standart run `-m 'not eval'` ilə gedir.
  - `docker-compose.override.yml`: postgres `127.0.0.1:55432`, redis `127.0.0.1:56379`. Hostdan qoşulma yoxlanıldı, işləyən API təsirlənmədi (`/api/stats` 200).
  - `tests/fakes.py` (`FakeChatModel`): ssenari üzrə cavab verir və "casus" kimi işləyir. Əvvəl test yazıldı.
  - Köhnə testlər `git mv` ilə `tests/legacy/`-ə köçürüldü və `legacy` markeri aldı.
- Yaradılan və dəyişən fayllar:
  - requirements.txt, requirements-dev.txt, pyproject.toml, docker-compose.override.yml
  - tests/fakes.py, tests/unit/test_fakes.py, tests/legacy/test_graph.py (köçürüldü)

### Faza 1: Konfiqurasiya və iş təqvimi
- **Status:** complete
- Görülən işlər:
  - Settings genişləndirildi: iki bot, servis tokeni, şifrələmə açarları, migrasiya URL-i, retention. `require()` çatışmayan dəyişənin adını aydın mesajla göstərir. Köhnə `TELEGRAM_BOT_TOKEN` adı da qəbul olunur. Real `.env` problemsiz yüklənir.
  - `app/workflow/states.py`: enum-lar və Azərbaycan dilində etiketlər. Faza 2-dən bura çəkildi, çünki konfiqurasiya validasiyası ona lazımdır.
  - `app/yaml_config.py`: 7 fayl üçün Pydantic modelləri. Xəta mesajında faylın adı göstərilir. Şablonlarda icazəsiz yer tutucu yoxlanılır. Dırnaqsız vaxt rədd edilir.
  - `config/`: şöbələr (pilot üçün nümunə), təsdiq qaydaları, SLA (qanun maddələri ilə), 2026 bayramları (rəsmi), agentlərin modelləri, şablonlar (m. 7.8 şikayət qaydası daxil), razılıq layihəsi v1.
  - Bayramlar iki rəsmi mənbədən yoxlanıldı: Nazirlər Kabinetinin №362 qərarı və nazirliyin 2026 istehsalat təqvimi. Test 241 iş gününü təsdiqləyir.
  - `app/workflow/calendar.py`: iş günü, iş saatı, qanuni son tarix, təcili 24 saat, tamamlanmamış il üçün xəbərdarlıq.
  - `tzdata` əlavə olundu, `Dockerfile`-a `COPY config` yazıldı.
  - Planda boşluq tapıldı: retention təmizliyi tapşırıq kimi yox idi, 8.5 kimi əlavə olundu.
- Yaradılan və dəyişən fayllar:
  - app/config.py, app/yaml_config.py, app/workflow/{__init__,states,calendar}.py
  - config/{departments,approval_rules,sla,holidays,agents,templates}.yaml, config/consent.md
  - tests/unit/{test_settings,test_yaml_config,test_calendar}.py
  - .env.example, requirements.txt, Dockerfile

### Faza 2: Məlumat modeli, migrasiyalar, şifrələmə
- **Status:** complete
- Görülən işlər:
  - İstifadəçi qərarı: yeni sxem ayrıca `muraciet_v2` bazasındadır, köhnə bot keçidə qədər işləyir.
  - Settings: `APP_DATABASE_URL`, rol adları. `MIGRATION_DATABASE_URL`-in köhnə bazaya avtomatik keçidi ləğv edildi (təhlükəli idi).
  - `app/privacy/crypto.py`: MultiFernet (açar rotasiyası), HMAC kor indeksi, telefon normallaşdırması. 13 test.
  - `app/db/tables.py`: 11 cədvəl, ayrıca metadata. `app/db/engine.py`: modul səviyyəsində sabit engine yoxdur.
  - Alembic: autogenerate ilə yaradıldı, sonra əl ilə düzəldildi (təkrar enum tipləri, vector, v_daily_stats, dəyərlər dondurulub). `alembic check`: "No new upgrade operations".
  - `env.py`-da `include_object`: LangGraph və langchain cədvəlləri autogenerate-də silinməyə namizəd olmur.
  - `app/db/roles.py`: tətbiq rolu (`appeal_events` yalnız INSERT/SELECT) və n8n rolu (yalnız `v_daily_stats`). Ad və parollar injection-a qarşı yoxlanılır.
  - `app/manage.py`: `create-db`, `migrate` (idempotent), `sync-config`.
  - `.env`-ə 7 yeni açar avtomatik yaradıldı (dəyərlər çap edilmədi). İnkişaf bazası quruldu, şöbələr yazıldı.
  - İnteqrasiya testləri: `muraciet_test` hər sessiyada sıfırdan qurulur, test rolları ayrıdır. 8 test.
  - Legacy testləri standart run-dan çıxarıldı (Faza 0-da qeyd olunan risk təsdiqləndi).
- Yaradılan və dəyişən fayllar:
  - app/config.py, app/privacy/{__init__,crypto}.py, app/db/{tables,engine,roles}.py, app/manage.py, app/workflow/states.py
  - alembic.ini, migrations/{env.py,script.py.mako,versions/0001_ilkin_sxem.py}
  - tests/unit/{test_settings,test_crypto}.py, tests/integration/{conftest,test_schema}.py
  - pyproject.toml, .env.example, .env (yalnız yeni açarlar)

### Faza 3: Workflow
- **Status:** complete
- Görülən işlər:
  - Spec cədvəli koda çevrilərkən 9 boşluq tapıldı və həll olundu. Hamısı findings.md-də cədvəl şəklindədir, məsələn: gözləmə vəziyyətindən cavab vermək (10 günlük SLA qaydası üçün), uzatmaların cəmi ≤30 iş günü, deaktivasiyada `pending_approval`-ın taleyi.
  - `machine.py`: 29 keçid. Test gözlənilən cədvəli spec-dən müstəqil yazır, qalan bütün (vəziyyət × əməliyyat) cütlərinin qadağan olduğunu yoxlayır (157 test).
  - `authz.py`: 9 icraçı × 19 əməliyyat matrisi (175 test), `require_admin`.
  - `approval.py`: seçmə təsdiq səbəbləri ilə. Koordinatorun öz cavabı təsdiqsiz gedir, AI xətası təhlükəsiz standarta keçir.
  - `suggest.py`: açıq işi ən az olan əməkdaş, bərabərlikdə növbə ilə.
  - `service.py`: 17 əməliyyat. FOR UPDATE, versiya, audit, Notifier. Anonim müraciət servis səviyyəsində rədd edilir (m. 6.3.2, 6.4).
  - Mutasiya yoxlaması: FOR UPDATE, versiya yoxlaması və authorize ayrı-ayrılıqda sındırıldı. Hər birini məhz uyğun test tutdu.
- Yaradılan və dəyişən fayllar:
  - app/workflow/{errors,machine,authz,approval,suggest,service}.py, app/workflow/states.py
  - tests/unit/{test_machine,test_authz,test_approval}.py, tests/integration/test_workflow.py, tests/fakes.py

### Faza 4: Notifier
- **Status:** complete
- Görülən işlər:
  - `telegram_api.py`: 429 → RetryAfter, 403 → Forbidden, 400 → BadRequest ("not modified" uğurdur), şəbəkə/5xx → TelegramUnavailable. httpx logları söndürülüb, xətalara URL düşmür. Token sızması testinin işlədiyi mutasiya ilə sübut olundu.
  - `cards.py`: koordinator, icraçı və təsdiq kartları, vətəndaş mesajları (templates.yaml-dan). callback ≤64 bayt. Uzun mətn paraqraf sərhədində bölünür. Rədd və qismən təmin cavablarına şikayət qaydası avtomatik əlavə olunur (m. 7.8). Ada şəkilçi qoşulmur ("Təyin et: Rəşad H.").
  - `outbox.py`: eyni tranzaksiyada növbə, SKIP LOCKED, 1d/5d/15d/1s/4s cəhdləri, 429 cəhd sayılmır, 403/400 dərhal ölü. Ölü mesaj barədə aktiv koordinatorlara xəbərdarlıq gedir, xəbərdarlığın öz xətası dövrə yaratmır. Restart bərpası var.
  - `notifier.py` (OutboxNotifier): 14 hadisə → alıcılar və mesajlar. `refresh_cards`: aktiv kartlar yeni versiya ilə yenilənir, aktivliyini itirənlər bağlanır (migrasiya 0002: `telegram_messages.closed_at`).
  - **TDD pozuntusu:** notifier.py testdən əvvəl yazıldı. Testlər sonra yazıldı və 3 mutasiya ilə yoxlandı (yenilənmə, bağlanma, deaktiv süzgəci), hamısı tutuldu.
  - `jobs.py` (JobRunner): handler-i olmayan iş götürülmür, LLM çağırışı DB tranzaksiyasından kənardadır. `worker.py`, compose-da `worker` (profile v2).
  - Test məlumatları `tests/integration/helpers.py`-a köçürüldü.
  - **Qəbul:** istifadəçinin icazəsi ilə real vətəndaş botu vasitəsilə onun çatına test mesajı göndərildi: `sent`, 1-ci cəhddə.
- Yaradılan və dəyişən fayllar:
  - app/notifier/{__init__,telegram_api,cards,outbox,recipients,notifier}.py, app/privacy/display.py, app/jobs.py, app/worker.py
  - app/db/tables.py, migrations/versions/0002_kartlarin_baglanmasi.py, migrations/env.py, docker-compose.yml
  - tests/unit/{test_telegram_api,test_cards}.py, tests/integration/{test_outbox,test_notifier,test_jobs,helpers}.py

### Faza 5: Privacy qatı və 8 agent
- **Status:** complete
- Görülən işlər:
  - `masking.py` (Pseudonymizer): profil dəyərləri (şəkilçi və translit ilə), telefon/FİN/e-poçt/kart regex-i, `foreign_values`. Enum sahələri bərpada toxunulmaz qalır.
  - `privacy/llm.py` (PrivateLLM): LLM-ə yeganə yol, arxitektura testi. Strukturlaşdırılmış çıxış native `json_schema` ilə (Sonnet 5.5 məcburi tool_choice-u rədd edir).
  - `app/llm.py`: `get_chat_model` agents.yaml-dan model və `effort` oxuyur, `max_tokens=8192` (thinking defolt açıqdır). `get_embedding_model` (Ollama bge-m3).
  - `knowledge/kb.py` + `manage.py ingest`: muraciet_v2-də real bilik bazası.
  - 8 agent (guard, router, faq, intake, triage, routing, drafter ReAct, qa), promptlar `app/agents/prompts/`. `staff_pipeline` (triaj və yönləndirmə paralel), `agent_jobs` (worker-də triage/draft işləri), `citizen_graph` (supervisor, Postgres checkpointer; şəxsi məlumat checkpoint-ə düşmür).
  - Eval (#33): `eval/datasets/*.jsonl` (5 agent, 22–28 hal, az/translit/ru), `eval/run_agent_eval.py`, nəticələr `eval/results/*.json`.
  - Keyfiyyət agentinin promptu eval nəticəsinə görə dəqiqləşdirildi (kodların "yaz / yazma" meyarları). **Diqqət:** eyni dəst üzərində düzəldildi, nəticə optimistdir; real cavablarla yeni hallar əlavə olunmalıdır.
  - **TDD pozuntusu:** staff_pipeline, agent_jobs, citizen_graph testdən əvvəl yazıldı; testlər sonra yazıldı və mutasiyalarla yoxlandı (hamısı tutuldu). Eval skripti, enum düzəlişi, json_schema və effort dəyişiklikləri TDD ilə (əvvəl qırmızı test).
- Yaradılan və dəyişən fayllar:
  - app/privacy/{masking,llm,profile}.py, app/knowledge/kb.py, app/agents/*.py, app/agents/prompts/*.md, app/agent_jobs.py, app/llm.py, app/worker.py, app/manage.py, app/yaml_config.py, config/agents.yaml
  - eval/run_agent_eval.py, eval/datasets/*.jsonl, eval/results/*.json
  - tests/unit/{test_masking,test_private_llm,test_agents,test_staff_pipeline,test_citizen_graph,test_agent_eval,test_llm_factory}.py, tests/integration/{test_knowledge,test_agent_jobs,test_checkpoint_privacy}.py, tests/fakes.py

### Faza 6: Vətəndaş botu
- **Status:** complete
- Görülən işlər:
  - İstifadəçinin nazirlik şablonları ("Vətəndaş müraciəti tiktok.docx") əsasında nümunə domeni dəyişdi: bilik bazası 9 sənəd, şöbələr mövzular üzrə, router/triaj/FAQ promptları, eval dəstləri. Eval: router, faq, routing, qa 1.0; triaj legal meyarı dəqiqləşdirildikdən sonra 1.0 (optimist).
  - Qurumların rəsmi telefon/e-poçtu maskalanmır və "başqa şəxsin məlumatı" sayılmır (bilik bazasından avtomatik).
  - `app/citizen/session.py`, `app/citizen/controller.py`, `app/api/{deps,bot,web}.py`, `bot/runner.py`; qrafda `intake_new` addımı.
  - **TDD pozuntusu:** session.py testdən əvvəl yazıldı (test sonra, real Redis ilə). Qalan hər şey əvvəl qırmızı test.
  - **Qəbul (real Telegram):** köhnə bot müvəqqəti dayandırıldı (istifadəçinin icazəsi), yeni API + runner + worker lokal işə salındı. İstifadəçi sual verdi (mənbəli cavab, 👍), müraciət qeydə aldı: MR-2026-000001, son tarix 12.11.2026, triaj: şikayət/yüksək/pensiya. 12 mesaj çatdırıldı, xəta yoxdur. Köhnə bot geri qaldırıldı.
  - Real yoxlamada tapılan xəta: qəbul dialoqunun davamı `question` niyyəti ilə qeydə alınırdı. Düzəldildi (test).
- Yaradılan və dəyişən fayllar:
  - data/knowledge/01–09, config/{departments,templates,agents}.yaml, app/privacy/masking.py, app/agents/{citizen_graph.py,prompts/*}
  - app/citizen/*, app/api/*, bot/runner.py, eval/datasets/*, eval/results/*
  - tests/integration/{test_citizen_controller,test_citizen_session}.py, tests/unit/{test_api,test_runner}.py

### Faza 7: Əməkdaş botu
- **Status:** complete
- Görülən işlər:
  - `app/staff/{controller,session,invites}.py`, `POST /bot/staff/update`, `notifier.show_card`, `manage.py invite`.
  - Kart düymələri, cavab axını (nəticə → mətn/layihə → QA → önizləmə → göndər/təsdiqə), m. 8.1 səbəbləri, uzatma, aidiyyəti üzrə göndərmə (m. 14.0.6), FİN alert ilə, admin əmrləri, /panel, /tap, /islerim.
  - 7 mutasiya: 5-i dərhal tutuldu, 2-si (köhnə çoxaddımlı düymə, saxta admin düyməsi) əlavə testlərlə bağlandı.
  - **Qəbul (iki real bot):** istifadəçi @emekdas_bot-da dəvət linki ilə admin koordinator oldu, @social_az_bot-da müraciət etdi (MR-2026-000002), özünə təyin etdi, AI layihəsi 34 s-də gəldi, cavab göndərildi, vətəndaş 👎 basdı, müraciət yenidən açıldı. 34 mesaj çatdırıldı, xəta yoxdur. Köhnə bot geri qaldırıldı.
  - **Real yoxlamada tapılan ciddi xəta:** AI layihəsindəki `[...]` boşluqları ilə cavab vətəndaşa getdi. Düzəliş: servis səviyyəsində qadağa + önizləmədə göndərmə düyməsi yoxdur + layihənin sonundakı "Hörmətlə," silinir.

### Faza 8: SLA, n8n, hesabat
- **Status:** complete
- Görülən işlər:
  - `app/workflow/sla.py` (SlaMonitor), `app/workflow/retention.py`, `app/notifier/recipients.broadcast`, `app/api/internal.py`.
  - Compose: `api-v2` (profil v2, port açılmır), worker-ə konteyner ünvanları.
  - 8.3: connector-un n8n-i uzaq serverdədir (diaqnostika workflow-u ilə IP yoxlandı, sonra arxivləndi). İstifadəçi lokal n8n seçdi.
  - `n8n/v2/`: SLA monitoru (hər saat), gündəlik hesabat (iş günləri 18:00, Postgres `muraciet_n8n` ilə `v_daily_stats`), xəta bildirişi (Error Trigger), saxlama təmizliyi (03:00). Hamısı aktivdir.
  - **Qəbul:** test icraları uğurlu. Gündəlik hesabat və xəta bildirişi (müvəqqəti xəta verən workflow ilə real xəta) istifadəçinin Telegram-ına çatdı (rəhbər olmadığı üçün admin kimi).
  - TDD: sla, retention, broadcast, internal endpoint-lər əvvəl qırmızı test. Mutasiya: təkrarsızlıq, təcili 24/7, iş saatı qapısı tutuldu; `break` mutasiyası sağ qaldı və əsl xəta göstərdi (düzəldildi, test əlavə olundu).

### Faza 9: Keçid, uçdan-uca test, sənədlər
- **Status:** complete
- Görülən işlər:
  - Köhnə prototip silindi: app/main.py, services, köhnə qraf/tool/promptlar, db/models, rag, cache, köhnə bot, köhnə n8n JSON-ları, tests/legacy, köhnə eval, köhnə konfiqurasiya sahələri.
  - `/admin/*` (yalnız oxuma, maskalanmış mətn), MCP yalnız oxuma, veb-vidcet `/`-da.
  - `test_e2e.py`: iki bot + 8 agent + outbox + iş növbəsi HTTP səviyyəsində; privacy casusu (PrivateLLM-də maskalama söndürüləndə test sızıntını göstərir); LLM-siz rejim.
  - Compose: api, worker, citizen-bot, staff-bot (runner), migrate, n8n 2.29.7; portlar yalnız 127.0.0.1 (dev); `docker-compose.prod.yml` Docker secrets ilə (`env_ignore_empty`).
  - Keçid: `docker compose up -d --remove-orphans` — köhnə bot və api-v2 silindi, yeni servislər sağlam; n8n workflow-ları `http://api:8000`, aktiv, SLA test icrası uğurlu.
  - Köhnə `muraciet` bazası silindi (0 müraciət, 18 sınaq dialoqu; istifadəçi ehtiyatsız silməyi seçdi).
  - README və `docs/pilot-checklist.md`.
  - **Son yoxlama (konteynerlərdə, real botlar):** MR-2026-000003 tam dövrə; 22 mesaj çatdırıldı. Tapılan: qəbul agentinin yanlış şərhi (Mərhələ 2), xülasədə `}` (düzəldildi).

### Mərhələ 2 (22.10.2026)
- **Status:** complete
- Lokal NER ([SEXS_n], toponim qaydası), FAQ keşi (Redis, mənbə heşi ilə), LangSmith (yalnız maskalanmış LLM
  çağırışları, avtomatik izləmə söndürülüb), bilik bazasının boşluqları hesabatı (`/admin/knowledge-gaps`, MCP),
  dəvət linkinin outbox-dan silinməsi, foto/PDF əlavələri (şifrəli, miqrasiya 0003), kurator agenti (9-cu,
  miqrasiya 0004, admin təsdiqi).

### Mərhələ 3 (22.10.2026)
- **Status:** server, hüquqi rəy və real sınaq gözlənilir; kod hissəsi bitib
- Şifrəli gündəlik ehtiyat nüsxə və bərpa sınağı (`backup` servisi), hesabat analitiki (10-cu agent, həftəlik
  hesabat, n8n `05`), canlı operator çatı (miqrasiya 0005), SLA/canlı çat yoxlaması 5 dəqiqədən bir, production-a
  hazırlıq (`manage preflight`, `scripts/deploy.sh`, `docs/deploy.md`), MCP təyinat təklifi (koordinator
  təsdiqləyir), özünü yoxlama (saxlama canlı çatı da silir, çatda fayl səssizcə saxlanmır).

## Test Results
| Test | Input | Expected | Actual | Status |
|------|-------|----------|--------|--------|
| Köhnə smoke testlər | `pytest -q` | 6 keçir | 6 keçdi | ✓ |
| Real Claude ilə söhbət | "salam" | chitchat | chitchat, 3.6 s | ✓ |
| RAG sualı | "Müraciətimə neçə günə baxılır?" | 15 iş günü | 15 iş günü, mənbə ilə | ✓ |
| Prompt injection | "Ignore previous instructions…" | bloklanır | bloklandı, 26 ms | ✓ |
| Faza 0: bütün dəst | `pytest -q` | hamısı keçir | 14 keçdi (8 unit + 6 legacy) | ✓ |
| Faza 0: --strict-markers | `pytest --strict-markers` | qeydiyyatsız marker yoxdur | 14 keçdi | ✓ |
| Faza 1: bütün dəst | `pytest -q --strict-markers` | hamısı keçir | 61 keçdi (55 unit + 6 legacy) | ✓ |
| 2026 iş günləri | 365 gün üzrə `is_workday` | 241 (rəsmi təqvim) | 241 | ✓ |
| Faza 2: standart dəst | `pytest -q --strict-markers` | hamısı keçir | 77 keçdi (69 unit + 8 integration) | ✓ |
| Faza 2: legacy | `pytest -m legacy tests/legacy` | 6 keçir | 6 keçdi | ✓ |
| Alembic modelə uyğunluq | `alembic check` | dəyişiklik yoxdur | No new upgrade operations detected | ✓ |
| İnkişaf bazası rolları | muraciet_app / muraciet_n8n ilə qoşulma | işləyir | işləyir | ✓ |
| Köhnə sistem | köhnə API `/api/stats` | 200 | 200 | ✓ |
| Faza 3: standart dəst | `pytest -q --strict-markers` | hamısı keçir | 447 keçdi (414 unit + 33 integration) | ✓ |
| Faza 3: mutasiya | FOR UPDATE / versiya / authorize ayrı-ayrılıqda sındırıldı | hər biri tutulur | 3/3 tutuldu | ✓ |
| Faza 4: standart dəst | `pytest -q --strict-markers` | hamısı keçir | 497 keçdi (437 unit + 60 integration) | ✓ |
| Faza 4: mutasiya | token logu, kart yenilənməsi, bağlanma, deaktiv süzgəci | hər biri tutulur | 4/4 tutuldu | ✓ |
| Faza 4: real Telegram | outbox → vətəndaş botu → istifadəçinin çatı | sent | sent, 1-ci cəhddə | ✓ |
| Faza 5: standart dəst | `pytest -q` | hamısı keçir | 592 keçdi | ✓ |
| Faza 5: mutasiya | eval metrikləri (6), checkpoint config, triaj paralelliyi, maskalama | hər biri tutulur | hamısı tutuldu | ✓ |
| Eval: router (Haiku 5.5, low) | 28 hal | yüksək dəqiqlik | accuracy 1.0, p50 1.4 s | ✓ |
| Eval: faq (Sonnet 5.5, low) | 28 hal | uydurma 0 | answerable 1.0, keyword 1.0, hallucination 0.0, p50 2.1 s | ✓ |
| Eval: triage (Haiku 5.5, low) | 23 hal | prioritet aşağı salınmır | hamısı 1.0, under_prioritized 0, urgent_recall 1.0 | ✓ |
| Eval: routing (Haiku 5.5, low) | 23 hal | yüksək dəqiqlik | accuracy 1.0 | ✓ |
| Eval: qa (Sonnet 5.5, medium), 1-ci | 22 hal | az yanlış irad | recall 1.0, amma exact 0.364, təmiz cavaba irad 25% | ✗ |
| Eval: qa, prompt düzəlişindən sonra | 22 hal | az yanlış irad | exact 0.955, təmiz cavaba irad 0%, recall 1.0 | ✓ |
| Eval (nazirlik dəstləri) | 5 agent | yüksək dəqiqlik | router/faq/routing/qa 1.0 (qa yeni dəstdə), triaj legal 0.70 → 1.0 | ✓ |
| Faza 6: standart dəst | `pytest -q` | hamısı keçir | 636 keçdi | ✓ |
| Faza 6: mutasiya | kontakt sahibi, status süzgəci, forma jurnalı, təkrar update, qrup çatı, reply sahibi | hər biri tutulur | 6/6 (sonuncu əlavə testlə) | ✓ |
| Faza 6: real Telegram | sual + müraciət | cavab və MR nömrəsi | MR-2026-000001, 12 mesaj sent | ✓ |
| Faza 7: standart dəst | `pytest -q` | hamısı keçir | 668 keçdi | ✓ |
| Faza 7: mutasiya | deaktiv, dəvət vaxtı, təkrar dəvət, authorize, FİN mesajla, versiya, admin | hər biri tutulur | 7/7 (2-si əlavə testlə) | ✓ |
| Faza 7: real Telegram (iki bot) | tam dövrə | vətəndaş cavabı alır | MR-2026-000002, 34 mesaj sent; `[...]` xətası tapıldı və düzəldildi | ✓ |
| Faza 8: n8n test icraları | 4 workflow | uğurlu | SLA, təmizlik, hesabat, xəta: uğurlu; hesabat və xəta bildirişi Telegram-a çatdı | ✓ |
| Faza 9: standart dəst | `pytest -q` | hamısı keçir | 702 keçdi (legacy yoxdur) | ✓ |
| Faza 9: privacy mutasiyası | PrivateLLM maskalamasız | test_e2e qırılır | FİN, telefon, ünvan tapıldı | ✓ |
| Mərhələ 2: NER mutasiyası | NER söndürülür | test_e2e başqa şəxsin adını tapır | qırıldı | ✓ |
| Eval: curator (Sonnet 5.5, medium) | 23 hal | yanlış təklif 0, PII 0 | accuracy 1.0, false_proposal 0, pii_leaks 0 | ✓ |
| Analitik, real model (göndərilmədən) | son 7 gün | uydurma yoxdur | rəqəmlər düz; 2 uydurma əlaqə → prompt düzəldildi → yoxdur | ✓ |
| Ehtiyat nüsxə | once + verify | 20 cədvəl eynidir | eynidir; zədələnmiş fayl və səhv parol rədd olunur | ✓ |
| Prod konfiqurasiyası (saxta sirlərlə) | compose config | açılır, port yoxdur | `.env` məcburi idi → düzəldildi; 0 açıq port | ✓ |
| Mərhələ 3: standart dəst | `pytest -q` | hamısı keçir | 836 keçdi | ✓ |
| Real avtomatik sınaq: canlı çat (22.10.2026, istifadəçinin razılığı ilə) | real LLM + Telegram | tam dövrə | 5/5: human niyyəti, kart, qəbul, gözləmə mesajı, iki tərəfli ötürmə, /bitir, 3 mesaj jurnalda | ✓ |
| Real avtomatik sınaq: müraciət | real LLM + Telegram | tam dövrə | 11/11: MR-2026-000004, foto+PDF, triaj (request/high), təyinat, 2 fayl əməkdaşa, layihə, QA, cavab, 👍, kurator təklifi | ✓ |

## Error Log
| Timestamp | Error | Attempt | Resolution |
|-----------|-------|---------|------------|
| 2026-10-20 | Ollama portu 11434 məşğuldur | 1 | Host port xəritəsi silindi |
| 2026-10-20 | `temperature` claude-sonnet-5-5 ilə işləmir | 1 | Anthropic üçün ötürülmür |
| 2026-10-21 | WebFetch PDF-i oxuya bilmədi | 1 | Saxlanmış fayl pypdf ilə oxundu |
| 2026-10-21 | YAML flow-da vergüllü bayram adı iki açara bölünürdü | 1 | Adlar dırnağa alındı |
| 2026-10-21 | Konsolda cp1252 ilə Unicode çıxışı | 2 | `PYTHONIOENCODING=utf-8` |
| 2026-10-22 | Bash heredoc `\n`-i sətir sonuna çevirdi (bir neçə dəfə) | 3 | Kod dəyişiklikləri Write/Edit və ya skript faylı ilə |
| 2026-10-22 | Mutasiyadan sonra `git checkout` faylın commit olunmamış dəyişikliklərini sildi | 1 | Ehtiyat nüsxədən bərpa; bundan sonra yalnız `cp` ilə |
| 2026-10-22 | Səhv yolda fayl yaradıldı (`C:\ALIHUS~1`) | 1 | Fayl silindi, boş qovluğu istifadəçi silməlidir |

## 5-Question Reboot Check
| Question | Answer |
|----------|--------|
| Where am I? | Mərhələ 1-3-ün kod hissəsi bitib; server, hüquqi rəy və real sınaq gözlənilir |
| Where am I going? | Pilot: docs/pilot-checklist.md, docs/deploy.md |
| What's the goal? | task_plan.md-dəki Goal |
| What have I learned? | findings.md |
| What have I done? | Yuxarıdakı jurnal |
