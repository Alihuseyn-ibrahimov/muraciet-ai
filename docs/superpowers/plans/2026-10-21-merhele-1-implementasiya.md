# Mərhələ 1: implementasiya planı

- **Spec:** [2026-10-21-dovlet-muraciet-sistemi-design.md](../specs/2026-10-21-dovlet-muraciet-sistemi-design.md)
- **Branch:** `merhele-1`
- **İzləmə faylları** (layihənin kökündə):
  - `task_plan.md`: fazaların statusu;
  - `progress.md`: sessiya jurnalı və test nəticələri;
  - `findings.md`: kəşflər və texniki qərarlar.

---

## İcra qaydaları

1. **Əvvəl test yazılır.** Hər tapşırıqda əvvəlcə uğursuz test yazılır, sonra kod, sonra testin keçdiyi yoxlanılır.
2. **Hər faza işlək vəziyyətdə bitir.** Faza sonunda bütün testlər keçir, dəyişikliklər `merhele-1` branch-ına commit olunur və `progress.md` yenilənir.
3. **Hər fazadan sonra qısa hesabat verilir.** Növbəti fazaya sizin təsdiqinizlə keçilir.
4. **Testlərin üç növü var** (pytest markerləri):
   - `unit`: xarici asılılıq yoxdur;
   - `integration`: test PostgreSQL və Redis lazımdır;
   - `eval`: real LLM ilə işləyir və əl ilə işə salınır.
5. **`unit` və `integration` testlərində saxta LLM istifadə olunur.** Ona görə bu testlər pulsuzdur və hər dəfə eyni nəticə verir.

## Sizdən lazım olanlar

| Nə | Nə vaxt | Niyə |
|---|---|---|
| Əməkdaş botu: @BotFather-da yeni bot, onun tokeni və username-i | Faza 7-dən əvvəl | Spec iki ayrı bot tələb edir |
| Qurumun şöbələrinin siyahısı və səlahiyyətləri | Faza 5-dən əvvəl. O vaxta qədər pilot üçün nümunə siyahı ilə işləyirik | Yönləndirmə agenti üçün lazımdır |
| Vətəndaş botunun tokenini yeniləmək (`/revoke`) | Real pilotdan əvvəl mütləq | Köhnə token əvvəllər loglarda görünüb |
| Spec-in 3-cü bölməsindəki hüquqi açıq məsələlər | Real pilotdan əvvəl | Telegram kanalının statusu, razılıq mətni, saxlama müddəti, LLM-ə göndərmə |

## Yeni kod quruluşu

```
muraciet-ai/
├── alembic.ini, migrations/           # DB migrasiyaları
├── config/                            # departments, approval_rules, sla, holidays, agents, templates (yaml), consent.md
├── app/
│   ├── config.py, yaml_config.py      # env və YAML konfiqurasiyası
│   ├── manage.py                      # migrate, sync-config, ingest
│   ├── worker.py                      # outbox və jobs növbələrinin işçisi
│   ├── db/                            # modellər, sessiya
│   ├── privacy/                       # crypto.py, masking.py, llm.py (LLM-ə yeganə yol)
│   ├── workflow/                      # states, calendar, authz, machine, approval, suggest, service, sla
│   ├── notifier/                      # telegram_api, outbox, cards
│   ├── agents/                        # base, tooling, 8 agent, prompts/, staff_pipeline, citizen_graph
│   ├── citizen/                       # session, controller
│   ├── staff/                         # session, controller
│   ├── api/                           # deps, bot, internal, admin, web
│   ├── rag/, guardrails/, cache.py    # mövcud, uyğunlaşdırılır
│   └── main.py
├── bot/runner.py                      # ümumi poller: python -m bot.runner citizen|staff
├── eval/datasets/, eval/run_agent_eval.py
├── n8n/                               # connector ilə qurulan workflow-ların ixracı
└── tests/unit/, tests/integration/
```

---

## Faza 0: Hazırlıq

**Məqsəd:** test infrastrukturunu və yeni asılılıqları qurmaq.

