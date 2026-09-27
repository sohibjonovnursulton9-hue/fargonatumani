"""
Internationalization (i18n) — all user-facing text in two interface languages
and three complaint languages.

Interface languages: O'zbekcha (uz), Русский (ru)
Complaint languages: O'zbek Latin (uz_latin), Ўзбек Кирилл (uz_cyrillic), Русский (ru)

IMPORTANT: Legal/consent texts are PLACEHOLDERS.
They MUST be reviewed and approved by a qualified lawyer before production use.
"""

from __future__ import annotations

from typing import Any

# ============================================================================
# Interface texts (bot menus, buttons, instructions)
# ============================================================================

TEXTS: dict[str, dict[str, str]] = {
    # --- Start & Language Selection ---
    "welcome": {
        "uz": "🏛 *Farg'ona tuman hokimligi murojaatlar tizimi*\n\n"
              "Xush kelibsiz! Quyidagi menyudan kerakli bo‘limni tanlang:",
        "ru": "🏛 *Система обращений хокимията Ферганского района*\n\n"
              "Добро пожаловать! Выберите нужный раздел в меню ниже:",
    },
    "language_selected": {
        "uz": "✅ Interfeys tili: O'zbekcha",
        "ru": "✅ Язык интерфейса: Русский",
    },

    # --- Consent ---
    # ⚠️ PLACEHOLDER: Must be drafted by a lawyer. See LEGAL-OPEN-QUESTIONS.md
    "consent_text": {
        "uz": (
            "📋 *Shaxsiy ma'lumotlarni qayta ishlashga rozilik*\n\n"
            "⚠️ _Bu matn hali rasmiy yurist tomonidan tasdiqlanmagan. "
            "Ishga tushirishdan oldin huquqiy ko'rib chiqish talab etiladi._\n\n"
            "Ushbu tizimdan foydalanish orqali siz quyidagilarga rozilik bildirasiz:\n"
            "• Siz kiritgan shaxsiy ma'lumotlar (F.I.Sh., telefon raqami, manzil) "
            "murojaatingizni ko'rib chiqish maqsadida qayta ishlanadi.\n"
            "• Ma'lumotlaringiz faqat mas'ul idora xodimlariga ko'rsatiladi.\n"
            "• Murojaatingiz va unga tegishli hujjatlar qonunda belgilangan muddat "
            "davomida saqlanadi.\n\n"
            "Davom etish uchun quyidagi tugmani bosing:"
        ),
        "ru": (
            "📋 *Согласие на обработку персональных данных*\n\n"
            "⚠️ _Данный текст ещё не утверждён юристом. "
            "Перед запуском требуется юридическая проверка._\n\n"
            "Используя данную систему, вы соглашаетесь на следующее:\n"
            "• Ваши персональные данные (ФИО, номер телефона, адрес) "
            "будут обработаны для рассмотрения вашего обращения.\n"
            "• Ваши данные будут доступны только уполномоченным сотрудникам.\n"
            "• Ваше обращение и приложенные документы хранятся в течение "
            "срока, установленного законодательством.\n\n"
            "Для продолжения нажмите кнопку ниже:"
        ),
    },
    "consent_accept_btn": {
        "uz": "✅ Roziman",
        "ru": "✅ Согласен(на)",
    },
    "consent_decline_btn": {
        "uz": "❌ Rad etaman",
        "ru": "❌ Не согласен(на)",
    },
    "consent_declined": {
        "uz": "❌ Siz rozilik bermadingiz. Tizimdan foydalanish uchun rozilik talab etiladi.\n"
              "/start buyrug'i bilan qayta boshlashingiz mumkin.",
        "ru": "❌ Вы не дали согласие. Для использования системы требуется согласие.\n"
              "Вы можете начать заново командой /start.",
    },
    "consent_accepted": {
        "uz": "✅ Roziligingiz qabul qilindi.",
        "ru": "✅ Ваше согласие принято.",
    },

    # --- Main Menu ---
    "main_menu": {
        "uz": "📋 *Asosiy menyu*\n\nTanlang:",
        "ru": "📋 *Главное меню*\n\nВыберите:",
    },
    "btn_new_complaint": {
        "uz": "📝 Murojaat yuborish",
        "ru": "📝 Подать обращение",
    },
    "btn_my_complaints": {
        "uz": "📂 Murojaatlarim",
        "ru": "📂 Мои обращения",
    },
    "btn_help": {
        "uz": "❓ Yordam / Aloqa",
        "ru": "❓ Помощь / Контакты",
    },
    "btn_change_language": {
        "uz": "🌐 Tilni o'zgartirish",
        "ru": "🌐 Сменить язык",
    },

    # --- Complaint Language Selection ---
    "select_complaint_language": {
        "uz": "🌐 *Murojaat tilini tanlang:*\n\n"
              "Bu tilda murojaatingiz matnini yozasiz va javob olasiz.",
        "ru": "🌐 *Выберите язык обращения:*\n\n"
              "На этом языке вы напишете текст обращения и получите ответ.",
    },
    "btn_lang_uz_latin": {
        "uz": "O'zbek tili (lotin)",
        "ru": "Узбекский (латиница)",
    },
    "btn_lang_uz_cyrillic": {
        "uz": "Ўзбек тили (кирилл)",
        "ru": "Узбекский (кириллица)",
    },
    "btn_lang_ru": {
        "uz": "Русский язык",
        "ru": "Русский язык",
    },

    # --- Complaint Type ---
    "select_complaint_type": {
        "uz": "📌 *Murojaat turini tanlang:*",
        "ru": "📌 *Выберите тип обращения:*",
    },
    "btn_ariza": {
        "uz": "📋 Ariza (iltimosnoma)",
        "ru": "📋 Заявление",
    },
    "btn_shikoyat": {
        "uz": "⚠️ Shikoyat",
        "ru": "⚠️ Жалоба",
    },
    "btn_taklif": {
        "uz": "💡 Taklif",
        "ru": "💡 Предложение",
    },

    # --- Personal Info ---
    "enter_full_name": {
        "uz": "👤 *F.I.Sh. ni to'liq kiriting:*\n\n"
              "Misol: Karimov Anvar Baxtiyor o'g'li",
        "ru": "👤 *Введите ваше полное ФИО:*\n\n"
              "Пример: Каримов Анвар Бахтиёрович",
    },
    "invalid_full_name": {
        "uz": "❌ F.I.Sh. noto'g'ri formatda. Kamida ism va familiya kiriting.",
        "ru": "❌ Неверный формат ФИО. Введите минимум имя и фамилию.",
    },
    "enter_phone": {
        "uz": "📱 *Telefon raqamingizni kiriting:*\n\n"
              "Formatda: +998XXXXXXXXX\n"
              "Telefon egasini tekshirish uchun pastdagi 'Telefon raqamni ulashish' tugmasidan foydalaning.",
        "ru": "📱 *Введите ваш номер телефона:*\n\n"
              "Формат: +998XXXXXXXXX\n"
              "Чтобы подтвердить владельца номера, используйте кнопку 'Поделиться контактом'.",
    },
    "btn_share_contact": {
        "uz": "📲 Telefon raqamni ulashish",
        "ru": "📲 Поделиться контактом",
    },
    "invalid_phone": {
        "uz": "❌ Telefon raqam noto'g'ri formatda. +998 bilan boshlanuvchi 12 raqamli bo'lishi kerak.",
        "ru": "❌ Неверный формат номера. Должен начинаться с +998 и содержать 12 цифр.",
    },
    "own_contact_required": {
        "uz": "Iltimos, aynan o'zingizning raqamingizni Telegram'dagi 'Telefon raqamni ulashish' tugmasi orqali yuboring.",
        "ru": "Отправьте именно свой номер с помощью кнопки Telegram «Поделиться контактом».",
    },
    "enter_birth_date": {
        "uz": "📅 *Tug'ilgan yili, oyi va kuni:*\n\nSanani quyidagi shaklda kiriting. Masalan: 01.01.1990\n\n_Bu maydon ixtiyoriy. O'tkazib yuborish mumkin._",
        "ru": "📅 *Год, месяц и день рождения:*\n\nНапример: 01.01.1990\n\n_Это поле необязательное. Можно пропустить._",
    },
    "enter_passport": {
        "uz": "🛂 *Pasport seriya raqami:*\n\nPasport seriyangiz va raqamini kiriting. Masalan: AA1234567\n\n_Bu maydon ixtiyoriy. O'tkazib yuborish mumkin._",
        "ru": "🛂 *Серия и номер паспорта:*\n\nНапример: AA1234567\n\n_Это поле необязательное. Можно пропустить._",
    },
    "btn_skip": {
        "uz": "⏭ O'tkazib yuborish",
        "ru": "⏭ Пропустить",
    },

    # --- Address ---
    "select_mfy": {
        "uz": "🏘 *Mahallangizni tanlang:*",
        "uz_cyrillic": "🏘 *Маҳаллангизни танланг:*",
        "ru": "🏘 *Выберите вашу махаллю:*",
    },
    "enter_address_detail": {
        "uz": "📍 *Aniq manzilingizni kiriting:*\n\nKo‘cha, uy raqami va mo‘ljalni yozing. Bu maydon majburiy.",
        "uz_cyrillic": "📍 *Аниқ манзилингизни киритинг:*\n\nКўча, уй рақами ва мўлжални ёзинг. Бу майдон мажбурий.",
        "ru": "📍 *Введите точный адрес:*\n\nУкажите улицу, номер дома и ориентир. Это обязательное поле.",
    },
    "invalid_address_detail": {
        "uz": "❌ To‘liq manzilni kamida 8 ta belgi bilan kiriting: ko‘cha nomi va uy raqami.",
        "uz_cyrillic": "❌ Тўлиқ манзилни камида 8 та белги билан киритинг: кўча номи ва уй рақами.",
        "ru": "❌ Укажите полный адрес (не менее 8 символов): название улицы и номер дома.",
    },

    # --- Category & Organization ---
    "select_category": {
        "uz": "📁 *Murojaat sohasini tanlang:*",
        "uz_cyrillic": "📁 *Мурожаат соҳасини танланг:*",
        "ru": "📁 *Выберите тему обращения:*",
    },

    # --- Complaint Content ---
    "enter_title": {
        "uz": "📌 *Murojaat sarlavhasini kiriting:*\n\n"
              "Muammo/masalani qisqacha tavsiflang (100 belgigacha).",
        "ru": "📌 *Введите заголовок обращения:*\n\n"
              "Кратко опишите проблему/вопрос (до 100 символов).",
    },
    "enter_description": {
        "uz": "📝 *Murojaat matnini batafsil yozing:*\n\n"
              "Muammo, holat va so'rovingizni to'liq tavsiflang.",
        "ru": "📝 *Подробно опишите ваше обращение:*\n\n"
              "Опишите проблему, ситуацию и ваш запрос полностью.",
    },
    "ask_attachments": {
        "uz": "📎 *Fayl biriktirmoqchimisiz?*\n\n"
              "Foto, video yoki hujjat yuboring.\n"
              "Tayyor bo'lsangiz, \"Davom etish\" tugmasini bosing.\n\n"
              "Ruxsat etilgan formatlar: jpg, png, pdf, doc, docx, mp4\n"
              "Maksimal hajm: 20 MB",
        "ru": "📎 *Хотите прикрепить файл?*\n\n"
              "Отправьте фото, видео или документ.\n"
              "Когда закончите, нажмите «Продолжить».\n\n"
              "Разрешённые форматы: jpg, png, pdf, doc, docx, mp4\n"
              "Максимальный размер: 20 МБ",
    },
    "btn_skip_attachments": {
        "uz": "▶️ Davom etish",
        "ru": "▶️ Продолжить",
    },
    "attachment_saved": {
        "uz": "✅ Fayl qabul qilindi. Yana biriktiring yoki \"Davom etish\" bosing.",
        "ru": "✅ Файл принят. Прикрепите ещё или нажмите «Продолжить».",
    },
    "attachment_invalid": {
        "uz": "❌ Bu fayl turi qo'llab-quvvatlanmaydi yoki hajmi katta.",
        "ru": "❌ Этот тип файла не поддерживается или файл слишком большой.",
    },

    # --- Preview & Confirmation ---
    "preview_header": {
        "uz": "📋 *Murojaatingizni ko'rib chiqing:*\n\n",
        "ru": "📋 *Проверьте ваше обращение:*\n\n",
    },
    "preview_type": {
        "uz": "📌 *Turi:* {type}\n",
        "ru": "📌 *Тип:* {type}\n",
    },
    "preview_name": {
        "uz": "👤 *F.I.Sh.:* {name}\n",
        "ru": "👤 *ФИО:* {name}\n",
    },
    "preview_phone": {
        "uz": "📱 *Telefon:* {phone}\n",
        "ru": "📱 *Телефон:* {phone}\n",
    },
    "preview_address": {
        "uz": "📍 *Manzil:* {mfy}, {address}\n",
        "ru": "📍 *Адрес:* {mfy}, {address}\n",
    },
    "preview_category": {
        "uz": "📁 *Soha:* {category}\n",
        "ru": "📁 *Сфера:* {category}\n",
    },
    "preview_title": {
        "uz": "📌 *Sarlavha:* {title}\n",
        "ru": "📌 *Заголовок:* {title}\n",
    },
    "preview_text": {
        "uz": "📝 *Matn:* {text}\n",
        "ru": "📝 *Текст:* {text}\n",
    },
    "preview_attachments": {
        "uz": "📎 *Biriktirilgan fayllar:* {count} ta\n",
        "ru": "📎 *Прикреплённые файлы:* {count} шт.\n",
    },
    "preview_footer": {
        "uz": "\n_Yuborishdan oldin ma'lumotlarni tekshiring._",
        "ru": "\n_Проверьте данные перед отправкой._",
    },
    "btn_confirm_send": {
        "uz": "✅ Yuborish",
        "ru": "✅ Отправить",
    },
    "btn_edit": {
        "uz": "✏️ Tahrirlash",
        "ru": "✏️ Редактировать",
    },
    "btn_cancel": {
        "uz": "❌ Bekor qilish",
        "ru": "❌ Отменить",
    },

    # --- Success ---
    "complaint_submitted": {
        "uz": (
            "✅ *Murojaatingiz qabul qilindi!*\n\n"
            "📋 Tracking raqami: `{tracking_id}`\n"
            "📅 Qabul sanasi: {date}\n"
            "📌 Turi: {type}\n"
            "📁 Soha: {category}\n\n"
            "Arizangiz tizimga qabul qilindi va hozir saralashni kutmoqda. "
            "Mas'ul idora hali biriktirilmagan. Idora biriktirilganda uning "
            "nomini bot orqali alohida yuboramiz.\n\n"
            "Holatini \"Murojaatlarim\" bo'limidan tracking raqami bilan "
            "kuzatishingiz mumkin. Bu xabar tizim arizangizni qabul qilganini "
            "tasdiqlaydi; ko'rib chiqish muddati amaldagi tartib bo'yicha belgilanadi."
        ),
        "ru": (
            "✅ *Ваше обращение принято!*\n\n"
            "📋 Номер отслеживания: `{tracking_id}`\n"
            "📅 Дата приёма: {date}\n"
            "📌 Тип: {type}\n"
            "📁 Сфера: {category}\n\n"
            "Обращение принято системой и ожидает первичной сортировки. "
            "Ответственный орган пока не назначен. После назначения мы "
            "отправим вам название органа отдельным сообщением.\n\n"
            "Отслеживайте обращение по номеру в разделе «Мои обращения». "
            "Это сообщение подтверждает получение системой; срок рассмотрения "
            "определяется действующим порядком."
        ),
    },

    # --- My Complaints ---
    "no_complaints": {
        "uz": "📂 Sizda hali murojaatlar yo'q.",
        "ru": "📂 У вас пока нет обращений.",
    },
    "complaints_list_header": {
        "uz": "📂 *Murojaatlaringiz:*\n\n",
        "ru": "📂 *Ваши обращения:*\n\n",
    },
    "complaint_list_item": {
        "uz": "• `{tracking_id}` — {title}\n  📌 {status} | 📅 {date}\n\n",
        "ru": "• `{tracking_id}` — {title}\n  📌 {status} | 📅 {date}\n\n",
    },

    # --- Status Labels ---
    "status_draft": {"uz": "Qoralama", "ru": "Черновик"},
    "status_submitted": {"uz": "Qabul qilindi · saralash kutilmoqda", "ru": "Принято · ожидает сортировки"},
    "status_triage": {"uz": "Saralanmoqda", "ru": "Первичная сортировка"},
    "status_routed": {"uz": "Mas'ul idoraga yo'naltirildi", "ru": "Направлено в ответственный орган"},
    "status_in_progress": {"uz": "Bajarilmoqda", "ru": "В работе"},
    "status_waiting_for_citizen": {"uz": "Fuqarodan javob kutilmoqda", "ru": "Ожидание ответа гражданина"},
    "status_response_provided": {"uz": "Javob berildi", "ru": "Ответ предоставлен"},
    "status_implementation_reported": {"uz": "Bajarish xabar qilindi", "ru": "Выполнение отчитано"},
    "status_citizen_confirmation_pending": {"uz": "Fuqaro tasdiqlashi kutilmoqda", "ru": "Ожидание подтверждения"},
    "status_resolved": {"uz": "Hal qilindi ✅", "ru": "Решено ✅"},
    "status_reopened": {"uz": "Qayta ochildi", "ru": "Возобновлено"},
    "status_rejected": {"uz": "Rad etildi", "ru": "Отклонено"},
    "status_withdrawn": {"uz": "Qaytarib olindi", "ru": "Отозвано"},
    "status_referred_outside": {"uz": "Boshqa organga yuborildi", "ru": "Перенаправлено"},
    "status_out_of_scope": {"uz": "Vakolat doirasidan tashqari", "ru": "Вне компетенции"},

    # --- Complaint Type Labels ---
    "type_ariza": {"uz": "Ariza", "ru": "Заявление"},
    "type_shikoyat": {"uz": "Shikoyat", "ru": "Жалоба"},
    "type_taklif": {"uz": "Taklif", "ru": "Предложение"},

    # --- Citizen Confirmation ---
    "citizen_confirmation_ask": {
        "uz": (
            "📬 *Murojaatingiz bo'yicha javob berildi*\n\n"
            "Tracking: `{tracking_id}`\n\n"
            "Mas'ul idora murojaatingiz bo'yicha ish bajarilganini xabar qildi.\n\n"
            "❓ *Masalangiz hal bo'ldimi?*"
        ),
        "ru": (
            "📬 *По вашему обращению предоставлен ответ*\n\n"
            "Номер: `{tracking_id}`\n\n"
            "Ответственный орган сообщил о выполнении работ.\n\n"
            "❓ *Ваш вопрос решён?*"
        ),
    },
    "btn_yes_resolved": {
        "uz": "✅ Ha, hal bo'ldi",
        "ru": "✅ Да, решено",
    },
    "btn_no_not_resolved": {
        "uz": "❌ Yo'q, hal bo'lmadi",
        "ru": "❌ Нет, не решено",
    },
    "citizen_confirmed_resolved": {
        "uz": "✅ Rahmat! Murojaatingiz muvaffaqiyatli yakunlandi.",
        "ru": "✅ Спасибо! Ваше обращение успешно завершено.",
    },
    "citizen_not_resolved_ask_reason": {
        "uz": "📝 Iltimos, nima uchun masala hal bo'lmaganini yozing:",
        "ru": "📝 Пожалуйста, опишите, почему вопрос не решён:",
    },
    "citizen_reopened": {
        "uz": "📋 Murojaatingiz qayta ko'rib chiqish uchun yuborildi. Tracking: `{tracking_id}`",
        "ru": "📋 Ваше обращение отправлено на повторное рассмотрение. Номер: `{tracking_id}`",
    },
    "additional_info_prompt": {
        "uz": "📩 Iltimos, so‘ralgan qo‘shimcha ma’lumotni yozing yoki bitta rasm/hujjat yuboring. Faylga izohni caption’da yozishingiz mumkin.",
        "ru": "📩 Отправьте запрошенные сведения текстом или одним фото/документом. Комментарий к файлу можно добавить в подписи.",
    },
    "additional_info_received": {
        "uz": "✅ Qo‘shimcha ma’lumotingiz qabul qilindi va murojaatga biriktirildi.",
        "ru": "✅ Дополнительные сведения получены и прикреплены к обращению.",
    },
    "additional_info_invalid": {
        "uz": "Matn yoki ruxsat etilgan hajm va formatdagi bitta fayl yuboring.",
        "ru": "Отправьте текст или один файл допустимого размера и формата.",
    },
    "additional_info_expired": {
        "uz": "Bu murojaat uchun qo‘shimcha ma’lumot so‘rovi faol emas.",
        "ru": "Запрос дополнительных сведений по этому обращению уже не активен.",
    },
    "additional_info_button": {
        "uz": "📩 Ma’lumot yuborish",
        "ru": "📩 Отправить сведения",
    },

    # --- Duplicate Warning ---
    "duplicate_warning": {
        "uz": (
            "⚠️ *Sizda shunga o'xshash murojaat mavjud:*\n"
            "Tracking: `{tracking_id}` — {title}\n"
            "Status: {status}\n\n"
            "Nima qilmoqchisiz?"
        ),
        "ru": (
            "⚠️ *У вас есть похожее обращение:*\n"
            "Номер: `{tracking_id}` — {title}\n"
            "Статус: {status}\n\n"
            "Что вы хотите сделать?"
        ),
    },
    "btn_new_anyway": {
        "uz": "📝 Yangi murojaat yuborish",
        "ru": "📝 Подать новое обращение",
    },
    "btn_view_existing": {
        "uz": "📂 Mavjudini ko'rish",
        "ru": "📂 Посмотреть существующее",
    },

    # --- Help ---
    "help_text": {
        "uz": (
            "❓ *Yordam*\n\n"
            "Bu bot orqali siz Farg'ona tuman hokimligiga murojaat yuborishingiz mumkin.\n\n"
            "📝 *Murojaat yuborish* — yangi ariza, shikoyat yoki taklif yuboring\n"
            "📂 *Murojaatlarim* — yuborgan murojaatlaringiz holatini kuzating\n"
            "🌐 *Tilni o'zgartirish* — bot interfeysi tilini almashtiring\n\n"
            "📞 *Aloqa:*\n"
            "⚠️ _Quyidagi raqamlar PLACEHOLDER. Rasmiy raqamlar hokimlikdan "
            "tasdiqlanishi kerak._\n"
            "• Hokimlik: +998 XX XXX XX XX\n"
            "• Favqulodda holat: 112\n\n"
            "⚠️ *MUHIM:* Agar hayot yoki sog'liqqa xavf bo'lsa, darhol "
            "tez yordam (103), politsiya (102) yoki favqulodda xizmat (112) ga "
            "qo'ng'iroq qiling!"
        ),
        "ru": (
            "❓ *Помощь*\n\n"
            "Через этого бота вы можете подать обращение в хокимият "
            "Ферганского района.\n\n"
            "📝 *Подать обращение* — подайте заявление, жалобу или предложение\n"
            "📂 *Мои обращения* — отслеживайте статус ваших обращений\n"
            "🌐 *Сменить язык* — переключите язык интерфейса бота\n\n"
            "📞 *Контакты:*\n"
            "⚠️ _Приведённые номера являются ЗАГЛУШКОЙ. Официальные номера "
            "должны быть утверждены хокимиятом._\n"
            "• Хокимият: +998 XX XXX XX XX\n"
            "• Экстренная помощь: 112\n\n"
            "⚠️ *ВАЖНО:* При угрозе жизни или здоровью немедленно звоните в "
            "скорую (103), полицию (102) или экстренную службу (112)!"
        ),
    },

    # --- Rate Limit ---
    "rate_limit_exceeded": {
        "uz": "⚠️ Siz bugun {max} ta murojaat yuborish limitiga yetdingiz. "
              "Ertaga qayta urinib ko'ring.",
        "ru": "⚠️ Вы достигли лимита в {max} обращений в день. "
              "Попробуйте снова завтра.",
    },

    # --- Errors & Navigation ---
    "error_generic": {
        "uz": "❌ Xatolik yuz berdi. Iltimos, qayta urinib ko'ring yoki /start bosing.",
        "ru": "❌ Произошла ошибка. Попробуйте ещё раз или нажмите /start.",
    },
    "btn_back": {
        "uz": "⬅️ Orqaga",
        "ru": "⬅️ Назад",
    },
    "btn_main_menu": {
        "uz": "🏠 Asosiy menyu",
        "ru": "🏠 Главное меню",
    },
    "cancelled": {
        "uz": "❌ Bekor qilindi. Asosiy menyuga qaytdingiz.",
        "ru": "❌ Отменено. Вы вернулись в главное меню.",
    },
    "draft_found": {
        "uz": "📋 Sizda tugatilmagan murojaat qoralamasi mavjud. Davom ettirasizmi?",
        "ru": "📋 У вас есть незавершённый черновик обращения. Продолжить?",
    },
    "btn_continue_draft": {
        "uz": "▶️ Davom ettirish",
        "ru": "▶️ Продолжить",
    },
    "btn_new_complaint_discard": {
        "uz": "🆕 Yangidan boshlash",
        "ru": "🆕 Начать заново",
    },

    # --- Edit Options ---
    "edit_what": {
        "uz": "✏️ *Nimani tahrirlash kerak?*",
        "ru": "✏️ *Что нужно отредактировать?*",
    },
    "btn_edit_name": {"uz": "👤 F.I.Sh.", "ru": "👤 ФИО"},
    "btn_edit_phone": {"uz": "📱 Telefon", "ru": "📱 Телефон"},
    "btn_edit_address": {"uz": "📍 Manzil", "ru": "📍 Адрес"},
    "btn_edit_category": {"uz": "📁 Soha", "ru": "📁 Сфера"},
    "btn_edit_title": {"uz": "📌 Sarlavha", "ru": "📌 Заголовок"},
    "btn_edit_text": {"uz": "📝 Matn", "ru": "📝 Текст"},
    "btn_done_editing": {"uz": "✅ Tahrir tugadi", "ru": "✅ Готово"},
}


def t(key: str, lang: str, **kwargs: Any) -> str:
    """
    Get translated text by key and language code.
    Falls back to Uzbek if key not found for language.
    """
    text_dict = TEXTS.get(key, {})
    text = text_dict.get(lang, text_dict.get("uz", f"[Missing: {key}]"))
    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, IndexError):
            pass  # Return unformatted text if format fails
    return text


def get_status_label(status: str, lang: str) -> str:
    """Get localized label for complaint status."""
    key = f"status_{status}"
    return t(key, lang)


def get_type_label(complaint_type: str, lang: str) -> str:
    """Get localized label for complaint type."""
    key = f"type_{complaint_type}"
    return t(key, lang)
