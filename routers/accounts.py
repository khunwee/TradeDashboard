# =============================================================================
# routers/accounts.py — Account Management Endpoints
# Production-grade: SQLite + PostgreSQL compatible, no Enum dependencies
# =============================================================================
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_validator
from typing import Optional, List
from datetime import datetime, timezone
import secrets
import logging

from sqlalchemy.orm import Session
from database import get_db
from models import (
    Account, AccountGroup, AccountPermission,
    User, AuditLog, EaVersionLog, DepositWithdrawal
)
from auth import get_current_user, hash_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/accounts", tags=["Accounts"])

# Valid values stored as plain strings in the DB
VALID_ACCOUNT_TYPES = {"live", "demo", "contest"}
VALID_STATUSES      = {"live", "delayed", "disconnected", "market_closed", "warning"}


# =============================================================================
# REQUEST / RESPONSE SCHEMAS
# =============================================================================

class AccountCreate(BaseModel):
    account_number:   str
    broker_server:    str
    broker_name:      Optional[str] = None
    label:            Optional[str] = None
    account_currency: str = "USD"
    leverage:         int = 100
    account_type:     str = "live"
    start_date:       Optional[str] = None
    initial_deposit:  float = 0.0
    group_id:         Optional[str] = None

    @field_validator("account_number")
    @classmethod
    def validate_account_number(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Account number cannot be empty")
        return v

    @field_validator("broker_server")
    @classmethod
    def validate_broker_server(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Broker server cannot be empty")
        return v

    @field_validator("account_type")
    @classmethod
    def validate_account_type(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in VALID_ACCOUNT_TYPES:
            return "live"
        return v

    @field_validator("leverage")
    @classmethod
    def validate_leverage(cls, v: int) -> int:
        if v <= 0:
            return 100
        return v


class AccountUpdate(BaseModel):
    label:                 Optional[str]       = None
    broker_name:           Optional[str]       = None
    leverage:              Optional[int]       = None
    account_type:          Optional[str]       = None
    group_id:              Optional[str]       = None
    heartbeat_timeout_sec: Optional[int]       = None
    push_interval_sec:     Optional[int]       = None
    push_ip_whitelist:     Optional[List[str]] = None


class GroupCreate(BaseModel):
    name:  str
    color: str = "#3B82F6"


class DepositWithdrawalCreate(BaseModel):
    amount:  float
    note:    Optional[str] = None
    tx_date: str


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def _role(user: User) -> str:
    """Return user role as plain string safely."""
    r = user.role
    return r.value if hasattr(r, "value") else str(r or "trader")


def _status_str(account: Account) -> str:
    """Return account status as plain string safely."""
    s = account.status
    return s.value if hasattr(s, "value") else str(s or "disconnected")


def _account_type_str(account: Account) -> str:
    """Return account type as plain string safely."""
    t = account.account_type
    return t.value if hasattr(t, "value") else str(t or "live")


def user_can_access(user: User, account: Account, db: Session) -> bool:
    """Check if user has access to this account."""
    role = _role(user)
    if role in ("super_admin", "admin"):
        return True
    if account.owner_id == user.id:
        return True
    perm = db.query(AccountPermission).filter(
        AccountPermission.account_id == account.id,
        AccountPermission.user_id    == user.id,
        AccountPermission.can_view   == True,
    ).first()
    return perm is not None


def user_can_edit(user: User, account: Account) -> bool:
    """Check if user can edit this account."""
    role = _role(user)
    if role in ("super_admin", "admin"):
        return True
    return account.owner_id == user.id


def get_account_or_404(account_id: str, db: Session) -> Account:
    """Get active account by ID or raise 404."""
    account = db.query(Account).filter(
        Account.id        == account_id,
        Account.is_active == True,
    ).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    return account


def format_account(account: Account) -> dict:
    """Serialize account to a clean API response dict."""
    def _iso(dt):
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()

    return {
        # Identity
        "id":               account.id,
        "account_number":   account.account_number,
        "broker_server":    account.broker_server,
        "broker_name":      account.broker_name,
        "label":            account.label or account.account_number,
        "account_currency": account.account_currency or "USD",
        "leverage":         account.leverage or 100,
        "account_type":     _account_type_str(account),
        "group_id":         account.group_id,
        "initial_deposit":  account.initial_deposit or 0.0,
        "start_date":       _iso(account.start_date),
        "created_at":       _iso(account.created_at),

        # EA info
        "ea_name":          account.ea_name,
        "ea_version":       account.ea_version,
        "ea_build":         account.ea_build,

        # Connection status
        "status":           _status_str(account),
        "last_push_at":     _iso(account.last_push_at),
        "uptime_pct":       account.uptime_pct or 0.0,
        "has_push_api_key": bool(account.push_api_key_hash),
        "push_api_key_prefix": account.push_api_key_prefix,
        "heartbeat_timeout_sec": account.heartbeat_timeout_sec or 60,
        "push_interval_sec":     account.push_interval_sec or 5,

        # Live metrics
        "balance":          account.balance or 0.0,
        "equity":           account.equity or 0.0,
        "floating_pl":      account.floating_pl or 0.0,
        "margin":           account.margin or 0.0,
        "margin_level":     account.margin_level or 0.0,
        "free_margin":      account.free_margin or 0.0,
        "open_orders_count": account.open_orders_count or 0,

        # Today
        "profit_today":         account.profit_today or 0.0,
        "lots_today":           account.lots_today or 0.0,
        "orders_closed_today":  account.orders_closed_today or 0,
        "max_dd_today":         account.max_dd_today or 0.0,

        # Statistics
        "peak_equity":            account.peak_equity or 0.0,
        "max_drawdown_abs":       account.max_drawdown_abs or 0.0,
        "max_drawdown_pct":       account.max_drawdown_pct or 0.0,
        "profit_factor":          account.profit_factor or 0.0,
        "win_rate":               account.win_rate or 0.0,
        "sharpe_ratio":           account.sharpe_ratio,
        "sortino_ratio":          account.sortino_ratio,
        "calmar_ratio":           account.calmar_ratio,
        "recovery_factor":        account.recovery_factor or 0.0,
        "expectancy":             account.expectancy or 0.0,
        "total_trades":           account.total_trades or 0,
        "growth_pct":             account.growth_pct or 0.0,
        "annualized_return":      account.annualized_return,
        "avg_hold_time_min":      account.avg_hold_time_min,
        "max_consecutive_wins":   account.max_consecutive_wins or 0,
        "max_consecutive_losses": account.max_consecutive_losses or 0,
        "current_streak":         account.current_streak or 0,
        "largest_win":            account.largest_win or 0.0,
        "largest_loss":           account.largest_loss or 0.0,
        "avg_daily_profit":       account.avg_daily_profit or 0.0,
        "max_deposit_load_pct":   account.max_deposit_load_pct or 0.0,
    }


def _audit(db: Session, user_id: str, action: str, resource_id: str = None):
    """Write an audit log entry, never crashing the main request."""
    try:
        db.add(AuditLog(
            user_id     = user_id,
            action      = action,
            resource    = "account",
            resource_id = resource_id,
        ))
        db.commit()
    except Exception as e:
        logger.warning(f"Audit log failed: {e}")
        db.rollback()


# =============================================================================
# ACCOUNT CRUD
# =============================================================================

@router.post("", status_code=201, summary="Add New MT4/MT5 Account")
async def create_account(
    req: AccountCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Register a new MT4/MT5 trading account for the current user."""
    # Duplicate check
    existing = db.query(Account).filter(
        Account.owner_id       == current_user.id,
        Account.account_number == req.account_number.strip(),
        Account.broker_server  == req.broker_server.strip(),
        Account.is_active      == True,
    ).first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Account {req.account_number} on {req.broker_server} is already registered",
        )

    # Parse optional start_date
    start_dt = None
    if req.start_date:
        try:
            start_dt = datetime.fromisoformat(req.start_date)
        except ValueError:
            pass

    account = Account(
        owner_id         = current_user.id,
        account_number   = req.account_number.strip(),
        broker_server    = req.broker_server.strip(),
        broker_name      = req.broker_name,
        label            = req.label,
        account_currency = req.account_currency or "USD",
        leverage         = req.leverage or 100,
        account_type     = req.account_type,
        initial_deposit  = req.initial_deposit or 0.0,
        initial_balance  = req.initial_deposit or 0.0,
        group_id         = req.group_id,
        start_date       = start_dt,
        status           = "disconnected",
        is_active        = True,
        push_ip_whitelist = [],
    )
    db.add(account)
    db.commit()
    db.refresh(account)

    _audit(db, current_user.id, "account.create", account.id)

    logger.info(f"Account created: {account.account_number} by user {current_user.email}")
    return format_account(account)


@router.get("", summary="List All Accounts")
async def list_accounts(
    search:       Optional[str] = Query(None, description="Search by account number, label, broker"),
    status_f:     Optional[str] = Query(None, alias="status"),
    account_type: Optional[str] = Query(None),
    group_id:     Optional[str] = Query(None),
    sort_by:      str           = Query("created_at"),
    order:        str           = Query("desc"),
    current_user: User          = Depends(get_current_user),
    db:           Session       = Depends(get_db),
):
    """List all accounts accessible to the current user."""
    q = db.query(Account).filter(Account.is_active == True)

    # Non-admins see only their own + granted accounts
    role = _role(current_user)
    if role not in ("super_admin", "admin"):
        granted_ids = [
            p.account_id for p in db.query(AccountPermission).filter(
                AccountPermission.user_id == current_user.id,
            ).all()
        ]
        q = q.filter(
            (Account.owner_id == current_user.id) |
            (Account.id.in_(granted_ids))
        )

    # Filters
    if search:
        term = f"%{search}%"
        q = q.filter(
            Account.account_number.ilike(term) |
            Account.label.ilike(term) |
            Account.broker_name.ilike(term) |
            Account.ea_name.ilike(term)
        )
    if status_f and status_f in VALID_STATUSES:
        q = q.filter(Account.status == status_f)
    if account_type and account_type in VALID_ACCOUNT_TYPES:
        q = q.filter(Account.account_type == account_type)
    if group_id:
        q = q.filter(Account.group_id == group_id)

    # Sorting — only allow known columns to prevent SQL injection
    safe_sort_cols = {
        "created_at", "account_number", "broker_name",
        "balance", "equity", "profit_today", "status",
    }
    sort_col = getattr(Account, sort_by if sort_by in safe_sort_cols else "created_at")
    q = q.order_by(sort_col.desc() if order == "desc" else sort_col.asc())

    accounts = q.all()
    formatted = [format_account(a) for a in accounts]

    return {
        "accounts": formatted,
        "total":    len(formatted),
        "summary": {
            "total_balance":      sum(a["balance"]      for a in formatted),
            "total_equity":       sum(a["equity"]       for a in formatted),
            "total_floating_pl":  sum(a["floating_pl"]  for a in formatted),
            "total_profit_today": sum(a["profit_today"] for a in formatted),
            "total_open_orders":  sum(a["open_orders_count"] for a in formatted),
            "live_count":         sum(1 for a in formatted if a["status"] == "live"),
            "disconnected_count": sum(1 for a in formatted if a["status"] == "disconnected"),
        },
    }


@router.get("/{account_id}", summary="Get Account Detail")
async def get_account(
    account_id:   str,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    account = get_account_or_404(account_id, db)
    if not user_can_access(current_user, account, db):
        raise HTTPException(status_code=403, detail="Access denied")
    return format_account(account)


@router.patch("/{account_id}", summary="Update Account Settings")
async def update_account(
    account_id:   str,
    req:          AccountUpdate,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    account = get_account_or_404(account_id, db)
    if not user_can_edit(current_user, account):
        raise HTTPException(status_code=403, detail="Access denied")

    changed = False
    if req.label is not None:
        account.label = req.label; changed = True
    if req.broker_name is not None:
        account.broker_name = req.broker_name; changed = True
    if req.leverage is not None and req.leverage > 0:
        account.leverage = req.leverage; changed = True
    if req.account_type is not None and req.account_type in VALID_ACCOUNT_TYPES:
        account.account_type = req.account_type; changed = True
    if req.group_id is not None:
        account.group_id = req.group_id; changed = True
    if req.heartbeat_timeout_sec is not None and req.heartbeat_timeout_sec > 0:
        account.heartbeat_timeout_sec = req.heartbeat_timeout_sec; changed = True
    if req.push_interval_sec is not None and req.push_interval_sec > 0:
        account.push_interval_sec = req.push_interval_sec; changed = True
    if req.push_ip_whitelist is not None:
        account.push_ip_whitelist = req.push_ip_whitelist; changed = True

    if changed:
        db.commit()
        _audit(db, current_user.id, "account.update", account_id)

    return format_account(account)


@router.delete("/{account_id}", summary="Delete Account")
async def delete_account(
    account_id:   str,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    account = get_account_or_404(account_id, db)
    if not user_can_edit(current_user, account):
        raise HTTPException(status_code=403, detail="Access denied")

    account.is_active = False
    db.commit()
    _audit(db, current_user.id, "account.delete", account_id)
    return {"message": "Account deleted successfully"}


# =============================================================================
# EA PUSH API KEY
# =============================================================================

@router.post("/{account_id}/api-key", summary="Generate EA Push API Key")
async def generate_api_key(
    account_id:   str,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """Generate a new API key for the MetaTrader EA to push data."""
    account = get_account_or_404(account_id, db)
    if not user_can_edit(current_user, account):
        raise HTTPException(status_code=403, detail="Access denied")

    raw_key = f"td_{secrets.token_urlsafe(32)}"
    account.push_api_key_hash   = hash_token(raw_key)
    account.push_api_key_prefix = raw_key[:12]
    db.commit()

    _audit(db, current_user.id, "account.api_key_generated", account_id)

    return {
        "api_key": raw_key,
        "prefix":  account.push_api_key_prefix,
        "message": "Copy this key and paste it into MetaTrader EA settings. It will not be shown again.",
    }


@router.delete("/{account_id}/api-key", summary="Revoke EA Push API Key")
async def revoke_api_key(
    account_id:   str,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    account = get_account_or_404(account_id, db)
    if not user_can_edit(current_user, account):
        raise HTTPException(status_code=403, detail="Access denied")

    account.push_api_key_hash   = None
    account.push_api_key_prefix = None
    db.commit()
    _audit(db, current_user.id, "account.api_key_revoked", account_id)
    return {"message": "API key revoked. EA can no longer push data until a new key is generated."}


# =============================================================================
# ACCOUNT PERMISSIONS (share with other users)
# =============================================================================

@router.get("/{account_id}/permissions", summary="List Account Permissions")
async def list_permissions(
    account_id:   str,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    account = get_account_or_404(account_id, db)
    if not user_can_edit(current_user, account):
        raise HTTPException(status_code=403, detail="Access denied")

    perms = db.query(AccountPermission).filter(
        AccountPermission.account_id == account_id
    ).all()
    return [
        {
            "id":       p.id,
            "user_id":  p.user_id,
            "can_view": p.can_view,
            "can_edit": p.can_edit,
        }
        for p in perms
    ]


# =============================================================================
# ACCOUNT GROUPS
# =============================================================================

@router.post("/groups/create", status_code=201, summary="Create Account Group")
async def create_group(
    req:          GroupCreate,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    group = AccountGroup(
        owner_id = current_user.id,
        name     = req.name.strip(),
        color    = req.color,
    )
    db.add(group)
    db.commit()
    db.refresh(group)
    return {"id": group.id, "name": group.name, "color": group.color}


@router.get("/groups/list", summary="List Account Groups")
async def list_groups(
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    groups = db.query(AccountGroup).filter(
        AccountGroup.owner_id == current_user.id
    ).order_by(AccountGroup.name).all()
    return [{"id": g.id, "name": g.name, "color": g.color} for g in groups]


@router.delete("/groups/{group_id}", summary="Delete Account Group")
async def delete_group(
    group_id:     str,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    group = db.query(AccountGroup).filter(
        AccountGroup.id       == group_id,
        AccountGroup.owner_id == current_user.id,
    ).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    # Unassign accounts from this group
    db.query(Account).filter(Account.group_id == group_id).update({"group_id": None})
    db.delete(group)
    db.commit()
    return {"message": "Group deleted"}


# =============================================================================
# DEPOSITS / WITHDRAWALS
# =============================================================================

@router.get("/{account_id}/deposits", summary="List Deposits & Withdrawals")
async def list_deposits(
    account_id:   str,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    account = get_account_or_404(account_id, db)
    if not user_can_access(current_user, account, db):
        raise HTTPException(status_code=403, detail="Access denied")

    entries = db.query(DepositWithdrawal).filter(
        DepositWithdrawal.account_id == account_id
    ).order_by(DepositWithdrawal.tx_date.desc()).all()

    return [
        {
            "id":      e.id,
            "amount":  e.amount,
            "note":    e.note,
            "tx_date": e.tx_date.isoformat() if e.tx_date else None,
            "type":    "deposit" if e.amount > 0 else "withdrawal",
        }
        for e in entries
    ]


@router.post("/{account_id}/deposits", status_code=201, summary="Record Deposit or Withdrawal")
async def add_deposit(
    account_id:   str,
    req:          DepositWithdrawalCreate,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    account = get_account_or_404(account_id, db)
    if not user_can_edit(current_user, account):
        raise HTTPException(status_code=403, detail="Access denied")

    try:
        tx_date = datetime.fromisoformat(req.tx_date)
    except ValueError:
        tx_date = datetime.now(timezone.utc)

    entry = DepositWithdrawal(
        account_id = account_id,
        amount     = req.amount,
        note       = req.note,
        tx_date    = tx_date,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)

    return {
        "id":      entry.id,
        "amount":  entry.amount,
        "type":    "deposit" if entry.amount > 0 else "withdrawal",
        "message": "Transaction recorded successfully",
    }


@router.delete("/{account_id}/deposits/{deposit_id}", summary="Delete Transaction")
async def delete_deposit(
    account_id:   str,
    deposit_id:   str,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    account = get_account_or_404(account_id, db)
    if not user_can_edit(current_user, account):
        raise HTTPException(status_code=403, detail="Access denied")

    entry = db.query(DepositWithdrawal).filter(
        DepositWithdrawal.id         == deposit_id,
        DepositWithdrawal.account_id == account_id,
    ).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Transaction not found")

    db.delete(entry)
    db.commit()
    return {"message": "Transaction deleted"}


# =============================================================================
# EA VERSION HISTORY
# =============================================================================

@router.get("/{account_id}/ea-versions", summary="EA Version History")
async def ea_version_history(
    account_id:   str,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    account = get_account_or_404(account_id, db)
    if not user_can_access(current_user, account, db):
        raise HTTPException(status_code=403, detail="Access denied")

    logs = db.query(EaVersionLog).filter(
        EaVersionLog.account_id == account_id
    ).order_by(EaVersionLog.logged_at.desc()).limit(50).all()

    return [
        {
            "id":         log.id,
            "ea_name":    log.ea_name,
            "ea_version": log.ea_version,
            "ea_build":   log.ea_build,
            "logged_at":  log.logged_at.isoformat() if log.logged_at else None,
        }
        for log in logs
    ]


# =============================================================================
# ACCOUNT SNAPSHOTS (equity curve data)
# =============================================================================

@router.get("/{account_id}/snapshots", summary="Equity Curve Snapshots")
async def get_snapshots(
    account_id:   str,
    limit:        int     = Query(500, ge=1, le=5000),
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    from models import AccountSnapshot
    account = get_account_or_404(account_id, db)
    if not user_can_access(current_user, account, db):
        raise HTTPException(status_code=403, detail="Access denied")

    snaps = db.query(AccountSnapshot).filter(
        AccountSnapshot.account_id == account_id
    ).order_by(AccountSnapshot.snapshot_time.desc()).limit(limit).all()

    return [
        {
            "time":        s.snapshot_time.isoformat() if s.snapshot_time else None,
            "balance":     s.balance,
            "equity":      s.equity,
            "floating_pl": s.floating_pl,
            "open_orders": s.open_orders,
        }
        for s in reversed(snaps)
    ]


# =============================================================================
# DAILY STATS
# =============================================================================

@router.get("/{account_id}/daily-stats", summary="Daily P&L Statistics")
async def get_daily_stats(
    account_id:   str,
    days:         int     = Query(90, ge=1, le=365),
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    from models import DailyStats
    account = get_account_or_404(account_id, db)
    if not user_can_access(current_user, account, db):
        raise HTTPException(status_code=403, detail="Access denied")

    stats = db.query(DailyStats).filter(
        DailyStats.account_id == account_id
    ).order_by(DailyStats.date.desc()).limit(days).all()

    return [
        {
            "date":          s.date,
            "profit":        s.profit,
            "profit_pct":    s.profit_pct,
            "trades_count":  s.trades_count,
            "lots_volume":   s.lots_volume,
            "max_drawdown":  s.max_drawdown,
            "close_balance": s.close_balance,
            "close_equity":  s.close_equity,
        }
        for s in reversed(stats)
    ]
