"""
Demo seed data for development and testing.
ALL DATA HERE IS PROVISIONAL / DEMO — not from official sources.

! IMPORTANT:
- MFY list: Must be replaced with official hokimlik data
- Categories: Must be confirmed by hokimlik
- Organizations: Must be confirmed by hokimlik
- Category-organization mappings: Must be confirmed by hokimlik
"""

from __future__ import annotations

import logging

import bcrypt
from sqlalchemy import select

from app.config import get_settings
from app.database import get_session_factory
from app.mfy_catalog import MFY_CATALOG
from app.models import (
    AdminRole,
    AdminUser,
    Category,
    CategoryOrganization,
    MFYArea,
    Organization,
)

logger = logging.getLogger(__name__)


# ============================================================================
# PROVISIONAL Categories — 17 categories per requirements
# ! All marked is_provisional=True
# ============================================================================
DEMO_CATEGORIES = [
    ("education", "Maktabgacha va maktab ta'limi", "Мактабгача ва мактаб таълими", "Дошкольное и школьное образование", 1),
    ("healthcare", "Sog'liqni saqlash", "Соғлиқни сақлаш", "Здравоохранение", 2),
    ("water", "Suv ta'minoti va irrigatsiya", "Сув таъминоти ва ирригация", "Водоснабжение и ирригация", 3),
    ("electricity", "Elektr ta'minoti", "Электр таъминоти", "Электроснабжение", 4),
    ("gas", "Gaz ta'minoti", "Газ таъминоти", "Газоснабжение", 5),
    ("waste", "Chiqindi boshqaruvi", "Чиқинди бошқаруви", "Управление отходами", 6),
    ("roads", "Yo'l, ko'cha yoritish va obodonlashtirish", "Йўл, кўча ёритиш ва ободонлаштириш", "Дороги, освещение и благоустройство", 7),
    ("construction", "Qurilish va uy-joy-kommunal xo'jaligi", "Қурилиш ва уй-жой-коммунал хўжалиги", "Строительство и ЖКХ", 8),
    ("cadastre", "Kadastr va yer masalalari", "Кадастр ва ер масалалари", "Кадастр и земельные вопросы", 9),
    ("employment", "Bandlik, mehnat va kambag'allikni qisqartirish", "Бандлик, меҳнат ва камбағалликни қисқартириш", "Занятость, труд и сокращение бедности", 10),
    ("social", "Ijtimoiy yordam", "Ижтимоий ёрдам", "Социальная помощь", 11),
    ("youth", "Yoshlar va sport", "Ёшлар ва спорт", "Молодёжь и спорт", 12),
    ("family", "Oila va xotin-qizlar", "Оила ва хотин-қизлар", "Семья и женщины", 13),
    ("agriculture", "Qishloq xo'jaligi", "Қишлоқ хўжалиги", "Сельское хозяйство", 14),
    ("ecology", "Ekologiya", "Экология", "Экология", 15),
    ("culture", "Madaniyat va turizm", "Маданият ва туризм", "Культура и туризм", 16),
    ("security", "Xavfsizlik va huquq-tartibot", "Хавфсизлик ва ҳуқуқ-тартибот", "Безопасность и правопорядок", 17),
    ("other", "Boshqa masala", "Бошқа масала", "Другой вопрос", 99),
]

# ============================================================================
# PROVISIONAL Organizations
# ============================================================================
DEMO_ORGANIZATIONS = [
    ("edu_dept", "Tuman xalq ta'limi bo'limi", "Туман халқ таълими бўлими", "Районный отдел народного образования"),
    ("health_dept", "Tuman tibbiyot birlashmasi", "Туман тиббиёт бирлашмаси", "Районное медицинское объединение"),
    ("water_dept", "Suv ta'minoti korxonasi", "Сув таъминоти корхонаси", "Предприятие водоснабжения"),
    ("electric_dept", "Elektr tarmoqlari korxonasi", "Электр тармоқлари корхонаси", "Предприятие электрических сетей"),
    ("gas_dept", "Gaz ta'minoti korxonasi", "Газ таъминоти корхонаси", "Предприятие газоснабжения"),
    ("utility_dept", "Toza hudud korxonasi", "Тоза ҳудуд корхонаси", "Предприятие 'Чистая территория'"),
    ("roads_dept", "Yo'l xo'jaligi bo'limi", "Йўл хўжалиги бўлими", "Отдел дорожного хозяйства"),
    ("construction_dept", "Qurilish va uy-joy bo'limi", "Қурилиш ва уй-жой бўлими", "Отдел строительства и ЖКХ"),
    ("cadastre_dept", "Yer resurslari bo'limi", "Ер ресурслари бўлими", "Отдел земельных ресурсов"),
    ("employment_dept", "Bandlik markazi", "Бандлик маркази", "Центр занятости"),
    ("social_dept", "Ijtimoiy himoya markazi", "Ижтимоий ҳимоя маркази", "Центр социальной защиты"),
    ("hokimlik", "Farg'ona tuman hokimligi", "Фарғона туман ҳокимлиги", "Хокимият Ферганского района"),
]

