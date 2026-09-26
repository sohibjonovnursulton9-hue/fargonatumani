import secrets
import bcrypt
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Optional, List

from fastapi import FastAPI, Depends, Request, Form, HTTPException, status, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc, func, update

from app.database import get_session
from app.models import (
    AdminUser, AdminRole, AdminSession, Complaint, ComplaintStatus, AuditAction, AuditEvent,
    Assignment, Response as ComplaintResponse, StatusEvent,
    VALID_TRANSITIONS, utcnow
)
from app.services import (
    ComplaintService, AdminAuthService, AuditService, StatisticsService, CatalogService,
    AssignmentService, ResponseService, DeadlineService
)
from app.logging_config import get_logger

logger = get_logger(__name__)

app = FastAPI(title="FTMT Admin Panel", docs_url=None, redoc_url=None)

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

# Dependencies
async def get_current_admin_optional(request: Request, db: AsyncSession = Depends(get_session)) -> Optional[AdminUser]:
    token = request.cookies.get("admin_session")
    if not token:
        return None
        
    stmt = select(AdminSession).where(
        and_(
            AdminSession.session_token == token,
            AdminSession.is_active == True,
            AdminSession.expires_at > utcnow()
        )
    )
    result = await db.execute(stmt)
    session_obj = result.scalar_one_or_none()
    
    if not session_obj:
        return None
        
    admin_stmt = select(AdminUser).where(and_(AdminUser.id == session_obj.admin_user_id, AdminUser.is_active == True))
    admin_result = await db.execute(admin_stmt)
    return admin_result.scalar_one_or_none()

async def get_current_admin(request: Request, admin: Optional[AdminUser] = Depends(get_current_admin_optional)):
    if not admin:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/admin/login"})
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
        if not token or not cookie_token or token != cookie_token:
            raise HTTPException(status_code=403, detail="CSRF token validation failed")

# Health check
@app.get("/health")
async def health_check():
    return {"status": "ok"}

# Routes
@app.get("/admin/login", response_class=HTMLResponse)
async def login_form(request: Request):
    csrf_token = secrets.token_urlsafe(32)
    response = templates.TemplateResponse("admin/login.html", {"request": request, "csrf_token": csrf_token})
    response.set_cookie(key="csrf_token", value=csrf_token, httponly=True, samesite="lax")
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
    admin = await AdminAuthService.get_admin_by_username(db, username)
    if not admin or not admin.is_active:
        return templates.TemplateResponse("admin/login.html", {"request": request, "error": "Invalid credentials", "csrf_token": request.cookies.get("csrf_token")})
        
    if await AdminAuthService.check_lockout(db, admin):
        return templates.TemplateResponse("admin/login.html", {"request": request, "error": "Account is locked", "csrf_token": request.cookies.get("csrf_token")})
        
    try:
        if not bcrypt.checkpw(password.encode('utf-8'), admin.password_hash.encode('utf-8')):
            await AdminAuthService.record_failed_login(db, admin)
            return templates.TemplateResponse("admin/login.html", {"request": request, "error": "Invalid credentials", "csrf_token": request.cookies.get("csrf_token")})
    except ValueError:
        return templates.TemplateResponse("admin/login.html", {"request": request, "error": "Invalid credentials", "csrf_token": request.cookies.get("csrf_token")})

    await AdminAuthService.record_successful_login(db, admin)
    
    token = secrets.token_urlsafe(32)
    admin_session = AdminSession(
        session_token=token,
        admin_user_id=admin.id,
        expires_at=utcnow() + timedelta(hours=8),
        ip_address=request.client.host if request.client else None
    )
    db.add(admin_session)
    await db.commit()
    
    redirect = RedirectResponse(url="/admin/", status_code=303)
    redirect.set_cookie(key="admin_session", value=token, httponly=True, samesite="lax", max_age=8*3600)
    return redirect

@app.post("/admin/logout")
async def logout(request: Request, response: Response, db: AsyncSession = Depends(get_session), _=Depends(verify_csrf)):
    token = request.cookies.get("admin_session")
    if token:
        await db.execute(update(AdminSession).where(AdminSession.session_token == token).values(is_active=False))
        await db.commit()
        
    redirect = RedirectResponse(url="/admin/login", status_code=303)
    redirect.delete_cookie("admin_session")
    return redirect