| # | Tapşırıq |
|---|---|
| 0.1 | `requirements.txt` faylına `alembic`, `cryptography`, `pyyaml`, `pytest` əlavə etmək. `pyproject.toml`-da pytest konfiqurasiyası yazmaq: `unit`, `integration`, `eval` markerləri və standart olaraq `-m "not eval"` |
| 0.2 | `docker-compose.override.yml` (yalnız inkişaf üçün) yaratmaq: postgres `127.0.0.1:55432`, redis `127.0.0.1:56379`. Məqsəd testlərin hostdan qoşula bilməsidir |
| 0.3 | `tests/fakes.py`: `FakeChatModel`. Bu, ssenari üzrə cavab verən və ona göndərilən bütün mesajları qeyd edən saxta modeldir. `with_structured_output` və `bind_tools` dəstəkləyir |
| 0.4 | Mövcud 6 testi `tests/legacy/` qovluğuna köçürmək. Köhnə kod silinənə qədər (Faza 9) onlar işləməyə davam edir |

**Qəbul:** `pytest` işləyir və köhnə 6 test keçir.

---

## Faza 1: Konfiqurasiya və iş təqvimi

**Məqsəd:** bütün parametrləri koddan ayırmaq və qanuni müddətləri dəqiq hesablamaq.

| # | Tapşırıq | Testlər |
|---|---|---|
| 1.1 | `app/config.py`-a yeni dəyişənlər əlavə etmək: `CITIZEN_BOT_TOKEN`, `CITIZEN_BOT_USERNAME`, `STAFF_BOT_TOKEN`, `STAFF_BOT_USERNAME`, `SERVICE_TOKEN`, `DATA_ENCRYPTION_KEYS`, `HMAC_KEY`, `MIGRATION_DATABASE_URL`, `APP_DB_PASSWORD`, `N8N_DB_PASSWORD`, `RETENTION_YEARS`, `HOTLINE_TEXT`. Köhnə `TELEGRAM_BOT_TOKEN` → `CITIZEN_BOT_TOKEN`. `.env.example` yenilənir | Məcburi dəyişən olmayanda aydın xəta mesajı |
| 1.2 | `app/yaml_config.py` yaratmaq. Hər YAML faylı üçün Pydantic modeli və keşlənən `load_config()` funksiyası olacaq | `general` şöbəsi yoxdursa xəta. `agents.yaml`-da 8 agentin hamısı olmalıdır. İş saatları düzgün olmalıdır |
| 1.3 | Konfiqurasiya fayllarını yaratmaq (siyahı bu cədvəlin altındadır) | 1.2-nin testləri real fayllarla keçir |
| 1.4 | `app/workflow/calendar.py` yaratmaq: `is_workday(d)`, `add_workdays(d, n)`, `work_hours_between(a, b)`, `legal_due_date(received_at)` (15 iş günü), `urgent_deadline(received_at)` (24 saat). Bütün funksiyalar `now` dəyərini parametr kimi alır | Həftə sonu, bayram, il keçidi. İş saatlarının günlər arasında hesabı: cümə 17:30 + 1 iş saatı = bazar ertəsi 09:30 |

**1.3-də yaradılacaq fayllar:**
- `departments.yaml`: pilot üçün nümunə siyahı (`general`, `kommunal`, `sosial`, `huquq`, `abadliq`). Sonra qurumun real siyahısı ilə əvəz olunacaq.
- `approval_rules.yaml`.
- `sla.yaml`: iş saatları 09:00–18:00, bazar ertəsi–cümə. Eskalasiya 4/8 saat, təcili müraciətdə 1/2 saat. Xatırlatmalar 3/1 gün. Avtomatik bağlanma 7 gün. Gözləmə xatırlatması 5/10 gün. Outbox intervalları.
- `holidays.yaml`: 2026–2027 üçün rəsmi siyahı. Rəsmi mənbədən yoxlanılır, mənbə `findings.md`-yə yazılır.
- `agents.yaml`: Haiku 5.5 və Sonnet 5.5 bölgüsü.
- `templates.yaml`: imza, bildiriş mətnləri, m. 7.8 üzrə şikayət qaydası.
- `consent.md`: v1, hüquqşünasın təsdiqini gözləyən layihə mətni.

**Qəbul:** bütün unit testlər keçir.
**Kurs:** #1–6, #12 (Pydantic).

