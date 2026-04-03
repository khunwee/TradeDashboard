# =============================================================================
# main.py — FastAPI Application Entry Point
# WebSocket Manager, Router Registration, CORS, Health, Startup/Shutdown
# =============================================================================
import logging
import asyncio
import json
import os
from contextlib import asynccontextmanager
from typing import Dict, Set, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.openapi.docs import get_swagger_ui_html
import uvicorn

from config import settings
from database import check_db_connection, create_tables
from auth import decode_access_token

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# =============================================================================
# WEBSOCKET MANAGER
# =============================================================================

class WebSocketManager:
    def __init__(self):
        self._account_sockets: Dict[str, Set[WebSocket]] = {}
        self._user_sockets:    Dict[str, Set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket,
                      account_id: Optional[str] = None,
                      user_id: Optional[str] = None):
        await websocket.accept()
        async with self._lock:
            if account_id:
                self._account_sockets.setdefault(account_id, set()).add(websocket)
            if user_id:
                self._user_sockets.setdefault(user_id, set()).add(websocket)

    async def disconnect(self, websocket: WebSocket,
                         account_id: Optional[str] = None,
                         user_id: Optional[str] = None):
        async with self._lock:
            if account_id and account_id in self._account_sockets:
                self._account_sockets[account_id].discard(websocket)
                if not self._account_sockets[account_id]:
                    del self._account_sockets[account_id]
            if user_id and user_id in self._user_sockets:
                self._user_sockets[user_id].discard(websocket)
                if not self._user_sockets[user_id]:
                    del self._user_sockets[user_id]

    async def broadcast_to_account(self, account_id: str, data: dict):
        message = json.dumps(data)
        dead: Set[WebSocket] = set()
        for ws in self._account_sockets.get(account_id, set()).copy():
            try:
                await ws.send_text(message)
            except Exception:
                dead.add(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._account_sockets.get(account_id, set()).discard(ws)

    async def broadcast_to_user(self, user_id: str, data: dict):
        message = json.dumps(data)
        dead: Set[WebSocket] = set()
        for ws in self._user_sockets.get(user_id, set()).copy():
            try:
                await ws.send_text(message)
            except Exception:
                dead.add(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._user_sockets.get(user_id, set()).discard(ws)

    @property
    def total_connections(self) -> int:
        return (sum(len(s) for s in self._account_sockets.values()) +
                sum(len(s) for s in self._user_sockets.values()))


ws_manager = WebSocketManager()


# =============================================================================
# APP LIFESPAN
# =============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=" * 60)
    logger.info(f"  {settings.APP_NAME} v{settings.APP_VERSION}")
    logger.info("=" * 60)

    if check_db_connection():
        logger.info("✅ Database connected")
        create_tables()
        logger.info("✅ Tables created / verified")
    else:
        logger.error("❌ Database connection FAILED — check DATABASE_URL in .env")

    scheduler = None
    try:
        from scheduler import setup_scheduler
        scheduler = setup_scheduler()
        scheduler.start()
        logger.info("✅ Background scheduler started")
    except Exception as e:
        logger.warning(f"⚠️  Scheduler failed to start: {e}")

    yield

    if scheduler:
        try:
            scheduler.shutdown(wait=False)
        except Exception:
            pass
    logger.info("Application shutdown complete")


# =============================================================================
# FASTAPI APP
# =============================================================================

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Professional MT4/MT5 Trading Dashboard API",
    docs_url=None,
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =============================================================================
# ROUTERS
# =============================================================================

from routers.auth_router   import router as auth_router
from routers.accounts      import router as accounts_router
from routers.stats         import router as stats_router
from routers.trades        import router as trades_router
from routers.alerts_router import router as alerts_router
from routers.push          import router as push_router
from routers.admin         import router as admin_router

app.include_router(auth_router)
app.include_router(accounts_router)
app.include_router(stats_router)
app.include_router(trades_router)
app.include_router(alerts_router)
app.include_router(push_router)
app.include_router(admin_router)


# =============================================================================
# WEBSOCKET ENDPOINTS
# =============================================================================

@app.websocket("/ws/account/{account_id}")
async def ws_account(websocket: WebSocket, account_id: str, token: str = Query(...)):
    payload = decode_access_token(token)
    if not payload:
        await websocket.close(code=4001, reason="Unauthorized")
        return
    user_id = payload.get("sub")
    await ws_manager.connect(websocket, account_id=account_id, user_id=user_id)
    try:
        await websocket.send_json({"type": "connected", "account_id": account_id})
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug(f"WS account error: {e}")
    finally:
        await ws_manager.disconnect(websocket, account_id=account_id, user_id=user_id)


@app.websocket("/ws/portfolio")
async def ws_portfolio(websocket: WebSocket, token: str = Query(...)):
    payload = decode_access_token(token)
    if not payload:
        await websocket.close(code=4001, reason="Unauthorized")
        return
    user_id = payload.get("sub")
    await ws_manager.connect(websocket, user_id=user_id)
    try:
        await websocket.send_json({"type": "connected", "view": "portfolio"})
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        pass
    finally:
        await ws_manager.disconnect(websocket, user_id=user_id)


# =============================================================================
# HEALTH & SYSTEM ENDPOINTS
# =============================================================================

@app.get("/health", tags=["System"])
async def health():
    db_ok = check_db_connection()
    return JSONResponse(
        status_code=200 if db_ok else 503,
        content={
            "status":         "healthy" if db_ok else "degraded",
            "database":       "connected" if db_ok else "disconnected",
            "version":        settings.APP_VERSION,
            "ws_connections": ws_manager.total_connections,
        }
    )


@app.get("/api/v1/info", tags=["System"])
async def api_info():
    return {"name": settings.APP_NAME, "version": settings.APP_VERSION, "docs": "/api/docs"}


@app.get("/api/docs", include_in_schema=False)
async def custom_docs():
    return get_swagger_ui_html(
        openapi_url="/api/openapi.json",
        title=f"{settings.APP_NAME} — API Docs",
    )


# =============================================================================
# STATIC FILES & SPA FALLBACK
# =============================================================================

static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str):
        if full_path.startswith(("api/", "ws/")):
            raise HTTPException(status_code=404)
        index = os.path.join(static_dir, "index.html")
        if os.path.isfile(index):
            return FileResponse(index)
        raise HTTPException(status_code=404)


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.DEBUG,
        log_level="debug" if settings.DEBUG else "info",
        ws_ping_interval=20,
        ws_ping_timeout=10,
    )
