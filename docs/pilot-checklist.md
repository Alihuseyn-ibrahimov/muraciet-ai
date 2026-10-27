# Pilotdan əvvəl yoxlama siyahısı

Real vətəndaşlarla işə başlamazdan əvvəl bütün bəndlər işarələnməlidir. Hüquqi bəndlər bağlanmayınca
sistem yalnız test məlumatı ilə işləyə bilər.

## 1. Hüquqi (qurumun hüquqşünası)

- [ ] **Telegram rəsmi müraciət kanalı kimi** (Qanunun 6.7 və 7.6-cı maddələri): elektron müraciətin
      qəbulu və cavabın Telegram-la verilməsi qurumun qaydalarına uyğundurmu.
- [ ] **Razılıq mətni** (`config/consent.md`): təsdiqlənib, versiya nömrəsi artırılıb.
- [ ] **Saxlama müddəti** (`RETENTION_YEARS`): dəyər müəyyən edilib və təyin olunub.
- [ ] **Maskalanmış mətnin xarici LLM-ə (Anthropic) getməsi**: şəxsi məlumat əvəzedicilərlə dəyişdirilir,
      amma profildə olmayan başqa şəxsin adı tanınmır (qalıq risk, `findings.md`). Qəbul edilib və ya
      lokal model (`config/agents.yaml`, Ollama/vLLM) seçilib.
- [ ] Müddətin hesablanması: daxil olduğu gün 1-ci gün sayılır (`count_receipt_day: true`, m. 10.5).

## 2. Konfiqurasiya

- [ ] `config/departments.yaml`: qurumun real şöbələri və səlahiyyətləri, `sync-config` edilib.
- [ ] `data/knowledge/`: qurumun rəsmi, aktual sənədləri; `ingest` edilib. Linklər yoxlanılıb
      (ÜDSY video linki və DOST-un Facebook linki şablondan çıxarılmışdı).
- [ ] `config/holidays.yaml`: cari və növbəti ilin bayramları (Nazirlər Kabinetinin qərarı) daxil edilib.
- [ ] `config/templates.yaml`: mətnlər qurumun üslubuna uyğunlaşdırılıb.
- [ ] `config/approval_rules.yaml`: hansı cavabların təsdiqə düşəcəyi razılaşdırılıb.
- [ ] `.env` / `secrets/`: `ORG_NAME`, `HOTLINE_TEXT`, bot adları, `ADMIN_API_KEY` doldurulub.

## 3. Təhlükəsizlik

- [ ] **Vətəndaş botunun tokeni yenilənib** (@BotFather → /revoke): köhnə prototipin logunda görünmüşdü.
- [ ] `DATA_ENCRYPTION_KEYS` və `HMAC_KEY`-in ehtiyat nüsxəsi serverdən kənarda, təhlükəsiz yerdə saxlanılır.
      Açar itsə, şifrələnmiş FİN, telefon və ünvan oxunmaz.
- [ ] Production `docs/deploy.md` üzrə qaldırılıb: `sh scripts/deploy.sh --check` xətasızdır (sirlər, açıq port
  yoxdur), sonra `sh scripts/deploy.sh`.
- [ ] n8n paneli internetə açıq deyil, admin parolu güclüdür.
- [ ] LangSmith: ya `LANGSMITH_TRACING=false`, ya da hüquqi razılıq var. Açıq olanda xaricə yalnız maskalanmış
  LLM çağırışları gedir (`app/observability.py`), qrafın xam vəziyyəti getmir.
- [ ] Bazanın gündəlik ehtiyat nüsxəsi: `backup` servisi işləyir, `secrets/backup_passphrase` serverdən kənarda
  saxlanılır, `./backups/` başqa yerə köçürülür, ayda bir dəfə `backup.sh verify` (README).

## 4. Agentlərin keyfiyyəti (real modellə)

```bash
python -m eval.run_agent_eval --agent all
```

- [ ] Marşrutlaşdırıcı, Triaj, Yönləndirmə: dəqiqlik ≥ 90%.
- [ ] Məlumat agenti: uydurma cavab (hallucination_rate) < 2%.
- [ ] Triaj: prioriteti aşağı qiymətləndirmə (under_prioritized) 0, təcili halların tapılması 100%.
- [ ] Keyfiyyət agenti: m. 7.8 (şikayət qaydası) 100%, qüsursuz cavaba irad ≤ 10%.
- [ ] Kurator: fərdi qərar və bilik bazasında olan məlumat üçün təklif (false_proposal_rate) 0, təklifdə şəxsi
      məlumat (pii_leaks) 0. Pilotda adminin rədd etdiyi təkliflərin payına baxılır.
- [ ] Dəstlərə qurumun real (anonimləşdirilmiş) müraciətlərindən nümunələr əlavə edilib.

