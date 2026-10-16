"""Azerbaijani Telegram message catalog. All user-facing bot text lives here."""

AUTH_PROMPT = "Salam. Daxil olmaq üçün giriş parolunu göndərin."
AUTH_FAILED = "Giriş parolu yanlışdır və ya müvəqqəti limitə çatmısınız."
AUTH_WAIT = "Çoxsaylı uğursuz cəhd oldu. Zəhmət olmasa bir qədər sonra yenidən cəhd edin."
AUTH_DISABLED = "Bu hesab deaktiv edilib. Administratorla əlaqə saxlayın."
AUTH_REJECTED = "Giriş müraciətiniz rədd edilib."
AUTH_PENDING = "Giriş müraciətiniz administrator təsdiqini gözləyir."
AUTH_ADMIN = "Giriş təsdiqləndi. Siz administrator olaraq aktivləşdirildiniz."
AUTH_APPROVED = "Giriş müraciətiniz təsdiqləndi. Artıq sistemdən istifadə edə bilərsiniz."
AUTH_REJECTED_BY_ADMIN = "Giriş müraciətiniz administrator tərəfindən rədd edildi."
PRIVATE_ONLY = "Bu bot yalnız şəxsi söhbətdə istifadə edilə bilər."
UNKNOWN_USER = "Əvvəlcə /start əmrini göndərin və giriş prosesini tamamlayın."
NO_PERMISSION = "Bu əməliyyat üçün icazəniz yoxdur."
WELCOME = "Xoş gəlmisiniz! Ofis köməkçiniz hazırdır. Aşağıdan seçim edin və ya birbaşa yazın."
MENU_TITLE = (
    "🏢 OFİS KÖMƏKÇİNİZ\n\n"
    "Salam! Məhsul, sənəd və satış barədə adi dildə soruşa bilərsiniz. "
    "PDF, Word, Excel və ya şəkli birbaşa söhbətə göndərin.\n\n"
    "Nədən başlayaq?"
)
MENU_CHAT_BUTTON = "💬 Sual ver"
MENU_PRODUCT_BUTTON = "📦 Məhsul axtar"
MENU_UPLOAD_BUTTON = "📄 Sənəd əlavə et"
MENU_SALES_BUTTON = "📊 Satış hesabatı"
MENU_INTERNET_BUTTON = "🌐 İnternet"
MENU_HELP_BUTTON = "❔ Kömək"
# Retained for legacy callback compatibility and explicit slash commands.
MENU_INTERNAL_BUTTON = "Daxili bazada axtar"
MENU_COMBINED_BUTTON = "Bizdə və internetdə axtar"
MENU_CHAT_PROMPT = (
    "💬 SUALINIZ\n\nSualınızı sadəcə yazın. Sənəd göndərmisinizsə, "
    "onun haqqında da soruşa bilərsiniz."
)
MENU_PRODUCT_PROMPT = (
    "📦 MƏHSUL AXTARIŞI\n\nMəhsulun kodunu və ya adını yazın. "
    "Məsələn: «KA45 bizdə var? Qiyməti nədir?»"
)
MENU_SALES_PROMPT = (
    "📊 SATIŞ HESABATI\n\n"
    "Şirkət və müddəti adi dildə yazın:\n"
    "«Bu il A şirkətinə nə qədər satmışıq?»\n"
    "və ya «A Şirkəti | 2026»\n\n"
    "📥 EXCEL İDXALI (ADMIN)\n"
    "Müştəri, Tarix, Faktura, Məbləğ, Valyuta sütunları olan "
    ".xlsx faylına «Satışları idxal et» qeydi əlavə edib göndərin. "
    "İdxaldan əvvəl fakturaları təsdiqləmək lazımdır.\n\n"
    "Qeyd: yalnız təsdiqlənmiş satış fakturaları hesablanır; "
    "təkliflər və ödənişlər satış kimi göstərilmir."
)
MENU_ASK_INTERNAL = "Daxili bazada nə axtarım? Məhsul kodunu və ya sualı yazın."
MENU_ASK_INTERNET = (
    "🌐 İNTERNET AXTARIŞI\n\nAçıq internetdə nə axtarım? Sualınızı yazın. "
    "Bu rejim daxili bazadan istifadə etmir. Məxfi şirkət məlumatı göndərməyin."
)
MENU_ASK_COMBINED = (
    "Daxili və xarici məlumatları nə üzrə müqayisə edim? "
    "İnternetə məxfi şirkət məlumatı göndərməyin."
)
MENU_UPLOAD_GUIDE = (
    "📄 SƏNƏD ƏLAVƏ ET\n\nPDF, Word, Excel, TXT və ya JPG/PNG göndərin. "
    "Fayllar əvvəlcə müvəqqəti saxlanılır. Hazır olduqda aşağıdakı "
    "«Yadda saxla» düyməsi ilə təsdiqləyin.\n\n"
    "Məhsul şəklini kodla əlaqələndirmək üçün şəklə «Bu KA45-dir, yadda saxla» "
    "kimi qeyd əlavə edə bilərsiniz."
)
MENU_SAVE_BUTTON = "✅ Yadda saxla"
MENU_CANCEL_BUTTON = "Ləğv et"
MENU_SAVE_CONFIRM = (
    "📋 YADDA SAXLAMA TƏSDİQİ\n\n"
    "Göndərdiyiniz faylları şirkətin bilik bazasına əlavə edim? "
    "Təsdiqdən sonra sənədlər emal növbəsinə keçəcək."
)
MENU_ACTIVE_SESSION = (
    "Əvvəl göndərdiyiniz fayllar üçün açıq sessiya var. "
    "Onları yadda saxlamaq və ya ləğv etmək üçün aşağıdakı düyməni seçin."
)
MENU_SESSION_EXPIRED = "Əvvəlki seçim vaxtı bitib. /menu ilə yenidən başlaya bilərsiniz."
MENU_TEMPORARY_UNAVAILABLE = (
    "Seçim müvəqqəti açılmadı. Sualı birbaşa yazın və ya /menu ilə yenidən sınayın."
)
PENDING_STATUS = "Hesabınız administrator təsdiqini gözləyir."
WHOAMI_UNKNOWN = "Sizin üçün qeydiyyatdan keçmiş aktiv hesab tapılmadı."
WHOAMI_DETAILS = "Telegram ID: {telegram_user_id}\nRol: {role}\nStatus: {status}"
USERS_NONE_REGISTERED = "Qeydiyyatda istifadəçi yoxdur."
USERS_NO_NAME = "Ad yoxdur"
USERS_NO_USERNAME = "username yoxdur"
USERS_LINE = "ID: {telegram_user_id} | {name} | {username} | {status}"
CANCELLED = "Parol daxil etmə prosesi ləğv edildi."
TEMPORARY_ERROR = "Müvəqqəti texniki xəta baş verdi. Zəhmət olmasa sonra yenidən cəhd edin."
AI_TIMEOUT = (
    "Sorğunun emalı vaxt limitini keçdi. Heç bir əlavə məlumat "
    "bazaya yazılmadı. Sualı qısaldıb yenidən sınayın."
)
FILE_UNSUPPORTED = "Bu fayl növü dəstəklənmir. PDF, DOCX, XLSX, TXT, JPG, JPEG və PNG göndərin."
FILE_TOO_LARGE = "Fayl texniki ölçü limitini aşır."
FILE_ADDED = (
    "Fayl müvəqqəti sessiyaya əlavə edildi.\n"
    "Fayllar: {files}\nTəlimat sayı: {instructions}\nYadda saxlamaq üçün /saxla"
)
FILE_DUPLICATE = "\nBu faylın eyni məzmunu əvvəlcədən mövcuddur; yeni göndəriş saxlanıldı."
CAPTION_COMMAND_FILE_SKIPPED = (
    "Diqqət: açıq əmr istifadə edildiyi üçün bu mesajdakı fayl yadda saxlanılmadı."
)
IMAGE_NOT_READ = (
    "Diqqət: göndərdiyiniz şəkli oxuya bilmədim. Cavab yalnız mətn hissəsidir; "
    "şəkili yenidən göndərin və ya keyfiyyətini yoxlayın."
)
INGESTION_COMPLETED = (
    "Emal tamamlandı.\n"
    "Fayllar: {total}\n"
    "Uğurla emal olundu: {succeeded}\n"
    "Mətni çıxarılmadı (OCR/şəkil): {deferred}\n"
    "Xəta: {failed}"
)
INSTRUCTION_ADDED = "Təlimat müvəqqəti sessiyaya əlavə edildi."
NO_OPEN_SESSION = "Açıq yadda saxlama sessiyanız yoxdur. Əvvəlcə fayl göndərin."
SAVE_SUCCESS = (
    "Yadda saxlama sessiyası qəbul edildi.\n"
    "Fayllar: {files}\nTəlimatlar: {instructions}\n"
    "Dublikat fayllar: {duplicates}\nEmal statusu: növbədə"
)
SAVE_NO_SESSION = "Yadda saxlamaq üçün açıq sessiya yoxdur."
CANCEL_SUCCESS = "Açıq yadda saxlama sessiyası ləğv edildi."
CANCEL_NO_SESSION = "Ləğv ediləcək açıq sessiya yoxdur."
STORAGE_ERROR = "Fayl müvəqqəti saxlanılmadı. Zəhmət olmasa sonra yenidən cəhd edin."
INGESTION_LIMIT = "Sessiya üçün texniki fayl sayı limitinə çatıldı."
AI_NOT_CONFIGURED = "AI cavab xidməti konfiqurasiya edilməyib."
AI_RATE_LIMITED = "Çox tez-tez sorğu göndərildi. Zəhmət olmasa bir qədər sonra yenidən cəhd edin."
AI_INVALID_QUESTION = "Sual boş ola bilməz. Məsələn: /baza KA45 zəmanət müddəti nə qədərdir?"
WEB_INVALID_QUESTION = "Sual boş ola bilməz. Məsələn: /internet Python 3.14 yenilikləri"
WEB_UNAVAILABLE = "İnternet axtarışı hazırda əlçatan deyil. Zəhmət olmasa sonra yenidən cəhd edin."
AI_UNAVAILABLE = (
    "Cavab yaratma xidməti hazırda əlçatan deyil. Zəhmət olmasa sonra yenidən cəhd edin."
)
QDRANT_UNAVAILABLE = (
    "Daxili bilik bazası hazırda əlçatan deyil. Zəhmət olmasa sonra yenidən cəhd edin."
)
COMBINED_INTERNAL_UNAVAILABLE = (
    "Daxili bilik bazası hazırda əlçatan deyil, ona görə birləşdirilmiş cavab verə bilmirəm. "
    "Yalnız internet cavabı üçün /internet əmrini göndərin."
)
AI_SOURCE_VALIDATION_FAILED = (
    "Cavabın mənbə yoxlaması uğursuz oldu. Zəhmət olmasa sualı yenidən verin."
)

