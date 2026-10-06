"""Azerbaijani Telegram message catalog for Phase 2."""

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
SOURCE_FILE_UNAVAILABLE = "Mənbə faylı tapılmadı və ya hazırda göndərilə bilmir."
SOURCE_FILE_AMBIGUOUS = "Bir neçə fayl uyğundur. Mənbə nömrəsini qeyd edin."
WEB_SOURCE_UNAVAILABLE = "Son cavabda uyğun internet linki tapılmadı."
EXTRACTION_APPLIED = (
    "Yaddaşa yazma tamamlandı.\n\n"
    "Əlavə edildi: {applied}\nMövcud idi: {existing}\n"
    "Admin baxışı tələb edir: {admin}\nTətbiq olunmadı: {failed}"
)
