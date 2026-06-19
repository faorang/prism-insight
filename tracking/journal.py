"""
Trading Journal Manager

Handles trading journal creation, principle extraction, and context retrieval.
Extracted from stock_tracking_agent.py for LLM context efficiency.
"""

import json
import logging
import re
import traceback
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from cores.utils import parse_llm_json
from tracking.helpers import SECTOR_GROUPS

logger = logging.getLogger(__name__)


class JournalManager:
    """Manages trading journal operations."""

    def __init__(self, cursor, conn, language: str = "ko", enable_journal: bool = False):
        """
        Initialize JournalManager.

        Args:
            cursor: SQLite cursor
            conn: SQLite connection
            language: Language code (ko/en)
            enable_journal: Whether journal feature is enabled
        """
        self.cursor = cursor
        self.conn = conn
        self.language = language
        self.enable_journal = enable_journal

    def _get_compatible_sectors(self, sector: str) -> list:
        """Get all equivalent sector names for database query matching."""
        if not sector or sector == "Unknown":
            return ["Unknown"]
            
        import re
        sector_clean = re.sub(r'[\s·,\-/]', '', sector.lower())
        
        for group in SECTOR_GROUPS:
            norm_group = {re.sub(r'[\s·,\-/]', '', item.lower()) for item in group}
            if sector_clean in norm_group:
                return list(group)
                
        return [sector]

    async def create_entry(
        self,
        stock_data: Dict[str, Any],
        sell_price: float,
        profit_rate: float,
        holding_days: int,
        sell_reason: str,
        trade_date: Optional[str] = None
    ) -> bool:
        """
        Create trading journal entry with retrospective analysis.

        Args:
            stock_data: Original stock data including buy info
            sell_price: Price at which the stock was sold
            profit_rate: Realized profit/loss percentage
            holding_days: Number of days the stock was held
            sell_reason: Reason for selling

        Returns:
            bool: True if journal entry was created successfully
        """
        if not self.enable_journal:
            logger.debug("Trading journal is disabled")
            return False

        try:
            from cores.agents.trading_journal_agent import create_trading_journal_agent
            from mcp_agent.workflows.llm.augmented_llm import RequestParams
            from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM

            ticker = stock_data.get('ticker', '')
            company_name = stock_data.get('company_name', '')
            buy_price = stock_data.get('buy_price', 0)
            buy_date = stock_data.get('buy_date', '')
            scenario_json = stock_data.get('scenario', '{}')

            logger.info(f"Creating journal entry for {ticker}({company_name})")

            # Parse scenario
            scenario_data = {}
            if isinstance(scenario_json, str):
                try:
                    scenario_data = json.loads(scenario_json)
                except (json.JSONDecodeError, TypeError):
                    scenario_data = {}

            # Create journal agent
            journal_agent = create_trading_journal_agent(self.language)

            async with journal_agent:
                llm = await journal_agent.attach_llm(OpenAIAugmentedLLM)

                prompt = self._build_analysis_prompt(
                    company_name, ticker, buy_price, buy_date,
                    scenario_data, sell_price, profit_rate, holding_days, sell_reason
                )

                response = await llm.generate_str(
                    message=prompt,
                    request_params=RequestParams(model="gpt-5.4-mini", reasoning_effort="none", maxTokens=16000)
                )
                logger.info(f"Journal agent response received: {len(response)} chars")

            # Parse and save
            journal_data = self._parse_response(response)
            journal_id = self._save_to_database(
                ticker, company_name, buy_price, buy_date, scenario_json,
                scenario_data, sell_price, sell_reason, profit_rate,
                holding_days, journal_data, trade_date
            )

            logger.info(f"Journal entry created for {ticker}: {journal_data.get('one_line_summary', '')}")

            # Extract principles
            lessons = journal_data.get('lessons', [])
            if lessons and journal_id > 0:
                extracted_count = self.extract_principles(lessons, journal_id)
                logger.info(f"Extracted {extracted_count} principles from journal {journal_id}")

            return True

        except Exception as e:
            logger.error(f"Error creating journal entry: {str(e)}")
            logger.error(traceback.format_exc())
            return False

    async def create_skip_entry(
        self,
        stock_data: Dict[str, Any],
        tracking_data: Dict[str, Any],
        trade_date: Optional[str] = None
    ) -> bool:
        """
        Create trading journal entry for skipped stocks with retrospective analysis.

        Args:
            stock_data: Original stock analysis data including buy score, min score, etc.
            tracking_data: Tracked price and yield information (7d, 14d, 30d)
            trade_date: Optional specific trade date

        Returns:
            bool: True if journal entry was created successfully
        """
        if not self.enable_journal:
            logger.debug("Trading journal is disabled")
            return False

        try:
            from cores.agents.trading_journal_agent import create_trading_journal_agent
            from mcp_agent.workflows.llm.augmented_llm import RequestParams
            from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM

            ticker = stock_data.get('ticker', '')
            company_name = stock_data.get('company_name', '')
            buy_price = stock_data.get('buy_price') or stock_data.get('analyzed_price', 0)
            buy_date = stock_data.get('buy_date') or stock_data.get('analyzed_date', '')
            target_price = stock_data.get('target_price') or 0
            stop_loss = stock_data.get('stop_loss') or 0
            scenario_json = stock_data.get('scenario', '{}')

            # Strict validation: Do not journal without valid original analysis context, target price, or stop loss
            if not scenario_json or scenario_json == '{}' or scenario_json == 'None':
                logger.warning(f"[{ticker}] Skipped stock journaling aborted: Missing original scenario JSON.")
                return False
            
            if target_price <= 0 or stop_loss <= 0:
                logger.warning(f"[{ticker}] Skipped stock journaling aborted: Invalid target_price ({target_price}) or stop_loss ({stop_loss}).")
                return False

            # Prevent duplicate skipped stock journals to avoid redundant LLM calls and DB records
            self.cursor.execute(
                "SELECT id FROM trading_journal WHERE ticker = ? AND buy_date = ? AND trade_type = 'skip'",
                (ticker, buy_date)
            )
            if self.cursor.fetchone():
                logger.info(f"Journal entry for skipped stock {ticker}({company_name}) on {buy_date} already exists. Skipping duplicate.")
                return True

            # Parse scenario if string
            scenario_data = {}
            if isinstance(scenario_json, str):
                try:
                    scenario_data = json.loads(scenario_json)
                except (json.JSONDecodeError, TypeError):
                    scenario_data = {}
            elif isinstance(scenario_json, dict):
                scenario_data = scenario_json
                scenario_json = json.dumps(scenario_json, ensure_ascii=False)

            logger.info(f"Creating skipped journal entry for {ticker}({company_name})")

            # Create journal agent
            journal_agent = create_trading_journal_agent(self.language)

            async with journal_agent:
                llm = await journal_agent.attach_llm(OpenAIAugmentedLLM)

                prompt = self._build_skip_analysis_prompt(
                    company_name, ticker, buy_price, buy_date,
                    scenario_data, tracking_data, stock_data
                )

                response = await llm.generate_str(
                    message=prompt,
                    request_params=RequestParams(model="gpt-5.4-mini", reasoning_effort="none", maxTokens=16000)
                )
                logger.info(f"Journal agent response received for skipped stock: {len(response)} chars")

            # Parse and save
            journal_data = self._parse_response(response)
            
            # Use 30d tracking yield (represented in percent, e.g. 0.15 -> 15.0%)
            profit_rate = tracking_data.get('tracked_30d_return', 0.0) * 100.0
            sell_price = tracking_data.get('tracked_30d_price', 0.0)
            skip_reason = stock_data.get('skip_reason', '') or stock_data.get('decision_reason', 'Low Score')

            journal_id = self._save_to_database(
                ticker=ticker,
                company_name=company_name,
                buy_price=buy_price,
                buy_date=buy_date,
                scenario_json=scenario_json,
                scenario_data=scenario_data,
                sell_price=sell_price,
                sell_reason=skip_reason,
                profit_rate=profit_rate,
                holding_days=30,
                journal_data=journal_data,
                trade_date=trade_date,
                trade_type='skip'
            )

            logger.info(f"Skipped journal entry created for {ticker}: {journal_data.get('one_line_summary', '')}")

            # Extract principles
            lessons = journal_data.get('lessons', [])
            if lessons and journal_id > 0:
                extracted_count = self.extract_principles(lessons, journal_id)
                logger.info(f"Extracted {extracted_count} principles from skipped journal {journal_id}")

            return True

        except Exception as e:
            logger.error(f"Error creating skipped journal entry: {str(e)}")
            logger.error(traceback.format_exc())
            return False

    def _build_skip_analysis_prompt(
        self, company_name: str, ticker: str, buy_price: float, buy_date: str,
        scenario_data: Dict, tracking_data: Dict, stock_data: Dict
    ) -> str:
        """Build prompt for retrospective analysis of skipped stock."""
        buy_score = stock_data.get('buy_score', scenario_data.get('buy_score', 'N/A'))
        min_score = stock_data.get('min_score', scenario_data.get('min_score', 'N/A'))
        skip_reason = stock_data.get('skip_reason', '') or stock_data.get('decision_reason', 'Low Score')
        target_price = stock_data.get('target_price') or scenario_data.get('target_price', 'N/A')
        stop_loss = stock_data.get('stop_loss') or scenario_data.get('stop_loss', 'N/A')

        r7 = tracking_data.get('tracked_7d_return', 0.0) * 100.0
        r14 = tracking_data.get('tracked_14d_return', 0.0) * 100.0
        r30 = tracking_data.get('tracked_30d_return', 0.0) * 100.0
        p30 = tracking_data.get('tracked_30d_price', 0.0)

        # Note: We use English templates for LLM prompt consistency, regardless of target language.
        return f"""
Please review the following skipped trade opportunity (this stock was analyzed but not purchased):

## Analysis (Skipped) Information
- Stock: {company_name}({ticker})
- Analysis Price (Base): {buy_price:,.0f} KRW
- Analysis Date: {buy_date}
- Skip Reason: {skip_reason}
- Scenario & Criteria:
  - Buy Score: {buy_score}
  - Min Required Score: {min_score}
  - Target Price: {target_price} KRW
  - Stop Loss: {stop_loss} KRW
  - Rationale: {scenario_data.get('rationale', 'N/A')}
  - Sector: {scenario_data.get('sector', 'N/A')}
  - Market Condition: {scenario_data.get('market_condition', 'N/A')}

## Post-Tracking Performance (30 Days)
- Tracked 7d Return: {r7:+.2f}%
- Tracked 14d Return: {r14:+.2f}%
- Tracked 30d Return: {r30:+.2f}%
- Final Tracked Price (30d): {p30:,.0f} KRW

## Analysis Request
1. Compare the analysis-time context with the subsequent 30-day price trend.
2. Evaluate the decision to skip this purchase:
   - If it went up (Missed Opportunity): Why did we skip it? Was the scoring algorithm too conservative? Did we fail to weigh key positive indicators?
   - If it went down (Avoided Loss): Did our filters/criteria work correctly? What specific warning signals did we identify that saved us from losses?
3. Extract lessons and actionable guidelines for future buy decision scoring.
4. Assign pattern tags (e.g., "missed_rally", "correct_avoidance", "underpriced").
"""

    def _build_analysis_prompt(
        self, company_name: str, ticker: str, buy_price: float, buy_date: str,
        scenario_data: Dict, sell_price: float, profit_rate: float,
        holding_days: int, sell_reason: str
    ) -> str:
        """Build prompt for retrospective analysis."""
        if self.language == "ko":
            return f"""
Please review the following completed trade:

## Buy Information
- Stock: {company_name}({ticker})
- Buy Price: {buy_price:,.0f} KRW
- Buy Date: {buy_date}
- Buy Scenario:
  - Buy Score: {scenario_data.get('buy_score', 'N/A')}
  - Rationale: {scenario_data.get('rationale', 'N/A')}
  - Target Price: {scenario_data.get('target_price', 'N/A')} KRW
  - Stop Loss: {scenario_data.get('stop_loss', 'N/A')} KRW
  - Investment Period: {scenario_data.get('investment_period', 'N/A')}
  - Sector: {scenario_data.get('sector', 'N/A')}
  - Market Condition: {scenario_data.get('market_condition', 'N/A')}

## Sell Information
- Sell Price: {sell_price:,.0f} KRW
- Profit Rate: {profit_rate:.2f}%
- Holding Days: {holding_days} days
- Sell Reason: {sell_reason}

## Analysis Request
1. Use kospi_kosdaq tools to check current market conditions and stock trends
2. Compare and analyze buy-time vs sell-time situations
3. Evaluate decision appropriateness and extract lessons
4. Assign pattern tags
"""
        else:
            return f"""
Please review the following completed trade:

## Buy Information
- Stock: {company_name}({ticker})
- Buy Price: {buy_price:,.0f} KRW
- Buy Date: {buy_date}
- Buy Scenario:
  - Buy Score: {scenario_data.get('buy_score', 'N/A')}
  - Rationale: {scenario_data.get('rationale', 'N/A')}
  - Target Price: {scenario_data.get('target_price', 'N/A')} KRW
  - Stop Loss: {scenario_data.get('stop_loss', 'N/A')} KRW
  - Investment Period: {scenario_data.get('investment_period', 'N/A')}
  - Sector: {scenario_data.get('sector', 'N/A')}
  - Market Condition: {scenario_data.get('market_condition', 'N/A')}

## Sell Information
- Sell Price: {sell_price:,.0f} KRW
- Profit Rate: {profit_rate:.2f}%
- Holding Days: {holding_days} days
- Sell Reason: {sell_reason}

## Analysis Request
1. Use kospi_kosdaq tools to check current market and stock trends
2. Compare buy time vs sell time situations
3. Evaluate decisions and extract lessons
4. Assign pattern tags
"""

    def _parse_response(self, response: str) -> Dict[str, Any]:
        """Parse journal agent response into structured data."""
        result = parse_llm_json(response, context='journal response')
        if result is not None:
            return result
        logger.error(f"Journal response parse failed. Full response: {response}")
        return {
            "situation_analysis": {"raw_response": response[:500]},
            "judgment_evaluation": {},
            "lessons": [],
            "pattern_tags": [],
            "one_line_summary": "Analysis parsing failed",
            "confidence_score": 0.3
        }

    def _save_to_database(
        self, ticker: str, company_name: str, buy_price: float, buy_date: str,
        scenario_json: str, scenario_data: Dict, sell_price: float, sell_reason: str,
        profit_rate: float, holding_days: int, journal_data: Dict,
        trade_date: Optional[str] = None, trade_type: str = 'sell'
    ) -> int:
        """Save journal entry to database."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        actual_trade_date = trade_date or now
        self.cursor.execute(
            """
            INSERT INTO trading_journal
            (ticker, company_name, trade_date, trade_type,
             buy_price, buy_date, buy_scenario, buy_market_context,
             sell_price, sell_reason, profit_rate, holding_days,
             situation_analysis, judgment_evaluation, lessons, pattern_tags,
             one_line_summary, confidence_score, compression_layer, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ticker, company_name, actual_trade_date, trade_type,
                buy_price, buy_date, scenario_json,
                json.dumps(scenario_data.get('market_condition', ''), ensure_ascii=False),
                sell_price, sell_reason, profit_rate, holding_days,
                json.dumps(journal_data.get('situation_analysis', {}), ensure_ascii=False),
                json.dumps(journal_data.get('judgment_evaluation', {}), ensure_ascii=False),
                json.dumps(journal_data.get('lessons', []), ensure_ascii=False),
                json.dumps(journal_data.get('pattern_tags', []), ensure_ascii=False),
                journal_data.get('one_line_summary', ''),
                journal_data.get('confidence_score', 0.5),
                1, now
            )
        )
        self.conn.commit()
        return self.cursor.lastrowid

    def extract_principles(self, lessons: List[Dict[str, Any]], source_journal_id: int) -> int:
        """Extract universal principles from lessons."""
        extracted_count = 0

        for lesson in lessons:
            if not isinstance(lesson, dict):
                continue

            condition = lesson.get('condition', '')
            action = lesson.get('action', '')
            reason = lesson.get('reason', '')
            priority = lesson.get('priority', 'medium')

            if not condition or not action:
                continue

            scope = 'universal' if priority == 'high' else 'sector'

            if self._save_principle(scope, None, condition, action, reason, priority, source_journal_id):
                extracted_count += 1

        return extracted_count

    def _save_principle(
        self, scope: str, scope_context: Optional[str], condition: str,
        action: str, reason: str, priority: str, source_journal_id: int
    ) -> bool:
        """Save a principle to database."""
        try:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            self.cursor.execute("""
                SELECT id, supporting_trades, source_journal_ids
                FROM trading_principles
                WHERE condition = ? AND action = ? AND is_active = 1
            """, (condition, action))

            existing = self.cursor.fetchone()

            if existing:
                existing_ids = existing[2] or ''
                new_ids = f"{existing_ids},{source_journal_id}" if existing_ids else str(source_journal_id)

                self.cursor.execute("""
                    UPDATE trading_principles
                    SET supporting_trades = supporting_trades + 1,
                        confidence = MIN(1.0, confidence + 0.1),
                        source_journal_ids = ?,
                        last_validated_at = ?
                    WHERE id = ?
                """, (new_ids, now, existing[0]))
            else:
                self.cursor.execute("""
                    INSERT INTO trading_principles
                    (scope, scope_context, condition, action, reason, priority,
                     confidence, supporting_trades, source_journal_ids, created_at, is_active)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (scope, scope_context, condition, action, reason, priority,
                      0.5, 1, str(source_journal_id), now, 1))

            self.conn.commit()
            return True

        except Exception as e:
            logger.error(f"Error saving principle: {e}")
            return False

    def get_performance_tracker_stats(self, trigger_type: str = None) -> Dict[str, Any]:
        """
        Get performance statistics from analysis_performance_tracker.

        Queries actual 7/14/30-day returns for all analyzed stocks (both traded and watched)
        to provide ground-truth performance data for buy decisions.

        Args:
            trigger_type: Filter by trigger type (optional)

        Returns:
            Dict with trigger stats, missed opportunities, and overall stats
        """
        stats = {}
        try:
            # Check if table exists
            self.cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='analysis_performance_tracker'"
            )
            if not self.cursor.fetchone():
                return stats

            # 1. Stats for the current trigger type (if provided)
            if trigger_type:
                self.cursor.execute("""
                    SELECT
                        COUNT(*) as total,
                        SUM(CASE WHEN tracked_30d_return > 0 THEN 1 ELSE 0 END) as wins,
                        AVG(tracked_7d_return) as avg_7d,
                        AVG(tracked_14d_return) as avg_14d,
                        AVG(tracked_30d_return) as avg_30d
                    FROM analysis_performance_tracker
                    WHERE trigger_type = ? AND tracking_status = 'completed'
                """, (trigger_type,))
                row = self.cursor.fetchone()
                if row and row[0] > 0:
                    stats['current_trigger'] = {
                        'trigger_type': trigger_type,
                        'total': row[0],
                        'win_rate': row[1] / row[0] if row[0] > 0 else 0,
                        'avg_7d': row[2],
                        'avg_14d': row[3],
                        'avg_30d': row[4],
                    }

            # 2. Missed opportunities: stocks we skipped but went up
            self.cursor.execute("""
                SELECT
                    COUNT(*) as total_skipped,
                    SUM(CASE WHEN tracked_30d_return > 5 THEN 1 ELSE 0 END) as missed_gains,
                    AVG(CASE WHEN tracked_30d_return > 5 THEN tracked_30d_return END) as avg_missed_gain
                FROM analysis_performance_tracker
                WHERE was_traded = 0 AND tracking_status = 'completed'
                    AND tracked_30d_return IS NOT NULL
            """)
            row = self.cursor.fetchone()
            if row and row[0] > 0:
                stats['missed_opportunities'] = {
                    'total_skipped': row[0],
                    'missed_gains_count': row[1] or 0,
                    'avg_missed_gain': row[2],
                }

            # 3. Traded vs watched comparison
            self.cursor.execute("""
                SELECT
                    was_traded,
                    COUNT(*) as count,
                    AVG(tracked_30d_return) as avg_30d
                FROM analysis_performance_tracker
                WHERE tracking_status = 'completed' AND tracked_30d_return IS NOT NULL
                GROUP BY was_traded
            """)
            traded_vs_watched = {}
            for row in self.cursor.fetchall():
                key = 'traded' if row[0] else 'watched'
                traded_vs_watched[key] = {
                    'count': row[1],
                    'avg_30d': row[2],
                }
            if traded_vs_watched:
                stats['traded_vs_watched'] = traded_vs_watched

            # 4. All trigger types performance ranking
            self.cursor.execute("""
                SELECT
                    trigger_type,
                    COUNT(*) as total,
                    SUM(CASE WHEN tracked_30d_return > 0 THEN 1 ELSE 0 END) as wins,
                    AVG(tracked_30d_return) as avg_30d
                FROM analysis_performance_tracker
                WHERE tracking_status = 'completed' AND tracked_30d_return IS NOT NULL
                    AND trigger_type IS NOT NULL
                GROUP BY trigger_type
                HAVING total >= 3
                ORDER BY avg_30d DESC
            """)
            trigger_ranking = []
            for row in self.cursor.fetchall():
                trigger_ranking.append({
                    'trigger_type': row[0],
                    'total': row[1],
                    'win_rate': row[2] / row[1] if row[1] > 0 else 0,
                    'avg_30d': row[3],
                })
            if trigger_ranking:
                stats['trigger_ranking'] = trigger_ranking

            # 5. Skip accuracy by trigger type (how often our skips were correct)
            self.cursor.execute("""
                SELECT trigger_type,
                       COUNT(*) as total_skipped,
                       SUM(CASE WHEN tracked_30d_return < 0 THEN 1 ELSE 0 END) as correct_skips
                FROM analysis_performance_tracker
                WHERE was_traded = 0 AND tracking_status = 'completed'
                    AND tracked_30d_return IS NOT NULL AND trigger_type IS NOT NULL
                GROUP BY trigger_type
                HAVING total_skipped >= 3
            """)
            skip_accuracy = []
            for row in self.cursor.fetchall():
                skip_accuracy.append({
                    'trigger_type': row[0],
                    'total_skipped': row[1],
                    'correct_skip_rate': row[2] / row[1] if row[1] > 0 else 0,
                })
            if skip_accuracy:
                stats['skip_accuracy_by_trigger'] = skip_accuracy

        except Exception as e:
            logger.warning(f"Failed to get performance tracker stats: {e}")

        return stats

    def _format_performance_context(self, stats: Dict[str, Any]) -> List[str]:
        """Format performance tracker stats into context strings for the trading agent."""
        parts = []
        if not stats:
            return parts

        parts.append("#### 📈 Analysis Performance Tracker (Actual Results)")

        # Current trigger type stats
        if 'current_trigger' in stats:
            t = stats['current_trigger']
            win_pct = t['win_rate'] * 100
            avg_30d = t['avg_30d']
            avg_30d_str = f"{avg_30d * 100:+.1f}%" if avg_30d is not None else "N/A"
            parts.append(
                f"- **This trigger ({t['trigger_type']})**: "
                f"Win rate {win_pct:.0f}% (n={t['total']}), "
                f"30d avg return {avg_30d_str}"
            )

        # Trigger ranking
        if 'trigger_ranking' in stats:
            parts.append("- **Trigger Performance Ranking (30d, n>=3):**")
            for rank, t in enumerate(stats['trigger_ranking'][:5], 1):
                avg_30d_str = f"{t['avg_30d'] * 100:+.1f}%" if t['avg_30d'] is not None else "N/A"
                win_pct = t['win_rate'] * 100
                parts.append(
                    f"  {rank}. {t['trigger_type']}: "
                    f"{avg_30d_str} avg, {win_pct:.0f}% win (n={t['total']})"
                )

        # Missed opportunities (D): skipped stocks that gained >5%
        if 'missed_opportunities' in stats:
            m = stats['missed_opportunities']
            if m['missed_gains_count'] and m['missed_gains_count'] > 0:
                avg_str = f"{m['avg_missed_gain'] * 100:+.1f}%" if m['avg_missed_gain'] is not None else "N/A"
                parts.append(
                    f"- ⚠️ **Missed Opportunities**: {m['missed_gains_count']} skipped stocks "
                    f"gained >5% in 30d (avg {avg_str}) out of {m['total_skipped']} total skips"
                )

        # Traded vs Watched comparison (E): are our filters selecting well?
        if 'traded_vs_watched' in stats:
            tw = stats['traded_vs_watched']
            traded = tw.get('traded', {})
            watched = tw.get('watched', {})
            if traded and watched:
                t_avg = f"{traded['avg_30d'] * 100:+.1f}%" if traded.get('avg_30d') is not None else "N/A"
                w_avg = f"{watched['avg_30d'] * 100:+.1f}%" if watched.get('avg_30d') is not None else "N/A"
                parts.append(
                    f"- **Traded vs Skipped 30d avg**: "
                    f"Traded {t_avg} (n={traded.get('count', 0)}) vs "
                    f"Skipped {w_avg} (n={watched.get('count', 0)})"
                )

        # Skip accuracy by trigger (F): how often skips were correct
        if 'skip_accuracy_by_trigger' in stats:
            parts.append("- **Skip Accuracy by Trigger (% of skips that actually declined, n>=3):**")
            for sa in stats['skip_accuracy_by_trigger']:
                accuracy_pct = sa['correct_skip_rate'] * 100
                emoji = "✅" if accuracy_pct >= 60 else "⚠️" if accuracy_pct >= 40 else "❌"
                parts.append(
                    f"  {emoji} {sa['trigger_type']}: "
                    f"{accuracy_pct:.0f}% correct skips (n={sa['total_skipped']})"
                )

        parts.append("")
        return parts

    def get_context_for_ticker(self, ticker: str, sector: str = None, trigger_type: str = None) -> str:
        """Retrieve relevant trading journal context for buy decisions.
        
        Optimized to load lightweight columns, fetch identical ticker records first,
        and fallback to 1 extreme profit/loss record from the same sector if empty.
        Additionally, dynamically filters and injects matched intuitions up to 3.
        """
        if not self.enable_journal:
            return ""

        try:
            context_parts = []

            # 1. Performance tracker stats (ground truth data, no LLM cost)
            perf_stats = self.get_performance_tracker_stats(trigger_type)
            perf_context = self._format_performance_context(perf_stats)
            if perf_context:
                context_parts.extend(perf_context)

            # 2. Universal principles (top 5 verified ones)
            principles = self.get_universal_principles()
            if principles:
                context_parts.append("#### 🎯 Core Trading Principles (Applied to All Trades)")
                context_parts.extend(principles)
                context_parts.append("")

            # 3. Same stock history & Sector fallback
            retrieved_entries = []

            # 1순위: 동일 종목 이력 조회 (최신 3건)
            self.cursor.execute("""
                SELECT ticker, company_name, profit_rate, holding_days,
                       one_line_summary, lessons, pattern_tags, trade_date,
                       sell_reason, situation_analysis, judgment_evaluation, trade_type
                FROM trading_journal WHERE ticker = ?
                ORDER BY trade_date DESC LIMIT 3
            """, (ticker,))

            for raw_entry in self.cursor.fetchall():
                if hasattr(raw_entry, 'keys'):
                    entry = dict(raw_entry)
                else:
                    entry = dict(zip([d[0] for d in self.cursor.description], raw_entry))
                entry['is_fallback'] = False
                retrieved_entries.append(entry)

            # 2순위: 동일 종목 이력이 전혀 없을 때(0건)만 동일 섹터 타 종목 극단값 1건 로드
            if not retrieved_entries and sector and sector != "Unknown":
                comp_sectors = self._get_compatible_sectors(sector)
                like_clauses = " OR ".join(["buy_scenario LIKE ?" for _ in comp_sectors])
                params = [ticker] + [f'%"{s}"%' for s in comp_sectors]
                self.cursor.execute(f"""
                    SELECT ticker, company_name, profit_rate, holding_days,
                           one_line_summary, lessons, pattern_tags, trade_date,
                           sell_reason, situation_analysis, judgment_evaluation, trade_type
                    FROM trading_journal 
                    WHERE ticker != ? AND ({like_clauses})
                    ORDER BY ABS(profit_rate) DESC LIMIT 1
                """, tuple(params))
                
                for raw_entry in self.cursor.fetchall():
                    if hasattr(raw_entry, 'keys'):
                        entry = dict(raw_entry)
                    else:
                        entry = dict(zip([d[0] for d in self.cursor.description], raw_entry))
                    entry['is_fallback'] = True
                    retrieved_entries.append(entry)

            # 이력을 sell과 skip으로 분리
            sell_entries = [e for e in retrieved_entries if e.get('trade_type') != 'skip']
            skip_entries = [e for e in retrieved_entries if e.get('trade_type') == 'skip']

            # 매매 이력 마크다운 빌드 (sell + fallback만)
            if sell_entries:
                context_parts.append("#### 📜 Past Trading History References")
                for entry in sell_entries:
                    if entry.get('is_fallback'):
                        # Fallback 전용 상세 규격 포맷팅
                        trade_date_str = entry.get('trade_date', '')[:10]
                        sell_reason = entry.get('sell_reason') or "N/A"
                        
                        judgment_evaluation_str = "N/A"
                        if entry.get('judgment_evaluation'):
                            try:
                                je = json.loads(entry['judgment_evaluation']) if isinstance(entry['judgment_evaluation'], str) else entry['judgment_evaluation']
                                if isinstance(je, dict):
                                    sell_quality = je.get("sell_quality_reason") or je.get("sell_quality") or ""
                                    missed = je.get("missed_signals", [])
                                    missed_str = f" / 놓친 신호: {', '.join(missed)}" if missed else ""
                                    if sell_quality or missed_str:
                                        judgment_evaluation_str = f"{sell_quality}{missed_str}"
                            except Exception:
                                pass

                        context_parts.append(
                            f"[주의: 아래 기록은 현재 분석 종목의 데이터가 아닌, '동일 섹터 타 종목'의 과거 매매 참고 데이터입니다.]\n"
                            f"- 원천 종목: {entry.get('company_name', 'Unknown')} ({entry.get('ticker', 'N/A')})\n"
                            f"- 매매 일자: {trade_date_str}\n"
                            f"- 당시 결과: {entry.get('profit_rate', 0.0):+.1f}% (매도/제외사유: {sell_reason})\n"
                            f"- 복기 내용: {judgment_evaluation_str}"
                        )
                    else:
                        # 동일 종목 이력 기본 포맷팅
                        prefix = "[동일 종목 이력]"
                        profit_emoji = "✅" if entry.get('profit_rate', 0.0) > 0 else "❌"
                        
                        lessons_str = ""
                        try:
                            lessons_json = entry.get('lessons')
                            lessons = json.loads(lessons_json) if lessons_json else []
                            if lessons:
                                lessons_str = " / Lessons: " + ", ".join(
                                    [l.get('action', '') for l in lessons[:2] if isinstance(l, dict)]
                                )
                        except Exception:
                            pass

                        recency_tag = ""
                        try:
                            trade_date_str = entry.get('trade_date')
                            if trade_date_str:
                                exit_date = datetime.strptime(trade_date_str[:10], "%Y-%m-%d")
                                days_since = (datetime.now() - exit_date).days
                                if days_since <= 7:
                                    recency_tag = f" ⚠️ {days_since}일 전 매도 — 추격 재진입 신중 검토"
                                else:
                                    recency_tag = f" ({days_since}일 전)"
                        except Exception:
                            pass

                        context_parts.append(
                            f"- {prefix} {profit_emoji} Return {entry.get('profit_rate', 0.0):.1f}% "
                            f"(held {entry.get('holding_days', 0)} days) - {entry.get('one_line_summary', '')}{lessons_str}{recency_tag}"
                        )

                        sell_reason = entry.get('sell_reason') or ""
                        if sell_reason:
                            context_parts.append(f"  - 매도/제외 사유: {sell_reason}")

                        try:
                            sit_analysis_json = entry.get('situation_analysis')
                            situation = json.loads(sit_analysis_json) if sit_analysis_json else {}
                            sell_ctx = situation.get("sell_context_summary", "")
                            if sell_ctx:
                                context_parts.append(f"  - 매도 시 상황: {sell_ctx}")
                            key_changes = situation.get("key_changes", [])
                            if key_changes:
                                changes_str = " / ".join(str(c) for c in key_changes[:3])
                                context_parts.append(f"  - 핵심 변화: {changes_str}")
                        except Exception:
                            pass

                        try:
                            judg_eval_json = entry.get('judgment_evaluation')
                            judgment = json.loads(judg_eval_json) if judg_eval_json else {}
                            sell_quality_reason = judgment.get("sell_quality_reason", "")
                            if sell_quality_reason:
                                context_parts.append(f"  - 매도 판단: {sell_quality_reason}")
                            missed = judgment.get("missed_signals", [])
                            if missed:
                                missed_str = " / ".join(str(m) for m in missed[:2])
                                context_parts.append(f"  - 놓친 신호: {missed_str}")
                        except Exception:
                            pass

                context_parts.append("")

            # 놓친 기회 이력 마크다운 빌드 (skip 전용, 최대 2건)
            if skip_entries:
                context_parts.append("#### 🔸 Missed Opportunity References (Previously Skipped)")
                context_parts.append("[NOTE: These are stocks that were analyzed but NOT purchased. "
                                     "The returns below are hypothetical — what would have happened if bought.]")
                for entry in skip_entries[:2]:
                    profit_rate = entry.get('profit_rate', 0.0)
                    profit_emoji = "📈" if profit_rate > 0 else "📉"

                    skip_reason = entry.get('sell_reason') or "N/A"

                    recency_tag = ""
                    try:
                        trade_date_str = entry.get('trade_date')
                        if trade_date_str:
                            skip_date = datetime.strptime(trade_date_str[:10], "%Y-%m-%d")
                            days_since = (datetime.now() - skip_date).days
                            recency_tag = f" ({days_since}일 전)"
                    except Exception:
                        pass

                    context_parts.append(
                        f"- [놓친 기회] {profit_emoji} Hypothetical return {profit_rate:+.1f}% (30d tracking){recency_tag}"
                    )
                    context_parts.append(f"  - 스킵 사유: {skip_reason}")

                    # 교훈 추출
                    try:
                        lessons_json = entry.get('lessons')
                        lessons_list = json.loads(lessons_json) if lessons_json else []
                        if lessons_list:
                            for lesson in lessons_list[:2]:
                                if isinstance(lesson, dict):
                                    action = lesson.get('action', '')
                                    if action:
                                        context_parts.append(f"  - 교훈: {action}")
                    except Exception:
                        pass

                    context_parts.append("  - ⚠️ 유사한 상황이 아닌지 재검토 필요")

                context_parts.append("")

            # 4. 장기 직관 필터링 (최대 3개 조합: 매칭 2개 + 범용 1~2개)
            matched_intuitions = []
            
            # 1단계: 카테고리 매칭 직관 조회 (섹터 또는 트리거 타입)
            if sector or trigger_type:
                self.cursor.execute("""
                    SELECT category, condition, insight, confidence
                    FROM trading_intuitions 
                    WHERE is_active = 1 AND (category = ? OR category = ?)
                    ORDER BY confidence DESC LIMIT 3
                """, (sector or "", trigger_type or ""))
                
                for row in self.cursor.fetchall():
                    matched_intuitions.append(row)

            # 2단계: 부족분을 범용 직관으로 보완하여 합산 3개 유지
            needed = 3 - len(matched_intuitions)
            if needed > 0:
                self.cursor.execute("""
                    SELECT category, condition, insight, confidence
                    FROM trading_intuitions 
                    WHERE is_active = 1 AND category NOT IN (?, ?)
                    ORDER BY confidence DESC LIMIT ?
                """, (sector or "", trigger_type or "", needed))
                
                for row in self.cursor.fetchall():
                    matched_intuitions.append(row)

            if matched_intuitions:
                context_parts.append("#### Accumulated Trading Intuitions")
                for i in matched_intuitions:
                    confidence_bar = "●" * int(i[3] * 5) + "○" * (5 - int(i[3] * 5))
                    context_parts.append(
                        f"- [{i[0]}] {i[1]} → {i[2]} (Confidence: {confidence_bar})"
                    )
                context_parts.append("")

            if context_parts:
                return "### 📚 Past Trading Experience Reference\n\n" + "\n".join(context_parts)
            return ""

        except Exception as e:
            logger.warning(f"Failed to get journal context: {e}")
            return ""

    def get_universal_principles(self, limit: int = 5) -> List[str]:
        """Retrieve universal trading principles.

        Only includes principles with supporting_trades >= 2 to avoid injecting
        unverified rules into LLM prompts. Limited to top 5 to reduce token usage.
        """
        try:
            self.cursor.execute("""
                SELECT condition, action, reason, priority, confidence, supporting_trades
                FROM trading_principles
                WHERE is_active = 1 AND scope = 'universal'
                  AND supporting_trades >= 2
                ORDER BY priority DESC, confidence DESC
                LIMIT ?
            """, (limit,))

            result = []
            for p in self.cursor.fetchall():
                priority_emoji = "🔴" if p[3] == 'high' else "🟡" if p[3] == 'medium' else "⚪"
                confidence_bar = "●" * int((p[4] or 0.5) * 5) + "○" * (5 - int((p[4] or 0.5) * 5))

                text = f"{priority_emoji} **{p[0]}** → {p[1]}"
                if p[2]:
                    text += f" (Reason: {p[2][:50]}...)" if len(p[2] or '') > 50 else f" (Reason: {p[2]})"
                text += f" [Confidence: {confidence_bar}, Trades: {p[5]}]"
                result.append(f"- {text}")

            return result

        except Exception as e:
            logger.warning(f"Failed to get universal principles: {e}")
            return []

    def get_score_adjustment(self, ticker: str, sector: str = None, trigger_type: str = None) -> Tuple[float, List[str]]:
        """Calculate deterministic score adjustment based on past experiences.
        
        Controls Python-side scoring weight limits between -1.5 and +1.0 points (10-point scale).
        """
        try:
            adjustment = 0.0
            reasons = []

            # 1. Same stock history (from journal - excluding skipped stocks)
            self.cursor.execute("""
                SELECT profit_rate FROM trading_journal
                WHERE ticker = ? AND trade_type != 'skip' ORDER BY trade_date DESC LIMIT 3
            """, (ticker,))

            same_stock = self.cursor.fetchall()
            if same_stock:
                rates = [s[0] for s in same_stock]
                avg_profit = sum(rates) / len(rates)
                if avg_profit < -5.0:
                    adjustment -= 1.0
                    reasons.append(f"동일 종목 과거 평균 손실 우려 ({avg_profit:.1f}%)")
                elif avg_profit > 10.0:
                    adjustment += 0.5
                    reasons.append(f"동일 종목 과거 평균 수익 호조 ({avg_profit:.1f}%)")

            # 2. Sector performance (from journal - excluding skipped stocks)
            if sector and sector != "Unknown":
                comp_sectors = self._get_compatible_sectors(sector)
                like_clauses = " OR ".join(["buy_scenario LIKE ?" for _ in comp_sectors])
                params = [f'%"{s}"%' for s in comp_sectors]
                self.cursor.execute(f"""
                    SELECT AVG(profit_rate), COUNT(*)
                    FROM trading_journal WHERE ({like_clauses}) AND trade_type != 'skip'
                """, tuple(params))

                sector_stats = self.cursor.fetchone()
                if sector_stats and sector_stats[1] >= 3:
                    avg_sec = sector_stats[0]
                    if avg_sec < -3.0:
                        adjustment -= 0.5
                        reasons.append(f"{sector} 섹터 평균 손실 ({avg_sec:.1f}%)")
                    elif avg_sec > 5.0:
                        adjustment += 0.3
                        reasons.append(f"{sector} 섹터 평균 수익 ({avg_sec:.1f}%)")

            # 3. Trigger type performance (from performance_tracker - ground truth)
            if trigger_type:
                perf_stats = self.get_performance_tracker_stats(trigger_type)
                if 'current_trigger' in perf_stats:
                    t = perf_stats['current_trigger']
                    if t['total'] >= 5:  # Require minimum sample size
                        if t['win_rate'] < 0.35:
                            adjustment -= 1.0
                            reasons.append(
                                f"트리거 '{trigger_type}' 승률 저조 {t['win_rate']*100:.0f}% (n={t['total']})"
                            )
                        elif t['win_rate'] > 0.65:
                            adjustment += 0.5
                            reasons.append(
                                f"트리거 '{trigger_type}' 승률 우수 {t['win_rate']*100:.0f}% (n={t['total']})"
                            )

            # 보정 한계치 제어 [-1.5, +1.0] (10점 만점 척도 기준)
            return round(max(-1.5, min(1.0, adjustment)), 2), reasons

        except Exception as e:
            logger.warning(f"Failed to calculate score adjustment: {e}")
            return 0.0, []
