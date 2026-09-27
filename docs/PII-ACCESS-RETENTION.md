# FTMT: shaxsiy ma’lumotlarga kirish va saqlash

Bu hujjat koddagi himoya va ochiq qarorlarni qayd etadi. U hokimlikning qonuniy asosini yoki saqlash muddatini belgilamaydi.

## Ma’lumotlar

- Telegram foydalanuvchi IDsi, interfeys tili va rozilik yozuvi.
- Fuqaro ismi va telefon raqami, hudud/manzil izohi, murojaat matni, yo‘nalish, til va ish tarixi.
- Telegram attachment identifikatori; ayrim deploylarda ixtiyoriy lokal fayl yo‘li ham bo‘lishi mumkin.
- Xodim akkaunti, roli, idora biriktiruvi, sessiya va audit voqealari.
- Pasport raqami va tug‘ilgan sana bot oqimida majburiy emas; bot sozlamasidan bularni faollashtirish rad etiladi.

Murojaat matni, ichki izoh va attachment ichida fuqaroning sezgir ma’lumoti bo‘lishi mumkin. Ism, telefon, murojaat matni/fayli, token yoki parolni application log yoki audit metadata’ga kiritmang.

## Kirish nazorati

- Super-admin va tuman nazoratchisi tuman doirasidagi murojaatlarni ko‘radi.
- Idora rahbari o‘z idorasiga faol biriktirilgan murojaatlarni ko‘radi.
- Ijrochi faqat o‘ziga faol biriktirilgan murojaatlarni ko‘radi.
- Auditor o‘qish huquqiga ega; o‘zgartirishga ruxsat yo‘q.
- Ichki izoh va attachment ochish murojaat ruxsati bilan tekshiriladi; attachment ochilishi auditga yoziladi.
- Bot sirlarini admin panelda ko‘rsatmaydi.
- Yangi admin cookie tokenlari bazada SHA-256 hash ko‘rinishida turadi. Eski ochiq tokenlar keyingi muvaffaqiyatli so‘rovda hashga aylantiriladi.
- Admin URL javoblariga private, no-store header’i qo‘yiladi. Hozirgi lokal server qayta ishga tushmagani uchun so‘nggi middleware o‘zgarishi faol processda hali yuklanmagan.

Rollar va vakolatlar hokimlikning delegatsiya qoidalari bilan tasdiqlanishi kerak.

## Saqlash va o‘chirish

Hozircha hech qanday saqlash muddati belgilanmagan. Arxivlash qaytariladigan amal bo‘lib, murojaat va audit tarixini saqlaydi; bu o‘chirish yoki retention siyosati emas. Productiondan oldin mas’ullar quyidagilarni yozma tasdiqlashi kerak:

1. Murojaat, rozilik, attachment, ichki izoh, audit va backup ma’lumotlarini saqlash muddati.
2. O‘chirish, legal hold, fuqaro so‘rovi va eksportga ruxsat berish hamda auditlash tartibi.
3. Backup’ga kim kira olishi va muddati tugaganda qanday yo‘q qilinishi.
4. Ushbu davlat xizmati jarayonida Telegram Bot API’dan foydalanish va rozilik matni.
5. Hozir so‘ralayotgan ma’lumotlarning maqsadi va qonuniy asosi.

Tasdiqlar olinmaguncha development/testga haqiqiy murojaat nusxalanmasin, keraksiz shaxsiy ma’lumot so‘ralmasin va retention xususiyati bor deb da’vo qilinmasin.
