# =============================================================================
# calculations.py — Complete Statistical Calculation Engine
# All 25+ metrics: Sharpe, Sortino, Calmar, MAE/MFE, Kelly, etc.
# =============================================================================
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List, Tuple
from sqlalchemy.orm import Session
import logging

from models import Account, ClosedTrade, AccountSnapshot, DailyStats, OpenPosition
from config import settings

logger = logging.getLogger(__name__)


# =============================================================================
# DATA LOADERS
# =============================================================================

def load_closed_trades(db: Session, account_id: str, days: Optional[int] = None) -> pd.DataFrame:
    """Load closed trades as a DataFrame."""
    q = db.query(ClosedTrade).filter(ClosedTrade.account_id == account_id)
    if days:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        q = q.filter(ClosedTrade.close_time >= cutoff)
    trades = q.order_by(ClosedTrade.close_time).all()

    if not trades:
        return pd.DataFrame()

    data = [{
        "ticket":       t.ticket,
        "symbol":       t.symbol,
        "trade_type":   t.trade_type.value,
        "lots":         t.lots,
        "open_price":   t.open_price,
        "close_price":  t.close_price,
        "profit":       t.profit,
        "commission":   t.commission,
        "swap":         t.swap,
        "net_profit":   t.net_profit,
        "open_time":    t.open_time,
        "close_time":   t.close_time,
        "duration_min": t.duration_min,
        "magic_number": t.magic_number,
        "mae":          t.mae,
        "mfe":          t.mfe,
    } for t in trades]

    df = pd.DataFrame(data)
    df["open_time"]  = pd.to_datetime(df["open_time"],  utc=True)
    df["close_time"] = pd.to_datetime(df["close_time"], utc=True)
    df["is_win"]     = df["net_profit"] > 0
    return df


def load_daily_stats(db: Session, account_id: str) -> pd.DataFrame:
    """Load daily stats as a DataFrame sorted by date."""
    stats = db.query(DailyStats).filter(
        DailyStats.account_id == account_id
    ).order_by(DailyStats.date).all()

    if not stats:
        return pd.DataFrame()

    return pd.DataFrame([{
        "date":             s.date,
        "open_balance":     s.open_balance,
        "close_balance":    s.close_balance,
        "open_equity":      s.open_equity,
        "close_equity":     s.close_equity,
        "high_equity":      s.high_equity,
        "low_equity":       s.low_equity,
        "realized_pl":      s.realized_pl,
        "total_pl":         s.total_pl,
        "daily_return_pct": s.daily_return_pct,
        "trade_count":      s.trade_count,
        "win_count":        s.win_count,
        "lots_traded":      s.lots_traded,
        "max_drawdown":     s.max_drawdown,
    } for s in stats])


def load_equity_snapshots(db: Session, account_id: str, limit: int = 10000) -> pd.DataFrame:
    """Load equity curve snapshots."""
    snaps = db.query(AccountSnapshot).filter(
        AccountSnapshot.account_id == account_id
    ).order_by(AccountSnapshot.ts).limit(limit).all()

    if not snaps:
        return pd.DataFrame()

    return pd.DataFrame([{
        "ts":          s.ts,
        "balance":     s.balance,
        "equity":      s.equity,
        "floating_pl": s.floating_pl,
        "margin":      s.margin,
        "margin_level": s.margin_level,
        "open_orders": s.open_orders,
    } for s in snaps])


# =============================================================================
# CORE BALANCE / EQUITY METRICS
# =============================================================================

def calc_floating_pl(equity: float, balance: float) -> float:
    """Floating P/L = Equity - Balance"""
    return equity - balance


def calc_profit_today(
    db: Session,
    account_id: str,
    balance_start_of_day: float,
    current_floating_pl: float,
    include_unrealized: bool = True,
) -> Tuple[float, float]:
    """
    Profit Today ($) = Sum of profits of trades closed today + current floating P/L (if configured)
    Profit Today (%) = Profit Today / Balance at start of today × 100
    Returns: (profit_today_dollar, profit_today_pct)
    """
    today_utc = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    closed_today = db.query(ClosedTrade).filter(
        ClosedTrade.account_id == account_id,
        ClosedTrade.close_time >= today_utc,
    ).all()
    realized = sum(t.net_profit for t in closed_today)

    profit = realized + (current_floating_pl if include_unrealized else 0)

    pct = (profit / balance_start_of_day * 100) if balance_start_of_day > 0 else 0.0
    return round(profit, 2), round(pct, 4)


