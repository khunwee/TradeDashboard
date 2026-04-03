# =============================================================================
# routers/accounts.py — Account (Port) Management Endpoints
# =============================================================================
from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timezone
import secrets
import logging

from sqlalchemy.orm import Session
from database import get_db
from models import (
    Account, AccountGroup, AccountPermission, AccountType,
    User, UserRole, AuditLog, EaVersionLog, DepositWithdrawal, ApiKey
)
from auth import get_current_user, hash_token, require_trader_up

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/accounts", tags=["Accounts"])


# =============================================================================
# SCHEMAS
# =============================================================================

class AccountCreate(BaseModel):
    account_number:  str
    broker_server:   str
    broker_name:     Optional[str] = None
    label:           Optional[str] = None
    account_currency: str = "USD"
    leverage:        int = 100
    account_type:    str = "live"
    start_date:      Optional[str] = None
    initial_deposit: float = 0.0
    group_id:        Optional[str] = None


class AccountUpdate(BaseModel):
    label:           Optional[str] = None
    broker_name:     Optional[str] = None
    leverage:        Optional[int] = None
    account_type:    Optional[str] = None
    group_id:        Optional[str] = None
    heartbeat_timeout_sec: Optional[int] = None
    push_interval_sec:     Optional[int] = None
    push_ip_whitelist:     Optional[List[str]] = None


class GroupCreate(BaseModel):
    name:  str
    color: str = "#3B82F6"


class DepositWithdrawalCreate(BaseModel):
    amount:  float
    note:    Optional[str] = None
    tx_date: str


# =============================================================================
# HELPERS
# =============================================================================

def user_can_access_account(user: User, account: Account, db: Session) -> bool:
    """Check if user can access this account (own account or explicit permission)."""
    if user.role in (UserRole.SUPER_ADMIN, UserRole.ADMIN):
        return True
    if account.owner_id == user.id:
        return True
    perm = db.query(AccountPermission).filter(
        AccountPermission.account_id == account.id,
        AccountPermission.user_id == user.id,
    ).first()
    return perm is not None


def get_account_or_404(account_id: str, db: Session) -> Account:
    account = db.query(Account).filter(Account.id == account_id, Account.is_active == True).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    return account


def format_account(account: Account) -> dict:
    """Serialize account to API response dict."""
    return {
        "id":              account.id,
        "account_number":  account.account_number,
        "broker_server":   account.broker_server,
        "broker_name":     account.broker_name,
        "label":           account.label,
        "ea_name":         account.ea_name,
        "ea_version":      account.ea_version,
        "ea_build":        account.ea_build,
        "account_currency": account.account_currency,
        "leverage":        account.leverage,
        "account_type":    account.account_type.value,
        "status":          account.status.value,
        "last_push_at":    account.last_push_at.isoformat() if account.last_push_at else None,
        # Live metrics
        "balance":         account.balance,
        "equity":          account.equity,
        "floating_pl":     account.floating_pl,
        "margin":          account.margin,
        "margin_level":    account.margin_level,
        "free_margin":     account.free_margin,
        "open_orders_count": account.open_orders_count,
        # Today metrics
        "profit_today":    account.profit_today,
        "lots_today":      account.lots_today,
        "orders_closed_today": account.orders_closed_today,
        "max_dd_today":    account.max_dd_today,
        # All-time metrics
        "peak_equity":          account.peak_equity,
        "max_drawdown_abs":     account.max_drawdown_abs,
        "max_drawdown_pct":     account.max_drawdown_pct,
        "profit_factor":        account.profit_factor,
        "win_rate":             account.win_rate,
        "sharpe_ratio":         account.sharpe_ratio,
        "sortino_ratio":        account.sortino_ratio,
        "calmar_ratio":         account.calmar_ratio,
        "recovery_factor":      account.recovery_factor,
        "expectancy":           account.expectancy,
        "total_trades":         account.total_trades,
        "growth_pct":           account.growth_pct,
        "annualized_return":    account.annualized_return,
        "avg_hold_time_min":    account.avg_hold_time_min,
        "max_consecutive_wins": account.max_consecutive_wins,
        "max_consecutive_losses": account.max_consecutive_losses,
        "current_streak":       account.current_streak,
        "largest_win":          account.largest_win,
        "largest_loss":         account.largest_loss,
        "avg_daily_profit":     account.avg_daily_profit,
        "max_deposit_load_pct": account.max_deposit_load_pct,
        "uptime_pct":           account.uptime_pct,
        "initial_deposit":      account.initial_deposit,
        "start_date":           account.start_date.isoformat() if account.start_date else None,
        "group_id":             account.group_id,
        "created_at":           account.created_at.isoformat() if account.created_at else None,
        # API key visibility
        "has_push_api_key":     bool(account.push_api_key_hash),
        "push_api_key_prefix":  account.push_api_key_prefix,
    }


