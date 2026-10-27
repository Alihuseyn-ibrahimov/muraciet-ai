# Task Plan: Mərhələ 1, dövlət qurumu üçün müraciət sistemi

## Goal
`muraciet-ai`-ni spec-ə uyğun sistemə çevirmək. Vətəndaş Telegram-da yazır. AI ya bilik bazasından cavab verir, ya da müraciəti koordinatora yönləndirir. Əməkdaş cavabı əməkdaş botunda yazır, bot cavabı vətəndaşa çatdırır. Sistem 8 agent, state machine, privacy qatı, outbox və SLA üzərində qurulur.

- Spec: `docs/superpowers/specs/2026-10-21-dovlet-muraciet-sistemi-design.md`
- Detallı plan: `docs/superpowers/plans/2026-10-21-merhele-1-implementasiya.md`
- Branch: `merhele-1`

## Current Phase
Mərhələ 1 tamamlandı (2026-10-22). Növbəti: pilot yoxlama siyahısı və Mərhələ 2

## Phases

### Phase 0: Hazırlıq
- [x] 0.1 asılılıqlar və pytest markerləri (pytest → requirements-dev.txt)
- [x] 0.2 docker-compose.override.yml (55432 / 56379)
- [x] 0.3 FakeChatModel
- [x] 0.4 köhnə testlər → tests/legacy
- **Status:** complete

