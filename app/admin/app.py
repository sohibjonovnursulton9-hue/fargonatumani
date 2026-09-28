import hmac
import hashlib
import json
import secrets
import bcrypt
import re
from contextlib import asynccontextmanager
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Optional, List

from fastapi import FastAPI, Depends, Request, Form, HTTPException, status, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete, select, and_, desc, func, or_, update
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.citizen_info import CitizenInfoService
from app.database import close_db, create_all_tables, get_session, get_session_factory, init_db
from app.models import (
    AdminUser, AdminRole, AdminSession, Complaint, ComplaintStatus, AuditAction, AuditEvent,
    Assignment, Response as ComplaintResponse, StatusEvent, Category, CategoryOrganization,
    MFYArea, Organization,
    VALID_TRANSITIONS, TERMINAL_STATUSES, utcnow
)
from app.services import (
    ComplaintService, AdminAuthService, AuditService, StatisticsService, CatalogService,
    AssignmentService, ResponseService, DeadlineService
)
from app.logging_config import get_logger
from app.admin.csrf import get_or_create_csrf_token

logger = get_logger(__name__)

@asynccontextmanager
async def lifespan(_app):
    """Initialize this process's own database connection before serving requests."""
    settings = get_settings()
    await init_db(settings.database_url)
    if settings.is_development:
        await create_all_tables()
    if settings.seed_demo_data and settings.is_development:
        from app.seed import seed_demo_data
        await seed_demo_data()
    async with get_session_factory()() as db:
        admin_exists = await db.scalar(select(AdminUser.id).limit(1))
        if admin_exists is None:
            from app.seed import _ensure_initial_admin
            await _ensure_initial_admin(db, settings)
            await db.commit()
            admin_exists = await db.scalar(select(AdminUser.id).limit(1))
        if settings.is_production and admin_exists is None:
            raise RuntimeError("Initial administrator is required before production starts")
    try:
        yield
    finally:
        await close_db()


app = FastAPI(title="FTMT Admin Panel", docs_url=None, redoc_url=None, lifespan=lifespan)


@app.middleware("http")
async def prevent_admin_page_caching(request: Request, call_next):
    response = await call_next(request)
    if request.url.path == "/admin" or request.url.path.startswith("/admin/"):
        if not response.headers.get("Cache-Control"):
            response.headers["Cache-Control"] = "private, no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


def hash_session_token(token: str) -> str:
    """Hash a high-entropy browser session token before persisting it."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

# Templates
templates = Jinja2Templates(directory="app/admin/templates")

# Static files (mount if needed, optional per requirements, but good practice)
# import os
# if not os.path.exists("app/admin/static"):
#     os.makedirs("app/admin/static")
# app.mount("/static", StaticFiles(directory="app/admin/static"), name="static")

def to_tashkent_tz(dt):
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    tz = ZoneInfo("Asia/Tashkent")
    return dt.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S")

templates.env.filters["tashkent_tz"] = to_tashkent_tz
templates.env.globals["AdminRole"] = AdminRole
templates.env.globals["ComplaintStatus"] = ComplaintStatus


def set_csrf_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key="csrf_token",
        value=token,
        httponly=True,
        secure=not get_settings().is_development,
        samesite="lax",
        path="/",
    )


async def ensure_complaint_access(
    db: AsyncSession, complaint_id: int, admin: AdminUser, *, write: bool = False,
    include_deleted: bool = False,
) -> Complaint:
    """Enforce active organization/executor assignments on complaint access."""
    complaint = (await db.execute(
        select(Complaint).where(Complaint.id == complaint_id)
    )).scalar_one_or_none()
    if complaint is None:
        raise HTTPException(status_code=404)

    if complaint.deleted_at is not None and not (
        include_deleted and admin.role in {
            AdminRole.SUPER_ADMIN.value, AdminRole.DISTRICT_SUPERVISOR.value,
        }
    ):
        raise HTTPException(status_code=404)

    role = AdminRole(admin.role)
    if role == AdminRole.AUDITOR:
        if write:
            raise HTTPException(status_code=403, detail="Auditors have read-only access")
        return complaint
    if role in {AdminRole.SUPER_ADMIN, AdminRole.DISTRICT_SUPERVISOR}:
        if write and complaint.archived_at is not None:
            raise HTTPException(status_code=409, detail="Arxivlangan murojaatni avval tiklang")
        return complaint

    assignment_stmt = select(Assignment.id).where(
        Assignment.complaint_id == complaint_id,
        Assignment.organization_id == admin.organization_id,
        Assignment.is_active.is_(True),
    )
    if role == AdminRole.EXECUTOR:
        assignment_stmt = assignment_stmt.where(Assignment.assigned_to_id == admin.id)
    if (await db.execute(assignment_stmt.limit(1))).scalar_one_or_none() is None:
        raise HTTPException(status_code=403, detail="Not assigned to this complaint")
    if write and complaint.archived_at is not None:
        raise HTTPException(status_code=409, detail="Arxivlangan murojaatni avval tiklang")
    return complaint


def statistics_scope(admin: AdminUser) -> dict[str, int | None]:
    """Return assignment filters for agency and executor dashboards."""
    if admin.role == AdminRole.AGENCY_HEAD.value:
        return {"organization_id": admin.organization_id or -1, "assigned_to_id": None}
    if admin.role == AdminRole.EXECUTOR.value:
        return {"organization_id": admin.organization_id or -1, "assigned_to_id": admin.id}
    return {}

# Dependencies
async def get_current_admin_optional(request: Request, db: AsyncSession = Depends(get_session)) -> Optional[AdminUser]:
    token = request.cookies.get("admin_session")
    if not token:
        return None

    token_hash = hash_session_token(token)
    # Never accept the stored digest itself as a bearer credential.
    if re.fullmatch(r"[0-9a-f]{64}", token):
        return None
    stmt = select(AdminSession).where(
        and_(
            or_(AdminSession.session_token == token_hash, AdminSession.session_token == token),
            AdminSession.is_active == True,
            AdminSession.expires_at > utcnow()
        )
    )
    result = await db.execute(stmt)
    session_obj = result.scalar_one_or_none()
    
    if not session_obj:
        return None

    # Re-hash legacy plaintext sessions on their first successful use.
    if session_obj.session_token == token:
        session_obj.session_token = token_hash
        await db.commit()
        
    admin_stmt = select(AdminUser).where(and_(AdminUser.id == session_obj.admin_user_id, AdminUser.is_active == True))
    admin_result = await db.execute(admin_stmt)
    return admin_result.scalar_one_or_none()

async def get_current_admin(request: Request, admin: Optional[AdminUser] = Depends(get_current_admin_optional)):
    if not admin:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/admin/login"})
    if admin.must_change_password and request.url.path not in {
        "/admin/change-password", "/admin/logout"
    }:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/admin/change-password"},
        )
    return admin

def require_role(roles: List[AdminRole]):
    def role_checker(admin: AdminUser = Depends(get_current_admin)):
        if AdminRole(admin.role) not in roles:
            raise HTTPException(status_code=403, detail="Forbidden")
        return admin
    return role_checker

async def verify_csrf(request: Request):
    if request.method in ["POST", "PUT", "DELETE", "PATCH"]:
        form = await request.form()
        token = form.get("csrf_token")
        cookie_token = request.cookies.get("csrf_token")
        if (
            not isinstance(token, str)
            or not isinstance(cookie_token, str)
            or not hmac.compare_digest(token, cookie_token)
        ):
            raise HTTPException(status_code=403, detail="CSRF token validation failed")

# Health check
@app.get("/", include_in_schema=False)
async def admin_entry():
    """The service's dedicated public domain opens the admin sign-in page."""
    return RedirectResponse(url="/admin/login", status_code=302)


@app.get("/health")
async def health_check(db: AsyncSession = Depends(get_session)):
    await db.execute(select(1))
    return {"status": "ok", "database": "ok"}

# Routes
@app.get("/admin/login", response_class=HTMLResponse)
async def login_form(request: Request):
    csrf_token = get_or_create_csrf_token(request)
    response = templates.TemplateResponse(request=request, name="admin/login.html", context={"request": request, "csrf_token": csrf_token})
    set_csrf_cookie(response, csrf_token)
    return response

