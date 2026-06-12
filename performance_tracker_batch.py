#!/usr/bin/env python3
"""
Post-Analysis Performance Tracking Batch Script

Tracks prices at 7/14/30 days after analysis for all stocks (both traded and watched)
to collect statistics on which trigger types actually perform well.

Usage:
    python performance_tracker_batch.py              # Update all tracking targets
    python performance_tracker_batch.py --dry-run    # Test without actual DB updates
    python performance_tracker_batch.py --report     # Current tracking status report
"""
from dotenv import load_dotenv
load_dotenv()

import os
import sys
import sqlite3
import argparse
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple
from pathlib import Path

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"performance_tracker_{datetime.now().strftime('%Y%m%d')}.log")
    ]
)
logger = logging.getLogger(__name__)

# Project root path
PROJECT_ROOT = Path(__file__).parent
DB_PATH = PROJECT_ROOT / "stock_tracking_db.sqlite"

# krx_data_client import
try:
    from krx_data_client import (
        get_market_ohlcv_by_date,
        get_nearest_business_day_in_a_week,
    )
    KRX_AVAILABLE = True
except ImportError:
    KRX_AVAILABLE = False
    logger.warning("krx_data_client package is not installed.")


class PerformanceTrackerBatch:
    """Batch processor for tracking analyzed stock performance"""

    # Tracking day thresholds
    TRACK_DAYS = [7, 14, 30]

    def __init__(self, db_path: str = None, dry_run: bool = False, no_journal: bool = False):
        """
        Args:
            db_path: SQLite DB path
            dry_run: If True, test only without actual DB updates
            no_journal: If True, disable skipped stocks journaling
        """
        self.db_path = db_path or str(DB_PATH)
        self.dry_run = dry_run
        self.no_journal = no_journal
        self.today = datetime.now().strftime("%Y-%m-%d")
        self.today_yyyymmdd = datetime.now().strftime("%Y%m%d")

    def connect_db(self) -> sqlite3.Connection:
        """Connect to database"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def get_tracking_targets(self) -> List[Dict[str, Any]]:
        """Query stocks that need tracking

        Returns:
            List of stocks that need tracking
        """
        conn = self.connect_db()
        try:
            cursor = conn.execute("""
                SELECT
                    id,
                    ticker,
                    company_name,
                    trigger_type,
                    trigger_mode,
                    analyzed_date,
                    analyzed_price,
                    decision,
                    was_traded,
                    skip_reason,
                    buy_score,
                    min_score,
                    target_price,
                    stop_loss,
                    risk_reward_ratio,
                    tracked_7d_date,
                    tracked_7d_price,
                    tracked_7d_return,
                    tracked_14d_date,
                    tracked_14d_price,
                    tracked_14d_return,
                    tracked_30d_date,
                    tracked_30d_price,
                    tracked_30d_return,
                    tracking_status
                FROM analysis_performance_tracker
                WHERE tracking_status IN ('pending', 'in_progress')
                ORDER BY analyzed_date ASC
            """)

            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def get_current_price(self, ticker: str) -> Optional[float]:
        """Query current stock price

        Args:
            ticker: Stock code (6 digits)

        Returns:
            Current close price or None
        """
        # Try FinanceDataReader first (fast, reliable, no login required)
        try:
            import FinanceDataReader as fdr
            today = datetime.now()
            # Query last 10 days to handle holidays and weekends
            start_date = (today - timedelta(days=10)).strftime("%Y-%m-%d")
            end_date = today.strftime("%Y-%m-%d")
            
            df = fdr.DataReader(ticker, start_date, end_date)
            if df is not None and not df.empty:
                latest_close = df['Close'].iloc[-1]
                logger.debug(f"[{ticker}] Price fetched via FinanceDataReader: {latest_close}")
                return float(latest_close)
        except Exception as e:
            logger.debug(f"[{ticker}] FinanceDataReader query failed: {e}")

        # Fallback to krx_data_client
        if not KRX_AVAILABLE:
            logger.error("krx_data_client is not available.")
            return None

        try:
            # Get nearest business day within last 7 days
            today_str = datetime.now().strftime("%Y%m%d")
            week_ago_str = (datetime.now() - timedelta(days=7)).strftime("%Y%m%d")

            df = get_market_ohlcv_by_date(week_ago_str, today_str, ticker)

            if df is None or df.empty:
                logger.warning(f"[{ticker}] No price data available")
                return None

            # Return most recent close price (krx_data_client uses English column names)
            close_col = 'Close' if 'Close' in df.columns else '종가'
            latest_close = df[close_col].iloc[-1]
            return float(latest_close)

        except Exception as e:
            logger.error(f"[{ticker}] Price query failed: {e}")
            return None

    def get_historical_price(self, ticker: str, target_date: str) -> Optional[float]:
        """Query historical close price for a specific date
        
        Args:
            ticker: Stock code
            target_date: Target date string (YYYY-MM-DD)
            
        Returns:
            Close price on target_date or nearest business day, or None
        """
        try:
            import FinanceDataReader as fdr
            # Query window around target_date to handle weekends/holidays
            target = datetime.strptime(target_date, "%Y-%m-%d")
            start_date = (target - timedelta(days=5)).strftime("%Y-%m-%d")
            end_date = (target + timedelta(days=5)).strftime("%Y-%m-%d")
            
            df = fdr.DataReader(ticker, start_date, end_date)
            if df is not None and not df.empty:
                # Find nearest date to target
                df.index = df.index.strftime("%Y-%m-%d")
                
                # Check if exact date exists
                if target_date in df.index:
                    return float(df.loc[target_date]['Close'])
                
                # If target date does not exist (holiday/weekend), get the closest preceding business day
                preceding = [d for d in df.index if d <= target_date]
                if preceding:
                    closest_date = preceding[-1]
                    return float(df.loc[closest_date]['Close'])
                
                # Fallback to the first available date
                return float(df['Close'].iloc[0])
        except Exception as e:
            logger.debug(f"[{ticker}] FinanceDataReader historical query failed for {target_date}: {e}")

        # Fallback to krx_data_client
        if not KRX_AVAILABLE:
            return None

        try:
            target = datetime.strptime(target_date, "%Y-%m-%d")
            start_str = (target - timedelta(days=5)).strftime("%Y%m%d")
            end_str = (target + timedelta(days=5)).strftime("%Y%m%d")
            
            df = get_market_ohlcv_by_date(start_str, end_str, ticker)
            if df is not None and not df.empty:
                close_col = 'Close' if 'Close' in df.columns else '종가'
                # Find closest preceding date
                df.index = df.index.strftime("%Y-%m-%d")
                preceding = [d for d in df.index if d <= target_date]
                if preceding:
                    closest_date = preceding[-1]
                    return float(df.loc[closest_date][close_col])
                return float(df[close_col].iloc[0])
        except Exception as e:
            logger.error(f"[{ticker}] Historical price query failed for {target_date}: {e}")
            
        return None

    def calculate_days_elapsed(self, analyzed_date: str) -> int:
        """Calculate days elapsed since analysis date

        Args:
            analyzed_date: Analysis date (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)

        Returns:
            Days elapsed
        """
        try:
            # Extract date only if timestamp is included
            date_only = analyzed_date.split(' ')[0] if ' ' in analyzed_date else analyzed_date
            analyzed = datetime.strptime(date_only, "%Y-%m-%d")
            today = datetime.now()
            return (today - analyzed).days
        except Exception as e:
            logger.error(f"Date calculation error: {e}")
            return 0

    def calculate_return(self, analyzed_price: float, current_price: float) -> float:
        """Calculate return rate

        Args:
            analyzed_price: Price at analysis time
            current_price: Current price

        Returns:
            Return rate (e.g., 0.05 = 5%)
        """
        if analyzed_price <= 0:
            return 0.0
        return (current_price - analyzed_price) / analyzed_price

    def update_tracking_record(
        self,
        record: Dict[str, Any],
        days_elapsed: int,
        current_price: float,
        analyzed_price: float
    ) -> Dict[str, Any]:
        """Update tracking record

        Args:
            record: Existing record info (to check already recorded values)
            days_elapsed: Days elapsed
            current_price: Current price
            analyzed_price: Price at analysis time

        Returns:
            Fields and values to update
        """
        updates = {}
        analyzed_date = record.get('analyzed_date')
        ticker = record.get('ticker', '')
        
        if not analyzed_date:
            logger.warning(f"[{ticker}] missing analyzed_date, skipping update")
            return updates
            
        # Calculate target dates
        date_only = analyzed_date.split(' ')[0] if ' ' in analyzed_date else analyzed_date
        analyzed_dt = datetime.strptime(date_only, "%Y-%m-%d")

        # 7-day update (if not yet recorded and 7+ days elapsed)
        if days_elapsed >= 7 and record.get('tracked_7d_return') is None:
            target_date = (analyzed_dt + timedelta(days=7)).strftime("%Y-%m-%d")
            price = self.get_historical_price(ticker, target_date)
            # Fallback to current_price as a last resort if it is very close to the 7-day mark
            if price is None and days_elapsed < 10:
                price = current_price
                
            if price is not None:
                updates['tracked_7d_date'] = target_date
                updates['tracked_7d_price'] = price
                updates['tracked_7d_return'] = self.calculate_return(analyzed_price, price)

        # 14-day update (if not yet recorded and 14+ days elapsed)
        if days_elapsed >= 14 and record.get('tracked_14d_return') is None:
            target_date = (analyzed_dt + timedelta(days=14)).strftime("%Y-%m-%d")
            price = self.get_historical_price(ticker, target_date)
            if price is None and days_elapsed < 17:
                price = current_price
                
            if price is not None:
                updates['tracked_14d_date'] = target_date
                updates['tracked_14d_price'] = price
                updates['tracked_14d_return'] = self.calculate_return(analyzed_price, price)

        # 30-day update (if not yet recorded and 30+ days elapsed)
        if days_elapsed >= 30 and record.get('tracked_30d_return') is None:
            target_date = (analyzed_dt + timedelta(days=30)).strftime("%Y-%m-%d")
            price = self.get_historical_price(ticker, target_date)
            if price is None:
                price = current_price
                
            if price is not None:
                updates['tracked_30d_date'] = target_date
                updates['tracked_30d_price'] = price
                updates['tracked_30d_return'] = self.calculate_return(analyzed_price, price)
                updates['tracking_status'] = 'completed'
        elif days_elapsed >= 7 and record.get('tracking_status') == 'pending':
            updates['tracking_status'] = 'in_progress'

        if updates:
            updates['updated_at'] = self.today

        return updates

    def apply_updates(self, record_id: int, updates: Dict[str, Any]) -> bool:
        """Apply updates to database

        Args:
            record_id: Record ID
            updates: Fields and values to update

        Returns:
            Success status
        """
        if not updates:
            return True

        if self.dry_run:
            logger.info(f"[DRY-RUN] ID {record_id}: {updates}")
            return True

        conn = self.connect_db()
        try:
            # Dynamically generate UPDATE query
            set_clause = ", ".join([f"{k} = ?" for k in updates.keys()])
            values = list(updates.values()) + [record_id]

            query = f"UPDATE analysis_performance_tracker SET {set_clause} WHERE id = ?"
            conn.execute(query, values)
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"DB update failed (ID {record_id}): {e}")
            conn.rollback()
            return False
        finally:
            conn.close()

    def run(self) -> Dict[str, Any]:
        """Execute batch

        Returns:
            Execution result statistics
        """
        logger.info("="*60)
        logger.info(f"Performance tracking batch started: {self.today}")
        if self.dry_run:
            logger.info("[DRY-RUN mode] No actual DB updates")
        logger.info("="*60)

        # Statistics
        stats = {
            'total': 0,
            'updated': 0,
            'skipped': 0,
            'errors': 0,
            'completed': 0,
            'by_trigger_type': {},
            'by_decision': {'traded': 0, 'watched': 0}
        }

        # Query tracking targets
        targets = self.get_tracking_targets()
        stats['total'] = len(targets)
        logger.info(f"Tracking targets: {stats['total']} stocks")

        if not targets:
            logger.info("No stocks to track.")
            return stats

        # Collect pending journals to process at the end
        pending_skip_journals = []

        # Process each stock
        for record in targets:
            ticker = record['ticker']
            company_name = record['company_name']
            trigger_type = record['trigger_type'] or 'unknown'
            analyzed_date = record['analyzed_date']
            analyzed_price = record['analyzed_price']
            was_traded = record['was_traded']

            # Calculate days elapsed
            days_elapsed = self.calculate_days_elapsed(analyzed_date)

            # Skip if tracking already completed for this period
            should_update = False
            if days_elapsed >= 7 and record['tracked_7d_price'] is None:
                should_update = True
            if days_elapsed >= 14 and record['tracked_14d_price'] is None:
                should_update = True
            if days_elapsed >= 30 and record['tracked_30d_price'] is None:
                should_update = True

            if not should_update:
                logger.debug(f"[{ticker}] {company_name}: No update needed ({days_elapsed} days elapsed)")
                stats['skipped'] += 1
                continue

            logger.info(f"[{ticker}] {company_name}: {days_elapsed} days elapsed, trigger={trigger_type}")

            # Query current price
            current_price = self.get_current_price(ticker)
            if current_price is None:
                logger.warning(f"[{ticker}] Price query failed, skipping")
                stats['errors'] += 1
                continue

            # Calculate return
            return_rate = self.calculate_return(analyzed_price, current_price)
            logger.info(f"  Analyzed: {analyzed_price:,.0f} → Current: {current_price:,.0f} ({return_rate*100:+.2f}%)")

            # Determine updates
            updates = self.update_tracking_record(
                record,
                days_elapsed,
                current_price,
                analyzed_price
            )

            # Apply DB updates
            if self.apply_updates(record['id'], updates):
                stats['updated'] += 1

                # Statistics by trigger type
                if trigger_type not in stats['by_trigger_type']:
                    stats['by_trigger_type'][trigger_type] = {'count': 0, 'returns': []}
                stats['by_trigger_type'][trigger_type]['count'] += 1
                stats['by_trigger_type'][trigger_type]['returns'].append(return_rate)

                # Classify traded/watched
                if was_traded:
                    stats['by_decision']['traded'] += 1
                else:
                    stats['by_decision']['watched'] += 1

                # Count completed
                if updates.get('tracking_status') == 'completed':
                    stats['completed'] += 1
                    
                    # If this was a skipped stock, check smart filters for journaling
                    if not was_traded:
                        buy_score = record.get('buy_score') or 0
                        min_score = record.get('min_score') or 0
                        target_price = record.get('target_price') or 0
                        skip_reason = record.get('skip_reason') or ''
                        
                        ret_7d = updates.get('tracked_7d_return') if 'tracked_7d_return' in updates else record['tracked_7d_return']
                        ret_14d = updates.get('tracked_14d_return') if 'tracked_14d_return' in updates else record['tracked_14d_return']
                        ret_30d = updates.get('tracked_30d_return') if 'tracked_30d_return' in updates else record['tracked_30d_return']
                        p_30d = updates.get('tracked_30d_price') if 'tracked_30d_price' in updates else record['tracked_30d_price']
                        
                        ret_7d = ret_7d if ret_7d is not None else 0.0
                        ret_14d = ret_14d if ret_14d is not None else 0.0
                        ret_30d = ret_30d if ret_30d is not None else 0.0
                        
                        # Max tracked price check
                        max_tracked_price = max(
                            record['analyzed_price'] or 0,
                            updates.get('tracked_7d_price') or record['tracked_7d_price'] or 0,
                            updates.get('tracked_14d_price') or record['tracked_14d_price'] or 0,
                            p_30d or 0
                        )
                        
                        matched_conditions = []
                        
                        # 1. Missed Trend: 7d, 14d, 30d all positive and max reached >= target_price
                        if ret_7d > 0 and ret_14d > 0 and ret_30d > 0 and (target_price > 0 and max_tracked_price >= target_price):
                            matched_conditions.append("Missed Trend")
                            
                        # 2. Near-Miss: buy_score >= min_score * 0.9 and 30d return >= 15%
                        if min_score > 0 and buy_score >= min_score * 0.9 and ret_30d >= 0.15:
                            matched_conditions.append("Near-Miss")
                            
                        # 3. No-Slot Omission: skip_reason is slot limit and 30d return >= 10%
                        is_slot_reason = any(k in skip_reason.lower() for k in ['maximum', 'slot', 'full', '슬롯'])
                        if is_slot_reason and ret_30d >= 0.10:
                            matched_conditions.append("No-Slot Omission")
                            
                        if matched_conditions:
                            logger.info(f"[{ticker}] {company_name} matched skipped journaling filters: {matched_conditions}")
                            pending_skip_journals.append((record, {
                                'tracked_7d_return': ret_7d,
                                'tracked_14d_return': ret_14d,
                                'tracked_30d_return': ret_30d,
                                'tracked_30d_price': p_30d,
                                'matched_conditions': matched_conditions
                            }))
            else:
                stats['errors'] += 1

        # Summary
        logger.info("="*60)
        logger.info("Batch execution completed")
        logger.info(f"  Total: {stats['total']}, Updated: {stats['updated']}, "
                   f"Skipped: {stats['skipped']}, Errors: {stats['errors']}")
        logger.info(f"  Completed: {stats['completed']}, Traded: {stats['by_decision']['traded']}, "
                   f"Watched: {stats['by_decision']['watched']}")
        logger.info("="*60)

        # Process pending journals asynchronously
        if pending_skip_journals and not self.dry_run and not self.no_journal:
            import asyncio
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(self.process_skip_journals(pending_skip_journals))
                else:
                    loop.run_until_complete(self.process_skip_journals(pending_skip_journals))
            except RuntimeError:
                asyncio.run(self.process_skip_journals(pending_skip_journals))

        return stats

    async def process_skip_journals(self, pending_journals: List[Tuple[Dict[str, Any], Dict[str, Any]]]):
        """Create skip journals asynchronously"""
        if not pending_journals:
            return
            
        logger.info(f"Processing {len(pending_journals)} skipped stock journals asynchronously...")
        
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        try:
            sys.path.append(str(PROJECT_ROOT))
            from tracking.journal import JournalManager
            
            enable_journal = os.getenv("ENABLE_JOURNAL", "True").lower() == "true"
            language = os.getenv("LANGUAGE", "ko")
            
            journal_manager = JournalManager(
                cursor=cursor,
                conn=conn,
                language=language,
                enable_journal=enable_journal
            )
            
            import asyncio
            tasks = []
            for record, tracking_data in pending_journals:
                ticker = record['ticker']
                target_price = record.get('target_price') or 0
                stop_loss = record.get('stop_loss') or 0
                
                # Pre-check prices
                if target_price <= 0 or stop_loss <= 0:
                    logger.warning(f"[{ticker}] Skipping retrospective journal: Missing or invalid target_price ({target_price}) or stop_loss ({stop_loss})")
                    continue

                scenario_json = '{}'
                try:
                    wl_row = conn.execute(
                        "SELECT scenario FROM watchlist_history WHERE ticker = ? ORDER BY analyzed_date DESC LIMIT 1",
                        (ticker,)
                    ).fetchone()
                    if wl_row and wl_row['scenario']:
                        scenario_json = wl_row['scenario']
                except Exception as db_err:
                    logger.debug(f"Failed to fetch scenario from watchlist_history for {ticker}: {db_err}")
                
                # Pre-check scenario JSON presence
                if not scenario_json or scenario_json == '{}' or scenario_json == 'None':
                    logger.warning(f"[{ticker}] Skipping retrospective journal: Missing original scenario JSON in watchlist_history")
                    continue

                stock_data = {
                    'ticker': ticker,
                    'company_name': record['company_name'],
                    'buy_price': record['analyzed_price'],
                    'buy_date': record['analyzed_date'],
                    'scenario': scenario_json,
                    'buy_score': record['buy_score'],
                    'min_score': record['min_score'],
                    'skip_reason': record['skip_reason'],
                    'target_price': target_price,
                    'stop_loss': stop_loss
                }
                
                tasks.append(journal_manager.create_skip_entry(
                    stock_data=stock_data,
                    tracking_data=tracking_data,
                    trade_date=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                ))
                
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for i, result in enumerate(results):
                ticker = pending_journals[i][0]['ticker']
                if isinstance(result, Exception):
                    logger.error(f"Error processing journal for {ticker}: {result}")
                elif result:
                    logger.info(f"Journal successfully processed for {ticker}")
                else:
                    logger.warning(f"Journal failed or disabled for {ticker}")
                    
        finally:
            conn.close()

    def generate_report(self) -> str:
        """Generate tracking status report

        Returns:
            Report string
        """
        conn = self.connect_db()
        try:
            # Overall statistics
            cursor = conn.execute("""
                SELECT
                    tracking_status,
                    COUNT(*) as count
                FROM analysis_performance_tracker
                GROUP BY tracking_status
            """)
            status_stats = {row['tracking_status']: row['count'] for row in cursor.fetchall()}

            # Statistics by trigger type
            cursor = conn.execute("""
                SELECT
                    trigger_type,
                    COUNT(*) as count,
                    SUM(CASE WHEN was_traded = 1 THEN 1 ELSE 0 END) as traded_count,
                    AVG(tracked_7d_return) as avg_7d_return,
                    AVG(tracked_14d_return) as avg_14d_return,
                    AVG(tracked_30d_return) as avg_30d_return
                FROM analysis_performance_tracker
                WHERE tracking_status = 'completed'
                GROUP BY trigger_type
            """)
            trigger_stats = cursor.fetchall()

            # Traded vs watched performance comparison
            cursor = conn.execute("""
                SELECT
                    CASE WHEN was_traded = 1 THEN 'Traded' ELSE 'Watched' END as decision,
                    COUNT(*) as count,
                    AVG(tracked_7d_return) as avg_7d_return,
                    AVG(tracked_14d_return) as avg_14d_return,
                    AVG(tracked_30d_return) as avg_30d_return
                FROM analysis_performance_tracker
                WHERE tracking_status = 'completed'
                GROUP BY was_traded
            """)
            decision_stats = cursor.fetchall()

            # Generate report
            report = []
            report.append("="*70)
            report.append(f"📊 Analyzed Stock Performance Tracking Report ({self.today})")
            report.append("="*70)
            report.append("")

            # Status overview
            report.append("## 1. Status Overview")
            report.append("-"*40)
            for status, count in status_stats.items():
                status_name = {
                    'pending': 'Pending',
                    'in_progress': 'In Progress',
                    'completed': 'Completed'
                }.get(status, status)
                report.append(f"  {status_name}: {count} records")
            report.append("")

            # Performance by trigger type
            report.append("## 2. Performance by Trigger Type (Completed only)")
            report.append("-"*40)
            if trigger_stats:
                report.append(f"{'Trigger Type':<25} {'Count':>6} {'Traded':>6} {'7d':>8} {'14d':>8} {'30d':>8}")
                report.append("-"*70)
                for row in trigger_stats:
                    trigger_type = row['trigger_type'] or 'unknown'
                    count = row['count']
                    traded = row['traded_count'] or 0
                    avg_7d = row['avg_7d_return']
                    avg_14d = row['avg_14d_return']
                    avg_30d = row['avg_30d_return']

                    # Format returns
                    r7 = f"{avg_7d*100:+.1f}%" if avg_7d else "N/A"
                    r14 = f"{avg_14d*100:+.1f}%" if avg_14d else "N/A"
                    r30 = f"{avg_30d*100:+.1f}%" if avg_30d else "N/A"

                    report.append(f"{trigger_type:<25} {count:>6} {traded:>6} {r7:>8} {r14:>8} {r30:>8}")
            else:
                report.append("  No completed tracking data.")
            report.append("")

            # Traded vs watched performance
            report.append("## 3. Traded vs Watched Performance")
            report.append("-"*40)
            if decision_stats:
                report.append(f"{'Type':<10} {'Count':>6} {'7d':>10} {'14d':>10} {'30d':>10}")
                report.append("-"*50)
                for row in decision_stats:
                    decision = row['decision']
                    count = row['count']
                    avg_7d = row['avg_7d_return']
                    avg_14d = row['avg_14d_return']
                    avg_30d = row['avg_30d_return']

                    r7 = f"{avg_7d*100:+.1f}%" if avg_7d else "N/A"
                    r14 = f"{avg_14d*100:+.1f}%" if avg_14d else "N/A"
                    r30 = f"{avg_30d*100:+.1f}%" if avg_30d else "N/A"

                    report.append(f"{decision:<10} {count:>6} {r7:>10} {r14:>10} {r30:>10}")
            else:
                report.append("  No completed tracking data.")
            report.append("")

            # Recently completed tracking
            cursor = conn.execute("""
                SELECT
                    ticker,
                    company_name,
                    trigger_type,
                    analyzed_date,
                    analyzed_price,
                    tracked_30d_price,
                    tracked_30d_return,
                    was_traded,
                    decision
                FROM analysis_performance_tracker
                WHERE tracking_status = 'completed'
                ORDER BY updated_at DESC
                LIMIT 10
            """)
            recent = cursor.fetchall()

            report.append("## 4. Recently Completed Tracking (max 10)")
            report.append("-"*40)
            if recent:
                for row in recent:
                    ticker = row['ticker']
                    name = row['company_name']
                    trigger = row['trigger_type'] or 'unknown'
                    analyzed_price = row['analyzed_price']
                    final_price = row['tracked_30d_price']
                    return_rate = row['tracked_30d_return']
                    was_traded = "Traded" if row['was_traded'] else "Watched"

                    ret_str = f"{return_rate*100:+.1f}%" if return_rate else "N/A"
                    final_str = f"{final_price:,.0f}" if final_price else "N/A"
                    analyzed_str = f"{analyzed_price:,.0f}" if analyzed_price else "N/A"
                    report.append(f"  [{ticker}] {name}")
                    report.append(f"    Trigger: {trigger}, Decision: {was_traded}")
                    report.append(f"    Analyzed: {analyzed_str} → 30d later: {final_str} ({ret_str})")
            else:
                report.append("  No completed tracking data.")
            report.append("")

            report.append("="*70)

            return "\n".join(report)

        finally:
            conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Analyzed Stock Performance Tracking Batch",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python performance_tracker_batch.py              # Update all tracking (with journaling)
    python performance_tracker_batch.py --no-journal  # Update all tracking (without journaling)
    python performance_tracker_batch.py --dry-run    # Test mode
    python performance_tracker_batch.py --report     # Print status report only
        """
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Test only without actual DB updates"
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="Print current tracking status report"
    )
    parser.add_argument(
        "--no-journal",
        action="store_true",
        help="Disable skipped stocks retrospective journaling"
    )
    parser.add_argument(
        "--db",
        type=str,
        default=None,
        help="SQLite DB path (default: ./stock_tracking_db.sqlite)"
    )

    args = parser.parse_args()

    tracker = PerformanceTrackerBatch(db_path=args.db, dry_run=args.dry_run, no_journal=args.no_journal)

    if args.report:
        # Print report only
        report = tracker.generate_report()
        print(report)
    else:
        # Execute batch
        stats = tracker.run()

        # Also print result report
        print("\n")
        report = tracker.generate_report()
        print(report)


if __name__ == "__main__":
    main()
