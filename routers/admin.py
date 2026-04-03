# =============================================================================
# routers/admin.py — Super Admin Console
# User management, system stats, impersonation, feature flags
# =============================================================================
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, EmailStr
from typing import Optional, List
from datetime import datetime, timezone, timedelta
import logging

from sqlalchemy.orm import Session
from sqlalchemy import func
from database import get_db
from models import (
    User, Account, ClosedTrade, AccountSnapshot, AuditLog,
    AlertHistory, Notification, UserRole
)
from auth import (
    get_current_user, require_super_admin, hash_password,
    create_access_token, revoke_all_refresh_tokens
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/admin", tags=["Admin"])


# =============================================================================
# SCHEMAS
# =============================================================================

class UserCreateAdmin(BaseModel):
    email:        EmailStr
    password:     str
    display_name: str
    role:         str = "trader"

class UserUpdateAdmin(BaseModel):
    role:      Optional[str] = None
    is_active: Optional[bool] = None
    display_name: Optional[str] = None

class FeatureFlagUpdate(BaseModel):
    key:   str
    value: bool

# In-memory feature flags (production: store in DB or Redis)
_feature_flags: dict = {
    "registration_open": True,
    "public_investor_links": True,
    "telegram_alerts": True,
    "line_alerts": True,
    "sms_alerts": False,
}


# =============================================================================
# SYSTEM STATS
# =============================================================================

@router.get("/stats", summary="System Statistics")
async def system_stats(
    _: User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    now = datetime.now(timezone.utc)
    day_ago = now - timedelta(days=1)
    week_ago = now - timedelta(days=7)

    total_users    = db.query(func.count(User.id)).scalar()
    active_users   = db.query(func.count(User.id)).filter(User.is_active == True).scalar()
    total_accounts = db.query(func.count(Account.id)).filter(Account.is_active == True).scalar()
    total_trades   = db.query(func.count(ClosedTrade.id)).scalar()
    total_snaps    = db.query(func.count(AccountSnapshot.id)).scalar()

    new_users_today = db.query(func.count(User.id)).filter(User.created_at >= day_ago).scalar()
    new_users_week  = db.query(func.count(User.id)).filter(User.created_at >= week_ago).scalar()

    alerts_today = db.query(func.count(AlertHistory.id)).filter(
        AlertHistory.triggered_at >= day_ago
    ).scalar()

    from models import AccountStatus
    live_accounts = db.query(func.count(Account.id)).filter(
        Account.status == AccountStatus.LIVE
    ).scalar()
    disconnected_accounts = db.query(func.count(Account.id)).filter(
        Account.status == AccountStatus.DISCONNECTED
    ).scalar()

    return {
        "users": {
            "total":       total_users,
            "active":      active_users,
            "new_today":   new_users_today,
            "new_week":    new_users_week,
        },
        "accounts": {
            "total":        total_accounts,
            "live":         live_accounts,
            "disconnected": disconnected_accounts,
        },
        "data": {
            "total_trades":    total_trades,
            "total_snapshots": total_snaps,
        },
        "alerts": {
            "fired_today": alerts_today,
        },
        "server_time": now.isoformat(),
    }


# =============================================================================
# USER MANAGEMENT
# =============================================================================

@router.get("/users", summary="List All Users")
async def list_users(
    search:   Optional[str] = Query(None),
    role:     Optional[str] = Query(None),
    page:     int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    _: User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    q = db.query(User)
    if search:
        q = q.filter(
            User.email.ilike(f"%{search}%") | User.display_name.ilike(f"%{search}%")
        )
    if role:
        try:
            q = q.filter(User.role == UserRole(role))
        except ValueError:
            pass

    total = q.count()
    users = q.order_by(User.created_at.desc()).offset((page-1)*page_size).limit(page_size).all()

    return {
        "users": [_format_user(u, db) for u in users],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/users/{user_id}", summary="Get User Detail")
async def get_user(
    user_id: str,
    _: User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return _format_user(user, db)


@router.post("/users", status_code=201, summary="Create User (Admin)")
async def create_user(
    req: UserCreateAdmin,
    admin: User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    existing = db.query(User).filter(User.email == req.email.lower()).first()
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")

    try:
        role = UserRole(req.role)
    except ValueError:
        role = UserRole.TRADER

    user = User(
        email=req.email.lower(),
        hashed_password=hash_password(req.password),
        display_name=req.display_name,
        role=role,
        is_active=True,
        is_verified=True,
    )
    db.add(user)
    db.add(AuditLog(user_id=admin.id, action="admin.user.create", resource="user",
                    details={"email": req.email, "role": req.role}))
    db.commit()
    db.refresh(user)
    return _format_user(user, db)


@router.patch("/users/{user_id}", summary="Update User (Admin)")
async def update_user(
    user_id: str,
    req: UserUpdateAdmin,
    admin: User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if req.role is not None:
        try:
            user.role = UserRole(req.role)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Invalid role: {req.role}")
    if req.is_active is not None:
        user.is_active = req.is_active
        if not req.is_active:
            revoke_all_refresh_tokens(db, user_id)
    if req.display_name is not None:
        user.display_name = req.display_name

    db.add(AuditLog(user_id=admin.id, action="admin.user.update", resource="user",
                    resource_id=user_id, details=req.dict(exclude_none=True)))
    db.commit()
    return _format_user(user, db)


@router.delete("/users/{user_id}", summary="Deactivate User (Admin)")
async def deactivate_user(
    user_id: str,
    admin: User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="Cannot deactivate yourself")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.is_active = False
    revoke_all_refresh_tokens(db, user_id)
    db.add(AuditLog(user_id=admin.id, action="admin.user.deactivate", resource="user", resource_id=user_id))
    db.commit()
    return {"message": "User deactivated"}


# =============================================================================
# IMPERSONATION
# =============================================================================

@router.post("/impersonate/{user_id}", summary="Impersonate User (Super Admin Only)")
async def impersonate_user(
    user_id: str,
    admin: User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    """Generate a short-lived access token for a user (audit logged)."""
    user = db.query(User).filter(User.id == user_id, User.is_active == True).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.role == UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Cannot impersonate other super admins")

    db.add(AuditLog(
        user_id=admin.id,
        action="admin.impersonate",
        resource="user",
        resource_id=user_id,
        details={"impersonated_email": user.email},
    ))
    db.commit()

    # Generate short-lived token (15 min)
    token = create_access_token(user.id, user.role.value)
    return {
        "access_token": token,
        "expires_in":   900,
        "message":      f"Impersonating {user.email} — token expires in 15 minutes",
    }


# =============================================================================
# AUDIT LOGS
# =============================================================================

@router.get("/audit-logs", summary="System Audit Logs")
async def audit_logs(
    user_id:  Optional[str] = Query(None),
    action:   Optional[str] = Query(None),
    resource: Optional[str] = Query(None),
    days:     int = Query(7, ge=1, le=90),
    page:     int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    _: User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    q = db.query(AuditLog).filter(AuditLog.created_at >= cutoff)

    if user_id:  q = q.filter(AuditLog.user_id == user_id)
    if action:   q = q.filter(AuditLog.action.ilike(f"%{action}%"))
    if resource: q = q.filter(AuditLog.resource == resource)

    total = q.count()
    logs  = q.order_by(AuditLog.created_at.desc()).offset((page-1)*page_size).limit(page_size).all()

    return {
        "logs": [
            {
                "id": l.id, "user_id": l.user_id, "action": l.action,
                "resource": l.resource, "resource_id": l.resource_id,
                "details": l.details, "ip_address": l.ip_address,
                "created_at": l.created_at.isoformat(),
            }
            for l in logs
        ],
        "total": total,
    }


# =============================================================================
# FEATURE FLAGS
# =============================================================================

@router.get("/feature-flags", summary="List Feature Flags")
async def list_flags(_: User = Depends(require_super_admin)):
    return _feature_flags


@router.patch("/feature-flags", summary="Update Feature Flag")
async def update_flag(
    req: FeatureFlagUpdate,
    admin: User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    if req.key not in _feature_flags:
        raise HTTPException(status_code=404, detail=f"Flag '{req.key}' not found")
    _feature_flags[req.key] = req.value
    db.add(AuditLog(user_id=admin.id, action="admin.feature_flag.update",
                    details={"key": req.key, "value": req.value}))
    db.commit()
    return {"message": f"Flag '{req.key}' set to {req.value}"}


# =============================================================================
# DATA MANAGEMENT
# =============================================================================

@router.post("/maintenance/purge-snapshots", summary="Manually Purge Old Snapshots")
async def purge_snapshots(
    days: int = Query(90, ge=30),
    admin: User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    from models import AccountSnapshot
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    deleted = db.query(AccountSnapshot).filter(AccountSnapshot.ts < cutoff).delete()
    db.add(AuditLog(user_id=admin.id, action="admin.purge_snapshots",
                    details={"days": days, "deleted_count": deleted}))
    db.commit()
    return {"message": f"Purged {deleted} snapshots older than {days} days"}


@router.post("/maintenance/recalculate-all", summary="Recalculate All Account Stats")
async def recalculate_all(
    admin: User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    from calculations import recalculate_all_stats
    accounts = db.query(Account).filter(Account.is_active == True).all()
    for account in accounts:
        recalculate_all_stats(db, account)
    db.add(AuditLog(user_id=admin.id, action="admin.recalculate_all",
                    details={"account_count": len(accounts)}))
    db.commit()
    return {"message": f"Recalculated stats for {len(accounts)} accounts"}


# =============================================================================
# HELPERS
# =============================================================================

def _format_user(user: User, db: Session) -> dict:
    account_count = db.query(func.count(Account.id)).filter(
        Account.owner_id == user.id, Account.is_active == True
    ).scalar()
    return {
        "id":           user.id,
        "email":        user.email,
        "display_name": user.display_name,
        "role":         user.role.value,
        "is_active":    user.is_active,
        "is_verified":  user.is_verified,
        "totp_enabled": user.totp_enabled,
        "account_count": account_count,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        "created_at":   user.created_at.isoformat() if user.created_at else None,
        "timezone":     user.timezone,
    }