@app.post("/admin/login")
async def login(
    request: Request,
    response: Response,
    username: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_session),
    _=Depends(verify_csrf)
):
    admin = await AdminAuthService.get_admin_by_username(db, username.strip())
    if not admin or not admin.is_active:
        return templates.TemplateResponse(request=request, name="admin/login.html", context={"request": request, "error": "Foydalanuvchi nomi yoki parol noto‘g‘ri", "username": username, "csrf_token": request.cookies.get("csrf_token")})
        
    if await AdminAuthService.check_lockout(db, admin):
        return templates.TemplateResponse(request=request, name="admin/login.html", context={"request": request, "error": "Ko‘p marta xato kiritildi. Biroz kutib qayta urinib ko‘ring.", "username": username, "csrf_token": request.cookies.get("csrf_token")})
        
    try:
        if not bcrypt.checkpw(password.encode('utf-8'), admin.password_hash.encode('utf-8')):
            await AdminAuthService.record_failed_login(db, admin)
            return templates.TemplateResponse(request=request, name="admin/login.html", context={"request": request, "error": "Foydalanuvchi nomi yoki parol noto‘g‘ri", "username": username, "csrf_token": request.cookies.get("csrf_token")})
    except ValueError:
        return templates.TemplateResponse(request=request, name="admin/login.html", context={"request": request, "error": "Foydalanuvchi nomi yoki parol noto‘g‘ri", "username": username, "csrf_token": request.cookies.get("csrf_token")})

    await AdminAuthService.record_successful_login(db, admin)
    
    token = secrets.token_urlsafe(32)
    admin_session = AdminSession(
        session_token=hash_session_token(token),
        admin_user_id=admin.id,
        expires_at=utcnow() + timedelta(seconds=get_settings().admin_session_lifetime),
        ip_address=request.client.host if request.client else None
    )
    db.add(admin_session)
    await db.commit()
    
    redirect = RedirectResponse(url="/admin/", status_code=303)
    redirect.set_cookie(
        key="admin_session",
        value=token,
        httponly=True,
        secure=not get_settings().is_development,
        samesite="lax",
        path="/",
        max_age=get_settings().admin_session_lifetime,
    )
    return redirect

@app.post("/admin/logout")
async def logout(request: Request, response: Response, db: AsyncSession = Depends(get_session), _=Depends(verify_csrf)):
    token = request.cookies.get("admin_session")
    if token:
        await db.execute(update(AdminSession).where(
            AdminSession.session_token.in_([token, hash_session_token(token)])
        ).values(is_active=False))
        await db.commit()
        
    redirect = RedirectResponse(url="/admin/login", status_code=303)
    redirect.delete_cookie(
        "admin_session", path="/", secure=not get_settings().is_development, samesite="lax"
    )
    return redirect

@app.get("/admin/", response_class=HTMLResponse)
async def dashboard(request: Request, admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_session)):
    from datetime import timedelta
    from app.models import Deadline, TERMINAL_STATUSES
    scope = statistics_scope(admin)
    stats = await StatisticsService.get_complaint_counts(db, **scope)
    now = utcnow()
    base_due = select(Deadline.id).join(Complaint, Complaint.id == Deadline.complaint_id).where(
        Complaint.archived_at.is_(None), Complaint.deleted_at.is_(None),
        Complaint.status.not_in([x.value for x in TERMINAL_STATUSES])
    )
    if admin.role in [AdminRole.AGENCY_HEAD.value, AdminRole.EXECUTOR.value]:
        base_due = base_due.join(Assignment, Assignment.complaint_id == Complaint.id).where(
            Assignment.organization_id == admin.organization_id, Assignment.is_active.is_(True)
        )
        if admin.role == AdminRole.EXECUTOR.value:
            base_due = base_due.where(Assignment.assigned_to_id == admin.id)
    stats["overdue"] = (await db.execute(
        select(func.count()).select_from(Deadline).where(
            Deadline.id.in_(base_due.where(Deadline.current_deadline < now))
        )
    )).scalar_one()
    stats["due_soon"] = (await db.execute(
        select(func.count()).select_from(Deadline).where(
            Deadline.id.in_(base_due.where(
                Deadline.current_deadline >= now,
                Deadline.current_deadline <= now + timedelta(days=3),
            ))
        )
    )).scalar_one()

    stmt = select(Complaint).where(
        Complaint.archived_at.is_(None), Complaint.deleted_at.is_(None)
    ).order_by(desc(Complaint.created_at)).limit(10)
    if admin.role in [AdminRole.AGENCY_HEAD.value, AdminRole.EXECUTOR.value]:
        stmt = stmt.join(Assignment).where(
            Assignment.organization_id == admin.organization_id, Assignment.is_active.is_(True)
        )
        if admin.role == AdminRole.EXECUTOR.value:
            stmt = stmt.where(Assignment.assigned_to_id == admin.id)
        stmt = stmt.distinct()
    recent = (await db.execute(stmt)).scalars().all()
    visible_ids = [item.id for item in recent]
    warnings = await DeadlineService.get_approaching_deadlines(db)
    if admin.role in [AdminRole.AGENCY_HEAD.value, AdminRole.EXECUTOR.value]:
        warnings = [item for item in warnings if item.complaint_id in set(visible_ids)]
    category_ids = {item.category_id for item in recent}
    category_rows = (await db.execute(select(Category).where(Category.id.in_(category_ids)))).scalars().all() if category_ids else []
    category_names = {row.id: row.name_uz for row in category_rows}

    status_stmt = select(Complaint.status, func.count(Complaint.id)).where(
        Complaint.archived_at.is_(None), Complaint.deleted_at.is_(None)
    )
    if admin.role in [AdminRole.AGENCY_HEAD.value, AdminRole.EXECUTOR.value]:
        status_assignment_scope = select(Assignment.complaint_id).where(
            Assignment.organization_id == admin.organization_id,
            Assignment.is_active.is_(True),
        )
        if admin.role == AdminRole.EXECUTOR.value:
            status_assignment_scope = status_assignment_scope.where(
                Assignment.assigned_to_id == admin.id
            )
        status_stmt = status_stmt.where(Complaint.id.in_(status_assignment_scope))
    status_stmt = status_stmt.group_by(Complaint.status)
    status_counts = dict((await db.execute(status_stmt)).all())
    labels = {
        ComplaintStatus.DRAFT.value: ('Qoralama', 'info'),
        ComplaintStatus.SUBMITTED.value: ("Yangi", "primary"),
        ComplaintStatus.TRIAGE.value: ("Saralash", "info"),
        ComplaintStatus.ROUTED.value: ("Idoraga yuborilgan", "warning"),
        ComplaintStatus.IN_PROGRESS.value: ("Jarayonda", "warning"),
        ComplaintStatus.WAITING_FOR_CITIZEN.value: ("Fuqarodan javob kutilmoqda", "info"),
        ComplaintStatus.RESPONSE_PROVIDED.value: ("Javob berilgan", "success"),
        ComplaintStatus.IMPLEMENTATION_REPORTED.value: ("Ijro hisoboti berilgan", "success"),
        ComplaintStatus.CITIZEN_CONFIRMATION_PENDING.value: ("Fuqaro tasdig‘i kutilmoqda", "warning"),
        ComplaintStatus.RESOLVED.value: ("Hal qilingan", "success"),
        ComplaintStatus.REOPENED.value: ("Qayta ochilgan", "warning"),
        ComplaintStatus.REJECTED.value: ("Rad etilgan", "danger"),
        ComplaintStatus.WITHDRAWN.value: ("Qaytarib olingan", "secondary"),
        ComplaintStatus.REFERRED_OUTSIDE.value: ("Boshqa idoraga yo‘naltirilgan", "secondary"),
        ComplaintStatus.OUT_OF_SCOPE.value: ("Vakolat doirasidan tashqari", "secondary"),
    }
    status_rows = [
        {"key": key, "label": label, "value": status_counts.get(key, 0), "color": color}
        for key, (label, color) in labels.items()
    ]
    status_rows.append({"key": "overdue", "label": "Muddati o‘tgan", "value": stats["overdue"], "color": "danger"})
    denominator = max(stats.get("total", 0), 1)
    for row in status_rows:
        row["percent"] = min(100, round(row["value"] * 100 / denominator))

    org_stmt = select(
        Organization.id, Organization.name_uz, func.count(func.distinct(Complaint.id))
    ).outerjoin(
        Assignment, and_(Assignment.organization_id == Organization.id, Assignment.is_active.is_(True))
    ).outerjoin(
        Complaint, and_(
            Complaint.id == Assignment.complaint_id,
            Complaint.archived_at.is_(None),
            Complaint.deleted_at.is_(None),
        )
    ).where(Organization.is_active.is_(True))
    if admin.role in [AdminRole.AGENCY_HEAD.value, AdminRole.EXECUTOR.value]:
        org_stmt = org_stmt.where(Organization.id == (admin.organization_id or -1))
    org_stmt = org_stmt.group_by(Organization.id, Organization.name_uz).order_by(Organization.name_uz).limit(12)
    agency_rows = (await db.execute(org_stmt)).all()

    audit_stmt = select(AuditEvent).where(AuditEvent.entity_type == "complaint").order_by(desc(AuditEvent.created_at))
    if admin.role in [AdminRole.AGENCY_HEAD.value, AdminRole.EXECUTOR.value]:
        audit_stmt = audit_stmt.where(AuditEvent.entity_id.in_([str(value) for value in visible_ids]))
    recent_actions = (await db.execute(audit_stmt.limit(8))).scalars().all()

    csrf_token = get_or_create_csrf_token(request)
    response = templates.TemplateResponse(request=request, name="admin/dashboard.html", context={
        "request": request, "admin": admin, "stats": stats, "recent_complaints": recent,
        "warnings": warnings, "csrf_token": csrf_token, "status_rows": status_rows,
        "category_names": category_names, "agency_rows": agency_rows,
        "recent_actions": recent_actions,
    })
    set_csrf_cookie(response, csrf_token)
    return response

