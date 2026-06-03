import os
import sys
import unittest
import json
import sqlite3
from unittest.mock import AsyncMock, MagicMock, patch

# Add workspace directory to path
sys.path.append("/home/ubuntu/prism-insight")

from trigger_batch import calculate_agent_fit_metrics, determine_market_regime
from stock_tracking_enhanced_agent import EnhancedStockTrackingAgent


class TestTradingHardRules(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.test_db = "test_hard_rules_db.sqlite"
        # Ensure clean DB state
        if os.path.exists(self.test_db):
            os.remove(self.test_db)
            
        # Setup tables
        conn = sqlite3.connect(self.test_db)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS stock_holdings (
                ticker TEXT PRIMARY KEY,
                company_name TEXT,
                buy_price REAL,
                buy_date TEXT,
                current_price REAL,
                last_updated TEXT,
                scenario TEXT,
                target_price REAL,
                stop_loss REAL,
                trigger_type TEXT,
                trigger_mode TEXT,
                highest_price REAL DEFAULT 0.0
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS market_condition (
                date TEXT PRIMARY KEY,
                kospi_index REAL,
                kosdaq_index REAL,
                condition INTEGER,
                volatility REAL
            )
        """)
        conn.commit()
        conn.close()

    def tearDown(self):
        if os.path.exists(self.test_db):
            os.remove(self.test_db)

    @patch("trigger_batch.determine_market_regime")
    @patch("trigger_batch.get_multi_day_ohlcv")
    def test_calculate_agent_fit_metrics_pivot_buffer(self, mock_ohlcv, mock_regime):
        # 1. Bull market scenario (buffer 15%)
        mock_regime.return_value = "strong_bull"
        import pandas as pd
        # Set Volume and Amount high enough (e.g., Amount = 10 Billion KRW) to pass absolute filters
        mock_ohlcv.return_value = pd.DataFrame({
            "High": [1000, 1000, 1000, 1000, 1000],
            "Close": [1000, 1000, 1000, 1000, 1000],
            "Volume": [10000000, 10000000, 10000000, 10000000, 10000000],
            "Amount": [10000000000, 10000000000, 10000000000, 10000000000, 10000000000]
        })
        
        # Test current price is 112% of pivot (1120 vs 1000)
        # In bull market (+15%), this should be valid (agent_fit_score > 0)
        res = calculate_agent_fit_metrics("005930", 1120, "20260603", lookback_days=5, trigger_type="거래량 급증 상위주")
        self.assertGreater(res["agent_fit_score"], 0.0)
        
        # Test current price is 116% of pivot (1160 vs 1000)
        # Above +15%, should be invalid (agent_fit_score == 0)
        res = calculate_agent_fit_metrics("005930", 1160, "20260603", lookback_days=5, trigger_type="거래량 급증 상위주")
        self.assertEqual(res["agent_fit_score"], 0.0)

        # 2. Bear/Neutral market scenario (buffer 8%)
        mock_regime.return_value = "sideways"
        # Test current price is 107% of pivot (1070 vs 1000) -> valid under 8%
        res = calculate_agent_fit_metrics("005930", 1070, "20260603", lookback_days=5, trigger_type="거래량 급증 상위주")
        self.assertGreater(res["agent_fit_score"], 0.0)
        
        # Test current price is 110% of pivot (1100 vs 1000) -> invalid under 8%
        res = calculate_agent_fit_metrics("005930", 1100, "20260603", lookback_days=5, trigger_type="거래량 급증 상위주")
        self.assertEqual(res["agent_fit_score"], 0.0)

    @patch("stock_tracking_enhanced_agent.create_sell_decision_agent")
    @patch("stock_tracking_enhanced_agent.OpenAIAugmentedLLM")
    @patch("stock_tracking_enhanced_agent.EnhancedStockTrackingAgent._get_current_stock_price")
    @patch("stock_tracking_enhanced_agent.EnhancedStockTrackingAgent._analyze_simple_market_condition")
    @patch("stock_tracking_enhanced_agent.EnhancedStockTrackingAgent._cleanup_old_watchlist")
    @patch("stock_tracking_enhanced_agent.EnhancedStockTrackingAgent._get_stock_volatility")
    @patch("stock_tracking_enhanced_agent.EnhancedStockTrackingAgent._analyze_trend")
    async def test_sell_decision_guardrails(self, mock_trend, mock_volatility, mock_cleanup, mock_market, mock_price, mock_llm_cls, mock_sell_agent):
        # Setup mocks
        mock_market.return_value = (0, 0.0)
        mock_cleanup.return_value = True
        mock_volatility.return_value = 15.0
        mock_trend.return_value = 0 # Neutral trend
        
        mock_llm_instance = AsyncMock()
        mock_llm_cls.return_value = mock_llm_instance
        
        # Mock LLM returns Hold
        llm_response = {
            "should_sell": False,
            "sell_reason": "Hold because momentum is fine.",
            "confidence": 8,
            "analysis_summary": {
                "technical_trend": "Uptrend",
                "volume_analysis": "Normal",
                "market_condition_impact": "None",
                "time_factor": "Keep"
            },
            "portfolio_adjustment": {
                "needed": False,
                "reason": "",
                "new_target_price": None,
                "new_stop_loss": None,
                "urgency": "low"
            }
        }
        mock_llm_instance.generate_str.return_value = json.dumps(llm_response)
        
        # Initialize test agent
        agent = EnhancedStockTrackingAgent(db_path=self.test_db)
        await agent.initialize()
        agent.sell_decision_agent = MagicMock()
        agent.sell_decision_agent.attach_llm = AsyncMock(return_value=mock_llm_instance)
        
        # Mock price feed
        mock_price.return_value = 10000.0

        # Scenario A: Trailing Stop Hard-rule Triggered
        # Buy price: 10000, current price: 10560 (Profit: +5.6%), highest price: 12000 (Drawdown: -12.0%)
        # LLM said "Hold", but trailing stop hard-rule should force a "Sell"
        stock_data = {
            "ticker": "000010",
            "company_name": "TestStockA",
            "buy_price": 10000.0,
            "buy_date": "2026-06-01 09:00:00",
            "current_price": 10560.0,
            "target_price": 13000.0,
            "stop_loss": 9300.0,
            "scenario": "{}"
        }
        
        # Save initially to stock_holdings so DB query in sell decision can fetch highest_price
        agent.cursor.execute("""
            INSERT INTO stock_holdings (ticker, company_name, buy_price, buy_date, current_price, target_price, stop_loss, highest_price, scenario)
            VALUES ('000010', 'TestStockA', 10000.0, '2026-06-01 09:00:00', 10560.0, 13000.0, 9300.0, 12000.0, '{}')
        """)
        agent.conn.commit()

        should_sell, reason = await agent._analyze_sell_decision(stock_data)
        
        self.assertTrue(should_sell)
        self.assertIn("Trailing Stop hard-rule triggered", reason)

        # Scenario B: Break-even Stop Loss Elevation Hard-rule
        # Buy price: 10000, current price: 11100 (Profit: +11%), highest: 11100 (Drawdown: 0%)
        # LLM said "Hold" with no adjustment.
        # Guardrail should force target stop_loss to at least buy_price (10000)
        stock_data_b = {
            "ticker": "000020",
            "company_name": "TestStockB",
            "buy_price": 10000.0,
            "buy_date": "2026-06-01 09:00:00",
            "current_price": 11100.0,
            "target_price": 13000.0,
            "stop_loss": 9300.0,
            "scenario": "{}"
        }
        agent.cursor.execute("""
            INSERT INTO stock_holdings (ticker, company_name, buy_price, buy_date, current_price, target_price, stop_loss, highest_price, scenario)
            VALUES ('000020', 'TestStockB', 10000.0, '2026-06-01 09:00:00', 11100.0, 13000.0, 9300.0, 11100.0, '{}')
        """)
        agent.conn.commit()

        should_sell, reason = await agent._analyze_sell_decision(stock_data_b)
        self.assertFalse(should_sell)
        
        # Verify stop_loss was raised in DB to 10000 (buy_price)
        agent.cursor.execute("SELECT stop_loss FROM stock_holdings WHERE ticker = '000020'")
        new_sl = agent.cursor.fetchone()[0]
        self.assertEqual(new_sl, 10000.0)

        # Scenario C: Trailing Stop Loss Elevation Hard-rule
        # Buy price: 10000, current price: 10500 (Profit: +5%), highest: 11500 (Drawdown: -8.7% - not triggered)
        # LLM said "Hold" with no adjustment.
        # Trailing stop line (highest * 0.88) = 10120
        # DB stop_loss (9300) should be raised to 10120
        stock_data_c = {
            "ticker": "000030",
            "company_name": "TestStockC",
            "buy_price": 10000.0,
            "buy_date": "2026-06-01 09:00:00",
            "current_price": 10500.0,
            "target_price": 13000.0,
            "stop_loss": 9300.0,
            "scenario": "{}"
        }
        agent.cursor.execute("""
            INSERT INTO stock_holdings (ticker, company_name, buy_price, buy_date, current_price, target_price, stop_loss, highest_price, scenario)
            VALUES ('000030', 'TestStockC', 10000.0, '2026-06-01 09:00:00', 10500.0, 13000.0, 9300.0, 11500.0, '{}')
        """)
        agent.conn.commit()

        should_sell, reason = await agent._analyze_sell_decision(stock_data_c)
        self.assertFalse(should_sell)
        
        # Verify stop_loss was raised to 10120 (11500 * 0.88)
        agent.cursor.execute("SELECT stop_loss FROM stock_holdings WHERE ticker = '000030'")
        new_sl_c = agent.cursor.fetchone()[0]
        self.assertEqual(new_sl_c, 10120.0)


if __name__ == "__main__":
    unittest.main()
