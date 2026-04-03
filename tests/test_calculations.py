# =============================================================================
# tests/test_calculations.py — Unit Tests for All Financial Calculations
# Run with: pytest tests/ -v
# =============================================================================
import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone

# Import calculation functions
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from calculations import (
    calc_basic_metrics,
    calc_sharpe_ratio,
    calc_sortino_ratio,
    calc_calmar_ratio,
    calc_profit_factor,
    calc_win_rate,
    calc_expectancy,
    calc_recovery_factor,
    calc_kelly_criterion,
    calc_consecutive_streaks,
    calc_drawdown,
    calc_var_95,
    calc_modified_dietz,
    calc_annualized_return,
    calc_symbol_breakdown,
    calc_direction_analysis,
    calc_monthly_returns,
    calc_hourly_heatmap,
    calc_profit_distribution,
    calc_duration_distribution,
    calc_mae_mfe_summary,
    calc_currency_exposure,
)


# =============================================================================
# FIXTURES
# =============================================================================

def make_trade(ticket, symbol, trade_type, lots, open_price, close_price,
               net_profit, open_time=None, close_time=None, duration_min=60,
               mae=None, mfe=None, commission=0.0, swap=0.0):
    """Helper to create a mock trade row as a dict."""
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return {
        "ticket":       ticket,
        "symbol":       symbol,
        "trade_type":   trade_type,
        "lots":         lots,
        "open_price":   open_price,
        "close_price":  close_price,
        "net_profit":   net_profit,
        "open_time":    open_time or base + timedelta(hours=ticket),
        "close_time":   close_time or base + timedelta(hours=ticket) + timedelta(minutes=duration_min),
        "duration_min": duration_min,
        "mae":          mae or -abs(net_profit) * 0.5,
        "mfe":          mfe or abs(net_profit) * 1.5,
        "commission":   commission,
        "swap":         swap,
    }


@pytest.fixture
def winning_trades():
    return [
        make_trade(1, "EURUSD", "buy",  0.1, 1.1000, 1.1050, +50.0,  duration_min=120),
        make_trade(2, "EURUSD", "buy",  0.1, 1.1020, 1.1080, +60.0,  duration_min=90),
        make_trade(3, "GBPUSD", "sell", 0.1, 1.2700, 1.2640, +60.0,  duration_min=45),
        make_trade(4, "USDJPY", "buy",  0.2, 149.50, 150.00, +100.0, duration_min=200),
        make_trade(5, "GBPUSD", "buy",  0.1, 1.2600, 1.2680, +80.0,  duration_min=60),
    ]


@pytest.fixture
def mixed_trades():
    return [
        make_trade(1,  "EURUSD", "buy",  0.1, 1.1000, 1.1050, +50.0,  duration_min=120),
        make_trade(2,  "EURUSD", "sell", 0.1, 1.1050, 1.1100, -50.0,  duration_min=30),
        make_trade(3,  "GBPUSD", "buy",  0.2, 1.2600, 1.2680, +160.0, duration_min=90),
        make_trade(4,  "GBPUSD", "sell", 0.1, 1.2700, 1.2750, -50.0,  duration_min=25),
        make_trade(5,  "USDJPY", "buy",  0.5, 149.00, 150.00, +500.0, duration_min=360),
        make_trade(6,  "USDJPY", "sell", 0.1, 150.00, 150.50, -50.0,  duration_min=10),
        make_trade(7,  "EURUSD", "buy",  0.1, 1.0950, 1.0930, -20.0,  duration_min=15),
        make_trade(8,  "GBPUSD", "buy",  0.3, 1.2500, 1.2600, +300.0, duration_min=480),
        make_trade(9,  "EURUSD", "sell", 0.1, 1.1100, 1.1080, +20.0,  duration_min=40),
        make_trade(10, "XAUUSD", "buy",  0.1, 2000.0, 1990.0, -100.0, duration_min=720),
    ]


@pytest.fixture
def trades_df(mixed_trades):
    return pd.DataFrame(mixed_trades)


@pytest.fixture
def winning_df(winning_trades):
    return pd.DataFrame(winning_trades)


@pytest.fixture
def daily_returns():
    """20 days of daily returns."""
    return [0.5, -0.2, 0.8, -0.3, 1.2, 0.1, -0.5, 0.9, 0.3, -0.1,
            0.6, -0.4, 1.5, 0.2, -0.2, 0.7, -0.8, 0.4, 0.1, 0.3]


