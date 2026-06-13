#!/usr/bin/env python3
"""
Test Skipped Stocks Smart-Filtered Journaling System

This module validates:
1. create_skip_entry functionality in JournalManager
2. Database trade_type = 'skip' integration
3. Smart filter matching logic inside PerformanceTrackerBatch
"""

import asyncio
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from tracking.journal import JournalManager
from tracking.db_schema import create_all_tables, create_indexes
from performance_tracker_batch import PerformanceTrackerBatch

# Mock response for retrospective LLM
MOCK_JOURNAL_RESPONSE = """
```json
{
    "situation_analysis": {
        "analysis_context_summary": "Passed on this stock due to score below threshold, but it broke resistance later."
    },
    "judgment_evaluation": {
        "skip_appropriateness": "이유 있는 보류였으나 매수 기준 완화 필요"
    },
    "lessons": [
        {"condition": "주요 지지선 안착 시", "action": "점수 가중치 상향", "reason": "안정적 추세 형성", "priority": "high"},
        {"condition": "섹터 전반 수급 개선 시", "action": "필터 기준 완화", "reason": "동반 상승 가능성", "priority": "medium"}
    ],
    "pattern_tags": ["missed_rally", "underpriced"],
    "one_line_summary": "매수 보류 후 30일 간 강력한 상승 추세 형성으로 기회 비용 발생",
    "confidence_score": 0.85
}
```
"""


@pytest.fixture
def anyio_backend():
    return 'asyncio'


class TestSkipJournaling:
    """Test skipped stock retrospective journaling"""

    def setup_method(self):
        """Set up test fixtures"""
        self.temp_db = tempfile.NamedTemporaryFile(suffix='.sqlite', delete=False)
        self.db_path = self.temp_db.name
        self.temp_db.close()
        
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.cursor = self.conn.cursor()
        
        # Create schema
        create_all_tables(self.cursor, self.conn)
        create_indexes(self.cursor, self.conn)

    def teardown_method(self):
        """Clean up test fixtures"""
        self.conn.close()
        try:
            os.unlink(self.db_path)
        except:
            pass

    @pytest.mark.anyio
    @patch("cores.agents.trading_journal_agent.create_trading_journal_agent")
    async def test_create_skip_entry_saves_correctly(self, mock_agent_factory):
        """Test that create_skip_entry successfully runs LLM and saves to DB with trade_type = 'skip'"""
        # Set up mock agent and LLM
        mock_agent = MagicMock()
        mock_llm = MagicMock()
        mock_llm.generate_str = AsyncMock(return_value=MOCK_JOURNAL_RESPONSE)
        mock_agent.attach_llm = AsyncMock(return_value=mock_llm)
        mock_agent_factory.return_value = mock_agent

        # JournalManager instance
        manager = JournalManager(
            cursor=self.cursor,
            conn=self.conn,
            language="ko",
            enable_journal=True
        )

        stock_data = {
            "ticker": "005930",
            "company_name": "삼성전자",
            "analyzed_price": 70000.0,
            "analyzed_date": "2026-05-12 09:00:00",
            "scenario": '{"sector": "반도체", "rationale": "테스트 근거"}',
            "buy_score": 65,
            "min_score": 70,
            "skip_reason": "Low Score",
            "target_price": 80000.0,
            "stop_loss": 65000.0
        }

        tracking_data = {
            "tracked_7d_return": 0.05,
            "tracked_14d_return": 0.10,
            "tracked_30d_return": 0.20,
            "tracked_30d_price": 84000.0
        }

        # Run method
        result = await manager.create_skip_entry(
            stock_data=stock_data,
            tracking_data=tracking_data,
            trade_date="2026-06-12 17:00:00"
        )

        assert result is True

        # Query database to confirm insertion
        self.cursor.execute("SELECT * FROM trading_journal WHERE ticker = '005930'")
        row = self.cursor.fetchone()
        
        assert row is not None
        assert row["trade_type"] == "skip"
        assert row["sell_price"] == 84000.0
        assert row["profit_rate"] == 20.0  # 0.20 * 100
        assert row["sell_reason"] == "Low Score"
        assert row["holding_days"] == 30
        assert "missed_rally" in row["pattern_tags"]
        
        # Verify principles extraction
        self.cursor.execute("SELECT * FROM trading_principles WHERE source_journal_ids IS NOT NULL")
        principles = self.cursor.fetchall()
        assert len(principles) > 0
        assert principles[0]["scope"] in ["universal", "sector"]


