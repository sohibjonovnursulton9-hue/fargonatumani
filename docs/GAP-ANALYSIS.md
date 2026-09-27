# FTMT — Talablar bo‘yicha holat va ochiq ishlar

**Sana:** 2026-09-27. Holat joriy kod va testlar bo‘yicha yozildi; rasmiy hokimlik ma’lumotlari o‘rniga demo katalog ishlatilishi mumkin.

## Kodda bajarilgan

| Yo‘nalish | Holat |
|---|---|
| Admin login, rolga asoslangan ruxsat, CSRF va parol almashtirish | Mavjud; xodim yaratish/tahrirlash va oxirgi super-adminni saqlash qoidalari testlangan |
| Admin UI | Login, umumiy navigatsiya, murojaatlar, dashboard/statistika, bot sozlamalari, audit, xodimlar va katalog sahifalari bor; mobil/desktop ko‘rinishi synthetic ma’lumot bilan tekshirilgan |
| Katalog boshqaruvi | Yo‘nalish, idora, ularning bog‘lanishi va MFY uchun panel oqimlari mavjud |
| MFY manba importi | 58 ta foydalanuvchi yuborgan hujjat nomlari lokal bot bazasiga audit bilan import qilingan; barchasi hokimlik tasdig‘igacha vaqtinchalik. Paneldan oxirgi importni qaytarish bor |
| Bot mazmuni | Uz lotin, uz kirill va rus tilidagi matnlar, forma maydonlari, ish vaqti/aloqa, e’lon va texnik tanaffus kabi sozlamalar DB’da; o‘zgarish draft-preview-publish-audit-rollback oqimidan o‘tadi |
| Murojaat ko‘rigi | Mas’ul idora/xodim biriktirish, holat, rasmiy javob, ichki izoh, attachment access, audit va arxiv mavjud |
| Fuqaro javobi | Hal bo‘lganini tasdiqlash yoki qayta ochish callback’i murojaat egasini tekshiradi |
| Qo‘shimcha ma’lumot | Admin so‘rovi, fuqaroning matn/bitta fayl javobi, egani tekshirish, audit va ijrochiga mazmunsiz bildirishnoma mavjud |
| Muddat/xabarnoma | SLA tanlash, eslatma/eskalatsiya, fuqaro tasdig‘i eslatmasi, retry backoff va muddat uzaytirish xabari/outbox mavjud |
| Xabarnoma nazorati | Super-admin navbatni ko‘radi va faqat yakuniy xatoni audit bilan qayta navbatga qo‘yadi; xabar matni yashiriladi va urinishlar jami 10 bilan cheklanadi |
| Shaxsiy ma’lumot himoyasi | Pasport/tug‘ilgan sana bot formasida majburiy emas; admin sessiyalari hash; admin sahifalarida cache taqiqlash kodi mavjud |

## Hali kodda yakunlanmagan yoki alohida tasdiq talab qiladi

### Mahsulot oqimlari

1. Rasmiy attachment retention, virus tekshiruvi, backup retention/restore jarayoni hali belgilanmagan.
2. Murojaat eksporti va ma’lumotni o‘chirish/saqlash so‘rovlarining hokimlik tasdiqlagan jarayoni aniqlanmagan.
3. MFA ustuni bor, ammo MFA autentifikatsiya oqimi joriy qilinmagan.

### Hokimlik/yurist tasdig‘i

- Tasdiqlangan to‘liq MFY, idora, yo‘nalish ro‘yxati va yo‘naltirish qoidalari.
- Har bir murojaat turi bo‘yicha qonuniy muddat, uzaytirish vakolati va eskalatsiya tartibi.
- Rozilik va maxfiylik matni; Telegram’dan foydalanishning huquqiy asosi va shaxsiy ma’lumotni saqlash muddati.
- Fuqaroga ko‘rsatiladigan rasmiy aloqa va favqulodda vaziyat ko‘rsatmalari.
- Xodim rollari va idoralar kesimidagi ko‘rish/o‘zgartirish vakolatlari.

### Ishga tushirish/tekshirish

- Telegram botning `app.main` jarayoni ishlayapti va polling qilishi mumkin. Ushbu tekshiruvda yangi bot jarayoni yoki fuqaroga test xabari yuborilmadi, chunki Telegram update’lari haqiqiy murojaat bo‘lishi mumkin. Docker CLI mavjud emas.
- Yuborilgan MFY hujjatlari Hokimlik tasdiqlagan to‘liq ro‘yxat ekani aniqlanmagan; kirillcha/ruscha nomlar avtomatik transliteratsiya qilingan va rasmiy idoralar tomonidan tekshirilishi shart.
- PostgreSQL migratsiyasi, load testi, backup-restore mashqi va mustaqil xavfsizlik auditi bajarilmagan.
- Lokal admin server `GET /health` va `GET /admin/login` uchun 200, himoyalangan MFY import sahifasi uchun login redirect qaytardi. Fuqaro murojaati bilan admin CRUD/UI to‘liq end-to-end ko‘rikdan o‘tkazilmadi.
- Manzil va MFY faol bot sozlamasida majburiy. Kamida 8 belgili manzil validatsiyasi testdan o‘tdi; Python jarayoni avtomatik reload qilmagani uchun ishga tushgan bot jarayonidagi kod yangilanishi alohida restartni talab qiladi, majburiy sozlama esa DB’dan dinamik yangilanadi.
- Fuqaroga ko‘rinadigan MFY/yo‘nalish promptlaridan “DEMO” ogohlantirishi olib tashlandi; DB matnlari uz/uz_cyrillic/ru bo‘yicha audit bilan yangilandi. Kategoriya va idora katalogi hokimlik tasdig‘igacha admin panelda vaqtinchalik deb ko‘rsatiladi.
- Full test suite: 82 passed; 3 PTB ogohlantirishi. `python -m compileall -q app tests` va `git diff --check` muvaffaqiyatli. Ruff o‘rnatilmagan. Production deploy qilinmadi.