APPROVE = "Təsdiqlə"
REJECT = "Rədd et"
DISABLE = "Deaktiv et"
EXTRACTION_PROBLEMS = "Diqqət tələb edən namizədlər:"
EXTRACTION_REJECTED = "Strukturlaşdırılmış namizədlər ləğv edildi. Mənbə faylları saxlanıldı."
EXTRACTION_REVIEW_UNAVAILABLE = "Bu baxış artıq etibarlı deyil və ya tamamlanıb."
SOURCE_FILE_UNAVAILABLE = "Mənbə faylı tapılmadı və ya hazırda göndərilə bilmir."
SOURCE_FILE_AMBIGUOUS = "Bir neçə fayl uyğundur. Mənbə nömrəsini qeyd edin."
WEB_SOURCE_UNAVAILABLE = "Son cavabda uyğun internet linki tapılmadı."
EXTRACTION_APPLIED = (
    "Yaddaşa yazma tamamlandı.\n\n"
    "Əlavə edildi: {applied}\nMövcud idi: {existing}\n"
    "Admin baxışı tələb edir: {admin}\nTətbiq olunmadı: {failed}"
)

WEEKDAY_NAMES = (
    "bazar ertəsi",
    "çərşənbə axşamı",
    "çərşənbə",
    "cümə axşamı",
    "cümə",
    "şənbə",
    "bazar",
)

