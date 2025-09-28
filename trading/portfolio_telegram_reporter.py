#!/usr/bin/env python3
"""
포트폴리오 텔레그램 리포터
- 주기적으로 계좌 및 포트폴리오 상황을 텔레그램으로 전송
- crontab으로 실행 가능
"""

import asyncio
import os
import sys
import logging
import datetime
import yaml
from pathlib import Path
from typing import Dict, Any, List
from dotenv import load_dotenv

# 현재 스크립트의 디렉토리를 기준으로 경로 설정
SCRIPT_DIR = Path(__file__).parent
TRADING_DIR = SCRIPT_DIR

# trading 모듈 import를 위한 경로 추가
PARENT_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(PARENT_DIR))
sys.path.insert(0, str(TRADING_DIR))

# 설정파일 로딩
CONFIG_FILE = TRADING_DIR / "config" / "kis_devlp.yaml"
config_root = os.path.join(os.path.expanduser("~"), "src", "hantoo", ".HKIS", "config")
CONFIG_FILE = os.path.join(config_root, "kis_devlp.yaml")
with open(CONFIG_FILE, encoding="UTF-8") as f:
    _cfg = yaml.load(f, Loader=yaml.FullLoader)

# 로컬 모듈 import
from trading.domestic_stock_trading import DomesticStockTrading
from telegram_bot_agent import TelegramBotAgent

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(SCRIPT_DIR / 'portfolio_reporter.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# env파일 로드
SCRIPT_DIR = Path(__file__).parent.absolute()  # trading/
PROJECT_ROOT = SCRIPT_DIR.parent              # project_root/
ENV_FILE = PROJECT_ROOT / ".env"
load_dotenv(dotenv_path=str(ENV_FILE))

class PortfolioTelegramReporter:
    """포트폴리오 상황을 텔레그램으로 리포트하는 클래스"""

    def __init__(self, telegram_token: str = None, chat_id: str = None, trading_mode: str = None):
        """
        초기화

        Args:
            telegram_token: 텔레그램 봇 토큰
            chat_id: 텔레그램 채널 ID
            trading_mode: 트레이딩 모드 ('demo' 또는 'real', None이면 yaml 설정 사용)
        """
        # 텔레그램 설정
        self.telegram_token = telegram_token or os.environ.get("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHANNEL_ID")
        
        if not self.telegram_token:
            raise ValueError("텔레그램 봇 토큰이 필요합니다. 환경 변수 TELEGRAM_BOT_TOKEN 또는 파라미터로 제공해주세요.")
        
        if not self.chat_id:
            raise ValueError("텔레그램 채널 ID가 필요합니다. 환경 변수 TELEGRAM_CHANNEL_ID 또는 파라미터로 제공해주세요.")

        # 트레이딩 설정 - yaml 파일의 default_mode를 기본값으로 사용
        self.trading_mode = trading_mode if trading_mode is not None else _cfg["default_mode"]
        self.telegram_bot = TelegramBotAgent(token=self.telegram_token)
        
        logger.info(f"PortfolioTelegramReporter 초기화 완료")
        logger.info(f"트레이딩 모드: {self.trading_mode} (yaml 설정: {_cfg['default_mode']})")

    def format_currency(self, amount: float) -> str:
        """금액을 한국 원화 형식으로 포맷팅"""
        return f"{amount:,.0f}원" if amount else "0원"

    def format_percentage(self, rate: float) -> str:
        """퍼센트를 포맷팅"""
        return f"{rate:+.2f}%" if rate else "0.00%"

    def create_portfolio_message(self, portfolio: List[Dict[str, Any]], account_summary: Dict[str, Any]) -> str:
        """
        포트폴리오와 계좌 요약을 기반으로 텔레그램 메시지 생성

        Args:
            portfolio: 포트폴리오 데이터
            account_summary: 계좌 요약 데이터

        Returns:
            포맷팅된 텔레그램 메시지
        """
        current_time = datetime.datetime.now().strftime("%m/%d %H:%M")
        mode_emoji = "🧪" if self.trading_mode == "demo" else "💰"
        mode_text = "모의투자" if self.trading_mode == "demo" else "실전투자"

        # 헤더
        message = f"📊 포트폴리오 리포트 {mode_emoji}\n"
        message += f"🕐 {current_time} | {mode_text}\n\n"

        # 계좌 요약
        if account_summary:
            total_eval = account_summary.get('total_eval_amount', 0)
            total_profit = account_summary.get('total_profit_amount', 0)
            total_profit_rate = account_summary.get('total_profit_rate', 0)
            available = account_summary.get('available_amount', 0)

            profit_emoji = "📈" if total_profit >= 0 else "📉"
            profit_sign = "+" if total_profit >= 0 else ""

            message += f"💰 총 평가액: `{self.format_currency(total_eval)}`\n"
            message += f"{profit_emoji} 평가손익: `{profit_sign}{self.format_currency(total_profit)}` "
            message += f"({self.format_percentage(total_profit_rate)})\n"

            if available > 0:
                message += f"💳 주문가능: `{self.format_currency(available)}`\n"
            message += "\n"
        else:
            message += "❌ 계좌 정보를 가져올 수 없습니다\n\n"

        # 보유 종목
        if portfolio:
            message += f"📈 보유종목 ({len(portfolio)}개)\n"

            for i, stock in enumerate(portfolio, 1):
                stock_name = stock.get('stock_name', '알 수 없음')
                stock_code = stock.get('stock_code', '')
                quantity = stock.get('quantity', 0)
                current_price = stock.get('current_price', 0)
                profit_amount = stock.get('profit_amount', 0)
                profit_rate = stock.get('profit_rate', 0)
                eval_amount = stock.get('eval_amount', 0)
                avg_price = stock.get('avg_price', 0)

                # 수익률 상태
                if profit_rate > 0:
                    status_emoji = "🔺"
                elif profit_rate < 0:
                    status_emoji = "🔻"
                else:
                    status_emoji = "➖"

                profit_sign = "+" if profit_amount >= 0 else ""

                # 종목별 정보
                message += f"\n*{i}. {stock_name}* ({stock_code}) {status_emoji}\n"
                message += f"  평가금액: `{self.format_currency(eval_amount)}`\n"
                message += f"  평균단가: `{self.format_currency(avg_price)}` ({quantity}주)\n"
                message += f"  손익: `{profit_sign}{self.format_currency(profit_amount)}`  |  {self.format_percentage(profit_rate)}\n"

        else:
            message += "📭 *보유종목*: 없음\n\n"

        return message


    async def get_trading_data(self) -> tuple:
        """
        트레이딩 데이터를 가져옴

        Returns:
            (portfolio, account_summary) 튜플
        """
        try:
            trader = DomesticStockTrading(mode=self.trading_mode)
            
            logger.info("포트폴리오 데이터 조회 중...")
            portfolio = trader.get_portfolio()
            
            logger.info("계좌 요약 데이터 조회 중...")
            account_summary = trader.get_account_summary()
            
            logger.info(f"데이터 조회 완료: 보유종목 {len(portfolio)}개")
            return portfolio, account_summary
            
        except Exception as e:
            logger.error(f"트레이딩 데이터 조회 중 오류: {str(e)}")
            return [], {}

    async def send_portfolio_report(self) -> bool:
        """
        포트폴리오 리포트를 텔레그램으로 전송

        Returns:
            전송 성공 여부
        """
        try:
            logger.info("포트폴리오 리포트 생성 시작...")
            
            # 트레이딩 데이터 조회
            portfolio, account_summary = await self.get_trading_data()
            
            # 메시지 생성
            message = self.create_portfolio_message(portfolio, account_summary)
            
            logger.info("텔레그램 메시지 전송 중...")
            # 텔레그램 전송
            success = await self.telegram_bot.send_message(self.chat_id, message)
            
            if success:
                logger.info("포트폴리오 리포트 전송 성공!")
                return True
            else:
                logger.error("포트폴리오 리포트 전송 실패!")
                return False
                
        except Exception as e:
            logger.error(f"포트폴리오 리포트 전송 중 오류: {str(e)}")
            return False

    async def send_simple_status(self, status_type: str = "morning") -> bool:
        """
        간단한 상태 메시지 전송

        Args:
            status_type: 상태 타입 ('morning', 'evening', 'market_close' 등)

        Returns:
            전송 성공 여부
        """
        try:
            current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            mode_emoji = "🧪" if self.trading_mode == "demo" else "💰"
            
            # 상태별 메시지 설정
            status_messages = {
                "morning": "🌅 **장 시작 전 체크**",
                "evening": "🌆 **장 마감 후 정리**", 
                "market_close": "🔔 **시장 마감**",
                "weekend": "🏖️ **주말 상태 체크**"
            }
            
            title = status_messages.get(status_type, "📊 **상태 체크**")
            
            # 간단한 계좌 요약만 조회
            _, account_summary = await self.get_trading_data()
            
            message = f"{title} {mode_emoji}\n"
            message += f"📅 {current_time}\n\n"
            
            if account_summary:
                total_eval = account_summary.get('total_eval_amount', 0)
                total_profit = account_summary.get('total_profit_amount', 0)
                total_profit_rate = account_summary.get('total_profit_rate', 0)
                
                profit_emoji = "📈" if total_profit >= 0 else "📉"
                
                message += f"💼 총 평가: {self.format_currency(total_eval)}\n"
                message += f"{profit_emoji} 손익: {self.format_currency(total_profit)} ({self.format_percentage(total_profit_rate)})\n"
            else:
                message += "❌ 계좌 정보 조회 실패\n"
            
            message += "\n🤖 자동 상태 체크"
            
            success = await self.telegram_bot.send_message(self.chat_id, message)
            
            if success:
                logger.info(f"{status_type} 상태 메시지 전송 성공!")
                return True
            else:
                logger.error(f"{status_type} 상태 메시지 전송 실패!")
                return False
                
        except Exception as e:
            logger.error(f"상태 메시지 전송 중 오류: {str(e)}")
            return False


async def main():
    """메인 함수"""
    import argparse
    
    parser = argparse.ArgumentParser(description="포트폴리오 텔레그램 리포터")
    parser.add_argument("--mode", choices=["demo", "real"], 
                       help=f"트레이딩 모드 (demo: 모의투자, real: 실전투자, 기본값: {_cfg['default_mode']})")
    parser.add_argument("--type", choices=["full", "simple", "morning", "evening", "market_close", "weekend"], 
                       default="full", help="리포트 타입")
    parser.add_argument("--token", help="텔레그램 봇 토큰")
    parser.add_argument("--chat-id", help="텔레그램 채널 ID")
    
    args = parser.parse_args()
    
    try:
        # 리포터 초기화 (mode가 None이면 yaml 설정 사용)
        reporter = PortfolioTelegramReporter(
            telegram_token=args.token,
            chat_id=args.chat_id,
            trading_mode=args.mode  # None이면 yaml의 default_mode 사용
        )
        
        # 리포트 타입에 따른 실행
        if args.type == "full":
            success = await reporter.send_portfolio_report()
        else:
            # simple 또는 특정 상태 메시지
            status_type = args.type if args.type != "simple" else "morning"
            success = await reporter.send_simple_status(status_type)
        
        if success:
            logger.info("프로그램 실행 완료 - 성공")
            sys.exit(0)
        else:
            logger.error("프로그램 실행 완료 - 실패")
            sys.exit(1)
            
    except Exception as e:
        logger.error(f"프로그램 실행 중 오류: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