class TestSmartFilterMatching:
    """Test the smart filter criteria in PerformanceTrackerBatch"""

    def setup_method(self):
        """Set up test fixtures"""
        self.temp_db = tempfile.NamedTemporaryFile(suffix='.sqlite', delete=False)
        self.db_path = self.temp_db.name
        self.temp_db.close()
        
        self.conn = sqlite3.connect(self.db_path)
        self.cursor = self.conn.cursor()
        create_all_tables(self.cursor, self.conn)
        
        # Create performance tracker table (needs specific structure)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS analysis_performance_tracker (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT,
                company_name TEXT,
                trigger_type TEXT,
                trigger_mode TEXT,
                analyzed_date TEXT,
                analyzed_price REAL,
                decision TEXT,
                was_traded INTEGER,
                skip_reason TEXT,
                buy_score REAL,
                min_score REAL,
                target_price REAL,
                stop_loss REAL,
                risk_reward_ratio REAL,
                tracked_7d_date TEXT,
                tracked_7d_price REAL,
                tracked_7d_return REAL,
                tracked_14d_date TEXT,
                tracked_14d_price REAL,
                tracked_14d_return REAL,
                tracked_30d_date TEXT,
                tracked_30d_price REAL,
                tracked_30d_return REAL,
                tracking_status TEXT,
                updated_at TEXT
            )
        """)
        self.conn.commit()

    def teardown_method(self):
        """Clean up test fixtures"""
        self.conn.close()
        try:
            os.unlink(self.db_path)
        except:
            pass

    @pytest.mark.anyio
    @patch("performance_tracker_batch.PerformanceTrackerBatch.apply_updates", return_value=True)
    @patch("performance_tracker_batch.PerformanceTrackerBatch.get_current_price", return_value=100.0)
    async def test_smart_filter_logic(self, mock_get_price, mock_apply):
        """Test that different combinations of returns and scores trigger journaling correctly"""
        
        # 1. Missed Trend: 7d, 14d, 30d all positive & max_price reached >= target_price
        # Insert target that matches Missed Trend
        self.cursor.execute("""
            INSERT INTO analysis_performance_tracker 
            (ticker, company_name, was_traded, tracking_status, analyzed_price, buy_score, min_score, target_price, skip_reason,
             tracked_7d_return, tracked_7d_price, tracked_14d_return, tracked_14d_price, analyzed_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "000001", "MissedTrendStock", 0, "pending", 80.0, 60, 70, 95.0, "Low Score",
            0.05, 84.0, 0.10, 88.0, "2026-05-12 09:00:00"
        ))
        
        # 2. Near-Miss: buy_score >= min_score * 0.9 & ret_30d >= 15%
        self.cursor.execute("""
            INSERT INTO analysis_performance_tracker 
            (ticker, company_name, was_traded, tracking_status, analyzed_price, buy_score, min_score, target_price, skip_reason,
             tracked_7d_return, tracked_7d_price, tracked_14d_return, tracked_14d_price, analyzed_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "000002", "NearMissStock", 0, "pending", 80.0, 64, 70, 120.0, "Low Score",
            0.01, 80.8, 0.02, 81.6, "2026-05-12 09:00:00"
        ))
        
        # 3. No-Slot Omission: skip_reason matches slot & ret_30d >= 10%
        self.cursor.execute("""
            INSERT INTO analysis_performance_tracker 
            (ticker, company_name, was_traded, tracking_status, analyzed_price, buy_score, min_score, target_price, skip_reason,
             tracked_7d_return, tracked_7d_price, tracked_14d_return, tracked_14d_price, analyzed_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "000003", "NoSlotStock", 0, "pending", 80.0, 75, 70, 120.0, "Holdings at maximum slots",
            0.01, 80.8, 0.02, 81.6, "2026-05-12 09:00:00"
        ))
        
        # 4. Avoided Danger: 30d return <= -10%
        self.cursor.execute("""
            INSERT INTO analysis_performance_tracker 
            (ticker, company_name, was_traded, tracking_status, analyzed_price, buy_score, min_score, target_price, skip_reason,
             tracked_7d_return, tracked_7d_price, tracked_14d_return, tracked_14d_price, analyzed_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "000004", "AvoidedDangerStock", 0, "pending", 80.0, 50, 70, 100.0, "Low Score",
            -0.02, 78.4, -0.05, 76.0, "2026-05-12 09:00:00"
        ))
        
        # 5. Non-matching neutral stock
        self.cursor.execute("""
            INSERT INTO analysis_performance_tracker 
            (ticker, company_name, was_traded, tracking_status, analyzed_price, buy_score, min_score, target_price, skip_reason,
             tracked_7d_return, tracked_7d_price, tracked_14d_return, tracked_14d_price, analyzed_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "000005", "NeutralStock", 0, "pending", 80.0, 40, 70, 100.0, "Low Score",
            0.01, 80.8, 0.02, 81.6, "2026-05-12 09:00:00"
        ))
        
        self.conn.commit()

        # Instantiate batch runner
        batch = PerformanceTrackerBatch(db_path=self.db_path, dry_run=False)
        
        # Mock process_skip_journals to see what got accumulated
        batch.process_skip_journals = AsyncMock()
        
        # Mock calculate_days_elapsed to force 30 days (completion)
        batch.calculate_days_elapsed = MagicMock(return_value=30)
        
        # Mock get_historical_price to return None so it falls back to mock current_price
        batch.get_historical_price = MagicMock(return_value=None)
        
        # Adjust mock current price logic depending on the stock code
        def side_effect_price(ticker):
            # Target price is checked for max price reached
            # MissedTrend target is 95, analyzed is 80. We return 100.0 (gain 25%)
            if ticker == "000001":
                return 100.0
            # Near-Miss target is 120, return 96.0 (gain 20% >= 15%)
            elif ticker == "000002":
                return 96.0
            # No-Slot target 120, return 89.6 (gain 12% >= 10%)
            elif ticker == "000003":
                return 89.6
            # Avoided Danger target 100, return 71.2 (loss -11% <= -10%)
            elif ticker == "000004":
                return 71.2
            # Neutral target 100, return 82.4 (gain 3% -> matches none)
            else:
                return 82.4
                
        batch.get_current_price = MagicMock(side_effect=side_effect_price)

        # Run the batch
        await batch.run()
        
        # Verify that process_skip_journals was called with the correct targets
        assert batch.process_skip_journals.call_count == 1
        
        # Check targets passed to process_skip_journals
        called_args = batch.process_skip_journals.call_args[0][0]
        assert len(called_args) == 3  # MissedTrend, NearMiss, NoSlot should match
        
        called_tickers = {item[0]['ticker'] for item in called_args}
        assert "000001" in called_tickers
        assert "000002" in called_tickers
        assert "000003" in called_tickers
        assert "000004" not in called_tickers  # AvoidedDangerStock should be filtered out
        assert "000005" not in called_tickers  # NeutralStock should be filtered out
        
        # Confirm matched conditions descriptions are logged/saved
        matched_conditions_by_ticker = {item[0]['ticker']: item[1]['matched_conditions'] for item in called_args}
        assert "Missed Trend" in matched_conditions_by_ticker["000001"]
        assert "Near-Miss" in matched_conditions_by_ticker["000002"]
        assert "No-Slot Omission" in matched_conditions_by_ticker["000003"]