HELP_TEXT = (
    "Mümkün əmrlər:\n\n"
    "/start — hesabı açmaq və giriş etmək\n"
    "/menu — sadə seçim menyusunu açmaq\n"
    "/baza <sual> — daxili bilik bazasından cavab almaq\n"
    "/internet <sual> — internetdən müvəqqəti cavab almaq (yaddaşa yazılmır)\n"
    "/tamaxtar <sual> — daxili və internet mənbələrində birlikdə axtarmaq\n"
    "/saxla — göndərilən faylları və təlimatları yaddaşa köçürmək\n"
    "/hatirlatmalar — aktiv xatırlatmalarınızı görmək\n"
    "/cancel — açıq sessiyanı ləğv etmək\n"
    "/leqv_et <kod> — təsdiqlənmiş şəkil əlaqəsini geri almaq (yalnız administrator)\n"
    "/verilerimi_sil — şəxsi məlumatlarınızı silmək\n"
    "/whoami — hesabınızın statusunu görmək\n"
    "/help — bu siyahı\n\n"
    "Orijinal mənbə faylını almaq üçün, məsələn, 2-ci mənbəni göndər yazın.\n\n"
    "Xatırlatma üçün sadə mesaj yazın, məsələn: sabah saat 9-da Əliyə zəng etməyi xatırlat. "
    "Vaxtı başa düşəndə sizdən təsdiq istəyəcəyik; təsdiq etmədən heç nə saxlanılmır.\n\n"
    "Şəkil göndərərkən yazın: Bu KA45-dir, yadda saxla. Təsdiq açıq yadda saxlama sessiyasına "
    "götürülür; /saxla şəkli həmin məhsulun nümunəsi kimi əlaqələndirir, /cancel isə təsdiqi və "
    "gözləyən faylı silir. Məhsul tapılmasa, kod birmənalı olmasa və ya şəkil artıq başqa məhsula "
    "təsdiqlənibsə heç nə yadda saxlanılmır.\n\n"
    "Administrator /leqv_et KA45 əmri ilə təsdiqlənmiş şəkil əlaqəsini geri ala bilər; əvvəlcə "
    "təsdiq soruşulur. Ləğv edilən təsdiq tarixçədə qalır və şəkil faylları silinmir."
)

