"""Audited supplemental information exchange for a complaint."""
from __future__ import annotations

import json

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.change_models import ComplaintCitizenMessage
from app.config import get_settings
from app.models import (
    AdminRole,
    AdminUser,
    Assignment,
    Attachment,
    AuditEvent,
    Complaint,
    ComplaintStatus,
    Notification,
    User,
)
from app.services import ComplaintService


class CitizenInfoService:
    """Request and receive supplemental information within an assigned case."""

    @staticmethod
    async def request_information(
        session: AsyncSession, complaint_id: int, request_text: str, admin_id: int
    ) -> ComplaintCitizenMessage:
        request_text = request_text.strip() if isinstance(request_text, str) else ""
        if not request_text or len(request_text) > 2000:
            raise ValueError("Request text must contain 1–2000 characters")

        complaint = await session.get(Complaint, complaint_id)
        if complaint is None:
            raise ValueError("Murojaat topilmadi")
        if complaint.deleted_at is not None or complaint.archived_at is not None:
            raise ValueError("Arxivlangan murojaatni o‘zgartirib bo‘lmaydi")
        if complaint.status != ComplaintStatus.IN_PROGRESS.value:
            raise ValueError("Qo‘shimcha ma’lumot faqat bajarilayotgan murojaatda so‘raladi")

        user = await session.get(User, complaint.user_id)
        if user is None or not user.telegram_id:
            raise ValueError("Fuqaro bilan Telegram orqali aloqa mavjud emas")

        message = ComplaintCitizenMessage(
            complaint_id=complaint_id,
            message_type="request",
            author_type="admin",
            author_id=admin_id,
            body=request_text,
        )
        session.add(message)
        await session.flush()
        await ComplaintService.transition_status(
            session,
            complaint_id,
            ComplaintStatus.WAITING_FOR_CITIZEN,
            "admin",
            str(admin_id),
            "Fuqarodan qo‘shimcha ma’lumot so‘raldi",
            notify_citizen=False,
            commit=False,
        )
        language = user.interface_language or "uz"
        heading = (
            f"По обращению {complaint.tracking_id} запрошены дополнительные сведения:"
            if language == "ru"
            else f"{complaint.tracking_id} murojaati bo‘yicha qo‘shimcha ma’lumot kerak:"
        )
        session.add(Notification(
            complaint_id=complaint_id,
            recipient_telegram_id=user.telegram_id,
            notification_type="additional_info_request",
            message_text=f"{heading}\n\n{request_text}",
            language=language,
        ))
        session.add(AuditEvent(
            action="citizen_info_requested",
            entity_type="complaint",
            entity_id=str(complaint_id),
            actor_type="admin",
            actor_id=str(admin_id),
            new_value=json.dumps({"message_id": message.id}),
        ))
        await session.commit()
        return message

    @staticmethod
    async def submit_citizen_reply(
        session: AsyncSession,
        complaint_id: int,
        request_id: int,
        user_id: int,
        body: str,
        attachment: dict | None = None,
    ) -> ComplaintCitizenMessage:
        body = body.strip() if isinstance(body, str) else ""
        if len(body) > 5000:
            raise ValueError("Javob 5000 belgidan oshmasligi kerak")
        if not body and not attachment:
            raise ValueError("Matn yoki bitta fayl yuboring")

        request = await session.get(ComplaintCitizenMessage, request_id)
        complaint = await session.get(Complaint, complaint_id)
        if (
            request is None
            or request.complaint_id != complaint_id
            or request.message_type != "request"
            or complaint is None
            or complaint.user_id != user_id
        ):
            raise ValueError("So‘rov bu fuqaroga tegishli emas")
        if (
            complaint.deleted_at is not None
            or complaint.archived_at is not None
            or complaint.status != ComplaintStatus.WAITING_FOR_CITIZEN.value
        ):
            raise ValueError("Murojaat qo‘shimcha javobni qabul qilmayapti")
        user = await session.get(User, user_id)
        if user is None:
            raise ValueError("Fuqaro akkaunti topilmadi")
        answered = (await session.execute(
            select(ComplaintCitizenMessage.id).where(
                ComplaintCitizenMessage.reply_to_id == request_id
            ).limit(1)
        )).scalar_one_or_none()
        if answered is not None:
            raise ValueError("Bu so‘rovga javob oldin yuborilgan")

        if attachment:
            settings = get_settings()
            file_type = attachment.get("file_type")
            file_id = attachment.get("file_id")
            file_size = attachment.get("file_size") or 0
            if file_type not in {"photo", "document", "video"}:
                raise ValueError("Fayl turi qo‘llab-quvvatlanmaydi")
            if not isinstance(file_id, str) or not file_id or len(file_id) > 255:
                raise ValueError("Fayl identifikatori noto‘g‘ri")
            if type(file_size) is not int or file_size < 0 or file_size > settings.max_file_size:
                raise ValueError("Fayl hajmi ruxsat etilgan chegaradan katta")
            filename = attachment.get("file_name")
            if filename and (not isinstance(filename, str) or len(filename) > 255):
                raise ValueError("Fayl nomi noto‘g‘ri")
            if file_type == "document" and filename:
                ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
                if ext not in settings.allowed_extensions_list:
                    raise ValueError("Fayl formati qo‘llab-quvvatlanmaydi")

        reply = ComplaintCitizenMessage(
            complaint_id=complaint_id,
            reply_to_id=request_id,
            message_type="reply",
            author_type="citizen",
            author_id=user_id,
            body=body or None,
        )
        session.add(reply)
        await session.flush()
        if attachment:
            session.add(Attachment(
                complaint_id=complaint_id,
                message_id=reply.id,
                file_type=attachment["file_type"],
                file_name=attachment.get("file_name"),
                file_size=attachment.get("file_size"),
                telegram_file_id=attachment["file_id"],
                mime_type=attachment.get("mime_type"),
                uploaded_by="citizen",
            ))

        await ComplaintService.transition_status(
            session,
            complaint_id,
            ComplaintStatus.IN_PROGRESS,
            "citizen",
            str(user_id),
            "Fuqaro qo‘shimcha ma’lumot yubordi",
            notify_citizen=False,
            commit=False,
        )
        recipients = (await session.execute(
            select(AdminUser.telegram_id)
            .join(Assignment, Assignment.organization_id == AdminUser.organization_id)
            .where(
                Assignment.complaint_id == complaint_id,
                Assignment.is_active.is_(True),
                AdminUser.is_active.is_(True),
                AdminUser.telegram_id.is_not(None),
                or_(
                    AdminUser.role == AdminRole.AGENCY_HEAD.value,
                    and_(
                        AdminUser.role == AdminRole.EXECUTOR.value,
                        Assignment.assigned_to_id == AdminUser.id,
                    ),
                ),
            ).distinct()
        )).scalars().all()
        language = user.interface_language or "uz"
        notice = (
            f"По обращению {complaint.tracking_id} поступили дополнительные сведения."
            if language == "ru"
            else f"{complaint.tracking_id} murojaati bo‘yicha qo‘shimcha ma’lumot keldi."
        )
        for telegram_id in recipients:
            session.add(Notification(
                complaint_id=complaint_id,
                recipient_telegram_id=telegram_id,
                notification_type="citizen_additional_info",
                message_text=notice,
                language=language,
            ))
        session.add(AuditEvent(
            action="citizen_info_received",
            entity_type="complaint",
            entity_id=str(complaint_id),
            actor_type="citizen",
            actor_id=str(user_id),
            new_value=json.dumps({
                "message_id": reply.id,
                "has_attachment": bool(attachment),
            }),
        ))
        await session.commit()
        return reply
