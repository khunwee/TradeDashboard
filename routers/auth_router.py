# =============================================================================
# routers/auth_router.py — Auth Endpoints
# =============================================================================
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr
from typing import Optional
from datetime import datetime, timezone
import logging

from sqlalchemy.orm import Session
from database import get_db
from models import User, AuditLog
from auth import (
    hash_password, verify_password, validate_password_strength,
    create_access_token, create_refresh_token, store_refresh_token,
    rotate_refresh_token, revoke_all_refresh_tokens, hash_token,
    check_lockout, record_failed_attempt, clear_failed_attempts,
    log_login_attempt, create_password_reset_token, verify_password_reset_token,
    generate_totp_secret, get_totp_uri, verify_totp,
    generate_backup_codes, verify_backup_code, get_current_user,
    decode_access_token,
)
from config import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/auth", tags=["Authentication"])


# ── Helper: get role as string safely ────────────────────────────────────────
def role_str(user: User) -> str:
    """Return role as plain string — works whether stored as Enum or String."""
    r = user.role
    return r.value if hasattr(r, "value") else str(r)


# =============================================================================
# SCHEMAS
# =============================================================================

class RegisterRequest(BaseModel):
    email:        EmailStr
    password:     str
    display_name: str

class LoginRequest(BaseModel):
    email:       str
    password:    str
    totp_code:   Optional[str] = None
    remember_me: bool = False

class RefreshRequest(BaseModel):
    refresh_token: str

class PasswordResetRequest(BaseModel):
    email: str

class PasswordResetConfirm(BaseModel):
    token:        str
    new_password: str

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password:     str

class Enable2FARequest(BaseModel):
    totp_code: str


# =============================================================================
# REGISTER
# =============================================================================

