#!/usr/bin/env python3
"""
Portfolio Telegram Reporter
- Periodically sends account and portfolio status to Telegram
- Supports both Korean (KR) and US stock portfolios
- Can be executed via crontab
"""

import asyncio
import os
import sys
import logging
import datetime
import yaml
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from dotenv import load_dotenv

# Set paths based on current script directory
SCRIPT_DIR = Path(__file__).parent.resolve()
TRADING_DIR = SCRIPT_DIR

# Add paths for importing trading module
PARENT_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(TRADING_DIR))              # trading/ for local imports
sys.path.insert(0, str(PARENT_DIR))               # project root - MUST be first for 'from trading.xxx'

# Load configuration file
CONFIG_FILE = TRADING_DIR / "config" / "kis_devlp.yaml"
config_root = os.path.join(os.path.expanduser("~"), "src", "hantoo", ".HKIS", "config")
CONFIG_FILE = os.path.join(config_root, "kis_devlp.yaml")
with open(CONFIG_FILE, encoding="UTF-8") as f:
    _cfg = yaml.load(f, Loader=yaml.FullLoader)

# Import local modules
from trading.domestic_stock_trading import DomesticStockTrading
from telegram_bot_agent import TelegramBotAgent

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(SCRIPT_DIR / 'portfolio_reporter.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Load .env file
SCRIPT_DIR = Path(__file__).parent.absolute()  # trading/
PROJECT_ROOT = SCRIPT_DIR.parent              # project_root/
ENV_FILE = PROJECT_ROOT / ".env"
load_dotenv(dotenv_path=str(ENV_FILE))

class PortfolioTelegramReporter:
    """Class for reporting portfolio status to Telegram"""

    # Season 2 constants
    SEASON2_START_DATE = "2025.11.03"
    SEASON2_START_AMOUNT = 4_212_440 # Starting capital in KRW

    def __init__(self, telegram_token: str = None, chat_id: str = None, trading_mode: str = None, broadcast_languages: list = None):
        """
        Initialize

        Args:
            telegram_token: Telegram bot token
            chat_id: Telegram channel ID
            trading_mode: Trading mode ('demo' or 'real', uses yaml config if None)
            broadcast_languages: List of languages to broadcast in parallel (e.g., ['en', 'ja', 'zh'])
        """
        # Telegram configuration
        self.telegram_token = telegram_token or os.environ.get("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHANNEL_ID")
        self.broadcast_languages = broadcast_languages or []
        self.broadcast_channel_ids = {}

        if not self.telegram_token:
            raise ValueError("Telegram bot token is required. Please provide via environment variable TELEGRAM_BOT_TOKEN or parameter.")

        if not self.chat_id:
            raise ValueError("Telegram channel ID is required. Please provide via environment variable TELEGRAM_CHANNEL_ID or parameter.")

        # Load broadcast channel IDs
        self._load_broadcast_channels()

        # Trading configuration - use yaml default_mode as default value
        self.trading_mode = trading_mode if trading_mode is not None else _cfg["default_mode"]
        self.telegram_bot = TelegramBotAgent(token=self.telegram_token)

        logger.info(f"PortfolioTelegramReporter initialized")
        logger.info(f"Trading mode: {self.trading_mode} (yaml config: {_cfg['default_mode']})")

    def _load_broadcast_channels(self):
        """
        Load telegram channel IDs for broadcast languages
        """
        for lang in self.broadcast_languages:
            lang_upper = lang.upper()
            env_key = f"TELEGRAM_CHANNEL_ID_{lang_upper}"
            channel_id = os.getenv(env_key)

            if channel_id:
                self.broadcast_channel_ids[lang] = channel_id
                logger.info(f"Broadcast channel loaded: {lang} -> {channel_id[:10]}...")
            else:
                logger.warning(f"Broadcast channel ID not configured for language: {lang} (env var: {env_key})")

    def format_currency(self, amount: float, currency: str = "KRW") -> str:
        """Format amount in specified currency"""
        if currency == "USD":
            return f"${amount:,.2f}" if amount else "$0.00"
        else:  # KRW
            return f"{amount:,.0f}원" if amount else "0원"

    def format_percentage(self, rate: float) -> str:
        """Format percentage"""
        return f"{rate:+.2f}%" if rate else "0.00%"

    def format_currency_with_sign(self, amount: float, currency: str = "KRW") -> str:
        """Format amount with +/- sign"""
        if currency == "USD":
            sign = "+" if amount >= 0 else ""
            return f"{sign}${amount:,.2f}" if amount else "$0.00"
        else:  # KRW
            sign = "+" if amount >= 0 else ""
            return f"{sign}{amount:,.0f}원" if amount else "0원"

    def get_index_performance(self, start_date: str) -> Dict[str, Any]:
        """
        Fetch KOSPI & KOSDAQ returns from start_date to latest date in SQLite DB
        """
        result = {}
        try:
            import sqlite3
            db_path = PROJECT_ROOT / "stock_tracking_db.sqlite"
            if not db_path.exists():
                logger.warning(f"Database not found at {db_path}, cannot fetch index returns")
                return result

            with sqlite3.connect(db_path) as conn:
                cur = conn.cursor()
                
                # Fetch start values (closest on or after start_date)
                cur.execute(
                    "SELECT date, kospi_index, kosdaq_index FROM market_condition WHERE date >= ? ORDER BY date ASC LIMIT 1",
                    (start_date,)
                )
                start_row = cur.fetchone()
                
                # Fetch latest values
                cur.execute(
                    "SELECT date, kospi_index, kosdaq_index FROM market_condition ORDER BY date DESC LIMIT 1"
                )
                end_row = cur.fetchone()
                
                if start_row and end_row:
                    result['kospi_start'] = start_row[1]
                    result['kosdaq_start'] = start_row[2]
                    result['kospi_latest'] = end_row[1]
                    result['kosdaq_latest'] = end_row[2]
                    
                    if start_row[1] > 0:
                        result['kospi_return'] = ((end_row[1] - start_row[1]) / start_row[1]) * 100.0
                    else:
                        result['kospi_return'] = 0.0
                        
                    if start_row[2] > 0:
                        result['kosdaq_return'] = ((end_row[2] - start_row[2]) / start_row[2]) * 100.0
                    else:
                        result['kosdaq_return'] = 0.0
                        
        except Exception as e:
            logger.error(f"Error fetching index performance: {e}")
            
        return result

    def get_trading_history_stats(self, start_date: str) -> Dict[str, Any]:
        """
        Fetch trading history statistics (win rate, count, holding days) from SQLite DB
        """
        result = {}
        try:
            import sqlite3
            db_path = PROJECT_ROOT / "stock_tracking_db.sqlite"
            if not db_path.exists():
                return result

            with sqlite3.connect(db_path) as conn:
                cur = conn.cursor()
                
                # Check if trading_history table exists first
                cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='trading_history'")
                if not cur.fetchone():
                    return result
                
                # Query closed trades summary
                cur.execute(
                    """
                    SELECT 
                        COUNT(*), 
                        SUM(CASE WHEN profit_rate > 0 THEN 1 ELSE 0 END), 
                        AVG(profit_rate),
                        AVG(holding_days)
                    FROM trading_history 
                    WHERE buy_date >= ?
                    """,
                    (start_date,)
                )
                row = cur.fetchone()
                if row and row[0] > 0:
                    result['total_trades'] = row[0]
                    result['win_trades'] = row[1] or 0
                    result['loss_trades'] = row[0] - (row[1] or 0)
                    result['win_rate'] = (result['win_trades'] / row[0]) * 100.0
                    result['avg_return'] = row[2] or 0.0
                    result['avg_holding_days'] = row[3] or 0.0
                    
        except Exception as e:
            logger.error(f"Error fetching trading history stats: {e}")
            
        return result

    def create_portfolio_message(
        self,
        kr_portfolio: List[Dict[str, Any]],
        kr_account_summary: Dict[str, Any]
    ) -> str:
        """
        Generate telegram message based on KR portfolio data

        Args:
            kr_portfolio: Korean stock portfolio data
            kr_account_summary: Korean account summary data

        Returns:
            Formatted telegram message
        """
        current_time = datetime.datetime.now().strftime("%m/%d %H:%M")
        mode_emoji = "🧪" if self.trading_mode == "demo" else "💰"
        mode_text = "모의투자" if self.trading_mode == "demo" else "실전투자"

        # Header
        message = f"📊 포트폴리오 리포트 {mode_emoji}\n"
        message += f"🕐 {current_time} | {mode_text}\n\n"

        # Season 2 info
        message += f"🏆 *시즌2* (시작: {self.SEASON2_START_DATE})\n"
        message += f"💵 시작금액: `{self.format_currency(self.SEASON2_START_AMOUNT)}`\n\n"

        # ========== KR Account Summary ==========
        if kr_account_summary:
            total_eval = kr_account_summary.get('total_eval_amount', 0)
            total_profit = kr_account_summary.get('total_profit_amount', 0)
            total_profit_rate = kr_account_summary.get('total_profit_rate', 0)
            deposit = kr_account_summary.get('deposit', 0)
            total_cash = kr_account_summary.get('total_cash', deposit)

            total_assets = total_eval

            # Calculate season 2 profit rate (from start amount)
            season_profit = total_assets - self.SEASON2_START_AMOUNT
            season_profit_rate = (season_profit / self.SEASON2_START_AMOUNT) * 100 if self.SEASON2_START_AMOUNT > 0 else 0

            # Calculate cash ratio
            cash_ratio = (total_cash / total_assets * 100) if total_assets > 0 else 0

            # Total assets and season profit
            season_profit_emoji = "📈" if season_profit >= 0 else "📉"

            # Calculate CAGR and elapsed days
            start_date_str = self.SEASON2_START_DATE.replace(".", "-") # "2025.11.03" -> "2025-11-03"
            days_elapsed = 0
            cagr = 0.0
            try:
                start_dt = datetime.datetime.strptime(start_date_str, "%Y-%m-%d").date()
                today_dt = datetime.date.today()
                days_elapsed = (today_dt - start_dt).days
                if days_elapsed > 0:
                    years_elapsed = days_elapsed / 365.0
                    cagr = ((total_assets / self.SEASON2_START_AMOUNT) ** (1.0 / years_elapsed) - 1.0) * 100.0
            except Exception as e:
                logger.error(f"Error calculating CAGR: {e}")

            # Fetch index data and calculate Alpha
            index_info = self.get_index_performance(start_date_str)

            message += f"🇰🇷 *한국주식 계좌*\n"
            message += f"💰 총 자산: `{self.format_currency(total_assets)}`\n"
            message += f"{season_profit_emoji} 시즌 수익: `{self.format_currency_with_sign(season_profit)}` "
            message += f"({self.format_percentage(season_profit_rate)})\n"
            
            # 1. Annualized return (CAGR)
            if days_elapsed > 0:
                cagr_emoji = "🔥" if cagr >= 0 else "❄️"
                message += f"{cagr_emoji} 연평균 수익률(CAGR): `{self.format_percentage(cagr)}` ({days_elapsed}일 경과)\n"

            message += f"📊 평가손익: `{self.format_currency_with_sign(total_profit)}` "
            message += f"({self.format_percentage(total_profit_rate)})\n"
            message += f"💳 현금: `{self.format_currency(total_cash)}` ({cash_ratio:.1f}%)\n\n"

            # 2. Market Index & Alpha
            if index_info:
                message += f"📈 *시장 지수 및 초과수익(Alpha)*\n"
                if index_info.get('kospi_start') and index_info.get('kospi_latest'):
                    kospi_ret = index_info['kospi_return']
                    kospi_alpha = season_profit_rate - kospi_ret
                    message += f" ▫️ KOSPI: `{index_info['kospi_latest']:,.2f}` ({self.format_percentage(kospi_ret)}) | *Alpha*: `{self.format_percentage(kospi_alpha)}`\n"
                if index_info.get('kosdaq_start') and index_info.get('kosdaq_latest'):
                    kosdaq_ret = index_info['kosdaq_return']
                    kosdaq_alpha = season_profit_rate - kosdaq_ret
                    message += f" ▫️ KOSDAQ: `{index_info['kosdaq_latest']:,.2f}` ({self.format_percentage(kosdaq_ret)}) | *Alpha*: `{self.format_percentage(kosdaq_alpha)}`\n"
                message += "\n"

            # 3. Holding performance & trade statistics
            if kr_portfolio:
                sorted_portfolio = sorted(kr_portfolio, key=lambda x: x.get('profit_rate', 0))
                worst_stock = sorted_portfolio[0]
                best_stock = sorted_portfolio[-1]
                
                message += f"🏆 *보유 종목 현황*\n"
                message += f" ▫️ 최고 수익: `{best_stock.get('stock_name')}` ({self.format_percentage(best_stock.get('profit_rate', 0))})\n"
                message += f" ▫️ 최저 수익: `{worst_stock.get('stock_name')}` ({self.format_percentage(worst_stock.get('profit_rate', 0))})\n\n"

            trade_stats = self.get_trading_history_stats(start_date_str)
            if trade_stats:
                message += f"⚙️ *시즌2 매매 누적 통계*\n"
                message += f" ▫️ 총 매도: `{trade_stats['total_trades']}회`\n"
                message += f" ▫️ 승률: `{trade_stats['win_rate']:.1f}%` ({trade_stats['win_trades']}승 {trade_stats['loss_trades']}패)\n"
                message += f" ▫️ 평균 보유 기간: `{trade_stats['avg_holding_days']:.1f}일`\n"
                message += f" ▫️ 거래당 평균 손익률: `{self.format_percentage(trade_stats['avg_return'])}`\n\n"

        else:
            message += "🇰🇷 *한국주식 계좌*\n"
            message += "❌ 계좌 정보를 가져올 수 없습니다\n\n"

        # ========== KR Holdings ==========
        message += "━" * 20 + "\n"

        if kr_portfolio:
            message += f"🇰🇷 *한국 보유종목* ({len(kr_portfolio)}개)\n"

            for i, stock in enumerate(kr_portfolio, 1):
                stock_name = stock.get('stock_name', 'Unknown')
                stock_code = stock.get('stock_code', '')
                quantity = stock.get('quantity', 0)
                profit_amount = stock.get('profit_amount', 0)
                profit_rate = stock.get('profit_rate', 0)
                eval_amount = stock.get('eval_amount', 0)
                avg_price = stock.get('avg_price', 0)

                # Return status
                if profit_rate > 0:
                    status_emoji = "⬆️"
                elif profit_rate < 0:
                    status_emoji = "⬇️"
                else:
                    status_emoji = "➖"

                # Stock information
                message += f"\n*{i}. {stock_name}* ({stock_code}) {status_emoji}\n"
                message += f"  평가: `{self.format_currency(eval_amount)}`\n"
                message += f"  단가: `{self.format_currency(avg_price)}` ({quantity}주)\n"
                message += f"  손익: `{self.format_currency_with_sign(profit_amount)}`  |  {self.format_percentage(profit_rate)}\n"

        else:
            message += "🇰🇷 *한국 보유종목*: 없음\n"

        return message


    async def get_trading_data(self) -> Tuple[List, Dict]:
        """
        Fetch trading data for KR market

        Returns:
            (kr_portfolio, kr_account_summary) tuple
        """
        kr_portfolio = []
        kr_account_summary = {}

        # Fetch KR trading data
        try:
            kr_trader = DomesticStockTrading(mode=self.trading_mode)

            logger.info("Fetching KR portfolio data...")
            kr_portfolio = kr_trader.get_portfolio()

            logger.info("Fetching KR account summary...")
            kr_account_summary = kr_trader.get_account_summary() or {}

            logger.info(f"KR data fetch complete: {len(kr_portfolio)} holdings")

        except Exception as e:
            logger.error(f"Error fetching KR trading data: {str(e)}")

        return kr_portfolio, kr_account_summary

    async def send_portfolio_report(self) -> bool:
        """
        Send portfolio report to Telegram

        Returns:
            Success status
        """
        try:
            logger.info("Starting portfolio report generation...")

            # Fetch trading data
            kr_portfolio, kr_account_summary = await self.get_trading_data()

            # Generate message
            message = self.create_portfolio_message(
                kr_portfolio, kr_account_summary
            )

            logger.info("Sending telegram message...")
            # Send to main channel
            success = await self.telegram_bot.send_message(self.chat_id, message)

            if success:
                logger.info("Portfolio report sent successfully!")
            else:
                logger.error("Failed to send portfolio report!")

            # Send to broadcast channels and await completion before returning
            if self.broadcast_languages:
                try:
                    await self._send_translated_portfolio_report(message)
                except Exception as e:
                    logger.error(f"Broadcast portfolio report failed: {e}")

            return success

        except Exception as e:
            logger.error(f"Error sending portfolio report: {str(e)}")
            return False

    async def _send_translated_portfolio_report(self, original_message: str):
        """
        Send translated portfolio report to additional language channels

        Args:
            original_message: Original Korean message
        """
        try:
            import sys
            from pathlib import Path

            # Add cores directory to path for importing translator agent
            cores_path = Path(__file__).parent.parent / "cores"
            if str(cores_path) not in sys.path:
                sys.path.insert(0, str(cores_path))

            from agents.telegram_translator_agent import translate_telegram_message

            for lang in self.broadcast_languages:
                try:
                    # Get channel ID for this language
                    channel_id = self.broadcast_channel_ids.get(lang)
                    if not channel_id:
                        logger.warning(f"No channel ID configured for language: {lang}")
                        continue

                    logger.info(f"Translating portfolio report to {lang}")

                    # Translate message
                    translated_message = await translate_telegram_message(
                        original_message,
                        model="gpt-5-nano",
                        from_lang="ko",
                        to_lang=lang
                    )

                    # Send translated message
                    success = await self.telegram_bot.send_message(channel_id, translated_message)

                    if success:
                        logger.info(f"Portfolio report sent successfully to {lang} channel")
                    else:
                        logger.error(f"Failed to send portfolio report to {lang} channel")

                except Exception as e:
                    logger.error(f"Error sending portfolio report to {lang}: {str(e)}")

        except Exception as e:
            logger.error(f"Error in _send_translated_portfolio_report: {str(e)}")

    async def send_simple_status(self, status_type: str = "morning") -> bool:
        """
        Send simple status message

        Args:
            status_type: Status type ('morning', 'evening', 'market_close', etc.)

        Returns:
            Success status
        """
        try:
            current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            mode_emoji = "🧪" if self.trading_mode == "demo" else "💰"

            # Status message settings
            status_messages = {
                "morning": "🌅 **Pre-Market Check**",
                "evening": "🌆 **Post-Market Summary**",
                "market_close": "🔔 **Market Close**",
                "weekend": "🏖️ **Weekend Status Check**"
            }

            title = status_messages.get(status_type, "📊 **Status Check**")

            # Fetch account summaries
            _, kr_account_summary = await self.get_trading_data()

            message = f"{title} {mode_emoji}\n"
            message += f"📅 {current_time}\n\n"

            # KR account summary
            if kr_account_summary:
                total_eval = kr_account_summary.get('total_eval_amount', 0)
                total_profit = kr_account_summary.get('total_profit_amount', 0)
                total_profit_rate = kr_account_summary.get('total_profit_rate', 0)

                profit_emoji = "📈" if total_profit >= 0 else "📉"

                message += f"🇰🇷 *Korea*\n"
                message += f"💼 Total Value: {self.format_currency(total_eval)}\n"
                message += f"{profit_emoji} P/L: {self.format_currency_with_sign(total_profit)} ({self.format_percentage(total_profit_rate)})\n"
            else:
                message += "🇰🇷 ❌ Failed to retrieve account info\n"

            message += "\n🤖 Automated Status Check"

            success = await self.telegram_bot.send_message(self.chat_id, message)

            if success:
                logger.info(f"{status_type} status message sent successfully!")
                return True
            else:
                logger.error(f"Failed to send {status_type} status message!")
                return False

        except Exception as e:
            logger.error(f"Error sending status message: {str(e)}")
            return False


async def main():
    """Main function"""
    import argparse

    parser = argparse.ArgumentParser(description="Portfolio Telegram Reporter")
    parser.add_argument("--mode", choices=["demo", "real"],
                       help=f"Trading mode (demo: paper trading, real: live trading, default: {_cfg['default_mode']})")
    parser.add_argument("--type", choices=["full", "simple", "morning", "evening", "market_close", "weekend"],
                       default="full", help="Report type")
    parser.add_argument("--token", help="Telegram bot token")
    parser.add_argument("--chat-id", help="Telegram channel ID")
    parser.add_argument("--broadcast-languages", type=str, default="",
                       help="Additional languages for parallel telegram channel broadcasting (comma-separated, e.g., 'en,ja,zh')")

    args = parser.parse_args()

    # Parse broadcast languages
    broadcast_languages = [lang.strip() for lang in args.broadcast_languages.split(",") if lang.strip()]

    try:
        # Initialize reporter (uses yaml config if mode is None)
        reporter = PortfolioTelegramReporter(
            telegram_token=args.token,
            chat_id=args.chat_id,
            trading_mode=args.mode,  # Uses yaml's default_mode if None
            broadcast_languages=broadcast_languages
        )

        # Execute based on report type
        if args.type == "full":
            success = await reporter.send_portfolio_report()
        else:
            # Simple or specific status message
            status_type = args.type if args.type != "simple" else "morning"
            success = await reporter.send_simple_status(status_type)

        if success:
            logger.info("Program completed successfully")
            sys.exit(0)
        else:
            logger.error("Program completed with failure")
            sys.exit(1)

    except Exception as e:
        logger.error(f"Error during program execution: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