@app.get("/admin/complaints", response_class=HTMLResponse)
async def list_complaints(
    request: Request, 
    page: int = 1,
    status: Optional[str] = None,
    tracking_id: Optional[str] = None,
    archived: bool = False,
    deleted: bool = False,
    admin: AdminUser = Depends(get_current_admin), 
    db: AsyncSession = Depends(get_session)
):
    per_page = 20
    offset = (page - 1) * per_page
    if (archived or deleted) and admin.role not in [AdminRole.SUPER_ADMIN.value, AdminRole.DISTRICT_SUPERVISOR.value]:
        raise HTTPException(status_code=403, detail="Arxiv yoki o‘chirilganlar bo‘limiga ruxsat yo‘q")
    if archived and deleted:
        raise HTTPException(status_code=400, detail="Arxiv va o‘chirilganlar filtrini bir vaqtda tanlab bo‘lmaydi")

    if deleted:
        visibility_filter = Complaint.deleted_at.is_not(None)
    elif archived:
        visibility_filter = and_(
            Complaint.archived_at.is_not(None), Complaint.deleted_at.is_(None)
        )
    else:
        visibility_filter = and_(
            Complaint.archived_at.is_(None), Complaint.deleted_at.is_(None)
        )
    stmt = select(Complaint).where(visibility_filter).order_by(desc(Complaint.created_at))
    
    # Filter by role
    if admin.role in [AdminRole.AGENCY_HEAD.value, AdminRole.EXECUTOR.value]:
        stmt = stmt.join(Assignment).where(
            Assignment.organization_id == admin.organization_id,
            Assignment.is_active.is_(True),
        )
        if admin.role == AdminRole.EXECUTOR.value:
            stmt = stmt.where(Assignment.assigned_to_id == admin.id)
        stmt = stmt.distinct()
    if status:
        stmt = stmt.where(Complaint.status == status)
    if tracking_id:
        stmt = stmt.where(Complaint.tracking_id.ilike(f"%{tracking_id}%"))
        
    total_stmt = select(func.count()).select_from(stmt.subquery())
    total_items = (await db.execute(total_stmt)).scalar() or 0
    
    stmt = stmt.offset(offset).limit(per_page)
    complaints = (await db.execute(stmt)).scalars().all()
    
    total_pages = (total_items + per_page - 1) // per_page
    
    csrf_token = get_or_create_csrf_token(request)
    response = templates.TemplateResponse(request=request, name="admin/complaints/list.html", context={
        "request": request,
        "admin": admin,
        "complaints": complaints,
        "page": page,
        "total_pages": total_pages,
        "status": status,
        "tracking_id": tracking_id,
        "archived": archived,
        "deleted": deleted,
        "csrf_token": csrf_token
    })
    set_csrf_cookie(response, csrf_token)
    
    # Audit log bulk export or view (here just view list)
    await AuditService.log_event(db, AuditAction.BULK_ACTION.value, "complaint", "admin", actor_id=str(admin.id), metadata={"action": "list_view"})
    return response

@app.get("/admin/complaints/{id}", response_class=HTMLResponse)
async def detail_complaint(
    id: int, request: Request, deleted: bool = False,
    admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_session),
):
    complaint = await ensure_complaint_access(db, id, admin, include_deleted=deleted)
    if admin.role in {AdminRole.SUPER_ADMIN.value, AdminRole.DISTRICT_SUPERVISOR.value}:
        await db.refresh(complaint, attribute_names=["passport_data", "birth_date"])
        await AuditService.log_event(
            db, "identity_viewed", "complaint", "admin",
            entity_id=str(complaint.id), actor_id=str(admin.id),
            metadata={"fields": ["passport_data", "birth_date"]},
        )
        await db.commit()

    # Fetch related data
    events = (await db.execute(select(StatusEvent).where(StatusEvent.complaint_id == id).order_by(StatusEvent.created_at))).scalars().all()
    assignment_stmt = select(Assignment).where(Assignment.complaint_id == id)
    response_stmt = select(ComplaintResponse).where(ComplaintResponse.complaint_id == id)
    if admin.role in [AdminRole.AGENCY_HEAD.value, AdminRole.EXECUTOR.value]:
        assignment_stmt = assignment_stmt.where(
            Assignment.organization_id == admin.organization_id,
            Assignment.is_active.is_(True),
        )
        if admin.role == AdminRole.EXECUTOR.value:
            assignment_stmt = assignment_stmt.where(Assignment.assigned_to_id == admin.id)
        response_stmt = response_stmt.join(
            AdminUser, ComplaintResponse.responded_by_id == AdminUser.id
        ).where(AdminUser.organization_id == admin.organization_id)
    assignments = (await db.execute(assignment_stmt)).scalars().all()
    responses = (await db.execute(response_stmt)).scalars().all()
    organizations = await CatalogService.get_organizations_for_category(db, complaint.category_id)
    if admin.role == AdminRole.AGENCY_HEAD.value:
        organizations = [item for item in organizations if item.id == admin.organization_id]
    from app.change_models import ComplaintCitizenMessage, ComplaintInternalNote
    from app.models import Attachment, Deadline
    note_rows = (await db.execute(
        select(ComplaintInternalNote, AdminUser.username).join(
            AdminUser, AdminUser.id == ComplaintInternalNote.author_id
        ).where(ComplaintInternalNote.complaint_id == id).order_by(ComplaintInternalNote.created_at.desc())
    )).all()
    internal_notes = [{"note": row[0], "author": row[1]} for row in note_rows]
    citizen_messages = (await db.execute(
        select(ComplaintCitizenMessage).where(
            ComplaintCitizenMessage.complaint_id == id
        ).order_by(ComplaintCitizenMessage.created_at)
    )).scalars().all()
    citizen_message_ids = [item.id for item in citizen_messages]
    citizen_attachment_rows = (await db.execute(
        select(Attachment).where(
            Attachment.message_id.in_(citizen_message_ids or [-1])
        ).order_by(Attachment.id)
    )).scalars().all()
    citizen_attachments = {}
    for item in citizen_attachment_rows:
        citizen_attachments.setdefault(item.message_id, []).append(item)
    attachments = (await db.execute(
        select(Attachment).where(
            Attachment.complaint_id == id,
            Attachment.message_id.is_(None),
            Attachment.is_response_attachment.is_(False),
        ).order_by(Attachment.id)
    )).scalars().all()
    deadline = (await db.execute(select(Deadline).where(Deadline.complaint_id == id))).scalar_one_or_none()
    deadline_overdue = bool(deadline and (deadline.current_deadline.replace(tzinfo=timezone.utc) if deadline.current_deadline.tzinfo is None else deadline.current_deadline) < utcnow())
    organization_ids = [org.id for org in organizations]
    all_assignment_orgs = await CatalogService.get_organizations_for_category(db, complaint.category_id)
    organization_names = {item.id: item.name_uz for item in all_assignment_orgs}
    executor_ids = {item.assigned_to_id for item in assignments if item.assigned_to_id}
    assigned_staff = (await db.execute(select(AdminUser).where(AdminUser.id.in_(executor_ids or [-1])))).scalars().all()
    assignee_names = {item.id: (item.full_name or item.username) for item in assigned_staff}
    executors = (await db.execute(select(AdminUser).where(
        AdminUser.role == AdminRole.EXECUTOR.value,
        AdminUser.is_active.is_(True),
        AdminUser.organization_id.in_(organization_ids or [-1]),
    ).order_by(AdminUser.full_name))).scalars().all()
    from app.bot_config import read_bot_config
    active_bot_config = await read_bot_config(db)
    
    csrf_token = get_or_create_csrf_token(request)
    response = templates.TemplateResponse(request=request, name="admin/complaints/detail.html", context={
        "request": request,
        "admin": admin,
        "complaint": complaint,
        "events": events,
        "assignments": assignments,
        "responses": responses,
        "valid_transitions": [
            s.value for s in VALID_TRANSITIONS.get(ComplaintStatus(complaint.status), [])
            if s != ComplaintStatus.WAITING_FOR_CITIZEN
            and (admin.role in {"super_admin", "district_supervisor"} or (
                admin.role in {"agency_head", "executor"} and s in {
                    ComplaintStatus.IN_PROGRESS, ComplaintStatus.RESPONSE_PROVIDED,
                    ComplaintStatus.IMPLEMENTATION_REPORTED,
                }
            ))
        ],
        "terminal": ComplaintStatus(complaint.status) in TERMINAL_STATUSES,
        "organizations": organizations,
        "organization_names": organization_names,
        "assignee_names": assignee_names,
        "executors": executors,
        "internal_notes": internal_notes,
        "citizen_messages": citizen_messages,
        "citizen_attachments": citizen_attachments,
        "attachments": attachments,
        "deadline": deadline, "deadline_overdue": deadline_overdue,
        "response_templates": active_bot_config["response_templates"],
        "csrf_token": csrf_token
    })
    set_csrf_cookie(response, csrf_token)
    return response

