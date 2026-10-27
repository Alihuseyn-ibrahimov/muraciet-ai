# Dövlət qurumu üçün vətəndaş müraciətləri sistemi: Mərhələ 1 dizaynı

- **Tarix:** 2026-10-21
- **Status:** dizayn təsdiqlənib, implementasiya planı gözlənilir
- **Layihə:** `muraciet-ai`
- **Əhatə:** 4 mərhələdən birincisi, əsas müraciət dövrəsi

---

## 1. Məqsəd

Dövlət qurumuna Telegram vasitəsilə gündə təxminən 100 vətəndaş yazır. Sistem bunları edir:

1. Bilik bazasında cavabı olan suallara AI dərhal, mənbə göstərərək cavab verir.
2. Araşdırma tələb edən məsələlər rəsmi müraciət kimi qeydə alınır və koordinatora ötürülür.
3. Koordinator müraciəti əməkdaşa təyin edir. Əməkdaş araşdırır və cavabı Telegram-da əməkdaş botuna yazır.
4. Bot cavabı vətəndaşa çatdırır, qanuni müddətlərə nəzarət edir və hər addımı audit jurnalına yazır.

### Mərhələlər

| Mərhələ | Məzmun |
|---|---|
| **1. Əsas dövrə (bu sənəd)** | Vətəndaş → AI cavabı və ya müraciət → koordinator → əməkdaş → cavab → vətəndaş. Rollar, SLA, audit |
| 2. Keyfiyyət | Təsdiqlənmiş cavablardan bilik bazası, LangSmith, web scraping, lokal NER |
| 3. İstismar | Rəhbərlik hesabatları, MCP, production deploy, backup, cavabın e-poçt nüsxəsi |
| 4. Genişlənmə | Lokal model, fine-tune, vLLM, səsli müraciətlər, WhatsApp |

Hər mərhələnin öz dizaynı, planı və icrası olur.

---

## 2. Qəbul edilmiş qərarlar

| # | Mövzu | Qərar | Səbəb |
|---|---|---|---|
| Q1 | Müraciət əməkdaşa necə çatır | **Koordinator təyin edir.** AI şöbə və icraçı təklif edir. Koordinatorun əvəzedicisi var. Vaxtında təyin olunmayan müraciət avtomatik rəhbərə eskalasiya olunur | Nəzarət koordinatordadır. Onun darboğaza çevrilmə riskini AI təklifi, əvəzedici və eskalasiya azaldır |
| Q2 | Cavabın təsdiqi | **Seçmə təsdiq.** Həssas cavablar koordinatordan keçir, qalanlarını əməkdaş özü göndərir | Sürət ilə nəzarət arasında balans |
| Q3 | Data və LLM | **Hibrid.** Şəxsi məlumatlar qurumun serverində qalır, bulud LLM yalnız maskalanmış mətn görür | Keyfiyyət yüksəkdir, GPU lazım deyil, şəxsi məlumat xaricə çıxmır |
| Q4 | Vətəndaşın şəxsiyyəti | **Telegram-da təsdiqlənmiş telefon + soyad, ad, ata adı + ünvan + FİN** | Qanunun tələbi (m. 6.3.2) və vətəndaşı qurumun daxili sistemlərində tapmaq |
| Q5 | Uzunmüddətli proses | **A yanaşması:** state machine (PostgreSQL), ayrıca əməkdaş botu, AI üçün LangGraph, taymerlər üçün n8n | Həftələrlə gözləməyə dözümlüdür, audit və hesabat asandır, hər hissə ayrıca test olunur |
| Q6 | AI-nin quruluşu | **Hər iş üçün bir agent (8 agent).** Heç bir agent müraciətin vəziyyətini dəyişə bilmir | Kontekst dar olanda səhv az olur. Hər agent ayrıca test olunur və ona ayrıca model seçilir |
| Q7 | n8n | İnkişaf zamanı n8n connector (MCP) ilə qurulur, production-da qurumun serverində işləyir. **n8n şəxsi məlumat görmür** | Vətəndaş datası n8n-dən keçmir |

**Rədd edilmiş yanaşmalar:**
- **B. Bütün proses LangGraph human-in-the-loop ilə.** Günlərlə dayanmış thread-lər qrafın kodu dəyişəndə qırıla bilər. Hesabat üçün onsuz da ayrıca cədvəl lazım olardı.
- **C. n8n-mərkəzli proses.** Seçmə təsdiq, yenidən təyinat və aydınlaşdırma sualları kimi budaqlar n8n-də qarışır və test etmək çətinləşir.

---

## 3. Hüquqi əsaslar

