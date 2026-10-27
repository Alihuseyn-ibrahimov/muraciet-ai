# Findings & Decisions

## Requirements
- Dövlət qurumu, gündə ~100 vətəndaş, kanal Telegram.
- AI bilik bazasında cavabı olan suallara özü cavab verir. Araşdırma tələb edən məsələlər müraciət kimi koordinatora gedir.
- Koordinator müraciəti əməkdaşa təyin edir. Əməkdaş cavabı əməkdaş botunda yazır, bot onu vətəndaşa çatdırır.
- Hər işi bir agent görür, cəmi 8 agent.
- Data hibrid rejimdədir: LLM yalnız maskalanmış mətn görür. FİN toplanır və şifrələnir.
- Kurs mövzuları mərhələlər üzrə əhatə olunur (spec 17-ci bölmə).

## Research Findings
- **Qanun:** "Vətəndaşların müraciətləri haqqında" №1308-IVQ (30.09.2015). Mətn migration.gov.az PDF-indən pypdf ilə çıxarılıb.
  - Baxılma müddəti 15 iş günü, əlavə öyrənmə tələb edən müraciətdə 30 iş günü (m. 10.1). Uzatma ≤30 iş günü (m. 10.2).
  - Təcili müraciət 24 saat (m. 10.4). Müddət müraciətin daxil olduğu gündən başlayır (m. 10.5).
  - Aidiyyəti üzrə göndərmə 3 iş günü (m. 7.10). Baxılmamış saxlama barədə bildiriş 5 iş günü (m. 8.2).
  - Məcburi məlumatlar: ad, ata adı, soyad, ünvan (m. 6.3.2). Anonim müraciət qəbul edilmir (m. 6.4).
  - Cavab dövlət dilində verilir (m. 7.5). Cavabda tədbirlər göstərilir (m. 7.7). Rədd, qismən təmin və baxılmamış saxlamada şikayət qaydası izah olunur (m. 7.8).
  - Elektron müraciət e-poçt və ya sayt üzərindən qəbul olunur (m. 6.7), cavab e-poçtla verilir (m. 7.6). Telegram kanalının statusu hüquqi açıq məsələdir.
  - Son redaksiya e-qanun.az-da yoxlanmalıdır.
- **Şikayət qaydası (m. 7.8 şablonu üçün):** qərardan yuxarı dövlət orqanına və məhkəməyə şikayət vermək hüququ var (m. 9.5). Qərarla razılaşmadıqda inzibati və/və ya məhkəmə qaydasında şikayət vermək olar (m. 14.0.8).
- **m. 14.0.6:** vətəndaşın qeydiyyat nömrəsini və daxilolma tarixini, aidiyyəti üzrə göndərildikdə isə məktubun qeydiyyat nömrəsini və göndərilmə tarixini bilmək hüququ var. **Faza 7:** "Aidiyyəti üzrə" əməliyyatı məktub nömrəsini də istəməlidir (istəyə bağlı sahə).
- **Bayramlar 2026 (axtarış nəticəsi, rəsmi sənədlə təsdiqlənməlidir):** Nazirlər Kabinetinin 2 dekabr 2025 tarixli №362 qərarı: Novruz 20–24 mart, Ramazan 20–21 mart, Qurban 27–28 may. Bu günlər Əmək Məcəlləsinin 105-ci maddəsinin 2-ci hissəsinə əsasən iş günü sayılmır. Mənbə: nk.gov.az/uploads/doc/docs/692e89d45e806.pdf. 2026 üçün iş və istirahət günlərinin yerdəyişməsi barədə qərar axtarışda tapılmadı.
  - **№362-nin mətni PDF-dən oxunub təsdiqləndi:** "2026-cı il martın 20, 21, 22, 23, 24-ü Novruz bayramı, martın 20, 21-i Ramazan bayramı, mayın 27, 28-i Qurban bayramı". Ramazan Novruzla üst-üstə düşür, ona görə yerdəyişmələr ehtimal olunur.
  - **Tam siyahının rəsmi mənbəyi:** Əmək və Əhalinin Sosial Müdafiəsi Nazirliyinin təsdiqlədiyi 2026-cı il istehsalat təqvimi (sosial.gov.az, xəbər 8777).
  - **Etibarsız mənbə:** Playroll (üçüncü tərəf) rəsmi qərarla ziddiyyət təşkil edir, istifadə olunmur.
  - **RƏSMİ 2026 siyahısı** (sosial.gov.az, xəbər 8777; təqvim: sosial.gov.az/az/faydali/istehsalat-teqvimi/2026-ci-ilin-istehsalat-teqvimi):
    - Qeyri-iş bayram günləri: 1, 2 yanvar; 20 yanvar (Ümumxalq Hüzn Günü); 8 mart; 20–24 mart (Novruz); 20–21 mart (Ramazan); 9 may; 27–28 may (Qurban); 28 may (Müstəqillik); 15 iyun; 26 iyun; 8 noyabr (Zəfər); 9 noyabr (Bayraq); 31 dekabr.
    - Beşgünlük iş həftəsində keçirilmiş istirahət günləri: 9, 25, 26, 27, 30 mart; 11, 29 may; 10 noyabr.
    - Cəmi 241 iş günü (5 bayramqabağı və 1 hüzn günü ərəfəsi qısaldılmış gün daxil). Qısaldılmış günlər SLA hesabına təsir etmir, nəzərə alınmır.
  - **2027:** Ramazan, Qurban və yerdəyişmələr hələ elan olunmayıb. `holidays.yaml`-da yalnız 2026 tam sayılır (`complete_years`). 2027-də bayramlar çatışmırsa, son tarix daha **erkən** hesablanır. Bu, qanun baxımından təhlükəsiz istiqamətdir, sistem həm də xəbərdarlıq loglayır.