@app.post("/admin/complaints/{id}/triage")
async def triage_complaint(
    id: int, 
    request: Request,
    admin: AdminUser = Depends(require_role([AdminRole.SUPER_ADMIN, AdminRole.DISTRICT_SUPERVISOR])), 
    db: AsyncSession = Depends(get_session),
    _=Depends(verify_csrf)
):
    await ensure_complaint_access(db, id, admin, write=True)
    try:
        await ComplaintService.transition_status(db, id, ComplaintStatus.TRIAGE, "admin", str(admin.id))
        return RedirectResponse(url=f"/admin/complaints/{id}", status_code=303)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/admin/complaints/{id}/assign")
async def assign_complaint(
    id: int,
    request: Request,
    organization_id: int = Form(...),
    assigned_to_id: Optional[int] = Form(None),
    notes: Optional[str] = Form(None),
    admin: AdminUser = Depends(require_role([AdminRole.SUPER_ADMIN, AdminRole.DISTRICT_SUPERVISOR, AdminRole.AGENCY_HEAD])),
    db: AsyncSession = Depends(get_session),
    _=Depends(verify_csrf)
):
    complaint = (await db.execute(select(Complaint).where(Complaint.id == id))).scalar_one_or_none()
    if complaint is None:
        raise HTTPException(status_code=404, detail="Complaint not found")
    await ensure_complaint_access(db, id, admin, write=True)

    if complaint.archived_at is not None or complaint.deleted_at is not None:
        raise HTTPException(status_code=409, detail="Arxivlangan yoki o‘chirilgan murojaatni biriktirib bo‘lmaydi")
    if ComplaintStatus(complaint.status) in TERMINAL_STATUSES:
        raise HTTPException(status_code=409, detail="Yakunlangan murojaatga ijrochi biriktirib bo‘lmaydi")

    if admin.role == AdminRole.AGENCY_HEAD.value and organization_id != admin.organization_id:
        raise HTTPException(status_code=403, detail="Cannot assign outside your organization")
    organization = (await db.execute(select(Organization).where(
        Organization.id == organization_id,
        Organization.is_active.is_(True),
    ))).scalar_one_or_none()
    if organization is None:
        raise HTTPException(status_code=400, detail="Faol idora topilmadi")
    if assigned_to_id is not None:
        executor = (await db.execute(select(AdminUser).where(
            AdminUser.id == assigned_to_id,
            AdminUser.organization_id == organization_id,
            AdminUser.role == AdminRole.EXECUTOR.value,
            AdminUser.is_active.is_(True),
        ))).scalar_one_or_none()
        if executor is None:
            raise HTTPException(status_code=400, detail="Tanlangan faol ijrochi bu idorada mavjud emas")

    mapping = (await db.execute(select(CategoryOrganization.category_id).where(
        CategoryOrganization.category_id == complaint.category_id,
        CategoryOrganization.organization_id == organization_id,
    ))).scalar_one_or_none()
    if mapping is None:
        raise HTTPException(status_code=400, detail="Bu soha tanlangan idoraga biriktirilmagan")

    try:
        original_status = ComplaintStatus(complaint.status)
        await AssignmentService.assign_complaint(
            db, id, organization_id, admin.id, assigned_to_id,
            notes=notes, commit=False,
        )
        if original_status == ComplaintStatus.SUBMITTED:
            await ComplaintService.transition_status(
                db, id, ComplaintStatus.TRIAGE, "admin", str(admin.id),
                reason="Initial triage during assignment", notify_citizen=False, commit=False,
            )
            await ComplaintService.transition_status(
                db, id, ComplaintStatus.ROUTED, "admin", str(admin.id),
                reason="Assigned to organization", notify_citizen=False, commit=False,
            )
        elif original_status == ComplaintStatus.TRIAGE:
            await ComplaintService.transition_status(
                db, id, ComplaintStatus.ROUTED, "admin", str(admin.id),
                reason="Assigned to organization", notify_citizen=False, commit=False,
            )
        await db.commit()
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return RedirectResponse(url=f"/admin/complaints/{id}", status_code=303)

@app.post("/admin/complaints/{id}/status")
async def change_status(
    id: int,
    request: Request,
    new_status: str = Form(...),
    reason: Optional[str] = Form(None),
    admin: AdminUser = Depends(get_current_admin),
    db: AsyncSession = Depends(get_session),
    _=Depends(verify_csrf)
):
    await ensure_complaint_access(db, id, admin, write=True)
    try:
        status_enum = ComplaintStatus(new_status)
        if status_enum == ComplaintStatus.WAITING_FOR_CITIZEN:
            raise HTTPException(
                status_code=400,
                detail="Fuqarodan ma’lumot so‘rash uchun maxsus so‘rov formasidan foydalaning",
            )
        if AdminRole(admin.role) in {AdminRole.AGENCY_HEAD, AdminRole.EXECUTOR}:
            permitted = {
                ComplaintStatus.IN_PROGRESS,
                ComplaintStatus.WAITING_FOR_CITIZEN,
                ComplaintStatus.RESPONSE_PROVIDED,
                ComplaintStatus.IMPLEMENTATION_REPORTED,
            }
            if status_enum not in permitted:
                raise HTTPException(status_code=403, detail="Status change is not allowed for this role")
        await ComplaintService.transition_status(db, id, status_enum, "admin", str(admin.id), reason)
        return RedirectResponse(url=f"/admin/complaints/{id}", status_code=303)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/admin/complaints/{id}/respond")
async def add_response(
    id: int,
    request: Request,
    response_text: str = Form(...),
    response_language: str = Form("uz"),
    actions_taken: Optional[str] = Form(None),
    admin: AdminUser = Depends(require_role([AdminRole.SUPER_ADMIN, AdminRole.DISTRICT_SUPERVISOR, AdminRole.AGENCY_HEAD, AdminRole.EXECUTOR])),
    db: AsyncSession = Depends(get_session),
    _=Depends(verify_csrf)
):
    await ensure_complaint_access(db, id, admin, write=True)
    if not response_text.strip() or len(response_text.strip()) > 8000:
        raise HTTPException(status_code=400, detail="Javob 1–8000 belgi oralig‘ida bo‘lsin")
    if actions_taken and len(actions_taken) > 3000:
        raise HTTPException(status_code=400, detail="Chora izohi 3000 belgidan oshmasin")
    if response_language not in {"uz", "ru", "uz_latin", "uz_cyrillic"}:
        raise HTTPException(status_code=400, detail="Unsupported response language")
    await ResponseService.add_response(db, id, response_text.strip(), response_language, admin.id, actions_taken)
    return RedirectResponse(url=f"/admin/complaints/{id}", status_code=303)


@app.post("/admin/complaints/{id}/request-info")
async def request_citizen_information(
    id: int,
    request_text: str = Form(...),
    admin: AdminUser = Depends(get_current_admin),
    db: AsyncSession = Depends(get_session),
    _=Depends(verify_csrf),
):
    await ensure_complaint_access(db, id, admin, write=True)
    try:
        await CitizenInfoService.request_information(db, id, request_text, admin.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=f"/admin/complaints/{id}#citizen-information", status_code=303)

