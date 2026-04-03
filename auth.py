# =============================================================================
# auth.py — Authentication: JWT, Password Hashing, 2FA, Brute-Force Protection
# Handles bcrypt/passlib version conflicts automatically
# =============================================================================
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple, List
import secrets
import hashlib
import string
import logging

from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from config import settings
from database import get_db
from models import (
    User, RefreshToken, FailedLoginAttempt, LoginHistory,
    PasswordResetToken
)

logger = logging.getLogger(__name__)

bearer_scheme = HTTPBearer(auto_error=False)

LOCKOUT_ATTEMPTS     = 5
LOCKOUT_DURATION_MIN = 15
BCRYPT_MAX_BYTES     = 72


# =============================================================================
# PASSWORD HASHING — tries multiple methods, always succeeds
# =============================================================================

def hash_password(password: str) -> str:
    """Hash password. Tries passlib, then direct bcrypt, then sha256."""
    pw = password[:BCRYPT_MAX_BYTES]

    # Method 1: passlib (preferred)
    try:
        from passlib.context import CryptContext
        ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
        return ctx.hash(pw)
    except Exception as e:
        logger.debug(f"passlib hash failed: {e}")

    # Method 2: direct bcrypt
    try:
        import bcrypt as _bcrypt
        salt = _bcrypt.gensalt(rounds=12)
        return _bcrypt.hashpw(pw.encode("utf-8"), salt).decode("utf-8")
    except Exception as e:
        logger.debug(f"direct bcrypt hash failed: {e}")

    # Method 3: sha256 fallback
    return _sha256_hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    """Verify password. Handles bcrypt, sha256, and all fallback formats."""
    if not plain or not hashed:
        return False

    # sha256 formats
    if hashed.startswith("sha256$"):
        return _sha256_verify(plain, hashed)

    pw = plain[:BCRYPT_MAX_BYTES]

    # Method 1: passlib verify
    try:
        from passlib.context import CryptContext
        ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
        return ctx.verify(pw, hashed)
    except Exception as e:
        logger.debug(f"passlib verify failed: {e}")

    # Method 2: direct bcrypt verify
    try:
        import bcrypt as _bcrypt
        return _bcrypt.checkpw(pw.encode("utf-8"), hashed.encode("utf-8"))
    except Exception as e:
        logger.debug(f"direct bcrypt verify failed: {e}")

    return False


def _sha256_hash(password: str) -> str:
    salt   = secrets.token_hex(16)
    digest = hashlib.sha256((salt + password).encode()).hexdigest()
    return f"sha256${salt}${digest}"


def _sha256_verify(plain: str, hashed: str) -> bool:
    try:
        parts = hashed.split("$")
        if len(parts) < 3:
            return False
        salt, stored = parts[1], parts[2]
        # Handle the "override" special case from create_admin.py
        if salt == "override":
            digest = hashlib.sha256(("override" + plain).encode()).hexdigest()
        else:
            digest = hashlib.sha256((salt + plain).encode()).hexdigest()
        return secrets.compare_digest(digest, stored)
    except Exception:
        return False


def validate_password_strength(password: str) -> Tuple[bool, str]:
    if len(password) < 8:
        return False, "Password must be at least 8 characters"
    if not any(c.isupper() for c in password):
        return False, "Password must contain at least one uppercase letter"
    if not any(c.isdigit() for c in password):
        return False, "Password must contain at least one number"
    if not any(c in string.punctuation for c in password):
        return False, "Password must contain at least one special character"
    return True, "OK"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# =============================================================================
# JWT TOKENS
# =============================================================================

