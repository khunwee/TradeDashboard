# =============================================================================
# scheduler.py — Background Job Scheduler (APScheduler)
# Jobs: stat recalculation, alert checking, daily reports, data cleanup
# =============================================================================
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from datetime import datetime, timezone, timedelta
import logging

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="UTC")


# =============================================================================
# JOB: Recalculate All Account Statistics (every 60 seconds)
# =============================================================================

async def job_recalculate_stats():
    """Recalculate all statistical metrics for all active accounts."""
    from database import get_db_context
    from models import Account
    from calculations import recalculate_all_stats

    with get_db_context() as db:
        accounts = db.query(Account).filter(Account.is_active == True).all()
        for account in accounts:
            recalculate_all_stats(db, account)
    logger.debug("Stats recalculation complete")


# =============================================================================
# JOB: Check Alert Conditions (every 30 seconds)
# =============================================================================

async def job_check_alerts():
    """Check all active alert rules and fire if conditions met."""
    from alerts import check_all_alerts
    await check_all_alerts()


# =============================================================================
# JOB: Update Account Statuses (every 15 seconds)
# =============================================================================

async def job_update_statuses():
    """Mark accounts as Live/Delayed/Disconnected based on last push time."""
    from database import get_db_context
    from models import Account, AccountStatus

    now = datetime.now(timezone.utc)

    with get_db_context() as db:
        accounts = db.query(Account).filter(Account.is_active == True).all()
        for account in accounts:
            if not account.last_push_at:
                account.status = AccountStatus.DISCONNECTED
                continue

            last_push = account.last_push_at
            if not last_push.tzinfo:
                last_push = last_push.replace(tzinfo=timezone.utc)

            elapsed_sec = (now - last_push).total_seconds()
            timeout = account.heartbeat_timeout_sec or 60

            if elapsed_sec <= timeout:
                account.status = AccountStatus.LIVE
            elif elapsed_sec <= 300:  # 5 minutes
                account.status = AccountStatus.DELAYED
            else:
                account.status = AccountStatus.DISCONNECTED


# =============================================================================
# JOB: Aggregate Daily Stats (every day at 00:05 UTC)
# =============================================================================

async def job_aggregate_daily_stats():
    """
    Create/update DailyStats record for yesterday.
    Called at 00:05 UTC so all trades for the day are closed.
    """
    from database import get_db_context
    from models import Account, ClosedTrade, AccountSnapshot, DailyStats

    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    today = yesterday + timedelta(days=1)

    with get_db_context() as db:
        accounts = db.query(Account).filter(Account.is_active == True).all()

        for account in accounts:
            try:
                # Trades closed yesterday
                trades = db.query(ClosedTrade).filter(
                    ClosedTrade.account_id == account.id,
                    ClosedTrade.close_time >= yesterday,
                    ClosedTrade.close_time < today,
                ).all()

                # Snapshots for equity curve
                snaps = db.query(AccountSnapshot).filter(
                    AccountSnapshot.account_id == account.id,
                    AccountSnapshot.ts >= yesterday,
                    AccountSnapshot.ts < today,
                ).all()

                if not snaps:
                    continue

                equities = [s.equity for s in snaps]
                realized_pl = sum(t.net_profit for t in trades)
                wins = sum(1 for t in trades if t.net_profit > 0)
                lots = sum(t.lots for t in trades)

                open_equity  = snaps[0].equity if snaps else 0
                close_equity = snaps[-1].equity if snaps else 0
                open_balance = snaps[0].balance if snaps else 0
                close_balance = snaps[-1].balance if snaps else 0

                daily_return = (
                    (close_equity - open_equity) / open_equity * 100
                    if open_equity > 0 else 0
                )

                # Max intraday drawdown
                peak = open_equity
                max_dd = 0.0
                for eq in equities:
                    peak = max(peak, eq)
                    dd = peak - eq
                    max_dd = max(max_dd, dd)

                # Upsert DailyStats
                existing = db.query(DailyStats).filter(
                    DailyStats.account_id == account.id,
                    DailyStats.date == yesterday,
                ).first()

                if existing:
                    stat = existing
                else:
                    stat = DailyStats(account_id=account.id, date=yesterday)
                    db.add(stat)

                stat.open_balance     = open_balance
                stat.close_balance    = close_balance
                stat.open_equity      = open_equity
                stat.close_equity     = close_equity
                stat.high_equity      = max(equities) if equities else 0
                stat.low_equity       = min(equities) if equities else 0
                stat.realized_pl      = realized_pl
                stat.total_pl         = close_equity - open_equity
                stat.daily_return_pct = daily_return
                stat.trade_count      = len(trades)
                stat.win_count        = wins
                stat.lots_traded      = lots
                stat.max_drawdown     = max_dd

            except Exception as e:
                logger.error(f"Daily stats aggregation failed for account {account.id}: {e}")