- **Planda boşluq:** spec 9-cu bölmədəki `RETENTION_YEARS` təmizliyi (şəxsi sahələrin silinməsi) planda tapşırıq kimi yox idi. Faza 8-ə 8.5 kimi əlavə olundu.
- **Windows və slim image-də IANA vaxt zonası bazası yoxdur.** `zoneinfo("Asia/Baku")` işləsin deyə `tzdata` paketi lazımdır.
- **Fərdi məlumatlar qanunu:** axtarışda data lokalizasiyası barədə konkret maddə tapılmadı. Bu məsələ hüquqşünasa ötürülüb.
- **n8n connector (MCP):** istifadəçinin mövcud n8n-inə qoşulub. Orada "TV Klip" və "TV Sujet İzləmə" workflow-ları var, team projects aktiv deyil. Bu, muraciet-ai compose-dakı n8n deyil.
- **Quraşdırılmış versiyalar** (.venv, 2026-10-21): langgraph 1.2.16, langchain-core 1.6.12, langchain-anthropic 1.7.6, langchain-postgres 0.0.19, mcp 2.3.0, fastapi 0.151. Docker image-də: fastapi 0.156.4, python-telegram-bot 22.8, sqlalchemy 2.1.5.
- **API davranışları:**
  - `claude-sonnet-5-5` `temperature` parametrini qəbul etmir.
  - MCP 2.x-də `FastMCP` adı `MCPServer` oldu (`mcp.server.mcpserver`).
- **Mühit:**
  - Hostda Windows Ollama işləyir və 11434 portunu tutur.
  - Qlobal git `user.name` dəyəri "Adınız"-dır. Commit-lərdə `-c user.name="Alihuseyn Ibrahimov"` istifadə olunur.

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| `callback_data` formatı `kod:müraciət:versiya[:əlavə]` | Telegram limiti 64 baytdır. Versiya köhnə düyməni tanımaq üçün lazımdır |
| Redis sessiyaları: vətəndaş üçün 24 saat, əməkdaş üçün 30 dəq | Spec 7-ci bölmə. Formun PII-si Redis-də saxlanılmır |
| Append-only audit DB rolu ilə təmin olunur (`muraciet_app`) | Spec 8-ci bölmə |
| n8n-in read-only rolu (`muraciet_n8n`) yalnız `v_daily_stats`-ı oxuya bilir | Spec 11-ci bölmə |
| Bayram siyahısı rəsmi mənbədən (Faza 1.3) | Uydurulmamalıdır |
| İnkişaf bazası `muraciet_v2`, test bazası `muraciet_test`, rollar: `muraciet_app`/`muraciet_n8n` (dev), `muraciet_test_app`/`muraciet_test_n8n` (test) | Rollar klaster üçün ortaqdır, testlər dev parollarına toxunmamalıdır |
| `v_daily_stats`-da vaxt zonası `'Asia/Baku'` birbaşa yazılıb | Görünüş DB səviyyəsindədir. sla.yaml-da zona dəyişərsə, yeni migrasiya lazımdır |
| Alembic `include_object` xarici cədvəlləri nəzərə almır | Əks halda autogenerate checkpoint və vektor cədvəllərini silərdi |

### Faza 3: spec cədvəli koda çevrilərkən dəqiqləşdirmələr
| Boşluq | Qərar |
|---|---|
| Spec-də `waiting_citizen`-dən yalnız "vətəndaş cavab verir" keçidi var. SLA isə 10 gündən sonra icraçıya "mövcud məlumatla cavab ver və ya m. 8.1.5 üzrə baxılmamış saxla" təklif edir | `waiting_citizen`-dən cavab göndərmək, təsdiq istəmək, ikinci sual vermək və koordinatora qaytarmaq mümkündür. 8.1.5 üçün icraçı müraciəti qaytarır, koordinator `not_review` edir |
| Vətəndaş gözləmə vəziyyətindən kənarda da əlavə məlumat göndərə bilər | `citizen_reply` bütün açıq vəziyyətlərdə qəbul olunur. Status yalnız `waiting_citizen` → `assigned` keçidində dəyişir, digər hallarda mesaj saxlanılır və icraçıya (yoxdursa koordinatorlara) ötürülür |
| "Cavab göndər" iki fərqli vəziyyətə apara bilər | Machine-də iki ayrıca əməliyyatdır: `send_answer` (→ answered) və `request_approval` (→ pending_approval). Hansının seçiləcəyini servis təsdiq qaydalarına görə həll edir |
| Deaktiv edilən əməkdaşın `pending_approval` müraciəti | Cavab artıq yazılıb, ona görə müraciət `pending_approval`-da qalır. Koordinator onu geri qaytararsa və icraçı deaktivdirsə, müraciət `new`-a düşür |
| Bir neçə uzatma | m. 10.2: bütün uzatmaların cəmi ilkin son tarixdən sonra ≤30 iş günü ola bilər |
| 👎 ilə yenidən açılma | İcraçı silinir, müraciət koordinatorun növbəsinə qayıdır |
| Anonim müraciət (m. 6.3.2, 6.4) | Servis vətəndaşın ad, ata adı, soyad, ünvan, telefon, FİN və razılığı tam deyilsə müraciət yaratmır. Bu qoruma botdan asılı deyil |
| Rədd və qismən təmin cavablarında şikayət qaydası (m. 7.8) | Faza 4-də rəsmi cavab mətninə avtomatik əlavə olunur (QA-dan əlavə təhlükəsizlik toru) |
| Bildirişlərin alıcıları | Faza 3 hadisələri `Notifier` interfeysinə ötürür. Alıcılar və mətn Faza 4-də (outbox) müəyyən olunur |