def create_access_token(user_id: str, role: str) -> str:
    payload = {
        "sub":  user_id,
        "role": role,
        "type": "access",
        "exp":  datetime.now(timezone.utc) + timedelta(
                    minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        "iat":  datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_refresh_token() -> str:
    return secrets.token_urlsafe(64)


def decode_access_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        if payload.get("type") != "access":
            return None
        return payload
    except JWTError:
        return None


def store_refresh_token(
    db: Session, user_id: str, token: str,
    device_fp: Optional[str] = None
) -> RefreshToken:
    rt = RefreshToken(
        user_id    = user_id,
        token_hash = hash_token(token),
        device_fp  = device_fp,
        expires_at = datetime.now(timezone.utc) + timedelta(
                         days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
    )
    db.add(rt)
    db.commit()
    return rt


def rotate_refresh_token(
    db: Session, old_token: str, user_id: str,
    device_fp: Optional[str] = None
) -> Optional[str]:
    old_hash = hash_token(old_token)
    rt = db.query(RefreshToken).filter(
        RefreshToken.token_hash == old_hash,
        RefreshToken.user_id   == user_id,
        RefreshToken.revoked   == False,
    ).first()
    if not rt:
        return None
    now = datetime.now(timezone.utc)
    exp = rt.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if exp < now:
        return None
    rt.revoked = True
    db.commit()
    new_token = create_refresh_token()
    store_refresh_token(db, user_id, new_token, device_fp)
    return new_token


def revoke_all_refresh_tokens(db: Session, user_id: str):
    db.query(RefreshToken).filter(
        RefreshToken.user_id == user_id,
        RefreshToken.revoked == False,
    ).update({"revoked": True})
    db.commit()


# =============================================================================
# BRUTE-FORCE PROTECTION
# =============================================================================

def check_lockout(db: Session, identifier: str) -> Tuple[bool, Optional[datetime]]:
    record = db.query(FailedLoginAttempt).filter(
        FailedLoginAttempt.identifier == identifier
    ).first()
    if not record or not record.locked_until:
        return False, None
    now = datetime.now(timezone.utc)
    locked_until = record.locked_until
    if locked_until.tzinfo is None:
        locked_until = locked_until.replace(tzinfo=timezone.utc)
    if locked_until > now:
        return True, locked_until
    db.delete(record)
    db.commit()
    return False, None


def record_failed_attempt(db: Session, identifier: str):
    record = db.query(FailedLoginAttempt).filter(
        FailedLoginAttempt.identifier == identifier
    ).first()
    if record:
        record.attempts += 1
        if record.attempts >= LOCKOUT_ATTEMPTS:
            record.locked_until = datetime.now(timezone.utc) + timedelta(
                minutes=LOCKOUT_DURATION_MIN)
    else:
        record = FailedLoginAttempt(identifier=identifier, attempts=1)
        db.add(record)
    db.commit()


def clear_failed_attempts(db: Session, identifier: str):
    db.query(FailedLoginAttempt).filter(
        FailedLoginAttempt.identifier == identifier
    ).delete()
    db.commit()


# =============================================================================
# LOGIN HISTORY
# =============================================================================

def log_login_attempt(
    db: Session, user_id: str, request: Request,
    success: bool, fail_reason: Optional[str] = None
):
    try:
        ua = request.headers.get("User-Agent", "")
        history = LoginHistory(
            user_id     = user_id,
            ip_address  = request.client.host if request.client else None,
            device      = ua[:255] if ua else None,
            success     = success,
            fail_reason = fail_reason,
        )
        db.add(history)
        db.commit()
    except Exception as e:
        logger.warning(f"Failed to log login: {e}")


# =============================================================================
# PASSWORD RESET
# =============================================================================

def create_password_reset_token(db: Session, user_id: str) -> str:
    db.query(PasswordResetToken).filter(
        PasswordResetToken.user_id == user_id,
        PasswordResetToken.used    == False,
    ).update({"used": True})
    db.commit()
    plain = secrets.token_urlsafe(32)
    record = PasswordResetToken(
        user_id    = user_id,
        token_hash = hash_token(plain),
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=10),
        used       = False,
    )
    db.add(record)
    db.commit()
    return plain


def verify_password_reset_token(db: Session, token: str) -> Optional[str]:
    record = db.query(PasswordResetToken).filter(
        PasswordResetToken.token_hash == hash_token(token),
        PasswordResetToken.used       == False,
    ).first()
    if not record:
        return None
    now = datetime.now(timezone.utc)
    exp = record.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if exp < now:
        return None
    record.used = True
    db.commit()
    return record.user_id


# =============================================================================
# 2FA (TOTP)
# =============================================================================

def generate_totp_secret() -> str:
    import pyotp
    return pyotp.random_base32()


def get_totp_uri(secret: str, email: str) -> str:
    import pyotp
    return pyotp.TOTP(secret).provisioning_uri(
        name=email, issuer_name=settings.APP_NAME
    )


def verify_totp(secret: str, code: str) -> bool:
    try:
        import pyotp
        return pyotp.TOTP(secret).verify(code, valid_window=1)
    except Exception:
        return False


def generate_backup_codes() -> Tuple[List[str], List[str]]:
    plain  = [secrets.token_hex(4).upper() for _ in range(8)]
    hashed = [hash_token(c) for c in plain]
    return plain, hashed


def verify_backup_code(stored: List[str], code: str) -> Tuple[bool, List[str]]:
    h = hash_token(code.upper())
    if h in stored:
        return True, [x for x in stored if x != h]
    return False, stored


# =============================================================================
# FASTAPI DEPENDENCIES
# =============================================================================

def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not credentials:
        raise exc
    payload = decode_access_token(credentials.credentials)
    if not payload:
        raise exc
    user = db.query(User).filter(User.id == payload["sub"]).first()
    if not user or not user.is_active:
        raise exc
    return user


def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> Optional[User]:
    if not credentials:
        return None
    payload = decode_access_token(credentials.credentials)
    if not payload:
        return None
    return db.query(User).filter(User.id == payload["sub"]).first()


def require_role(*roles):
    def _check(current_user: User = Depends(get_current_user)) -> User:
        allowed = [r.value if hasattr(r, "value") else r for r in roles]
        if current_user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return current_user
    return _check


require_super_admin = require_role("super_admin")
require_admin_up    = require_role("super_admin", "admin")
require_trader_up   = require_role("super_admin", "admin", "trader")