@router.post("/register", status_code=201, summary="Register New User")
async def register(req: RegisterRequest, request: Request, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.email == req.email.lower()).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    ok, msg = validate_password_strength(req.password)
    if not ok:
        raise HTTPException(status_code=422, detail=msg)

    user = User(
        email            = req.email.lower(),
        hashed_password  = hash_password(req.password),
        display_name     = req.display_name[:100],
        role             = "trader",
        is_active        = True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    try:
        db.add(AuditLog(
            user_id    = user.id,
            action     = "user.register",
            ip_address = request.client.host if request.client else None,
        ))
        db.commit()
    except Exception as e:
        logger.warning(f"Audit log failed: {e}")

    access_token  = create_access_token(user.id, role_str(user))
    refresh_token = create_refresh_token()
    store_refresh_token(db, user.id, refresh_token)

    return {
        "access_token":  access_token,
        "refresh_token": refresh_token,
        "token_type":    "bearer",
        "user": {
            "id":           user.id,
            "email":        user.email,
            "display_name": user.display_name,
            "role":         role_str(user),
        },
    }


# =============================================================================
# LOGIN
# =============================================================================

@router.post("/login", summary="Login")
async def login(req: LoginRequest, request: Request, db: Session = Depends(get_db)):
    identifier = req.email.lower().strip()

    # Brute-force check
    locked, locked_until = check_lockout(db, identifier)
    if locked:
        raise HTTPException(
            status_code=429,
            detail=f"Account locked until {locked_until.isoformat()}",
        )

    # Find user
    user = db.query(User).filter(User.email == identifier).first()
    if not user:
        record_failed_attempt(db, identifier)
        raise HTTPException(status_code=401, detail="Invalid email or password")

    # Verify password
    if not verify_password(req.password, user.hashed_password):
        record_failed_attempt(db, identifier)
        try:
            log_login_attempt(db, user.id, request, success=False, fail_reason="wrong_password")
        except Exception:
            pass
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is disabled")

    # 2FA check
    if user.totp_enabled and user.totp_secret:
        if not req.totp_code:
            return {"requires_2fa": True, "message": "TOTP code required"}
        # Try TOTP
        if not verify_totp(user.totp_secret, req.totp_code):
            # Try backup code
            valid, updated = verify_backup_code(user.backup_codes or [], req.totp_code)
            if valid:
                user.backup_codes = updated
                db.commit()
            else:
                record_failed_attempt(db, identifier)
                raise HTTPException(status_code=401, detail="Invalid 2FA code")

    # SUCCESS
    clear_failed_attempts(db, identifier)
    try:
        log_login_attempt(db, user.id, request, success=True)
    except Exception:
        pass

    user.last_login_at = datetime.now(timezone.utc)
    db.commit()

    access_token  = create_access_token(user.id, role_str(user))
    refresh_token = create_refresh_token()
    device_fp     = request.headers.get("X-Device-Fingerprint")
    store_refresh_token(db, user.id, refresh_token, device_fp)

    return {
        "access_token":  access_token,
        "refresh_token": refresh_token,
        "token_type":    "bearer",
        "user": {
            "id":           user.id,
            "email":        user.email,
            "display_name": user.display_name,
            "role":         role_str(user),
            "totp_enabled": bool(user.totp_enabled),
            "timezone":     user.timezone or "UTC",
            "theme":        user.theme or "dark",
        },
    }


# =============================================================================
# REFRESH TOKEN
# =============================================================================

@router.post("/refresh", summary="Refresh Access Token")
async def refresh_token(req: RefreshRequest, request: Request, db: Session = Depends(get_db)):
    from models import RefreshToken
    token_hash = hash_token(req.refresh_token)
    rt = db.query(RefreshToken).filter(
        RefreshToken.token_hash == token_hash,
        RefreshToken.revoked    == False,
    ).first()
    if not rt:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    user = db.query(User).filter(User.id == rt.user_id).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found")

    device_fp   = request.headers.get("X-Device-Fingerprint")
    new_refresh = rotate_refresh_token(db, req.refresh_token, rt.user_id, device_fp)
    if not new_refresh:
        raise HTTPException(status_code=401, detail="Refresh token expired")

    access_token = create_access_token(user.id, role_str(user))
    return {
        "access_token":  access_token,
        "refresh_token": new_refresh,
        "token_type":    "bearer",
    }


# =============================================================================
# LOGOUT
# =============================================================================

@router.post("/logout", summary="Logout")
async def logout(req: RefreshRequest, db: Session = Depends(get_db)):
    from models import RefreshToken
    token_hash = hash_token(req.refresh_token)
    rt = db.query(RefreshToken).filter(RefreshToken.token_hash == token_hash).first()
    if rt:
        rt.revoked = True
        db.commit()
    return {"message": "Logged out successfully"}


@router.post("/logout-all", summary="Logout All Devices")
async def logout_all(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    revoke_all_refresh_tokens(db, current_user.id)
    return {"message": "All sessions revoked"}


# =============================================================================
# PASSWORD RESET
# =============================================================================

@router.post("/password-reset-request", summary="Request Password Reset")
async def request_password_reset(req: PasswordResetRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == req.email.lower()).first()
    if user:
        token     = create_password_reset_token(db, user.id)
        reset_url = f"{settings.FRONTEND_URL}/static/reset-password.html?token={token}"
        logger.info(f"Password reset URL for {user.email}: {reset_url}")
    return {"message": "If the email exists, a reset link has been sent"}


@router.post("/password-reset-confirm", summary="Confirm Password Reset")
async def confirm_password_reset(req: PasswordResetConfirm, db: Session = Depends(get_db)):
    user_id = verify_password_reset_token(db, req.token)
    if not user_id:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    ok, msg = validate_password_strength(req.new_password)
    if not ok:
        raise HTTPException(status_code=422, detail=msg)

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.hashed_password = hash_password(req.new_password)
    revoke_all_refresh_tokens(db, user_id)
    db.commit()
    return {"message": "Password reset successfully"}


@router.post("/change-password", summary="Change Password")
async def change_password(
    req: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(req.current_password, current_user.hashed_password):
        raise HTTPException(status_code=401, detail="Current password is incorrect")
    ok, msg = validate_password_strength(req.new_password)
    if not ok:
        raise HTTPException(status_code=422, detail=msg)
    current_user.hashed_password = hash_password(req.new_password)
    db.commit()
    return {"message": "Password changed successfully"}


# =============================================================================
# 2FA
# =============================================================================

@router.post("/2fa/setup", summary="Initialize 2FA Setup")
async def setup_2fa(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    secret = generate_totp_secret()
    current_user.totp_secret = secret
    db.commit()
    uri = get_totp_uri(secret, current_user.email)
    return {"secret": secret, "uri": uri, "message": "Scan QR code then verify"}


@router.post("/2fa/enable", summary="Enable 2FA")
async def enable_2fa(
    req: Enable2FARequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not current_user.totp_secret:
        raise HTTPException(status_code=400, detail="Call /2fa/setup first")
    if not verify_totp(current_user.totp_secret, req.totp_code):
        raise HTTPException(status_code=400, detail="Invalid TOTP code")
    plain_codes, hashed_codes = generate_backup_codes()
    current_user.totp_enabled = True
    current_user.backup_codes = hashed_codes
    db.commit()
    return {"message": "2FA enabled", "backup_codes": plain_codes}


@router.post("/2fa/disable", summary="Disable 2FA")
async def disable_2fa(
    req: Enable2FARequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not verify_totp(current_user.totp_secret, req.totp_code):
        raise HTTPException(status_code=400, detail="Invalid TOTP code")
    current_user.totp_enabled = False
    current_user.totp_secret  = None
    current_user.backup_codes = []
    db.commit()
    return {"message": "2FA disabled"}


# =============================================================================
# PROFILE
# =============================================================================

@router.get("/me", summary="Get Current User")
async def get_me(current_user: User = Depends(get_current_user)):
    return {
        "id":             current_user.id,
        "email":          current_user.email,
        "display_name":   current_user.display_name,
        "role":           role_str(current_user),
        "avatar_url":     current_user.avatar_url,
        "timezone":       current_user.timezone or "UTC",
        "language":       current_user.language or "en",
        "currency_display": current_user.currency_display or "USD",
        "theme":          current_user.theme or "dark",
        "totp_enabled":   bool(current_user.totp_enabled),
        "telegram_chat_id": current_user.telegram_chat_id,
        "line_notify_token": bool(current_user.line_notify_token),
        "quiet_hours_start": current_user.quiet_hours_start,
        "quiet_hours_end":   current_user.quiet_hours_end,
    }


@router.patch("/me", summary="Update Profile")
async def update_profile(
    updates: dict,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    allowed = {
        "display_name", "timezone", "language", "currency_display",
        "theme", "font_size", "density", "telegram_chat_id",
        "line_notify_token", "quiet_hours_start", "quiet_hours_end",
    }
    for key, val in updates.items():
        if key in allowed:
            setattr(current_user, key, val)
    db.commit()
    return {"message": "Profile updated"}


@router.get("/login-history", summary="Login History")
async def login_history(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from models import LoginHistory
    history = db.query(LoginHistory).filter(
        LoginHistory.user_id == current_user.id
    ).order_by(LoginHistory.created_at.desc()).limit(50).all()
    return [
        {
            "id":           h.id,
            "ip_address":   h.ip_address,
            "device":       h.device,
            "success":      h.success,
            "fail_reason":  h.fail_reason,
            "created_at":   h.created_at.isoformat() if h.created_at else None,
        }
        for h in history
    ]