---

## Faza 2: Məlumat modeli, migrasiyalar, şifrələmə

> **Qərar (2026-10-21, istifadəçi):** yeni sxem ayrıca `muraciet_v2` bazasında qurulur. Köhnə cədvəllər silinmir, köhnə bot Faza 9-a qədər işləyir. Keçid addımları Faza 9.1-dədir. `ingest` əmri Faza 5-ə (5.9) keçdi.

**Məqsəd:** yeni sxem, iki DB rolu və sahə səviyyəsində şifrələmə.

| # | Tapşırıq | Testlər |
|---|---|---|
| 2.1 | `app/privacy/crypto.py` yaratmaq. Funksiyalar: `encrypt`, `decrypt`, `blind_index(kind, value)`, `reencrypt`. Şifrələmə MultiFernet ilədir: açarlar `DATA_ENCRYPTION_KEYS`-də vergüllə ayrılır, birincisi əsas açardır. `blind_index` HMAC-SHA256 ilə işləyir, dəyəri əvvəlcə normallaşdırır: FİN böyük hərflərə, telefon `+994…` formatına salınır | Şifrələyib açanda eyni dəyər alınır. Köhnə açarla şifrələnmiş dəyər oxunur və yeni açarla yenidən şifrələnir. HMAC sabitdir, fərqli növlər fərqli heş verir |
| 2.2 | `app/db/models.py` (spec 8-ci bölmə) və `app/workflow/states.py` (enum-lar) yaratmaq. Əlavə olaraq `jobs` cədvəli: triaj və layihə işlərinin növbəsi, server restart olsa da itmir | — |
| 2.3 | Alembic qurmaq: `alembic.ini`, `migrations/env.py` (`MIGRATION_DATABASE_URL`, sahib rolu ilə). Migrasiya `0001_initial`-ın tərkibi bu cədvəlin altındadır | 2.5-də |
| 2.4 | `app/manage.py` yaratmaq. Əmrlər: `migrate` (Alembic, LangGraph checkpointer setup, PGVector cədvəlləri, icazələrin yenilənməsi), `sync-config` (`departments.yaml` → baza), `ingest` (bilik bazası) | — |
| 2.5 | `tests/conftest.py`-a inteqrasiya fixture-ları əlavə etmək: `muraciet_test` bazası (migrasiya ilə qurulur, hər testdən sonra TRUNCATE) və Redis DB 15 | Migrasiya boş bazaya tətbiq olunur. `muraciet_app` `appeal_events`-də UPDATE/DELETE edə bilmir. `muraciet_n8n` `citizens`-i oxuya bilmir, `v_daily_stats`-ı oxuya bilir. Bazada FİN açıq mətn kimi görünmür |

**`0001_initial` migrasiyasının tərkibi:**
- köhnə `appeals`, `citizens`, `chat_logs` cədvəllərini silmək (onlarda yalnız test məlumatı var);
- yeni cədvəlləri yaratmaq;
- indekslər: `appeals(status)`, `appeals(assigned_staff_id, status)`, `outbox(status, next_attempt_at)`, `jobs(status, next_attempt_at)`, `telegram_messages(bot, chat_id, message_id)`;
- `v_daily_stats` görünüşü;
- `muraciet_app` rolu: `appeal_events`-də yalnız SELECT və INSERT edə bilir;
- `muraciet_n8n` rolu: yalnız `v_daily_stats`-da SELECT edə bilir.

**Qəbul:** `python -m app.manage migrate` inkişaf bazasında işləyir və inteqrasiya testləri keçir.
**Kurs:** #9–10.

---

## Faza 3: Workflow

**Məqsəd:** müraciətin həyat dövrünü LLM və Telegram olmadan tam qurmaq və test etmək.

