# =============================================================================
# alerts.py — Alert Engine: Rule Evaluation + Multi-Channel Delivery
# Channels: In-App, Email, LINE Notify, Telegram, SMS (Twilio), Discord
# =============================================================================
from datetime import datetime, timezone, timedelta
from typing import Optional, List
import logging
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import httpx
from sqlalchemy.orm import Session

from config import settings
from models import (
    Account, AlertRule, AlertHistory, AlertConditionType,
    AlertChannel, AlertStatus, Notification, User
)
from database import get_db_context

logger = logging.getLogger(__name__)


# =============================================================================
# ALERT MESSAGE BUILDERS
# =============================================================================

def build_alert_message(rule: AlertRule, account: Account, trigger_value: float) -> str:
    """Human-readable alert message."""
    acct_label = account.label or account.account_number
    ctype = rule.condition_type

    messages = {
        AlertConditionType.PROFIT_DAILY_TARGET:   f"✅ {acct_label}: Daily profit target reached! ${trigger_value:.2f}",
        AlertConditionType.PROFIT_WEEKLY_TARGET:  f"✅ {acct_label}: Weekly profit target reached! ${trigger_value:.2f}",
        AlertConditionType.PROFIT_MONTHLY_TARGET: f"✅ {acct_label}: Monthly profit target reached! ${trigger_value:.2f}",
        AlertConditionType.NEW_EQUITY_HIGH:       f"🚀 {acct_label}: New all-time equity high! ${trigger_value:.2f}",
        AlertConditionType.DRAWDOWN_WARNING:      f"⚠️ {acct_label}: Drawdown warning — {trigger_value:.2f}% drawdown",
        AlertConditionType.DRAWDOWN_CRITICAL:     f"🚨 {acct_label}: CRITICAL drawdown — {trigger_value:.2f}% drawdown!",
        AlertConditionType.DAILY_LOSS_LIMIT:      f"🔴 {acct_label}: Daily loss limit hit! ${trigger_value:.2f}",
        AlertConditionType.EA_DISCONNECTED:       f"❌ {acct_label}: EA disconnected! No data for {trigger_value:.0f} minutes",
        AlertConditionType.EA_VERSION_CHANGED:    f"🔄 {acct_label}: EA version changed to v{trigger_value}",
        AlertConditionType.UNUSUAL_PUSH_FREQ:     f"⚠️ {acct_label}: Unusual EA push frequency detected",
        AlertConditionType.TRADE_OPENED:          f"📊 {acct_label}: New trade opened (total open: {trigger_value:.0f})",
        AlertConditionType.TRADE_CLOSED_PROFIT:   f"💚 {acct_label}: Trade closed with profit ${trigger_value:.2f}",
        AlertConditionType.TRADE_CLOSED_LOSS:     f"❤️ {acct_label}: Trade closed with loss ${trigger_value:.2f}",
        AlertConditionType.MAX_OPEN_ORDERS:       f"📈 {acct_label}: Open orders exceed limit ({trigger_value:.0f} orders)",
        AlertConditionType.POSITION_LOSS:         f"🔴 {acct_label}: Position floating loss exceeds threshold (${trigger_value:.2f})",
        AlertConditionType.MARGIN_LEVEL_LOW:      f"⚠️ {acct_label}: Margin level LOW — {trigger_value:.1f}%",
    }

    return messages.get(ctype, f"Alert: {ctype.value} — value: {trigger_value}")


# =============================================================================
# CHANNEL DELIVERY FUNCTIONS
# =============================================================================

async def send_email_alert(to_email: str, subject: str, message: str, account: Account):
    """Send HTML email alert via SMTP."""
    if not settings.SMTP_USER:
        logger.warning("SMTP not configured — skipping email alert")
        return False

    html = f"""
    <html><body style="font-family: Arial, sans-serif; background: #0a0a0a; color: #e0e0e0; padding: 20px;">
      <div style="max-width: 600px; margin: 0 auto; background: #141414; border-radius: 12px; padding: 30px; border: 1px solid #2a2a2a;">
        <h2 style="color: #00d4aa; margin-bottom: 5px;">Trading Dashboard Alert</h2>
        <p style="color: #999; margin-top: 0; font-size: 13px;">{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}</p>
        <hr style="border-color: #2a2a2a; margin: 20px 0;">
        <h3 style="color: #fff;">{message}</h3>
        <div style="background: #1a1a1a; border-radius: 8px; padding: 15px; margin: 20px 0;">
          <p style="margin:0; color: #999; font-size: 13px;">Account: <span style="color: #fff;">{account.label or account.account_number}</span></p>
          <p style="margin:5px 0 0 0; color: #999; font-size: 13px;">Broker: <span style="color: #fff;">{account.broker_name or account.broker_server}</span></p>
          <p style="margin:5px 0 0 0; color: #999; font-size: 13px;">Balance: <span style="color: #00d4aa;">${account.balance:,.2f}</span></p>
          <p style="margin:5px 0 0 0; color: #999; font-size: 13px;">Equity: <span style="color: #00d4aa;">${account.equity:,.2f}</span></p>
        </div>
        <p style="color: #666; font-size: 12px;">Sent by MT4/MT5 Trading Dashboard</p>
      </div>
    </body></html>
    """

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = settings.SMTP_FROM
        msg["To"]      = to_email
        msg.attach(MIMEText(html, "html"))

        context = ssl.create_default_context()
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
            server.starttls(context=context)
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.sendmail(settings.SMTP_FROM, to_email, msg.as_string())
        return True
    except Exception as e:
        logger.error(f"Email delivery failed: {e}")
        return False