@app.post("/admin/complaints/{id}/extend-deadline")
async def extend_deadline(
    id: int,
    request: Request,
    days: int = Form(...),
    reason: str = Form(...),
    admin: AdminUser = Depends(require_role([AdminRole.SUPER_ADMIN, AdminRole.DISTRICT_SUPERVISOR])),
    db: AsyncSession = Depends(get_session),
    _=Depends(verify_csrf)
):
    await ensure_complaint_access(db, id, admin, write=True)
    from app.models import Deadline
    deadline = (await db.execute(
        select(Deadline).where(Deadline.complaint_id == id)
    )).scalar_one_or_none()
    if deadline is None:
        raise HTTPException(status_code=404, detail="Murojaat muddati topilmadi")
    new_deadline = deadline.current_deadline + timedelta(days=days)
    try:
        await DeadlineService.extend_deadline(
            db, deadline.id, new_deadline, reason, admin.id
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=f"/admin/complaints/{id}", status_code=303)

@app.get("/admin/statistics", response_class=HTMLResponse)
async def view_statistics(request: Request, admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_session)):
    scope = statistics_scope(admin)
    stats = await StatisticsService.get_complaint_counts(db, **scope)
    res_time = await StatisticsService.get_resolution_time_stats(db, **scope)
    
    return templates.TemplateResponse(request=request, name="admin/statistics.html", context={
        "request": request,
        "admin": admin,
        "stats": stats,
        "res_time": res_time
    })

@app.get("/admin/audit", response_class=HTMLResponse)
async def view_audit(
    request: Request, 
    page: int = 1,
    admin: AdminUser = Depends(require_role([AdminRole.SUPER_ADMIN, AdminRole.AUDITOR])), 
    db: AsyncSession = Depends(get_session)
):
    per_page = 50
    offset = (page - 1) * per_page
    
    stmt = select(AuditEvent).order_by(desc(AuditEvent.created_at))
    
    total_stmt = select(func.count()).select_from(stmt.subquery())
    total_items = (await db.execute(total_stmt)).scalar() or 0
    
    events = (await db.execute(stmt.offset(offset).limit(per_page))).scalars().all()
    total_pages = (total_items + per_page - 1) // per_page
    
    return templates.TemplateResponse(request=request, name="admin/audit.html", context={
        "request": request,
        "admin": admin,
        "events": events,
        "page": page,
        "total_pages": total_pages
    })

async def _audit_catalog_change(
    db: AsyncSession, admin: AdminUser, entity_type: str, entity_id: int | None, payload: dict
) -> None:
    db.add(AuditEvent(
        action=AuditAction.CONFIG_CHANGED.value,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        actor_type="admin",
        actor_id=str(admin.id),
        new_value=json.dumps(payload, ensure_ascii=False),
    ))
    await db.commit()


def _validate_catalog_name(value: str, field_label: str) -> str:
    value = value.strip()
    if not value or len(value) > 255:
        raise HTTPException(status_code=400, detail=f"{field_label} 1-255 ta belgi bo'lishi kerak")
    return value


@app.get("/admin/categories", response_class=HTMLResponse)
async def list_categories(
    request: Request,
    admin: AdminUser = Depends(require_role([AdminRole.SUPER_ADMIN])),
    db: AsyncSession = Depends(get_session),
):
    categories = (await db.execute(select(Category).order_by(Category.sort_order, Category.id))).scalars().all()
    organizations = (await db.execute(select(Organization).order_by(Organization.name_uz))).scalars().all()
    mappings = (await db.execute(
        select(CategoryOrganization, Category, Organization)
        .join(Category, Category.id == CategoryOrganization.category_id)
        .join(Organization, Organization.id == CategoryOrganization.organization_id)
        .order_by(Category.sort_order, Organization.name_uz)
    )).all()
    csrf_token = get_or_create_csrf_token(request)
    response = templates.TemplateResponse(request=request, name="admin/categories.html", context={
        "request": request, "admin": admin, "categories": categories,
        "organizations": organizations, "mappings": mappings, "csrf_token": csrf_token,
    })
    set_csrf_cookie(response, csrf_token)
    return response


@app.post("/admin/categories")
async def create_category(
    request: Request,
    code: str = Form(...),
    name_uz: str = Form(...),
    name_uz_cyrillic: str = Form(...),
    name_ru: str = Form(...),
    sort_order: int = Form(0),
    sla_days: Optional[int] = Form(None),
    is_emergency: bool = Form(False),
    admin: AdminUser = Depends(require_role([AdminRole.SUPER_ADMIN])),
    db: AsyncSession = Depends(get_session),
    _=Depends(verify_csrf),
):
    code = code.strip().lower()
    if not re.fullmatch(r"[a-z0-9_-]{2,50}", code):
        raise HTTPException(status_code=400, detail="Kod 2-50 ta lotin harfi, raqam, _ yoki - bo'lsin")
    if sla_days is not None and not 1 <= sla_days <= 365:
        raise HTTPException(status_code=400, detail="Muddat 1 dan 365 kungacha bo'lishi kerak")
    category = Category(
        code=code,
        name_uz=_validate_catalog_name(name_uz, "O'zbekcha nom"),
        name_uz_cyrillic=_validate_catalog_name(name_uz_cyrillic, "Кириллча ном"),
        name_ru=_validate_catalog_name(name_ru, "Ruscha nom"),
        sort_order=sort_order,
        sla_days=sla_days,
        is_emergency=is_emergency,
        is_provisional=True,
    )
    db.add(category)
    try:
        await db.flush()
        await _audit_catalog_change(db, admin, "category", category.id, {"code": code})
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Bu kodli soha allaqachon mavjud") from exc
    return RedirectResponse(url="/admin/categories", status_code=303)


@app.post("/admin/categories/{category_id}")
async def update_category(
    category_id: int,
    code: str = Form(...),
    name_uz: str = Form(...),
    name_uz_cyrillic: str = Form(...),
    name_ru: str = Form(...),
    sort_order: int = Form(0),
    sla_days: Optional[int] = Form(None),
    is_emergency: bool = Form(False),
    is_provisional: bool = Form(True),
    admin: AdminUser = Depends(require_role([AdminRole.SUPER_ADMIN])),
    db: AsyncSession = Depends(get_session),
    _=Depends(verify_csrf),
):
    category = (await db.execute(select(Category).where(Category.id == category_id))).scalar_one_or_none()
    if category is None:
        raise HTTPException(status_code=404)
    code = code.strip().lower()
    if not re.fullmatch(r"[a-z0-9_-]{2,50}", code):
        raise HTTPException(status_code=400, detail="Noto'g'ri soha kodi")
    if sla_days is not None and not 1 <= sla_days <= 365:
        raise HTTPException(status_code=400, detail="Muddat 1 dan 365 kungacha bo'lishi kerak")
    category.code = code
    category.name_uz = _validate_catalog_name(name_uz, "O'zbekcha nom")
    category.name_uz_cyrillic = _validate_catalog_name(name_uz_cyrillic, "Кириллча ном")
    category.name_ru = _validate_catalog_name(name_ru, "Ruscha nom")
    category.sort_order = sort_order
    category.sla_days = sla_days
    category.is_emergency = is_emergency
    category.is_provisional = is_provisional
    try:
        await _audit_catalog_change(db, admin, "category", category.id, {"code": code})
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Bu kodli soha allaqachon mavjud") from exc
    return RedirectResponse(url="/admin/categories", status_code=303)


@app.post("/admin/categories/{category_id}/status")
async def change_category_status(
    category_id: int,
    is_active: bool = Form(...),
    admin: AdminUser = Depends(require_role([AdminRole.SUPER_ADMIN])),
    db: AsyncSession = Depends(get_session),
    _=Depends(verify_csrf),
):
    category = (await db.execute(select(Category).where(Category.id == category_id))).scalar_one_or_none()
    if category is None:
        raise HTTPException(status_code=404)
    category.is_active = is_active
    await _audit_catalog_change(db, admin, "category", category.id, {"is_active": is_active})
    return RedirectResponse(url="/admin/categories", status_code=303)


