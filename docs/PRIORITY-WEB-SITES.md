# Prioritet internet saytları

Yalnız aktiv administrator saytların axtarış sırasını dəyişə bilər. Aktiv əməkdaş siyahıya baxa bilər.

Telegram əmrləri:

- `/saytlar` — cari prioritet domenlərin sırası
- `/sayt_elave knatank.az` — siyahının sonuna əlavə et
- `/sayt_sil knatank.az` — siyahıdan çıxar

Menyu yolu: əsas menyuda «🌐 Prioritet saytlar» bölməsi cari siyahını göstərir. Aktiv administrator «➕ Sayt əlavə et» / «➖ Sayt sil» düyməsi ilə bir mesajlıq domen daxiletməsini başladır; növbəti mətn mesajı domen kimi yoxlanılıb əlavə/sil edilir. Bu seçim bir dəfəlikdir: `/cancel`, menyuya qayıtmaq və ya başqa əmr/fayl/şəkil göndərmək onu ləğv edir. Məhdudiyyətlər (maksimum 10, yalnız əsas domen, audit) və icazə yoxlaması əmrlərlə eynidir; aktiv əməkdaş yalnız siyahıya baxır.

Əlavə edilən domenlər PostgreSQL-də saxlanılır, tətbiq restartından sonra da qalır. Siyahı maksimum 10 domen qəbul edir. Saytın **əsas domenini** yazın; URL yolları, portlar, IP ünvanları və axtarış operatorları qəbul edilmir. Yalnız administratorun təsdiqlənmiş hesabı siyahını dəyişə bilər və dəyişiklik auditdə qeyd olunur.

`/internet` və `/tamaxtar` internet hissəsi əvvəl bu saytlar üzrə sıra ilə axtarır. Ən azı iki istifadə edilə bilən mənbə tapılmayıbsa digər prioritet saytlara, sonra ümumi axtarışa keçir. Prioritet nəticələr birinci göstərilir. Nəticələrin uyğunluğu və tarix yoxlaması saxlanılır; prioritet saytlar avtomatik olaraq daxili və ya təsdiqli mənbə sayılmır. İnternet cavabı şirkətin daxili məlumatlarını bu saytlara göndərmir.

Staging sınağı: admin `/sayt_elave knatank.az`, sonra `/saytlar` və uyğun mövzuda `/internet ...` əmrini icra etsin. Domenin əlçatanlığı və indeksdə nəticələri olduğu əvvəlcədən fərz edilmir. Əgər bu domen üzrə nəticə alınmırsa ümumi internet axtarışı işləməlidir. Sonra `/sayt_sil knatank.az` ilə silmə və təkrar `/saytlar` yoxlanılır. Digər istifadəçi eyni əlavə/silmə əmrinə `icazə yoxdur` cavabı almalıdır. Eyni ssenari menyu yolu ilə də («🌐 Prioritet saytlar» → əlavə/sil → domen mesajı) yoxlanılmalıdır.

Şəkil axtarışı: «📷 Şəkillə axtar» → «İnternetdə axtar» seçimi foto üzrə Yandex əks-axtarışı edir. Nəticələr əvvəl prioritet saytlar, sonra `.az` domenləri, sonra digər mənbələr üzrə sıralanır (qrup daxilində provayder sırası qorunur). Nəticə panelində «⭐ Prioritet», «🔎 Digər» və «🌐 Hamısı» filtrləri eyni yüklənmiş nəticə üzərində yalnız görünüşü dəyişir — yeni Yandex sorğusu göndərilmir. Prioritet saytlar avtomatik təsdiqli mənbə sayılmır; şəkil uyğunluğu məhsul kimliyini, qiyməti və ya stoku təsdiqləmir.