### Phase 1: Konfiqurasiya və iş təqvimi
- [x] 1.1 Settings genişlənir (köhnə TELEGRAM_BOT_TOKEN adı Faza 9-a qədər qəbul olunur)
- [x] 1.2 yaml_config.py (states.py Faza 2-dən bura çəkildi)
- [x] 1.3 config/*.yaml və consent.md (2026 bayramları rəsmi mənbədən, 241 iş günü yoxlanıldı)
- [x] 1.4 workflow/calendar.py
- **Status:** complete

### Phase 2: Məlumat modeli, migrasiyalar, şifrələmə
- [x] 2.1 privacy/crypto.py
- [x] 2.2 db/tables.py (yeni metadata, jobs daxil) + db/engine.py
- [x] 2.3 Alembic 0001 (enum-lar, vector, v_daily_stats), ayrıca baza muraciet_v2
- [x] 2.4 manage.py (create-db, migrate, sync-config; ingest Faza 5-ə keçdi) + db/roles.py
- [x] 2.5 inteqrasiya fixture-ları (muraciet_test, ayrıca test rolları)
- **Status:** complete

### Phase 3: Workflow
- [x] 3.1 states (Faza 1-2-də hazır idi; Action və etiketlər machine.py-da)
- [x] 3.2 authz (9 icraçı × 19 əməliyyat matrisi)
- [x] 3.3 machine (29 keçid, qalan bütün cütlər qadağan)
- [x] 3.4 approval
- [x] 3.5 suggest
- [x] 3.6 AppealService (25 inteqrasiya testi + mutasiya yoxlaması)
- **Status:** complete

### Phase 4: Notifier
- [x] 4.1 telegram_api (token sızmasına qarşı test + mutasiya)
- [x] 4.2 outbox (retry/backoff, 429, 403, restart bərpası, SKIP LOCKED)
- [x] 4.3 cards (+ privacy/display.py; m. 7.8 şikayət qaydası avtomatik)
- [x] 4.4 OutboxNotifier + refresh_cards (migrasiya 0002: closed_at)
- [x] 4.5 jobs.py + worker.py + compose `worker` (profile v2); real Telegram test mesajı göndərildi
- **Status:** complete

### Phase 5: Privacy qatı və 8 agent
- [x] 5.1 masking (+ enum bərpası düzəlişi)
- [x] 5.2 PrivateLLM və arxitektura testi (structured → native json_schema)
- [x] 5.3 get_chat_model (agents.yaml-da effort, max_tokens 8192)
- [x] 5.4 base və tooling
- [x] 5.5 8 agent
- [x] 5.6 staff_pipeline
- [x] 5.7 citizen_graph
- [x] 5.8 eval dəstləri (5 × 22–28 hal) və eval/run_agent_eval.py (unit testlər + 6 mutasiya tutuldu)
- [x] 5.8b real modellə eval: router/faq/triage/routing 1.0; qa prompt düzəlişindən sonra exact 0.955
- [x] 5.9 manage.py ingest (v2 bilik bazası; Faza 2-dən köçürüldü)
- [x] 5.10 progress.md qeydi və Faza 5 commit-i
- **Status:** complete

### Phase 6: Vətəndaş botu
- [x] 6.0 nümunə domeni: nazirlik şablonları (bilik bazası, şöbələr, eval yenidən; ictimai əlaqələr maskalanmır)
- [x] 6.1 bot/runner.py (update itirmir, tokeni loga yazmır)
- [x] 6.2 citizen session (Redis; forma məlumatı Redis-ə düşmür)
- [x] 6.3 citizen controller (22 inteqrasiya ssenarisi, 6 mutasiya tutuldu)
- [x] 6.4 endpoint-lər (/bot/citizen/update, /api/chat)
- [x] 6.5 chat_logs (forma və profil ekranları qeyd kimi)
- [x] Qəbul: real botla yoxlama (sual + MR-2026-000001), köhnə bot geri qaldırıldı
- [ ] FAQ keşi → Mərhələ 2; vidcetin rate limiti → Faza 9
- **Status:** complete

### Phase 7: Əməkdaş botu
- [x] 7.1 staff session (ümumi Redis anbarı, 30 dəq)
- [x] 7.2 staff controller (26 inteqrasiya testi, 7/7 mutasiya tutuldu)
- [x] 7.3 draft işi (Faza 3-də təyinatda yaranırdı; layihə düyməsi işləyir)
- [x] 7.4 endpoint /bot/staff/update + manage invite (ilk admin)
- [x] Qəbul: iki real botla tam dövrə (MR-2026-000002: təyinat → AI layihəsi → cavab → 👎 → yenidən açıldı)
- [x] Real yoxlamada tapılan: `[...]` boşluqlu layihə vətəndaşa getdi → servis və önizləmə bloklayır
- **Status:** complete

### Phase 8: SLA, n8n, hesabat
- [x] 8.1 sla.py (spec 11 cədvəli, 10 test, 4 mutasiya → biri əsl xəta tapdı)
- [x] 8.2 /internal/sla/check, /internal/notify (rəhbər yoxdursa adminə)
- [x] 8.3 connector-un n8n-i uzaqdadır (n8n.prdostu.com) → istifadəçi lokal n8n seçdi
- [x] 8.4 4 workflow lokal n8n-də aktiv (SLA, hesabat, xəta, təmizlik); test icraları uğurlu; hesabat və xəta bildirişi Telegram-a çatdı
- [x] 8.5 retention_cleanup + /internal/retention/cleanup (RETENTION_YEARS yoxdursa iş görmür)
- **Status:** complete

### Phase 9: Keçid, uçdan-uca test, sənədlər
- [x] 9.1 köhnə kod silindi (spec 16); admin API və MCP yalnız oxuma; veb-vidcet yeni /api/chat; köhnə `muraciet` bazası silindi (istifadəçinin qərarı, ehtiyatsız)
- [x] 9.2 docker-compose: api, worker, citizen-bot, staff-bot, migrate, n8n; healthcheck; prod secrets
- [x] 9.3 test_e2e: tam dövrə HTTP səviyyəsində + privacy casusu (mutasiya ilə sübut) + LLM-siz rejim
- [x] 9.4 docs/pilot-checklist.md, README
- [x] 9.5 konteynerlərdə real botlarla son əl ilə yoxlama (MR-2026-000003: tam dövrə, 22 mesaj, xəta yoxdur)
- **Status:** complete

## Key Questions
1. Əməkdaş botunun tokeni və username-i nə vaxt hazır olacaq? Faza 7-dən əvvəl lazımdır.
2. Qurumun real şöbələri hansılardır? O vaxta qədər nümunə siyahı istifadə olunur.
3. Connector-un n8n-i API-yə və bazaya necə çatır? Faza 8.3-də yoxlanılacaq.
4. 2026–2027 bayram siyahısı hansı rəsmi mənbədən götürüləcək? Faza 1.3-də müəyyənləşəcək.

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| Spec-in Q1–Q7 qərarları (koordinator, seçmə təsdiq, hibrid data, FİN, state machine, 8 agent, n8n) | Brainstorming-də istifadəçi təsdiqləyib |
| `writing-plans` əvəzinə `planning-with-files` + detallı plan sənədi | `writing-plans` skill-i quraşdırılmayıb |
| `jobs` cədvəli və ayrıca `worker` servisi | Triaj və layihə işləri restart zamanı itməsin. `SKIP LOCKED` ilə bir neçə worker təhlükəsizdir |
| Botlar update-i API-yə ötürən nazik poller-lərdir | Məntiq API-də cəmlənir və HTTP səviyyəsində test olunur |
| Formun şəxsi məlumatları birbaşa bazaya şifrələnmiş yazılır, Redis-də yalnız addım saxlanılır | Redis-də açıq PII olmasın |
| Dev portları 55432 / 56379 | Hostdakı başqa servislərlə toqquşmasın (Ollama-dakı kimi) |
| `count_receipt_day: true`: daxil olduğu iş günü 1-ci gün sayılır | m. 10.5-in ehtiyatlı şərhi, son tarix bir gün erkən çıxır. Hüquqşünas təsdiqləyənə qədər belə qalır |
| Konfiqurasiya səhvdirsə sistem işə düşmür | Səhv parametrlə səssizcə işləməkdənsə dayanmaq daha yaxşıdır |
| Yeni sxem ayrıca bazada (`muraciet_v2`), köhnə baza keçidə qədər toxunulmaz qalır | İstifadəçi seçdi: köhnə bot Faza 9-a qədər işləyir, keçid bir addımda olur |
| Yeni modellər `app/db/tables.py`-da, ayrıca metadata ilə; köhnə `models.py` Faza 9-da silinir | Köhnə kod keçidə qədər işləməyə davam etməlidir |
| DB rolları və icazələr Alembic-də yox, `manage.py migrate`-in idempotent addımındadır | Rollar bütün klaster üçündür, parollar isə mühitə görə dəyişir |
| `StaleAppeal` ORM obyekti yox, snapshot daşıyır | Rollback-dan sonra ORM obyekti etibarsız olur, kart isə cari vəziyyətlə yenilənməlidir |
| Bildiriş hadisələri `Notifier` interfeysindən keçir, alıcılar Faza 4-də | Servis Telegram-dan asılı olmasın, test edilə bilsin |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| Ollama konteyneri 11434 portunu ala bilmədi | 1 | Host port xəritəsi silindi |
| `claude-sonnet-5-5` `temperature` qəbul etmir | 1 | Anthropic üçün temperature ötürülmür |
| MCP 2.x-də `FastMCP` yoxdur | 1 | `MCPServer`-ə keçildi |
| httpx Telegram tokenini loglayırdı | 1 | httpx loqları WARNING səviyyəsinə salındı |
| PowerShell 5.1-də `&&` yoxdur | 1 | Əmrlər ayrı-ayrı verildi |
| Git Bash-da cp1252 ilə Unicode çıxışı | 1 | `PYTHONIOENCODING=utf-8` / `reconfigure` |
| Legacy testlər yeni testlərlə bir prosesdə OperationalError verir | 1 | Standart run-dan çıxarıldı, ayrıca: `pytest -m legacy tests/legacy` |
| `.env` yazıldıqdan sonra print-də cp1252 xətası | 1 | Skript təkrarlanmadı, açarlar dəyərsiz yoxlandı (hər biri 1 dəfə) |
| Outbox `next_attempt_at` DB now() ilə yazılsaydı, testlərdəki saxta saatla uyğunsuzluq yaranardı | 1 | enqueue funksiyaları vaxtı parametr kimi alır |
| notifier.py testdən əvvəl yazıldı (TDD pozuntusu) | 1 | Testlər sonra yazıldı və 3 mutasiya ilə yoxlandı (hamısı tutuldu) |

## Notes
- Faza statusu belə dəyişir: pending → in_progress → complete
- Böyük qərarlardan əvvəl bu fayl yenidən oxunur
- Hər xəta qeyd olunur
- Hər faza testlərin keçməsi və commit ilə bitir

## Mərhələ 2: foto və sənəd əlavələri (22.10.2026, complete)
Dizayn:
- Vətəndaş müraciəti yazarkən (step `intake` və ya `confirm`) foto/sənəd göndərir. API faylı vətəndaş botu ilə
  yükləyir (getFile, ≤ 10 MB, jpeg/png/pdf), Fernet ilə şifrələyib `attachments` cədvəlinə yazır (appeal_id boş,
  sahibi telegram id), sessiyada id-lər saxlanılır (ən çox 5). Başqa vaxt: "əlavəni müraciət yazarkən göndərin".
- Müraciət yaradılanda əlavələr ona bağlanır. LLM əlavələri görmür (yalnız mətn).
- Kartda "📎 Əlavələr: n" və düymə `at`. İcazə: koordinator və təyin olunmuş icraçı (`Action.view_attachments`,
  audit `pii_viewed`). Outbox `send_document` metodu: payload-da yalnız attachment_id, worker faylı açıb əməkdaş
  botu ilə multipart göndərir (fərqli botların file_id-si keçmir).
- Saxlama: bağlanmamış (appeal_id boş) əlavələr 2 gündən sonra, anonimləşdirilən müraciətlərin əlavələri silinir.
Addımlar: [x] cədvəl + miqrasiya + Crypto bytes  [x] Telegram get_file/download/send_document
[x] vətəndaş axını  [x] əməkdaş düyməsi + outbox  [x] saxlama  [x] sənədlər, commit, deploy (779 test)

## Mərhələ 2: kurator agenti (22.10.2026, complete)
9-cu agent: 👍 → `curate` işi → `CuratorAgent` (maskalanmış giriş, deterministik PII qoruması) → `kb_entries`
(pending) → adminlərə kart (`ka`/`kr`) → təsdiq: `KnowledgeBase.add_entry`, rədd: status. `ingest` təsdiqlənmişləri
daxil edir. Miqrasiya 0004. 793 test.

## Mərhələ 3 (22.10.2026)
- [x] Bazanın şifrəli gündəlik ehtiyat nüsxəsi və bərpa sınağı (`backup` servisi)
- [x] Hesabat analitiki agenti (10): həftəlik hesabat, n8n `05_weekly_report`, `/admin/report`, MCP — 809 test
- [x] Real vaxtda operator çatı (`app/livechat.py`, miqrasiya 0005) — 814 test
- [x] MCP-də yazma: yalnız təyinat təklifi, koordinator təsdiqləyir — 836 test · [~] Production deploy: hazırlıq bitdi (`docs/deploy.md`, `scripts/deploy.sh`, `manage preflight`), server gözlənilir · [ ] E-poçt nüsxəsi (hüquqi)