# Category → Organization mapping (provisional)
CATEGORY_ORG_MAP = {
    "education": [("edu_dept", True)],
    "healthcare": [("health_dept", True)],
    "water": [("water_dept", True)],
    "electricity": [("electric_dept", True)],
    "gas": [("gas_dept", True)],
    "waste": [("utility_dept", True)],
    "roads": [("roads_dept", True), ("hokimlik", False)],
    "construction": [("construction_dept", True)],
    "cadastre": [("cadastre_dept", True)],
    "employment": [("employment_dept", True)],
    "social": [("social_dept", True)],
    "youth": [("hokimlik", True)],
    "family": [("hokimlik", True)],
    "agriculture": [("hokimlik", True)],
    "ecology": [("hokimlik", True)],
    "culture": [("hokimlik", True)],
    "security": [("hokimlik", True)],
    "other": [("hokimlik", True)],
}

# ============================================================================
# User-provided MFY roster — provisional until the Hokimlik confirms it.
# ============================================================================
DEMO_MFY_AREAS = [
    (entry.name_uz, entry.name_uz_cyrillic, entry.name_ru)
    for entry in MFY_CATALOG
]


async def seed_demo_data() -> None:
    """
    Seed database with demo/provisional data.
    Idempotent: skips if data already exists.
    """
    async with get_session_factory()() as session:
        settings = get_settings()
        await _ensure_initial_admin(session, settings)

        # Check if already seeded
        result = await session.execute(select(Category).limit(1))
        if result.scalar_one_or_none() is not None:
            await session.commit()
            logger.info("Database already seeded, skipping.")
            return

        logger.info("Seeding DEMO data... (all data is PROVISIONAL)")

        # --- MFY Areas ---
        for i, (name_uz, name_cyr, name_ru) in enumerate(DEMO_MFY_AREAS):
            mfy = MFYArea(
                name_uz=name_uz,
                name_uz_cyrillic=name_cyr,
                name_ru=name_ru,
                is_provisional=True,
                sort_order=i + 1,
            )
            session.add(mfy)

        # --- Organizations ---
        org_map: dict[str, Organization] = {}
        for code, name_uz, name_cyr, name_ru in DEMO_ORGANIZATIONS:
            org = Organization(
                code=code,
                name_uz=name_uz,
                name_uz_cyrillic=name_cyr,
                name_ru=name_ru,
                is_provisional=True,
            )
            session.add(org)
            org_map[code] = org

        await session.flush()  # Get IDs

        # --- Categories ---
        cat_map: dict[str, Category] = {}
        for code, name_uz, name_cyr, name_ru, sort_order in DEMO_CATEGORIES:
            is_emergency = code == "security"
            emergency_uz = None
            emergency_ru = None
            if is_emergency:
                emergency_uz = (
                    "! MUHIM: Hayot yoki sog'liqqa xavf bo'lsa, darhol:\n"
                    "• Tez yordam: 103\n"
                    "• Politsiya: 102\n"
                    "• Favqulodda: 112\n"
                    "! Bu raqamlar PLACEHOLDER — rasmiy tasdiqlanishi kerak."
                )
                emergency_ru = (
                    "! ВАЖНО: При угрозе жизни или здоровью, немедленно звоните:\n"
                    "• Скорая: 103\n"
                    "• Полиция: 102\n"
                    "• Экстренная: 112\n"
                    "! Эти номера являются ЗАГЛУШКОЙ — требуется официальное подтверждение."
                )

            cat = Category(
                code=code,
                name_uz=name_uz,
                name_uz_cyrillic=name_cyr,
                name_ru=name_ru,
                is_provisional=True,
                sort_order=sort_order,
                is_emergency=is_emergency,
                emergency_guidance_uz=emergency_uz,
                emergency_guidance_ru=emergency_ru,
            )
            session.add(cat)
            cat_map[code] = cat

        await session.flush()  # Get IDs

        # --- Category-Organization mappings ---
        for cat_code, org_list in CATEGORY_ORG_MAP.items():
            cat = cat_map.get(cat_code)
            if cat is None:
                continue
            for org_code, is_primary in org_list:
                org = org_map.get(org_code)
                if org is None:
                    continue
                mapping = CategoryOrganization(
                    category_id=cat.id,
                    organization_id=org.id,
                    is_primary=is_primary,
                )
                session.add(mapping)

        await session.commit()
        logger.info("DEMO data seeded successfully. ! All data is PROVISIONAL.")


async def _ensure_initial_admin(session, settings) -> None:
    """Create the configured first admin only when an explicitly strong password exists."""
    if not settings.initial_admin_username:
        return
    existing = await session.execute(
        select(AdminUser).where(AdminUser.username == settings.initial_admin_username)
    )
    if existing.scalar_one_or_none() is not None:
        return

    raw_password = settings.initial_admin_password
    placeholders = {
        "123456",
        "7971",
        "change-me-immediately-to-a-strong-password",
    }
    if (
        not raw_password or len(raw_password) < 12
        or len(raw_password.encode("utf-8")) > 72
        or raw_password.lower() in placeholders
    ):
        logger.error(
            "Initial admin was not created: set a unique INITIAL_ADMIN_PASSWORD of at least 12 characters."
        )
        logger.error("Use the secure admin reset utility to create the first admin account.")
        return

    password_hash = bcrypt.hashpw(raw_password.encode(), bcrypt.gensalt()).decode()
    session.add(AdminUser(
        username=settings.initial_admin_username,
        password_hash=password_hash,
        full_name="Tizim administratori",
        role=AdminRole.SUPER_ADMIN.value,
        telegram_id=int(settings.initial_admin_telegram_id)
        if settings.initial_admin_telegram_id
        else None,
        must_change_password=True,
    ))
    logger.info("Initial super admin user created (username from environment)")