### Faza 4: dizayn qərarları
| Məsələ | Qərar |
|---|---|
| Kart düymələri müraciətin versiyasını daşıyır, hər əməliyyat isə versiyanı artırır | `refresh_cards` hər hadisədən sonra hələ aktiv olan kartları yeni versiya ilə yeniləyir, aktivliyini itirənləri bağlayır (düymələr silinir). Bağlanmış kart bir daha yenilənmir: `telegram_messages.closed_at` (migrasiya 0002) |
| Göndərmə zəmanəti | Outbox-da ən azı bir dəfə çatdırılma (at-least-once). Worker mesajı göndərib statusu yaza bilməzdən əvvəl çöksə, mesaj nadir hallarda təkrar gedə bilər. Bu, mesajın itməsindən daha yaxşıdır |
| Mesajların sırası | Növbə id sırası ilə emal olunur. Təkrar cəhd gözləyən mesaj sonrakıları bloklamır. Pilot üçün qəbul olunur |
| 429 (rate limit) | Telegram-ın göstərdiyi qədər gözlənilir. Bu, təkrar cəhd limitindən sayılmır |
| Uzun rəsmi cavab (>4096) | Kəsilmir, paraqraf sərhədində bir neçə mesaja bölünür. Rəy düymələri son hissədədir |
| Format | Mətn parse_mode olmadan, adi mətn kimi göndərilir. Vətəndaşın və əməkdaşın mətnində Markdown/HTML simvolları sınmasın deyə |

