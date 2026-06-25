#!/usr/bin/env python3
"""
Stock Tracking and Trading Agent

This module performs buy/sell decisions using AI-based stock analysis reports
and manages trading records.

Main Features:
1. Generate trading scenarios based on analysis reports
2. Manage stock purchases/sales (maximum 10 slots)
3. Track trading history and returns
4. Share results through Telegram channel
"""
from dotenv import load_dotenv
load_dotenv()  # Load environment variables from .env file

import asyncio
import json
import logging
import os
import re
import sqlite3
import sys
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional

from telegram import Bot
from telegram.error import TelegramError

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"stock_tracking_{datetime.now().strftime('%Y%m%d')}.log")
    ]
)
logger = logging.getLogger(__name__)

# MCP related imports
from mcp_agent.app import MCPApp
from mcp_agent.workflows.llm.augmented_llm import RequestParams
from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM

# Core agent imports
from cores.agents.trading_agents import create_trading_scenario_agent
from cores.utils import parse_llm_json

# Tracking package imports (refactored helpers)
from tracking import (
    create_all_tables,
    create_indexes,
    add_scope_column_if_missing,
    add_trigger_columns_if_missing,
    add_sector_column_if_missing,
    extract_ticker_info,
    get_current_stock_price,
    get_trading_value_rank_change,
    is_ticker_in_holdings,
    get_current_slots_count,
    check_sector_diversity,
    parse_price_value,
    default_scenario,
    analyze_sell_decision,
    format_buy_message,
    format_sell_message,
    calculate_profit_rate,
    calculate_holding_days,
    JournalManager,
    CompressionManager,
    TelegramSender,
)

# Create MCPApp instance
app = MCPApp(name="stock_tracking")

