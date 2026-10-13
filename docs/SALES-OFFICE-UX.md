# Satış ofisi üçün Telegram interfeysi — V1 istifadəçi axınları

Bu interfeys Master Spec §3, §4, §5 və §21 sərhədlərini qoruyur.
Məqsəd 3–4 nəfərlik satış ofisində texniki biliyi zəif istifadəçinin
adi Azərbaycan dili ilə işləməsidir; SQL, Qdrant və provider adları göstərilmir.

## Sadə menyu

Aktiv istifadəçi /start və ya /menu yazdıqda beş seçim görür:

- Bizim bazada axtar: növbəti adi mətn **yalnız daxili** məlumatda axtarılır.
- İnternetdə axtar: növbəti adi mətn **yalnız xarici** web nəticələrində axtarılır.
- Bizdə və internetdə axtar: növbəti mətn hər iki sinifdə araşdırılır,
  nəticələr aydın ayrılır; xarici qiymət daxili qiyməti dəyişmir.
- Fayl və şəkil saxla: yazılı izah açılır. Faylı göndərmək məlumatı dərhal
  authoritative saxlamır; /saxla tələb olunur.
- Ətraflı kömək: mövcud yardım mətnini göstərir.

Seçimlər yalnız ACTIVE istifadəçiyə və yalnız private chat-da açılır.
Axtarış üçün seçim 10 dəqiqə ərzində **bir adi mesaj** üçün qüvvədədir,
sonra avtomatik silinir. İstifadəçi /menu ilə menyunu yenidən açdıqda,
«Fayl və şəkil saxla» və ya «Ətraflı kömək» seçdikdə, başqa əmr yazdıqda
və ya foto/sənəd göndərdikdə əvvəlki axtarış seçimi ləğv olunur. Beləliklə,
əvvəl seçilmiş internet axtarışı sonradan göndərilən şəxsi məlumatı
səhvən xaricə ötürmür. Bu, application user_data-da müvəqqəti UX vəziyyətidir,
authoritative biznes yazısı deyil. Bot yenidən başladıqda seçim itə bilər;
istifadəçi /menu ilə yenidən seçir.

Açıq ingestion sessiyası zamanı yeni axtarış rejimi seçilmir:
əvvəl /saxla və ya /cancel tələb olunur. Beləliklə, sessiya təlimatı axtarış
sorğusu ilə səhv salınmır. Hesab təsdiqi və səlahiyyətlər hər dəfə serverdə
yoxlanılır. Menyu düymələri birbaşa məlumat yazmır, silmir, məhsul yaratmır.

## Məxfiliyin qorunması

İnternet rejiminə keçmək istifadəçinin açıq seçimidir.
İnternetdə axtarışa göndərilən sual provider-ə çıxa bilər; menyu buna görə
məxfi şirkət məlumatı daxil etməməyi açıq xəbərdar edir.
Bot daxili PostgreSQL/Qdrant sübutlarını /internet cavabına qatmalı deyil.
Model istifadəçi rolunu, məhsul identifikasiyasını və qiymət hesablamasını seçmir.

## Hələ yoxlanmalı qəbul sınaqları

- İki Telegram hesabı ilə admin/istifadəçi hüquqları və isolation.
- Adi, səhv yazılmış satış sualları, AZ/RU/EN məhsul kodları və axtarış rejimləri.
- SearXNG-in real nəticələri, Tavily fallback, səhifə fetch və SSRF.
- 3–4 paralel istifadəçi ilə burst/load və timeout sanity.
- Fayl/şəkil upload, /saxla, /cancel və /leqv_et tam dövrü.
- Sistemin yenidən başladılması, disk dolması və internet kəsintisi.
- Məxfi sənədlərin/logların GitHub-a və xarici provider-lərə sızmaması.

Bu mərhələdə bunlar **manual və ya ayrıca integration gate** olaraq qalır;
unit test və sağlam Docker konteyneri production hazırlığı demək deyil.
