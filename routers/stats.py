# =============================================================================
# routers/stats.py — Statistics & Analytics Endpoints
# =============================================================================
from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional
from datetime import datetime, timezone, timedelta
import logging

from sqlalchemy.orm import Session
from database import get_db
from models import (
    Account, AccountSnapshot, ClosedTrade, OpenPosition,
    DailyStats, User
)
from auth import get_current_user
from routers.accounts import get_account_or_404, user_can_access_account
from calculations import (
    load_closed_trades, load_daily_stats, load_equity_snapshots,
    calc_symbol_breakdown, calc_direction_analysis, calc_hourly_heatmap,
    calc_monthly_returns, calc_rolling_metrics, calc_profit_distribution,
    calc_duration_distribution, calc_mae_mfe_analysis, calc_currency_exposure,
    calc_value_at_risk, recalculate_all_stats, calc_kelly_criterion,
    calc_trades_per_period, calc_avg_profit_per_period,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/stats", tags=["Statistics"])


def parse_date_param(date_str: Optional[str]) -> Optional[datetime]:
    if not date_str:
        return None
    try:
        dt = datetime.fromisoformat(date_str)
        if not dt.tzinfo:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


# =============================================================================
# EQUITY CURVE DATA
# =============================================================================

@router.get("/{account_id}/equity-curve", summary="Equity & Balance Curve")
async def equity_curve(
    account_id: str,
    period: str = Query("1M", description="Today/1D/1W/1M/3M/6M/YTD/1Y/All"),
    from_date: Optional[str] = Query(None),
    to_date:   Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = get_account_or_404(account_id, db)
    if not user_can_access_account(current_user, account, db):
        raise HTTPException(status_code=403, detail="Access denied")

    now = datetime.now(timezone.utc)

    # Calculate date range from period
    period_map = {
        "Today": timedelta(days=1),
        "1D":    timedelta(days=1),
        "1W":    timedelta(weeks=1),
        "1M":    timedelta(days=30),
        "3M":    timedelta(days=90),
        "6M":    timedelta(days=180),
        "YTD":   timedelta(days=(now - now.replace(month=1, day=1)).days),
        "1Y":    timedelta(days=365),
    }

    start = parse_date_param(from_date)
    end   = parse_date_param(to_date) or now

    if not start:
        if period in period_map:
            start = now - period_map[period]
        else:  # All
            start = datetime(2000, 1, 1, tzinfo=timezone.utc)

    snaps = db.query(AccountSnapshot).filter(
        AccountSnapshot.account_id == account_id,
        AccountSnapshot.ts >= start,
        AccountSnapshot.ts <= end,
    ).order_by(AccountSnapshot.ts).all()

    # Get deposit/withdrawal markers
    from models import DepositWithdrawal
    deposits = db.query(DepositWithdrawal).filter(
        DepositWithdrawal.account_id == account_id,
        DepositWithdrawal.tx_date >= start,
        DepositWithdrawal.tx_date <= end,
    ).all()

    # Get EA version change markers
    from models import EaVersionLog
    versions = db.query(EaVersionLog).filter(
        EaVersionLog.account_id == account_id,
        EaVersionLog.created_at >= start,
        EaVersionLog.created_at <= end,
    ).all()

    return {
        "data": [
            {
                "ts":          s.ts.isoformat(),
                "balance":     s.balance,
                "equity":      s.equity,
                "floating_pl": s.floating_pl,
                "margin_level": s.margin_level,
            }
            for s in snaps
        ],
        "markers": {
            "deposits": [
                {"ts": d.tx_date.isoformat(), "amount": d.amount, "note": d.note}
                for d in deposits
            ],
            "ea_versions": [
                {"ts": v.created_at.isoformat(), "version": v.new_version}
                for v in versions
            ],
        },
        "summary": {
            "start_equity": snaps[0].equity if snaps else 0,
            "end_equity":   snaps[-1].equity if snaps else 0,
            "min_equity":   min(s.equity for s in snaps) if snaps else 0,
            "max_equity":   max(s.equity for s in snaps) if snaps else 0,
        },
    }


# =============================================================================
# DAILY P/L BAR CHART
# =============================================================================

@router.get("/{account_id}/daily-pl", summary="Daily P&L Bar Chart")
async def daily_pl(
    account_id: str,
    days: int = Query(90),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = get_account_or_404(account_id, db)
    if not user_can_access_account(current_user, account, db):
        raise HTTPException(status_code=403, detail="Access denied")

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    stats = db.query(DailyStats).filter(
        DailyStats.account_id == account_id,
        DailyStats.date >= cutoff,
    ).order_by(DailyStats.date).all()

    cumulative = 0.0
    result = []
    for s in stats:
        cumulative += s.realized_pl
        result.append({
            "date":         s.date.isoformat()[:10] if hasattr(s.date, 'isoformat') else str(s.date)[:10],
            "pl":           s.realized_pl,
            "pl_pct":       s.daily_return_pct,
            "trade_count":  s.trade_count,
            "cumulative_pl": round(cumulative, 2),
            "is_win":       s.realized_pl >= 0,
        })

    return result


# =============================================================================
# SYMBOL BREAKDOWN
# =============================================================================

@router.get("/{account_id}/symbols", summary="Symbol Performance Breakdown")
async def symbol_breakdown(
    account_id: str,
    days: Optional[int] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = get_account_or_404(account_id, db)
    if not user_can_access_account(current_user, account, db):
        raise HTTPException(status_code=403, detail="Access denied")

    df = load_closed_trades(db, account_id, days)
    return calc_symbol_breakdown(df)


# =============================================================================
# DIRECTION ANALYSIS (Long vs Short)
# =============================================================================

@router.get("/{account_id}/direction", summary="Long vs Short Analysis")
async def direction_analysis(
    account_id: str,
    days: Optional[int] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = get_account_or_404(account_id, db)
    if not user_can_access_account(current_user, account, db):
        raise HTTPException(status_code=403, detail="Access denied")

    df = load_closed_trades(db, account_id, days)
    return calc_direction_analysis(df)


# =============================================================================
# HEATMAPS
# =============================================================================

@router.get("/{account_id}/heatmap/hourly", summary="Hourly Performance Heatmap")
async def hourly_heatmap(
    account_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = get_account_or_404(account_id, db)
    if not user_can_access_account(current_user, account, db):
        raise HTTPException(status_code=403, detail="Access denied")

    df = load_closed_trades(db, account_id)
    return calc_hourly_heatmap(df)


@router.get("/{account_id}/heatmap/monthly", summary="Monthly Returns Heatmap")
async def monthly_heatmap(
    account_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = get_account_or_404(account_id, db)
    if not user_can_access_account(current_user, account, db):
        raise HTTPException(status_code=403, detail="Access denied")

    daily_df = load_daily_stats(db, account_id)
    return calc_monthly_returns(daily_df)


# =============================================================================
# ROLLING METRICS
# =============================================================================

@router.get("/{account_id}/rolling", summary="Rolling Performance Metrics")
async def rolling_metrics(
    account_id: str,
    window: int = Query(30, ge=7, le=90),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = get_account_or_404(account_id, db)
    if not user_can_access_account(current_user, account, db):
        raise HTTPException(status_code=403, detail="Access denied")

    daily_df = load_daily_stats(db, account_id)
    return calc_rolling_metrics(daily_df, window)


# =============================================================================
# DISTRIBUTIONS
# =============================================================================

@router.get("/{account_id}/distribution/profit", summary="Profit Distribution Histogram")
async def profit_distribution(
    account_id: str,
    days: Optional[int] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = get_account_or_404(account_id, db)
    if not user_can_access_account(current_user, account, db):
        raise HTTPException(status_code=403, detail="Access denied")

    df = load_closed_trades(db, account_id, days)
    return calc_profit_distribution(df)


@router.get("/{account_id}/distribution/duration", summary="Trade Duration Distribution")
async def duration_distribution(
    account_id: str,
    days: Optional[int] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = get_account_or_404(account_id, db)
    if not user_can_access_account(current_user, account, db):
        raise HTTPException(status_code=403, detail="Access denied")

    df = load_closed_trades(db, account_id, days)
    return calc_duration_distribution(df)


# =============================================================================
# MAE / MFE ANALYSIS
# =============================================================================

@router.get("/{account_id}/mae-mfe", summary="MAE/MFE Scatter Analysis")
async def mae_mfe_analysis(
    account_id: str,
    days: Optional[int] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = get_account_or_404(account_id, db)
    if not user_can_access_account(current_user, account, db):
        raise HTTPException(status_code=403, detail="Access denied")

    df = load_closed_trades(db, account_id, days)
    return calc_mae_mfe_analysis(df)


# =============================================================================
# CURRENCY EXPOSURE
# =============================================================================

@router.get("/{account_id}/currency-exposure", summary="Currency Exposure by Open Positions")
async def currency_exposure(
    account_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = get_account_or_404(account_id, db)
    if not user_can_access_account(current_user, account, db):
        raise HTTPException(status_code=403, detail="Access denied")

    positions = db.query(OpenPosition).filter(OpenPosition.account_id == account_id).all()
    return calc_currency_exposure(positions)


# =============================================================================
# PORTFOLIO MULTI-ACCOUNT STATS
# =============================================================================

@router.get("/portfolio/summary", summary="Portfolio Summary (All Accounts)")
async def portfolio_summary(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    accounts = db.query(Account).filter(
        Account.owner_id == current_user.id,
        Account.is_active == True,
    ).all()

    all_positions = []
    for acc in accounts:
        positions = db.query(OpenPosition).filter(OpenPosition.account_id == acc.id).all()
        all_positions.extend(positions)

    return {
        "total_accounts":     len(accounts),
        "total_balance":      sum(a.balance for a in accounts),
        "total_equity":       sum(a.equity for a in accounts),
        "total_floating_pl":  sum(a.floating_pl for a in accounts),
        "total_profit_today": sum(a.profit_today for a in accounts),
        "total_open_orders":  sum(a.open_orders_count for a in accounts),
        "combined_currency_exposure": calc_currency_exposure(all_positions),
        "accounts": [
            {
                "id":             a.id,
                "label":          a.label or a.account_number,
                "balance":        a.balance,
                "equity":         a.equity,
                "growth_pct":     a.growth_pct,
                "max_dd_pct":     a.max_drawdown_pct,
                "profit_factor":  a.profit_factor,
                "win_rate":       a.win_rate,
                "sharpe_ratio":   a.sharpe_ratio,
                "avg_daily_profit": a.avg_daily_profit,
                "status":         a.status.value,
            }
            for a in accounts
        ],
    }


# =============================================================================
# FORCE RECALCULATE
# =============================================================================

@router.post("/{account_id}/recalculate", summary="Force Full Recalculation")
async def force_recalculate(
    account_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = get_account_or_404(account_id, db)
    if account.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    recalculate_all_stats(db, account)
    return {"message": "Recalculation complete", "account_id": account_id}