### Faza 5: dizayn qərarları
| Məsələ | Qərar |
|---|---|
| Məlumat agentində ReAct hər cavab üçün 2–4 LLM çağırışı deməkdir, vətəndaş isə cavabı tez gözləyir | Axtarış əvvəlcə sabit kodla (tool funksiyası) edilir, sonra tək strukturlaşdırılmış çağırış gəlir. Mənbə tapılmasa LLM ümumiyyətlə çağırılmır. `answerable=true` olub mənbə göstərilməyibsə cavab qəbul olunmur (uydurmanın qarşısı) |
| ReAct (#26) harada | Cavab layihəsi agentində (əməkdaş tərəfi, asinxron iş): model ən çoxu 3 axtarış edir. Yekun strukturlaşdırılmış çağırış tool bloklarını deyil, toplanmış dəlillərin mətnini alır. Anthropic API-də məcburi sxem tool-u ilə köhnə tool bloklarının qarışması riskindən qaçmaq üçün |
| Şəxsi məlumat LangGraph checkpoint-ində | Checkpoint bazada şifrəsiz saxlanılır. Şifrəsi açılmış ad/FİN/telefon/ünvan qrafa `config["configurable"]["pseudo"]` (Pseudonymizer obyekti) ilə ötürülür, vəziyyətə yazılmır |
| NeMo Guardrails LLM-i özü çağırır | NeMo `PrivateLLM` daxilində maskalanmış mətnlə işə salınır. Quraşdırılmayıbsa və ya xəta verərsə, qayda əsaslı yoxlama işləyir |
| Embeddings hostdan | Konteynerdəki Ollama (bge-m3) yalnız inkişaf üçün 127.0.0.1:11435-ə açılır. `EMBEDDINGS_URL`: hostda 127.0.0.1:11435, konteynerdə Faza 9-da `http://ollama:11434` (APP_DATABASE_URL ilə eyni yanaşma) |
| Translit | Profil dəyərləri maskalanarkən ə/e, ı/i, ö/o, ü/u, ş/sh/s, ç/ch/c, ğ/g variantları da tutulur ("Memmedov" = "Məmmədov") |
| **LangGraph `config["configurable"]`-dəki sadə dəyərləri (sətir, rəqəm) checkpoint metadata-sına yazır** (mutasiya ilə yoxlanılıb) | Şəxsi məlumat heç vaxt config-ə sadə dəyər kimi qoyulmur, yalnız `Pseudonymizer` obyekti daxilində ötürülür (obyektlər yazılmır). `test_checkpoint_privacy` checkpoint cədvəllərinin xam baytlarını yoxlayır |
| Azərbaycan kiçik hərfi "I"-ni "ı" edir və ingiliscə "Ignore..." qaydasından yayınır (test tutdu) | Təhlükəsizlik qaydaları mətnin həm `az_lower`, həm də standart `lower` formasında yoxlanılır |
| NeMo rails-in keşi model obyekti ilə idi. Pydantic obyekti hash olunmur, ona görə NeMo heç vaxt işləməzdi | Keş agentin adına görədir |
| **Claude Sonnet 5.5 məcburi `tool_choice` (`any`/`tool`) göndərən sorğunu 400 ilə rədd edir** (Anthropic sənədi, Sonnet 5.5 miqrasiyası). LangChain-in `with_structured_output` standartı məcburi tool çağırışıdır; langchain-anthropic 1.7.6 bu modeldə məcburiyyəti götürür, model tool-u çağırmasa `OutputParserException` atır | `PrivateLLM.structured` Claude-un native structured outputs funksiyasından istifadə edir (`method="json_schema"`, `output_config.format`): JSON sxemə uyğunluğu zəmanətlidir, thinking ilə işləyir. Haiku 5.5 məcburi seçimi qəbul edir, amma onda düşünmür |
| **Haiku 5.5 və Sonnet 5.5-də thinking defolt olaraq açıqdır və `max_tokens`-ə daxildir** (Haiku standart effort `medium`, Sonnet `high`). Bizdə `max_tokens=2048` idi: düşünmə cavabı kəsə bilərdi | `config/agents.yaml`-da hər agentə açıq `effort` (təsnifat, FAQ, qəbul: `low`; cavab layihəsi və keyfiyyət: `medium`), `max_tokens=8192` (ödəniş yalnız istifadə olunan tokenlər üçündür). Eval ilə yoxlanılır |
| Thinking blokları söhbətə bağlıdır: əvvəlki mesajlar dəyişərsə, geri göndərilən blok 31.08.2026-dan sonra yaradılmış hesablarda 400 verir | Cavab layihəsi agentinin ReAct dövrəsi modelin cavabını (thinking blokları ilə) geri göndərir. Maskalama → bərpa → yenidən maskalama eyni baytları verməlidir; model profildə olmayan nömrə yazsa, yenidən maskalama onu dəyişər. **Faza 7-də real API ilə smoke test** |
| Model qiymətləri (Anthropic sənədi, 06.10.2026): Haiku 5.5 — $0.10 / $0.50, Sonnet 5.5 — $2 / $10 (1M giriş/çıxış tokeni) | Eval xərci təxminən 1 dollardan azdır |
| Kiçik bilik bazasında məsafə həddi cavabı olmayan sualları ayırmır: bazada olmayan suallar da 0.49–0.64 məsafədə "uyğunluq" tapır, cavabı olan "Qurumun ünvanı haradır?" isə 0.643-dədir (hədd 0.65) | Uydurmaya qarşı əsas qoruma LLM-in `answerable` qərarı və mənbəyə istinad yoxlamasıdır. Eval-da `hallucination_rate` ölçülür. Real bilik bazası ilə hədd yenidən seçilməlidir |
| `Pseudonymizer.unmask_obj` StrEnum sahələrini adi sətrə çevirirdi (StrEnum həm də `str`-dir): triaj nəticəsində `category.value` işləmirdi. Eval skripti tutdu | Enum dəyərləri olduğu kimi qaytarılır (sabit lüğətdir, şəxsi məlumat daşımır). Test: `test_unmask_keeps_enum_fields_as_enums` |
| LangSmith (#28) izləməsi açılsa, vətəndaş qrafının vəziyyəti (xam mətn, şəxsi məlumat) LangSmith-ə gedər. Hazırda `LANGSMITH_TRACING=false` | Faza 6: istehsalda izləmə söndürülmüş qalmalı və ya yalnız maskalanmış giriş/çıxış göndərilməlidir. Mərhələ 2-də (#28) həll olunur |

### Nümunə domeni: sosial müdafiə nazirliyi (2026-10-21, istifadəçinin qərarı)
| Məsələ | Qərar |
|---|---|
| İstifadəçi nazirliyin sosial şəbəkə cavab şablonlarını göndərdi ("Vətəndaş müraciəti tiktok.docx", ~30 sual-cavab, 11 mövzu) | Bələdiyyə demosu silindi. Bilik bazası 9 sənəd (`data/knowledge/01–09`), hər sual `##` bölməsidir (bölücü sualı cavabdan ayırmır). Şöbələr mövzular üzrə nümunədir: pensiya, udsy, elillik, mesgulluq, sosial_xidmet, menzil, emek, huquq, general |
| Şablonlarda qurumların rəsmi telefon və e-poçtları var. Maskalama onları şəxsi məlumat sayır, keyfiyyət agenti belə cavaba **kritik** `pii_other_person` yazardı | `Pseudonymizer(public=...)`: bilik bazasında olan telefon, e-poçt və s. dəyərlər standart olaraq ictimaidir (maskalanmır, "başqa şəxsin məlumatı" sayılmır). Test: `test_public_contacts_are_neither_masked_nor_foreign` |
| Sənəddə şübhəli yerlər | ÜDSY video linki pozuqdur (`youtu.be/zQ5_sXpHTqM` + artıq simvollar), bazaya salınmadı. DOST-un Facebook linki çox uzun kodlanmış URL-dir, salınmadı. "Dövlət Tibbi Sığorta və Ekspertiza Agentliyi (DTSEA)": istifadəçi adın və qısaltmanın düzgün olduğunu təsdiqlədi, link `dtsera` olaraq qalır. Kiçik orfoqrafik düzəlişlər: hazırki → hazırkı, sürəti → surəti, güncündə → küncündə, Qanunununa → Qanununa |
| Nazirlik sahəsində suallar şəxsi formada verilir ("Mənə sosial yardım düşür?"), amma cavabı ümumi qaydadır | Router promptu: ümumi qayda soruşulursa `question`, əməkdaşın araşdırması və ya vətəndaşın öz sənədlərinə baxmaq lazımdırsa `new_appeal` |
| Triaj ödəniş gecikməsini, evdən çıxarılmanı "hüquqi məsələ" sayırdı (legal dəqiqliyi 0.70). Hüquqi məsələ rəhbər təsdiqi tələb edir | `legal_issue` yalnız qərarın qanuniliyi ilə mübahisə, məhkəmə və hüquqi izahat üçün. Həssas qrupa aid olmaq özlüyündə `high` deyil. Bu dəqiqləşdirmə eval dəstinə baxılaraq edildi, nəticə (1.0) optimistdir |
| Eval (yeni dəstlər) | router 1.0, faq 1.0 (uydurma 0), routing 1.0, qa 1.0 (yeni dəstdə; əvvəlki prompt düzəlişi ümumiləşdi), triaj düzəlişdən sonra 1.0 |

### Faza 6: dizayn qərarları
| Məsələ | Qərar |
|---|---|
| Spec: "botların tokeni yalnız API-dədir". Amma polling üçün runner-in özü tokenlə `getUpdates` çağırmalıdır | Runner tokeni yalnız `getUpdates` üçün istifadə edir, mesaj göndərmir (hamısı API outbox-u ilə). httpx logları söndürülüb, xətalardan yalnız növü yazılır (test) |
| Runner update-i API-yə çatdıra bilməsə | Offset irəliləmir, artan intervalla təkrar (1→60 s). API təkrar emaldan qorunur: update_id Redis-də 24 saat (SET NX). Emal xəta ilə bitərsə, id unudulur |
| Pozuq update bütün vətəndaşlar üçün botu dayandıra bilər (runner ardıcıldır) | Baza/Redis əlçatmazlığı → 503 (runner təkrar göndərir). Digər xəta → loga yazılır, update atılır (200, ok=false) |
| chat_logs-da şəxsi məlumat | Forma addımlarında vətəndaşın yazdığının yerinə "[forma: fin]" kimi qeyd. Botun profil göstərən mesajları da qeyd kimi ("[profilin təsdiqi]"). Test chat_logs-da FİN, ünvan, ad, telefon olmadığını yoxlayır |
| "Müraciət etmək istəyirəm" düyməsi | Vətəndaşın son sualından yeni qəbul dialoqu başlayır (`step="intake_new"`: marşrutlaşdırıcı keçilir, köhnə yarımçıq dialoq istifadə olunmur) |
| Razılıq mətni Markdown-dadır, bot isə adi mətn göndərir | `**` göndərilməzdən əvvəl silinir |
| Profil ekranında nə göstərilir | Ad tam (vətəndaş yoxlamalıdır), telefon və FİN maskalı, ünvan tam (öz çatında, yoxlamaq üçün) |
| FAQ keşi (Redis) və veb-vidcetin rate limiti | Vidcetin limiti həll edildi: IP-yə görə dəqiqədə RATE_LIMIT_PER_MINUTE (Redis, `web` prefiksi); reverse proxy arxasında X-Forwarded-For etibarlı qurulmalıdır. FAQ keşi Faza 6-da edilmədi. FAQ keşi: maskalanmış sualda şəxsi məlumat yoxdursa keşlənə bilər (Mərhələ 2, xərc azaltmaq üçün). Vidcetin limiti: Faza 9-da reverse proxy-də |

### Faza 7: dizayn qərarları
| Məsələ | Qərar |
|---|---|
| Yeni bazada əməkdaş yoxdur, `/devet` isə admin tələb edir | İlk admin terminalda: `python -m app.manage invite coordinator --admin` (birdəfəlik, 24 saatlıq link). Bot və terminal eyni `app/staff/invites.py`-ı işlədir |
| FİN-in göstərilməsi | Mesajla yox, callback xəbərdarlığı (alert) ilə: çat tarixçəsində və outbox cədvəlində qalmır. Hər baxış `pii_viewed` auditidir (test) |
| Mətn tələb edən düymələr (cavab, sual, şərh, orqan, səbəb) | İcazə, keçid və versiya düymə basılan anda yoxlanılır ki, əməkdaş mətni yazıb sonra rədd almasın. Son servis çağırışı yenə `expected_version` ilə gedir |
| Baxılmamış saxlama səbəbləri | Qanunun 8.1.1–8.1.6 bəndləri düymə kimi. Vətəndaşa gedən mətn: bəndin adı + əməkdaşın izahı |
| Aidiyyəti üzrə göndərmə | Orqanın adı, sonra məktubun nömrəsi və tarixi (m. 14.0.6) və ya "⏭ Keç" |
| Mutasiya testi iki boşluq tapdı | 1) çoxaddımlı düymənin köhnə versiyası yoxlanmırdı; 2) admin olmayan şəxs saxta düymə ilə dəvət yarada bilərdi (deaktivasiyanı servis ayrıca qoruyurdu, dəvəti yox). Hər ikisi üçün test əlavə olundu |
| **Real yoxlamada: AI layihəsi `[...]` boşluqları ilə vətəndaşa getdi.** Koordinator layihəni olduğu kimi göndərdi (koordinator cavabı təsdiqsizdir), keyfiyyət agenti də bunu tutmadı | Deterministik qayda: `[...]`/`[…]` olan cavabı `AppealService.submit_answer` heç bir yolla qəbul etmir (təsdiq yolu daxil), önizləmədə 🔴 xəbərdarlıq var və "Göndər" düyməsi yoxdur. LLM-ə etibar edilmir |
| Layihənin sonundakı "Hörmətlə," sistemin imzası ilə təkrarlanırdı (promptdakı qadağaya baxmayaraq) | Drafter sondakı bağlanış sətrini silir (test), prompt da dəqiqləşdirildi |
| Tək nəfərlik sınaqda koordinator müraciəti özünə təyin edə bilmirdi | "Başqası" siyahısında koordinatorlar da var (servis buna icazə verirdi), əvvəl icraçılar |
| Dəvət tokeni outbox cədvəlində açıq qalır (link mesajının mətni) | **Bağlandı (22.10.2026):** dəvət mesajı `purpose=secret` ilə növbəyə düşür, worker göndərəndən və ya alınmayandan (dead) sonra mətni `[gizli mətn silinib]` ilə əvəz edir, `telegram_messages`-ə yazmır. Gözləyən (hələ göndərilməmiş) mesajda token qısa müddət qalır. Real bazada köhnə link yox idi (61 mesajdan 0) |

