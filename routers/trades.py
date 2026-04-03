# =============================================================================
# routers/trades.py — Trade History & Open Orders Endpoints
# =============================================================================
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from typing import Optional
from datetime import datetime, timezone, timedelta
import csv
import io
import logging

from sqlalchemy.orm import Session
from database import get_db
from models import OpenPosition, ClosedTrade, Account, User
from auth import get_current_user
from routers.accounts import get_account_or_404, user_can_access_account, user_can_access

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/trades", tags=["Trades"])


# =============================================================================
# OPEN POSITIONS
# =============================================================================

@router.get("/{account_id}/open", summary="Open Positions (Real-Time)")
async def open_positions(
    account_id: str,
    symbol:   Optional[str] = Query(None),
    direction: Optional[str] = Query(None),  # buy/sell
    magic:    Optional[int]  = Query(None),
    sort_by:  str = Query("floating_pl"),
    order:    str = Query("asc"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = get_account_or_404(account_id, db)
    if not user_can_access_account(current_user, account, db):
        raise HTTPException(status_code=403, detail="Access denied")

    q = db.query(OpenPosition).filter(OpenPosition.account_id == account_id)

    if symbol:
        q = q.filter(OpenPosition.symbol.ilike(f"%{symbol}%"))
    if direction:
        if direction.lower() in ("buy", "long"):
            q = q.filter(OpenPosition.trade_type.in_(["buy", "buy_limit", "buy_stop"]))
        else:
            q = q.filter(OpenPosition.trade_type.in_(["sell", "sell_limit", "sell_stop"]))
    if magic is not None:
        q = q.filter(OpenPosition.magic_number == magic)

    sort_col = getattr(OpenPosition, sort_by, OpenPosition.floating_pl)
    q = q.order_by(sort_col.asc() if order == "asc" else sort_col.desc())

    positions = q.all()
    now = datetime.now(timezone.utc)

    return {
        "positions": [
            {
                "id":            p.id,
                "ticket":        p.ticket,
                "symbol":        p.symbol,
                "trade_type":    p.trade_type.value,
                "lots":          p.lots,
                "open_price":    p.open_price,
                "current_price": p.current_price,
                "sl":            p.sl,
                "tp":            p.tp,
                "floating_pl":   p.floating_pl,
                "floating_pl_pct": round(p.floating_pl / account.balance * 100, 4) if account.balance else 0,
                "swap":          p.swap,
                "open_time":     p.open_time.isoformat(),
                "duration_min":  round((now - p.open_time.replace(tzinfo=timezone.utc)).total_seconds() / 60, 1),
                "magic_number":  p.magic_number,
                "comment":       p.comment,
                "mae":           p.mae,
                "mfe":           p.mfe,
                "pips":          round(abs(p.current_price - p.open_price) * 10000, 1),
            }
            for p in positions
        ],
        "summary": {
            "total_positions":  len(positions),
            "total_lots":       round(sum(p.lots for p in positions), 2),
            "total_floating_pl": round(sum(p.floating_pl for p in positions), 2),
            "by_symbol": {},  # quick aggregation could be added here
        },
    }


@router.get("/all-open", summary="All Open Positions (All Accounts)")
async def all_open_positions(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Portfolio-level open positions across all accounts."""
    accounts = db.query(Account).filter(
        Account.owner_id == current_user.id,
        Account.is_active == True,
    ).all()
    account_ids = [a.id for a in accounts]
    account_map = {a.id: a for a in accounts}

    positions = db.query(OpenPosition).filter(
        OpenPosition.account_id.in_(account_ids)
    ).order_by(OpenPosition.floating_pl).all()

    now = datetime.now(timezone.utc)
    return [
        {
            "ticket":        p.ticket,
            "account_id":    p.account_id,
            "account_label": account_map[p.account_id].label or account_map[p.account_id].account_number,
            "symbol":        p.symbol,
            "trade_type":    p.trade_type.value,
            "lots":          p.lots,
            "open_price":    p.open_price,
            "current_price": p.current_price,
            "floating_pl":   p.floating_pl,
            "open_time":     p.open_time.isoformat(),
            "duration_min":  round((now - p.open_time.replace(tzinfo=timezone.utc)).total_seconds() / 60, 1),
            "magic_number":  p.magic_number,
        }
        for p in positions
    ]


# =============================================================================
# CLOSED TRADE HISTORY
# =============================================================================

@router.get("/{account_id}/closed", summary="Closed Trade History")
async def closed_trades(
    account_id: str,
    from_date:   Optional[str] = Query(None),
    to_date:     Optional[str] = Query(None),
    symbol:      Optional[str] = Query(None),
    direction:   Optional[str] = Query(None),
    magic:       Optional[int] = Query(None),
    min_profit:  Optional[float] = Query(None),
    max_profit:  Optional[float] = Query(None),
    ticket:      Optional[int]   = Query(None),
    comment:     Optional[str]   = Query(None),
    page:        int = Query(1, ge=1),
    page_size:   int = Query(50, ge=1, le=500),
    sort_by:     str = Query("close_time"),
    order:       str = Query("desc"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = get_account_or_404(account_id, db)
    if not user_can_access_account(current_user, account, db):
        raise HTTPException(status_code=403, detail="Access denied")

    q = db.query(ClosedTrade).filter(ClosedTrade.account_id == account_id)

    # Date filters
    if from_date:
        try:
            dt = datetime.fromisoformat(from_date)
            if not dt.tzinfo:
                dt = dt.replace(tzinfo=timezone.utc)
            q = q.filter(ClosedTrade.close_time >= dt)
        except ValueError:
            pass
    if to_date:
        try:
            dt = datetime.fromisoformat(to_date)
            if not dt.tzinfo:
                dt = dt.replace(tzinfo=timezone.utc)
            q = q.filter(ClosedTrade.close_time <= dt)
        except ValueError:
            pass

    if symbol:
        q = q.filter(ClosedTrade.symbol.ilike(f"%{symbol}%"))
    if direction:
        if direction.lower() in ("buy", "long"):
            q = q.filter(ClosedTrade.trade_type.in_(["buy", "buy_limit", "buy_stop"]))
        else:
            q = q.filter(ClosedTrade.trade_type.in_(["sell", "sell_limit", "sell_stop"]))
    if magic is not None:
        q = q.filter(ClosedTrade.magic_number == magic)
    if min_profit is not None:
        q = q.filter(ClosedTrade.net_profit >= min_profit)
    if max_profit is not None:
        q = q.filter(ClosedTrade.net_profit <= max_profit)
    if ticket:
        q = q.filter(ClosedTrade.ticket == ticket)
    if comment:
        q = q.filter(ClosedTrade.comment.ilike(f"%{comment}%"))

    total = q.count()

    sort_col = getattr(ClosedTrade, sort_by, ClosedTrade.close_time)
    q = q.order_by(sort_col.desc() if order == "desc" else sort_col.asc())
    q = q.offset((page - 1) * page_size).limit(page_size)

    trades = q.all()

    return {
        "trades": [
            {
                "id":           t.id,
                "ticket":       t.ticket,
                "symbol":       t.symbol,
                "trade_type":   t.trade_type.value,
                "lots":         t.lots,
                "open_price":   t.open_price,
                "close_price":  t.close_price,
                "sl":           t.sl,
                "tp":           t.tp,
                "profit":       t.profit,
                "commission":   t.commission,
                "swap":         t.swap,
                "net_profit":   t.net_profit,
                "profit_pips":  t.profit_pips,
                "open_time":    t.open_time.isoformat(),
                "close_time":   t.close_time.isoformat(),
                "duration_min": t.duration_min,
                "magic_number": t.magic_number,
                "comment":      t.comment,
                "mae":          t.mae,
                "mfe":          t.mfe,
                "is_win":       t.net_profit > 0,
            }
            for t in trades
        ],
        "pagination": {
            "total":     total,
            "page":      page,
            "page_size": page_size,
            "pages":     (total + page_size - 1) // page_size,
        },
        "analytics": _compute_trade_analytics(
            db.query(ClosedTrade).filter(ClosedTrade.account_id == account_id).all()
        ),
    }


def _compute_trade_analytics(trades) -> dict:
    """Quick analytics on a set of trades."""
    if not trades:
        return {}
    wins = [t for t in trades if t.net_profit > 0]
    losses = [t for t in trades if t.net_profit <= 0]
    gross_profit = sum(t.net_profit for t in wins)
    gross_loss   = abs(sum(t.net_profit for t in losses))

    return {
        "total_trades":  len(trades),
        "win_count":     len(wins),
        "win_rate":      round(len(wins) / len(trades) * 100, 2),
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else 0,
        "net_profit":    round(sum(t.net_profit for t in trades), 2),
        "avg_hold_min":  round(sum(t.duration_min or 0 for t in trades) / len(trades), 1),
    }


# =============================================================================
# EXPORT
# =============================================================================

@router.get("/{account_id}/export/csv", summary="Export Closed Trades as CSV")
async def export_csv(
    account_id: str,
    from_date: Optional[str] = Query(None),
    to_date:   Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = get_account_or_404(account_id, db)
    if not user_can_access_account(current_user, account, db):
        raise HTTPException(status_code=403, detail="Access denied")

    q = db.query(ClosedTrade).filter(ClosedTrade.account_id == account_id)
    if from_date:
        q = q.filter(ClosedTrade.close_time >= datetime.fromisoformat(from_date))
    if to_date:
        q = q.filter(ClosedTrade.close_time <= datetime.fromisoformat(to_date))

    trades = q.order_by(ClosedTrade.close_time).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Ticket", "Symbol", "Type", "Lots", "Open Price", "Close Price",
        "SL", "TP", "Profit", "Commission", "Swap", "Net Profit", "Pips",
        "Open Time", "Close Time", "Duration (min)", "Magic Number", "Comment"
    ])

    for t in trades:
        writer.writerow([
            t.ticket, t.symbol, t.trade_type.value, t.lots,
            t.open_price, t.close_price, t.sl, t.tp,
            t.profit, t.commission, t.swap, t.net_profit, t.profit_pips,
            t.open_time.isoformat(), t.close_time.isoformat(),
            round(t.duration_min, 1) if t.duration_min else "",
            t.magic_number, t.comment,
        ])

    output.seek(0)
    label = account.label or account.account_number
    filename = f"trades_{label}_{datetime.now().strftime('%Y%m%d')}.csv"

    return StreamingResponse(
        io.BytesIO(output.getvalue().encode("utf-8-sig")),  # BOM for Excel
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