## 5. Əl ilə yoxlama (iki real botla)

Vətəndaş botu:
- [ ] `/start` salamlaşma; ümumi sual → mənbəli cavab və 👍 / 📝 düymələri.
- [ ] Bilik bazasında olmayan sual → "rəsmi məlumatım yoxdur" və müraciət təklifi.
- [ ] Translit və rusca sual → Azərbaycan dilində cavab.
- [ ] İlk müraciət: dəqiqləşdirici suallar → razılıq → öz kontaktı (başqasının kontaktı rədd olunur) →
      ad → FİN (yanlış format rədd olunur) → ünvan → təsdiq → MR nömrəsi və son tarix.
- [ ] Növbəti müraciət: yalnız "Məlumatlarınız düzdür?" soruşulur.
- [ ] İş vaxtı "canlı adamla danışmaq istəyirəm" → "💬 Operatorla danış" → koordinatorlara kart → qəbul →
      iki tərəfli yazışma → /bitir; iş vaxtından kənar düymə yoxdur, `/operator` qaynar xətti təklif edir.
- [ ] Cavaba 👍 → bir neçə saniyədən sonra adminə "📚 Bilik bazasına təklif" kartı (şəxsi məlumatsız);
      təsdiqdən sonra eyni sual botda AI tərəfindən cavablandırılır; fərdi qərar olan cavablar üçün təklif gəlmir.
- [ ] Müraciət yazarkən foto və PDF → "📎 qəbul edildi (n/5)", təsdiqdə "📎 Əlavələr: n"; .exe/.docx rədd olunur;
      icraçının kartında "📎 Əlavələri göstər" → fayllar əməkdaş botuna gəlir; başqa icraçıya icazə verilmir.
- [ ] "Müraciətim nə yerdədir?" → yalnız öz müraciətləri; `/melumatlarim` → maskalanmış profil.

Əməkdaş botu:
- [ ] Qeydiyyatsız şəxs rədd olunur; dəvət linki bir dəfə işləyir.
- [ ] Koordinator kartı triajdan sonra gəlir; təyinat → icraçı kartı və AI layihəsi.
- [ ] `[...]` olan layihə göndərilə bilmir; önizləmədə keyfiyyət iradları görünür.
- [ ] Vətəndaşa sual → vətəndaş reply edir → cavab icraçıya çatır.
- [ ] Şikayət üzrə cavab təsdiqə düşür → koordinator təsdiqləyir → vətəndaş rəsmi cavabı alır.
- [ ] Rədd cavabında şikayət qaydası (m. 7.8) avtomatik var.
- [ ] Aidiyyəti üzrə göndərmə (məktubun nömrəsi ilə), baxılmamış saxlama (m. 8.1), müddətin uzadılması:
      vətəndaşa bildirişlər gedir.
- [ ] 👎 → səbəb → müraciət yenidən açılır; 👍 → bağlanır.
- [ ] FİN yalnız düymə ilə, pəncərədə görünür və auditə yazılır.
- [ ] Köhnə kartın düyməsi "köhnəlib" cavabı verir.

n8n:
- [ ] 4 workflow aktivdir; SLA test icrası uğurlu; gündəlik hesabat rəhbərə gəlir; xəta bildirişi adminə gəlir.

## 6. Pilotun ölçüləri (spec, 14-cü bölmə; ilk 4 həftə)

| Meyar | Hədəf | Harada |
|---|---|---|
| AI-nin əməkdaşsız həll etdiyi suallar | ≥ 50% | gündəlik hesabat, `chat_logs.resolved_by_ai` |
| Uydurma cavab (eval) | < 2% | `eval/results/faq.json` |
| Median təyinat vaxtı | < 4 iş saatı | gündəlik hesabat |
| Median cavab vaxtı | < 5 iş günü | gündəlik hesabat |
| Qanuni müddətə əməl | 100% | `/admin/appeals/overdue` |
| Bilik bazasının boşluqları (həftəlik baxılır, tez-tez verilən suallar əlavə edilir) | azalan | `/admin/knowledge-gaps` |
| 👍 payı | ≥ 80% | gündəlik hesabat |
| LLM-ə gedən mətndə şəxsi məlumat | 0 | `test_e2e.py` + həftəlik log nümunəsi |

## 7. Geri qayıtma planı

- Bot dayandırılır: `docker compose stop citizen-bot staff-bot`. Vətəndaşlar "Qaynar xətt"ə yönləndirilir.
- Outbox-da gözləyən mesajlar itmir; bot yenidən qalxanda göndərilir.
- Açıq müraciətlər `/admin/appeals` ilə ixrac edilib adi kargüzarlıq qaydasına ötürülə bilər.