# =============================================================================
# DRAWDOWN CALCULATIONS
# =============================================================================

def calc_daily_max_drawdown(
    db: Session, account_id: str, balance_start_of_day: float
) -> float:
    """
    Daily Max DD = min(Equity during today) - Balance at start of today
    Returned as negative dollar value.
    """
    today_utc = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    snaps = db.query(AccountSnapshot).filter(
        AccountSnapshot.account_id == account_id,
        AccountSnapshot.ts >= today_utc,
    ).all()

    if not snaps:
        return 0.0

    min_equity = min(s.equity for s in snaps)
    return round(min_equity - balance_start_of_day, 2)


def calc_absolute_max_drawdown(equity_series: pd.Series) -> Tuple[float, float]:
    """
    Absolute Max DD ($) = Peak Equity (all-time) - Lowest Equity after that peak
    Returns: (max_dd_dollar, max_dd_pct)
    """
    if equity_series.empty:
        return 0.0, 0.0

    peak = equity_series.cummax()
    drawdown = equity_series - peak

    max_dd_dollar = drawdown.min()  # most negative value
    peak_at_dd = peak[drawdown.idxmin()] if not drawdown.empty else 1
    max_dd_pct = (max_dd_dollar / peak_at_dd * 100) if peak_at_dd > 0 else 0.0

    return round(max_dd_dollar, 2), round(max_dd_pct, 4)


def calc_relative_drawdown_pct(equity_series: pd.Series) -> float:
    """
    Relative Max DD % = (Peak Equity - Trough Equity) / Peak Equity × 100
    """
    if equity_series.empty:
        return 0.0
    peak = equity_series.cummax()
    dd_pct = ((peak - equity_series) / peak * 100)
    return round(dd_pct.max(), 4)


def calc_current_drawdown(current_equity: float, peak_equity: float) -> Tuple[float, float]:
    """Current drawdown from peak — updated real-time."""
    if peak_equity <= 0:
        return 0.0, 0.0
    dd_dollar = current_equity - peak_equity
    dd_pct = (dd_dollar / peak_equity) * 100
    return round(dd_dollar, 2), round(dd_pct, 4)


# =============================================================================
# RISK-ADJUSTED RETURN METRICS
# =============================================================================

def calc_sharpe_ratio(daily_returns: pd.Series, risk_free_rate_annual: float = 0.0) -> Optional[float]:
    """
    Sharpe = (Mean Daily Return - Risk-Free Rate) / StdDev of Daily Returns
    Requires minimum 30 days of data.
    """
    if len(daily_returns) < 30:
        return None

    daily_rf = risk_free_rate_annual / 365
    excess   = daily_returns - daily_rf
    std      = daily_returns.std()

    if std == 0:
        return None

    # Annualize: multiply by sqrt(252) trading days
    sharpe = (excess.mean() / std) * np.sqrt(252)
    return round(float(sharpe), 4)


def calc_sortino_ratio(daily_returns: pd.Series, risk_free_rate_annual: float = 0.0) -> Optional[float]:
    """
    Sortino = (Mean Daily Return - Risk-Free Rate) / Downside Deviation
    Downside Deviation = StdDev of negative returns only.
    """
    if len(daily_returns) < 30:
        return None

    daily_rf     = risk_free_rate_annual / 365
    excess        = daily_returns - daily_rf
    negative_rets = daily_returns[daily_returns < 0]

    if len(negative_rets) == 0:
        return None

    downside_dev = negative_rets.std()
    if downside_dev == 0:
        return None

    sortino = (excess.mean() / downside_dev) * np.sqrt(252)
    return round(float(sortino), 4)


def calc_calmar_ratio(annualized_return_pct: float, max_drawdown_pct: float) -> Optional[float]:
    """Calmar = Annualized Return % / Max Drawdown %"""
    if max_drawdown_pct == 0:
        return None
    return round(abs(annualized_return_pct / max_drawdown_pct), 4)


# =============================================================================
# TRADE PERFORMANCE METRICS
# =============================================================================

def calc_profit_factor(df: pd.DataFrame) -> float:
    """
    Profit Factor = Gross Profit / |Gross Loss|
    >2 excellent, 1.5–2 good, 1–1.5 acceptable, <1 losing
    """
    if df.empty:
        return 0.0

    gross_profit = df[df["net_profit"] > 0]["net_profit"].sum()
    gross_loss   = abs(df[df["net_profit"] < 0]["net_profit"].sum())

    if gross_loss == 0:
        return 999.0 if gross_profit > 0 else 0.0

    return round(gross_profit / gross_loss, 4)