| # | Tapşırıq | Testlər |
|---|---|---|
| 3.1 | `states.py`: `AppealStatus` (8), `Outcome` (4), `Priority` (3), `Category` (4), `StaffRole` (3), `Action` | — |
| 3.2 | `authz.py`: `Actor` (citizen, staff: rol, id, `is_admin`; system) və `authorize(actor, action, appeal)`. İcazə yoxdursa `Forbidden` qaytarır | Rol × əməliyyat × mülkiyyət matrisi (parametrized, hər hüceyrənin gözlənilən nəticəsi cədvəldə) |
| 3.3 | `machine.py`: `TRANSITIONS: dict[(status, action), status]` (spec 7-ci bölmədəki cədvəl), `next_status()`. Keçid olmayanda `InvalidTransition` | Hər icazəli keçid işləyir. Bütün digər (status, action) cütləri qadağandır. Son vəziyyətlərdən çıxış yoxdur |
| 3.4 | `approval.py`: `needs_approval(appeal, outcome, qa_report, actor)` → `(bool, səbəblər)`. Koordinatorun öz cavabı təsdiqə düşmür | Hər qayda ayrıca. Heç bir qayda ödənmədikdə `false` |
| 3.5 | `suggest.py`: şöbədə açıq müraciəti ən az olan aktiv əməkdaş. Bərabərlikdə son təyinatı ən köhnə olan seçilir | Yük, bərabərlik, şöbədə aktiv əməkdaş yoxdur |
| 3.6 | `service.py` — `AppealService`. Metodları bu cədvəlin altındadır | Bu cədvəlin altındadır |

**`AppealService` metodları:**
- `create_appeal`, `apply_triage`, `assign`, `reassign`, `return_to_coordinator`;
- `ask_citizen`, `citizen_reply`, `submit_answer`, `approve`, `send_back`;
- `redirect`, `not_review`, `extend`, `citizen_feedback`, `auto_close`;
- `deactivate_staff`, `reveal_fin`.

**Hər əməliyyat eyni ardıcıllıqla işləyir:**
1. `authorize` icazəni yoxlayır.
2. `next_status` yeni vəziyyəti tapır.
3. `UPDATE … WHERE version = :expected` icra olunur. Sətir dəyişməsə, cari icraçı ilə `StaleAppeal` qaytarılır.
4. `appeal_events` cədvəlinə qeyd yazılır.
5. Bildirişlər `Notifier` interfeysi ilə outbox-a əlavə olunur.

**3.6-nın inteqrasiya testləri:**
- müraciətin tam həyat dövrü;
- iki təyinat eyni vaxtda: ikincisi `StaleAppeal` alır;
- hər əməliyyat audit jurnalına yazılır və düzgün bildiriş hadisəsini `Notifier`-ə ötürür (alıcılar və outbox Faza 4-də test olunur);
- deaktiv edilən əməkdaşın işləri `new` vəziyyətinə qayıdır;
- uzatma 30 iş günündən çox ola bilməz;
- `reveal_fin` `pii_viewed` hadisəsi yaradır;
- son vəziyyətdə heç bir əməliyyat icra olunmur.

**Qəbul:** workflow-un bütün testləri keçir.
**Kurs:** #1–6, #10.

---

## Faza 4: Notifier (outbox, worker, kartlar)

**Məqsəd:** heç bir mesajın itmədiyi zəmanətli çatdırılma.

| # | Tapşırıq | Testlər |
|---|---|---|
| 4.1 | `telegram_api.py` (httpx ilə): `send_message`, `edit_message_text`, `answer_callback_query`. Xəta növləri: `RetryAfter(seconds)`, `Forbidden`, `BadRequest`. "message is not modified" cavabı uğur sayılır. Token loglara yazılmır | httpx `MockTransport` ilə: 200, 429, 403, 400 |
| 4.2 | `outbox.py`: `enqueue(...)` və `process_batch(now)` (`FOR UPDATE SKIP LOCKED`). Ssenarilər bu cədvəlin altındadır | Hər ssenari ayrıca test olunur |
| 4.3 | `cards.py`: koordinator kartı, icraçı kartı, layihə mesajı, önizləmə, təsdiq kartı. Vətəndaş mesajları: qeydiyyat, sual, cavab, uzatma, aidiyyəti üzrə göndərmə, baxılmamış saxlama (şikayət qaydası ilə), status. `callback_data` formatı `kod:müraciət:versiya[:əlavə]`, ölçüsü 64 baytdan çox deyil. Mesaj 4096 simvoldan çox deyil | Maksimal ID-lərlə `callback_data` ≤ 64 bayt. FİN maskalanır. Rədd, qismən və baxılmamış saxlama mətnlərində şikayət qaydası var |
| 4.4 | `refresh_cards(appeal_id)`: vəziyyət dəyişəndə aktiv kartlar `edit_message_text` ilə yenilənir | Təyinatdan sonra hər iki koordinatorun kartı yenilənir |
| 4.5 | `app/worker.py` və docker-compose-da `worker` servisi. Hər saniyə outbox və jobs növbələrini emal edir | — |