REMINDER_PROPOSED = (
    "Xatırlatmanı belə başa düşdüm:\n\n"
    "Məzmun: {text}\nVaxt: {when}\n\n"
    "Təsdiq etmədən heç nə yadda saxlanılmayacaq."
)
REMINDER_CLARIFY_NOTE = "\n\nVaxt birmənalı deyil: {reason}. Zəhmət olmasa dəqiqləşdirin."
REMINDER_CONFIRM_BUTTON = "Təsdiqlə"
REMINDER_EDIT_BUTTON = "Dəyiş"
REMINDER_DISCARD_BUTTON = "Ləğv et"
REMINDER_SAVED = "Xatırlatma yadda saxlanıldı.\nVaxt: {when}"
REMINDER_PROPOSAL_EXPIRED = "Bu təklifin vaxtı keçib. Zəhmət olmasa xatırlatmanı yenidən göndərin."
REMINDER_INVALID_LINK = "Bu təklif artıq etibarlı deyil. Xatırlatmanı yenidən göndərin."
REMINDER_EDIT_PROMPT = "Yeni vaxtı yazın. Məsələn: sabah saat 10-da, 2 saatdan sonra, cümə axşamı."
REMINDER_EDIT_UNPARSEABLE = (
    "Vaxtı başa düşmədim. Məsələn: sabah saat 10-da, 2 saatdan sonra, ayın 15-də."
)
REMINDER_TIME_MISSING = (
    "Xatırlatma vaxtını görmədim. Məsələn: sabah saat 9-da zəng etməyi xatırlat."
)
REMINDER_DISCARDED = "Təklif ləğv edildi. Heç nə yadda saxlanılmadı."
REMINDER_NONE = "Aktiv xatırlatmanız yoxdur."
REMINDER_LIST_HEADER = "Xatırlatmalarınız: {count} ədəd"
REMINDER_LIST_ITEM = "{index}. {when} — {text}"
REMINDER_LIST_PAUSED = " (göndərilmə növbəsində)"
REMINDER_CANCELLED = "Xatırlatma ləğv edildi."
REMINDER_NOT_CANCELLABLE = "Bu xatırlatma artıq göndərilib və ya ləğv edilib."
REMINDER_DELIVERED = "Xatırlatma: {text}"

