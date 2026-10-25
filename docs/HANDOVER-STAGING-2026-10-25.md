# Telegram Ofis Asistenti — sınaq mühitinin yekun təhvil aktı
**Tarix:** 2026-10-25 (Asia/Baku)  
**Layihə:** `f1r10/telegram-ai`  
**Əhatə:** Kali Linux `~/telegram-ai-staging`, `docker-compose.staging.yml`, ayrıca Telegram sınaq botu.  
**Kod etalonu:** `main` @ `724b1ebbd00f7786fb9b1c359980db6c4e23559d` (PR #107 prioritet saytlar daxil).  
**Status:** **STAGING_ACCEPTED_WITH_LIMITS**. **PRODUCTION_READY / tam spesifikasiya üzrə qəbul deyil.**

Bu sənəd 2026-10-25 tarixli sahibin təqdim etdiyi real Kali/Telegram nəticələrini və həmin commit üçün GitHub Actions testlərini ayırır. Sənəd məxfi şirkət fayllarını, bot tokenlərini, parolları, giriş linklərini və ya müştəri məlumatlarını saxlamır.

## 1. Etibarlı test sübutları

| Sahə | Müşahidə edilən nəticə | Sübut sinfi |
| --- | --- | --- |
| CI unit | 949 passed, 67 deselected; Ruff lint və format PASS; strict Pyright 0 error | [main CI #625](https://github.com/f1r10/telegram-ai/actions/runs/40221824084) |
| CI disposable PostgreSQL/Qdrant | 66 passed, 1 skipped, 949 deselected | Eyni CI, ayrıca inteqrasiya işi |
| Telegram sənəd saxla/axtar | Sintetik PDF/DOCX/XLSX, ziddiyyət/mənbə, yeni test qeydi: istifadəçi uğur bildirib | Owner UAT; tam ekran qeydləri bu depoda yoxdur |
| Telegram istifadəçi icazələri/köhnə mənbə | Administrator–adi istifadəçi sərhədi və köhnə mənbə düyməsi: istifadəçi uğur bildirib | Owner UAT |
| Prioritet internet saytları | `/sayt_elave`, `/saytlar`, `/sayt_sil`, `/internet` funksiyası istifadəçi tərəfindən canlı sınaqda qəbul edilib | PR #107 və owner UAT |
| Servis hazırlığı | `READY`, PDF/OCR, PostgreSQL, indeks işləri, Qdrant, SearXNG real JSON `OK`; `tgstage_app` və `tgstage_worker` restart=0 (müşahidə vaxtında) | Owner-provided Kali terminal çıxışı |
| Mini App | `MINIAPP_ENABLED=False`, stock transfer/actions/XLSX flags `False` | Təhlükəsiz konfiqurasiya təsdiqi, **Mini App UAT deyil** |
| PostgreSQL backup | Restic `SNAPSHOT_WRITTEN`; artefakt testi `SNAPSHOT_ARTIFACTS_VERIFIED`; TOC oxunur | Owner terminal çıxışı |
| PostgreSQL real restore | Ayrı PostgreSQL 17 konteynerində `ISOLATED_POSTGRES_RESTORE=PASS`; users=1, file_objects=15, knowledge_index_records=21, sales_entries=1; Alembic `0022_inventory_units` | Owner terminal çıxışı |
| Qdrant snapshot restore | SHA256 keçib; ayrı Qdrant 1.19 konteynerində `ISOLATED_QDRANT_RESTORE=PASS`; vektor 22/22 | Owner terminal çıxışı |
| DB ↔ live vector mapping | `POSTGRES_EXPECTED_CHUNKS=22`, `QDRANT_ACTUAL_POINTS=22`, `MATCHED_GROUPS=22`, `MISSING_POINTS=0`, `EXTRA_POINTS=0`; `DOCUMENT_VECTOR_INTEGRITY=PASS` | Owner terminal çıxışı; hər bir vektorun mətn/hash identikliyi ayrıca yoxlanmayıb |

**CI skip:** Qdrant/PostgreSQL inteqrasiya işində 1 skip var. Bu, ayrı canlı SearXNG inteqrasiya sınağının CI mühitində konfiqurasiya edilməməsi ilə uyğun gəlir; Kali `check_readiness` vasitəsilə real SearXNG JSON sorğusu ayrıca sınaqdan keçib. Skip = PASS deyil.

**Staging commit təsdiqi:** GitHub etalonu məlumdur; Kali checkout-da ayrıca `git rev-parse HEAD` çıxışı bu akt üçün təqdim edilməyib. “Tam eyni commit VM-də işləyir” nəticəsi verilməyib.

## 2. Funksional təhvilin dəqiq əhatəsi

Native Telegram bot sınaq istifadəsi üçün təhvil verilir: normal sual, daxili mənbəli sənəd sorğuları, sintetik PDF/DOCX/XLSX/TXT saxlanması və indekslənməsi, müvafiq mənbə faylının çatdırılması, istifadəçi rolları, xatırlatma, internet sorğusu və administrator tərəfindən idarə olunan prioritet domenlər. Mövcud satış/anbar mexanizmləri yalnız təsdiq və səlahiyyət sərhədləri çərçivəsində istifadə edilməlidir. Sənəd qiyməti və ya təklifdən faktiki satış və ya cari stok avtomatik yaradılmamalıdır.

Sistem `READY` olduqda belə bu yalnız hazırkı konfiqurasiyanın sağlamlıq yoxlamasıdır; qeyri-məhdud yük, real korpus üzrə bütün cavabların dəqiqliyi və istehsal istifadəsi üçün qəbul sertifikatı deyil.

## 3. Hələ tamamlanmamış sərhədlər — saxta PASS yoxdur

1. [#43](https://github.com/f1r10/telegram-ai/issues/43): Mini App HTTPS / Telegram Android-iOS, real imzalı launch, fayl yükləmə/endirmə və mobil UI qəbul sınağı. `MINIAPP_ENABLED=False`; aktivləşdirilməyib.
2. [#40](https://github.com/f1r10/telegram-ai/issues/40): bütün anbarlar, vahid çevirmələri, düzəlişlər, transfer, rezervasiya və təhlükəsiz Excel bulk import üzrə tam istehsal UAT. Mini App anbar yazılış flag-ləri bağlıdır.
3. [#35](https://github.com/f1r10/telegram-ai/issues/35): real kameradan fərqli bucaqlarda çəkilmiş şəkillər, qəsdən oxşar və mənfi nümunələrlə foto modeli kalibrasiya olunmayıb. Semantik namizəd dəqiq məhsul, qiymət və ya qalıq sayılmır.
4. [#32](https://github.com/f1r10/telegram-ai/issues/32): tarixi uğursuz OCR/indeks retry yollarının bütün failure-injection senariləri sınaq mühitində operator tərəfindən tam qəbul edilməyib.
5. [#11](https://github.com/f1r10/telegram-ai/issues/11): orijinal istifadəçi korpusu və real sürətli/yüklü mühitə dair bütün P0 tələblərinin bağlanması üçün kifayət qədər canlı sübut yoxdur. UI-ni yalnız sınaq istifadəçisi təsdiqləyib.
6. **Backup siyasəti:** Restic repository və Qdrant snapshot-ları eyni Kali VM diskindədir; ayrıca məkan/off-site replikasiya, periodik backup cədvəli, parolun təhlükəsiz alternativ saxlanması və bərpa vaxtı hədəfləri (RPO/RTO) **qurulmuş sayılmır**.
7. **Birləşdirilmiş bərpa:** PostgreSQL və Qdrant **ayrı-ayrılıqda** bərpa olunub. Fərqli vaxtlarda yaradılan snapshot-ların eyni anlıq vəziyyəti, bərpa edilmiş *vahid* mühitdə tam chatbot sorğusu və bütün orijinal fayl hash-ləri üzrə yekun qəbul **yoxlanmayıb**.
8. **Kommersiya:** Müqavilə/lisenziya, çoxşirkətli sərhədlər və real biznes məlumatlarının təhlükəsiz miqrasiyası üçün ayrıca qərar lazımdır.

## 4. İstismar və təhlükəsizlik sərhədləri

- Kali sınaq mühitində həmişə `docker-compose.staging.yml` və `--env-file .env`; **`docker compose down -v` işlətmə**.
- Cari backup və snapshot-ları silmə; Restic parol faylını GitHub-a göndərmə. `/root/telegram-office-backup/repo` yalnız lokal test repository-sidir.
- Botun Mini App və geri dönməz anbar dəyişiklikləri üçün flag-lərini ayrıca yoxlamadan açma.
- Məxfi istifadəçi sənədləri və tokenlər ictimai GitHub issue/PR/log-a əlavə edilməməlidir.
- Əgər istifadəçi sınaq botunu gündəlik daxili sınaqdan istehsala keçirmək istəyirsə, yuxarıdakı açıq qapıların sahib tərəfindən xüsusi təsdiqi tələb olunur.

## 5. Qərar və təhvil

**Qəbul olunur:** İndiki Kali sınaq mühitində native Telegram ofis köməkçisinin müşahidə edilən gündəlik imkanları, ayrıca PostgreSQL/Qdrant backup-restore infrastrukturu və təsdiqlənmiş CI baza keyfiyyəti.

**Qəbul edilmir:** Bütün V2 xüsusiyyətlərinin tam başa çatması, Mini App-in istifadəyə hazır olması, real foto məhsul tanımasının kommersiya dəqiqliyi, bütün anbar əməliyyatlarının istehsal təhlükəsizliyi, fərqli snapshot-larla vahid fəlakət bərpası və prod deploy.

Bu mərhələdə **əlavə təkrarlanan terminal sınaqlarına ehtiyac yoxdur**. Test istifadəçisi yorulduğuna görə yeni sınaq addımları tələb edilmir. Gələcək tələb olarsa yalnız bloklayan açıq gate üzrə məqsədli UAT aparılmalıdır.