**4.2-nin ssenariləri:**
- **uğur:** status `sent` olur və `telegram_messages`-ə qeyd yazılır;
- **429:** mesaj `retry_after` müddəti qədər gözlədilir;
- **403:** mesaj dərhal `dead` olur və koordinatora "çatdırılmadı" bildirişi gedir;
- **digər xətalar:** təkrar cəhdlər 1 dəq, 5 dəq, 15 dəq, 1 saat, 4 saat sonra olur. Hamısı alınmasa, mesaj `dead` olur və bildiriş gedir;
- **restart bərpası:** 10 dəqiqədən çox `sending` vəziyyətində qalan sətir yenidən `pending` olur.

**Qəbul:** worker real Telegram-a test mesajı göndərir (öz çatınıza, əl ilə yoxlama).
**Kurs:** retry və xətaların idarəsi (#15-in prinsipləri, Python-da).

---

## Faza 5: Privacy qatı və 8 agent

**Məqsəd:** hər işi bir agentin gördüyü və şəxsi məlumatı LLM-ə ötürməyən AI qatı.

| # | Tapşırıq | Testlər |
|---|---|---|
| 5.1 | `masking.py` — `Pseudonymizer(known)`: `mask` və `unmask`. Regex ilə tapılanlar: telefon (`+994`, `0XX`, boşluqlu yazılış), FİN, e-poçt, kart. Profil dəyərləri də maskalanır (≥3 simvol, şəkilçili formalar daxil): "Əlinin" → `[AD_1]nin` | Maskalayıb bərpa edəndə orijinal alınır. Şəkilçili formalar tutulur. Eyni dəyərə eyni əvəzedici verilir. Başqa şəxsin nömrəsi də regex ilə maskalanır |
| 5.2 | `privacy/llm.py` — `PrivateLLM(agent_name)`: `structured(messages, schema, pseudo)` və `with_tools(messages, tools, pseudo)`. Girişdə maskalayır, çıxışda bərpa edir: sxemin bütün mətn sahələri və tool arqumentləri. `app.llm`-i import edən yeganə moduldur | Casus model ilə yoxlama: LLM-ə gedən mətndə şəxsi məlumat yoxdur, çıxış bərpa olunur. **Arxitektura testi:** `app/agents/` heç bir LLM provayderini və `app.llm`-i import etmir |
| 5.3 | `app/llm.py`-da `get_chat_model(agent_name)`: provayder və model `config/agents.yaml`-dan götürülür | Naməlum agent adında xəta |
| 5.4 | `agents/base.py` — `Agent`: prompt faylı, çıxış sxemi və `run()`. Cavab sxemə uymasa, xəta mətni ilə bir dəfə təkrar edir, yenə alınmasa `None` qaytarır və çağıran tərəf təhlükəsiz standarta keçir. `agents/tooling.py`: `search_knowledge_base` tool-u (xətada `"XƏTA: ..."` qaytarır) və ReAct icraçısı (ən çoxu 3 tool çağırışı, sonra strukturlaşdırılmış yekun cavab) | Uğur, bir təkrar, `None`. Tool xətası agenti çökdürmür |
| 5.5 | Səkkiz agent. Hər biri üçün `prompts/<agent>.md`, sxem və funksiya yazılır. Siyahı bu cədvəlin altındadır | Hər agent üçün saxta modellə: normal nəticə, təkrar cəhd, təhlükəsiz standart |
| 5.6 | `staff_pipeline.py`: LangGraph-da START-dan Triaj və Yönləndirmə paralel işləyir, sonra birləşmə node-u gəlir | İkisi də işləyir. Biri xəta verəndə digərinin nəticəsi saxlanılır |
| 5.7 | `citizen_graph.py`: supervisor qrafı. Ardıcıllıq və nəticə bu cədvəlin altındadır | Hər niyyətin yolu. Bloklanmış mesaj. LLM düşəndə təhlükəsiz cavab |
| 5.8 | Eval: `eval/datasets/{router,faq,triage,routing,qa}.jsonl`, hər birində ən azı 20 nümunə (translit və rus dili daxil). `eval/run_agent_eval.py --agent <ad>` nəticəni `eval/results/<agent>.json`-a yazır. Real LLM ilə əl ilə işə salınır | — |

**5.5-in agentləri:**
- **guard:** mövcud qaydalar və NeMo.
- **router:** niyyəti təyin edir.
- **faq:** ReAct və bilik bazası axtarışı.
- **intake:** 3 sual limiti kodda tətbiq olunur.
- **triage:** kateqoriya, prioritet, hüquqi məsələ.
- **routing:** naməlum şöbə kodu gələrsə `general` seçilir.
- **drafter:** ReAct və bilik bazası axtarışı.
- **qa:** əvvəlcə deterministik şəxsi məlumat yoxlaması, sonra LLM. Rədd, qismən və baxılmamış saxlama cavablarında `missing_appeal_procedure` kritikdir.

**5.7-nin qrafı:**
- **Ardıcıllıq:** guard → sessiya addımı `intake`-dirsə Qəbul agenti, deyilsə Marşrutlaşdırıcı → müvafiq node.
- **Nəticə:** `CitizenTurn{reply, buttons, effects, next_step, draft}`.
- **Checkpointer:** hər vətəndaş üçün ayrıca thread.
- **Qayda:** qraf heç bir müraciəti dəyişmir, yalnız `effects` qaytarır. Onları kontroller icra edir.

**Qəbul:** unit testlər keçir. Eval real modellərlə bir dəfə işə salınıb, nəticələr `progress.md`-dədir.
**Kurs:** #14, #16, #17–18, #25–26, #27, #30, #32, #33, #37.

---

## Faza 6: Vətəndaş botu

**Məqsəd:** spec-in 6-cı bölməsindəki bütün vətəndaş axını.

| # | Tapşırıq |
|---|---|
| 6.1 | `bot/runner.py`: ümumi poller, `python -m bot.runner citizen|staff`. python-telegram-bot polling edir və hər update-i JSON kimi `POST /bot/{kind}/update`-ə (servis tokeni ilə) göndərir. API xəta verərsə, artan intervalla təkrar cəhd edir ki, update itməsin. Update-lər ardıcıl emal olunur |
| 6.2 | `app/citizen/session.py` (Redis, 24 saat). Addımlar: `idle`, `intake`, `consent`, `contact`, `name`, `fin`, `address`, `confirm_profile`, `confirm`, `feedback_reason`, `choose_appeal`. Müraciət layihəsi də burada saxlanılır. Formda daxil edilən şəxsi məlumatlar Redis-də yox, dərhal şifrələnmiş şəkildə bazaya yazılır |
| 6.3 | `app/citizen/controller.py`. Vəzifələri bu cədvəlin altındadır |
| 6.4 | `POST /bot/citizen/update` endpoint-i. Veb üçün `/api/chat` yalnız suallara cavab verir, müraciət niyyətində Telegram botuna yönləndirir |
| 6.5 | `chat_logs` cədvəlinə yazmaq |

**6.3-də controller-in vəzifələri:**
- mesaj, kontakt və callback-ləri paylamaq;
- forma addımlarını aparmaq və validasiya etmək;
- təsdiqdən sonra `create_appeal` çağırmaq;
- status sorğusuna cavab vermək;
- vətəndaşın reply-ını müraciətə bağlamaq: reply → tək gözləyən müraciət → seçim;
- 👍/👎 və səbəbi qəbul etmək;
- `/start` və `/melumatlarim` əmrləri;
- rate limit;
- bütün cavabları outbox ilə göndərmək, callback-lərə isə dərhal `answer_callback_query` vermək.

**İnteqrasiya testləri** (saxta LLM və hazır update JSON-ları ilə):
- ilk müraciətin tam qeydiyyatı;
- yanlış FİN və təkrar giriş;
- başqasının kontaktı rədd edilir;
- razılıqdan imtina;
- qayıdan vətəndaş profilini təsdiqləyir;
- status yalnız öz müraciətlərini göstərir;
- aydınlaşdırma cavabının üç bağlanma halı;
- 👎 basılanda müraciət yenidən açılır;
- `/melumatlarim` maskalanmış məlumat göstərir;
- LLM düşəndə müraciət qeydiyyatı yenə işləyir.

**Qəbul:** real vətəndaş botu ilə əl ilə yoxlama: sual verəndə cavab gəlir, müraciət qeydiyyatı nömrə verir.
**Kurs:** #12, #13, #27.

---

## Faza 7: Əməkdaş botu

**Məqsəd:** spec-in 7-ci bölməsindəki koordinator və əməkdaş axını.

| # | Tapşırıq |
|---|---|
| 7.1 | `app/staff/session.py` (Redis, 30 dəq): yarımçıq əməliyyatlar |
| 7.2 | `app/staff/controller.py`. Vəzifələri bu cədvəlin altındadır |
| 7.3 | Təyinatdan sonra `draft` işi (`jobs`) yaradılır və layihə mesajı göndərilir |
| 7.4 | `POST /bot/staff/update` endpoint-i |

**7.2-də controller-in vəzifələri:**
- **İdentifikasiya.** Aktiv əməkdaş deyilsə, rədd edilir və cəhd jurnala yazılır.
- **Əmrlər:**
  - `/start inv_<token>`;
  - `/devet`, `/emekdaslar` (admin üçün);
  - `/panel`, `/tap`, `/islerim`.
- **Kart düymələri:**
  - təyin et; başqası (siyahı);
  - aidiyyəti üzrə göndər (orqanın adı ilə);
  - baxılmamış saxla (m. 8.1 səbəbləri və mətn);
  - uzat (gün sayı və səbəb);
  - cavab yaz: nəticə → mətn → QA → önizləmə → göndər və ya təsdiqə;
  - layihəni istifadə et; vətəndaşa sual; koordinatora qaytar;
  - FİN-i göstər; təsdiqlə; geri qaytar.
- **Köhnə düymə** basılanda "köhnəlib" cavabı verilir.
- **Mətnin uzunluğu** 4000 simvoldan çox ola bilməz.

**Testlər:**
- qeydiyyatsız şəxs rədd edilir;
- dəvət linki: etibarlı, vaxtı keçmiş, təkrar istifadə;
- təyinat: icraçı kartı və layihə işi yaradılır;
- cavab kritik QA qeydi ilə təsdiq yoluna düşür;
- təsdiqdən sonra vətəndaşa cavab outbox-a düşür;
- geri qaytarma işləyir;
- köhnə düymə;
- FİN baxışı audit jurnalındadır;
- deaktivasiya;
- `/panel` düzgün rəqəmlər göstərir;
- koordinatorun öz cavabı təsdiqsiz gedir.

**Qəbul:** iki real botla əl ilə tam dövrə keçilir. Vətəndaş müraciət edir, koordinator təyin edir, əməkdaş cavab yazır, vətəndaş cavabı alır.
**Kurs:** #12, #13.

---

## Faza 8: SLA, n8n, hesabat

**Məqsəd:** qanuni müddətlərə avtomatik nəzarət və gündəlik hesabat.

| # | Tapşırıq |
|---|---|
| 8.1 | `app/workflow/sla.py` — `sla_check(now)`. Tərkibi bu cədvəlin altındadır |
| 8.2 | `POST /internal/sla/check` və `POST /internal/notify` (servis tokeni ilə) |
| 8.3 | Connector-un qoşulduğu n8n-in API-yə və bazaya necə çatdığını yoxlamaq (`host.docker.internal` və ya başqa ünvan). Nəticə `findings.md`-yə yazılır |
| 8.4 | n8n connector (MCP) ilə üç workflow qurmaq: SLA monitoru, gündəlik hesabat (Postgres node, `muraciet_n8n` rolu ilə), xəta bildirişi (Error Trigger). Hər biri test icrası ilə yoxlanılır, JSON ixracı `n8n/` qovluğuna saxlanılır |
| 8.5 | Saxlama müddəti təmizliyi: `retention_cleanup(now)`. `RETENTION_YEARS` keçmiş bağlı müraciətlərin şəxsi sahələri silinir, statistika qalır. Parametr təyin olunmayıbsa, iş görmür və xəbərdarlıq loglayır. Gündəlik n8n çağırışı ilə işə salınır (spec 9-cu bölmə) |

**8.1-də `sla_check(now)`:**
- spec-in 11-ci bölməsindəki cədvəli tətbiq edir;
- eyni bildirişi təkrar göndərmir: `appeal_events`-də `sla_notified`, qayda açarı və tarix saxlanılır;
- cavabdan 7 gün sonra müraciəti avtomatik bağlayır;
- vətəndaş cavab vermirsə, 5 iş günündən sonra ona xatırlatma göndərir, 10 iş günündən sonra icraçıya təklif verir;
- təcili müraciətlər üzrə bildirişlər 24/7, qalanları iş saatlarında gedir.

**Testlər:** `sla_check` üçün konkret `now` dəyərləri ilə ssenarilər:
- cədvəlin hər sətri;
- təcili müraciət 24/7;
- təkrar bildirişin qarşısı;
- avtomatik bağlanma.

**Qəbul:**
- n8n-də üç workflow aktivdir və test icraları uğurludur;
- gündəlik hesabat rəhbər rolundakı test hesabına gəlir.

**Kurs:** #7–9, #15, #20.

---

## Faza 9: Keçid, uçdan-uca test, sənədlər

**Məqsəd:** köhnə kodu təmizləmək və bütün sistemi birlikdə təsdiqləmək.

| # | Tapşırıq |
|---|---|
| 9.1 | Köhnə kodu silmək (spec 16-cı bölmə) və keçid: konteynerlər `muraciet_v2`-yə və `APP_DATABASE_URL`-ə keçir, köhnə `muraciet` bazası silinir. Siyahı bu cədvəlin altındadır |
| 9.2 | docker-compose: `api`, `worker`, `citizen-bot`, `staff-bot`, `postgres`, `redis`, `ollama` (n8n barədə qərar 8.3-ə görə verilir). Healthcheck-lər. Docker secrets ilə `docker-compose.prod.yml` |
| 9.3 | Uçdan-uca inteqrasiya testi: spec-in 13-cü bölməsindəki tam ssenari, LLM-siz rejim və privacy testi (casus klient bütün ssenari boyu) |
| 9.4 | `docs/pilot-checklist.md` və yenilənmiş README |
| 9.5 | Bütün testləri işə salmaq, konteynerləri yenidən qurmaq, pilotdan əvvəl əl ilə yoxlama |

**9.1-də:**
- **Silinir:**
  - köhnə qraf və `create_appeal` tool-u;
  - `services/notify.py` və `bot/telegram_bot.py`;
  - köhnə n8n JSON-ları;
  - `PATCH /api/appeals` və MCP `answer_appeal`;
  - `tests/legacy`.
- **Yeni sxemə uyğunlaşdırılır:** admin API və MCP-nin oxuma tool-ları.

**Qəbul:** spec-in 13-cü bölməsindəki bütün testlər keçir və pilot yoxlama siyahısı tamamlanıb.

---

## Risklər

| Risk | Tədbir |
|---|---|
| Telegram `callback_data` 64 bayt limiti | Qısa kodlar istifadə olunur və limit testlə yoxlanılır |
| Update-lər ardıcıl emal olunur, bir vətəndaşın LLM gecikməsi digərlərini gözlədə bilər | Pilot həcmində (dəqiqədə 2 mesajdan az) bu qəbul olunur. Gecikmə loglanır, lazım olarsa Mərhələ 3-də söhbət üzrə paralellik əlavə olunur |
| n8n API-yə çata bilməyə bilər | Faza 8.3-də yoxlanılır. Alternativ: n8n-i compose daxilində işlətmək |
| Bayram siyahısının dəqiqliyi | Siyahı rəsmi mənbədən yoxlanılır və hər il yenilənir |
| LLM xərci | Təsnifat agentləri Haiku-da işləyir. Mərhələ 2-də LangSmith ilə izləniləcək |