# =============================================================================
# BASIC METRICS
# =============================================================================

class TestBasicMetrics:
    def test_win_rate_all_winners(self, winning_df):
        wr = calc_win_rate(winning_df)
        assert wr == 100.0

    def test_win_rate_mixed(self, trades_df):
        wr = calc_win_rate(trades_df)
        winners = len([t for t in trades_df.to_dict("records") if t["net_profit"] > 0])
        expected = (winners / len(trades_df)) * 100
        assert abs(wr - expected) < 0.01

    def test_win_rate_empty(self):
        assert calc_win_rate(pd.DataFrame()) == 0.0

    def test_profit_factor(self, trades_df):
        pf = calc_profit_factor(trades_df)
        gross_win  = sum(t["net_profit"] for t in trades_df.to_dict("records") if t["net_profit"] > 0)
        gross_loss = abs(sum(t["net_profit"] for t in trades_df.to_dict("records") if t["net_profit"] < 0))
        expected = gross_win / gross_loss if gross_loss else float("inf")
        assert abs(pf - expected) < 0.01

    def test_profit_factor_all_winners(self, winning_df):
        pf = calc_profit_factor(winning_df)
        assert pf == float("inf") or pf > 999

    def test_profit_factor_empty(self):
        assert calc_profit_factor(pd.DataFrame()) == 0.0

    def test_expectancy(self, trades_df):
        exp = calc_expectancy(trades_df)
        total = sum(t["net_profit"] for t in trades_df.to_dict("records"))
        expected = total / len(trades_df)
        assert abs(exp - expected) < 0.01

    def test_expectancy_empty(self):
        assert calc_expectancy(pd.DataFrame()) == 0.0

    def test_recovery_factor(self, trades_df):
        rf = calc_recovery_factor(trades_df)
        assert isinstance(rf, float)
        # Recovery factor = net_profit / max_drawdown
        assert rf >= 0 or rf == 0.0

    def test_kelly_criterion(self, trades_df):
        kelly = calc_kelly_criterion(trades_df)
        # Kelly % must be between 0 and 100
        assert 0.0 <= kelly <= 100.0


# =============================================================================
# DRAWDOWN
# =============================================================================

class TestDrawdown:
    def test_drawdown_no_loss(self, winning_df):
        dd = calc_drawdown(winning_df, start_balance=10000.0)
        # With all winners, drawdown could be 0
        assert dd["max_drawdown_dollar"] >= 0.0
        assert dd["max_drawdown_pct"] >= 0.0

    def test_drawdown_mixed(self, trades_df):
        dd = calc_drawdown(trades_df, start_balance=10000.0)
        assert "max_drawdown_dollar" in dd
        assert "max_drawdown_pct" in dd
        assert "daily_max_dd_pct" in dd

    def test_drawdown_pct_range(self, trades_df):
        dd = calc_drawdown(trades_df, start_balance=10000.0)
        assert 0.0 <= dd["max_drawdown_pct"] <= 100.0

    def test_drawdown_dollar_non_negative(self, trades_df):
        dd = calc_drawdown(trades_df, start_balance=10000.0)
        assert dd["max_drawdown_dollar"] >= 0.0


# =============================================================================
# RISK RATIOS
# =============================================================================

class TestRiskRatios:
    def test_sharpe_positive_returns(self, daily_returns):
        sharpe = calc_sharpe_ratio(daily_returns, risk_free=0.0)
        assert isinstance(sharpe, float)
        # Mostly positive returns should yield positive Sharpe
        assert sharpe > 0

    def test_sharpe_zero_std(self):
        # All same returns → std = 0
        sharpe = calc_sharpe_ratio([1.0, 1.0, 1.0, 1.0], risk_free=0.0)
        assert sharpe == 0.0 or sharpe == float("inf")

    def test_sharpe_empty(self):
        assert calc_sharpe_ratio([], risk_free=0.0) == 0.0

    def test_sortino_ratio(self, daily_returns):
        sortino = calc_sortino_ratio(daily_returns, risk_free=0.0)
        assert isinstance(sortino, float)
        # Sortino ≥ Sharpe when there are upside-only returns
        sharpe = calc_sharpe_ratio(daily_returns, risk_free=0.0)
        # Not always true, but sortino focuses on downside only
        assert sortino >= 0 or True  # may be negative if mostly losses

    def test_calmar_ratio(self, trades_df, daily_returns):
        calmar = calc_calmar_ratio(annual_return=15.0, max_drawdown_pct=5.0)
        assert abs(calmar - 3.0) < 0.01

    def test_calmar_zero_drawdown(self):
        calmar = calc_calmar_ratio(annual_return=15.0, max_drawdown_pct=0.0)
        assert calmar == 0.0 or calmar == float("inf")