async def send_line_notify(token: str, message: str) -> bool:
    """Send LINE Notify message."""
    if not token:
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://notify-api.line.me/api/notify",
                headers={"Authorization": f"Bearer {token}"},
                data={"message": f"\n{message}"},
            )
            return resp.status_code == 200
    except Exception as e:
        logger.error(f"LINE Notify failed: {e}")
        return False


async def send_telegram(chat_id: str, message: str) -> bool:
    """Send Telegram message via Bot API."""
    if not settings.TELEGRAM_BOT_TOKEN or not chat_id:
        return False
    try:
        url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json={
                "chat_id":    chat_id,
                "text":       message,
                "parse_mode": "HTML",
            })
            return resp.status_code == 200
    except Exception as e:
        logger.error(f"Telegram delivery failed: {e}")
        return False


async def send_sms(to_number: str, message: str) -> bool:
    """Send SMS via Twilio."""
    if not settings.TWILIO_ACCOUNT_SID:
        return False
    try:
        async with httpx.AsyncClient(timeout=10, auth=(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)) as client:
            resp = await client.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{settings.TWILIO_ACCOUNT_SID}/Messages.json",
                data={"From": settings.TWILIO_FROM_NUMBER, "To": to_number, "Body": message[:160]},
            )
            return resp.status_code in (200, 201)
    except Exception as e:
        logger.error(f"SMS delivery failed: {e}")
        return False