@app.get("/admin/", response_class=HTMLResponse)
async def dashboard(request: Request, admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_session)):
    stats = await StatisticsService.get_complaint_counts(db)
    
    # Recent complaints based on role
    stmt = select(Complaint).order_by(desc(Complaint.created_at)).limit(10)
    if admin.role in [AdminRole.AGENCY_HEAD, AdminRole.EXECUTOR]:
        # Simple subquery logic for related complaints (executors/agency_head)
        stmt = stmt.join(Assignment).where(Assignment.organization_id == admin.organization_id)
        if admin.role == AdminRole.EXECUTOR:
            stmt = stmt.where(Assignment.assigned_to_id == admin.id)
            
    recent = (await db.execute(stmt)).scalars().all()
    warnings = await DeadlineService.get_approaching_deadlines(db)
    
    csrf_token = secrets.token_urlsafe(32)
    response = templates.TemplateResponse("admin/dashboard.html", {
        "request": request,
        "admin": admin,
        "stats": stats,
        "recent_complaints": recent,
        "warnings": warnings,
        "csrf_token": csrf_token
    })
    response.set_cookie(key="csrf_token", value=csrf_token, httponly=True, samesite="lax")
    return response

@app.get("/admin/complaints", response_class=HTMLResponse)
async def list_complaints(
    request: Request, 
    page: int = 1,
    status: Optional[str] = None,
    tracking_id: Optional[str] = None,
    admin: AdminUser = Depends(get_current_admin), 
    db: AsyncSession = Depends(get_session)
):
    per_page = 20
    offset = (page - 1) * per_page
    
    stmt = select(Complaint).order_by(desc(Complaint.created_at))
    
    # Filter by role
    if admin.role in [AdminRole.AGENCY_HEAD.value, AdminRole.EXECUTOR.value]:
        stmt = stmt.join(Assignment).where(Assignment.organization_id == admin.organization_id)
        if admin.role == AdminRole.EXECUTOR.value:
            stmt = stmt.where(Assignment.assigned_to_id == admin.id)
            
    if status:
        stmt = stmt.where(Complaint.status == status)
    if tracking_id:
        stmt = stmt.where(Complaint.tracking_id.ilike(f"%{tracking_id}%"))
        
    total_stmt = select(func.count()).select_from(stmt.subquery())
    total_items = (await db.execute(total_stmt)).scalar() or 0
    
    stmt = stmt.offset(offset).limit(per_page)
    complaints = (await db.execute(stmt)).scalars().all()
    
    total_pages = (total_items + per_page - 1) // per_page
    
    csrf_token = secrets.token_urlsafe(32)
    response = templates.TemplateResponse("admin/complaints/list.html", {
        "request": request,
        "admin": admin,
        "complaints": complaints,
        "page": page,
        "total_pages": total_pages,
        "status": status,
        "tracking_id": tracking_id,
        "csrf_token": csrf_token
    })
    response.set_cookie(key="csrf_token", value=csrf_token, httponly=True, samesite="lax")
    
    # Audit log bulk export or view (here just view list)
    await AuditService.log_event(db, AuditAction.BULK_ACTION.value, "complaint", "admin", actor_id=str(admin.id), metadata={"action": "list_view"})
    return response

@app.get("/admin/complaints/{id}", response_class=HTMLResponse)
async def detail_complaint(id: int, request: Request, admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_session)):
    stmt = select(Complaint).where(Complaint.id == id)
    complaint = (await db.execute(stmt)).scalar_one_or_none()
    
    if not complaint:
        raise HTTPException(status_code=404)
        
    # Check access
    if admin.role in [AdminRole.AGENCY_HEAD.value, AdminRole.EXECUTOR.value]:
        # ensure assigned
        ass_stmt = select(Assignment).where(and_(Assignment.complaint_id == id, Assignment.organization_id == admin.organization_id))
        if admin.role == AdminRole.EXECUTOR.value:
            ass_stmt = ass_stmt.where(Assignment.assigned_to_id == admin.id)
        if not (await db.execute(ass_stmt)).first():
            raise HTTPException(status_code=403, detail="Not authorized to view this complaint")

    # Fetch related data
    events = (await db.execute(select(StatusEvent).where(StatusEvent.complaint_id == id).order_by(StatusEvent.created_at))).scalars().all()
    assignments = (await db.execute(select(Assignment).where(Assignment.complaint_id == id))).scalars().all()
    responses = (await db.execute(select(ComplaintResponse).where(ComplaintResponse.complaint_id == id))).scalars().all()
    organizations = await CatalogService.get_organizations_for_category(db, complaint.category_id)
    
    csrf_token = secrets.token_urlsafe(32)
    response = templates.TemplateResponse("admin/complaints/detail.html", {
        "request": request,
        "admin": admin,
        "complaint": complaint,
        "events": events,
        "assignments": assignments,
        "responses": responses,
        "valid_transitions": [s.value for s in VALID_TRANSITIONS.get(ComplaintStatus(complaint.status), [])],
        "organizations": organizations,
        "csrf_token": csrf_token
    })
    response.set_cookie(key="csrf_token", value=csrf_token, httponly=True, samesite="lax")
    return response