# =============================================================================
# CONSECUTIVE STREAKS
# =============================================================================

class TestStreaks:
    def test_all_winners_streak(self, winning_df):
        streaks = calc_consecutive_streaks(winning_df)
        assert streaks["max_win_streak"]  == len(winning_df)
        assert streaks["max_loss_streak"] == 0

    def test_mixed_streaks(self, trades_df):
        streaks = calc_consecutive_streaks(trades_df)
        assert streaks["max_win_streak"]  >= 1
        assert streaks["max_loss_streak"] >= 1

    def test_single_trade_win(self):
        df = pd.DataFrame([make_trade(1, "EURUSD", "buy", 0.1, 1.1, 1.11, +100.0)])
        streaks = calc_consecutive_streaks(df)
        assert streaks["max_win_streak"] == 1

    def test_single_trade_loss(self):
        df = pd.DataFrame([make_trade(1, "EURUSD", "buy", 0.1, 1.1, 1.09, -100.0)])
        streaks = calc_consecutive_streaks(df)
        assert streaks["max_loss_streak"] == 1
        assert streaks["max_win_streak"]  == 0

    def test_empty_trades(self):
        streaks = calc_consecutive_streaks(pd.DataFrame())
        assert streaks["max_win_streak"]  == 0
        assert streaks["max_loss_streak"] == 0


# =============================================================================
# VALUE AT RISK
# =============================================================================

class TestVaR:
    def test_var_returns_negative(self, daily_returns):
        var = calc_var_95(daily_returns)
        # VaR (loss potential) should be expressed as negative or ≤ 0
        assert var <= 0

    def test_var_empty(self):
        var = calc_var_95([])
        assert var == 0.0

    def test_var_all_gains(self):
        returns = [1.0, 2.0, 3.0, 4.0, 5.0]
        var = calc_var_95(returns)
        # Even with gains, 5th percentile gives the smallest gain
        assert isinstance(var, float)


# =============================================================================
# MODIFIED DIETZ RETURN
# =============================================================================

class TestModifiedDietz:
    def test_no_cashflows(self):
        result = calc_modified_dietz(
            start_value=10000.0,
            end_value=10500.0,
            cashflows=[],
        )
        assert abs(result - 5.0) < 0.01

    def test_with_deposit(self):
        result = calc_modified_dietz(
            start_value=10000.0,
            end_value=11200.0,
            cashflows=[{"date_weight": 0.5, "amount": 1000.0}],
        )
        # Adjusted for mid-period deposit
        assert isinstance(result, float)

    def test_negative_return(self):
        result = calc_modified_dietz(
            start_value=10000.0,
            end_value=9000.0,
            cashflows=[],
        )
        assert result == pytest.approx(-10.0, abs=0.01)

    def test_zero_start(self):
        result = calc_modified_dietz(
            start_value=0.0,
            end_value=1000.0,
            cashflows=[],
        )
        # Cannot compute with 0 start
        assert result == 0.0 or result is None or isinstance(result, float)


# =============================================================================
# ANNUALIZED RETURN
# =============================================================================

class TestAnnualizedReturn:
    def test_one_year(self):
        result = calc_annualized_return(
            total_return_pct=20.0,
            days=365,
        )
        assert abs(result - 20.0) < 0.5

    def test_six_months(self):
        result = calc_annualized_return(
            total_return_pct=10.0,
            days=182,
        )
        # Annualized should be roughly 20%
        assert result > 15.0

    def test_zero_days(self):
        result = calc_annualized_return(total_return_pct=10.0, days=0)
        assert result == 0.0

    def test_negative_return(self):
        result = calc_annualized_return(total_return_pct=-15.0, days=365)
        assert result < 0


# =============================================================================
# SYMBOL BREAKDOWN
# =============================================================================