### Faza 8: dizayn qərarları
| Məsələ | Qərar |
|---|---|
| n8n harada işləyir (istifadəçinin qərarı, 22.10.2026) | Lokal docker n8n (API və baza ilə eyni daxili şəbəkədə). Connector-un n8n-i (n8n.prdostu.com) lokal API-yə çata bilmir; tunel spec-ə ziddir. Workflow-lar `n8n/v2/*.json`-dadır (sirr yoxdur), CLI ilə import olunur |
| n8n yeni API-yə necə çatır | Compose-da `api-v2` servisi (profil v2), hosta port açılmır: `http://api-v2:8000`. `worker`-ə də konteyner ünvanları (postgres, ollama, redis) verildi, əvvəl `.env`-in host ünvanlarını götürürdü |
| Credential-lar | Servis tokeni (httpHeaderAuth) və `muraciet_n8n` (yalnız `v_daily_stats` oxuyur) `.env`-dən müvəqqəti fayl ilə import olundu, fayl hostda və konteynerdə silindi |
| n8n 2.x CLI | `n8n execute` üçün workflow-da Manual Trigger lazımdır; CLI işləyən instansiya ilə eyni broker portunu tutur (`N8N_RUNNERS_BROKER_PORT=5690`); aktivləşdirmə `publish:workflow` + restart. Git Bash-də `MSYS_NO_PATHCONV=1` |
| Gündəlik hesabat rəhbərə gedir, rəhbər isə qeydiyyatda olmaya bilər | `target=head` üçün aktiv rəhbər yoxdursa, adminlərə gedir (test) |
| SLA: yoxlamalar buraxılıbsa (n8n dayanıbsa), son gün "3 gün qalıb" yazılır və iki mesaj gedirdi (mutasiya testi tapdı) | Yalnız ən kiçik uyğun hədd göndərilir, böyüklər "göndərilmiş" sayılır |
| Saxlama müddəti | RETENTION_YEARS yoxdursa heç nə silinmir (xəbərdarlıq). Yaxın vaxtda bota yazan vətəndaşın profili silinmir. Qalıq risk: audit jurnalının payload-u və `telegram_messages` (chat_id) qalır |
| Qalıq məsələlər | ~~Gündəlik hesabat bayram günlərində də gedir~~ → həll edildi: `/internal/notify` `only_workday` ilə iş təqvimini yoxlayır. ~~n8n UI portu 5678 bütün interfeyslərə açıqdır~~ → həll edildi: yalnız 127.0.0.1 (override). Müvəqqəti test workflow-u (muracErrTestOnce) söndürülüb, UI-dan silinə bilər |

