# =============================================================================
# models.py — All SQLAlchemy Database Models
# SQLite + PostgreSQL compatible (uses String for enum columns)
# =============================================================================
from sqlalchemy import (
    Column, String, Integer, Float, Boolean, DateTime, Text,
    ForeignKey, BigInteger, JSON, Index, UniqueConstraint
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base
import enum
import uuid


def gen_uuid() -> str:
    return str(uuid.uuid4())


# =============================================================================
# PYTHON ENUMS (used in application code only — stored as String in DB)
# =============================================================================

class UserRole(str, enum.Enum):
    SUPER_ADMIN = "super_admin"
    ADMIN       = "admin"
    TRADER      = "trader"
    VIEWER      = "viewer"
    INVESTOR    = "investor"

class AccountType(str, enum.Enum):
    LIVE    = "live"
    DEMO    = "demo"
    CONTEST = "contest"

class AccountStatus(str, enum.Enum):
    LIVE          = "live"
    DELAYED       = "delayed"
    DISCONNECTED  = "disconnected"
    MARKET_CLOSED = "market_closed"
    WARNING       = "warning"

class TradeType(str, enum.Enum):
    BUY        = "buy"
    SELL       = "sell"
    BUY_LIMIT  = "buy_limit"
    SELL_LIMIT = "sell_limit"
    BUY_STOP   = "buy_stop"
    SELL_STOP  = "sell_stop"

class AlertConditionType(str, enum.Enum):
    PROFIT_DAILY_TARGET   = "profit_daily_target"
    PROFIT_WEEKLY_TARGET  = "profit_weekly_target"
    PROFIT_MONTHLY_TARGET = "profit_monthly_target"
    NEW_EQUITY_HIGH       = "new_equity_high"
    DRAWDOWN_WARNING      = "drawdown_warning"
    DRAWDOWN_CRITICAL     = "drawdown_critical"
    DAILY_LOSS_LIMIT      = "daily_loss_limit"
    EA_DISCONNECTED       = "ea_disconnected"
    EA_VERSION_CHANGED    = "ea_version_changed"
    UNUSUAL_PUSH_FREQ     = "unusual_push_freq"
    TRADE_OPENED          = "trade_opened"
    TRADE_CLOSED_PROFIT   = "trade_closed_profit"
    TRADE_CLOSED_LOSS     = "trade_closed_loss"
    MAX_OPEN_ORDERS       = "max_open_orders"
    POSITION_LOSS         = "position_loss"
    MARGIN_LEVEL_LOW      = "margin_level_low"

class AlertChannel(str, enum.Enum):
    IN_APP   = "in_app"
    EMAIL    = "email"
    LINE     = "line"
    TELEGRAM = "telegram"
    SMS      = "sms"
    DISCORD  = "discord"

class AlertStatus(str, enum.Enum):
    PENDING   = "pending"
    DELIVERED = "delivered"
    FAILED    = "failed"
    SUPPRESSED = "suppressed"

class NotificationCategory(str, enum.Enum):
    ALERT  = "alert"
    SYSTEM = "system"
    TRADE  = "trade"
    REPORT = "report"


# =============================================================================
# USER & AUTH MODELS
# =============================================================================

class User(Base):
    __tablename__ = "users"

    id               = Column(String(36), primary_key=True, default=gen_uuid)
    email            = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password  = Column(String(255), nullable=False)
    display_name     = Column(String(100), nullable=False)
    avatar_url       = Column(String(500), nullable=True)
    role             = Column(String(20), default="trader", nullable=False)
    is_active        = Column(Boolean, default=True)
    is_verified      = Column(Boolean, default=False)

    timezone         = Column(String(50), default="UTC")
    language         = Column(String(10), default="en")
    currency_display = Column(String(10), default="USD")
    theme            = Column(String(10), default="dark")
    font_size        = Column(String(10), default="medium")
    density          = Column(String(10), default="comfortable")

    totp_secret      = Column(String(64), nullable=True)
    totp_enabled     = Column(Boolean, default=False)
    backup_codes     = Column(JSON, default=list)

    notification_config = Column(JSON, default=dict)
    quiet_hours_start   = Column(Integer, nullable=True)
    quiet_hours_end     = Column(Integer, nullable=True)
    quiet_hours_override = Column(Boolean, default=False)

    personal_api_key  = Column(String(64), nullable=True, unique=True)
    telegram_chat_id  = Column(String(50), nullable=True)
    line_notify_token = Column(String(200), nullable=True)

    created_at    = Column(DateTime(timezone=True), server_default=func.now())
    updated_at    = Column(DateTime(timezone=True), onupdate=func.now())
    last_login_at = Column(DateTime(timezone=True), nullable=True)

    accounts      = relationship("Account", back_populates="owner", foreign_keys="Account.owner_id")
    login_history = relationship("LoginHistory", back_populates="user", cascade="all, delete-orphan")
    alert_rules   = relationship("AlertRule", back_populates="user", cascade="all, delete-orphan")
    notifications = relationship("Notification", back_populates="user", cascade="all, delete-orphan")
    audit_logs    = relationship("AuditLog", back_populates="user", cascade="all, delete-orphan")
    api_keys      = relationship("ApiKey", back_populates="user", cascade="all, delete-orphan")


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"
    id         = Column(String(36), primary_key=True, default=gen_uuid)
    user_id    = Column(String(36), ForeignKey("users.id"), nullable=False)
    token_hash = Column(String(255), nullable=False, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used       = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    id         = Column(String(36), primary_key=True, default=gen_uuid)
    user_id    = Column(String(36), ForeignKey("users.id"), nullable=False)
    token_hash = Column(String(255), nullable=False, index=True)
    device_fp  = Column(String(255), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked    = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class LoginHistory(Base):
    __tablename__ = "login_history"
    id          = Column(String(36), primary_key=True, default=gen_uuid)
    user_id     = Column(String(36), ForeignKey("users.id"), nullable=False)
    ip_address  = Column(String(45), nullable=True)
    device      = Column(String(255), nullable=True)
    browser     = Column(String(255), nullable=True)
    os          = Column(String(100), nullable=True)
    success     = Column(Boolean, nullable=False)
    fail_reason = Column(String(100), nullable=True)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())
    user = relationship("User", back_populates="login_history")


class FailedLoginAttempt(Base):
    __tablename__ = "failed_login_attempts"
    id           = Column(String(36), primary_key=True, default=gen_uuid)
    identifier   = Column(String(255), nullable=False, index=True)
    attempts     = Column(Integer, default=1)
    locked_until = Column(DateTime(timezone=True), nullable=True)
    created_at   = Column(DateTime(timezone=True), server_default=func.now())
    updated_at   = Column(DateTime(timezone=True), onupdate=func.now())


class ApiKey(Base):
    __tablename__ = "api_keys"
    id         = Column(String(36), primary_key=True, default=gen_uuid)
    user_id    = Column(String(36), ForeignKey("users.id"), nullable=False)
    label      = Column(String(100), nullable=False)
    key_hash   = Column(String(255), nullable=False, unique=True)
    key_prefix = Column(String(10), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    user = relationship("User", back_populates="api_keys")


class PermissionAuditLog(Base):
    __tablename__ = "permission_audit_logs"
    id          = Column(String(36), primary_key=True, default=gen_uuid)
    granter_id  = Column(String(36), ForeignKey("users.id"), nullable=True)
    grantee_id  = Column(String(36), nullable=True)
    account_id  = Column(String(36), nullable=True)
    action      = Column(String(50), nullable=False)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())


# =============================================================================
# ACCOUNT MODELS
# =============================================================================

class AccountGroup(Base):
    __tablename__ = "account_groups"
    id         = Column(String(36), primary_key=True, default=gen_uuid)
    owner_id   = Column(String(36), ForeignKey("users.id"), nullable=False)
    name       = Column(String(100), nullable=False)
    color      = Column(String(7), default="#3B82F6")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    accounts   = relationship("Account", back_populates="group")


class Account(Base):
    __tablename__ = "accounts"

    id             = Column(String(36), primary_key=True, default=gen_uuid)
    owner_id       = Column(String(36), ForeignKey("users.id"), nullable=False)
    group_id       = Column(String(36), ForeignKey("account_groups.id"), nullable=True)
    account_number = Column(String(20), nullable=False, index=True)
    broker_server  = Column(String(100), nullable=False)
    broker_name    = Column(String(100), nullable=True)
    label          = Column(String(100), nullable=True)
    ea_name        = Column(String(100), nullable=True)
    ea_version     = Column(String(20), nullable=True)
    ea_build       = Column(String(20), nullable=True)
    account_currency = Column(String(10), default="USD")
    leverage         = Column(Integer, default=100)
    account_type     = Column(String(20), default="live")
    start_date       = Column(DateTime(timezone=True), nullable=True)
    initial_deposit  = Column(Float, default=0.0)
    initial_balance  = Column(Float, default=0.0)

    push_api_key_hash   = Column(String(255), nullable=True)
    push_api_key_prefix = Column(String(10), nullable=True)
    push_ip_whitelist   = Column(JSON, default=list)
    heartbeat_timeout_sec = Column(Integer, default=60)
    push_interval_sec     = Column(Integer, default=5)

    status       = Column(String(20), default="disconnected")
    last_push_at = Column(DateTime(timezone=True), nullable=True)
    uptime_pct   = Column(Float, default=0.0)
    is_active    = Column(Boolean, default=True)

    # Live snapshot
    balance       = Column(Float, default=0.0)
    equity        = Column(Float, default=0.0)
    margin        = Column(Float, default=0.0)
    free_margin   = Column(Float, default=0.0)
    margin_level  = Column(Float, default=0.0)
    floating_pl   = Column(Float, default=0.0)
    open_orders_count = Column(Integer, default=0)
    lots_today    = Column(Float, default=0.0)
    orders_closed_today = Column(Integer, default=0)
    max_dd_today  = Column(Float, default=0.0)

    profit_today  = Column(Float, default=0.0)
    peak_equity   = Column(Float, default=0.0)

    # Calculated stats
    max_drawdown_abs   = Column(Float, default=0.0)
    max_drawdown_pct   = Column(Float, default=0.0)
    profit_factor      = Column(Float, default=0.0)
    win_rate           = Column(Float, default=0.0)
    sharpe_ratio       = Column(Float, nullable=True)
    sortino_ratio      = Column(Float, nullable=True)
    calmar_ratio       = Column(Float, nullable=True)
    recovery_factor    = Column(Float, default=0.0)
    total_trades       = Column(Integer, default=0)
    expectancy         = Column(Float, default=0.0)
    growth_pct         = Column(Float, default=0.0)
    annualized_return  = Column(Float, nullable=True)
    avg_hold_time_min  = Column(Float, nullable=True)
    max_consecutive_wins   = Column(Integer, default=0)
    max_consecutive_losses = Column(Integer, default=0)
    current_streak     = Column(Integer, default=0)
    largest_win        = Column(Float, default=0.0)
    largest_loss       = Column(Float, default=0.0)
    avg_daily_profit   = Column(Float, default=0.0)
    max_deposit_load_pct = Column(Float, default=0.0)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    owner           = relationship("User", back_populates="accounts", foreign_keys=[owner_id])
    group           = relationship("AccountGroup", back_populates="accounts")
    open_positions  = relationship("OpenPosition", back_populates="account", cascade="all, delete-orphan")
    closed_trades   = relationship("ClosedTrade", back_populates="account", cascade="all, delete-orphan")
    snapshots       = relationship("AccountSnapshot", back_populates="account", cascade="all, delete-orphan")
    daily_stats     = relationship("DailyStats", back_populates="account", cascade="all, delete-orphan")
    ea_version_logs = relationship("EaVersionLog", back_populates="account", cascade="all, delete-orphan")
    deposits        = relationship("DepositWithdrawal", back_populates="account", cascade="all, delete-orphan")
    alert_rules     = relationship("AlertRule", back_populates="account", cascade="all, delete-orphan")
    account_permissions = relationship("AccountPermission", back_populates="account", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("owner_id", "account_number", "broker_server", name="uq_account"),
        Index("ix_account_owner", "owner_id"),
        Index("ix_account_number", "account_number"),
    )


class AccountPermission(Base):
    __tablename__ = "account_permissions"
    id         = Column(String(36), primary_key=True, default=gen_uuid)
    account_id = Column(String(36), ForeignKey("accounts.id"), nullable=False)
    user_id    = Column(String(36), ForeignKey("users.id"), nullable=False)
    can_view   = Column(Boolean, default=True)
    can_edit   = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    account    = relationship("Account", back_populates="account_permissions")


class EaVersionLog(Base):
    __tablename__ = "ea_version_logs"
    id         = Column(String(36), primary_key=True, default=gen_uuid)
    account_id = Column(String(36), ForeignKey("accounts.id"), nullable=False)
    ea_name    = Column(String(100), nullable=True)
    ea_version = Column(String(20), nullable=True)
    ea_build   = Column(String(20), nullable=True)
    logged_at  = Column(DateTime(timezone=True), server_default=func.now())
    account    = relationship("Account", back_populates="ea_version_logs")


class DepositWithdrawal(Base):
    __tablename__ = "deposits_withdrawals"
    id         = Column(String(36), primary_key=True, default=gen_uuid)
    account_id = Column(String(36), ForeignKey("accounts.id"), nullable=False)
    amount     = Column(Float, nullable=False)
    note       = Column(String(255), nullable=True)
    tx_date    = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    account    = relationship("Account", back_populates="deposits")


# =============================================================================
# TRADING DATA MODELS
# =============================================================================

class OpenPosition(Base):
    __tablename__ = "open_positions"
    id            = Column(String(36), primary_key=True, default=gen_uuid)
    account_id    = Column(String(36), ForeignKey("accounts.id"), nullable=False)
    ticket        = Column(BigInteger, nullable=False)
    symbol        = Column(String(20), nullable=False)
    trade_type    = Column(String(20), nullable=False)
    lots          = Column(Float, nullable=False)
    open_price    = Column(Float, nullable=False)
    current_price = Column(Float, default=0.0)
    stop_loss     = Column(Float, nullable=True)
    take_profit   = Column(Float, nullable=True)
    profit        = Column(Float, default=0.0)
    swap          = Column(Float, default=0.0)
    commission    = Column(Float, default=0.0)
    open_time     = Column(DateTime(timezone=True), nullable=True)
    mae           = Column(Float, nullable=True)
    mfe           = Column(Float, nullable=True)
    comment       = Column(String(100), nullable=True)
    updated_at    = Column(DateTime(timezone=True), onupdate=func.now())
    account       = relationship("Account", back_populates="open_positions")
    __table_args__ = (
        UniqueConstraint("account_id", "ticket", name="uq_open_position"),
    )


class ClosedTrade(Base):
    __tablename__ = "closed_trades"
    id           = Column(String(36), primary_key=True, default=gen_uuid)
    account_id   = Column(String(36), ForeignKey("accounts.id"), nullable=False)
    ticket       = Column(BigInteger, nullable=False)
    symbol       = Column(String(20), nullable=False)
    trade_type   = Column(String(20), nullable=False)
    lots         = Column(Float, nullable=False)
    open_price   = Column(Float, nullable=False)
    close_price  = Column(Float, nullable=False)
    stop_loss    = Column(Float, nullable=True)
    take_profit  = Column(Float, nullable=True)
    gross_profit = Column(Float, default=0.0)
    commission   = Column(Float, default=0.0)
    swap         = Column(Float, default=0.0)
    net_profit   = Column(Float, default=0.0)
    open_time    = Column(DateTime(timezone=True), nullable=True)
    close_time   = Column(DateTime(timezone=True), nullable=True)
    duration_min = Column(Float, nullable=True)
    mae          = Column(Float, nullable=True)
    mfe          = Column(Float, nullable=True)
    comment      = Column(String(100), nullable=True)
    account      = relationship("Account", back_populates="closed_trades")
    __table_args__ = (
        UniqueConstraint("account_id", "ticket", name="uq_closed_trade"),
        Index("ix_closed_trade_account", "account_id"),
        Index("ix_closed_trade_symbol", "symbol"),
        Index("ix_closed_trade_close_time", "close_time"),
    )


class AccountSnapshot(Base):
    __tablename__ = "account_snapshots"
    id            = Column(String(36), primary_key=True, default=gen_uuid)
    account_id    = Column(String(36), ForeignKey("accounts.id"), nullable=False)
    balance       = Column(Float, nullable=False)
    equity        = Column(Float, nullable=False)
    margin        = Column(Float, default=0.0)
    free_margin   = Column(Float, default=0.0)
    margin_level  = Column(Float, default=0.0)
    floating_pl   = Column(Float, default=0.0)
    open_orders   = Column(Integer, default=0)
    snapshot_time = Column(DateTime(timezone=True), server_default=func.now())
    account       = relationship("Account", back_populates="snapshots")
    __table_args__ = (
        Index("ix_snapshot_account_time", "account_id", "snapshot_time"),
    )


class DailyStats(Base):
    __tablename__ = "daily_stats"
    id            = Column(String(36), primary_key=True, default=gen_uuid)
    account_id    = Column(String(36), ForeignKey("accounts.id"), nullable=False)
    date          = Column(String(10), nullable=False)
    open_balance  = Column(Float, default=0.0)
    close_balance = Column(Float, default=0.0)
    open_equity   = Column(Float, default=0.0)
    close_equity  = Column(Float, default=0.0)
    profit        = Column(Float, default=0.0)
    profit_pct    = Column(Float, default=0.0)
    trades_count  = Column(Integer, default=0)
    lots_volume   = Column(Float, default=0.0)
    max_drawdown  = Column(Float, default=0.0)
    created_at    = Column(DateTime(timezone=True), server_default=func.now())
    account       = relationship("Account", back_populates="daily_stats")
    __table_args__ = (
        UniqueConstraint("account_id", "date", name="uq_daily_stats"),
    )


# =============================================================================
# ALERT & NOTIFICATION MODELS
# =============================================================================

class AlertRule(Base):
    __tablename__ = "alert_rules"
    id              = Column(String(36), primary_key=True, default=gen_uuid)
    user_id         = Column(String(36), ForeignKey("users.id"), nullable=False)
    account_id      = Column(String(36), ForeignKey("accounts.id"), nullable=True)
    label           = Column(String(200), nullable=False)
    condition_type  = Column(String(50), nullable=False)
    threshold_value = Column(Float, nullable=True)
    threshold_unit  = Column(String(20), default="dollar")
    channels        = Column(JSON, default=list)
    is_active       = Column(Boolean, default=True)
    cooldown_min    = Column(Integer, default=15)
    quiet_hours_override = Column(Boolean, default=False)
    last_triggered  = Column(DateTime(timezone=True), nullable=True)
    trigger_count   = Column(Integer, default=0)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())
    updated_at      = Column(DateTime(timezone=True), onupdate=func.now())
    user    = relationship("User", back_populates="alert_rules")
    account = relationship("Account", back_populates="alert_rules")


class AlertHistory(Base):
    __tablename__ = "alert_history"
    id            = Column(String(36), primary_key=True, default=gen_uuid)
    rule_id       = Column(String(36), ForeignKey("alert_rules.id"), nullable=True)
    account_id    = Column(String(36), nullable=True)
    trigger_value = Column(Float, nullable=True)
    message       = Column(Text, nullable=False)
    channel       = Column(String(20), nullable=False)
    status        = Column(String(20), default="pending")
    created_at    = Column(DateTime(timezone=True), server_default=func.now())


class Notification(Base):
    __tablename__ = "notifications"
    id         = Column(String(36), primary_key=True, default=gen_uuid)
    user_id    = Column(String(36), ForeignKey("users.id"), nullable=False)
    title      = Column(String(200), nullable=False)
    message    = Column(Text, nullable=False)
    category   = Column(String(30), default="alert")
    link       = Column(String(500), nullable=True)
    is_read    = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    user = relationship("User", back_populates="notifications")


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id          = Column(String(36), primary_key=True, default=gen_uuid)
    user_id     = Column(String(36), ForeignKey("users.id"), nullable=True)
    action      = Column(String(200), nullable=False)
    resource    = Column(String(100), nullable=True)
    resource_id = Column(String(36), nullable=True)
    details     = Column(JSON, nullable=True)
    ip_address  = Column(String(45), nullable=True)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())
    user = relationship("User", back_populates="audit_logs")
    __table_args__ = (
        Index("ix_audit_log_user", "user_id"),
        Index("ix_audit_log_action", "action"),
    )


class ScheduledReport(Base):
    __tablename__ = "scheduled_reports"
    id           = Column(String(36), primary_key=True, default=gen_uuid)
    user_id      = Column(String(36), ForeignKey("users.id"), nullable=False)
    account_ids  = Column(JSON, default=list)
    frequency    = Column(String(20), default="daily")
    send_hour    = Column(Integer, default=8)
    is_active    = Column(Boolean, default=True)
    last_sent_at = Column(DateTime(timezone=True), nullable=True)
    created_at   = Column(DateTime(timezone=True), server_default=func.now())


class InvestorShareLink(Base):
    __tablename__ = "investor_share_links"
    id         = Column(String(36), primary_key=True, default=gen_uuid)
    account_id = Column(String(36), ForeignKey("accounts.id"), nullable=False)
    token      = Column(String(64), unique=True, nullable=False)
    label      = Column(String(100), nullable=True)
    is_active  = Column(Boolean, default=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