DATA_DELETE_CONFIRM = (
    "Şəxsi məlumatlarınız silinəcək:\n\n"
    "Söhbət qeydləri\nXatırlatmalar\nAçıq yadda saxlama sessiyaları\n\n"
    "Hesabınız və administrator qeydiyyatı saxlanılır. Bu əməliyyat geri qaytarıla bilməz."
)
DATA_DELETE_YES_BUTTON = "Bəli, silinsin"
DATA_DELETE_NO_BUTTON = "Xeyr"
DATA_DELETED = (
    "Şəxsi məlumatlarınız silindi.\n"
    "Söhbətlər: {conversations}\nXatırlatmalar: {reminders}\nSessiyalar: {sessions}"
)
DATA_DELETE_CANCELLED = "Silinmə əməliyyatı ləğv edildi."
DATA_DELETE_NO_REQUEST = (
    "Təsdiq gözlənilmir. Zəhmət olmasa /verilerimi_sil əmrini yenidən göndərin."
)

REFERENCE_IMAGE_QUEUED = (
    "{code} məhsulu üçün şəkil təsdiqi gözləməyə götürüldü. "
    "Yadda saxlamağı tamamlamaq üçün /saxla, imtina etmək üçün /cancel göndərin."
)
REFERENCE_IMAGE_ALREADY_SAVED = (
    "Bu şəkil artıq {code} məhsulu üçün təsdiqlənib. Yeni əlaqə yaradılmadı."
)
REFERENCE_IMAGE_PRODUCT_NOT_FOUND = (
    "Belə məhsul tapılmadı: {code}. Yeni məhsul avtomatik yaradılmır; təsdiq gözləməyə götürülmədi."
)
REFERENCE_IMAGE_AMBIGUOUS = (
    "Mesajda bir neçə məhsul kodu göründü. Şəkli təsdiqləmək üçün yalnız bir kod göndərin; "
    "təsdiq gözləməyə götürülmədi."
)
REFERENCE_IMAGE_CONFLICT = (
    "Ziddiyyət: bu şəkil artıq {code} məhsulu ilə təsdiqlənib. Mövcud əlaqə dəyişdirilmədi; "
    "təsdiq gözləməyə götürülmədi."
)
REFERENCE_IMAGE_NO_PICTURE = (
    "Bu mesajda şəkil tapılmadı; təsdiq gözləməyə götürülmədi. "
    "Şəkili kodla birlikdə yenidən göndərin."
)
REFERENCE_IMAGE_NO_SESSION = (
    "Bu şəkil üçün açıq yadda saxlama sessiyası yoxdur; təsdiq gözləməyə götürülmədi."
)
REFERENCE_IMAGE_SAVE_CONFLICT = (
    "Yadda saxlama dayandırıldı: bu şəkil artıq {code} məhsulu ilə təsdiqlənib. "
    "Sessiyada heç nə yadda saxlanılmadı; /cancel ilə imtina edə bilərsiniz."
)
REFERENCE_IMAGE_UNREADABLE = (
    "Göndərilən şəkil oxunmadı; yadda saxlama dayandırıldı və heç nə yadda saxlanılmadı. "
    "Zəhmət olmasa şəkili yenidən göndərin."
)
REFERENCE_IMAGE_LINKED_NOTE = "\nTəsdiqlənmiş şəkil əlaqələri: {count}"

REFERENCE_REVOKE_USAGE = "Şəkil təsdiqini ləğv etmək üçün məhsul kodunu yazın: /leqv_et KA45"
REFERENCE_REVOKE_CONFIRM = (
    "{code} məhsulu üçün təsdiqlənmiş nümunə şəkillər ləğv edilsin? Ləğv edilən təsdiq tarixçədə "
    "qalır və şəkil faylları silinmir."
)
REFERENCE_REVOKE_YES_BUTTON = "Bəli, ləğv et"
REFERENCE_REVOKE_NO_BUTTON = "Xeyr"
REFERENCE_REVOKE_DONE = (
    "{code} məhsulu üzrə {count} şəkil təsdiqi ləğv edildi. Şəkillər və sübut tarixçəsi saxlanılır."
)
REFERENCE_REVOKE_NOTHING = (
    "{code} məhsulu üçün aktiv təsdiqlənmiş şəkil yoxdur; heç nə dəyişdirilmədi."
)
REFERENCE_REVOKE_CANCELLED = "Şəkil təsdiqinin ləğvi dayandırıldı. Heç nə dəyişdirilmədi."
REFERENCE_REVOKE_NO_REQUEST = (
    "Ləğv təsdiqi gözlənilmir. Zəhmət olmasa /leqv_et əmrini məhsul kodu ilə yenidən göndərin."
)