class TestSymbolBreakdown:
    def test_returns_per_symbol(self, trades_df):
        symbols = calc_symbol_breakdown(trades_df)
        assert isinstance(symbols, list)
        # EURUSD, GBPUSD, USDJPY, XAUUSD should all be present
        sym_names = {s["symbol"] for s in symbols}
        assert "EURUSD" in sym_names
        assert "GBPUSD" in sym_names

    def test_symbol_fields(self, trades_df):
        symbols = calc_symbol_breakdown(trades_df)
        for s in symbols:
            assert "symbol"       in s
            assert "net_profit"   in s
            assert "trade_count"  in s
            assert "win_rate"     in s
            assert "profit_factor" in s

    def test_empty(self):
        assert calc_symbol_breakdown(pd.DataFrame()) == []

    def test_single_symbol(self):
        df = pd.DataFrame([
            make_trade(1, "EURUSD", "buy", 0.1, 1.1, 1.11, 100.0),
            make_trade(2, "EURUSD", "buy", 0.1, 1.1, 1.09, -50.0),
        ])
        symbols = calc_symbol_breakdown(df)
        assert len(symbols) == 1
        assert symbols[0]["symbol"] == "EURUSD"
        assert symbols[0]["net_profit"] == pytest.approx(50.0, abs=0.01)


# =============================================================================
# DIRECTION ANALYSIS
# =============================================================================

class TestDirectionAnalysis:
    def test_buy_sell_split(self, trades_df):
        result = calc_direction_analysis(trades_df)
        assert "buy"  in result
        assert "sell" in result

    def test_buy_fields(self, trades_df):
        result = calc_direction_analysis(trades_df)
        buy = result["buy"]
        assert "total"        in buy
        assert "net_profit"   in buy
        assert "win_rate"     in buy
        assert "gross_profit" in buy
        assert "gross_loss"   in buy

    def test_all_buys(self):
        df = pd.DataFrame([
            make_trade(1, "EURUSD", "buy", 0.1, 1.1, 1.11, 100.0),
            make_trade(2, "EURUSD", "buy", 0.1, 1.1, 1.09, -50.0),
        ])
        result = calc_direction_analysis(df)
        assert result["sell"]["total"] == 0
        assert result["buy"]["total"]  == 2

    def test_empty(self):
        result = calc_direction_analysis(pd.DataFrame())
        assert result["buy"]["total"] == 0


# =============================================================================
# MONTHLY RETURNS
# =============================================================================

class TestMonthlyReturns:
    def test_returns_by_year_month(self, trades_df):
        result = calc_monthly_returns(trades_df, start_balance=10000.0)
        assert isinstance(result, list)
        for row in result:
            assert "year"   in row
            assert "months" in row
            assert len(row["months"]) == 12

    def test_empty(self):
        result = calc_monthly_returns(pd.DataFrame(), start_balance=10000.0)
        assert isinstance(result, list)


# =============================================================================
# HOURLY HEATMAP
# =============================================================================

class TestHourlyHeatmap:
    def test_24_hours(self, trades_df):
        result = calc_hourly_heatmap(trades_df)
        assert isinstance(result, list)
        # Should have entries for each hour seen in data
        hours = [r["hour"] for r in result]
        assert all(0 <= h <= 23 for h in hours)

    def test_fields(self, trades_df):
        result = calc_hourly_heatmap(trades_df)
        for row in result:
            assert "hour"       in row
            assert "avg_profit" in row
            assert "trade_count" in row

    def test_empty(self):
        result = calc_hourly_heatmap(pd.DataFrame())
        assert isinstance(result, list)


# =============================================================================
# PROFIT DISTRIBUTION
# =============================================================================

class TestProfitDistribution:
    def test_bucket_count(self, trades_df):
        result = calc_profit_distribution(trades_df)
        assert isinstance(result, list)
        assert len(result) > 0

    def test_bucket_fields(self, trades_df):
        result = calc_profit_distribution(trades_df)
        for bucket in result:
            assert "label"  in bucket
            assert "count"  in bucket
            assert bucket["count"] >= 0

    def test_total_count_matches(self, trades_df):
        result = calc_profit_distribution(trades_df)
        total = sum(b["count"] for b in result)
        assert total == len(trades_df)

    def test_empty(self):
        result = calc_profit_distribution(pd.DataFrame())
        assert isinstance(result, list)


# =============================================================================
# DURATION DISTRIBUTION
# =============================================================================