def calc_win_rate(df: pd.DataFrame) -> float:
    """Win Rate % = Winning Trades / Total Closed Trades × 100"""
    if df.empty:
        return 0.0
    return round(df["is_win"].sum() / len(df) * 100, 2)


def calc_expectancy(df: pd.DataFrame) -> float:
    """
    Expectancy = (Win Rate × Avg Win $) - (Loss Rate × Avg Loss $)
    Positive = system has edge.
    """
    if df.empty:
        return 0.0

    wins   = df[df["is_win"]]
    losses = df[~df["is_win"]]

    win_rate  = len(wins) / len(df)
    loss_rate = 1 - win_rate
    avg_win   = wins["net_profit"].mean() if len(wins) > 0 else 0.0
    avg_loss  = abs(losses["net_profit"].mean()) if len(losses) > 0 else 0.0

    return round((win_rate * avg_win) - (loss_rate * avg_loss), 4)


def calc_recovery_factor(net_profit: float, max_drawdown_abs: float) -> float:
    """Recovery Factor = Net Profit / |Max Absolute Drawdown $|"""
    if max_drawdown_abs == 0:
        return 0.0
    return round(net_profit / abs(max_drawdown_abs), 4)


def calc_kelly_criterion(df: pd.DataFrame) -> Optional[float]:
    """
    Kelly % = Win Rate - (Loss Rate / (Avg Win / Avg Loss))
    Informational only — suggested % of capital per trade.
    """
    if df.empty or len(df) < 10:
        return None

    wins   = df[df["is_win"]]
    losses = df[~df["is_win"]]

    if len(wins) == 0 or len(losses) == 0:
        return None

    win_rate  = len(wins) / len(df)
    loss_rate = 1 - win_rate
    avg_win   = wins["net_profit"].mean()
    avg_loss  = abs(losses["net_profit"].mean())

    if avg_loss == 0:
        return None

    kelly = win_rate - (loss_rate / (avg_win / avg_loss))
    return round(float(kelly * 100), 2)   # as percentage


def calc_avg_hold_time(df: pd.DataFrame) -> Optional[float]:
    """Average Hold Time in minutes."""
    if df.empty or "duration_min" not in df.columns:
        return None
    valid = df["duration_min"].dropna()
    if valid.empty:
        return None
    return round(float(valid.mean()), 2)


def calc_consecutive_streaks(df: pd.DataFrame) -> Tuple[int, int, int]:
    """
    Returns: (max_consecutive_wins, max_consecutive_losses, current_streak)
    current_streak: positive = wins, negative = losses
    """
    if df.empty:
        return 0, 0, 0

    results = df.sort_values("close_time")["is_win"].tolist()
    max_wins = max_losses = current = best_win = best_loss = 0

    for win in results:
        if win:
            current = current + 1 if current > 0 else 1
        else:
            current = current - 1 if current < 0 else -1
        best_win  = max(best_win, current)
        best_loss = min(best_loss, current)

    return best_win, abs(best_loss), current


def calc_growth_pct(
    current_balance: float,
    initial_deposit: float,
    deposits_withdrawals: Optional[pd.DataFrame] = None,
) -> float:
    """
    Growth % — Modified Dietz method when there are deposits/withdrawals.
    Adjusted Growth = (End - Start - Net Deposits) / (Start + Weighted Net Deposits) × 100
    """
    if initial_deposit <= 0:
        return 0.0

    if deposits_withdrawals is None or deposits_withdrawals.empty:
        return round((current_balance - initial_deposit) / initial_deposit * 100, 4)

    # Modified Dietz
    net_deposits = deposits_withdrawals["amount"].sum()
    # Weight by time (simplified: midpoint weighting)
    weighted_deposits = net_deposits * 0.5
    denominator = initial_deposit + weighted_deposits

    if denominator <= 0:
        return 0.0

    growth = (current_balance - initial_deposit - net_deposits) / denominator * 100
    return round(growth, 4)


def calc_annualized_return(total_growth_pct: float, days_active: int) -> Optional[float]:
    """
    Annualized Return % = ((1 + Total Growth %)^(365/Days Active) - 1) × 100
    """
    if days_active <= 0:
        return None

    factor = (1 + total_growth_pct / 100) ** (365 / days_active) - 1
    return round(factor * 100, 4)