### Faza 9: son real yoxlamadan (MR-2026-000003, konteynerlərdə)
| Müşahidə | Qərar / növbəti addım |
|---|---|
| **Qəbul agenti düzəldildi (22.10.2026).** Yeni eval dəsti `eval/datasets/intake.jsonl` (22 hal, real sınaqdakı 2 dialoq daxil). Köhnə prompt: tamamlama dəqiqliyi 0.41 (aydın müraciətdə də rayon, portal, ödəniş üsulu soruşurdu, bir sualda bir neçə şey, "əlillik müavinəti"ni yenidən şərh edirdi) | Yeni prompt: mövzu və istək bəllidirsə tamamla; bir qısa sual; vətəndaşın sözlərini yenidən şərh etmə; yer/kanal soruşma; açar terminləri saxla. Nəticə: 1.0, terminlər 1.0, şəxsi məlumat sualı 0. Real hal indi `elillik` şöbəsinə gedir. Dəst üzərində düzəldildiyi üçün nəticə optimistdir |
| Qəbul agenti "əlillik dərəcəsi kəsilib" mesajını yanlış anladı ("hansı dərəcə: əmək haqqı, təqaüd, müavinət?"), əlillik konteksti itdi, müraciət `elillik` yox `pensiya` şöbəsinə getdi | Mərhələ 2: qəbul promptu ("vətəndaşın sözlərini yenidən şərh etmə, anlamadığını soruş") və eval-a qəbul agentinin dəsti (indi yoxdur) |
| Xülasənin sonuna `}` düşmüşdü | Qəbul agenti sahələrin sonundakı JSON qırıntılarını silir (test) |
| **Lokal NER (Mərhələ 2, 22.10.2026).** Profildə olmayan başqa şəxsin adı (qonşu, əməkdaş) LLM-ə açıq gedirdi | `app/privacy/ner.py`: qaydalara əsaslanan tanıyıcı, model yükləmir, mətn kompüterdən çıxmır. Soyad şəkilçiləri (-ov/-yev/-zadə, rusca -ов/-ев/-ин, hal şəkilçiləri ilə), ad + ata adı + oğlu/qızı, "X xanım/müəllim", "cənab X", ad siyahısı (~200, translit ilə). Yeni əvəzedici `[SEXS_n]`, QA-nın `foreign_values`-ına daxil deyil (əməkdaşın adı cavabı bloklamamalıdır). Tək ad maskalanmır. Toponim qaydası: ardınca küçə/prospekt/fond/pr. gələn ad şəxs deyil ("Vəliyev küçəsi", "Ziya Bünyadov pr."). Bilik bazasında yalançı tapıntı 0. E2E casusu "Hüseynova Nigar"-ı da yoxlayır, mutasiya ilə təsdiqləndi. Qalıq: -li/-lı soyadları, soyadsız tək ad |
| **FAQ keşi (Mərhələ 2, 22.10.2026).** Eyni ümumi sual hər dəfə Sonnet-ə gedirdi | `FaqCache` (Redis, `FAQ_CACHE_HOURS`, standart 24, 0 söndürür). Yalnız dialoqsuz və maskalamadan dəyişməz çıxan sual, cavabında profil məlumatı olmayan `answerable` nəticə keşlənir. Açar = heş(normallaşdırılmış sual + tapılan mənbələrin mətni): bilik bazası yenilənəndə köhnə cavab avtomatik işləmir, axtarış (lokal embedding) hər dəfə işləyir. Keşdə sualın mətni yoxdur. Redis düşəndə agent keşsiz işləyir. Hit nisbəti: geri qayıdan vətəndaşın dialoq tarixçəsi olduğu üçün keş əsasən ilk sualda və veb-vidcetdə işləyir |
| **LangSmith (Mərhələ 2, 22.10.2026).** `LANGSMITH_TRACING=true` LangGraph qrafının vəziyyətini (vətəndaşın xam mətni) xaricə göndərərdi | Avtomatik izləmə `app/config.py`-də söndürülür (settings oxunduqdan sonra, LangChain dəyişəni keşləməzdən əvvəl). `PrivateLLM` hər çağırışı `observability.traced()` ilə edir: izdə yalnız maskalanmış mesajlar və bərpadan əvvəlki cavab. İkinci qat: `Client(hide_inputs/hide_outputs=scrub)` regex + NER. Açar yoxdursa izləmə işləmir. Mutasiya: maskalanmamış mesaj ötürüləndə test qırılır |
| **Bilik bazasının boşluqları (Mərhələ 2, 22.10.2026).** Bilik bazasını genişləndirmək üçün rəsmi sənəd lazımdır, məzmun uydurulmur | Kurator üçün `app/knowledge/gaps.py`, `/admin/knowledge-gaps`, MCP `knowledge_gaps`: AI-nin "rəsmi məlumatım yoxdur" dediyi suallar (chat_logs-da sual + `no_answer_offer_appeal` cavabı cütlüyü), normallaşdırılıb sayılır, regex + NER ilə maskalanır. LLM xətası (`llm_unavailable`) boşluq sayılmır. Qalıq: profil istifadə olunmur, vətəndaşın tək adı sualda qala bilər (admin açarı ilə qorunur) |
| **Foto və sənəd əlavələri (Mərhələ 2, 22.10.2026).** Telegram-da file_id bota bağlıdır: vətəndaş botunun file_id-si əməkdaş botunda işləmir | Fayl vətəndaş botu ilə yüklənir (getFile, ≤ 10 MB, JPG/PNG/PDF, ən çoxu 5), Fernet ilə şifrəli bazada saxlanılır (miqrasiya 0003), əməkdaş botu multipart `sendDocument` ilə göndərir. Outbox payload-unda məzmun yoxdur, yalnız istinad. Əlavə müraciət yazılarkən (və ya boş vəziyyətdə, növbəti müraciət üçün) qəbul olunur; caption mətn kimi dialoqa gedir. İcazə: koordinator və təyin olunmuş icraçı, audit `pii_viewed {field: attachments}`. Saxlama: müraciətə çevrilməmiş əlavə 2 gündən sonra (RETENTION_YEARS-dən asılı olmayaraq), anonimləşdirilən müraciətin əlavələri onunla birlikdə silinir. Qalıq: artıq göndərilmiş müraciətə sonradan fayl əlavə etmək yoxdur (yeni müraciət kimi qəbul olunur); fayl məzmunu (məs. şəkildəki FİN) yoxlanılmır, LLM-ə getmədiyi üçün risk bazadadır |
| **Kurator agenti (Mərhələ 2, 22.10.2026).** Bilik bazası yalnız əl ilə genişlənirdi | 9-cu agent: vətəndaş 👍 verəndə `curate` işi; agent cavabı ümumiləşdirir (fərdi qərar, artıq bilik bazasında olan → suitable=false). Deterministik qoruma: təklifin mətni maskalanmadan dəyişməz çıxmalıdır (profil, telefon, FİN, başqa şəxsin adı yoxdur), `[...]` və əvəzedici olmamalıdır. Təklif `kb_entries`-də, kart yalnız adminlərə; təsdiq `KnowledgeBase.add_entry` (tətbiq rolu) ilə dərhal axtarışa düşür, yazmaq alınmasa təklif gözləmədə qalır. `ingest` bilik bazasını sıfırdan qurduğu üçün təsdiqlənmiş təklifləri bazadan yenidən əlavə edir. Mutasiya: qoruma və admin yoxlaması söndürüləndə testlər qırılır. Eval (22.10.2026, Sonnet 5.5 effort=medium, `eval/datasets/curator.jsonl`, 23 uydurma hal: ümumi, şəxsi məlumatlı, fərdi qərar, bilik bazasında olan): accuracy 1.0, false_proposal_rate 0.0, coverage 1.0, keyword_hit_rate 1.0 (bir açar söz dəstdə düzəldildi: "ailənin tərkibi"; saxlanmış cavablar API-siz yenidən hesablandı), pii_leaks 0, p50 2.8 s. Şəxsi məlumatlı 2 halda agent adı və telefonu özü çıxarıb. Dəst kiçik və uydurmadır, nəticə optimistdir; faktın doğruluğunu admin yoxlayır |
| **Ehtiyat nüsxə (Mərhələ 3, 22.10.2026).** Pilot siyahısında gündəlik nüsxə və bərpa sınağı açıq idi | `backup` servisi postgres image-i ilə (`pg_dump`, `openssl` var, ayrıca image yoxdur): `pg_dump -Fc | openssl AES-256-CBC pbkdf2 200k`, sha256 faylı, gündəlik, 14 gün. Nüsxədə müraciət mətnləri açıq olduğu üçün fayl bütövlükdə şifrəlidir. `verify`: müvəqqəti bazaya bərpa, 20 cədvəlin sətir sayı canlı baza ilə müqayisə, baza trap ilə həmişə silinir. Yoxlandı: zədələnmiş fayl (sha256) və səhv parol rədd olunur, köhnə nüsxə silinir. `.gitattributes`: `*.sh` LF (Windows-da CRLF konteynerdə sh-ı sındırır). Qalıq: serverdən kənara köçürmə və n8n həcminin (credential-lar) nüsxəsi əl ilədir |
| **Hesabat analitiki (Mərhələ 3, 22.10.2026).** Gündəlik hesabat yalnız rəqəmlərdir, şərh və tövsiyə yoxdur | 10-cu agent: `period_stats` (SQL: daxil olan, cavablanan, açıq, müddəti keçən, median cavab vaxtı, 👍/👎, yenidən açılan, AI cavabları, şöbə və kateqoriya üzrə, bilik bazasının boşluqları) → agent təhlil və tövsiyə yazır. Deterministik qoruma: mətndəki bütün rəqəmlər göstəricilərdə olmalıdır (fərq, faiz, müqayisə hesablaya bilməz), əks halda hesabat AI-siz gedir. n8n `05_weekly_report` bazar ertəsi 09:00 → `/internal/report/weekly` → rəhbər(lər)ə; `/admin/report` və MCP `weekly_report`. Mutasiya: qoruma söndürüləndə 3 test qırılır. Real yoxlama (22.10.2026, Sonnet 5.5, real bazanın son 7 günü, göndərilmədən): rəqəmlər düzgün, amma təhlil verilməyən əlaqə qurdu (👎 alan müraciəti Pensiya şöbəsinə aid etdi) və botun FAQ cavablarını (ai_answers) şikayət cavabları ilə qarışdırdı. Rəqəm yoxlaması belə səhvi tutmur. Düzəliş: promptda hər göstəricinin mənası və "JSON-da göstərilməyən əlaqə qurma" qaydası; təkrar yoxlamada hər iki səhv yoxdur. Qalıq: məntiqi əlaqə üçün deterministik yoxlama yoxdur, kiçik nümunə ilə yoxlanılıb |
| **Canlı çat (Mərhələ 3, 22.10.2026).** "human" niyyətində yalnız qaynar xətt təklif olunurdu | `live_chats` (miqrasiya 0005), `app/livechat.py`. İş vaxtı (`calendar.is_work_time`) human niyyətinə "💬 Operatorla danış" düyməsi, `/operator`, `/bitir`. Kart bütün aktiv koordinatorlara; ilk qəbul edən operator olur (SELECT FOR UPDATE), gözləmədə yazılanlar ona ötürülür. Vəziyyət bazadadır: əməkdaş sessiyası (Redis, 30 dəq) bitsə də çat davam edir. Açıq çatda vətəndaş mətni LLM-ə getmir. 10 dəq qəbul edilməyən çat bitir (qaynar xətt təklifi), 30 dəq yazışmasız aktiv çat bağlanır: n8n `sla/check` (5 dəqiqədən bir; bildirişlər `sla_notified` ilə təkrarlanmır) və vətəndaşın növbəti mesajı ilə; xəta bildirişi eyni workflow üzrə saatda bir dəfə ilə məhdudlaşdırıldı (`$getWorkflowStaticData`). Mesajlar chat_logs-da (`intent=live_chat`, role operator). Mutasiya: ikinci qəbul və koordinator yoxlaması söndürüləndə testlər qırılır. Qalıq: fayl/foto çatda ötürülmür; digər koordinatorların kartları yenilənmir (basanda "artıq qəbul edib" deyilir) |
| **Production-a hazırlıq (Mərhələ 3, 22.10.2026).** Əsas compose `env_file: .env`-i məcburi tələb edirdi: yalnız `.env.prod` olan serverdə production konfiqurasiyası açılmırdı | `.env` `required: false`; `${APP_DB_PASSWORD:-}`. `app/preflight.py` + `manage preflight` (konteynerdə, yalnız adlar: Fernet açarı, bot tokeni formatı, iki botun və iki açarın fərqli olması, tətbiqin sahib rolu ilə qoşulmaması, minimum uzunluq; saxlama müddəti, LangSmith, qaynar xətt xəbərdarlıqdır). `scripts/deploy.sh` (--check: fayllar, açıq port yoxdur, preflight). Müvəqqəti qovluqda saxta sirlərlə prod config yoxlandı: 0 açıq port. Qalıq: real serverdə sınanmayıb, n8n credential-ları CLI/UI ilə əl ilədir |
| **Özünü yoxlama (22.10.2026).** Bu sessiyada yazılan kodun nəzərdən keçirilməsi | 1) Saxlama təmizliyi `live_chats`-ı əhatə etmirdi: vətəndaşın Telegram ID-si əbədi qalırdı. İndi bitmiş köhnə çatlar silinir (`live_chats` sayı xülasədə). 2) Canlı çatda göndərilən foto "növbəti müraciətə əlavə" kimi səssizcə saxlanırdı, operator görmürdü: indi saxlanmır, vətəndaşa `live_chat_text_only` deyilir |
| **MCP-də yazma (Mərhələ 3, 22.10.2026).** Spec MCP ilə yazma əməliyyatlarını nəzərdə tutur, amma "AI təklif edir, insan qərar verir" qaydası dəyişməzdir | Ən təhlükəsiz variant seçildi: `propose_assignment` (MCP) → `POST /admin/proposals/assign` müraciəti dəyişmir, `assignment_proposed` auditi yazır və koordinatorlara `callback(ASSIGN, id, version, staff_id)` düyməli kart göndərir: təsdiq mövcud təyinat axınıdır (authz, optimistic locking, audit), müraciət dəyişibsə köhnə düymə işləmir. `GET /admin/staff` (əlaqə məlumatı yox). Bağlanmış müraciətə təklif 409. Mutasiya: vəziyyət yoxlaması söndürüləndə test qırılır. Qalıq: təklif kartı təyinatdan sonra yenilənmir (köhnə düymə "köhnəlib" deyir) |
| **Real avtomatik sınaq (22.10.2026).** İşləyən konteynerlərə istifadəçinin ID-si ilə update-lər (real Claude, real Telegram, sınaq faylları vətəndaş botu ilə yüklənib file_id-ləri istifadə olundu) | Canlı çat 5/5, müraciət 11/11 (MR-2026-000004). Tapılan: iş vaxtı "human" niyyətində düymə operatoru təklif edirdi, mətn isə yalnız rəsmi müraciətdən danışırdı → `human_handoff_live` şablonu. Sınaqdan qalanlar: MR-2026-000004 (bağlı, 👍) və admin qərarını gözləyən kurator təklifi |
| Koordinator tədbirsiz qısa cavab göndərdi ("10 gün ərzində icra ediləcəkdir", nəticə "təmin edildi") | Dizayna uyğundur: keyfiyyət iradları xəbərdarlıqdır, koordinatorun cavabı təsdiqə düşmür. Pilotda əməkdaş təlimi; lazım olarsa `missing_measures` + "təmin edildi" birləşməsi üçün təsdiq qaydası (approval_rules.yaml) |