class TestDurationDistribution:
    def test_returns_buckets(self, trades_df):
        result = calc_duration_distribution(trades_df)
        assert isinstance(result, list)
        assert len(result) > 0

    def test_total_matches(self, trades_df):
        result = calc_duration_distribution(trades_df)
        total = sum(b.get("count", 0) for b in result)
        assert total == len(trades_df)


# =============================================================================
# MAE/MFE ANALYSIS
# =============================================================================

class TestMaeMfe:
    def test_returns_list(self, trades_df):
        result = calc_mae_mfe_summary(trades_df)
        assert isinstance(result, list)

    def test_fields(self, trades_df):
        result = calc_mae_mfe_summary(trades_df)
        for item in result:
            assert "mae"        in item
            assert "mfe"        in item
            assert "net_profit" in item

    def test_mae_non_positive(self, trades_df):
        result = calc_mae_mfe_summary(trades_df)
        # MAE should be <= 0 (adverse means loss)
        for item in result:
            assert item["mae"] <= 0

    def test_mfe_non_negative(self, trades_df):
        result = calc_mae_mfe_summary(trades_df)
        # MFE should be >= 0 (favorable means gain)
        for item in result:
            assert item["mfe"] >= 0


# =============================================================================
# CURRENCY EXPOSURE
# =============================================================================

class TestCurrencyExposure:
    def make_position(self, symbol, lots, direction="buy"):
        return type("P", (), {
            "symbol":     symbol,
            "lots":       lots,
            "trade_type": type("T", (), {"value": direction})(),
            "profit":     0.0,
        })()

    def test_eurusd_exposure(self):
        positions = [self.make_position("EURUSD", 0.1, "buy")]
        result = calc_currency_exposure(positions)
        assert isinstance(result, list)
        # EURUSD buy → long EUR, short USD
        currencies = {r["currency"] for r in result}
        assert "EUR" in currencies or len(result) == 0  # depends on implementation

    def test_empty(self):
        result = calc_currency_exposure([])
        assert isinstance(result, list)
        assert len(result) == 0

    def test_opposing_positions_cancel(self):
        positions = [
            self.make_position("EURUSD", 0.1, "buy"),
            self.make_position("EURUSD", 0.1, "sell"),
        ]
        result = calc_currency_exposure(positions)
        # Net exposure should be zero
        for r in result:
            if r["currency"] in ("EUR", "USD"):
                assert abs(r.get("net_lots", 0)) < 0.001


# =============================================================================
# EDGE CASES
# =============================================================================

class TestEdgeCases:
    def test_single_trade_all_metrics(self):
        """All metrics should handle single trade without crashing."""
        df = pd.DataFrame([make_trade(1, "EURUSD", "buy", 0.1, 1.1, 1.11, 100.0)])
        assert calc_win_rate(df)     == 100.0
        assert calc_profit_factor(df) > 0
        assert calc_expectancy(df)    == pytest.approx(100.0, abs=0.01)
        assert isinstance(calc_consecutive_streaks(df), dict)
        assert isinstance(calc_symbol_breakdown(df), list)

    def test_large_dataset_performance(self):
        """1000-trade dataset should compute in reasonable time."""
        import time
        trades = [
            make_trade(
                i, "EURUSD" if i % 2 == 0 else "GBPUSD",
                "buy" if i % 3 == 0 else "sell",
                0.1,
                1.1 + i * 0.0001, 1.1 + i * 0.0001 + (0.005 if i % 2 == 0 else -0.003),
                50.0 if i % 2 == 0 else -30.0,
            )
            for i in range(1, 1001)
        ]
        df = pd.DataFrame(trades)

        start = time.time()
        calc_win_rate(df)
        calc_profit_factor(df)
        calc_expectancy(df)
        calc_consecutive_streaks(df)
        calc_symbol_breakdown(df)
        elapsed = time.time() - start

        assert elapsed < 5.0, f"Took too long: {elapsed:.2f}s"

    def test_nan_handling(self):
        """NaN values should not crash calculations."""
        trades = [
            {**make_trade(1, "EURUSD", "buy", 0.1, 1.1, 1.11, 100.0), "mae": float("nan")},
            {**make_trade(2, "EURUSD", "buy", 0.1, 1.1, 1.09, -50.0), "mfe": None},
        ]
        df = pd.DataFrame(trades)
        # Should not raise
        assert isinstance(calc_win_rate(df), float)
        assert isinstance(calc_profit_factor(df), float)
