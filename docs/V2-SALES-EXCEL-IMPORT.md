# V2 — Excel-dən satış fakturalarının idxalı

Bu idxal yalnız **təsdiqlənmiş, tamamlanmış satış fakturaları** üçündür. Müştəriyə verilən qiymət təklifi, sifariş və ya ödəniş qəbzi satış kimi daxil edilməməlidir.

## Excel formatı

Yalnız **bir görünən vərəqi** olan `.xlsx` faylı qəbul edilir. Birinci sətirdə bu beş sütun olmalıdır:

| Müştəri | Tarix | Faktura | Məbləğ | Valyuta |
|---|---|---|---:|---|
| A Şirkəti | 2026-10-01 | INV-001 | 125.50 | AZN |
| A Şirkəti | 2026-10-05 | INV-002 | 75.00 | AZN |
| B Şirkəti | 2026-10-06 | INV-003 | 10.00 | USD |

- Tarix ISO `YYYY-MM-DD` və ya Excel-in tarix xanası olmalıdır.
- Faktura nömrəsini Excel-də **mətn** kimi saxlayın; başlanğıc sıfırları qorunmalıdır.
- Məbləğ müsbət və iki onluq rəqəmə qədər olmalıdır. **Formula** qəbul edilmir.
- Faktura nömrəsi eyni müştəri üçün təkrar ola bilməz. Dublikat olarsa, **bütün idxal rədd edilir**.
- Bir faylda maksimum 100 faktura, maksimum 2 MiB sıxılmış XLSX qəbul edilir.
- Valyutalar məbləğcə birləşdirilmir. Stok/ədədlər hesablanmır; hər sətir fakturanın yekun pul məbləğidir.

## Telegram addımları

1. Telegram-da administrator kimi daxil olun.
2. Excel faylını **sənəd kimi** seçin və caption bölməsinə `Satışları idxal et` yazın.
3. Bot ilkin faktura sayını, valyuta üzrə cəmi və nümunə fakturaları göstərəcək.
4. Diqqətlə yoxlayın. Yalnız bundan sonra **Fakturaları təsdiqlə** düyməsini seçin.
5. Bot təsdiqlənmiş fakturaları bir tranzaksiyada PostgreSQL satış jurnalına yazır və audit qeydi yaradır.
6. `Bu il A şirkətinə nə qədər satmışıq?` sualı ilə hesabatı alın.

## Təhlükəsizlik və məhdudiyyətlər

- İdxal üçün **ADMIN** rolu tələb olunur. Adi USER hesabı yaza bilməz.
- Faylın Telegram-a göndərilməsi satış cədvəlinə yazmır; ayrıca düymə ilə təsdiq məcburidir.
- Təsdiq bir dəfə istifadə olunur və 10 dəqiqəyə bitir. Bot restart edilərsə gözləyən idxal təsdiqi itə bilər, lakin təsdiqlənmiş fakturalar bazada qalır.
- Yeni idxal üçün göndərilən XLSX **sənəd bilik bazasına avtomatik əlavə edilmir**. Sənədi arxivləmək istəyirsinizsə onu ayrıca normal fayl kimi göndərib yadda saxlayın.
- İdxal mətnli PDF, skan, faktura şəkilləri və ya ixtiyari Excel sxemlərini avtomatik başa düşmür. Bu imkanlar ayrıca review/extraction mərhələsi tələb edir.
- Təkliflər və ödənişlər tamamlanmış satış kimi **avtomatik çevrilmir**. Hesabat yalnız satış jurnalındakı POSTED fakturaları hesablayır.

## Test statusu

Test avtomatlaşdırması `tests/unit/test_v2_sales_xlsx_import.py`,
`tests/unit/test_v2_sales_xlsx_telegram.py` və
`tests/integration/test_v2_sales_postgres.py` fayllarındadır.
Həqiqi şirkət sənədləri ilə son istifadəçi qəbul testi ayrıca aparılmalıdır.
