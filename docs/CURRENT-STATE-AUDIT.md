# FTMT — Joriy holat auditi

**Sana:** 2026-09-27
**Asos:** joriy ishchi daraxt, test natijalari va lokal health-check. Repozitoriyada avvaldan mavjud o‘zgarishlar saqlandi; bu auditni bitta commitga bog‘lab bo‘lmaydi.

## Tekshirilgan holat

- Lokal admin sayti 127.0.0.1:8000 da ishlayapti: /health va /admin/login HTTP 200; himoyalangan /admin/mfy/import manzili login sahifasiga 303 yo‘naltiradi.
- `app.main` Telegram bot jarayoni va `uvicorn` admin jarayoni ishlayapti. Bot polling holati fuqaroga test xabari yubormasdan tekshirildi.
- Lokal bazadagi MFY katalogida 58 ta faol yozuv bor. Barchasi hokimlik rasmiy tasdig‘igacha vaqtinchalik; 57 ta qo‘shildi, mavjud Bahor yozuvi yangilandi, ro‘yxatda yo‘q 7 demo MFY o‘chirilmay nofaol qilindi.
- Bot sozlamasida MFY va aniq manzil majburiy qilib saqlandi. Ishlayotgan bot sozlamalarni har 5 soniyada bazadan yangilaydi.
- Fuqaroga ko‘rinadigan MFY va murojaat yo‘nalishi matnlaridan ichki “DEMO” ogohlantirishi olib tashlandi; uch til uchun DB override va audit yozuvi qo‘shildi. Kategoriya/idora yozuvlarining o‘zi admin panelda vaqtinchalik maqomini saqlaydi.
- Lokal Alembic bazasi 5e09b24cbfe7 head holatida.
- Haqiqiy Telegram update mazmuni ochilmadi va fuqaroga test xabari yuborilmadi.
- So‘nggi full suite: **82 test o‘tdi**, PTB ConversationHandler sozlamasi bo‘yicha 3 ta ogohlantirish bor.
- Production’ga deploy qilinmadi.

## Amaldagi imkoniyatlar

- Fuqaro oqimi: uz/ru interfeys tanlash, rozilik, aloqa va murojaat maydonlari, hudud/toifa, fayl, preview, yuborish va tracking raqami. Pasport va tug‘ilgan sana majburiy yig‘ilmaydi; ularni bot sozlamasidan yoqish ham bloklangan.
- Fuqaro murojaatlari ro‘yxati va holat kuzatuvi; yakuniy javobga “hal bo‘ldi / qayta ko‘rib chiqilsin” tugmalari bor.
- Xodim qo‘shimcha ma’lumot so‘rashi mumkin; bot javobni faqat murojaat egasidan qabul qiladi, ixtiyoriy bitta ruxsatli faylni murojaatga bog‘laydi va mas’ul ijrochiga mazmunsiz xabar yuboradi.
- Admin panel: login, rollar, xodimlar, yo‘nalish/idora/MFY kataloglari, murojaatni biriktirish, status/javob, ichki izoh, faylga ruxsatli kirish, audit va arxiv.
- Bot sozlamalari bazada draft → preview → publish tartibida saqlanadi; audit va oldingi versiyaga qaytarish bor.
- Muddat eslatmalari va eskalatsiya navbatga qo‘yiladi. Xabarnoma qayta urinishlari tanaffus bilan bajariladi. Muddat uzaytirilganda fuqaro xabari muvaffaqiyatli yuborilgandagina uzaytirish yozuvida yetkazilgan deb belgilanadi.
- Super-admin bot xabarnomalari navbatini filtrlab ko‘radi; xabar matni yashiriladi, Telegram ID qisqartiriladi. Faqat yakuniy xatoni jami 10 urinish chegarasigacha qayta navbatga qo‘yish mumkin; sabab va admin auditga yoziladi.
- Admin sessiya tokenlari yangi kirishda bazada SHA-256 ko‘rinishida saqlanadi; eski faol sessiyalar keyingi muvaffaqiyatli ishlatilganda hashga o‘tadi. Admin sahifalari uchun no-store header kodi va testlari qo‘shilgan.
- Foydalanuvchi yuborgan 58 MFY hujjatidan faqat mahalla nomlari olindi va lokal bot katalogiga audit bilan import qilindi. Paneldan oxirgi importni qaytarish mumkin. Hujjatlardagi xodim ma’lumotlari olinmadi.

## Hali bajarilishi yoki tasdiqlanishi kerak

- Fuqaro bilan Telegram’dagi to‘liq oqim synthetic chatda sinovdan o‘tkazilmadi; ishlayotgan polling haqiqiy yangilanishlarni olishi mumkin.
- MFY nomlari foydalanuvchi yuborgan hujjatlardan ko‘chirilgan nomzod ro‘yxatidir; bu hujjatlar Hokimlik tasdiqlagan to‘liq ro‘yxat sifatida tasdiqlanmagan. Kirillcha/ruscha shakllar transliteratsiya qilingan. Hokimlik rasmiy ro‘yxatni va tarjimalarni tasdiqlashi kerak. Yo‘nalish va idora ro‘yxatlari ham provisional.
- Rozilik matni, ma’lumotni saqlash muddati, Telegram orqali ishlov berishning huquqiy asosi, rasmiy muddatlar va favqulodda aloqa matnlari mas’ul yurist/hokimlik tomonidan tasdiqlanishi kerak.
- PostgreSQL uchun migration va production konfiguratsiyasi bor, lekin bu ishda PostgreSQL bazasiga qarshi migratsiya/test bajarilmadi.
- `python -m compileall -q app tests` va `git diff --check` muvaffaqiyatli; Ruff o‘rnatilmagani sababli lint tekshiruvi bajarilmadi.
- Manzilning kamida 8 belgili validatsiyasi source va testlarda bor. Python jarayoni kodni avtomatik qayta yuklamaydi; joriy botga majburiy manzil sozlamasi DB’dan dinamik yetib boradi, koddagi so‘nggi validatsiya esa jarayon yangilangandan keyin qo‘llanadi.
- Telegram pending update’lari ochilmadi va fuqaroga test xabari yuborilmadi. Bot jarayonini qayta ishga tushirishdan oldin haqiqiy murojaat update’lari bor-yo‘qligini muvofiqlashtirish kerak.

## Xavfsizlik chegaralari

Haqiqiy murojaat matnlari, telefonlar, fayllar, tokenlar yoki parollar testga, logga yoki javobga kiritilmadi. UI tekshiruvlari synthetic ma’lumot bilan bajarildi. Rasmiy katalog va huquqiy ma’lumotlarsiz production ishlatishga tayyor deb hisoblanmaydi.