@app.post("/admin/categories/{category_id}/organizations")
async def map_category_organization(
    category_id: int,
    organization_id: int = Form(...),
    is_primary: bool = Form(False),
    admin: AdminUser = Depends(require_role([AdminRole.SUPER_ADMIN])),
    db: AsyncSession = Depends(get_session),
    _=Depends(verify_csrf),
):
    category = (await db.execute(select(Category).where(Category.id == category_id))).scalar_one_or_none()
    organization = (await db.execute(select(Organization).where(
        Organization.id == organization_id, Organization.is_active.is_(True)
    ))).scalar_one_or_none()
    if category is None or organization is None:
        raise HTTPException(status_code=404, detail="Soha yoki faol idora topilmadi")
    if not category.is_active:
        raise HTTPException(status_code=400, detail="Faol bo‘lmagan sohaga idora biriktirib bo‘lmaydi")
    if is_primary:
        await db.execute(update(CategoryOrganization).where(
            CategoryOrganization.category_id == category_id
        ).values(is_primary=False))
    mapping = (await db.execute(select(CategoryOrganization).where(
        CategoryOrganization.category_id == category_id,
        CategoryOrganization.organization_id == organization_id,
    ))).scalar_one_or_none()
    if mapping is None:
        mapping = CategoryOrganization(
            category_id=category_id, organization_id=organization_id, is_primary=is_primary
        )
        db.add(mapping)
    else:
        mapping.is_primary = is_primary
    await _audit_catalog_change(db, admin, "category_organization", category_id, {
        "organization_id": organization_id, "is_primary": is_primary,
    })
    return RedirectResponse(url="/admin/categories", status_code=303)


@app.post("/admin/categories/{category_id}/organizations/{organization_id}/remove")
async def remove_category_organization(
    category_id: int,
    organization_id: int,
    admin: AdminUser = Depends(require_role([AdminRole.SUPER_ADMIN])),
    db: AsyncSession = Depends(get_session),
    _=Depends(verify_csrf),
):
    await db.execute(delete(CategoryOrganization).where(
        CategoryOrganization.category_id == category_id,
        CategoryOrganization.organization_id == organization_id,
    ))
    await _audit_catalog_change(db, admin, "category_organization", category_id, {
        "removed_organization_id": organization_id,
    })
    return RedirectResponse(url="/admin/categories", status_code=303)


@app.get("/admin/organizations", response_class=HTMLResponse)
async def list_organizations(
    request: Request,
    admin: AdminUser = Depends(require_role([AdminRole.SUPER_ADMIN])),
    db: AsyncSession = Depends(get_session),
):
    organizations = (await db.execute(select(Organization).order_by(Organization.name_uz))).scalars().all()
    csrf_token = get_or_create_csrf_token(request)
    response = templates.TemplateResponse(request=request, name="admin/organizations.html", context={
        "request": request, "admin": admin, "organizations": organizations, "csrf_token": csrf_token,
    })
    set_csrf_cookie(response, csrf_token)
    return response


@app.post("/admin/organizations")
async def create_organization(
    code: str = Form(...),
    name_uz: str = Form(...),
    name_uz_cyrillic: str = Form(...),
    name_ru: str = Form(...),
    contact_info: str = Form(""),
    admin: AdminUser = Depends(require_role([AdminRole.SUPER_ADMIN])),
    db: AsyncSession = Depends(get_session),
    _=Depends(verify_csrf),
):
    code = code.strip().lower()
    if not re.fullmatch(r"[a-z0-9_-]{2,50}", code):
        raise HTTPException(status_code=400, detail="Noto'g'ri idora kodi")
    org = Organization(
        code=code,
        name_uz=_validate_catalog_name(name_uz, "O'zbekcha nom"),
        name_uz_cyrillic=_validate_catalog_name(name_uz_cyrillic, "Кириллча ном"),
        name_ru=_validate_catalog_name(name_ru, "Ruscha nom"),
        contact_info=contact_info.strip() or None,
        is_provisional=True,
    )
    db.add(org)
    try:
        await db.flush()
        await _audit_catalog_change(db, admin, "organization", org.id, {"code": code})
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Bu kodli idora allaqachon mavjud") from exc
    return RedirectResponse(url="/admin/organizations", status_code=303)


@app.post("/admin/organizations/{organization_id}")
async def update_organization(
    organization_id: int,
    code: str = Form(...),
    name_uz: str = Form(...),
    name_uz_cyrillic: str = Form(...),
    name_ru: str = Form(...),
    contact_info: str = Form(""),
    is_provisional: bool = Form(True),
    admin: AdminUser = Depends(require_role([AdminRole.SUPER_ADMIN])),
    db: AsyncSession = Depends(get_session),
    _=Depends(verify_csrf),
):
    org = (await db.execute(select(Organization).where(Organization.id == organization_id))).scalar_one_or_none()
    if org is None:
        raise HTTPException(status_code=404)
    code = code.strip().lower()
    if not re.fullmatch(r"[a-z0-9_-]{2,50}", code):
        raise HTTPException(status_code=400, detail="Noto'g'ri idora kodi")
    org.code = code
    org.name_uz = _validate_catalog_name(name_uz, "O'zbekcha nom")
    org.name_uz_cyrillic = _validate_catalog_name(name_uz_cyrillic, "Кириллча ном")
    org.name_ru = _validate_catalog_name(name_ru, "Ruscha nom")
    org.contact_info = contact_info.strip() or None
    org.is_provisional = is_provisional
    try:
        await _audit_catalog_change(db, admin, "organization", org.id, {"code": code})
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Bu kodli idora allaqachon mavjud") from exc
    return RedirectResponse(url="/admin/organizations", status_code=303)


@app.post("/admin/organizations/{organization_id}/status")
async def change_organization_status(
    organization_id: int,
    is_active: bool = Form(...),
    admin: AdminUser = Depends(require_role([AdminRole.SUPER_ADMIN])),
    db: AsyncSession = Depends(get_session),
    _=Depends(verify_csrf),
):
    org = (await db.execute(select(Organization).where(Organization.id == organization_id))).scalar_one_or_none()
    if org is None:
        raise HTTPException(status_code=404)
    org.is_active = is_active
    await _audit_catalog_change(db, admin, "organization", org.id, {"is_active": is_active})
    return RedirectResponse(url="/admin/organizations", status_code=303)


@app.get("/admin/mfy", response_class=HTMLResponse)
async def list_mfy_areas(
    request: Request,
    admin: AdminUser = Depends(require_role([AdminRole.SUPER_ADMIN])),
    db: AsyncSession = Depends(get_session),
):
    areas = (await db.execute(select(MFYArea).order_by(MFYArea.sort_order, MFYArea.name_uz))).scalars().all()
    csrf_token = get_or_create_csrf_token(request)
    response = templates.TemplateResponse(request=request, name="admin/mfy.html", context={
        "request": request, "admin": admin, "areas": areas, "csrf_token": csrf_token,
    })
    set_csrf_cookie(response, csrf_token)
    return response


# Register the import routes before the dynamic /admin/mfy/{area_id} route.
from app.admin.mfy_import import build_mfy_import_router

app.include_router(build_mfy_import_router(
    templates=templates,
    admin_dependency=require_role([AdminRole.SUPER_ADMIN]),
    csrf_dependency=verify_csrf,
    set_csrf_cookie=set_csrf_cookie,
))


@app.post("/admin/mfy")
async def create_mfy_area(
    name_uz: str = Form(...),
    name_uz_cyrillic: str = Form(...),
    name_ru: str = Form(...),
    district: str = Form("Farg'ona tumani"),
    sort_order: int = Form(0),
    admin: AdminUser = Depends(require_role([AdminRole.SUPER_ADMIN])),
    db: AsyncSession = Depends(get_session),
    _=Depends(verify_csrf),
):
    area = MFYArea(
        name_uz=_validate_catalog_name(name_uz, "O'zbekcha nom"),
        name_uz_cyrillic=_validate_catalog_name(name_uz_cyrillic, "Кириллча ном"),
        name_ru=_validate_catalog_name(name_ru, "Ruscha nom"),
        district=_validate_catalog_name(district, "Tuman nomi"),
        sort_order=sort_order,
        is_provisional=True,
    )
    db.add(area)
    await db.flush()
    await _audit_catalog_change(db, admin, "mfy_area", area.id, {"name_uz": area.name_uz})
    return RedirectResponse(url="/admin/mfy", status_code=303)


@app.post("/admin/mfy/{area_id}")
async def update_mfy_area(
    area_id: int,
    name_uz: str = Form(...),
    name_uz_cyrillic: str = Form(...),
    name_ru: str = Form(...),
    district: str = Form("Farg'ona tumani"),
    sort_order: int = Form(0),
    is_provisional: bool = Form(True),
    admin: AdminUser = Depends(require_role([AdminRole.SUPER_ADMIN])),
    db: AsyncSession = Depends(get_session),
    _=Depends(verify_csrf),
):
    area = (await db.execute(select(MFYArea).where(MFYArea.id == area_id))).scalar_one_or_none()
    if area is None:
        raise HTTPException(status_code=404)
    area.name_uz = _validate_catalog_name(name_uz, "O'zbekcha nom")
    area.name_uz_cyrillic = _validate_catalog_name(name_uz_cyrillic, "Кириллча ном")
    area.name_ru = _validate_catalog_name(name_ru, "Ruscha nom")
    area.district = _validate_catalog_name(district, "Tuman nomi")
    area.sort_order = sort_order
    area.is_provisional = is_provisional
    await _audit_catalog_change(db, admin, "mfy_area", area.id, {"name_uz": area.name_uz})
    return RedirectResponse(url="/admin/mfy", status_code=303)


