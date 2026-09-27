"""Queue role-scoped staff notices in the caller's transaction."""
from sqlalchemy import or_, select

from app.models import AdminRole, AdminUser, Notification


async def queue_supervisor_notice(session, kind, message, complaint_id=None):
    recipients = (await session.execute(select(AdminUser.telegram_id).where(
        AdminUser.is_active.is_(True),
        AdminUser.telegram_id.is_not(None),
        AdminUser.role.in_([
            AdminRole.SUPER_ADMIN.value, AdminRole.DISTRICT_SUPERVISOR.value,
        ]),
    ))).scalars().all()
    for recipient in set(recipients):
        session.add(Notification(
            complaint_id=complaint_id, recipient_telegram_id=recipient,
            notification_type=kind, message_text=message, language="uz",
        ))


async def queue_assignment_notice(session, complaint, organization_id, assigned_to_id):
    recipients = (await session.execute(select(AdminUser.telegram_id).where(
        AdminUser.is_active.is_(True),
        AdminUser.telegram_id.is_not(None),
        AdminUser.organization_id == organization_id,
        or_(
            AdminUser.role == AdminRole.AGENCY_HEAD.value,
            (AdminUser.id == assigned_to_id) & (AdminUser.role == AdminRole.EXECUTOR.value),
        ),
    ))).scalars().all()
    for recipient in set(recipients):
        session.add(Notification(
            complaint_id=complaint.id, recipient_telegram_id=recipient,
            notification_type="staff_assignment", language="uz",
            message_text=(
                "📬 Sizga yoki idorangizga murojaat biriktirildi.\n"
                f"Tracking raqami: {complaint.tracking_id}\n"
                "Tafsilotlar va muddatni admin panelida ko‘ring."
            ),
        ))