# =============================================================================
# ACCOUNT CRUD
# =============================================================================

@router.post("", status_code=201, summary="Add New Account")
async def create_account(
    req: AccountCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Check for duplicate
    existing = db.query(Account).filter(
        Account.owner_id == current_user.id,
        Account.account_number == req.account_number,
        Account.broker_server == req.broker_server,
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="Account already registered")

    try:
        acct_type = AccountType(req.account_type)
    except ValueError:
        acct_type = AccountType.LIVE

    account = Account(
        owner_id=current_user.id,
        account_number=req.account_number,
        broker_server=req.broker_server,
        broker_name=req.broker_name,
        label=req.label,
        account_currency=req.account_currency,
        leverage=req.leverage,
        account_type=acct_type,
        initial_deposit=req.initial_deposit,
        group_id=req.group_id,
        start_date=datetime.fromisoformat(req.start_date) if req.start_date else None,
    )
    db.add(account)
    db.commit()
    db.refresh(account)

    db.add(AuditLog(user_id=current_user.id, action="account.create", resource="account", resource_id=account.id))
    db.commit()

    return format_account(account)


@router.get("", summary="List All Accounts")
async def list_accounts(
    search:      Optional[str] = Query(None),
    status_f:    Optional[str] = Query(None, alias="status"),
    account_type: Optional[str] = Query(None),
    group_id:    Optional[str] = Query(None),
    sort_by:     str = Query("created_at"),
    order:       str = Query("desc"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(Account).filter(Account.is_active == True)

    if current_user.role not in (UserRole.SUPER_ADMIN, UserRole.ADMIN):
        # Only own accounts + explicitly granted ones
        granted_ids = [p.account_id for p in db.query(AccountPermission).filter(
            AccountPermission.user_id == current_user.id
        ).all()]
        q = q.filter(
            (Account.owner_id == current_user.id) | (Account.id.in_(granted_ids))
        )

    if search:
        q = q.filter(
            Account.account_number.ilike(f"%{search}%") |
            Account.label.ilike(f"%{search}%") |
            Account.ea_name.ilike(f"%{search}%") |
            Account.broker_name.ilike(f"%{search}%")
        )
    if status_f:
        from models import AccountStatus
        try:
            q = q.filter(Account.status == AccountStatus(status_f))
        except ValueError:
            pass
    if account_type:
        try:
            q = q.filter(Account.account_type == AccountType(account_type))
        except ValueError:
            pass
    if group_id:
        q = q.filter(Account.group_id == group_id)

    # Sorting
    sort_col = getattr(Account, sort_by, Account.created_at)
    q = q.order_by(sort_col.desc() if order == "desc" else sort_col.asc())

    accounts = q.all()
    return {
        "accounts": [format_account(a) for a in accounts],
        "total": len(accounts),
        "summary": {
            "total_balance": sum(a.balance for a in accounts),
            "total_equity":  sum(a.equity for a in accounts),
            "total_floating_pl": sum(a.floating_pl for a in accounts),
            "total_profit_today": sum(a.profit_today for a in accounts),
            "total_open_orders": sum(a.open_orders_count for a in accounts),
        },
    }


@router.get("/{account_id}", summary="Get Account Detail")
async def get_account(
    account_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = get_account_or_404(account_id, db)
    if not user_can_access_account(current_user, account, db):
        raise HTTPException(status_code=403, detail="Access denied")
    return format_account(account)


@router.patch("/{account_id}", summary="Update Account")
async def update_account(
    account_id: str,
    req: AccountUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = get_account_or_404(account_id, db)
    if not user_can_access_account(current_user, account, db):
        raise HTTPException(status_code=403, detail="Access denied")

    if req.label is not None:
        account.label = req.label
    if req.broker_name is not None:
        account.broker_name = req.broker_name
    if req.leverage is not None:
        account.leverage = req.leverage
    if req.group_id is not None:
        account.group_id = req.group_id
    if req.heartbeat_timeout_sec is not None:
        account.heartbeat_timeout_sec = req.heartbeat_timeout_sec
    if req.push_interval_sec is not None:
        account.push_interval_sec = req.push_interval_sec
    if req.push_ip_whitelist is not None:
        account.push_ip_whitelist = req.push_ip_whitelist

    db.commit()
    return format_account(account)


@router.delete("/{account_id}", summary="Delete Account")
async def delete_account(
    account_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = get_account_or_404(account_id, db)
    if account.owner_id != current_user.id and current_user.role not in (UserRole.SUPER_ADMIN, UserRole.ADMIN):
        raise HTTPException(status_code=403, detail="Access denied")

    account.is_active = False
    db.add(AuditLog(user_id=current_user.id, action="account.delete", resource="account", resource_id=account_id))
    db.commit()
    return {"message": "Account deleted"}


# =============================================================================
# API KEY MANAGEMENT (for EA push)
# =============================================================================

@router.post("/{account_id}/api-key", summary="Generate EA Push API Key")
async def generate_api_key(
    account_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = get_account_or_404(account_id, db)
    if account.owner_id != current_user.id and current_user.role != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Access denied")

    raw_key = f"td_{secrets.token_urlsafe(32)}"
    account.push_api_key_hash   = hash_token(raw_key)
    account.push_api_key_prefix = raw_key[:12]
    db.commit()

    return {
        "api_key": raw_key,  # Shown ONCE — user must copy it
        "prefix":  account.push_api_key_prefix,
        "message": "Store this key safely — it will not be shown again",
    }


@router.delete("/{account_id}/api-key", summary="Revoke EA Push API Key")
async def revoke_api_key(
    account_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = get_account_or_404(account_id, db)
    if account.owner_id != current_user.id and current_user.role != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Access denied")

    account.push_api_key_hash   = None
    account.push_api_key_prefix = None
    db.commit()
    return {"message": "API key revoked"}


# =============================================================================
# ACCOUNT GROUPS
# =============================================================================

@router.post("/groups", status_code=201, summary="Create Account Group")
async def create_group(
    req: GroupCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    group = AccountGroup(owner_id=current_user.id, name=req.name, color=req.color)
    db.add(group)
    db.commit()
    db.refresh(group)
    return {"id": group.id, "name": group.name, "color": group.color}


@router.get("/groups", summary="List Account Groups")
async def list_groups(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    groups = db.query(AccountGroup).filter(AccountGroup.owner_id == current_user.id).all()
    return [{"id": g.id, "name": g.name, "color": g.color} for g in groups]


@router.delete("/groups/{group_id}", summary="Delete Account Group")
async def delete_group(
    group_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    group = db.query(AccountGroup).filter(
        AccountGroup.id == group_id,
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
    account_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = get_account_or_404(account_id, db)
    if not user_can_access_account(current_user, account, db):
        raise HTTPException(status_code=403, detail="Access denied")

    deposits = db.query(DepositWithdrawal).filter(
        DepositWithdrawal.account_id == account_id
    ).order_by(DepositWithdrawal.tx_date.desc()).all()

    return [
        {
            "id": d.id, "amount": d.amount, "note": d.note,
            "tx_date": d.tx_date.isoformat(), "type": "deposit" if d.amount > 0 else "withdrawal",
        }
        for d in deposits
    ]


@router.post("/{account_id}/deposits", status_code=201, summary="Add Deposit/Withdrawal")
async def add_deposit(
    account_id: str,
    req: DepositWithdrawalCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = get_account_or_404(account_id, db)
    if account.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    entry = DepositWithdrawal(
        account_id=account_id,
        amount=req.amount,
        note=req.note,
        tx_date=datetime.fromisoformat(req.tx_date),
    )
    db.add(entry)
    db.commit()
    return {"id": entry.id, "amount": entry.amount, "message": "Recorded"}


# =============================================================================
# EA VERSION HISTORY
# =============================================================================

@router.get("/{account_id}/ea-versions", summary="EA Version History")
async def ea_version_history(
    account_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = get_account_or_404(account_id, db)
    if not user_can_access_account(current_user, account, db):
        raise HTTPException(status_code=403, detail="Access denied")

    logs = db.query(EaVersionLog).filter(
        EaVersionLog.account_id == account_id
    ).order_by(EaVersionLog.created_at.desc()).all()

    return [
        {"id": l.id, "old_version": l.old_version, "new_version": l.new_version,
         "created_at": l.created_at.isoformat()}
        for l in logs
    ]
