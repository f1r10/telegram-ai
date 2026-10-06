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
WELCOME = "Xoş gəlmisiniz. Hesabınız aktivdir."
PENDING_STATUS = "Hesabınız administrator təsdiqini gözləyir."
WHOAMI_UNKNOWN = "Sizin üçün qeydiyyatdan keçmiş aktiv hesab tapılmadı."
CANCELLED = "Parol daxil etmə prosesi ləğv edildi."
TEMPORARY_ERROR = "Müvəqqəti texniki xəta baş verdi. Zəhmət olmasa sonra yenidən cəhd edin."
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
    "/baza <sual> — daxili bilik bazasından cavab almaq\n"
    "/internet <sual> — internetdən müvəqqəti cavab almaq (yaddaşa yazılmır)\n"
    "/tamaxtar <nömrə> — son cavabın mənbə faylını almaq\n"
    "/saxla — göndərilən faylları və təlimatları yaddaşa köçürmək\n"
    "/hatirlatmalar — aktiv xatırlatmalarınızı görmək\n"
    "/cancel — açıq sessiyanı ləğv etmək\n"
    "/verilerimi_sil — şəxsi məlumatlarınızı silmək\n"
    "/whoami — hesabınızın statusunu görmək\n"
    "/help — bu siyahı\n\n"
    "Xatırlatma üçün sadə mesaj yazın, məsələn: sabah saat 9-da Əliyə zəng etməyi xatırlat. "
    "Vaxtı başa düşəndə sizdən təsdiq istəyəcəyik; təsdiq etmədən heç nə saxlanılmır."
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