@app.post("/admin/complaints/{id}/triage")
async def triage_complaint(
    id: int, 
    request: Request,
    admin: AdminUser = Depends(require_role([AdminRole.SUPER_ADMIN, AdminRole.DISTRICT_SUPERVISOR])), 
    db: AsyncSession = Depends(get_session),
    _=Depends(verify_csrf)
):
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
    if admin.role == AdminRole.AGENCY_HEAD.value and organization_id != admin.organization_id:
        raise HTTPException(status_code=403, detail="Cannot assign outside your organization")
        
    await AssignmentService.assign_complaint(db, id, organization_id, admin.id, assigned_to_id, notes=notes)
    
    # Auto transition to routed if in triage
    comp = (await db.execute(select(Complaint).where(Complaint.id == id))).scalar_one()
    if comp.status == ComplaintStatus.TRIAGE.value:
        await ComplaintService.transition_status(db, id, ComplaintStatus.ROUTED, "admin", str(admin.id), reason="Assigned to organization")
        
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
    if admin.role == AdminRole.AUDITOR.value:
        raise HTTPException(status_code=403)
        
    try:
        status_enum = ComplaintStatus(new_status)
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
    await ResponseService.add_response(db, id, response_text, response_language, admin.id, actions_taken)
    return RedirectResponse(url=f"/admin/complaints/{id}", status_code=303)

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
    from app.models import Deadline
    deadline = (await db.execute(select(Deadline).where(Deadline.complaint_id == id))).scalar_one()
    new_deadline = deadline.current_deadline + timedelta(days=days)
    await DeadlineService.extend_deadline(db, deadline.id, new_deadline, reason, admin.id)
    return RedirectResponse(url=f"/admin/complaints/{id}", status_code=303)

@app.get("/admin/statistics", response_class=HTMLResponse)
async def view_statistics(request: Request, admin: AdminUser = Depends(get_current_admin), db: AsyncSession = Depends(get_session)):
    stats = await StatisticsService.get_complaint_counts(db)
    res_time = await StatisticsService.get_resolution_time_stats(db)
    
    return templates.TemplateResponse("admin/statistics.html", {
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
    
    return templates.TemplateResponse("admin/audit.html", {
        "request": request,
        "admin": admin,
        "events": events,
        "page": page,
        "total_pages": total_pages
    })

# Stubs for Categories/Orgs management
@app.get("/admin/categories")
async def list_categories(admin: AdminUser = Depends(require_role([AdminRole.SUPER_ADMIN])), db: AsyncSession = Depends(get_session)):
    cats = await CatalogService.get_active_categories(db)
    return {"categories": [c.name_uz for c in cats]}

@app.post("/admin/categories")
async def create_category(admin: AdminUser = Depends(require_role([AdminRole.SUPER_ADMIN])), db: AsyncSession = Depends(get_session)):
    return {"status": "not implemented"}

@app.get("/admin/organizations")
async def list_organizations(admin: AdminUser = Depends(require_role([AdminRole.SUPER_ADMIN])), db: AsyncSession = Depends(get_session)):
    return {"status": "not implemented"}

@app.post("/admin/organizations")
async def create_organization(admin: AdminUser = Depends(require_role([AdminRole.SUPER_ADMIN])), db: AsyncSession = Depends(get_session)):
    return {"status": "not implemented"}
