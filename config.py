# =============================================================================
# config.py — Application Settings (Pydantic v2)
# =============================================================================
from pydantic_settings import BaseSettings
from typing import List
import secrets


class Settings(BaseSettings):
    APP_NAME:    str  = "MT4/MT5 Trading Dashboard"
    APP_VERSION: str  = "1.0.0"
    DEBUG:       bool = False
    SECRET_KEY:  str  = secrets.token_urlsafe(32)
    ALGORITHM:   str  = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS:   int = 30

    DATABASE_URL: str = "postgresql://localhost/trading_dashboard"

    ALLOWED_ORIGINS: str = "http://localhost:8000"

    @property
    def cors_origins(self) -> List[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]

    SMTP_HOST:     str = "smtp.gmail.com"
    SMTP_PORT:     int = 587
    SMTP_USER:     str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM:     str = "Trading Dashboard <noreply@example.com>"

    LINE_NOTIFY_TOKEN:  str = ""
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID:   str = ""

    TWILIO_ACCOUNT_SID:  str = ""
    TWILIO_AUTH_TOKEN:   str = ""
    TWILIO_FROM_NUMBER:  str = ""

    FRONTEND_URL:    str   = "http://localhost:8000"
    RISK_FREE_RATE:  float = 0.0
    TICK_DATA_RETENTION_DAYS:       int = 90
    DEFAULT_ALERT_COOLDOWN_MINUTES: int = 15

    model_config = {
        "env_file": ".env",
        "case_sensitive": True,
        "extra": "ignore",
    }


settings = Settings()