async def send_discord_webhook(webhook_url: str, message: str, account: Account) -> bool:
    """Send Discord webhook message."""
    if not webhook_url:
        return False
    try:
        embed = {
            "title":       "Trading Dashboard Alert",
            "description": message,
            "color":       0x00d4aa,
            "fields": [
                {"name": "Account", "value": account.label or account.account_number, "inline": True},
                {"name": "Balance", "value": f"${account.balance:,.2f}", "inline": True},
                {"name": "Equity",  "value": f"${account.equity:,.2f}",  "inline": True},
            ],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(webhook_url, json={"embeds": [embed]})
            return resp.status_code in (200, 204)
    except Exception as e:
        logger.error(f"Discord webhook failed: {e}")
        return False


# =============================================================================
# RULE EVALUATION ENGINE
# =============================================================================

def is_in_quiet_hours(user: User) -> bool:
    """Check if current server time is within user's quiet hours."""
    if user.quiet_hours_start is None or user.quiet_hours_end is None:
        return False
    current_hour = datetime.now(timezone.utc).hour
    start = user.quiet_hours_start
    end   = user.quiet_hours_end
    if start <= end:
        return start <= current_hour < end
    else:  # wraps midnight
        return current_hour >= start or current_hour < end


def is_cooldown_active(rule: AlertRule) -> bool:
    """Check if alert is in cooldown period."""
    if not rule.last_triggered:
        return False
    elapsed = datetime.now(timezone.utc) - rule.last_triggered.replace(tzinfo=timezone.utc)
    return elapsed < timedelta(minutes=rule.cooldown_min)


def evaluate_rule(rule: AlertRule, account: Account) -> Optional[float]:
    """
    Evaluate an alert rule against current account state.
    Returns trigger_value if condition met, else None.
    """
    ctype = rule.condition_type
    thresh = rule.threshold_value or 0

    # ── Profit Alerts ─────────────────────────────────────────────────────────
    if ctype == AlertConditionType.PROFIT_DAILY_TARGET:
        val = account.profit_today
        if rule.threshold_unit == "pct":
            today_pct = (account.profit_today / account.balance * 100) if account.balance else 0
            return today_pct if today_pct >= thresh else None
        return val if val >= thresh else None

    if ctype == AlertConditionType.NEW_EQUITY_HIGH:
        if account.equity > (account.peak_equity or 0):
            return account.equity

    # ── Drawdown Alerts ───────────────────────────────────────────────────────
    if ctype in (AlertConditionType.DRAWDOWN_WARNING, AlertConditionType.DRAWDOWN_CRITICAL):
        if account.peak_equity and account.peak_equity > 0:
            current_dd_pct = abs((account.equity - account.peak_equity) / account.peak_equity * 100)
            return current_dd_pct if current_dd_pct >= thresh else None

    if ctype == AlertConditionType.DAILY_LOSS_LIMIT:
        val = abs(account.profit_today)
        return val if account.profit_today < 0 and val >= thresh else None

    # ── Connection Alerts ─────────────────────────────────────────────────────
    if ctype == AlertConditionType.EA_DISCONNECTED:
        if account.last_push_at:
            elapsed_min = (datetime.now(timezone.utc) - account.last_push_at.replace(tzinfo=timezone.utc)).seconds / 60
            return elapsed_min if elapsed_min >= thresh else None

    # ── Trade Alerts ──────────────────────────────────────────────────────────
    if ctype == AlertConditionType.MAX_OPEN_ORDERS:
        return account.open_orders_count if account.open_orders_count >= thresh else None

    if ctype == AlertConditionType.MARGIN_LEVEL_LOW:
        if account.margin_level > 0:
            return account.margin_level if account.margin_level <= thresh else None

    return None


async def fire_alert(
    db: Session,
    rule: AlertRule,
    account: Account,
    trigger_value: float,
    message: str,
):
    """Deliver alert across all configured channels and log results."""
    user = db.query(User).filter(User.id == rule.user_id).first()
    if not user:
        return

    channels = rule.channels or [AlertChannel.IN_APP]

    # Check quiet hours (skip unless override set)
    if is_in_quiet_hours(user) and not rule.quiet_hours_override:
        logger.info(f"Alert {rule.id} suppressed — quiet hours")
        return

    for channel in channels:
        success = False
        try:
            if channel == AlertChannel.IN_APP:
                notif = Notification(
                    user_id=rule.user_id,
                    title="Trading Alert",
                    message=message,
                    category="alert",
                    link=f"/account/{account.id}",
                )
                db.add(notif)
                db.commit()
                success = True

            elif channel == AlertChannel.EMAIL:
                success = await send_email_alert(
                    user.email, f"Alert: {account.label or account.account_number}", message, account
                )

            elif channel == AlertChannel.LINE:
                token = rule.user_id and user.line_notify_token or settings.LINE_NOTIFY_TOKEN
                success = await send_line_notify(token, message)

            elif channel == AlertChannel.TELEGRAM:
                success = await send_telegram(user.telegram_chat_id or "", message)

            elif channel == AlertChannel.SMS:
                # Assume user's email prefix is phone in simple setup
                # In production, add a phone_number field to User
                success = False  # placeholder until phone field added

        except Exception as e:
            logger.error(f"Channel {channel} delivery error: {e}")

        # Log delivery attempt
        history = AlertHistory(
            rule_id=rule.id,
            account_id=account.id,
            trigger_value=trigger_value,
            message=message,
            channel=channel,
            status=AlertStatus.DELIVERED if success else AlertStatus.FAILED,
        )
        db.add(history)

    # Update rule metadata
    rule.last_triggered = datetime.now(timezone.utc)
    rule.trigger_count  = (rule.trigger_count or 0) + 1
    db.commit()


# =============================================================================
# MAIN ALERT CHECK — called by scheduler every 30 seconds
# =============================================================================

async def check_all_alerts():
    """Evaluate all active alert rules against current account states."""
    with get_db_context() as db:
        rules = db.query(AlertRule).filter(AlertRule.is_active == True).all()

        for rule in rules:
            try:
                # Skip if in cooldown
                if is_cooldown_active(rule):
                    continue

                # Get relevant account(s)
                if rule.account_id:
                    accounts = db.query(Account).filter(Account.id == rule.account_id).all()
                else:
                    # All accounts owned by user
                    accounts = db.query(Account).filter(Account.owner_id == rule.user_id).all()

                for account in accounts:
                    trigger_val = evaluate_rule(rule, account)
                    if trigger_val is not None:
                        message = build_alert_message(rule, account, trigger_val)
                        await fire_alert(db, rule, account, trigger_val, message)

            except Exception as e:
                logger.error(f"Error processing alert rule {rule.id}: {e}")