@app.post("/admin/mfy/{area_id}/status")
async def change_mfy_status(
    area_id: int,
    is_active: bool = Form(...),
    admin: AdminUser = Depends(require_role([AdminRole.SUPER_ADMIN])),
    db: AsyncSession = Depends(get_session),
    _=Depends(verify_csrf),
):
    area = (await db.execute(select(MFYArea).where(MFYArea.id == area_id))).scalar_one_or_none()
    if area is None:
        raise HTTPException(status_code=404)
    area.is_active = is_active
    await _audit_catalog_change(db, admin, "mfy_area", area.id, {"is_active": is_active})
    return RedirectResponse(url="/admin/mfy", status_code=303)


# =============================================================================
# User Management Routes
# =============================================================================

@app.get("/admin/users", response_class=HTMLResponse)
async def list_users(
    request: Request,
    admin: AdminUser = Depends(require_role([AdminRole.SUPER_ADMIN])),
    db: AsyncSession = Depends(get_session)
):
    stmt = select(AdminUser).order_by(AdminUser.id.desc())
    users = (await db.execute(stmt)).scalars().all()
    orgs = (await db.execute(select(Organization))).scalars().all()
    csrf_token = get_or_create_csrf_token(request)

    response = templates.TemplateResponse(
        request=request, name="admin/users.html", context={
        "admin": admin,
        "users": users,
        "orgs": orgs,
        "org_names": {organization.id: organization.name_uz for organization in orgs},
        "roles": AdminRole,
        "csrf_token": csrf_token
    })
    response.headers["Cache-Control"] = "no-store, private"
    set_csrf_cookie(response, csrf_token)
    return response

@app.post("/admin/users")
async def create_user(
    request: Request,
    username: str = Form(...),
    full_name: str = Form(...),
    role: str = Form(...),
    organization_id: Optional[int] = Form(None),
    telegram_id: Optional[int] = Form(None),
    admin: AdminUser = Depends(require_role([AdminRole.SUPER_ADMIN])),
    db: AsyncSession = Depends(get_session),
    _=Depends(verify_csrf)
):
    username = username.strip().lower()
    full_name = full_name.strip()
    try:
        role_value = AdminRole(role).value
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Noto'g'ri rol") from exc
    if not re.fullmatch(r"[a-z0-9_.-]{3,100}", username):
        raise HTTPException(status_code=400, detail="Login 3-100 ta lotin harfi, raqam yoki ._- dan iborat bo'lsin")
    if len(full_name) < 3:
        raise HTTPException(status_code=400, detail="F.I.Sh. kamida 3 ta belgidan iborat bo'lsin")
    if telegram_id is not None and telegram_id <= 0:
        raise HTTPException(status_code=400, detail="Telegram ID musbat son bo'lishi kerak")

    requires_org = role_value in {AdminRole.AGENCY_HEAD.value, AdminRole.EXECUTOR.value}
    organization = None
    if requires_org:
        if organization_id is None:
            raise HTTPException(status_code=400, detail="Bu rol uchun idora tanlash shart")
        organization = (await db.execute(
            select(Organization).where(
                Organization.id == organization_id,
                Organization.is_active.is_(True),
            )
        )).scalar_one_or_none()
        if organization is None:
            raise HTTPException(status_code=400, detail="Faol idora topilmadi")

    # Check duplicate username
    existing = await db.execute(select(AdminUser).where(AdminUser.username == username))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Username allaqachon mavjud")

    # Check duplicate telegram_id
    if telegram_id is not None:
        existing_tg = await db.execute(select(AdminUser).where(AdminUser.telegram_id == telegram_id))
        if existing_tg.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Telegram ID allaqachon band")

    # Generate a high-entropy one-time password; force change on first sign-in.
    temp_password = secrets.token_urlsafe(18)
    password_hash = bcrypt.hashpw(temp_password.encode(), bcrypt.gensalt()).decode()

    new_user = AdminUser(
        username=username,
        full_name=full_name,
        password_hash=password_hash,
        role=role_value,
        organization_id=organization_id if requires_org else None,
        telegram_id=telegram_id,
        must_change_password=True
    )
    db.add(new_user)
    try:
        await db.flush()
        db.add(AuditEvent(
            action="user_created",
            entity_type="admin_user",
            entity_id=str(new_user.id),
            actor_type="admin",
            actor_id=str(admin.id),
            new_value=json.dumps({"username": username, "role": role_value, "telegram_id": telegram_id}),
        ))
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Login yoki Telegram ID allaqachon band") from exc

    response = JSONResponse({
        "status": "success",
        "temp_password": temp_password,
        "message": "Foydalanuvchi yaratildi. Bir martalik parol hozir ko'rsatiladi.",
    })
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["Pragma"] = "no-cache"
    return response


@app.post("/admin/users/{user_id}/edit")
async def edit_admin_user(
    user_id: int,
    username: str = Form(...),
    full_name: str = Form(...),
    role: str = Form(...),
    organization_id: Optional[int] = Form(None),
    telegram_id: Optional[int] = Form(None),
    admin: AdminUser = Depends(require_role([AdminRole.SUPER_ADMIN])),
    db: AsyncSession = Depends(get_session),
    _=Depends(verify_csrf),
):
    target = (await db.execute(
        select(AdminUser).where(AdminUser.id == user_id)
    )).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404)

    username = username.strip().lower()
    full_name = full_name.strip()
    try:
        role_value = AdminRole(role).value
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Noto‘g‘ri rol") from exc
    if not re.fullmatch(r"[a-z0-9_.-]{3,100}", username):
        raise HTTPException(status_code=400, detail="Login 3-100 ta lotin harfi, raqam yoki ._- dan iborat bo‘lsin")
    if not 3 <= len(full_name) <= 255:
        raise HTTPException(status_code=400, detail="F.I.Sh. 3-255 ta belgidan iborat bo‘lsin")
    if telegram_id is not None and telegram_id <= 0:
        raise HTTPException(status_code=400, detail="Telegram ID musbat son bo‘lishi kerak")
    if target.id == admin.id and role_value != target.role:
        raise HTTPException(status_code=400, detail="O‘z rolingizni o‘zgartira olmaysiz")

    requires_org = role_value in {AdminRole.AGENCY_HEAD.value, AdminRole.EXECUTOR.value}
    if requires_org:
        organization = (await db.execute(
            select(Organization).where(
                Organization.id == organization_id,
                Organization.is_active.is_(True),
            )
        )).scalar_one_or_none() if organization_id is not None else None
        if organization is None:
            raise HTTPException(status_code=400, detail="Bu rol uchun faol idora tanlash shart")
    else:
        organization_id = None

    duplicate_name = (await db.execute(
        select(AdminUser.id).where(
            AdminUser.username == username,
            AdminUser.id != target.id,
        )
    )).scalar_one_or_none()
    if duplicate_name is not None:
        raise HTTPException(status_code=409, detail="Login allaqachon mavjud")
    if telegram_id is not None:
        duplicate_telegram = (await db.execute(
            select(AdminUser.id).where(
                AdminUser.telegram_id == telegram_id,
                AdminUser.id != target.id,
            )
        )).scalar_one_or_none()
        if duplicate_telegram is not None:
            raise HTTPException(status_code=409, detail="Telegram ID allaqachon band")

    if (
        target.is_active
        and target.role == AdminRole.SUPER_ADMIN.value
        and role_value != AdminRole.SUPER_ADMIN.value
    ):
        active_superadmins = (await db.execute(
            select(func.count(AdminUser.id)).where(
                AdminUser.role == AdminRole.SUPER_ADMIN.value,
                AdminUser.is_active.is_(True),
            )
        )).scalar_one()
        if active_superadmins <= 1:
            raise HTTPException(status_code=400, detail="Oxirgi faol super-admin rolini o‘zgartirib bo‘lmaydi")

    old_value = {
        "username": target.username,
        "role": target.role,
        "organization_id": target.organization_id,
        "telegram_linked": target.telegram_id is not None,
    }
    target.username = username
    target.full_name = full_name
    target.role = role_value
    target.organization_id = organization_id
    target.telegram_id = telegram_id
    new_value = {
        "username": username,
        "role": role_value,
        "organization_id": organization_id,
        "telegram_linked": telegram_id is not None,
    }
    db.add(AuditEvent(
        action="admin_user_updated",
        entity_type="admin_user",
        entity_id=str(target.id),
        actor_type="admin",
        actor_id=str(admin.id),
        old_value=json.dumps(old_value, ensure_ascii=False),
        new_value=json.dumps(new_value, ensure_ascii=False),
    ))
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Login yoki Telegram ID allaqachon band") from exc
    return RedirectResponse(url="/admin/users", status_code=303)