Mənbə: "Vətəndaşların müraciətləri haqqında" Azərbaycan Respublikasının Qanunu, №1308-IVQ, 30.09.2015 ([migration.gov.az-dakı mətn](https://www.migration.gov.az/content/pdf/11f5298631e03047b059f073cecb0849.pdf)). Qanunun son redaksiyası e-qanun.az-da ayrıca yoxlanmalıdır.

| Maddə | Tələb | Dizaynda harada nəzərə alınıb |
|---|---|---|
| 6.3.2 | Müraciətdə ad, ata adı, soyad və ünvan göstərilməlidir | Qeydiyyat formunda məcburi sahələr |
| 6.4 | 6.3-ün tələblərinə cavab verməyən və heç bir əlaqə məlumatı olmayan müraciət anonim sayılır. Anonim müraciət qəbul edilmir və ona baxılmır | Telefon məcburidir. Form tamamlanmadan müraciət yaradılmır |
| 7.5 | Müraciətə dövlət dilində cavab verilir | Bot həmişə Azərbaycan dilində cavab verir |
| 7.7 | Cavabda araşdırmanın nəticəsi və görülən tədbirlər, yaxud aidiyyəti olan subyekt göstərilməlidir | Keyfiyyət agentinin `missing_measures` yoxlaması |
| 7.8 | Müraciət baxılmamış saxlandıqda, qismən təmin edildikdə və ya rədd edildikdə şikayət qaydası izah edilməlidir | `missing_appeal_procedure` kritik yoxlaması. Bu nəticələrlə verilən cavablar təsdiqə düşür. `not_reviewed` bildirişi şikayət qaydasını avtomatik daxil edir |
| 7.10 | Qurumun səlahiyyətinə aid olmayan müraciət 3 iş günü ərzində aidiyyəti üzrə göndərilir və vətəndaşa bildirilir | `redirected` vəziyyəti, 3 iş günlük SLA |
| 8.1–8.2 | Müraciətin baxılmamış saxlanılma əsasları. Səbəb vətəndaşa 5 iş günü ərzində bildirilir | `not_reviewed` vəziyyəti, 5 iş günlük SLA |
| 10.1 | Baxılma müddəti 15 iş günüdür, əlavə öyrənmə tələb edən müraciətlər üçün 30 iş günü | `due_date` hesablanması |
| 10.2 | Müddət ən çoxu 30 iş günü uzadıla bilər, bu barədə vətəndaşa bildirilir | [⏳ Müddəti uzat] əməliyyatı və avtomatik bildiriş |
| 10.4 | Gecikmə halında məlumat əhəmiyyətini itirəcəksə, müraciətə dərhal, ən gec 24 saat ərzində baxılır | `urgent` prioriteti. SLA yoxlaması 24/7 işləyir |
| 10.5 | Müddət müraciətin daxil olduğu gündən başlayır | `due_date` müraciətin yarandığı tarixdən hesablanır |

### Hüquqi açıq məsələlər

Bu məsələləri qurumun hüquqşünası həll edir:

1. **Telegram vasitəsilə qəbul olunan müraciətin və verilən cavabın rəsmi statusu.** Qanun elektron müraciəti e-poçt və rəsmi sayt üzərindən tanıyır (m. 6.7), elektron müraciətə cavabın da e-poçtla verilməsini nəzərdə tutur (m. 7.6). Lazım olarsa, cavabın rəsmi nüsxəsi e-poçt və ya poçtla da göndərilir (Mərhələ 3).
2. **Fərdi məlumatların emalına razılıq mətni.** Fayl: `config/consent.md`, versiya ilə saxlanılır.
3. **Saxlama müddəti.** Parametr: `RETENTION_YEARS`.
4. **Maskalanmış mətnin xarici LLM provayderinə göndərilməsi.** Bunun fərdi məlumatlar qanunvericiliyinə uyğunluğu təsdiqlənməlidir.

**Real vətəndaşlarla pilot yalnız bu dörd məsələ təsdiqləndikdən sonra başlayır.** O vaxta qədər sistem yalnız test məlumatları ilə işləyir.

---

## 4. Arxitektura

```
 Vətəndaş ─► Vətəndaş botu ─┐               ┌─ Əməkdaş botu ◄─ Koordinator, əməkdaş, rəhbər
                            ▼               ▼
                      ┌──────────── FastAPI ─────────────┐
                      │ Privacy   : maskalama, şifrələmə │
                      │ AI qatı   : 8 agent (LangGraph)  │
                      │ Workflow  : state machine, audit │
                      │ Notifier  : outbox → Telegram    │
                      └───────┬─────────────────┬────────┘
                    PostgreSQL + pgvector     Redis
                              ▲
                 n8n: saatlıq SLA yoxlaması, gündəlik hesabat
```

### Komponentlər

| Komponent | Vəzifəsi | Nədən asılıdır |
|---|---|---|
| Vətəndaş botu (`bot/citizen/`) | Vətəndaşın mesajlarını qəbul edib API-yə ötürür. Qeydiyyat formunu (razılıq, kontakt, ad, FİN, ünvan) sabit kodla aparır | API |
| Əməkdaş botu (`bot/staff/`) | Yalnız aktiv əməkdaşlar üçün kart və düymə interfeysi | API |
| API (FastAPI) | Botlar və n8n üçün yeganə giriş nöqtəsi. Botlar `X-Service-Token`, admin `X-API-Key` ilə daxil olur | Workflow, AI, Privacy, Notifier |
| Workflow (`app/workflow/`) | State machine, `authorize`, audit, iş günü və SLA hesabı | PostgreSQL |
| AI qatı (`app/agents/`) | 8 agent və vətəndaş dialoqunun qrafı (LangGraph) | Privacy wrapper, bilik bazası |
| Privacy (`app/privacy/`) | Maskalama və bərpa (LLM wrapper), sahə səviyyəsində şifrələmə | — |
| Notifier + outbox (`app/notifier/`) | Telegram mesajlarını zəmanətli göndərir və kartları yeniləyir | PostgreSQL, Telegram Bot API |
| n8n | SLA yoxlaması, gündəlik hesabat, xəta bildirişi | API, `v_daily_stats` |
| PostgreSQL + pgvector | Bütün vəziyyət, audit, bilik bazası, LangGraph checkpoint-ləri | — |
| Redis | Rate limit, FAQ keşi, əməkdaşın yarımçıq əməliyyatları | — |

- **Port açılmır.** Botlar polling rejimində işləyir, serverə kənardan port açılmır. API yalnız daxili şəbəkədədir.
- **Mesaj axını.** Botlar API-yə HTTP ilə müraciət edir. Bütün Telegram mesajlarını API outbox vasitəsilə göndərir, hər iki botun tokeni yalnız API-dədir.

### Yük hesabı

- **Mesaj axını:** gündə təxminən 100 vətəndaş 500–800 mesaj deməkdir, pik saatda dəqiqədə 2 mesajdan az.
- **Əməkdaş yükü** (fərziyyə): AI sualların yarısını özü cavablasa, koordinatora gündə 40–50 müraciət düşür. Hər müraciətə orta hesabla 40 dəqiqə sərf olunsa, bu 4–5 əməkdaşın tam iş günüdür.
- **Server:** bu yük üçün bir server (4 vCPU, 8–16 GB RAM) kifayətdir. LLM buludda maskalanmış mətnlə işləyir, qalan hər şey qurumun serverindədir.

---

## 5. Agentlər

### Prinsiplər

1. **Bir agent, bir iş.** Hər agentin öz promptu (`app/agents/prompts/<agent>.md`), öz tool siyahısı, öz Pydantic çıxış sxemi və öz eval dəsti var.
2. **Agentlər bir-birini çağırmır.** Vətəndaş tərəfində onları LangGraph supervisor qrafı, əməkdaş tərəfində workflow hadisələri işə salır.
3. **Agent müraciətin vəziyyətini dəyişə bilməz.** O yalnız təklif verir, qərarı kod və ya insan verir.
4. **Minimum məlumat.** Hər agent yalnız işi üçün lazım olanı görür. Məsələn, Keyfiyyət agenti bütün dialoqu yox, müraciətin xülasəsini və əməkdaşın cavabını alır.
5. **LLM-ə yalnız Privacy wrapper vasitəsilə müraciət olunur** (bax 9-cu bölmə).

### Agentlərin siyahısı

| # | Agent | Nə vaxt işləyir | Yeganə işi | Tool | Çıxış sxemi | Model |
|---|---|---|---|---|---|---|
| 1 | Təhlükəsizlik | Vətəndaşın hər mesajında | Prompt injection, təhqir və mövzudan kənar mesajları bloklamaq | yoxdur (qaydalar + NeMo Guardrails) | `GuardResult{allowed, reason}` | Haiku 5.5 |
| 2 | Marşrutlaşdırıcı | Təhlükəsizlik yoxlamasından sonra | Mesajın niyyətini təyin etmək | yoxdur | `Intent{intent, reason}`. `intent` bunlardan biridir: `question`, `new_appeal`, `status`, `clarification_reply`, `human`, `greeting`, `off_topic` | Haiku 5.5 |
| 3 | Məlumat | `question` niyyətində | Yalnız bilik bazasına əsaslanan cavab vermək | `search_knowledge_base` | `FaqAnswer{answerable, answer, source_ids}` | Sonnet 5.5 |
| 4 | Qəbul | `new_appeal` niyyətində | Problemi strukturlaşdırmaq, ən çoxu 3 dəqiqləşdirici sual vermək | yoxdur | `IntakeResult{complete, next_question, subject, summary}` | Sonnet 5.5 |
| 5 | Triaj | Müraciət yarananda | Kateqoriyanı, prioriteti və hüquqi məsələ olub-olmadığını təyin etmək | yoxdur | `Triage{category, priority, legal_issue, reason}`. `priority`: `normal`, `high` və ya `urgent` | Haiku 5.5 |
| 6 | Yönləndirmə | Müraciət yarananda, Triajla paralel | Şöbəni seçmək | yoxdur (şöbələrin səlahiyyət təsvirləri promptdadır) | `Routing{department_code, reason}` | Haiku 5.5 |
| 7 | Cavab layihəsi | Müraciət təyin olunanda | Rəsmi cavab layihəsi yazmaq | `search_knowledge_base` | `Draft{text, source_ids, missing_info}` | Sonnet 5.5 |
| 8 | Keyfiyyət | Əməkdaş cavabı göndərəndə | Cavabı yoxlamaq (düzəltmir) | `search_knowledge_base` | `QaReport{issues}`. Hər qeyd `{severity, code, message}` formasındadır, `severity`: `info`, `warning` və ya `critical` | Sonnet 5.5 |

**Keyfiyyət agentinin yoxlama kodları:**

| Kod | Nəyi yoxlayır | Ağırlıq |
|---|---|---|
| `unanswered_question` | Vətəndaşın sualı cavabsız qalıb | warning |
| `missing_measures` | Görülən tədbirlər göstərilməyib (m. 7.7) | warning |
| `missing_appeal_procedure` | Rədd, qismən təmin və ya baxılmamış saxlama qərarında şikayət qaydası izah olunmayıb (m. 7.8) | **critical** |
| `pii_other_person` | Cavabda başqa şəxsin şəxsi məlumatı var | **critical** |
| `contradicts_kb` | Cavab bilik bazası ilə ziddiyyət təşkil edir | warning |
| `tone` | Ton uyğun deyil | info |
| `spelling` | Orfoqrafiya səhvi var | info |

**Marşrutlaşdırıcının nəticəsinə görə nə baş verir:**
- `greeting` və `off_topic`: hazır mətnlə cavab verilir.
- `human`: bot qaynar xəttin nömrəsini və müraciət qeydiyyatını təklif edir.
- `status` və `clarification_reply`: LLM-siz, sabit kodla işlənir.

### Agentə verilməyən işlər

Bu işlərdə 100% dəqiqlik lazımdır və LLM-ə ehtiyac yoxdur, ona görə sabit kodla görülür:
- **Şəxsi məlumatların maskalanması.** Maskalamanı LLM etsə, şəxsi məlumatı görməli olardı.
- **Qeydiyyat formu** (razılıq, kontakt, ad, FİN, ünvan).
- **Status sorğusu** və vətəndaşın cavabının əməkdaşa ötürülməsi.
- **İcraçının təklif olunması:** şöbədə açıq müraciəti ən az olan aktiv əməkdaş seçilir.
- **Salamlaşma və menyu.**

### Agent xəta verəndə

Çıxış Pydantic sxeminə uymasa, agentə xəta mətni ilə **bir dəfə** təkrar cəhd verilir. İkinci cəhd də alınmasa, sistem təhlükəsiz standarta keçir:

| Agent | Təhlükəsiz standart |
|---|---|
| Təhlükəsizlik | Yalnız qayda-əsaslı yoxlama (NeMo söndürülür) |
| Marşrutlaşdırıcı | `question` niyyəti. Məlumat agenti cavab tapmasa, müraciət təklif edir |
| Məlumat | "Hazırda cavab verə bilmirəm" mesajı və müraciət təklifi |
| Qəbul | Dəqiqləşdirmə buraxılır, vətəndaşın öz mətni təsvir kimi götürülür |
| Triaj | Kateqoriya `request`, prioritet `normal`, `needs_approval=true`. Kartda "⚠️ AI triajı alınmadı" yazılır |
| Yönləndirmə | `general` (Ümumi) şöbəsi |
| Cavab layihəsi | Layihə olmur. Əməkdaşa "Layihə hazırlana bilmədi" bildirilir |
| Keyfiyyət | Hesabatda "Yoxlama aparılmadı" yazılır və cavab təsdiqə düşür |

**Prinsip:** AI xəta verəndə insan nəzarəti artır, heç vaxt azalmır.

### Model konfiqurasiyası

Hər agentin provayderi və modeli `config/agents.yaml` faylında ayrıca təyin olunur. Model ID-ləri: `claude-haiku-5-5`, `claude-sonnet-5-5`. İstənilən agent ayrıca lokal modelə (Ollama/vLLM) keçirilə bilər, digər agentlər buna görə dəyişmir.

---

## 6. Vətəndaş axını

Bot rus dilində və ə/ı/ş hərflərsiz (translit) yazılan mətnləri başa düşür, amma həmişə Azərbaycan dilində cavab verir (m. 7.5).

### A. Sual: AI cavab verə bilirsə

1. Mesaj Təhlükəsizlik agentindən keçir, Marşrutlaşdırıcı onu `question` kimi tanıyır.
2. Məlumat agenti bilik bazasında axtarış edir və mənbəyə əsaslanan cavab verir. Cavabın altında iki düymə olur: [👍 Kömək etdi] [📝 Müraciət etmək istəyirəm].
3. `answerable=false` olarsa, bot belə yazır: "Bu barədə rəsmi məlumatım yoxdur. Sualınızı müraciət kimi qeydə alım? Əməkdaşlarımız araşdırıb cavab verəcək." Altında [Bəli] [Xeyr] düymələri olur.
4. Şəxsi işlə bağlı suallara ("ərizəm niyə rədd edildi?") Məlumat agenti cavab vermir. Belə suallar müraciətə yönləndirilir.

### B. Müraciət: araşdırma tələb olunursa

1. **Problemin dəqiqləşdirilməsi.** Qəbul agenti yalnız problemlə bağlı ən çoxu 3 sual verir: nə baş verib, harada, nə vaxtdan, nə istəyirsiniz. Sonra qısa xülasə göstərir.
2. **Şəxsi məlumat formu.** Form sabit kodla işləyir, LLM yoxdur. İlk müraciətdə tam doldurulur:
   - **Razılıq:** `config/consent.md` mətni göstərilir: [Razıyam] [Razı deyiləm]. Razı olmayan vətəndaşın müraciəti qeydə alınmır, amma ümumi suallara cavab ala bilir. Razılığın versiyası və tarixi saxlanılır.
   - **Telefon:** [📱 Nömrəmi paylaş] düyməsi. Yalnız `contact.user_id == from_user.id` olan kontakt qəbul olunur. Nömrə `+994XXXXXXXXX` formatına salınır.
   - **Ad:** soyad, ad, ata adı.
   - **FİN:** böyük hərflərə çevrilir və `^[0-9A-Z]{7}$` formatı ilə yoxlanılır.
   - **Ünvan.**
3. **Növbəti müraciətlərdə** tam form doldurulmur. Bot soruşur: "Məlumatlarınız: Əli M., FİN 5AB\*\*\*D, ünvan... Düzdür?" Altında [Bəli] [Yenilə] düymələri olur.
4. **Təsdiq ekranı.** Xülasə və maskalanmış məlumatlar göstərilir: [✅ Göndər] [✏️ Düzəlt].
5. **Nəticə.** Bot belə yazır: "Müraciətiniz qeydə alındı: MR-2026-000123. Ən gec 11.11.2026 tarixinədək cavablandırılacaq. Cavab bu çata gələcək."
   - Nömrə `MR-YYYY-NNNNNN` formatındadır.
   - Müraciət yaranan anda Triaj və Yönləndirmə agentləri paralel işə düşür.

### C. Gözləmə dövrü

- **Status sorğusu.** "Statusum nədir?" sualına bot LLM-siz, birbaşa bazadan cavab verir. Vətəndaş yalnız öz müraciətlərini görür.
- **Əməkdaşın sualı.** Vətəndaş belə mesaj alır: "MR-2026-000123 üzrə əməkdaşımız soruşur: ..." Vətəndaşın cavabı bu qayda ilə müraciətə bağlanır:
  1. Vətəndaş sual mesajına reply edibsə, cavab həmin müraciətə bağlanır.
  2. Reply etməyibsə və `waiting_citizen` vəziyyətində yalnız bir müraciəti varsa, cavab o müraciətə bağlanır.
  3. Belə müraciət bir neçədirsə, bot hansı müraciət olduğunu düymələrlə soruşur.
- **Müddətin uzadılması.** Müddət uzadılarsa, vətəndaşa avtomatik bildiriş gedir (m. 10.2).
- **Öz məlumatları.** `/melumatlarim` əmri maskalanmış profili göstərir və onu yeniləməyə imkan verir.

### D. Cavabın çatdırılması

- **Mesaj.** Vətəndaş belə mesaj alır: "📩 MR-2026-000123 üzrə rəsmi cavab: ... Hörmətlə, {org_name}". Altında iki düymə olur: [👍 Qane oldum] [👎 Qane olmadım].
- **👍 Qane oldum.** Müraciət bağlanır (`closed`).
- **👎 Qane olmadım.** Bot səbəbi soruşur. Müraciət yenidən açılır (`new`, `reopen_count + 1`) və koordinatora qayıdır.
- **Reaksiya olmasa.** 7 təqvim günü ərzində reaksiya gəlməsə, müraciət avtomatik bağlanır.

### E. Kanallar

- **Müraciət qeydiyyatı** yalnız Telegram-dadır, çünki təsdiqlənmiş telefon lazımdır.
- **Veb-vidcet** yalnız ümumi suallar üçündür.

---

## 7. Koordinator və əməkdaş axını

### Rollar və icazələr

| Rol | Nə edə bilər |
|---|---|
| `coordinator` | Müraciəti təyin edir və yenidən təyin edir. Aidiyyəti üzrə göndərir. Baxılmamış saxlayır. Müddəti uzadır. Cavabları təsdiqləyir və ya geri qaytarır. İstənilən müraciətə cavab yaza bilər, onun öz cavabı təsdiqə düşmür. FİN-ə baxa bilər (audit ilə). Əmrləri: `/panel`, `/tap`. `is_admin=true` olarsa, `/devet` və `/emekdaslar` də |
| `staff` | Yalnız ona təyin olunmuş müraciətlərlə işləyir: cavab yazır, vətəndaşa sual verir, müraciəti koordinatora qaytarır, FİN-ə baxır (audit ilə). Əmri: `/islerim` |
| `head` | Eskalasiyaları və gündəlik hesabatı alır. `/panel` əmrinə yalnız oxumaq üçün çıxışı var |

Koordinatorlar (əsas və əvəzedici) eyni hüquqlara malikdir. Hər ikisi bütün yeni müraciətlərin kartını alır. **İcraçı** müraciətin təyin olunduğu əməkdaşdır.

### Əməkdaşın qeydiyyatı

1. Admin `/devet` yazır və rolu, şöbəni düymələrlə seçir.
2. Bot birdəfəlik link yaradır: `t.me/{STAFF_BOT_USERNAME}?start=inv_{token}`. Link 24 saat etibarlıdır, bazada yalnız tokenin heşi saxlanılır.
3. Əməkdaş linki açanda Telegram ID-si ilə qeydə alınır.

Bundan başqa:
- `/emekdaslar` əmri ilə əməkdaşı deaktiv etmək olar. Deaktiv edilən əməkdaşın açıq müraciətləri avtomatik `new` vəziyyətinə qayıdır.
- Qeydiyyatsız şəxs əməkdaş botuna yazsa, "Bu bot yalnız qurum əməkdaşları üçündür" cavabını alır və cəhd jurnala yazılır.

### Müraciətin vəziyyətləri

| Haradan | Əməliyyat | Kim edir | Haraya | Əlavə nəticə |
|---|---|---|---|---|
| `new` | Təyin et | koordinator | `assigned` | İcraçıya kart göndərilir, Cavab layihəsi agenti işə düşür |
| `new` | Aidiyyəti üzrə göndər (orqanın adı ilə) | koordinator | `redirected` | Vətəndaşa bildiriş gedir (m. 7.10). Rəsmi məktubu qurum adi kargüzarlıq qaydası ilə göndərir |
| `new` | Baxılmamış saxla (m. 8.1 üzrə səbəb + mətn) | koordinator | `not_reviewed` | Vətəndaşa səbəb və şikayət qaydası göndərilir (m. 8.2, 7.8) |
| `assigned` | Vətəndaşa sual ver | icraçı | `waiting_citizen` | Sual vətəndaşa göndərilir |
| `waiting_citizen` | Vətəndaş cavab verir | vətəndaş | `assigned` | Cavab icraçıya ötürülür |
| `assigned` | "Mənə aid deyil" (şərhlə) | icraçı | `new` | Koordinatorlara kart göndərilir |
| `assigned` | Cavab göndər, təsdiq lazım deyil | icraçı / koordinator | `answered` | Cavab outbox vasitəsilə vətəndaşa gedir |
| `assigned` | Cavab göndər, təsdiq lazımdır | icraçı | `pending_approval` | Koordinatorlara təsdiq kartı göndərilir |
| `pending_approval` | Təsdiqlə | koordinator | `answered` | Cavab vətəndaşa gedir |
| `pending_approval` | Geri qaytar (şərhlə) | koordinator | `assigned` | Şərh icraçıya göndərilir |
| `answered` | 👍 Qane oldum və ya 7 gün reaksiya yoxdur | vətəndaş / sistem | `closed` | — |
| `answered` | 👎 Qane olmadım (səbəb ilə) | vətəndaş | `new` | `reopen_count + 1` |
| Açıq istənilən vəziyyət | Müddəti uzat (≤30 iş günü, səbəb ilə) | koordinator | dəyişmir | Vətəndaşa bildiriş gedir (m. 10.2) |
| `assigned`, `waiting_citizen` | Yenidən təyin et | koordinator | dəyişmir | İcraçı dəyişir. Köhnə və yeni icraçıya bildiriş gedir, vətəndaşın gözlənilən cavabı yeni icraçıya çatır |

**Qaydalar:**
- Hər keçidi `authorize(icraçı, əməliyyat, müraciət)` yoxlayır və keçid `appeal_events` jurnalına yazılır.
- `closed`, `redirected` və `not_reviewed` son vəziyyətlərdir.
- Müraciət [👎 Qane olmadım] ilə yalnız `answered` vəziyyətindən yenidən açıla bilər.

### Kartlar

**Koordinatorun kartı (yeni müraciət):**
```
📥 MR-2026-000123 · Şikayət · 🔴 Yüksək · son tarix 11.11.2026
Xülasə: Nizami küç. 5 yaxınlığında 3 gündür işıqlar yanmır
Vətəndaş: Məmmədov Əli · FİN 5AB***D · +99450***4567
AI: Kommunal şöbə — "küçə işıqlandırması bu şöbənin səlahiyyətindədir"
Təklif olunan icraçı: Rəşad H. (açıq işləri: 3)
[✅ Rəşad H.-a təyin et] [👥 Başqası] [↪️ Aidiyyəti üzrə] [⏸ Baxılmamış saxla]
```
- Kart Triaj və Yönləndirmə agentləri işini bitirəndən sonra göndərilir. Agentlər xəta veribsə, təhlükəsiz standartla göndərilir. Bu adətən bir neçə saniyə çəkir.
- [👥 Başqası] düyməsi şöbələr üzrə əməkdaşların siyahısını açıq iş sayı ilə göstərir.
- Müraciət yenidən açılıbsa, kartda `reopen_count` göstərilir.

**İcraçının kartı.**
- Kartda müraciətin tam təsviri, vətəndaşın tam adı, telefonu və ünvanı olur.
- FİN maskalanmış göstərilir: `5AB***D` [👁 Göstər]. Hər baxış `appeal_events`-ə `pii_viewed` kimi yazılır.
- Düymələr: [✍️ Cavab yaz] [❓ Vətəndaşa sual] [↩️ Koordinatora qaytar].
- Bir neçə saniyə sonra Cavab layihəsi agentinin layihəsi ayrıca mesajla gəlir: [📋 Layihəni cavab kimi istifadə et].

### Cavabın yazılması

1. İcraçı [✍️ Cavab yaz] basır və nəticəni seçir: [Təmin edildi] [Qismən] [Rədd edildi] [İzahat verildi]. Nəticə `outcome` sahəsində saxlanılır: `satisfied`, `partial`, `rejected`, `explained`.
2. İcraçı cavabın mətnini yazır, ya da layihəni istifadə edir. Keyfiyyət agenti mətni yoxlayır və önizləmə göstərilir:
   ```
   Önizləmə — vətəndaş belə görəcək:
   📩 MR-2026-000123 üzrə rəsmi cavab: ...
   ⚠️ Görülən tədbirlər göstərilməyib (m. 7.7)
   🔴 "Rədd" qərarında şikayət qaydası izah olunmayıb (m. 7.8)
   [📤 Təsdiqə göndər] [✏️ Yenidən yaz]
   ```
3. Təsdiq lazım deyilsə, düymə [📤 Göndər] olur və müraciət `answered` vəziyyətinə keçir. Təsdiq lazımdırsa, müraciət `pending_approval` vəziyyətinə keçir.

### Seçmə təsdiq qaydaları

Qaydalar `config/approval_rules.yaml` faylındadır. Bu şərtlərdən **biri** ödənirsə, cavab koordinator təsdiqinə düşür:
- kateqoriya `complaint`-dir;
- Triaj agenti `legal_issue=true` qeyd edib;
- prioritet `high` və ya `urgent`-dir;
- nəticə `rejected` və ya `partial`-dır;
- Keyfiyyət agenti ən azı bir `critical` qeyd tapıb;
- Triaj və ya Keyfiyyət agenti xəta verib (təhlükəsiz standart).

AI icraçını heç vaxt bloklamır, kritik qeyd sadəcə cavabı insan təsdiqinə yönəldir.

### Eyni vaxtda edilən əməliyyatlar

- **Optimistic locking.** `appeals.version` sahəsi bu məqsədlə istifadə olunur. İki koordinator eyni müraciəti eyni anda təyin etməyə çalışsa, ikincisi "Artıq Rəşad H.-a təyin olunub" cavabını alır.
- **Kartların yenilənməsi.** Vəziyyət dəyişəndə həmin müraciətin bütün aktiv kartları avtomatik yenilənir. Kartlar `telegram_messages` cədvəli vasitəsilə tapılır.
- **Köhnə düymələr.** Hər düymənin `callback_data`-sında müraciətin ID-si və gözlənilən vəziyyəti olur. Köhnə düymə basılsa, bot "Bu əməliyyat artıq köhnəlib" cavabını verir.

### Yarımçıq əməliyyatlar

Əməkdaşın yarımçıq əməliyyatı (məsələn, [✍️ Cavab yaz] basılıb, mətn hələ göndərilməyib) Redis-də 30 dəqiqəlik sessiyada saxlanılır: `staff:{telegram_id}:pending → {action, appeal_id, outcome}`.

---

## 8. Məlumat modeli

PostgreSQL bazasıdır, sxem dəyişiklikləri **Alembic** migrasiyaları ilə idarə olunur. Bazada hazırda real müraciət yoxdur, ona görə köhnə sxem təmiz şəkildə yenisi ilə əvəz olunur.

| Cədvəl | Əsas sahələr |
|---|---|
| `departments` | `code`, `name`, `competence` (Yönləndirmə agenti üçün təsvir), `is_active`. Məlumat `config/departments.yaml` faylından yüklənir, `general` şöbəsi mütləq olmalıdır |
| `staff` | `telegram_user_id` (unikal), `full_name`, `role` (`coordinator`, `staff` və ya `head`), `is_admin`, `department_id`, `is_active` |
| `staff_invites` | `token_hash`, `role`, `department_id`, `created_by`, `expires_at`, `used_at`, `used_by` |
| `citizens` | `telegram_user_id` (unikal), `last_name`, `first_name`, `patronymic`. **Şifrələnmiş sahələr:** `phone_enc`, `fin_enc`, `address_enc`. HMAC heşləri: `phone_hash`, `fin_hash`. Razılıq: `consent_version`, `consent_at` |
| `appeals` | `number`, `citizen_id`, `category`, `priority`, `subject`, `summary`, `description`, `status`, `outcome`, `legal_issue`, `needs_approval`, `triage_failed`, `department_id`, `assigned_staff_id`, `ai_department_reason`, `due_date`, `original_due_date`, `extension_reason`, `redirect_target`, `not_reviewed_reason`, `reopen_count`, `satisfaction`, `version`, `created_at`, `updated_at`, `closed_at` |
| `appeal_messages` | `appeal_id`, `kind`, `author_staff_id`, `text`, `meta` (jsonb), `created_at`. `kind` bunlardan biridir: `staff_question`, `citizen_reply`, `ai_draft`, `qa_report`, `final_answer`, `coordinator_comment` |
| `appeal_events` | `appeal_id`, `actor_type` (`citizen`, `staff`, `system` və ya `ai`), `actor_id`, `action`, `from_status`, `to_status`, `payload` (jsonb), `created_at`. **Bu cədvələ yalnız yazmaq olar:** tətbiqin DB istifadəçisinin UPDATE və DELETE hüququ yoxdur |
| `telegram_messages` | `bot` (`citizen` və ya `staff`), `chat_id`, `message_id`, `appeal_id`, `purpose`. Kartları yeniləmək və reply-ı müraciətə bağlamaq üçündür |
| `outbox` | `bot`, `chat_id`, `method`, `payload`, `status` (`pending`, `sending`, `sent` və ya `dead`), `attempts`, `next_attempt_at`, `last_error`, `appeal_id` |
| `chat_logs` | Vətəndaş botu ilə dialoq. Saxlama müddəti qaydasına tabedir |
| LangGraph checkpoint cədvəlləri | Vətəndaş dialoqunun qısamüddətli yaddaşı |
| `v_daily_stats` (görünüş) | Gündəlik aqreqatlar, şəxsi məlumat yoxdur. n8n-in read-only istifadəçisi yalnız bu görünüşü oxuya bilir |

**Agent yaddaşı (#27):**
- **Qısamüddətli:** LangGraph checkpoint-ləri.
- **Uzunmüddətli:** `citizens` profili.
- **Epizodik:** vətəndaşın əvvəlki müraciətləri.

---

## 9. Təhlükəsizlik və məxfilik

### Şəxsi məlumatların qorunması

- **Sahə səviyyəsində şifrələmə.**
  - FİN, telefon və ünvan Fernet ilə şifrələnir. Açar rotasiyası MultiFernet ilə edilir.
  - Axtarış və təkrarları tapmaq üçün bu sahələrin ayrıca açarla HMAC-SHA256 heşi saxlanılır.
  - Qalan məlumatlar (müraciət mətni, adlar) bazaya giriş nəzarəti, disk və backup şifrələməsi ilə qorunur.
- **LLM sərhədi bir nöqtədən keçir.**
  - Bütün agentlər LLM-ə yalnız `app/privacy/` wrapper-i vasitəsilə müraciət edir.
  - Wrapper mesajları göndərməzdən əvvəl maskalayır, cavabı və tool arqumentlərini isə qayıdanda bərpa edir.
  - Maskalama iki mənbədən istifadə edir. Birincisi, vətəndaşın profilindəki dəqiq dəyərlərdir (ad, FİN, telefon, ünvan). İkincisi, regex ilə tapılan telefon, FİN, e-poçt və kart nömrələridir. Bunlar `[AD_1]`, `[FİN_1]` kimi əvəzedicilərlə dəyişdirilir.
  - Agent modullarının LLM provayder kitabxanalarını birbaşa import etmədiyini test yoxlayır.
- **Qalıq risk.** Vətəndaş mətndə **başqa şəxsin** adını yazsa, regex onu tanımaya bilər. Bu boşluq Mərhələ 2-də lokal NER modeli ilə bağlanır.
- **Saxlama müddəti.** `RETENTION_YEARS` bitəndə şəxsi sahələr silinir, statistika qalır.
- **Silinmə tələbi.** Vətəndaşın silinmə tələbi koordinatora gedir. Rəsmi müraciətlər arxiv qaydalarına tabedir və avtomatik silinmir.

### Giriş nəzarəti

- **Bir funksiya.** Bütün icazələri `authorize(icraçı, əməliyyat, müraciət)` funksiyası yoxlayır. O, rola, müraciətin vəziyyətinə və müraciətin kimə aid olduğuna baxır.
- **Test.** Bütün rol və əməliyyat cütləri test matrisi ilə yoxlanılır.
- **Botlar.** Əməkdaş botu yalnız aktiv əməkdaşlara cavab verir. Vətəndaş botunda heç bir əməkdaş funksiyası yoxdur.
- **Admin REST API.** Mərhələ 1-də yalnız oxumaq üçündür: siyahı, tək müraciət, statistika. Müraciətin vəziyyəti yalnız workflow vasitəsilə dəyişir. Mövcud `PATCH /api/appeals/{number}` və MCP serverin `answer_appeal` tool-u söndürülür, çünki onlar təsdiq qaydalarından yan keçir.

### İnfrastruktur

- **Bot tokenləri.** İki ayrı tokendir. httpx loqları WARNING səviyyəsindədir, ona görə tokenlər loglarda görünmür.
- **Şəbəkə.** Polling rejimidir, serverə kənardan port açılmır. API yalnız daxili şəbəkədədir.
- **Gizli açarlar.** Bot tokenləri, `DATA_ENCRYPTION_KEYS`, `HMAC_KEY`, `SERVICE_TOKEN`, `ADMIN_API_KEY` və LLM açarları production-da Docker secrets ilə ötürülür. İnkişaf mühitində `.env` faylındadır. Heç biri repoya düşmür.

---

## 10. Xətaların idarəsi

| Nə baş verir | Sistem nə edir |
|---|---|
| LLM provayderi cavab vermir | 3 dəfə təkrar cəhd edir (artan intervalla). Alınmasa, vətəndaşa yazır: "Hazırda avtomatik cavab verə bilmirəm" və müraciət yaratmağı təklif edir. Qeydiyyat formu LLM-siz işləyir. Triaj və Yönləndirmə alınmırsa, müraciət `general` şöbəsi ilə və `needs_approval=true` olaraq koordinatora düşür |
| Vektor baza və ya embedding xəta verir | Məlumat agenti söndürülür, vətəndaşa müraciət yaratmaq təklif olunur |
| Telegram mesajı getmir | Outbox 5 dəfə cəhd edir: 1 dəq, 5 dəq, 15 dəq, 1 saat, 4 saat sonra. Alınmasa, status `dead` olur və koordinatora bildiriş gedir |
| Telegram 429 qaytarır | Cavabdakı `retry_after` müddəti qədər gözlənilir |
| Vətəndaş botu bloklayıb (403) | Cəhdlər dərhal dayanır. Koordinatora mesaj gedir: "Cavab çatdırılmadı, vətəndaşla telefonla əlaqə saxlayın" |
| Redis işləmir | Keş və rate limit söndürülür, sistem işləməyə davam edir. Əməkdaş yarımçıq əməliyyatı yenidən başlayır |
| Server yenidən başlayır | `pending` və vaxtı keçmiş `sending` mesajları restartdan sonra göndərilir |
| PostgreSQL işləmir | API 503 qaytarır, botlar "Texniki işlər gedir" yazır. Konteynerlər healthcheck ilə yenidən başladılır |
| Eyni vaxtda dəyişiklik | Optimistic locking işə düşür (7-ci bölmə) |
| Əməkdaş deaktiv edilir | Onun açıq müraciətləri `new` vəziyyətinə qayıdır |

---

## 11. SLA, eskalasiya və hesabat

### Qanuni müddətlər

- **Hesablama qaydası.** Müddətlər iş günü ilə hesablanır, bayramlar `config/holidays.yaml` faylındadır. Müddət müraciətin daxil olduğu gündən başlayır (m. 10.5).
- **Baxılma.** 15 iş günü. Əlavə öyrənmə tələb edən müraciətlər üçün 30 iş günü (m. 10.1). Müddət ən çoxu 30 iş günü uzadıla bilər, bu barədə vətəndaşa bildiriş gedir (m. 10.2).
- **Təcili müraciət.** `urgent` müraciətə 24 saat ərzində baxılır, iş saatı ilə hesablanmır (m. 10.4).
- **Aidiyyəti üzrə göndərmə.** 3 iş günü (m. 7.10).
- **Baxılmamış saxlama barədə bildiriş.** 5 iş günü (m. 8.2).

### Daxili hədəflər və eskalasiya

- **İş saatları** `config/sla.yaml` faylındadır (standart: bazar ertəsi–cümə, 09:00–18:00, bayramlar istisna).
- **n8n yoxlaması** hər saat, 24/7 işləyir. Təcili müraciətlər üzrə xəbərdarlıqlar istənilən vaxt, qalanları yalnız iş saatlarında göndərilir.

| Hadisə | Kimə xəbər gedir |
|---|---|
| Müraciət 4 iş saatı ərzində təyin olunmayıb (`urgent` müraciətdə 1 saat) | koordinator və əvəzedici |
| Müraciət 8 iş saatı ərzində təyin olunmayıb (`urgent` müraciətdə 2 saat) | rəhbər |
| Son tarixə 3 iş günü qalıb | icraçı |
| Son tarixə 1 iş günü qalıb | icraçı və koordinator |
| Müddət keçib | hər gün rəhbərə |
| Vətəndaş əməkdaşın sualını 5 iş günü cavablamayıb | vətəndaşa xatırlatma |
| Vətəndaş əməkdaşın sualını 10 iş günü cavablamayıb | İcraçıya iki yol təklif olunur: mövcud məlumatla cavab vermək və ya m. 8.1.5 üzrə müraciəti baxılmamış saxlamaq |

Daxili hədəf cavab vaxtının medianının 5 iş günündən az olmasıdır. Bu, eskalasiya şərti deyil, pilotun ölçülən göstəricisidir.

### n8n workflow-ları

n8n şəxsi məlumat görmür və Telegram-a birbaşa yazmır, bütün mesajlar API-nin outbox-u ilə gedir.

| Workflow | Nə vaxt işləyir | Nə edir |
|---|---|---|
| SLA monitoru | Hər saat | Servis tokeni ilə `POST /internal/sla/check` çağırır. Xəta olsa, retry edir |
| Gündəlik hesabat | İş günləri 18:00 | Read-only istifadəçi ilə `v_daily_stats`-ı oxuyur, mətni formatlayır, sonra `POST /internal/notify` (hədəf: `head`) çağırır |
| Xəta bildirişi | İstənilən workflow xəta verəndə (n8n error workflow) | `POST /internal/notify` çağırır (hədəf: `is_admin=true` olan əməkdaşlar) |

Gündəlik hesabatda bunlar olur:
- yeni, bağlanan və gecikən müraciətlərin sayı;
- median təyinat və cavab vaxtı;
- AI-nin özü həll etdiyi sualların payı;
- 👍 payı.

---

## 12. Konfiqurasiya

Bütün fayllar git-də versiyalanır, onları dəyişmək üçün kod dəyişikliyi lazım deyil.

| Fayl | Məzmun |
|---|---|
| `config/departments.yaml` | Şöbələr: kod, ad, səlahiyyət təsviri (`general` mütləq olmalıdır) |
| `config/approval_rules.yaml` | Seçmə təsdiq şərtləri |
| `config/sla.yaml` | İş saatları, daxili hədəflər, eskalasiya intervalları, avtomatik bağlanma müddəti (7 gün) |
| `config/holidays.yaml` | Qeyri-iş günləri |
| `config/agents.yaml` | Hər agentin provayderi və modeli |
| `config/templates.yaml` | Cavab imzası, bildiriş mətnləri, şikayət qaydasının mətni (m. 7.8) |
| `config/consent.md` | Razılıq mətni (versiya ilə) |

---

## 13. Testlər

| Səviyyə | Nə yoxlanılır |
|---|---|
| Unit | State machine-in hər icazəli və qadağan keçidi. `authorize` matrisi. Bayramlarla iş günü hesabı. Maskalama (maskalayıb bərpa edəndə orijinal alınmalıdır). Şifrələmə və açar rotasiyası. Seçmə təsdiq qaydaları. Reply-ın müraciətə bağlanma qaydası |
| Agent eval (#33) | Hər agentin `eval/<agent>.jsonl` dəsti, translit və rus dilində nümunələrlə. Marşrutlaşdırıcı, Triaj və Yönləndirmə üçün dəqiqlik. Məlumat agenti üçün mənbəsiz iddia payı. Keyfiyyət agenti üçün m. 7.7 və 7.8 pozuntularını tutma faizi |
| İnteqrasiya | Saxta LLM və real PostgreSQL ilə tam ssenari: sual → müraciət → təyinat → vətəndaşa sual → cavab → seçmə təsdiq → çatdırılma → 👎 → yenidən açılma. LLM-siz rejim də yoxlanılır |
| Privacy | LLM klienti hər mesajı qeyd edən test klienti ilə əvəz olunur. Bütün ssenari boyu heç bir mesajda test vətəndaşının FİN-i, telefonu və ünvanı olmamalıdır |
| Əl ilə yoxlama | Pilotdan əvvəl real botlarla yoxlama siyahısı (`docs/pilot-checklist.md`) keçilir |

Telegram handler-ləri nazikdir. Məntiq API-dədir və HTTP səviyyəsində test olunur.

---

## 14. Pilotun uğur meyarları

Hədəflər ilk 4 həftə üçündür və ilk həftədən sonra dəqiqləşdirilir.

| Meyar | Hədəf |
|---|---|
| AI-nin əməkdaşsız həll etdiyi suallar | ≥ 50% |
| Eval dəstində mənbəsiz (uydurma) cavab | < 2% |
| Median təyinat vaxtı | < 4 iş saatı |
| Median cavab vaxtı | < 5 iş günü |
| Qanuni müddətə əməl | 100% |
| Vətəndaş məmnunluğu (👍 payı) | ≥ 80% |
| LLM-ə gedən mətndə şəxsi məlumat | 0 (privacy testi və log nümunələrinin həftəlik yoxlanışı) |

---

## 15. Əhatə dairəsi

**Mərhələ 1-ə daxildir:**
- 8 agent, vətəndaş botu və əməkdaş botu;
- state machine, audit, outbox, Privacy qatı;
- şifrələmə, SLA, eskalasiya, gündəlik hesabat;
- Alembic və bu sənəddəki bütün testlər.

**Mərhələ 1-ə daxil deyil:**

| Funksiya | Mərhələ |
|---|---|
| Foto və sənəd əlavələri | 2 |
| Təsdiqlənmiş cavablardan bilik bazası (kurator agenti) | 2 |
| LangSmith, lokal NER | 2 |
| Real vaxtda operatorla çat | 3 |
| Cavabın e-poçt / poçt nüsxəsi | 3 (hüquqi rəydən asılıdır) |
| MCP ilə yazma əməliyyatları, Hesabat analitiki agenti | 3 |
| Production deploy, backup avtomatlaşdırması | 3 |
| Səsli müraciətlər, WhatsApp, lokal model, fine-tune | 4 |

---

## 16. Mövcud koddan keçid

| Mövcud | Nə olur |
|---|---|
| `app/agents/tools.py::create_appeal` (ad və telefonu LLM-dən alır) | Silinir. Yerinə Qəbul agenti və sabit form gəlir |
| `app/agents/graph.py` | Yeni supervisor qrafı ilə əvəz olunur (Təhlükəsizlik → Marşrutlaşdırıcı → agentlər) |
| `bot/telegram_bot.py` | `bot/citizen/` və `bot/staff/` olaraq ikiyə bölünür |
| `app/db/models.py` | Yeni sxem yazılır, Alembic əlavə olunur. Test məlumatları silinir |
| `app/services/notify.py` (n8n webhook) | Silinir. Bildirişlər Notifier + outbox ilə gedir |
| `n8n/01_appeal_events.json`, `n8n/02_sla_monitor.json` | 11-ci bölmədəki üç workflow ilə əvəz olunur |
| `PATCH /api/appeals/{number}`, MCP `answer_appeal` | Söndürülür (9-cu bölmə) |
| `app/guardrails/`, `app/rag/`, `app/cache.py`, `app/llm.py` | Saxlanılır və Privacy wrapper ilə birləşdirilir |
| `web/index.html` | Saxlanılır, yalnız ümumi suallar üçündür |
| `mcp_server/`, `eval/`, `finetune/`, `scripts/` | Saxlanılır. Növbəti mərhələlərdə uyğunlaşdırılır |

---

## 17. Kurs mövzularının xəritəsi

| Mərhələ | Dərslər |
|---|---|
| 1. Əsas dövrə | #1–6 Python · #7–9 Docker, n8n, n8n + PostgreSQL · #10 SQLAlchemy · #12 FastAPI, Pydantic · #13 Redis · #14 RAG, yaddaş, tool-calling · #15 n8n retry və error handling · #16 prompt engineering · #17–18 LangChain, LCEL · #25–26 tool dizaynı, structured output · #27 agent yaddaşı · #30 LangGraph checkpointing · #32 supervisor, subgraph · #33 eval · #37 NeMo Guardrails |
| 2. Keyfiyyət | #28 LangSmith · #23 web scraping (rəsmi mənbələrdən bilik bazası) · Bilik bazası kuratoru agenti · lokal NER |
| 3. İstismar | #20 MCP (rəhbərlik üçün Claude Desktop) · #22 deploy (production qurumun serverində, DigitalOcean yalnız test datası ilə demo üçün) · Hesabat analitiki agenti |
| 4. Genişlənmə | #21 Ollama · #34 PyTorch · #35 LoRA/QLoRA (təsdiqlənmiş əməkdaş cavabları ilə) · #38 vLLM serving və benchmark · #24 Whisper, ElevenLabs · #31 WhatsApp |

Layihə dərsləri (#11, #19, #29, #36) bu layihənin özüdür.
