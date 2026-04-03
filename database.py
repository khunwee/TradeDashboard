# =============================================================================
# database.py — Database Connection (SQLite + PostgreSQL compatible)
# =============================================================================
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session, declarative_base
from contextlib import contextmanager
from typing import Generator
import logging
import os

logger = logging.getLogger(__name__)

# ── Get DATABASE_URL — prioritise environment variable, fallback to SQLite ────
def _get_db_url() -> str:
    # First try direct environment variable
    url = os.environ.get("DATABASE_URL", "")
    
    # If not set or still has placeholder values, use SQLite
    if (not url or 
        "user:password" in url or 
        "localhost/trading_dashboard" in url and "sqlite" not in url):
        
        # Try to load from .env file
        try:
            from dotenv import load_dotenv
            load_dotenv(override=True)
            url = os.environ.get("DATABASE_URL", "")
        except Exception:
            pass
    
    # Final fallback — always use SQLite if PostgreSQL is not reachable
    if not url or "user:password" in url:
        url = "sqlite:///./trading_dashboard.db"
        logger.info("Using SQLite database (fallback)")
    
    # If PostgreSQL URL but no server running, switch to SQLite
    if "postgresql" in url or "postgres://" in url:
        # Test if we can reach it — if not, fall back to SQLite
        try:
            import socket
            # Extract host and port from URL
            # postgresql://user:pass@host:port/db
            parts = url.replace("postgresql://", "").replace("postgres://", "")
            if "@" in parts:
                hostpart = parts.split("@")[1].split("/")[0]
                host = hostpart.split(":")[0]
                port = int(hostpart.split(":")[1]) if ":" in hostpart else 5432
                sock = socket.create_connection((host, port), timeout=2)
                sock.close()
                logger.info(f"PostgreSQL reachable at {host}:{port}")
            else:
                raise Exception("Cannot parse URL")
        except Exception as e:
            logger.warning(f"PostgreSQL not reachable ({e}) — switching to SQLite")
            url = "sqlite:///./trading_dashboard.db"
    
    logger.info(f"Database: {url[:50]}...")
    return url


DATABASE_URL = _get_db_url()
IS_SQLITE = "sqlite" in DATABASE_URL

# ── Engine ────────────────────────────────────────────────────────────────────
if IS_SQLITE:
    from sqlalchemy.pool import StaticPool
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )
else:
    from sqlalchemy.pool import QueuePool
    engine = create_engine(
        DATABASE_URL,
        poolclass=QueuePool,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        pool_recycle=3600,
        echo=False,
    )

# ── Session ───────────────────────────────────────────────────────────────────
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# ── Base ──────────────────────────────────────────────────────────────────────
Base = declarative_base()


# ── FastAPI dependency ────────────────────────────────────────────────────────
def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ── Background job context manager ───────────────────────────────────────────
@contextmanager
def get_db_context() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ── Health check ──────────────────────────────────────────────────────────────
def check_db_connection() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        return False


# ── Create tables ─────────────────────────────────────────────────────────────
def create_tables():
    from models import Base as ModelsBase
    ModelsBase.metadata.create_all(bind=engine)
    logger.info(f"Tables created/verified ({'SQLite' if IS_SQLITE else 'PostgreSQL'})")
