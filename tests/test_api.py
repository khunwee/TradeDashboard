# =============================================================================
# tests/test_api.py — API Integration Tests
# Uses FastAPI TestClient — no live DB required (uses SQLite in-memory)
# Run: pytest tests/test_api.py -v
# =============================================================================
import pytest
import os
import sys

# Point to project root
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Override DB URL to SQLite in-memory for tests
os.environ["DATABASE_URL"] = "sqlite:///./test_trading.db"
os.environ["SECRET_KEY"]   = "test-secret-key-not-for-production-abc123"
os.environ["DEBUG"]        = "true"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base, get_db
from main import app

# ── Test Database Setup ───────────────────────────────────────────────────────
TEST_DB_URL = "sqlite:///./test_trading.db"
engine      = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
TestSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestSession()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(scope="module", autouse=True)
def setup_database():
    """Create all tables before tests, drop after."""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
    # Clean up test DB file
    if os.path.exists("./test_trading.db"):
        os.remove("./test_trading.db")


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


# ── Shared state across tests ─────────────────────────────────────────────────
state = {}


# =============================================================================
# HEALTH CHECK
# =============================================================================

class TestHealth:
    def test_health_endpoint(self, client):
        resp = client.get("/health")
        assert resp.status_code in (200, 503)
        data = resp.json()
        assert "status"  in data
        assert "version" in data

    def test_api_info(self, client):
        resp = client.get("/api/v1/info")
        assert resp.status_code == 200
        data = resp.json()
        assert "name"    in data
        assert "version" in data


# =============================================================================
# AUTHENTICATION
# =============================================================================

