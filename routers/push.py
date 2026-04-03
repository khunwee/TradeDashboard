# =============================================================================
# routers/push.py — EA Data Push Endpoint (POST /api/v1/push)
# Handles incoming MT4/MT5 data from EA, stores to DB, broadcasts via WS
# =============================================================================
from fastapi import APIRouter, HTTPException, Request, Header, status, Depends
from pydantic import BaseModel, field_validator
from typing import Optional, List
from datetime import datetime, timezone
import hashlib
import logging

from sqlalchemy.orm import Session
from database import get_db
from models import (
    Account, OpenPosition, ClosedTrade, AccountSnapshot,
    EaVersionLog, TradeType
)
from auth import hash_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["EA Push"])


# =============================================================================
# PYDANTIC SCHEMAS (EA Payload)
# =============================================================================

class OpenPositionPayload(BaseModel):
    ticket:        int
    symbol:        str
    type:          str       # buy / sell / buy_limit etc.
    lots:          float
    open_price:    float
    current_price: float
    sl:            Optional[float] = None
    tp:            Optional[float] = None
    floating_pl:   float = 0.0
    swap:          float = 0.0
    open_time:     str
    magic_number:  int = 0
    comment:       Optional[str] = None


class ClosedTradePayload(BaseModel):
    ticket:        int
    symbol:        str
    type:          str
    lots:          float
    open_price:    float
    close_price:   float
    sl:            Optional[float] = None
    tp:            Optional[float] = None
    profit:        float
    commission:    float = 0.0
    swap:          float = 0.0
    open_time:     str
    close_time:    str
    magic_number:  int = 0
    comment:       Optional[str] = None


class PushPayload(BaseModel):
    account_number:         str
    api_key:                str
    server_time:            str
    balance:                float
    equity:                 float
    margin:                 float
    free_margin:            float
    margin_level:           float
    floating_pl:            float
    open_positions:         List[OpenPositionPayload] = []
    closed_since_last_push: List[ClosedTradePayload] = []
    ea_version:             Optional[str] = None
    ea_build:               Optional[str] = None


class HeartbeatPayload(BaseModel):
    account_number: str
    api_key:        str
    server_time:    str


# =============================================================================
# HELPERS
# =============================================================================

def parse_dt(dt_str: str) -> datetime:
    """Parse ISO datetime string to UTC-aware datetime."""
    dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    if not dt.tzinfo:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def normalize_trade_type(type_str: str) -> TradeType:
    mapping = {
        "buy": TradeType.BUY, "sell": TradeType.SELL,
        "buy_limit": TradeType.BUY_LIMIT, "sell_limit": TradeType.SELL_LIMIT,
        "buy_stop": TradeType.BUY_STOP, "sell_stop": TradeType.SELL_STOP,
        "0": TradeType.BUY, "1": TradeType.SELL,
    }
    return mapping.get(type_str.lower(), TradeType.BUY)