class StockTrackingAgent:
    """Stock Tracking and Trading Agent"""

    # Constants
    MAX_SLOTS = 10  # Maximum number of stocks to hold
    MAX_SAME_SECTOR = 3  # Maximum holdings in same sector
    SECTOR_CONCENTRATION_RATIO = 0.3  # Sector concentration limit ratio

    # Investment period constants
    PERIOD_SHORT = "short_term"  # Within 1 month
    PERIOD_MEDIUM = "medium_term"  # 1-3 months
    PERIOD_LONG = "long_term"  # 3+ months

    # Buy score thresholds
    SCORE_STRONG_BUY = 8  # Strong buy
    SCORE_CONSIDER = 7  # Consider buying
    SCORE_UNSUITABLE = 6  # Unsuitable for buying

    def __init__(self, db_path: str = "stock_tracking_db.sqlite", telegram_token: str = None, enable_journal: bool = None):
        """
        Initialize agent

        Args:
            db_path: SQLite database file path
            telegram_token: Telegram bot token
            enable_journal: Enable trading journal feature (default: False, reads from ENABLE_TRADING_JOURNAL env)
        """
        self.max_slots = self.MAX_SLOTS
        self.message_queue = []  # For storing Telegram messages
        self._msg_types = []  # msg_type for each message in queue
        self._broadcast_task = None  # Track broadcast translation task
        self.trading_agent = None
        self.db_path = db_path
        self.conn = None
        self.cursor = None

        # Set trading journal feature flag
        # Priority: parameter > environment variable > default (False)
        if enable_journal is not None:
            self.enable_journal = enable_journal
        else:
            env_value = os.environ.get("ENABLE_TRADING_JOURNAL", "false").lower()
            self.enable_journal = env_value in ("true", "1", "yes")

        # Set Telegram bot token
        self.telegram_token = telegram_token or os.environ.get("TELEGRAM_BOT_TOKEN")
        self.telegram_bot = None
        if self.telegram_token:
            self.telegram_bot = Bot(token=self.telegram_token)

    async def initialize(self, language: str = "ko"):
        """
        Create necessary tables and initialize

        Args:
            language: Language code for agents (default: "ko")
        """
        logger.info("Starting tracking agent initialization")
        logger.info(f"Trading journal feature: {'enabled' if self.enable_journal else 'disabled'}")

        # Store language for later use
        self.language = language

        # Initialize SQLite connection
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row  # Return results as dictionary
        self.cursor = self.conn.cursor()

        # Initialize trading scenario generation agent with language
        self.trading_agent = create_trading_scenario_agent()

        # Create database tables
        await self._create_tables()

        # Initialize helper managers (delegates to tracking/ package)
        self.journal_manager = JournalManager(
            self.cursor, self.conn, language, self.enable_journal
        )
        self.compression_manager = CompressionManager(
            self.cursor, self.conn, language, self.enable_journal
        )
        self.telegram_sender = TelegramSender(self.telegram_bot)

        logger.info("Tracking agent initialization complete")
        return True

    def close(self):
        """Close database connection and release resources"""
        if hasattr(self, 'conn') and self.conn:
            try:
                self.conn.close()
                logger.info("StockTrackingAgent database connection closed successfully")
            except Exception as e:
                logger.warning(f"Failed to close StockTrackingAgent database connection: {e}")

    async def _create_tables(self):
        """Create necessary database tables (delegates to tracking.db_schema)"""
        create_all_tables(self.cursor, self.conn)
        add_scope_column_if_missing(self.cursor, self.conn)  # Must run before indexes
        add_trigger_columns_if_missing(self.cursor, self.conn)  # v1.16.5 migration
        add_sector_column_if_missing(self.cursor, self.conn)  # v1.17 migration for AI agent sector queries
        create_indexes(self.cursor, self.conn)

    async def _extract_ticker_info(self, report_path: str) -> Tuple[str, str]:
        """Extract ticker code and company name (delegates to tracking.helpers)"""
        return extract_ticker_info(report_path)

    async def _get_current_stock_price(self, ticker: str) -> float:
        """Get current stock price (delegates to tracking.helpers)"""
        return await get_current_stock_price(self.cursor, ticker)

    async def _get_trading_value_rank_change(self, ticker: str) -> Tuple[float, str]:
        """Calculate trading value ranking change (delegates to tracking.helpers)"""
        return await get_trading_value_rank_change(ticker)

    async def _is_ticker_in_holdings(self, ticker: str) -> bool:
        """Check if stock is already in holdings (delegates to tracking.helpers)"""
        return is_ticker_in_holdings(self.cursor, ticker)

    async def _get_current_slots_count(self) -> int:
        """Get current number of holdings (delegates to tracking.helpers)"""
        return get_current_slots_count(self.cursor)

    async def _check_sector_diversity(self, sector: str) -> bool:
        """Check for over-concentration in same sector (delegates to tracking.helpers)"""
        return check_sector_diversity(
            self.cursor, sector,
            self.MAX_SAME_SECTOR, self.SECTOR_CONCENTRATION_RATIO
        )

    async def _extract_trading_scenario(
        self,
        report_content: str,
        rank_change_msg: str = "",
        ticker: str = None,
        sector: str = None,
        trigger_type: str = "",
        trigger_mode: str = ""
    ) -> Dict[str, Any]:
        """
        Extract trading scenario from report

        Args:
            report_content: Analysis report content
            rank_change_msg: Trading value ranking change info
            ticker: Stock ticker code (for journal context lookup)
            sector: Stock sector (for journal context lookup)
            trigger_type: Trigger type that activated this analysis (e.g., 'Volume Surge Top Stocks')
            trigger_mode: Trigger mode ('morning' or 'afternoon')

        Returns:
            Dict: Trading scenario information
        """
        try:
            # Get current holdings info and sector distribution
            current_slots = await self._get_current_slots_count()

            # Collect current portfolio information
            self.cursor.execute("""
                SELECT ticker, company_name, buy_price, current_price, scenario
                FROM stock_holdings
            """)
            holdings = [dict(row) for row in self.cursor.fetchall()]

            # Analyze sector distribution
            sector_distribution = {}
            investment_periods = {"short_term": 0, "medium_term": 0, "long_term": 0}

            for holding in holdings:
                scenario_str = holding.get('scenario', '{}')
                try:
                    if isinstance(scenario_str, str):
                        scenario_data = json.loads(scenario_str)

                        # Collect sector info
                        sector_name = scenario_data.get('sector', 'Unknown')
                        sector_distribution[sector_name] = sector_distribution.get(sector_name, 0) + 1

                        # Collect investment period info
                        period = scenario_data.get('investment_period', 'medium_term')
                        investment_periods[period] = investment_periods.get(period, 0) + 1
                except:
                    pass

            # Portfolio info string
            portfolio_info = f"""
            Current holdings: {current_slots}/{self.max_slots}
            Sector distribution: {json.dumps(sector_distribution, ensure_ascii=False)}
            Investment period distribution: {json.dumps(investment_periods, ensure_ascii=False)}
            """

            # Get trading journal context for informed decisions
            journal_context = ""
            adjustment = 0.0
            reasons = []
            if ticker:
                journal_context = self._get_relevant_journal_context(
                    ticker=ticker,
                    sector=sector,
                    market_condition=None,
                    trigger_type=trigger_type
                )
                # Get score adjustment for Python-side decision logic
                adjustment, reasons = self._get_score_adjustment_from_context(ticker, sector, trigger_type)

            # LLM call to generate trading scenario
            llm = await self.trading_agent.attach_llm(OpenAIAugmentedLLM)

            # Build trigger info section if available
            trigger_info_section = ""
            if trigger_type:
                if self.language == "ko":
                    trigger_info_section = f"""
                ### 📡 Trigger Info (Apply Trigger-Based Entry Criteria)
                - **Triggered By**: {trigger_type}
                - **Trigger Mode**: {trigger_mode or 'unknown'}
                """
                else:
                    trigger_info_section = f"""
                ### 📡 Trigger Info (Apply Trigger-Based Entry Criteria)
                - **Triggered By**: {trigger_type}
                - **Trigger Mode**: {trigger_mode or 'unknown'}
                """

            # Prepare prompt based on language
            if self.language == "ko":
                prompt_message = f"""
                This is an AI analysis report for a stock. Please generate a trading scenario based on this report.

                [중요 지침]
                - 출력 스키마의 'buy_score' 및 'effective_score'는 과거 매매 기록 통계 보정이 배제된 순수한 현재 보고서 및 모멘텀 분석 기준의 기본 점수(Base Score, 1.0~10.0)로만 산출하십시오. 과거 통계에 의한 최종 보정 연산은 파이썬 시스템 단에서 사후 처리되므로, AI가 임의로 미리 보정하지 않아야 합니다.

                ### Current Portfolio Status:
                {portfolio_info}
                {trigger_info_section}
                ### Trading Value Analysis:
                {rank_change_msg}
                {journal_context}

                ### Report Content:
                {report_content}
                """
            else:  # English
                prompt_message = f"""
                This is an AI analysis report for a stock. Please generate a trading scenario based on this report.

                [Important Directive]
                - The 'buy_score' (and 'effective_score') in your output must reflect a pure qualitative Base Score (1.0 to 10.0 scale) based solely on current analysis and momentum, without incorporating any past performance stats or adjustments. Final statistical scaling will be mathematically processed on the Python code side.

                ### Current Portfolio Status:
                {portfolio_info}
                {trigger_info_section}
                ### Trading Value Analysis:
                {rank_change_msg}
                {journal_context}

                ### Report Content:
                {report_content}
                """

            response = await llm.generate_str(
                message=prompt_message,
                request_params=RequestParams(
                    model="gpt-5.5",
                    maxTokens=30000,
                    metadata={
                        "service_tier":"flex",
                    }
                )
            )

            # ponytail: Fallback to standard tier if flex tier fails (returns empty)
            if not response or not response.strip():
                logger.warning("[trading scenario] Empty response received with flex tier. Retrying without service_tier...")
                response = await llm.generate_str(
                    message=prompt_message,
                    request_params=RequestParams(
                        model="gpt-5.5",
                        maxTokens=30000,
                    )
                )

            # JSON parsing (consolidated in cores/utils.py)
            # TODO: Create model and call generate_structured function to improve code maintainability
            scenario_json = parse_llm_json(response, context='trading scenario')
            if scenario_json is not None:
                # Apply hybrid scoring calculation and decision override in Python
                try:
                    original_buy_score = float(scenario_json.get("buy_score") or 0.0)
                    macro_adj = float(scenario_json.get("macro_adjustment") or 0.0)
                    min_score = float(scenario_json.get("min_score") or 5.0)

                    if original_buy_score == 0.0 and scenario_json.get("effective_score"):
                        original_buy_score = float(scenario_json.get("effective_score")) - macro_adj

                    # Compute final scores (buy_score gets adjustment, effective_score gets both adjustment and macro_adjustment)
                    final_buy_score = round(max(1.0, min(10.0, original_buy_score + adjustment)), 2)
                    final_effective_score = round(max(1.0, min(10.0, final_buy_score + macro_adj)), 2)

                    # Store score details inside the scenario JSON
                    scenario_json["buy_score_original"] = scenario_json.get("buy_score", 0.0)
                    scenario_json["effective_score_original"] = scenario_json.get("effective_score", 0.0)
                    scenario_json["buy_score"] = final_buy_score
                    scenario_json["effective_score"] = final_effective_score
                    scenario_json["score_adjustment"] = adjustment
                    scenario_json["score_adjustment_reasons"] = reasons

                    # Overrule decision if the resulting score doesn't reach the required minimum threshold
                    decision = self._normalize_decision(scenario_json.get("decision", "No entry"))
                    if decision == "Enter" and final_effective_score < min_score:
                        scenario_json["decision"] = "Skip"
                        scenario_json["rejection_reason"] = f"Insufficient final score ({final_effective_score} < {min_score}) [Base: {original_buy_score}, Macro: {macro_adj:+.1f}, Adj: {adjustment:+.2f}]"
                        logger.info(f"[{ticker}] Python Hybrid Scoring overridden decision from Enter to Skip. Final Score: {final_effective_score} < Min Score: {min_score}")
                except Exception as ex:
                    logger.warning(f"Hybrid scoring calculation failed: {ex}")

                logger.info(f"Scenario parsed: {json.dumps(scenario_json, ensure_ascii=False)}")
                return scenario_json

            logger.error(f"Trading scenario parse failed. Full response: {response}")
            return self._default_scenario()

        except Exception as e:
            logger.error(f"Error extracting trading scenario: {str(e)}")
            logger.error(traceback.format_exc())
            return self._default_scenario()

    def _default_scenario(self) -> Dict[str, Any]:
        """Return default trading scenario (delegates to tracking.helpers)"""
        return default_scenario()

    async def analyze_report(self, pdf_report_path: str) -> Dict[str, Any]:
        """
        Analyze stock analysis report and make trading decision

        Args:
            pdf_report_path: PDF analysis report file path

        Returns:
            Dict: Trading decision result
        """
        try:
            logger.info(f"Starting report analysis: {pdf_report_path}")

            # Extract ticker code and company name from file path
            ticker, company_name = await self._extract_ticker_info(pdf_report_path)

            if not ticker or not company_name:
                logger.error(f"Failed to extract ticker info: {pdf_report_path}")
                return {"success": False, "error": "Failed to extract ticker info"}

            # Check if already holding this stock
            is_holding = await self._is_ticker_in_holdings(ticker)
            if is_holding:
                logger.info(f"{ticker}({company_name}) already in holdings")
                # Get current price for the stock even when already holding
                holding_current_price = await self._get_current_stock_price(ticker)
                return {
                    "success": True,
                    "decision": "Already holding",
                    "ticker": ticker,
                    "company_name": company_name,
                    "current_price": holding_current_price
                }

            # Get current stock price
            current_price = await self._get_current_stock_price(ticker)
            if current_price <= 0:
                logger.error(f"{ticker} current price query failed")
                return {"success": False, "error": "Current price query failed"}

            # Analyze trading value ranking change
            rank_change_percentage, rank_change_msg = await self._get_trading_value_rank_change(ticker)

            # Read report content
            from pdf_converter import pdf_to_markdown_text
            report_content = pdf_to_markdown_text(pdf_report_path)

            # Remove disclaimer sections to save tokens and prevent OpenAI API load errors
            if isinstance(report_content, str) and report_content:
                disclaimer_pattern = re.compile(
                    r'(?:^|\n)#+\s*(투자\s*유의\s*사항|investment\s*disclaimer)',
                    re.IGNORECASE
                )
                match = disclaimer_pattern.search(report_content)
                if match:
                    logger.info(f"Removing disclaimer section starting at index {match.start()} for ticker {ticker}")
                    report_content = report_content[:match.start()].strip()


            # Get trigger info for this ticker (from trigger_results file loaded at run() time)
            trigger_info = getattr(self, 'trigger_info_map', {}).get(ticker, {})
            trigger_type = trigger_info.get('trigger_type', '')
            trigger_mode = trigger_info.get('trigger_mode', '')

            # Extract trading scenario (pass trading value ranking info, ticker, and trigger info)
            scenario = await self._extract_trading_scenario(
                report_content,
                rank_change_msg,
                ticker=ticker,
                sector=None,  # sector will be determined by the scenario agent
                trigger_type=trigger_type,
                trigger_mode=trigger_mode
            )

            # ponytail: guard against API/parse failures leaking into DB.
            # _default_scenario() sets _analysis_failed=True; without this check
            # the zero-score record pollutes watchlist_history → performance_tracker → journal.
            if scenario.get("_analysis_failed"):
                logger.warning(
                    f"Skipping {company_name}({ticker}): trading scenario extraction failed "
                    f"(API error or parse failure). Not saving to DB."
                )
                return {
                    "success": False,
                    "error": "Trading scenario extraction failed (API/parse error)",
                    "ticker": ticker,
                    "company_name": company_name,
                }

            # Check sector diversity
            sector = scenario.get("sector", "Unknown")
            is_sector_diverse = await self._check_sector_diversity(sector)

            # Determine initial decision
            decision = self._normalize_decision(scenario.get("decision", "No entry"))

            # volume_profile_info extraction and fallback
            vp_info = scenario.get("volume_profile_info", "")
            if not vp_info or vp_info == "No significant upper resistance":
                trigger_info = getattr(self, 'trigger_info_map', {}).get(ticker, {})
                vp_info = str(trigger_info.get('volume_profile_info', 'No significant upper resistance'))
            scenario["volume_profile_info"] = vp_info

            # Pivot Rule validation for buy (Enter) decisions
            if decision == "Enter":
                # Get pivot_point from scenario (AI suggested)
                pivot_val = scenario.get("pivot_point", 0)
                pivot_point = self._parse_price_value(pivot_val)

                # Fallback to technical pivot_point from trigger_info_map if scenario doesn't have a valid one
                if pivot_point <= 0:
                    trigger_info = getattr(self, 'trigger_info_map', {}).get(ticker, {})
                    pivot_point = float(trigger_info.get('pivot_point', 0))

                # If still invalid, fallback to current price
                if pivot_point <= 0:
                    pivot_point = float(current_price)

                # v2.1.0: 마켓 레짐에 따른 피벗 버퍼 한도 완화 (강세장 시 최대 15.0% 허용)
                try:
                    from trigger_batch import determine_market_regime
                    today_str = datetime.now().strftime("%Y%m%d")
                    regime = determine_market_regime(today_str)
                    is_bull = regime in ["strong_bull", "moderate_bull"]
                except Exception as regime_err:
                    logger.warning(f"Failed to check market regime: {regime_err}")
                    is_bull = False

                # Parse dynamic pivot buffer percentage from scenario, default is 5.0%
                pivot_buffer_pct = scenario.get("pivot_buffer_pct", 5.0)
                try:
                    pivot_buffer_pct = float(pivot_buffer_pct)
                    # Bound between 5.0% and 15.0% for bull market, else 8.0% for safety
                    max_buffer = 15.0 if is_bull else 8.0
                    pivot_buffer_pct = min(max(pivot_buffer_pct, 5.0), max_buffer)
                except (ValueError, TypeError):
                    pivot_buffer_pct = 5.0

                pivot_multiplier = 1.0 + (pivot_buffer_pct / 100.0)

                logger.info(f"{ticker}({company_name}): Verifying pivot rule. Pivot: {pivot_point:,.0f}, Current: {current_price:,.0f}, Buffer: {pivot_buffer_pct:.1f}%")

                if current_price < pivot_point:
                    decision = "Watch"
                    scenario["decision"] = "Watch"
                    scenario["rejection_reason"] = f"Pivot point not breakout yet (Current: {current_price:,.0f} < Pivot: {pivot_point:,.0f})"
                    logger.info(f"{ticker}({company_name}): Decision changed to Watch. Reason: {scenario['rejection_reason']}")
                elif current_price > pivot_point * pivot_multiplier:
                    decision = "Skip"
                    scenario["decision"] = "Skip"
                    scenario["rejection_reason"] = f"Chase buying prohibited (Current: {current_price:,.0f} > {pivot_multiplier:.2f} * Pivot: {pivot_point*pivot_multiplier:,.0f})"
                    logger.info(f"{ticker}({company_name}): Decision changed to Skip. Reason: {scenario['rejection_reason']}")
                else:
                    logger.info(f"{ticker}({company_name}): {pivot_buffer_pct:.1f}% pivot rule satisfied. Decision remains Enter.")
                    # Ensure pivot_point and pivot_buffer_pct are set in scenario for buy_stock
                    scenario["pivot_point"] = pivot_point
                    scenario["pivot_buffer_pct"] = pivot_buffer_pct

            # Return result
            return {
                "success": True,
                "ticker": ticker,
                "company_name": company_name,
                "current_price": current_price,
                "scenario": scenario,
                "decision": decision,
                "sector": sector,
                "sector_diverse": is_sector_diverse,
                "rank_change_percentage": rank_change_percentage,
                "rank_change_msg": rank_change_msg
            }

        except Exception as e:
            logger.error(f"Error analyzing report: {str(e)}")
            logger.error(traceback.format_exc())
            return {"success": False, "error": str(e)}

    @staticmethod
    def _normalize_decision(decision: str) -> str:
        """Normalize AI decision string to canonical English form.

        LLM may return decision in Korean or various English forms.
        Maps all variants to a consistent set: 'Enter', 'Watch', 'Skip'.
        """
        if not decision:
            return "Skip"
        normalized = decision.strip()
        enter_variants = {"진입", "Entry", "enter", "entry", "Enter", "매수", "Buy", "buy"}
        watch_variants = {"관망", "Watch", "watch", "Hold", "hold", "보류"}
        skip_variants = {"미진입", "Skip", "skip", "No entry", "no entry", "패스", "Pass", "pass", "No Entry"}

        enter_set = {s.casefold() for s in enter_variants}
        watch_set = {s.casefold() for s in watch_variants}
        skip_set  = {s.casefold() for s in skip_variants}

        key = normalized.casefold()

        if key in enter_set:
            return "Enter"
        if key in watch_set:
            return "Watch"
        if key in skip_set:
            return "Skip"
        return normalized

    def _parse_price_value(self, value: Any) -> float:
        """Parse price value and convert to number (delegates to tracking.helpers)"""
        return parse_price_value(value)

    def _get_trigger_win_rate(self, trigger_type: str) -> str:
        """Get trigger win rate string from analysis_performance_tracker.
        Returns a formatted string like '(이 트리거 과거 승률: 63%)' or empty string if no data."""
        if not trigger_type or not self.conn:
            return ""
        try:
            cursor = self.conn.cursor()
            row = cursor.execute("""
                SELECT COUNT(*) as completed,
                       SUM(CASE WHEN tracked_30d_return > 0 THEN 1 ELSE 0 END) as wins
                FROM analysis_performance_tracker
                WHERE trigger_type = ? AND tracking_status = 'completed'
            """, (trigger_type,)).fetchone()
            if row and row[0] >= 3:
                win_rate = int(row[1] / row[0] * 100)
                return f"📡 이 트리거 과거 승률: {win_rate}% ({row[0]}건)"
            return ""
        except Exception:
            return ""

    async def _retire_watch_stock(self, wl_id: int, ticker: str, decision: str, reason: str):
        """Helper to retire a watch stock as Skip in both tables."""
        try:
            self.cursor.execute(
                """
                UPDATE watchlist_history
                SET decision = ?, skip_reason = ?
                WHERE id = ?
                """,
                (decision, reason, wl_id)
            )
            self.cursor.execute(
                """
                UPDATE analysis_performance_tracker
                SET decision = ?, skip_reason = ?
                WHERE watchlist_id = ?
                """,
                (decision, reason, wl_id)
            )
            self.conn.commit()
            logger.info(f"[{ticker}] Retired watch stock as {decision}: {reason}")
        except Exception as e:
            logger.error(f"[{ticker}] Failed to retire watch stock ID {wl_id}: {e}")

    async def _reevaluate_watch_stocks(self) -> int:
        """
        Re-evaluate stocks currently in 'Watch' state in watchlist_history.
        Verify if they satisfy pivot breakout conditions or should be skipped.
        Returns the number of successful purchases.
        """
        watch_buy_count = 0
        try:
            import json
            from datetime import datetime, timedelta
            import traceback

            logger.info("Starting re-evaluation of 'Watch' stocks in watchlist")

            # 1. Fetch pending Watch items from watchlist_history
            self.cursor.execute(
                """
                SELECT id, ticker, company_name, analyzed_date, buy_score, min_score,
                       target_price, stop_loss, sector, scenario, trigger_type, trigger_mode
                FROM watchlist_history
                WHERE decision = 'Watch' AND was_traded = 0
                """
            )
            rows = self.cursor.fetchall()
            watch_stocks = [dict(row) for row in rows]

            if not watch_stocks:
                logger.info("No active 'Watch' stocks found for re-evaluation")
                return 0

            logger.info(f"Found {len(watch_stocks)} 'Watch' stock(s) to re-evaluate")

            # Determine market regime (used for maximum pivot buffer bound check)
            is_bull = False
            try:
                from trigger_batch import determine_market_regime
                today_str = datetime.now().strftime("%Y%m%d")
                regime = determine_market_regime(today_str)
                is_bull = regime in ["strong_bull", "moderate_bull"]
            except Exception as regime_err:
                logger.warning(f"Failed to check market regime during re-evaluation: {regime_err}")

            for item in watch_stocks:
                try:
                    ticker = str(item.get("ticker", ""))
                    company_name = str(item.get("company_name", ""))
                    analyzed_date_str = item.get("analyzed_date")

                    # Safe type conversions using _parse_price_value
                    stop_loss = self._parse_price_value(item.get("stop_loss", 0))
                    target_price = self._parse_price_value(item.get("target_price", 0))

                    # A. Parse scenario and extract pivot settings
                    scenario_str = item.get("scenario", "{}")
                    scenario = {}
                    try:
                        if isinstance(scenario_str, str):
                            scenario = json.loads(scenario_str)
                        elif isinstance(scenario_str, dict):
                            scenario = scenario_str
                    except Exception as parse_err:
                        logger.warning(f"[{ticker}] Failed to parse scenario JSON during re-evaluation: {parse_err}")

                    # Calculate days passed safely
                    days_passed = 0
                    if analyzed_date_str:
                        try:
                            # Strip timestamp if exists
                            date_only = str(analyzed_date_str).split(' ')[0] if ' ' in str(analyzed_date_str) else str(analyzed_date_str)
                            analyzed_date = datetime.strptime(date_only, "%Y-%m-%d")
                            days_passed = (datetime.now() - analyzed_date).days
                        except Exception as dt_err:
                            logger.warning(f"[{ticker}] Failed to parse analyzed_date '{analyzed_date_str}': {dt_err}")

                    # Fetch current price
                    current_price = await self._get_current_stock_price(ticker)
                    if current_price <= 0:
                        logger.warning(f"[{ticker}] Failed to query current price during watch re-evaluation, skipping")
                        continue

                    # Get pivot points and buffer pct from scenario
                    pivot_val = scenario.get("pivot_point", 0)
                    pivot_point = self._parse_price_value(pivot_val)
                    if pivot_point <= 0:
                        # Fallback to technical pivot_point from trigger_info_map if scenario doesn't have a valid one
                        trigger_info = getattr(self, 'trigger_info_map', {}).get(ticker, {})
                        pivot_point = self._parse_price_value(trigger_info.get('pivot_point', 0))

                    if pivot_point <= 0:
                        pivot_point = float(current_price)

                    pivot_buffer_pct = scenario.get("pivot_buffer_pct", 5.0)
                    try:
                        pivot_buffer_pct = float(pivot_buffer_pct)
                        max_buffer = 15.0 if is_bull else 8.0
                        pivot_buffer_pct = min(max(pivot_buffer_pct, 5.0), max_buffer)
                    except (ValueError, TypeError):
                        pivot_buffer_pct = 5.0

                    pivot_multiplier = 1.0 + (pivot_buffer_pct / 100.0)

                    # Check Expiration (14 days)
                    if days_passed > 14:
                        logger.info(f"[{ticker}] {company_name}: Watch period expired (14 days passed). Changing to Skip.")
                        await self._retire_watch_stock(item["id"], ticker, "Skip", "Watch period expired (14 days)")
                        continue

                    # Check Stop-loss breach (Current price below stop loss)
                    if stop_loss > 0 and current_price < stop_loss:
                        logger.info(f"[{ticker}] {company_name}: Price fell below stop_loss ({current_price:,.0f} < {stop_loss:,.0f}). Changing to Skip.")
                        await self._retire_watch_stock(item["id"], ticker, "Skip", "Watch stock fell below stop_loss prior to breakout")
                        continue

                    # Check Breakout & Pivot Rule validation
                    if current_price < pivot_point:
                        # Still below pivot, remain Watch
                        logger.info(f"[{ticker}] {company_name}: Still below pivot. Current: {current_price:,.0f} < Pivot: {pivot_point:,.0f}. Remain Watch.")
                        continue
                    elif current_price > pivot_point * pivot_multiplier:
                        # Chasing limit exceeded, transition to Skip
                        logger.info(f"[{ticker}] {company_name}: Chase buying prohibited. Current: {current_price:,.0f} > Max Pivot Buffer: {pivot_point * pivot_multiplier:,.0f}. Changing to Skip.")
                        await self._retire_watch_stock(item["id"], ticker, "Skip", f"Chase buying prohibited (Current: {current_price:,.0f} > Max Pivot Buffer: {pivot_point * pivot_multiplier:,.0f})")
                        continue
                    else:
                        # Pivot rule satisfied, BUY entry!
                        logger.info(f"[{ticker}] {company_name}: Breakout verified! Current: {current_price:,.0f} matches Pivot: {pivot_point:,.0f} with buffer. Executing Enter.")

                        # Ensure scenario keys are updated
                        scenario["pivot_point"] = pivot_point
                        scenario["pivot_buffer_pct"] = pivot_buffer_pct

                        # Trigger buy entry with is_watchlist_entry=True flag
                        buy_success = await self.buy_stock(
                            ticker=ticker,
                            company_name=company_name,
                            current_price=current_price,
                            scenario=scenario,
                            rank_change_msg="Watchlist breakout buy entry",
                            is_watchlist_entry=True
                        )

                        if buy_success:
                            # Call actual account trading function (async)
                            trade_result = {'success': False, 'message': 'Trading context execution bypassed or failed'}
                            try:
                                from trading.domestic_stock_trading import AsyncTradingContext
                                async with AsyncTradingContext() as trading:
                                    trade_result = await trading.async_buy_stock(stock_code=ticker, limit_price=current_price)

                                if trade_result['success']:
                                    logger.info(f"[{ticker}] Actual purchase successful: {trade_result['message']}")
                                    watch_buy_count += 1
                                else:
                                    logger.error(f"[{ticker}] Actual purchase failed: {trade_result['message']}")
                            except Exception as trade_err:
                                logger.error(f"[{ticker}] Error during actual purchase API execution: {trade_err}")
                                trade_result = {'success': False, 'message': str(trade_err)}

                            # Publish buy signal via Redis
                            try:
                                from messaging.redis_signal_publisher import publish_buy_signal
                                await publish_buy_signal(
                                    ticker=ticker,
                                    company_name=company_name,
                                    price=current_price,
                                    scenario=scenario,
                                    source="Watchlist Breakout Entry",
                                    trade_result=trade_result
                                )
                            except Exception as signal_err:
                                logger.warning(f"Buy signal publish failed (Redis): {signal_err}")

                            # Publish buy signal via GCP Pub/Sub
                            try:
                                from messaging.gcp_pubsub_signal_publisher import publish_buy_signal as gcp_publish_buy_signal
                                await gcp_publish_buy_signal(
                                    ticker=ticker,
                                    company_name=company_name,
                                    price=current_price,
                                    scenario=scenario,
                                    source="Watchlist Breakout Entry",
                                    trade_result=trade_result
                                )
                            except Exception as signal_err:
                                logger.warning(f"Buy signal publish failed (GCP): {signal_err}")

                            # Update DB entry in watchlist_history and analysis_performance_tracker
                            self.cursor.execute(
                                """
                                UPDATE watchlist_history
                                SET decision = 'Enter', was_traded = 1, current_price = ?
                                WHERE id = ?
                                """,
                                (current_price, item["id"])
                            )
                            self.cursor.execute(
                                """
                                UPDATE analysis_performance_tracker
                                SET decision = 'Enter', was_traded = 1
                                WHERE watchlist_id = ?
                                """,
                                (item["id"],)
                            )
                            self.conn.commit()
                            logger.info(f"[{ticker}] Watchlist history and performance tracker updated to Enter/Traded")
                except Exception as item_err:
                    logger.error(f"[{item.get('ticker', 'Unknown')}] Error processing single watch stock: {item_err}")
                    logger.error(traceback.format_exc())

        except Exception as e:
            logger.error(f"Error during watch stocks re-evaluation: {str(e)}")
            logger.error(traceback.format_exc())

        return watch_buy_count

    async def buy_stock(self, ticker: str, company_name: str, current_price: float, scenario: Dict[str, Any], rank_change_msg: str = "", is_watchlist_entry: bool = False) -> bool:
        """
        Process stock purchase

        Args:
            ticker: Stock code
            company_name: Company name
            current_price: Current stock price
            scenario: Trading scenario information
            rank_change_msg: Trading value ranking change info
            is_watchlist_entry: Whether this buy is triggered from watchlist re-evaluation

        Returns:
            bool: Purchase success status
        """
        try:
            # Check if already holding
            if await self._is_ticker_in_holdings(ticker):
                logger.warning(f"{ticker}({company_name}) already in holdings")
                return False

            # Check available slots
            current_slots = await self._get_current_slots_count()
            if current_slots >= self.max_slots:
                logger.warning(f"Holdings already at maximum ({self.max_slots})")
                return False

            # Check market-based maximum portfolio size
            max_portfolio_size = scenario.get('max_portfolio_size', self.max_slots)
            # Convert to int if stored as string
            if isinstance(max_portfolio_size, str):
                try:
                    max_portfolio_size = int(max_portfolio_size)
                except (ValueError, TypeError):
                    max_portfolio_size = self.max_slots
            if current_slots >= max_portfolio_size:
                logger.warning(f"Reached market-based max portfolio size ({max_portfolio_size}). Current holdings: {current_slots}")
                return False

            # Current time
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # Get trigger info from trigger_info_map (loaded from trigger_results file)
            trigger_info = getattr(self, 'trigger_info_map', {}).get(ticker, {})
            trigger_type = trigger_info.get('trigger_type', 'AI Analysis')
            trigger_mode = trigger_info.get('trigger_mode', getattr(self, 'trigger_mode', 'unknown'))

            # Add to holdings table
            self.cursor.execute(
                """
                INSERT INTO stock_holdings
                (ticker, company_name, buy_price, buy_date, current_price, last_updated, scenario, target_price, stop_loss, trigger_type, trigger_mode)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ticker,
                    company_name,
                    current_price,
                    now,
                    current_price,
                    now,
                    json.dumps(scenario, ensure_ascii=False),
                    scenario.get('target_price', 0),
                    scenario.get('stop_loss', 0),
                    trigger_type,
                    trigger_mode
                )
            )
            self.cursor.connection.commit() # Safely ensure commit in holdings

            buy_score = scenario.get("buy_score", 0)
            effective_score = scenario.get("effective_score", buy_score)
            min_score = scenario.get("min_score", 0)
            pivot_point = scenario.get("pivot_point", 0)

            # Extract score adjustments
            buy_score_orig = scenario.get("buy_score_original", buy_score)
            macro_adj = scenario.get("macro_adjustment", 0.0)
            score_adj = scenario.get("score_adjustment", 0.0)
            adj_reasons = scenario.get("score_adjustment_reasons", [])

            # Add purchase message
            if is_watchlist_entry:
                message = f"👁️ 관망 종목 돌파 매수: {company_name}({ticker})\n"
            else:
                message = f"📈 신규 매수: {company_name}({ticker})\n"

            # Format breakdown string
            adj_parts = [f"기본: {buy_score_orig}"]
            if macro_adj != 0.0:
                adj_parts.append(f"거시: {macro_adj:+.1f}")
            if score_adj != 0.0:
                adj_parts.append(f"이력: {score_adj:+.1f}")

            adj_str = f" [{', '.join(adj_parts)}]"
            message += f"점수: {effective_score} (최소 요구 점수: {min_score}){adj_str}\n"

            if score_adj != 0.0 and adj_reasons:
                message += "보정 사유:\n"
                for r in adj_reasons:
                    message += f"  • {r}\n"

            message += f"매수가: {current_price:,.0f}원\n"
            if pivot_point and pivot_point > 0:
                message += f"피벗 기준가: {pivot_point:,.0f}원\n"

            vp_info = scenario.get("volume_profile_info", "")
            if vp_info and vp_info != "No significant upper resistance":
                message += f"매물 저항대: {vp_info}\n"

            rr_ratio = scenario.get("risk_reward_ratio", 0)
            if rr_ratio:
                message += f"기대 손익비: {float(rr_ratio):.1f}배\n"

            message += f"목표가: {scenario.get('target_price', 0):,.0f}원\n" \
                       f"손절가: {scenario.get('stop_loss', 0):,.0f}원\n" \
                       f"투자기간: {scenario.get('investment_period', '단기')}\n" \
                       f"산업군: {scenario.get('sector', '알 수 없음')}\n"

            # Add trigger win rate
            trigger_win_rate = self._get_trigger_win_rate(trigger_type)
            if trigger_win_rate:
                message += f"{trigger_win_rate}\n"

            # Add valuation analysis if available
            if scenario.get('valuation_analysis'):
                message += f"밸류에이션: {scenario.get('valuation_analysis')}\n"

            # Add sector outlook if available
            if scenario.get('sector_outlook'):
                message += f"업종 전망: {scenario.get('sector_outlook')}\n"

            # Add trading value ranking info if available
            if rank_change_msg:
                message += f"거래대금 분석: {rank_change_msg}\n"

            message += f"투자근거: {scenario.get('rationale', '정보 없음')}\n"

            # Format trading scenario
            trading_scenarios = scenario.get('trading_scenarios', {})
            if trading_scenarios and isinstance(trading_scenarios, dict):
                message += "\n" + "="*40 + "\n"
                message += "📋 매매 시나리오\n"
                message += "="*40 + "\n\n"

                # 1. Key Levels
                key_levels = trading_scenarios.get('key_levels', {})
                if key_levels:
                    message += "💰 핵심 가격대:\n"

                    # Resistance levels
                    primary_resistance = self._parse_price_value(key_levels.get('primary_resistance', 0))
                    secondary_resistance = self._parse_price_value(key_levels.get('secondary_resistance', 0))
                    if primary_resistance or secondary_resistance:
                        message += f"  📈 저항선:\n"
                        if secondary_resistance:
                            message += f"    • 2차: {secondary_resistance:,.0f}원\n"
                        if primary_resistance:
                            message += f"    • 1차: {primary_resistance:,.0f}원\n"

                    # Current price
                    message += f"  ━━ 현재가: {current_price:,.0f}원 ━━\n"

                    # Support levels
                    primary_support = self._parse_price_value(key_levels.get('primary_support', 0))
                    secondary_support = self._parse_price_value(key_levels.get('secondary_support', 0))
                    if primary_support or secondary_support:
                        message += f"  📉 지지선:\n"
                        if primary_support:
                            message += f"    • 1차: {primary_support:,.0f}원\n"
                        if secondary_support:
                            message += f"    • 2차: {secondary_support:,.0f}원\n"

                    # Volume baseline
                    volume_baseline = key_levels.get('volume_baseline', '')
                    if volume_baseline:
                        message += f"  📊 거래량 기준: {volume_baseline}\n"

                    message += "\n"

                # 2. Sell Signals
                sell_triggers = trading_scenarios.get('sell_triggers', [])
                if sell_triggers:
                    message += "🔔 매도 시그널:\n"
                    for i, trigger in enumerate(sell_triggers, 1):
                        # Select emoji based on condition
                        if "profit" in trigger.lower() or "target" in trigger.lower() or "resistance" in trigger.lower():
                            emoji = "✅"
                        elif "loss" in trigger.lower() or "support" in trigger.lower() or "decline" in trigger.lower():
                            emoji = "⛔"
                        elif "time" in trigger.lower() or "sideways" in trigger.lower():
                            emoji = "⏰"
                        else:
                            emoji = "•"

                        message += f"  {emoji} {trigger}\n"
                    message += "\n"

                # 3. Hold Conditions
                hold_conditions = trading_scenarios.get('hold_conditions', [])
                if hold_conditions:
                    message += "✋ 보유 지속 조건:\n"
                    for condition in hold_conditions:
                        message += f"  • {condition}\n"
                    message += "\n"

                # 4. Portfolio Context
                portfolio_context = trading_scenarios.get('portfolio_context', '')
                if portfolio_context:
                    message += f"💼 포트폴리오 관점:\n  {portfolio_context}\n"

            self._msg_types.append("analysis")
            self.message_queue.append(message)
            logger.info(f"{ticker}({company_name}) purchase complete")

            return True

        except Exception as e:
            logger.error(f"{ticker} Error during purchase processing: {str(e)}")
            logger.error(traceback.format_exc())
            return False

    async def _analyze_sell_decision(self, stock_data: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Sell decision analysis

        Args:
            stock_data: Stock information

        Returns:
            Tuple[bool, str]: Whether to sell, sell reason
        """
        try:
            ticker = stock_data.get('ticker', '')
            buy_price = stock_data.get('buy_price', 0)
            buy_date = stock_data.get('buy_date', '')
            current_price = stock_data.get('current_price', 0)
            target_price = stock_data.get('target_price', 0)
            stop_loss = stock_data.get('stop_loss', 0)

            # Calculate profit rate
            profit_rate = ((current_price - buy_price) / buy_price) * 100

            # Days elapsed from buy date
            buy_datetime = datetime.strptime(buy_date, "%Y-%m-%d %H:%M:%S")
            days_passed = (datetime.now() - buy_datetime).days

            # Extract scenario information
            scenario_str = stock_data.get('scenario', '{}')
            investment_period = "medium_term"  # Default value

            try:
                if isinstance(scenario_str, str):
                    scenario_data = json.loads(scenario_str)
                    investment_period = scenario_data.get('investment_period', 'medium_term')
            except:
                pass

            # Check stop-loss condition
            if stop_loss > 0 and current_price <= stop_loss:
                return True, f"손절 조건 도달 (손절가: {stop_loss:,.0f}원)"

            # Check target price reached
            if target_price > 0 and current_price >= target_price:
                return True, f"목표가 달성 (목표가: {target_price:,.0f}원)"

            # Sell conditions by investment period
            if investment_period == "short_term":
                # Short-term investment: quicker sell (15+ days holding + 5%+ profit)
                if days_passed >= 15 and profit_rate >= 5:
                    return True, f"단기 투자 목표 달성 (보유: {days_passed}일, 수익률: {profit_rate:.2f}%)"

                # Short-term investment loss protection (10+ days + 3%+ loss)
                if days_passed >= 10 and profit_rate <= -3:
                    return True, f"단기 투자 손실 방어 (보유: {days_passed}일, 수익률: {profit_rate:.2f}%)"

            # Existing sell conditions
            # Sell if profit >= 10%
            if profit_rate >= 10:
                return True, f"수익률 10% 이상 달성 (현재 수익률: {profit_rate:.2f}%)"

            # Sell if loss >= 5%
            if profit_rate <= -5:
                return True, f"손실 -5% 이상 발생 (현재 수익률: {profit_rate:.2f}%)"

            # Sell if holding 30+ days with loss
            if days_passed >= 30 and profit_rate < 0:
                return True, f"30일 이상 보유 중 손실 (보유: {days_passed}일, 수익률: {profit_rate:.2f}%)"

            # Sell if holding 60+ days with 3%+ profit
            if days_passed >= 60 and profit_rate >= 3:
                return True, f"60일 이상 보유 중 3% 이상 수익 (보유: {days_passed}일, 수익률: {profit_rate:.2f}%)"

            # Long-term investment case (90+ days holding + loss)
            if investment_period == "long_term" and days_passed >= 90 and profit_rate < 0:
                return True, f"장기 투자 손실 정리 (보유: {days_passed}일, 수익률: {profit_rate:.2f}%)"

            # Continue holding by default
            return False, "보유 지속"

        except Exception as e:
            logger.error(f"{stock_data.get('ticker', '') if 'ticker' in locals() else 'Unknown stock'} Error analyzing sell: {str(e)}")
            return False, "Analysis error"

    async def sell_stock(self, stock_data: Dict[str, Any], sell_reason: str) -> bool:
        """
        Stock sell processing

        Args:
            stock_data: Stock information to sell
            sell_reason: Sell reason

        Returns:
            bool: Sell success status
        """
        try:
            ticker = stock_data.get('ticker', '')
            company_name = stock_data.get('company_name', '')
            buy_price = stock_data.get('buy_price', 0)
            buy_date = stock_data.get('buy_date', '')
            current_price = stock_data.get('current_price', 0)
            scenario_json = stock_data.get('scenario', '{}')
            trigger_type = stock_data.get('trigger_type', 'AI Analysis')
            trigger_mode = stock_data.get('trigger_mode', 'unknown')

            # Calculate profit rate
            profit_rate = ((current_price - buy_price) / buy_price) * 100

            # Calculate holding period (days)
            buy_datetime = datetime.strptime(buy_date, "%Y-%m-%d %H:%M:%S")
            now_datetime = datetime.now()
            holding_days = (now_datetime - buy_datetime).days

            # Current time
            now = now_datetime.strftime("%Y-%m-%d %H:%M:%S")

            # Add to trading history table
            self.cursor.execute(
                """
                INSERT INTO trading_history
                (ticker, company_name, buy_price, buy_date, sell_price, sell_date, profit_rate, holding_days, scenario, trigger_type, trigger_mode)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ticker,
                    company_name,
                    buy_price,
                    buy_date,
                    current_price,
                    now,
                    profit_rate,
                    holding_days,
                    scenario_json,
                    trigger_type,
                    trigger_mode
                )
            )

            # Remove from holdings
            self.cursor.execute(
                "DELETE FROM stock_holdings WHERE ticker = ?",
                (ticker,)
            )

            # Save changes
            self.conn.commit()

            # Add sell message
            arrow = "⬆️" if profit_rate > 0 else "⬇️" if profit_rate < 0 else "➖"
            message = f"📉 매도: {company_name}({ticker})\n" \
                      f"매수가: {buy_price:,.0f}원\n" \
                      f"매도가: {current_price:,.0f}원\n" \
                      f"수익률: {arrow} {abs(profit_rate):.2f}%\n" \
                      f"보유기간: {holding_days}일\n" \
                      f"매도이유: {sell_reason}"

            # Add trigger win rate
            trigger_type = stock_data.get('trigger_type', '')
            trigger_win_rate = self._get_trigger_win_rate(trigger_type)
            if trigger_win_rate:
                message += f"\n{trigger_win_rate}"

            self._msg_types.append("analysis")
            self.message_queue.append(message)
            logger.info(f"{ticker}({company_name}) sell complete (return: {profit_rate:.2f}%)")

            # Create trading journal entry for retrospective analysis
            try:
                await self._create_journal_entry(
                    stock_data=stock_data,
                    sell_price=current_price,
                    profit_rate=profit_rate,
                    holding_days=holding_days,
                    sell_reason=sell_reason
                )
            except Exception as journal_err:
                # Journal creation failure should not block the sell process
                logger.warning(f"Journal entry creation failed (non-critical): {journal_err}")

            return True

        except Exception as e:
            logger.error(f"Error during sell: {str(e)}")
            logger.error(traceback.format_exc())
            return False

    async def _create_journal_entry(
        self,
        stock_data: Dict[str, Any],
        sell_price: float,
        profit_rate: float,
        holding_days: int,
        sell_reason: str,
        trade_date: Optional[str] = None
    ) -> bool:
        """Create trading journal entry (delegates to tracking.journal.JournalManager)"""
        return await self.journal_manager.create_entry(
            stock_data, sell_price, profit_rate, holding_days, sell_reason, trade_date
        )

    def _extract_principles_from_lessons(
        self, lessons: List[Dict[str, Any]], source_journal_id: int
    ) -> int:
        """Extract principles from lessons (delegates to tracking.journal.JournalManager)"""
        return self.journal_manager.extract_principles(lessons, source_journal_id)

    def _parse_journal_response(self, response: str) -> Dict[str, Any]:
        """Parse journal response (delegates to tracking.journal.JournalManager)"""
        return self.journal_manager._parse_response(response)

    def _get_relevant_journal_context(
        self, ticker: str, sector: str = None, market_condition: str = None,
        trigger_type: str = None
    ) -> str:
        """Get journal context for buy decisions (delegates to tracking.journal.JournalManager)"""
        return self.journal_manager.get_context_for_ticker(ticker, sector, trigger_type)

    def _get_universal_principles(self, limit: int = 10) -> List[str]:
        """Get universal principles (delegates to tracking.journal.JournalManager)"""
        return self.journal_manager.get_universal_principles(limit)

    def _get_score_adjustment_from_context(
        self, ticker: str, sector: str = None, trigger_type: str = None
    ) -> Tuple[float, List[str]]:
        """Calculate score adjustment (delegates to tracking.journal.JournalManager)"""
        return self.journal_manager.get_score_adjustment(ticker, sector, trigger_type)

    async def compress_old_journal_entries(
        self,
        layer1_age_days: int = 7,
        layer2_age_days: int = 30,
        min_entries_for_compression: int = 3
    ) -> Dict[str, Any]:
        """Compress old journal entries (delegates to tracking.compression.CompressionManager)"""
        return await self.compression_manager.compress_old_entries(
            layer1_age_days, layer2_age_days, min_entries_for_compression
        )

    def get_compression_stats(self) -> Dict[str, Any]:
        """Get compression statistics (delegates to tracking.compression.CompressionManager)"""
        return self.compression_manager.get_stats()

    def cleanup_stale_data(
        self,
        max_principles: int = 50,
        max_intuitions: int = 50,
        min_confidence_threshold: float = 0.3,
        stale_days: int = 90,
        archive_layer3_days: int = 365,
        dry_run: bool = False
    ) -> Dict[str, Any]:
        """Clean up stale data (delegates to tracking.compression.CompressionManager)"""
        return self.compression_manager.cleanup_stale_data(
            max_principles, max_intuitions, min_confidence_threshold,
            stale_days, archive_layer3_days, dry_run
        )

    # === Backward compatibility wrappers for tests ===
    def _save_intuition(self, intuition: Dict[str, Any], source_ids: List[int]) -> bool:
        """Save intuition (delegates to tracking.compression.CompressionManager)"""
        return self.compression_manager._save_intuition(intuition, source_ids)

    def _generate_simple_summary(self, entry: Dict[str, Any]) -> str:
        """Generate simple summary (delegates to tracking.compression.CompressionManager)"""
        return self.compression_manager._generate_simple_summary(entry)

    def _format_entries_for_compression(self, entries: List[Dict[str, Any]]) -> str:
        """Format entries for compression (delegates to tracking.compression.CompressionManager)"""
        return self.compression_manager._format_entries_for_compression(entries)

    def _parse_compression_response(self, response: str) -> Dict[str, Any]:
        """Parse compression response (delegates to tracking.compression.CompressionManager)"""
        return self.compression_manager._parse_response(response)

    def _save_principle(
        self, scope: str, scope_context: Optional[str], condition: str,
        action: str, reason: str, priority: str, source_journal_id: int
    ) -> bool:
        """Save principle (delegates to tracking.journal.JournalManager)"""
        return self.journal_manager._save_principle(
            scope, scope_context, condition, action, reason, priority, source_journal_id
        )

    async def update_holdings(self) -> List[Dict[str, Any]]:
        """
        Update holdings information and make sell decisions

        Returns:
            List[Dict]: List of sold stock information
        """
        try:
            logger.info("Starting holdings info update")

            # Query holdings list
            self.cursor.execute(
                """SELECT ticker, company_name, buy_price, buy_date, current_price,
                   scenario, target_price, stop_loss, last_updated,
                   trigger_type, trigger_mode
                   FROM stock_holdings"""
            )
            holdings = [dict(row) for row in self.cursor.fetchall()]

            if not holdings or len(holdings) == 0:
                logger.info("No holdings")
                return []

            sold_stocks = []

            for stock in holdings:
                ticker = stock.get('ticker')
                company_name = stock.get('company_name')

                # Query current stock price
                current_price = await self._get_current_stock_price(ticker)

                if current_price <= 0:
                    old_price = stock.get('current_price', 0)
                    logger.warning(f"{ticker} Current price query failed, keeping previous price: {old_price}")
                    current_price = old_price

                # Update stock price information
                stock['current_price'] = current_price

                # Check scenario JSON string
                scenario_str = stock.get('scenario', '{}')
                try:
                    if isinstance(scenario_str, str):
                        scenario_json = json.loads(scenario_str)

                        # Check and update target price/stop-loss
                        if 'target_price' in scenario_json and stock.get('target_price', 0) == 0:
                            stock['target_price'] = scenario_json['target_price']

                        if 'stop_loss' in scenario_json and stock.get('stop_loss', 0) == 0:
                            stock['stop_loss'] = scenario_json['stop_loss']
                except:
                    logger.warning(f"{ticker} Scenario JSON parse failed")

                # Current time
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                # Analyze sell decision
                should_sell, sell_reason = await self._analyze_sell_decision(stock)

                if should_sell:
                    # 1. 실제 계좌 매도 먼저 수행 (실패 가능성 대비)
                    from trading.domestic_stock_trading import AsyncTradingContext
                    async with AsyncTradingContext() as trading:
                        # Execute async sell with limit price for reserved orders
                        trade_result = await trading.async_sell_stock(stock_code=ticker, limit_price=current_price)

                    if trade_result['success']:
                        logger.info(f"Actual sell successful: {trade_result['message']}")

                        # 2. 실제 매도가 성공한 경우에만 DB 정보 업데이트 (판매 처리)
                        sell_success = await self.sell_stock(stock, sell_reason)
                    else:
                        logger.error(f"Actual sell failed: {trade_result['message']}")
                        sell_success = False

                    if sell_success:
                        sold_stocks.append({
                            "ticker": ticker,
                            "company_name": company_name,
                            "buy_price": stock.get('buy_price', 0),
                            "sell_price": current_price,
                            "profit_rate": (
                                ((current_price - stock.get('buy_price', 0)) / stock.get('buy_price', 1) * 100)
                                if stock.get('buy_price', 0) > 0
                                else 0.0
                            ),
                            "reason": sell_reason
                        })
                else:
                    # Update current price
                    self.cursor.execute(
                        """UPDATE stock_holdings
                           SET current_price = ?, last_updated = ?
                           WHERE ticker = ?""",
                        (current_price, now, ticker)
                    )
                    self.conn.commit()
                    logger.info(f"{ticker}({company_name}) current price updated: {current_price:,.0f} KRW ({sell_reason})")

            return sold_stocks

        except Exception as e:
            logger.error(f"Error updating holdings: {str(e)}")
            logger.error(traceback.format_exc())
            return []

    async def generate_report_summary(self) -> str:
        """
        Generate holdings and profit statistics summary

        Returns:
            str: Summary message
        """
        try:
            # Query holdings
            self.cursor.execute(
                "SELECT ticker, company_name, buy_price, current_price, buy_date, scenario, target_price, stop_loss FROM stock_holdings"
            )
            holdings = [dict(row) for row in self.cursor.fetchall()]

            # Calculate total profit from trading history
            self.cursor.execute("SELECT SUM(profit_rate) FROM trading_history")
            total_profit = self.cursor.fetchone()[0] or 0

            # Number of trades
            self.cursor.execute("SELECT COUNT(*) FROM trading_history")
            total_trades = self.cursor.fetchone()[0] or 0

            # Number of successful/failed trades
            self.cursor.execute("SELECT COUNT(*) FROM trading_history WHERE profit_rate > 0")
            successful_trades = self.cursor.fetchone()[0] or 0

            # 시장 지수 정보 조회
            first_kospi = last_kospi = first_kosdaq = last_kosdaq = 0
            try:
                # first market condition
                self.cursor.execute("select kospi_index, kosdaq_index from  market_condition order by date asc limit 1")
                row = self.cursor.fetchone()
                if row:
                    first_kospi = row[0]
                    first_kosdaq = row[1]

                # last market condition
                self.cursor.execute("select kospi_index, kosdaq_index from  market_condition order by date desc limit 1")
                row = self.cursor.fetchone()
                if row:
                    last_kospi = row[0]
                    last_kosdaq = row[1]
            except Exception as e:
                logger.error(f"시장 지수 조회 중 오류: {str(e)}")
                first_kospi = last_kospi = first_kosdaq = last_kosdaq = 0

            # Generate message
            message = f"📊 프리즘 시뮬레이터 | 실시간 포트폴리오 ({datetime.now().strftime('%Y-%m-%d %H:%M')})\n\n"

            # 1. Portfolio summary
            message += f"🔸 현재 보유: {len(holdings) if holdings else 0}/{self.max_slots}개\n"

            # Best profit/loss stock information (if any)
            if holdings and len(holdings) > 0:
                profit_rates = []
                for h in holdings:
                    buy_price = h.get('buy_price', 0)
                    current_price = h.get('current_price', 0)
                    if buy_price > 0:
                        profit_rate = ((current_price - buy_price) / buy_price) * 100
                        profit_rates.append((h.get('ticker'), h.get('company_name'), profit_rate))

                if profit_rates:
                    best = max(profit_rates, key=lambda x: x[2])
                    worst = min(profit_rates, key=lambda x: x[2])

                    message += f"✅ 최고 수익: {best[1]}({best[0]}) {'+' if best[2] > 0 else ''}{best[2]:.2f}%\n"
                    message += f"⚠️ 최저 수익: {worst[1]}({worst[0]}) {'+' if worst[2] > 0 else ''}{worst[2]:.2f}%\n"

            message += "\n"

            # 2. Sector distribution analysis
            sector_counts = {}

            if holdings and len(holdings) > 0:
                message += f"🔸 보유 종목:\n"
                for stock in holdings:
                    ticker = stock.get('ticker', '')
                    company_name = stock.get('company_name', '')
                    buy_price = stock.get('buy_price', 0)
                    current_price = stock.get('current_price', 0)
                    buy_date = stock.get('buy_date', '')
                    scenario_str = stock.get('scenario', '{}')
                    target_price = stock.get('target_price', 0)
                    stop_loss = stock.get('stop_loss', 0)

                    # Extract sector information from scenario
                    sector = "알 수 없음"
                    try:
                        if isinstance(scenario_str, str):
                            scenario_data = json.loads(scenario_str)
                            sector = scenario_data.get('sector', '알 수 없음')
                    except:
                        pass

                    # Update sector count
                    sector_counts[sector] = sector_counts.get(sector, 0) + 1

                    profit_rate = ((current_price - buy_price) / buy_price) * 100 if buy_price else 0
                    arrow = "⬆️" if profit_rate > 0 else "⬇️" if profit_rate < 0 else "➖"

                    buy_datetime = datetime.strptime(buy_date, "%Y-%m-%d %H:%M:%S") if buy_date else datetime.now()
                    days_passed = (datetime.now() - buy_datetime).days

                    message += f"- {company_name}({ticker}) [{sector}]\n"
                    message += f"  매수가: {buy_price:,.0f}원 / 현재가: {current_price:,.0f}원\n"
                    message += f"  목표가: {target_price:,.0f}원 / 손절가: {stop_loss:,.0f}원\n"
                    message += f"  수익률: {arrow} {profit_rate:.2f}% / 보유기간: {days_passed}일\n\n"

                # Add sector distribution
                message += f"🔸 섹터 분포:\n"
                for sector, count in sector_counts.items():
                    percentage = (count / len(holdings)) * 100
                    message += f"- {sector}: {count}개 ({percentage:.1f}%)\n"
                message += "\n"
            else:
                message += "현재 보유 종목이 없습니다.\n\n"

            # 3. Trading history statistics
            message += f"🔸 매매 이력 통계\n"
            message += f"- 총 거래: {total_trades}건\n"
            message += f"- 수익 거래: {successful_trades}건\n"
            message += f"- 손실 거래: {total_trades - successful_trades}건\n"

            if total_trades > 0:
                message += f"- 승률: {(successful_trades / total_trades * 100):.2f}%\n"
            else:
                message += f"- 승률: 0.00%\n"

            message += f"- 누적 수익률: {total_profit:.2f}%\n\n"

            # 시장 지수 변화
            if first_kospi > 0 and last_kospi > 0:
                kospi_change = ((last_kospi - first_kospi) / first_kospi) * 100
                kosdaq_change = ((last_kosdaq - first_kosdaq) / first_kosdaq) * 100 if first_kosdaq > 0 else 0
                message += "📈 시장 지수 변화:\n"
                message += f"- KOSPI: {first_kospi:.2f} → {last_kospi:.2f} ({'+' if kospi_change >= 0 else ''}{kospi_change:.2f}%)\n"
                if first_kosdaq > 0:
                    message += f"- KOSDAQ: {first_kosdaq:.2f} → {last_kosdaq:.2f} ({'+' if kosdaq_change >= 0 else ''}{kosdaq_change:.2f}%)\n"
                message += "\n\n"
            else:
                message += "\n"

            # 4. Enhanced disclaimer
            message += "📝 주의사항:\n"
            message += "- 본 리포트는 AI 기반 시뮬레이션 결과이며 실제 매매와 무관합니다.\n"
            message += "- 본 정보는 참고용이며, 투자 결정과 책임은 전적으로 투자자에게 있습니다.\n"
            message += "- 본 채널은 종목 추천 및 매매 방이 아닙니다."

            return message

        except Exception as e:
            logger.error(f"Error generating report summary: {str(e)}")
            error_msg = f"Error occurred while generating report: {str(e)}"
            return error_msg

    async def process_reports(self, pdf_report_paths: List[str]) -> Tuple[int, int]:
        """
        Process analysis reports and make buy/sell decisions

        Args:
            pdf_report_paths: List of pdf analysis report file paths

        Returns:
            Tuple[int, int]: Buy count, sell count
        """
        try:
            logger.info(f"Starting processing of {len(pdf_report_paths)} reports")

            # Buy/sell counters
            buy_count = 0
            sell_count = 0

            # 1. Update existing holdings and make sell decisions
            sold_stocks = await self.update_holdings()
            sell_count = len(sold_stocks)

            if sold_stocks:
                logger.info(f"{len(sold_stocks)} stocks sold")
                for stock in sold_stocks:
                    logger.info(f"Sold: {stock['company_name']}({stock['ticker']}) - Return: {stock['profit_rate']:.2f}% / Reason: {stock['reason']}")
            else:
                logger.info("No stocks sold")

            # 2. Analyze new reports and make buy decisions
            for pdf_report_path in pdf_report_paths:
                # Analyze report
                analysis_result = await self.analyze_report(pdf_report_path)

                if not analysis_result.get("success", False):
                    ticker_info = analysis_result.get("company_name", "") or ""
                    if ticker_info:
                        ticker_info = f" [{ticker_info}({analysis_result.get('ticker', '')})]"
                    logger.error(
                        f"Report analysis failed{ticker_info}: {pdf_report_path} "
                        f"- {analysis_result.get('error', 'Unknown error')} (not saved to DB)"
                    )
                    continue

                # Skip if already holding this stock
                if analysis_result.get("decision") == "Already holding":
                    logger.info(f"Skipping stock in holdings: {analysis_result.get('ticker')} - {analysis_result.get('company_name')}")
                    continue

                # Stock information and scenario
                ticker = analysis_result.get("ticker")
                company_name = analysis_result.get("company_name")
                current_price = analysis_result.get("current_price", 0)
                scenario = analysis_result.get("scenario", {})
                sector = analysis_result.get("sector", "Unknown")
                sector_diverse = analysis_result.get("sector_diverse", True)
                rank_change_msg = analysis_result.get("rank_change_msg", "")
                rank_change_percentage = analysis_result.get("rank_change_percentage", 0)

                # Skip if sector diversity check fails
                if not sector_diverse:
                    logger.info(f"Purchase deferred: {company_name}({ticker}) - Preventing sector over-investment")
                    continue

                # Process buy if entry decision
                buy_score = scenario.get("buy_score", 0)
                effective_score = scenario.get("effective_score", buy_score)
                min_score = scenario.get("min_score", 0)
                logger.info(f"Buy score check: {company_name}({ticker}) - Buy Score: {buy_score}, Effective Score: {effective_score}")
                if analysis_result.get("decision") == "Enter":
                    # Process buy
                    buy_success = await self.buy_stock(ticker, company_name, current_price, scenario, rank_change_msg)

                    if buy_success:
                        # Call actual account trading function (async)
                        from trading.domestic_stock_trading import AsyncTradingContext
                        async with AsyncTradingContext() as trading:
                            # Execute async buy with limit price for reserved orders
                            trade_result = await trading.async_buy_stock(stock_code=ticker, limit_price=current_price)

                        if trade_result['success']:
                            logger.info(f"Actual purchase successful: {trade_result['message']}")
                        else:
                            logger.error(f"Actual purchase failed: {trade_result['message']}")

                        # [Optional] Publish buy signal via Redis Streams
                        # Auto-skipped if Redis not configured (requires UPSTASH_REDIS_REST_URL, UPSTASH_REDIS_REST_TOKEN)
                        try:
                            from messaging.redis_signal_publisher import publish_buy_signal
                            await publish_buy_signal(
                                ticker=ticker,
                                company_name=company_name,
                                price=current_price,
                                scenario=scenario,
                                source="AI Analysis",
                                trade_result=trade_result
                            )
                        except Exception as signal_err:
                            logger.warning(f"Buy signal publish failed (non-critical): {signal_err}")

                        # [Optional] Publish buy signal via GCP Pub/Sub
                        # Auto-skipped if GCP not configured (requires GCP_PROJECT_ID, GCP_PUBSUB_TOPIC_ID)
                        try:
                            from messaging.gcp_pubsub_signal_publisher import publish_buy_signal as gcp_publish_buy_signal
                            await gcp_publish_buy_signal(
                                ticker=ticker,
                                company_name=company_name,
                                price=current_price,
                                scenario=scenario,
                                source="AI Analysis",
                                trade_result=trade_result
                            )
                        except Exception as signal_err:
                            logger.warning(f"GCP buy signal publish failed (non-critical): {signal_err}")

                    if buy_success:
                        buy_count += 1
                        logger.info(f"Purchase complete: {company_name}({ticker}) @ {current_price:,.0f} KRW")
                    else:
                        logger.warning(f"Purchase failed: {company_name}({ticker})")
                else:
                    reason = ""
                    if effective_score < min_score:
                        reason = f"Effective score insufficient ({effective_score} < {min_score})"
                    elif analysis_result.get("decision") != "Enter":
                        reason = f"Not an entry decision (Decision: {analysis_result.get('decision')})"

                    logger.info(f"Purchase deferred: {company_name}({ticker}) - {reason}")

            # 3. [New v2.6.0] Re-evaluate Watchlist stocks for breakout/expiration (1+3 Approach)
            # Checked at the very end of process to prevent delaying time-sensitive morning report buys
            watch_buy_count = await self._reevaluate_watch_stocks()
            buy_count += watch_buy_count

            logger.info(f"Report processing complete - Purchased: {buy_count} stocks, Sold: {sell_count} stocks")
            return buy_count, sell_count

        except Exception as e:
            logger.error(f"Error processing reports: {str(e)}")
            logger.error(traceback.format_exc())
            return 0, 0


    async def send_telegram_message(self, chat_id: str, language: str = "ko") -> bool:
        """
        Send message via Telegram

        Args:
            chat_id: Telegram channel ID (no sending if None)
            language: Message language ("ko" or "en")

        Returns:
            bool: Send success status
        """
        try:
            # Skip Telegram sending if chat_id is None
            if not chat_id:
                logger.info("No Telegram channel ID. Skipping message send")

                # Log message output
                for message in self.message_queue:
                    logger.info(f"[Message (not sent)] {message[:100]}...")

                # Initialize message queue
                self.message_queue = []
                self._msg_types = []
                return True  # Consider intentional skip as success

            # If Telegram bot not initialized, only output logs
            if not self.telegram_bot:
                logger.warning("Telegram bot not initialized. Please check token")

                # Only output messages without actual sending
                for message in self.message_queue:
                    logger.info(f"[Telegram message (bot not initialized)] {message[:100]}...")

                # Initialize message queue
                self.message_queue = []
                self._msg_types = []
                return False

            # Generate summary report
            summary = await self.generate_report_summary()
            self._msg_types.append("portfolio")
            self.message_queue.append(summary)

            # Translate messages if English is requested
            if language == "en":
                logger.info(f"Translating {len(self.message_queue)} messages to English")
                try:
                    from cores.agents.telegram_translator_agent import translate_telegram_message
                    translated_queue = []
                    for idx, message in enumerate(self.message_queue, 1):
                        logger.info(f"Translating message {idx}/{len(self.message_queue)}")
                        translated = await translate_telegram_message(message, model="gpt-5-nano")
                        translated_queue.append(translated)
                    self.message_queue = translated_queue
                    logger.info("All messages translated successfully")
                except Exception as e:
                    logger.error(f"Translation failed: {str(e)}. Using original Korean messages.")

            # Send each message
            success = True
            for message in self.message_queue:
                logger.info(f"Sending Telegram message: {chat_id}")
                try:
                    # Telegram message length limit (4096 characters)
                    MAX_MESSAGE_LENGTH = 4096

                    if len(message) <= MAX_MESSAGE_LENGTH:
                        # Send in one message if short
                        await self.telegram_bot.send_message(
                            chat_id=chat_id,
                            text=message
                        )
                    else:
                        # Split and send if long
                        parts = []
                        current_part = ""

                        for line in message.split('\n'):
                            if len(current_part) + len(line) + 1 <= MAX_MESSAGE_LENGTH:
                                current_part += line + '\n'
                            else:
                                if current_part:
                                    parts.append(current_part.rstrip())
                                current_part = line + '\n'

                        if current_part:
                            parts.append(current_part.rstrip())

                        # Send split messages
                        for i, part in enumerate(parts, 1):
                            await self.telegram_bot.send_message(
                                chat_id=chat_id,
                                text=f"[{i}/{len(parts)}]\n{part}"
                            )
                            await asyncio.sleep(0.5)  # Short delay between split messages

                    logger.info(f"Telegram message sent: {chat_id}")
                except TelegramError as e:
                    logger.error(f"Telegram message send failed: {e}")
                    success = False

                # Delay to prevent API rate limiting
                await asyncio.sleep(1)

            # Send to broadcast channels if configured (awaited in run() finally block)
            if hasattr(self, 'telegram_config') and self.telegram_config and self.telegram_config.broadcast_languages:
                self._broadcast_task = asyncio.create_task(self._send_to_translation_channels(self.message_queue.copy(), self._msg_types.copy()))
                logger.info("Broadcast channel translation dispatched")

            # Clear message queue
            self.message_queue = []
            self._msg_types = []

            return success

        except Exception as e:
            logger.error(f"Error sending Telegram message: {str(e)}")
            logger.error(traceback.format_exc())
            return False

    async def _send_to_translation_channels(self, messages: List[str], msg_types: Optional[list] = None):
        """
        Send messages to translation channels

        Args:
            messages: List of original Korean messages
            msg_types: msg_type for each message in the list
        """
        try:
            from cores.agents.telegram_translator_agent import translate_telegram_message

            for lang in self.telegram_config.broadcast_languages:
                try:
                    # Get channel ID for this language
                    channel_id = self.telegram_config.get_broadcast_channel_id(lang)
                    if not channel_id:
                        logger.warning(f"No channel ID configured for language: {lang}")
                        continue

                    logger.info(f"Sending tracking messages to {lang} channel")

                    # Translate and send each message
                    for message in messages:
                        try:
                            # Translate message
                            logger.info(f"Translating tracking message to {lang}")
                            translated_message = await translate_telegram_message(
                                message,
                                model="gpt-5-nano",
                                from_lang="ko",
                                to_lang=lang
                            )

                            # Send translated message
                            MAX_MESSAGE_LENGTH = 4096

                            if len(translated_message) <= MAX_MESSAGE_LENGTH:
                                await self.telegram_bot.send_message(
                                    chat_id=channel_id,
                                    text=translated_message
                                )
                            else:
                                # Split long messages
                                parts = []
                                current_part = ""

                                for line in translated_message.split('\n'):
                                    if len(current_part) + len(line) + 1 <= MAX_MESSAGE_LENGTH:
                                        current_part += line + '\n'
                                    else:
                                        if current_part:
                                            parts.append(current_part.rstrip())
                                        current_part = line + '\n'

                                if current_part:
                                    parts.append(current_part.rstrip())

                                # Send split messages
                                for i, part in enumerate(parts, 1):
                                    await self.telegram_bot.send_message(
                                        chat_id=channel_id,
                                        text=f"[{i}/{len(parts)}]\n{part}"
                                    )
                                    await asyncio.sleep(0.5)

                            logger.info(f"Tracking message sent successfully to {lang} channel")
                            await asyncio.sleep(1)

                        except Exception as e:
                            logger.error(f"Error sending tracking message to {lang}: {str(e)}")

                except Exception as e:
                    logger.error(f"Error processing language {lang}: {str(e)}")

        except Exception as e:
            logger.error(f"Error in _send_to_translation_channels: {str(e)}")

    async def run(self, pdf_report_paths: List[str], chat_id: str = None, language: str = "ko", telegram_config=None, trigger_results_file: str = None) -> bool | None:
        """
        Main execution function for stock tracking system

        Args:
            pdf_report_paths: List of analysis report file paths
            chat_id: Telegram channel ID (no messages sent if None)
            language: Message language ("ko" or "en")
            telegram_config: TelegramConfig object for multi-language support
            trigger_results_file: Path to trigger results JSON file for tracking trigger types

        Returns:
            bool: Execution success status
        """
        try:
            logger.info("Starting tracking system batch execution")

            # Store telegram_config for use in send_telegram_message
            self.telegram_config = telegram_config

            # Load trigger type mapping from trigger_results file
            self.trigger_info_map = {}
            if trigger_results_file:
                try:
                    import os
                    if os.path.exists(trigger_results_file):
                        with open(trigger_results_file, 'r', encoding='utf-8') as f:
                            trigger_data = json.load(f)
                        # Build ticker -> trigger info mapping
                        for trigger_type, stocks in trigger_data.items():
                            if trigger_type == 'metadata':
                                self.trigger_mode = trigger_data.get('metadata', {}).get('trigger_mode', '')
                                continue
                            if isinstance(stocks, list):
                                for stock in stocks:
                                    ticker = stock.get('code', '')
                                    if ticker:
                                        self.trigger_info_map[ticker] = {
                                            'trigger_type': trigger_type,
                                            'trigger_mode': trigger_data.get('metadata', {}).get('trigger_mode', ''),
                                            'risk_reward_ratio': stock.get('risk_reward_ratio', 0),
                                            'pivot_point': stock.get('pivot_point', 0),
                                            'volume_profile_info': stock.get('volume_profile_info', 'No significant upper resistance')
                                        }
                        logger.info(f"Loaded trigger info for {len(self.trigger_info_map)} stocks")
                except Exception as e:
                    logger.warning(f"Failed to load trigger results file: {e}")

            # Initialize with language parameter
            await self.initialize(language)

            try:
                # Process reports
                buy_count, sell_count = await self.process_reports(pdf_report_paths)

                # Send Telegram message (only if chat_id is provided)
                if chat_id:
                    message_sent = await self.send_telegram_message(chat_id, language)
                    if message_sent:
                        logger.info("Telegram message sent successfully")
                    else:
                        logger.warning("Telegram message send failed")
                else:
                    logger.info("Telegram channel ID not provided, skipping message send")
                    # Call even if chat_id is None to clean up message queue
                    await self.send_telegram_message(None, language)

                logger.info("Tracking system batch execution complete")
                return True
            finally:
                # Wait for broadcast translation task before cleanup
                if self._broadcast_task:
                    try:
                        logger.info("Waiting for tracking broadcast translation to complete...")
                        await self._broadcast_task
                        logger.info("Tracking broadcast translation completed")
                    except Exception as e:
                        logger.error(f"Tracking broadcast translation failed: {e}")
                    self._broadcast_task = None

                # Ensure connection is always closed
                if self.conn:
                    self.conn.close()
                    logger.info("Database connection closed")

        except Exception as e:
            logger.error(f"Error during tracking system execution: {str(e)}")
            logger.error(traceback.format_exc())

            # Check and close database connection
            if hasattr(self, 'conn') and self.conn:
                try:
                    self.conn.close()
                    logger.info("Database connection closed after error")
                except:
                    pass

            return False

async def main():
    """Main function"""
    import argparse
    import logging

    # Get logger
    local_logger = logging.getLogger(__name__)

    parser = argparse.ArgumentParser(description="Stock tracking and trading agent")
    parser.add_argument("--reports", nargs="+", help="List of analysis report file paths")
    parser.add_argument("--chat-id", help="Telegram channel ID")
    parser.add_argument("--telegram-token", help="Telegram bot token")

    args = parser.parse_args()

    if not args.reports:
        local_logger.error("Report path not specified")
        return False

    async with app.run():
        agent = StockTrackingAgent(telegram_token=args.telegram_token)
        success = await agent.run(args.reports, args.chat_id)

        return success

if __name__ == "__main__":
    try:
        # Execute asyncio
        asyncio.run(main())
    except Exception as e:
        logger.error(f"Error during program execution: {str(e)}")
        logger.error(traceback.format_exc())
        sys.exit(1)
