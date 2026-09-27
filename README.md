# Farg'ona Tuman Murojaatlar Tizimi (FTMT)

**O'zbekcha:** Farg'ona tumani aholisining ariza, shikoyat va takliflarini qabul qilish, boshqarish va monitoringini yuritish uchun mo'ljallangan markazlashtirilgan tizim. Ushbu tizim Telegram bot va Admin paneldan iborat.

**Русский:** Централизованная система для приема, управления и мониторинга заявлений, жалоб и предложений жителей Ферганского района. Система состоит из Telegram бота и панели администратора.

> [!WARNING]
> **MUHIM / ВАЖНО**
> * Ushbu loyihada ishlatilgan barcha namunaviy ma'lumotlar (demo data) rasmiy emas.
> * Barcha yuridik matnlar va qoidalar huquqshunos (advokat) tomonidan tasdiqlanishi shart.
> * Tizim xavfsizlik auditi (security audit) o'tkazilmaguncha ishlab chiqarish muhitida (production) foydalanishga tayyor emas.

## 🌟 Features Overview (Imkoniyatlar)
- **Telegram Bot:** Fuqarolar uchun qulay interfeys (O'zbek/Rus).
- **Murojaatlar Boshqaruvi:** Ariza, shikoyat va takliflarni yuborish.
- **Admin Panel:** Murojaatlarni tahlil qilish, yo'naltirish, va monitoring qilish (RBAC tizimi bilan).
- **Holatlar Kuzatuvi (Status Tracking):** Murojaatning qaysi bosqichdaligini kuzatish.

## 🛠 Tech Stack
- **Python 3.11+**
- **Telegram Bot:** `python-telegram-bot` v20+
- **API / Admin Panel:** FastAPI (+ Jinja2 templates yoki API-only + oddiy HTML)
- **Database / ORM:** SQLAlchemy 2.0 (async), Alembic
- **Database Engine:** SQLite (development), PostgreSQL (production)
- **Data Validation:** Pydantic
- **Testing:** pytest

## ⚙️ Prerequisites
- Python 3.11 or higher
- Git
- SQLite (for dev) or PostgreSQL (for prod)

## 🚀 Quick Start (Local Setup)

1. **Create the environment file:**
   ```bash
   cp .env.example .env
   ```
   *Do NOT share your `.env` contents with anyone. It is ignored by Git.*

2. **Configure mandatory secrets in `.env`:**
   - `TELEGRAM_BOT_TOKEN`: Paste the token you got from BotFather (e.g. `1234:ABC...`).
   - `ADMIN_SECRET_KEY`: Generate a random string using `python -c "import secrets; print(secrets.token_hex(32))"` and paste it here.

3. **Configure the Initial Super Admin:**
   The first admin user is a Web Panel user but can also be linked to a Telegram ID.
   - Set `INITIAL_ADMIN_USERNAME` and `INITIAL_ADMIN_PASSWORD` in `.env`. Choose a secure password!
   - To get your Telegram ID, message a bot like `@userinfobot` and paste the numeric ID into `INITIAL_ADMIN_TELEGRAM_ID`.
   - For a local demo only, set `SEED_DEMO_DATA=true`; the first development startup creates provisional demo catalogs and the configured admin. Demo names are not official hokimlik data.
   - If demo seeding is off, the first development startup still creates the configured admin when its password has at least 12 characters. `python scripts/reset_admin.py` is available for a local password reset.

4. **Install dependencies:**
   ```bash
   python -m venv venv
   # On Windows: venv\Scripts\activate
   # On Linux/Mac: source venv/bin/activate
   pip install -e ".[dev]"
   ```

5. **Apply Database Migrations:**
   ```bash
   # Applies the Alembic schema to the database (defaults to SQLite locally)
   python -m alembic upgrade head
   ```

6. **Start the Application:**
   *Make sure you have filled in the `.env` file first!*

   **Run the Telegram bot and admin panel together:**
   ```bash
   python -m app.server
   ```
   Open `http://127.0.0.1:8000/`; it redirects to the admin login page. Run only one Telegram polling process for a bot token.

## Railway deployment

The repository contains a `Dockerfile` and `railway.json`. Create one PostgreSQL service and one application service from this repository. The application service runs `python -m app.server` and serves the admin panel on Railway's `PORT`; it runs the Telegram poller in the same process. Keep the application at one replica to avoid two pollers using the same token. Give the application a Railway-generated public domain. Its root URL opens `/admin/login`.

Set the application variables in Railway, without committing or sharing their values:

- `APP_ENV=production`, `SEED_DEMO_DATA=false`, `TELEGRAM_WEBHOOK_URL=`.
- `DATABASE_URL`: a Railway variable reference to the PostgreSQL service's connection URL.
- `TELEGRAM_BOT_TOKEN`: the bot token from BotFather.
- `ADMIN_SECRET_KEY`: a random value of at least 32 characters.
- `INITIAL_ADMIN_USERNAME` and `INITIAL_ADMIN_PASSWORD`: the first admin credentials; password must be at least 12 characters and at most 72 UTF-8 bytes. Set these only in Railway's private variables. The first login requires a password change.
- Optionally `INITIAL_ADMIN_TELEGRAM_ID` to receive supervisor notifications.

The server applies Alembic migrations before starting. `/health` checks database connectivity. A new production database has no demo categories, organizations or MFY entries. The admin must review and import the supplied 58-name MFY catalog and configure approved category-to-organization routing before citizens can submit appeals. Legal consent text, retention, official deadlines and organization details require hokimlik approval before public use. Do not copy local citizen data into Railway for a demonstration.

## 🧪 Testing
Run the test suite using pytest:
```bash
pytest tests/
```

## 📂 Project Structure Overview
```
bot-2026/
├── alembic/            # Database migrations
├── app/                # Telegram bot, FastAPI admin, models and services
├── docs/               # Documentation (Requirements, etc.)
├── scripts/            # Local admin reset helper
├── tests/              # Pytest cases
├── .env.example
├── Dockerfile
├── railway.json
├── README.md
└── pyproject.toml
```

## 📜 License
[MIT / Proprietary - Placeholder, must be confirmed]