def authenticate_push(db: Session, account_number: str, api_key: str) -> Account:
    """Validate API key against account — constant-time comparison."""
    account = db.query(Account).filter(
        Account.account_number == account_number,
        Account.is_active == True,
    ).first()

    if not account:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown account")

    if not account.push_api_key_hash:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No API key configured")

    # Constant-time comparison
    provided_hash = hash_token(api_key)
    if not hashlib.compare_digest(provided_hash, account.push_api_key_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")

    return account


def broadcast_live_data(account_id: str, data: dict):
    """Broadcast updated data to all WebSocket clients watching this account."""
    try:
        from main import ws_manager
        import asyncio
        # Schedule broadcast in event loop
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(ws_manager.broadcast_to_account(account_id, data))
    except Exception as e:
        logger.debug(f"WS broadcast skipped: {e}")


# =============================================================================
# ENDPOINTS
# =============================================================================

@router.post("/push", summary="EA Data Push")
async def receive_push(payload: PushPayload, db: Session = Depends(get_db)):
    """
    Main data ingestion endpoint — called by MT4/MT5 EA every 5 seconds.
    Authenticates API key, stores snapshot, upserts positions, saves new closed trades.
    """
    account = authenticate_push(db, payload.account_number, payload.api_key)
    now_utc = datetime.now(timezone.utc)

    # ── Detect EA version change ───────────────────────────────────────────────
    if payload.ea_version and payload.ea_version != account.ea_version:
        version_log = EaVersionLog(
            account_id=account.id,
            old_version=account.ea_version,
            new_version=payload.ea_version,
        )
        db.add(version_log)
        account.ea_version = payload.ea_version

    if payload.ea_build:
        account.ea_build = payload.ea_build

    # ── Update cached live metrics ─────────────────────────────────────────────
    account.balance       = payload.balance
    account.equity        = payload.equity
    account.margin        = payload.margin
    account.free_margin   = payload.free_margin
    account.margin_level  = payload.margin_level
    account.floating_pl   = payload.floating_pl
    account.open_orders_count = len(payload.open_positions)
    account.last_push_at  = now_utc

    # Update peak equity
    if payload.equity > (account.peak_equity or 0):
        account.peak_equity = payload.equity

    # ── Store snapshot ────────────────────────────────────────────────────────
    snapshot = AccountSnapshot(
        account_id=account.id,
        ts=parse_dt(payload.server_time) if payload.server_time else now_utc,
        balance=payload.balance,
        equity=payload.equity,
        floating_pl=payload.floating_pl,
        margin=payload.margin,
        margin_level=payload.margin_level,
        open_orders=len(payload.open_positions),
        open_lots=sum(p.lots for p in payload.open_positions),
    )
    db.add(snapshot)

    # ── Upsert open positions ─────────────────────────────────────────────────
    incoming_tickets = {p.ticket for p in payload.open_positions}

    # Remove positions that have been closed
    db.query(OpenPosition).filter(
        OpenPosition.account_id == account.id,
        OpenPosition.ticket.notin_(incoming_tickets),
    ).delete(synchronize_session=False)

    for pos in payload.open_positions:
        existing = db.query(OpenPosition).filter(
            OpenPosition.account_id == account.id,
            OpenPosition.ticket == pos.ticket,
        ).first()

        open_time = parse_dt(pos.open_time)
        duration_min = (now_utc - open_time).total_seconds() / 60

        if existing:
            # Track MAE / MFE
            if pos.floating_pl < (existing.mae or 0):
                existing.mae = pos.floating_pl
            if pos.floating_pl > (existing.mfe or 0):
                existing.mfe = pos.floating_pl

            existing.current_price = pos.current_price
            existing.floating_pl   = pos.floating_pl
            existing.swap          = pos.swap
        else:
            new_pos = OpenPosition(
                account_id=account.id,
                ticket=pos.ticket,
                symbol=pos.symbol,
                trade_type=normalize_trade_type(pos.type),
                lots=pos.lots,
                open_price=pos.open_price,
                current_price=pos.current_price,
                sl=pos.sl,
                tp=pos.tp,
                floating_pl=pos.floating_pl,
                swap=pos.swap,
                open_time=open_time,
                magic_number=pos.magic_number,
                comment=pos.comment,
                mae=pos.floating_pl,
                mfe=pos.floating_pl,
            )
            db.add(new_pos)

    # ── Store new closed trades ───────────────────────────────────────────────
    new_closed_count = 0
    for trade in payload.closed_since_last_push:
        existing = db.query(ClosedTrade).filter(
            ClosedTrade.account_id == account.id,
            ClosedTrade.ticket == trade.ticket,
        ).first()

        if existing:
            continue  # Already stored (idempotent)

        open_time  = parse_dt(trade.open_time)
        close_time = parse_dt(trade.close_time)
        duration   = (close_time - open_time).total_seconds() / 60
        net_profit = trade.profit + trade.commission + trade.swap
        profit_pips = abs(trade.close_price - trade.open_price) * 10000

        closed = ClosedTrade(
            account_id=account.id,
            ticket=trade.ticket,
            symbol=trade.symbol,
            trade_type=normalize_trade_type(trade.type),
            lots=trade.lots,
            open_price=trade.open_price,
            close_price=trade.close_price,
            sl=trade.sl,
            tp=trade.tp,
            profit=trade.profit,
            commission=trade.commission,
            swap=trade.swap,
            net_profit=net_profit,
            open_time=open_time,
            close_time=close_time,
            duration_min=duration,
            magic_number=trade.magic_number,
            comment=trade.comment,
            profit_pips=profit_pips,
        )
        db.add(closed)
        new_closed_count += 1

    db.commit()

    # ── Broadcast live update via WebSocket ───────────────────────────────────
    live_data = {
        "type":          "live_update",
        "account_id":    account.id,
        "balance":       payload.balance,
        "equity":        payload.equity,
        "floating_pl":   payload.floating_pl,
        "margin_level":  payload.margin_level,
        "open_orders":   len(payload.open_positions),
        "timestamp":     now_utc.isoformat(),
    }
    broadcast_live_data(account.id, live_data)

    logger.info(
        f"Push received — acct:{account.account_number} "
        f"eq:${payload.equity:.2f} "
        f"closed:{new_closed_count} open:{len(payload.open_positions)}"
    )

    return {
        "status": "ok",
        "new_closed_trades": new_closed_count,
        "timestamp": now_utc.isoformat(),
    }


@router.post("/heartbeat", summary="EA Heartbeat")
async def receive_heartbeat(payload: HeartbeatPayload, db: Session = Depends(get_db)):
    """Lightweight ping — updates last_push_at without full snapshot."""
    account = authenticate_push(db, payload.account_number, payload.api_key)
    account.last_push_at = datetime.now(timezone.utc)
    db.commit()
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@router.get("/config/{account_number}", summary="EA Remote Config")
async def get_ea_config(
    account_number: str,
    x_api_key: str = Header(...),
    db: Session = Depends(get_db),
):
    """Return remote configuration for EA (push interval, enabled symbols, etc.)."""
    account = authenticate_push(db, account_number, x_api_key)
    return {
        "push_interval_sec": account.push_interval_sec,
        "heartbeat_interval_sec": 30,
        "enabled": account.is_active,
    }