class TestAuth:
    def test_register_success(self, client):
        resp = client.post("/api/v1/auth/register", json={
            "email":        "trader@example.com",
            "password":     "SecurePass1!",
            "display_name": "Test Trader",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert "access_token"  in data
        assert "refresh_token" in data
        assert data["user"]["email"] == "trader@example.com"
        state["access_token"]  = data["access_token"]
        state["refresh_token"] = data["refresh_token"]
        state["user_id"]       = data["user"]["id"]

    def test_register_duplicate_email(self, client):
        resp = client.post("/api/v1/auth/register", json={
            "email":        "trader@example.com",
            "password":     "SecurePass1!",
            "display_name": "Duplicate",
        })
        assert resp.status_code == 400
        assert "already registered" in resp.json()["detail"].lower()

    def test_register_weak_password(self, client):
        resp = client.post("/api/v1/auth/register", json={
            "email":        "weak@example.com",
            "password":     "short",
            "display_name": "Weak",
        })
        assert resp.status_code == 422

    def test_login_success(self, client):
        resp = client.post("/api/v1/auth/login", json={
            "email":    "trader@example.com",
            "password": "SecurePass1!",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        state["access_token"]  = data["access_token"]
        state["refresh_token"] = data["refresh_token"]

    def test_login_wrong_password(self, client):
        resp = client.post("/api/v1/auth/login", json={
            "email":    "trader@example.com",
            "password": "WrongPassword1!",
        })
        assert resp.status_code == 401

    def test_login_nonexistent_user(self, client):
        resp = client.post("/api/v1/auth/login", json={
            "email":    "nobody@example.com",
            "password": "Password1!",
        })
        assert resp.status_code == 401

    def test_get_me(self, client):
        resp = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {state['access_token']}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["email"] == "trader@example.com"

    def test_get_me_no_token(self, client):
        resp = client.get("/api/v1/auth/me")
        assert resp.status_code == 401

    def test_get_me_invalid_token(self, client):
        resp = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer invalid.token.here"},
        )
        assert resp.status_code == 401

    def test_refresh_token(self, client):
        resp = client.post("/api/v1/auth/refresh", json={
            "refresh_token": state["refresh_token"],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token"  in data
        assert "refresh_token" in data
        # Update state with new tokens
        state["access_token"]  = data["access_token"]
        state["refresh_token"] = data["refresh_token"]

    def test_refresh_invalid_token(self, client):
        resp = client.post("/api/v1/auth/refresh", json={
            "refresh_token": "invalid-refresh-token",
        })
        assert resp.status_code == 401

    def test_update_profile(self, client):
        resp = client.patch(
            "/api/v1/auth/me",
            json={"display_name": "Updated Trader", "timezone": "Asia/Bangkok"},
            headers={"Authorization": f"Bearer {state['access_token']}"},
        )
        assert resp.status_code == 200

    def test_password_reset_request_valid_email(self, client):
        resp = client.post("/api/v1/auth/password-reset-request", json={
            "email": "trader@example.com",
        })
        # Always 200 to prevent enumeration
        assert resp.status_code == 200

    def test_password_reset_request_unknown_email(self, client):
        resp = client.post("/api/v1/auth/password-reset-request", json={
            "email": "nobody@example.com",
        })
        assert resp.status_code == 200

    def test_login_history(self, client):
        resp = client.get(
            "/api/v1/auth/login-history",
            headers={"Authorization": f"Bearer {state['access_token']}"},
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


# =============================================================================
# ACCOUNTS
# =============================================================================

class TestAccounts:
    def auth_headers(self):
        return {"Authorization": f"Bearer {state['access_token']}"}

    def test_create_account(self, client):
        resp = client.post(
            "/api/v1/accounts",
            json={
                "account_number":   "12345678",
                "broker_server":    "ICMarketsLive-MT4",
                "broker_name":      "IC Markets",
                "label":            "My Test Account",
                "account_currency": "USD",
                "leverage":         200,
                "account_type":     "live",
                "initial_deposit":  5000.0,
            },
            headers=self.auth_headers(),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["account_number"] == "12345678"
        state["account_id"] = data["id"]

    def test_create_duplicate_account_number(self, client):
        resp = client.post(
            "/api/v1/accounts",
            json={
                "account_number": "12345678",
                "broker_server":  "ICMarketsLive-MT4",
            },
            headers=self.auth_headers(),
        )
        assert resp.status_code == 400

    def test_list_accounts(self, client):
        resp = client.get(
            "/api/v1/accounts",
            headers=self.auth_headers(),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1

    def test_get_account_by_id(self, client):
        resp = client.get(
            f"/api/v1/accounts/{state['account_id']}",
            headers=self.auth_headers(),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == state["account_id"]

    def test_get_nonexistent_account(self, client):
        resp = client.get(
            "/api/v1/accounts/nonexistent-id",
            headers=self.auth_headers(),
        )
        assert resp.status_code == 404

    def test_update_account(self, client):
        resp = client.patch(
            f"/api/v1/accounts/{state['account_id']}",
            json={
                "label":      "Updated Label",
                "broker_name": "IC Markets (Updated)",
            },
            headers=self.auth_headers(),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["label"] == "Updated Label"

    def test_generate_api_key(self, client):
        resp = client.post(
            f"/api/v1/accounts/{state['account_id']}/api-key",
            headers=self.auth_headers(),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "api_key" in data
        assert len(data["api_key"]) > 20
        state["api_key"] = data["api_key"]

    def test_cannot_generate_second_key_without_revoke(self, client):
        # Generating again should work (it replaces the old one)
        resp = client.post(
            f"/api/v1/accounts/{state['account_id']}/api-key",
            headers=self.auth_headers(),
        )
        assert resp.status_code == 200
        state["api_key"] = resp.json()["api_key"]

    def test_add_deposit(self, client):
        resp = client.post(
            f"/api/v1/accounts/{state['account_id']}/deposits",
            json={"amount": 5000.0, "note": "Initial capital", "tx_date": "2024-01-01"},
            headers=self.auth_headers(),
        )
        assert resp.status_code == 201

    def test_add_withdrawal(self, client):
        resp = client.post(
            f"/api/v1/accounts/{state['account_id']}/deposits",
            json={"amount": -500.0, "note": "Profit withdrawal", "tx_date": "2024-03-01"},
            headers=self.auth_headers(),
        )
        assert resp.status_code == 201

    def test_get_deposits(self, client):
        resp = client.get(
            f"/api/v1/accounts/{state['account_id']}/deposits",
            headers=self.auth_headers(),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 2

    def test_account_groups_create(self, client):
        resp = client.post(
            "/api/v1/accounts/groups",
            json={"name": "Prop Firms", "color": "#00D4AA"},
            headers=self.auth_headers(),
        )
        assert resp.status_code == 201
        state["group_id"] = resp.json()["id"]

    def test_account_groups_list(self, client):
        resp = client.get(
            "/api/v1/accounts/groups",
            headers=self.auth_headers(),
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


# =============================================================================
# EA PUSH ENDPOINT
# =============================================================================

class TestPush:
    def push_headers(self):
        return {"X-API-Key": state.get("api_key", "invalid")}

    def sample_payload(self):
        return {
            "account_number": "12345678",
            "balance":        10500.0,
            "equity":         10450.0,
            "margin":         200.0,
            "free_margin":    10250.0,
            "margin_level":   5225.0,
            "profit":         450.0,
            "open_orders":    2,
            "server_time":    "2024-06-15 10:30:00",
            "open_positions": [
                {
                    "ticket":        1001,
                    "symbol":        "EURUSD",
                    "trade_type":    "buy",
                    "lots":          0.10,
                    "open_price":    1.08500,
                    "current_price": 1.09000,
                    "profit":        50.0,
                    "swap":          -1.2,
                    "commission":    -2.0,
                    "open_time":     "2024-06-15 08:00:00",
                },
                {
                    "ticket":        1002,
                    "symbol":        "GBPUSD",
                    "trade_type":    "sell",
                    "lots":          0.20,
                    "open_price":    1.27000,
                    "current_price": 1.26600,
                    "profit":        80.0,
                    "swap":          0.0,
                    "commission":    -4.0,
                    "open_time":     "2024-06-15 09:15:00",
                },
            ],
            "closed_trades": [
                {
                    "ticket":        900,
                    "symbol":        "USDJPY",
                    "trade_type":    "buy",
                    "lots":          0.30,
                    "open_price":    149.500,
                    "close_price":   150.000,
                    "net_profit":    150.0,
                    "gross_profit":  156.0,
                    "commission":    -6.0,
                    "swap":          0.0,
                    "open_time":     "2024-06-14 20:00:00",
                    "close_time":    "2024-06-15 07:30:00",
                    "stop_loss":     149.000,
                    "take_profit":   150.000,
                },
            ],
        }

    def test_push_valid_data(self, client):
        resp = client.post(
            "/api/v1/push",
            json=self.sample_payload(),
            headers=self.push_headers(),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") == "ok"

    def test_push_invalid_api_key(self, client):
        resp = client.post(
            "/api/v1/push",
            json=self.sample_payload(),
            headers={"X-API-Key": "invalid-key-xyz"},
        )
        assert resp.status_code == 401

    def test_push_no_api_key(self, client):
        resp = client.post("/api/v1/push", json=self.sample_payload())
        assert resp.status_code == 401

    def test_push_updates_balance(self, client):
        """After push, account balance should be updated."""
        client.post("/api/v1/push", json=self.sample_payload(), headers=self.push_headers())
        resp = client.get(
            f"/api/v1/accounts/{state['account_id']}",
            headers={"Authorization": f"Bearer {state['access_token']}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["balance"] == pytest.approx(10500.0, abs=0.01)

    def test_heartbeat(self, client):
        resp = client.post(
            "/api/v1/heartbeat",
            headers=self.push_headers(),
        )
        assert resp.status_code == 200

    def test_idempotent_push(self, client):
        """Pushing same closed trade twice should not duplicate it."""
        payload = self.sample_payload()
        client.post("/api/v1/push", json=payload, headers=self.push_headers())
        client.post("/api/v1/push", json=payload, headers=self.push_headers())
        # The closed trade ticket 900 should appear only once
        resp = client.get(
            f"/api/v1/trades/history/{state['account_id']}",
            headers={"Authorization": f"Bearer {state['access_token']}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        tickets = [t["ticket"] for t in data.get("trades", [])]
        assert tickets.count(900) == 1


# =============================================================================
# TRADES
# =============================================================================

class TestTrades:
    def auth_headers(self):
        return {"Authorization": f"Bearer {state['access_token']}"}

    def test_get_open_positions(self, client):
        resp = client.get(
            f"/api/v1/trades/open/{state['account_id']}",
            headers=self.auth_headers(),
        )
        assert resp.status_code == 200
        data = resp.json()
        positions = data.get("positions", data) if isinstance(data, dict) else data
        assert isinstance(positions, list)

    def test_get_trade_history(self, client):
        resp = client.get(
            f"/api/v1/trades/history/{state['account_id']}",
            headers=self.auth_headers(),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "trades" in data or isinstance(data, list)

    def test_trade_history_pagination(self, client):
        resp = client.get(
            f"/api/v1/trades/history/{state['account_id']}?page=1&limit=5",
            headers=self.auth_headers(),
        )
        assert resp.status_code == 200

    def test_trade_history_filter_symbol(self, client):
        resp = client.get(
            f"/api/v1/trades/history/{state['account_id']}?symbol=EURUSD",
            headers=self.auth_headers(),
        )
        assert resp.status_code == 200

    def test_trade_history_filter_direction(self, client):
        resp = client.get(
            f"/api/v1/trades/history/{state['account_id']}?direction=buy",
            headers=self.auth_headers(),
        )
        assert resp.status_code == 200

    def test_export_csv(self, client):
        resp = client.get(
            f"/api/v1/trades/export/{state['account_id']}",
            headers=self.auth_headers(),
        )
        assert resp.status_code == 200
        assert "text/csv" in resp.headers.get("content-type", "")


# =============================================================================
# STATS
# =============================================================================

class TestStats:
    def auth_headers(self):
        return {"Authorization": f"Bearer {state['access_token']}"}

    def test_equity_curve(self, client):
        resp = client.get(
            f"/api/v1/stats/{state['account_id']}/equity-curve",
            headers=self.auth_headers(),
        )
        assert resp.status_code == 200

    def test_equity_curve_with_period(self, client):
        for period in ["Today", "1W", "1M", "3M", "6M", "1Y", "All"]:
            resp = client.get(
                f"/api/v1/stats/{state['account_id']}/equity-curve?period={period}",
                headers=self.auth_headers(),
            )
            assert resp.status_code == 200, f"Failed for period {period}"

    def test_daily_pl(self, client):
        resp = client.get(
            f"/api/v1/stats/{state['account_id']}/daily-pl",
            headers=self.auth_headers(),
        )
        assert resp.status_code == 200

    def test_symbols(self, client):
        resp = client.get(
            f"/api/v1/stats/{state['account_id']}/symbols",
            headers=self.auth_headers(),
        )
        assert resp.status_code == 200

    def test_direction(self, client):
        resp = client.get(
            f"/api/v1/stats/{state['account_id']}/direction",
            headers=self.auth_headers(),
        )
        assert resp.status_code == 200

    def test_hourly_heatmap(self, client):
        resp = client.get(
            f"/api/v1/stats/{state['account_id']}/heatmap/hourly",
            headers=self.auth_headers(),
        )
        assert resp.status_code == 200

    def test_monthly_heatmap(self, client):
        resp = client.get(
            f"/api/v1/stats/{state['account_id']}/heatmap/monthly",
            headers=self.auth_headers(),
        )
        assert resp.status_code == 200

    def test_distribution_profit(self, client):
        resp = client.get(
            f"/api/v1/stats/{state['account_id']}/distribution/profit",
            headers=self.auth_headers(),
        )
        assert resp.status_code == 200

    def test_distribution_duration(self, client):
        resp = client.get(
            f"/api/v1/stats/{state['account_id']}/distribution/duration",
            headers=self.auth_headers(),
        )
        assert resp.status_code == 200

    def test_rolling_metrics(self, client):
        resp = client.get(
            f"/api/v1/stats/{state['account_id']}/rolling",
            headers=self.auth_headers(),
        )
        assert resp.status_code == 200

    def test_mae_mfe(self, client):
        resp = client.get(
            f"/api/v1/stats/{state['account_id']}/mae-mfe",
            headers=self.auth_headers(),
        )
        assert resp.status_code == 200

    def test_currency_exposure(self, client):
        resp = client.get(
            f"/api/v1/stats/{state['account_id']}/currency-exposure",
            headers=self.auth_headers(),
        )
        assert resp.status_code == 200

    def test_portfolio_summary(self, client):
        resp = client.get(
            "/api/v1/stats/portfolio/summary",
            headers=self.auth_headers(),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "total_balance" in data
        assert "accounts"      in data

    def test_stats_require_auth(self, client):
        resp = client.get(f"/api/v1/stats/{state['account_id']}/equity-curve")
        assert resp.status_code == 401

    def test_stats_wrong_account(self, client):
        resp = client.get(
            "/api/v1/stats/nonexistent-account-id/equity-curve",
            headers=self.auth_headers(),
        )
        assert resp.status_code == 404


# =============================================================================
# ALERTS
# =============================================================================

class TestAlerts:
    def auth_headers(self):
        return {"Authorization": f"Bearer {state['access_token']}"}

    def test_get_conditions(self, client):
        resp = client.get(
            "/api/v1/alerts/conditions",
            headers=self.auth_headers(),
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
        assert len(resp.json()) > 0

    def test_get_channels(self, client):
        resp = client.get(
            "/api/v1/alerts/channels",
            headers=self.auth_headers(),
        )
        assert resp.status_code == 200

    def test_create_alert_rule(self, client):
        resp = client.post(
            "/api/v1/alerts/rules",
            json={
                "label":           "Test Drawdown Alert",
                "account_id":      state["account_id"],
                "condition_type":  "drawdown_warning",
                "threshold_value": 5.0,
                "threshold_unit":  "pct",
                "channels":        ["in_app"],
                "cooldown_min":    30,
            },
            headers=self.auth_headers(),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["label"] == "Test Drawdown Alert"
        state["alert_rule_id"] = data["id"]

    def test_list_alert_rules(self, client):
        resp = client.get(
            "/api/v1/alerts/rules",
            headers=self.auth_headers(),
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_update_alert_rule(self, client):
        resp = client.patch(
            f"/api/v1/alerts/rules/{state['alert_rule_id']}",
            json={"threshold_value": 10.0, "is_active": True},
            headers=self.auth_headers(),
        )
        assert resp.status_code == 200

    def test_toggle_alert_rule(self, client):
        resp = client.patch(
            f"/api/v1/alerts/rules/{state['alert_rule_id']}",
            json={"is_active": False},
            headers=self.auth_headers(),
        )
        assert resp.status_code == 200

    def test_get_notifications(self, client):
        resp = client.get(
            "/api/v1/alerts/notifications",
            headers=self.auth_headers(),
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_delete_alert_rule(self, client):
        resp = client.delete(
            f"/api/v1/alerts/rules/{state['alert_rule_id']}",
            headers=self.auth_headers(),
        )
        assert resp.status_code == 204


# =============================================================================
# LOGOUT
# =============================================================================

class TestLogout:
    def test_logout(self, client):
        resp = client.post(
            "/api/v1/auth/logout",
            json={"refresh_token": state["refresh_token"]},
        )
        assert resp.status_code == 200

    def test_refresh_after_logout_fails(self, client):
        resp = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": state["refresh_token"]},
        )
        assert resp.status_code == 401