# =============================================================================
# JOB: Send Daily Email Reports (configurable time per user)
# =============================================================================

async def job_send_daily_reports():
    """Send scheduled daily reports to users who have them configured."""
    from database import get_db_context
    from models import ScheduledReport, User, Account
    from reports import generate_daily_report_email

    now_hour = datetime.now(timezone.utc).hour
    now_min  = datetime.now(timezone.utc).minute

    with get_db_context() as db:
        reports = db.query(ScheduledReport).filter(
            ScheduledReport.is_active == True,
            ScheduledReport.frequency == "daily",
        ).all()

        for report in reports:
            try:
                scheduled_h, scheduled_m = map(int, report.send_time.split(":"))
                if abs(now_hour - scheduled_h) == 0 and abs(now_min - scheduled_m) < 5:
                    await generate_daily_report_email(db, report)
            except Exception as e:
                logger.error(f"Daily report {report.id} failed: {e}")


# =============================================================================
# JOB: Purge Old Tick Data (daily at 03:00 UTC)
# =============================================================================

async def job_purge_old_snapshots():
    """Delete tick-level snapshots older than retention period."""
    from database import get_db_context
    from models import AccountSnapshot
    from config import settings

    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.TICK_DATA_RETENTION_DAYS)

    with get_db_context() as db:
        deleted = db.query(AccountSnapshot).filter(
            AccountSnapshot.ts < cutoff
        ).delete(synchronize_session=False)
        logger.info(f"Purged {deleted} old snapshots (>{settings.TICK_DATA_RETENTION_DAYS} days)")


# =============================================================================
# JOB: Compute Peak Equity & Max Deposit Load (every 5 minutes)
# =============================================================================

async def job_update_peak_equity():
    """Update peak equity and max deposit load % per account."""
    from database import get_db_context
    from models import Account

    with get_db_context() as db:
        accounts = db.query(Account).filter(Account.is_active == True).all()
        for account in accounts:
            if account.equity > (account.peak_equity or 0):
                account.peak_equity = account.equity

            if account.equity > 0 and account.margin > 0:
                load_pct = account.margin / account.equity * 100
                if load_pct > (account.max_deposit_load_pct or 0):
                    account.max_deposit_load_pct = load_pct


# =============================================================================
# SCHEDULER SETUP
# =============================================================================

def setup_scheduler():
    """Register all jobs and return configured scheduler."""

    scheduler.add_job(
        job_update_statuses,
        trigger=IntervalTrigger(seconds=15),
        id="update_statuses",
        replace_existing=True,
        max_instances=1,
    )

    scheduler.add_job(
        job_check_alerts,
        trigger=IntervalTrigger(seconds=30),
        id="check_alerts",
        replace_existing=True,
        max_instances=1,
    )

    scheduler.add_job(
        job_recalculate_stats,
        trigger=IntervalTrigger(seconds=60),
        id="recalculate_stats",
        replace_existing=True,
        max_instances=1,
    )

    scheduler.add_job(
        job_update_peak_equity,
        trigger=IntervalTrigger(minutes=5),
        id="update_peak_equity",
        replace_existing=True,
        max_instances=1,
    )

    scheduler.add_job(
        job_aggregate_daily_stats,
        trigger=CronTrigger(hour=0, minute=5),
        id="aggregate_daily_stats",
        replace_existing=True,
        max_instances=1,
    )

    scheduler.add_job(
        job_send_daily_reports,
        trigger=IntervalTrigger(minutes=5),
        id="send_daily_reports",
        replace_existing=True,
        max_instances=1,
    )

    scheduler.add_job(
        job_purge_old_snapshots,
        trigger=CronTrigger(hour=3, minute=0),
        id="purge_old_snapshots",
        replace_existing=True,
        max_instances=1,
    )

    return scheduler
