# Prioritet internet saytları

Yalnız aktiv administrator saytların axtarış sırasını dəyişə bilər. Aktiv əməkdaş siyahıya baxa bilər.

Telegram əmrləri:

- `/saytlar` — cari prioritet domenlərin sırası
- `/sayt_elave knatank.az` — siyahının sonuna əlavə et
- `/sayt_sil knatank.az` — siyahıdan çıxar

Əlavə edilən domenlər PostgreSQL-də saxlanılır, tətbiq restartından sonra da qalır. Siyahı maksimum 10 domen qəbul edir. Saytın **əsas domenini** yazın; URL yolları, portlar, IP ünvanları və axtarış operatorları qəbul edilmir. Yalnız administratorun təsdiqlənmiş hesabı siyahını dəyişə bilər və dəyişiklik auditdə qeyd olunur.

`/internet` və `/tamaxtar` internet hissəsi əvvəl bu saytlar üzrə sıra ilə axtarır. Ən azı iki istifadə edilə bilən mənbə tapılmayıbsa digər prioritet saytlara, sonra ümumi axtarışa keçir. Prioritet nəticələr birinci göstərilir. Nəticələrin uyğunluğu və tarix yoxlaması saxlanılır; prioritet saytlar avtomatik olaraq daxili və ya təsdiqli mənbə sayılmır. İnternet cavabı şirkətin daxili məlumatlarını bu saytlara göndərmir.

Staging sınağı: admin `/sayt_elave knatank.az`, sonra `/saytlar` və uyğun mövzuda `/internet ...` əmrini icra etsin. Domenin əlçatanlığı və indeksdə nəticələri olduğu əvvəlcədən fərz edilmir. Əgər bu domen üzrə nəticə alınmırsa ümumi internet axtarışı işləməlidir. Sonra `/sayt_sil knatank.az` ilə silmə və təkrar `/saytlar` yoxlanılır. Digər istifadəçi eyni əlavə/silmə əmrinə `icazə yoxdur` cavabı almalıdır.