@app.post("/admin/users/{user_id}/status")
async def change_user_status(
    user_id: int,
    is_active: bool = Form(...),
    admin: AdminUser = Depends(require_role([AdminRole.SUPER_ADMIN])),
    db: AsyncSession = Depends(get_session),
    _=Depends(verify_csrf)
):
    target_user = await db.execute(select(AdminUser).where(AdminUser.id == user_id))
    target_user = target_user.scalar_one_or_none()
    if not target_user: raise HTTPException(status_code=404)

    if target_user.id == admin.id and not is_active:
        raise HTTPException(status_code=400, detail="O'zingizning hisobingizni o'chira olmaysiz")
    if target_user.role == AdminRole.SUPER_ADMIN.value and target_user.is_active and not is_active:
        active_superadmins = (await db.execute(
            select(func.count(AdminUser.id)).where(
                AdminUser.role == AdminRole.SUPER_ADMIN.value,
                AdminUser.is_active.is_(True),
            )
        )).scalar_one()
        if active_superadmins <= 1:
            raise HTTPException(status_code=400, detail="Oxirgi faol super-adminni o'chirib bo'lmaydi")

    target_user.is_active = is_active

    # Invalidate sessions if deactivated
    if not is_active:
        await db.execute(update(AdminSession).where(AdminSession.admin_user_id == user_id).values(is_active=False))

    # Audit
    db.add(AuditEvent(
        action="user_status_changed",
        entity_type="admin_user",
        entity_id=str(user_id),
        actor_type="admin",
        actor_id=str(admin.id),
        new_value=json.dumps({"is_active": is_active})
    ))
    await db.commit()
    return RedirectResponse(url="/admin/users", status_code=303)

@app.get("/admin/change-password", response_class=HTMLResponse)
async def change_password_form(
    request: Request,
    admin: AdminUser = Depends(get_current_admin_optional),
    db: AsyncSession = Depends(get_session)
):
    if not admin:
        return RedirectResponse(url="/admin/login", status_code=303)
    csrf_token = get_or_create_csrf_token(request)
    response = templates.TemplateResponse(
        request=request, name="admin/change_password.html", context={
        "admin": admin,
        "csrf_token": csrf_token
    })
    set_csrf_cookie(response, csrf_token)
    return response

@app.post("/admin/change-password")
async def change_password_submit(
    request: Request,
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    admin: AdminUser = Depends(get_current_admin_optional),
    db: AsyncSession = Depends(get_session),
    _=Depends(verify_csrf)
):
    if not admin:
        return RedirectResponse(url="/admin/login", status_code=303)

    if new_password != confirm_password:
        raise HTTPException(status_code=400, detail="Parollar mos emas")
    if len(new_password.encode("utf-8")) > 72:
        raise HTTPException(status_code=400, detail="Parol UTF-8 formatida 72 baytdan oshmasin")
    if len(new_password) < 12:
        raise HTTPException(status_code=400, detail="Yangi parol kamida 12 ta belgidan iborat bo'lsin")

    admin.password_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
    admin.must_change_password = False

    # Invalidate other sessions
    token = request.cookies.get("admin_session")
    await db.execute(update(AdminSession).where(
        and_(AdminSession.admin_user_id == admin.id, AdminSession.session_token.not_in([token, hash_session_token(token or "")]))
    ).values(is_active=False))

    # Audit
    db.add(AuditEvent(
        action="password_changed",
        entity_type="admin_user",
        entity_id=str(admin.id),
        actor_type="admin",
        actor_id=str(admin.id)
    ))
    await db.commit()

    return RedirectResponse(url="/admin/", status_code=303)


@app.post("/admin/complaints/{id}/archive")
async def archive_complaint(
    id: int,
    admin: AdminUser = Depends(require_role([AdminRole.SUPER_ADMIN, AdminRole.DISTRICT_SUPERVISOR])),
    db: AsyncSession = Depends(get_session),
    _=Depends(verify_csrf),
):
    complaint = await ensure_complaint_access(db, id, admin, write=True)
    complaint.archived_at = utcnow()
    complaint.archived_by_id = admin.id
    await AuditService.log_event(
        db, AuditAction.COMPLAINT_ARCHIVED.value, "complaint", "admin",
        entity_id=str(id), actor_id=str(admin.id),
        old_value={"archived": False}, new_value={"archived": True},
    )
    await db.commit()
    return RedirectResponse(url="/admin/complaints?archived=true", status_code=303)


@app.post("/admin/complaints/{id}/restore")
async def restore_complaint(
    id: int,
    admin: AdminUser = Depends(require_role([AdminRole.SUPER_ADMIN, AdminRole.DISTRICT_SUPERVISOR])),
    db: AsyncSession = Depends(get_session),
    _=Depends(verify_csrf),
):
    complaint = await ensure_complaint_access(db, id, admin)
    if complaint.archived_at is None:
        raise HTTPException(status_code=409, detail="Murojaat arxivda emas")
    complaint.archived_at = None
    complaint.archived_by_id = None
    await AuditService.log_event(
        db, AuditAction.COMPLAINT_RESTORED.value, "complaint", "admin",
        entity_id=str(id), actor_id=str(admin.id),
        old_value={"archived": True}, new_value={"archived": False},
    )
    await db.commit()
    return RedirectResponse(url=f"/admin/complaints/{id}", status_code=303)


@app.post("/admin/complaints/{id}/delete")
async def soft_delete_complaint(
    id: int,
    admin: AdminUser = Depends(require_role([AdminRole.SUPER_ADMIN, AdminRole.DISTRICT_SUPERVISOR])),
    db: AsyncSession = Depends(get_session),
    _=Depends(verify_csrf),
):
    complaint = await ensure_complaint_access(db, id, admin)
    if complaint.deleted_at is not None:
        raise HTTPException(status_code=409, detail="Murojaat allaqachon o‘chirilganlar bo‘limida")
    complaint.deleted_at = utcnow()
    complaint.deleted_by_id = admin.id
    await AuditService.log_event(
        db, "complaint_soft_deleted", "complaint", "admin",
        entity_id=str(id), actor_id=str(admin.id),
        old_value={"deleted": False}, new_value={"deleted": True},
    )
    await db.commit()
    return RedirectResponse(url="/admin/complaints?deleted=true", status_code=303)


@app.post("/admin/complaints/{id}/restore-deleted")
async def restore_deleted_complaint(
    id: int,
    admin: AdminUser = Depends(require_role([AdminRole.SUPER_ADMIN, AdminRole.DISTRICT_SUPERVISOR])),
    db: AsyncSession = Depends(get_session),
    _=Depends(verify_csrf),
):
    complaint = await ensure_complaint_access(db, id, admin, include_deleted=True)
    if complaint.deleted_at is None:
        raise HTTPException(status_code=409, detail="Murojaat o‘chirilganlar bo‘limida emas")
    complaint.deleted_at = None
    complaint.deleted_by_id = None
    await AuditService.log_event(
        db, "complaint_restored_from_trash", "complaint", "admin",
        entity_id=str(id), actor_id=str(admin.id),
        old_value={"deleted": True}, new_value={"deleted": False},
    )
    await db.commit()
    target = "/admin/complaints?archived=true" if complaint.archived_at else "/admin/complaints"
    return RedirectResponse(url=target, status_code=303)


# Separate route module for citizen-facing bot configuration.
from app.admin.bot_control import build_bot_control_router

app.include_router(build_bot_control_router(
    templates=templates,
    admin_dependency=require_role([AdminRole.SUPER_ADMIN]),
    csrf_dependency=verify_csrf,
    set_csrf_cookie=set_csrf_cookie,
))

from app.admin.complaint_tools import build_complaint_tools_router

app.include_router(build_complaint_tools_router(
    admin_dependency=get_current_admin,
    csrf_dependency=verify_csrf,
    ensure_access=ensure_complaint_access,
))

from app.admin.notifications import build_notification_router

app.include_router(build_notification_router(
    templates=templates,
    admin_dependency=require_role([AdminRole.SUPER_ADMIN]),
    csrf_dependency=verify_csrf,
    set_csrf_cookie=set_csrf_cookie,
))
