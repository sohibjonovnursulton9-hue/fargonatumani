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

## 🚀 Quick Start

1. **Clone the repository:**
   ```bash
   git clone <repository-url>
   cd bot-2026
   ```

2. **Create environment variables:**
   ```bash
   cp .env.example .env
   # Edit .env with your specific tokens and database credentials
   ```

3. **Install dependencies:**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```

4. **Run migrations:**
   ```bash
   alembic upgrade head
   ```

5. **Seed demo data (Optional):**
   ```bash
   python scripts/seed_demo_data.py
   ```

6. **Start the application:**
   - **Start the Telegram Bot:**
     ```bash
     python -m bot.main
     ```
   - **Start the Admin Panel:**
     ```bash
     uvicorn api.main:app --reload
     ```

## 🧪 Testing
Run the test suite using pytest:
```bash
pytest tests/
```

## 📂 Project Structure Overview
```
bot-2026/
├── alembic/            # Database migrations
├── api/                # FastAPI application (Admin Panel)
├── bot/                # Telegram bot application
├── core/               # Shared settings, db configurations, security
├── docs/               # Documentation (Requirements, etc.)
├── models/             # SQLAlchemy models
├── schemas/            # Pydantic schemas
├── scripts/            # Helper scripts (e.g., seeding)
├── tests/              # Pytest cases
├── .env.example
├── README.md
└── requirements.txt
```

## 📜 License
[MIT / Proprietary - Placeholder, must be confirmed]