## Issues Encountered
| Issue | Resolution |
|-------|------------|
| Docker n8n-i ilə connector-un n8n-i fərqli instansiyalardır | **8.3 yoxlandı (22.10.2026):** connector-un n8n-i `https://n8n.prdostu.com` (xarici IP <SERVER_IP>), inkişaf kompüteri isə <SERVER_IP>. Uzaq n8n lokal API-yə (127.0.0.1:8001) və Postgres-ə çata bilməz. Spec-ə görə API və baza internetə açılmamalıdır, ona görə tunel tövsiyə olunmur. Diaqnostika workflow-u (aZKRhPeotIDD4rNI) arxivləndi |
| `pytest -m legacy` digər qovluqları da toplayır, `tests/integration/conftest.py` `app.config`-i birinci import edir | Legacy yalnız öz qovluğundan: `pytest -m legacy tests/legacy` |
| Köhnə test modulu import zamanı `DATABASE_URL`-i sqlite-a dəyişir, `app.config.settings` və `app.db.session.engine` isə import zamanı yaranır. Bu, Faza 2-nin inteqrasiya testlərinə təsir edə bilər | Faza 2-də engine/sessiya injeksiya oluna bilən olmalıdır (modul səviyyəsində sabit engine yox). Lazım olarsa legacy testlər ayrıca işə salınır: `pytest -m legacy` |

## Resources
- Spec: `docs/superpowers/specs/2026-10-21-dovlet-muraciet-sistemi-design.md`
- Plan: `docs/superpowers/plans/2026-10-21-merhele-1-implementasiya.md`
- Qanunun mətni: https://www.migration.gov.az/content/pdf/11f5298631e03047b059f073cecb0849.pdf

## Visual/Browser Findings
- İstifadəçinin skrinşotları: kurs dərsləri #0–#38 (4 ay). Siyahı spec-in 17-ci bölməsindədir.
