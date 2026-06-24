#!/usr/bin/env python3
"""
Test: Analysis-failed scenarios must NOT be saved to DB.

Validates that when _extract_trading_scenario returns a default (failed) scenario,
analyze_report returns success=False, and process_reports skips DB saves entirely.
"""

import asyncio
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tracking.helpers import default_scenario
from tracking.db_schema import create_all_tables, create_indexes


@pytest.fixture
def anyio_backend():
    return "asyncio"


class TestAnalysisFailedGuard:
    """Verify that analysis failures do not pollute the DB."""

    def setup_method(self):
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self.db_path = self.temp_db.name
        self.temp_db.close()

        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.cursor = self.conn.cursor()
        create_all_tables(self.cursor, self.conn)
        create_indexes(self.cursor, self.conn)

        # Create watchlist_history table (enhanced agent only)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS watchlist_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT, company_name TEXT, current_price REAL,
                analyzed_date TEXT, buy_score REAL, min_score REAL,
                decision TEXT, skip_reason TEXT, target_price REAL,
                stop_loss REAL, investment_period TEXT, sector TEXT,
                scenario TEXT, portfolio_analysis TEXT,
                valuation_analysis TEXT, sector_outlook TEXT,
                market_condition TEXT, rationale TEXT,
                trigger_type TEXT, trigger_mode TEXT,
                risk_reward_ratio REAL, was_traded INTEGER DEFAULT 0
            )
        """)
        # Create analysis_performance_tracker table
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS analysis_performance_tracker (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                watchlist_id INTEGER, ticker TEXT, company_name TEXT,
                trigger_type TEXT, trigger_mode TEXT,
                analyzed_date TEXT, analyzed_price REAL,
                decision TEXT, was_traded INTEGER, skip_reason TEXT,
                buy_score REAL, min_score REAL,
                target_price REAL, stop_loss REAL,
                risk_reward_ratio REAL, tracking_status TEXT,
                created_at TEXT
            )
        """)
        self.conn.commit()

    def teardown_method(self):
        self.conn.close()
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def test_default_scenario_has_marker(self):
        """default_scenario() must carry the _analysis_failed marker."""
        scenario = default_scenario()
        assert scenario.get("_analysis_failed") is True

    @pytest.mark.anyio
    async def test_analyze_report_returns_failure_on_default_scenario(self):
        """When _extract_trading_scenario returns default scenario,
        analyze_report must return success=False."""
        with patch("stock_tracking_agent.app") as mock_app:
            mock_app.run = MagicMock(return_value=AsyncMock())

            from stock_tracking_agent import StockTrackingAgent

            agent = StockTrackingAgent.__new__(StockTrackingAgent)
            agent.cursor = self.cursor
            agent.conn = self.conn
            agent.language = "ko"
            agent.max_slots = 5
            agent.enable_journal = False
            agent.trigger_info_map = {}

            # Mock internals to isolate the guard logic
            agent._extract_ticker_info = AsyncMock(return_value=("483650", "달바글로벌"))
            agent._is_ticker_in_holdings = AsyncMock(return_value=False)
            agent._get_current_stock_price = AsyncMock(return_value=228500.0)
            agent._get_trading_value_rank_change = AsyncMock(return_value=(0, ""))

            # Simulate API failure: _extract_trading_scenario returns default
            agent._extract_trading_scenario = AsyncMock(return_value=default_scenario())

            result = await agent.analyze_report("reports/483650_test.md")

            assert result["success"] is False
            assert "ticker" in result
            assert result["ticker"] == "483650"
            assert "API/parse error" in result.get("error", "")

    @pytest.mark.anyio
    async def test_analyze_report_passes_on_valid_scenario(self):
        """When scenario is valid (not failed), analyze_report must return success=True."""
        with patch("stock_tracking_agent.app") as mock_app:
            mock_app.run = MagicMock(return_value=AsyncMock())

            from stock_tracking_agent import StockTrackingAgent

            agent = StockTrackingAgent.__new__(StockTrackingAgent)
            agent.cursor = self.cursor
            agent.conn = self.conn
            agent.language = "ko"
            agent.max_slots = 5
            agent.MAX_SAME_SECTOR = 2
            agent.SECTOR_CONCENTRATION_RATIO = 0.4
            agent.enable_journal = False
            agent.trigger_info_map = {}

            agent._extract_ticker_info = AsyncMock(return_value=("005930", "삼성전자"))
            agent._is_ticker_in_holdings = AsyncMock(return_value=False)
            agent._get_current_stock_price = AsyncMock(return_value=70000.0)
            agent._get_trading_value_rank_change = AsyncMock(return_value=(5, "▲5"))

            valid_scenario = {
                "portfolio_analysis": "Good fundamentals",
                "buy_score": 7.5,
                "decision": "Enter",
                "target_price": 80000,
                "stop_loss": 65000,
                "investment_period": "Short-term",
                "rationale": "Strong momentum",
                "sector": "반도체",
                "considerations": "Watch for resistance",
                "pivot_point": 69000,
                "pivot_buffer_pct": 5.0,
                "buy_limit_price": 70000,
                "min_score": 5.0,
                "risk_reward_ratio": 2.0,
            }
            agent._extract_trading_scenario = AsyncMock(return_value=valid_scenario)
            agent._check_sector_diversity = AsyncMock(return_value=True)

            with patch("pdf_converter.pdf_to_markdown_text", return_value="Report content"):
                result = await agent.analyze_report("reports/005930_test.md")

            assert result["success"] is True
            assert result["ticker"] == "005930"

    @pytest.mark.anyio
    async def test_process_reports_skips_db_on_failure(self):
        """process_reports must not call _save_watchlist_item when analysis fails."""
        with patch("stock_tracking_agent.app") as mock_app:
            mock_app.run = MagicMock(return_value=AsyncMock())

            from stock_tracking_enhanced_agent import EnhancedStockTrackingAgent

            agent = EnhancedStockTrackingAgent.__new__(EnhancedStockTrackingAgent)
            agent.cursor = self.cursor
            agent.conn = self.conn
            agent.language = "ko"
            agent.max_slots = 5
            agent.enable_journal = False
            agent.message_queue = []
            agent._msg_types = []
            agent.trigger_info_map = {}
            agent.volatility_table = {}

            # analyze_report returns failure
            agent.analyze_report = AsyncMock(return_value={
                "success": False,
                "error": "Trading scenario extraction failed (API/parse error)",
                "ticker": "483650",
                "company_name": "달바글로벌",
            })
            agent.update_holdings = AsyncMock(return_value=[])
            agent._save_watchlist_item = AsyncMock()
            agent._reevaluate_watch_stocks = AsyncMock(return_value=0)

            buy_count, sell_count = await agent.process_reports(["reports/483650_test.md"])

            # _save_watchlist_item must NOT have been called
            agent._save_watchlist_item.assert_not_called()
            assert buy_count == 0

            # DB tables must be empty
            self.cursor.execute("SELECT COUNT(*) FROM watchlist_history")
            assert self.cursor.fetchone()[0] == 0

            self.cursor.execute("SELECT COUNT(*) FROM analysis_performance_tracker")
            assert self.cursor.fetchone()[0] == 0