def calc_trades_per_period(df: pd.DataFrame, start_date: datetime) -> Dict[str, float]:
    """Trades per day, week, and calendar month."""
    if df.empty:
        return {"per_day": 0, "per_week": 0, "per_month": 0}

    now = datetime.now(timezone.utc)
    days_active = max((now - start_date).days, 1)
    weeks  = max(days_active / 7, 1)
    months = max(days_active / 30.44, 1)
    total  = len(df)

    return {
        "per_day":   round(total / days_active, 2),
        "per_week":  round(total / weeks, 2),
        "per_month": round(total / months, 2),
    }


def calc_avg_profit_per_period(
    net_profit: float, start_date: datetime
) -> Dict[str, float]:
    now = datetime.now(timezone.utc)
    days_active = max((now - start_date).days, 1)
    weeks  = max(days_active / 7, 1)
    months = max(days_active / 30.44, 1)

    return {
        "per_day":   round(net_profit / days_active, 2),
        "per_week":  round(net_profit / weeks, 2),
        "per_month": round(net_profit / months, 2),
    }


# =============================================================================
# ADVANCED ANALYTICS
# =============================================================================

def calc_mae_mfe_analysis(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Maximum Adverse Excursion (MAE): worst floating loss before close.
    Maximum Favorable Excursion (MFE): best floating profit before close.
    Returns summary statistics for scatter plots.
    """
    if df.empty or "mae" not in df.columns:
        return {}

    mae_data = df[["mae", "mfe", "net_profit", "symbol"]].dropna()

    return {
        "avg_mae":         round(mae_data["mae"].mean(), 2) if not mae_data.empty else 0,
        "avg_mfe":         round(mae_data["mfe"].mean(), 2) if not mae_data.empty else 0,
        "mae_vs_profit":   mae_data[["mae", "net_profit"]].to_dict("records"),
        "mfe_vs_profit":   mae_data[["mfe", "net_profit"]].to_dict("records"),
        "avg_mfe_to_mae":  round(mae_data["mfe"].mean() / abs(mae_data["mae"].mean()), 2)
                           if mae_data["mae"].mean() != 0 else 0,
    }


def calc_symbol_breakdown(df: pd.DataFrame) -> List[Dict]:
    """Symbol-level performance breakdown."""
    if df.empty:
        return []

    grouped = df.groupby("symbol").agg(
        trade_count=("net_profit", "count"),
        win_count=("is_win", "sum"),
        gross_profit=("net_profit", lambda x: x[x > 0].sum()),
        gross_loss=("net_profit", lambda x: x[x < 0].sum()),
        net_profit=("net_profit", "sum"),
        total_lots=("lots", "sum"),
        avg_hold_min=("duration_min", "mean"),
    ).reset_index()

    result = []
    for _, row in grouped.iterrows():
        pf = (row["gross_profit"] / abs(row["gross_loss"])
              if row["gross_loss"] != 0 else 999.0)
        result.append({
            "symbol":       row["symbol"],
            "trade_count":  int(row["trade_count"]),
            "win_rate":     round(row["win_count"] / row["trade_count"] * 100, 2),
            "gross_profit": round(row["gross_profit"], 2),
            "gross_loss":   round(row["gross_loss"], 2),
            "net_profit":   round(row["net_profit"], 2),
            "profit_factor": round(pf, 2),
            "total_lots":   round(row["total_lots"], 2),
            "avg_hold_min": round(row["avg_hold_min"], 1) if not pd.isna(row["avg_hold_min"]) else 0,
        })

    return sorted(result, key=lambda x: x["net_profit"], reverse=True)


def calc_direction_analysis(df: pd.DataFrame) -> Dict:
    """Long vs Short performance split."""
    if df.empty:
        return {}

    longs  = df[df["trade_type"].isin(["buy", "buy_limit", "buy_stop"])]
    shorts = df[df["trade_type"].isin(["sell", "sell_limit", "sell_stop"])]

    def summarize(subset: pd.DataFrame) -> Dict:
        if subset.empty:
            return {"count": 0, "win_rate": 0, "avg_profit": 0, "total_lots": 0, "net_profit": 0}
        return {
            "count":       len(subset),
            "win_rate":    round(subset["is_win"].mean() * 100, 2),
            "avg_profit":  round(subset["net_profit"].mean(), 2),
            "total_lots":  round(subset["lots"].sum(), 2),
            "net_profit":  round(subset["net_profit"].sum(), 2),
        }

    return {"long": summarize(longs), "short": summarize(shorts)}


def calc_hourly_heatmap(df: pd.DataFrame) -> List[Dict]:
    """Performance by weekday × hour."""
    if df.empty:
        return []

    df2 = df.copy()
    df2["weekday"] = df2["close_time"].dt.weekday   # 0=Mon, 4=Fri
    df2["hour"]    = df2["close_time"].dt.hour

    heatmap = df2.groupby(["weekday", "hour"]).agg(
        avg_profit=("net_profit", "mean"),
        trade_count=("net_profit", "count"),
    ).reset_index()

    return [
        {
            "weekday":     int(row["weekday"]),
            "hour":        int(row["hour"]),
            "avg_profit":  round(row["avg_profit"], 2),
            "trade_count": int(row["trade_count"]),
        }
        for _, row in heatmap.iterrows()
    ]


def calc_monthly_returns(daily_df: pd.DataFrame) -> List[Dict]:
    """Monthly returns heatmap data."""
    if daily_df.empty:
        return []

    df2 = daily_df.copy()
    df2["date"] = pd.to_datetime(df2["date"], utc=True)
    df2["year"]  = df2["date"].dt.year
    df2["month"] = df2["date"].dt.month

    monthly = df2.groupby(["year", "month"]).agg(
        realized_pl=("realized_pl", "sum"),
        open_balance=("open_balance", "first"),
    ).reset_index()

    result = []
    for _, row in monthly.iterrows():
        pct = (row["realized_pl"] / row["open_balance"] * 100
               if row["open_balance"] > 0 else 0)
        result.append({
            "year":  int(row["year"]),
            "month": int(row["month"]),
            "pl":    round(row["realized_pl"], 2),
            "pct":   round(pct, 4),
        })

    return result


def calc_rolling_metrics(daily_df: pd.DataFrame, window: int = 30) -> List[Dict]:
    """Rolling window Sharpe, win rate, profit factor for degradation detection."""
    if daily_df.empty or len(daily_df) < window:
        return []

    df2 = daily_df.copy().sort_values("date").reset_index(drop=True)
    results = []

    for i in range(window, len(df2) + 1):
        chunk = df2.iloc[i - window:i]
        returns = chunk["daily_return_pct"] / 100

        sharpe = calc_sharpe_ratio(returns, settings.RISK_FREE_RATE)
        profit_days = chunk[chunk["realized_pl"] > 0]["realized_pl"].sum()
        loss_days   = abs(chunk[chunk["realized_pl"] < 0]["realized_pl"].sum())
        pf = profit_days / loss_days if loss_days > 0 else 999.0
        wins   = chunk["win_count"].sum()
        trades = chunk["trade_count"].sum()
        wr     = wins / trades * 100 if trades > 0 else 0

        results.append({
            "date":          chunk.iloc[-1]["date"].isoformat() if hasattr(chunk.iloc[-1]["date"], "isoformat") else str(chunk.iloc[-1]["date"]),
            "sharpe":        sharpe,
            "profit_factor": round(pf, 2),
            "win_rate":      round(wr, 2),
        })

    return results


def calc_profit_distribution(df: pd.DataFrame, buckets: int = 20) -> List[Dict]:
    """Histogram buckets of trade profit distribution."""
    if df.empty:
        return []

    profits = df["net_profit"]
    min_p, max_p = profits.min(), profits.max()
    bins = np.linspace(min_p, max_p, buckets + 1)

    hist_wins, _ = np.histogram(
        df[df["is_win"]]["net_profit"], bins=bins
    )
    hist_loss, _ = np.histogram(
        df[~df["is_win"]]["net_profit"], bins=bins
    )

    result = []
    for i in range(len(bins) - 1):
        result.append({
            "from":   round(float(bins[i]), 2),
            "to":     round(float(bins[i + 1]), 2),
            "wins":   int(hist_wins[i]),
            "losses": int(hist_loss[i]),
            "total":  int(hist_wins[i] + hist_loss[i]),
        })

    return result


def calc_duration_distribution(df: pd.DataFrame) -> List[Dict]:
    """Histogram of trade hold times by category."""
    if df.empty or "duration_min" not in df.columns:
        return []

    buckets = [
        ("< 1 min",    0, 1),
        ("1–5 min",    1, 5),
        ("5–30 min",   5, 30),
        ("30m–2h",     30, 120),
        ("2h–8h",      120, 480),
        ("8h–1d",      480, 1440),
        ("> 1 day",    1440, float("inf")),
    ]

    result = []
    for label, lo, hi in buckets:
        mask = (df["duration_min"] >= lo) & (df["duration_min"] < hi)
        subset = df[mask]
        result.append({
            "label":  label,
            "wins":   int(subset["is_win"].sum()),
            "losses": int((~subset["is_win"]).sum()),
            "total":  len(subset),
        })

    return result


def calc_currency_exposure(open_positions: List[Any]) -> Dict[str, float]:
    """
    Net long/short exposure per base currency.
    Positive = net long, Negative = net short.
    """
    exposure: Dict[str, float] = {}

    for pos in open_positions:
        symbol = pos.symbol.replace("-VIP", "").replace(".", "")  # normalize
        if len(symbol) < 6:
            continue
        base = symbol[:3].upper()
        quote = symbol[3:6].upper()

        # Long = buy base, Short = sell base
        direction = 1 if pos.trade_type.value in ("buy", "buy_limit", "buy_stop") else -1
        lots = pos.lots * direction

        exposure[base]  = exposure.get(base, 0.0)  + lots
        exposure[quote] = exposure.get(quote, 0.0) - lots

    return {k: round(v, 2) for k, v in sorted(exposure.items())}


def calc_value_at_risk(daily_returns: pd.Series, confidence: float = 0.95) -> Optional[float]:
    """
    VaR at given confidence level (parametric method).
    Returns estimated max loss in next period as positive %.
    """
    if len(daily_returns) < 30:
        return None

    mean   = daily_returns.mean()
    std    = daily_returns.std()
    z      = abs(np.percentile(daily_returns, (1 - confidence) * 100))
    return round(float(z * 100), 4)   # as % of equity


# =============================================================================
# MASTER RECALCULATION — run periodically by scheduler
# =============================================================================

def recalculate_all_stats(db: Session, account: Account):
    """
    Full recalculation of all statistics for an account.
    Called every minute by the background scheduler.
    """
    try:
        trades_df  = load_closed_trades(db, account.id)
        daily_df   = load_daily_stats(db, account.id)
        snaps_df   = load_equity_snapshots(db, account.id)

        if trades_df.empty:
            return

        # Basic counts
        account.total_trades = len(trades_df)
        account.win_rate     = calc_win_rate(trades_df)
        account.profit_factor = calc_profit_factor(trades_df)
        account.expectancy   = calc_expectancy(trades_df)
        account.largest_win  = float(trades_df["net_profit"].max())
        account.largest_loss = float(trades_df["net_profit"].min())
        account.avg_hold_time_min = calc_avg_hold_time(trades_df)

        # Streaks
        mw, ml, cs = calc_consecutive_streaks(trades_df)
        account.max_consecutive_wins   = mw
        account.max_consecutive_losses = ml
        account.current_streak         = cs

        # Equity curve stats
        if not snaps_df.empty:
            equity_series = snaps_df["equity"]
            dd_abs, dd_pct = calc_absolute_max_drawdown(equity_series)
            account.max_drawdown_abs  = dd_abs
            account.max_drawdown_pct  = dd_pct
            account.peak_equity       = float(equity_series.max())

        # Daily return metrics
        if not daily_df.empty and len(daily_df) >= 30:
            returns = daily_df["daily_return_pct"] / 100
            account.sharpe_ratio  = calc_sharpe_ratio(returns, settings.RISK_FREE_RATE)
            account.sortino_ratio = calc_sortino_ratio(returns, settings.RISK_FREE_RATE)

        # Growth & returns
        start_date = account.start_date or account.created_at
        days_active = max((datetime.now(timezone.utc) - start_date).days, 1)
        net_profit   = trades_df["net_profit"].sum()

        account.growth_pct = calc_growth_pct(account.balance, account.initial_deposit)
        account.annualized_return = calc_annualized_return(account.growth_pct, days_active)
        account.calmar_ratio = calc_calmar_ratio(
            account.annualized_return or 0, account.max_drawdown_pct
        )
        account.recovery_factor = calc_recovery_factor(net_profit, account.max_drawdown_abs)

        # Avg daily profit
        period_stats = calc_avg_profit_per_period(net_profit, start_date)
        account.avg_daily_profit = period_stats["per_day"]

        db.commit()
        logger.debug(f"Recalculated stats for account {account.account_number}")

    except Exception as e:
        logger.error(f"Error recalculating stats for account {account.id}: {e}")
        db.rollback()
