#!/usr/bin/env python3
"""
주식 트래킹 및 매매 에이전트

이 모듈은 AI 기반 주식 분석 보고서를 활용하여 매수/매도 의사결정을 수행하고
거래 내역을 관리하는 시스템입니다.

주요 기능:
1. 분석 보고서 기반의 매매 시나리오 생성
2. 종목 매수/매도 관리 (최대 10개 슬랏)
3. 거래 내역 및 수익률 추적
4. 텔레그램 채널을 통한 결과 공유
"""

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
from typing import List, Dict, Any, Tuple

from telegram import Bot
from telegram.error import TelegramError

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"stock_tracking_{datetime.now().strftime('%Y%m%d')}.log"),
    ],
)
logger = logging.getLogger(__name__)

# MCP 관련 임포트
from mcp_agent.agents.agent import Agent
from mcp_agent.app import MCPApp
from mcp_agent.workflows.llm.augmented_llm import RequestParams
from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM

# MCPApp 인스턴스 생성
app = MCPApp(name="stock_tracking")


class StockTrackingAgent:
    """주식 트래킹 및 매매 에이전트"""

    def __init__(
        self, db_path: str = "stock_tracking_db.sqlite", telegram_token: str = None
    ):
        """
        에이전트 초기화

        Args:
            db_path: SQLite 데이터베이스 파일 경로
            telegram_token: 텔레그램 봇 토큰
        """
        self.max_slots = 10  # 최대 보유 가능 종목 수
        self.message_queue = []  # 텔레그램 메시지 저장용
        self.trading_agent = None
        self.db_path = db_path
        self.conn = None
        self.cursor = None

        # 텔레그램 봇 토큰 설정
        self.telegram_token = telegram_token or os.environ.get("TELEGRAM_BOT_TOKEN")
        self.telegram_bot = None
        if self.telegram_token:
            self.telegram_bot = Bot(token=self.telegram_token)

    async def initialize(self):
        """필요한 테이블 생성 및 초기화"""
        logger.info("트래킹 에이전트 초기화 시작")

        # SQLite 연결 초기화
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row  # 결과를 딕셔너리 형태로 반환
        self.cursor = self.conn.cursor()

        # 트레이딩 시나리오 생성 에이전트 초기화
        self.trading_agent = Agent(
            name="trading_scenario_agent",
            instruction="""당신은 신중하고 분석적인 주식 매매 시나리오 생성 전문가입니다.
            기본적으로는 가치투자 원칙을 따르되, 상승 모멘텀이 확인될 때는 보다 적극적으로 진입합니다.
            주식 분석 보고서를 읽고 매매 시나리오를 JSON 형식으로 생성해야 합니다.
            
            ### 중요: 현재 포트폴리오 분석
            먼저 현재 보유 중인 종목 현황을 확인하세요. stock_holdings 테이블에 있는 기존 포트폴리오 정보를 분석하고
            다음과 같은 사항을 고려하세요:
            
            1. 현재 보유 종목 수 (최대 10개 슬랏 제한)
            2. 산업군 분포 (특정 산업군에 과다 노출되어 있는지 확인)
            3. 투자 기간 분포 (단기/중기/장기 투자 비율)
            4. 현재 포트폴리오의 평균 수익률
            
            ### 종목 평가 및 결정
            위 포트폴리오 상황을 고려하여 신규 종목에 대해 다음을 평가하세요:
            
            1. 종목에 대한 매수 적정성을 평가하세요 (1~10점).
               - 8~10점: 우수한 매수 기회 (확실한 근거 + 모멘텀)
               - 7점: 양호한 매수 기회 (기본 조건 만족)
               - 6점: 보통 (모멘텀이 있다면 고려)
               - 5점 이하: 매수 부적합
            
            2. 현재 진입 여부를 결정하세요 (진입/관망).
               - "진입" 결정은 매우 신중하게 내려야 합니다
               - 다음과 같은 경우 반드시 "관망"으로 결정하세요:
                 * 현재 보유 종목이 7개 이상인 경우 매수 점수 8점 미만
                 * 현재 보유 종목과 동일 산업군이 이미 2개 이상 있는 경우
                 * 재무상태에 의구심이 있는 경우
                 * 명확한 성장 동력이 확인되지 않는 경우
            
            3. 목표가를 제시해주세요 (현실적이고 합리적인 수준).
            4. 손절매 가격을 제시해주세요 (최대 허용 손실 기준).
            5. 투자 기간을 제안해주세요 (단기: 1개월 이내, 중기: 1~3개월, 장기: 3개월 이상).
               - 현재 포트폴리오의 투자 기간 분포를 고려하여 균형을 맞추세요
            6. 핵심 투자 근거를 3줄 이내로 요약해주세요.
            7. 이 종목의 산업군(섹터)을 한 단어로 명시해주세요(예: 반도체, 자동차, 바이오, 소프트웨어 등).
            
            ### 종목 적합성 판단
            다음 질문들을 고려하여 매수 적정성을 판단하세요:
            - 이 종목은 현재 주가 대비 상승여력이 충분한가?
            - 이 종목의 재무 상태는 건전한가?
            - 이 종목의 성장 가능성은 명확한가?
            - 이 종목은 현재 거래활동 증가나 관심 상승을 보이는가? (신규 추가)
            - 이 종목은 현재 포트폴리오와 적절한 분산효과를 제공하는가?
            
            **모멘텀 요소 특별 고려사항:**
            - 최근 거래량이 평소보다 크게 증가한 경우: 긍정적 요소로 고려
            - 투자자별 거래량에서 기관/외인 매수 우위: 긍정적 신호
            - 개인 투자자 대비 기관 투자자 순매수 증가: 신뢰도 높은 신호
            - 이러한 모멘텀 신호들이 확인될 때는 기존 매수 기준을 다소 완화하여 적용
            
            분석 보고서의 '투자 전략 및 의견' 섹션에 특히 주목하세요.
            주가, 목표가 및 손절가 정보는 보고서의 기술적 분석 부분에서 찾을 수 있습니다.
            모멘텀 요소인 거래량, 투자자별 거래량은 too call(name : kospi_kosdaq-get_stock_ohlcv, kospi_kosdaq-get_stock_trading_volume)을 사용하여 조회할 수 있습니다. 
            tool call 시 보고서 최상단의 '발행일: '이라고 표기된 날짜를 기준으로 적절한 범위의 데이터를 조회하면 됩니다.
            
            ### 응답 형식
            JSON 형식으로 다음과 같이 응답해주세요:
            {
                "portfolio_analysis": "현재 포트폴리오 상황 요약 (슬랏 수, 산업군 분포 등)",
                "buy_score": 1~10 사이의 점수,
                "decision": "진입" 또는 "관망",
                "target_price": 숫자 (원),
                "stop_loss": 숫자 (원),
                "investment_period": "단기" 또는 "중기" 또는 "장기",
                "rationale": "핵심 투자 근거를 간략히 설명",
                "sector": "이 종목의 산업군/섹터 이름",
                "considerations": "포트폴리오 맥락에서 이 종목 선택의 이유"
            }
            """,
            server_names=["kospi_kosdaq", "sqlite"],
        )

        # 데이터베이스 테이블 생성
        await self._create_tables()

        logger.info("트래킹 에이전트 초기화 완료")
        return True

    async def _create_tables(self):
        """필요한 데이터베이스 테이블 생성"""
        try:
            # 보유종목 테이블 생성
            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS stock_holdings (
                    ticker TEXT PRIMARY KEY,
                    company_name TEXT NOT NULL,
                    buy_price REAL NOT NULL,
                    buy_date TEXT NOT NULL,
                    current_price REAL,
                    last_updated TEXT,
                    scenario TEXT,
                    target_price REAL,
                    stop_loss REAL,
                    quantity INTEGER NOT NULL DEFAULT 0
                )
            """)

            # 매매 이력 테이블 생성
            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS trading_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT NOT NULL,
                    company_name TEXT NOT NULL,
                    buy_price REAL NOT NULL,
                    buy_date TEXT NOT NULL,
                    sell_price REAL NOT NULL,
                    sell_date TEXT NOT NULL,
                    profit_rate REAL NOT NULL,
                    holding_days INTEGER NOT NULL,
                    scenario TEXT,
                    quantity INTEGER NOT NULL
                )
            """)

            # 잔고 테이블 생성
            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS portfolio_balance (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id TEXT NOT NULL,             -- 계좌 식별자
                    balance REAL NOT NULL DEFAULT 0,      -- 보유 금액
                    currency TEXT NOT NULL DEFAULT 'KRW', -- 통화 단위
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 변경사항 저장
            self.conn.commit()

            logger.info("데이터베이스 테이블 생성 완료")

        except Exception as e:
            logger.error(f"테이블 생성 중 오류: {str(e)}")
            logger.error(traceback.format_exc())
            raise

    async def _extract_ticker_info(self, report_path: str) -> Tuple[str, str]:
        """
        보고서 파일 경로에서 종목 코드와 이름 추출

        Args:
            report_path: 보고서 파일 경로

        Returns:
            Tuple[str, str]: 종목 코드, 종목 이름
        """
        try:
            # 파일명에서 정규표현식으로 ticker와 company_name 추출
            file_name = Path(report_path).stem

            # 정규표현식을 사용한 파싱
            pattern = r"^([A-Za-z0-9]+)_([^_]+)"
            match = re.match(pattern, file_name)

            if match:
                ticker = match.group(1)
                company_name = match.group(2)
                return ticker, company_name
            else:
                # 기존 방식도 유지
                parts = file_name.split("_")
                if len(parts) >= 2:
                    return parts[0], parts[1]

            logger.error(f"파일명에서 종목 정보를 추출할 수 없습니다: {file_name}")
            return "", ""
        except Exception as e:
            logger.error(f"종목 정보 추출 중 오류: {str(e)}")
            return "", ""

    async def _get_current_stock_price(self, ticker: str) -> float:
        """
        현재 주가 조회

        Args:
            ticker: 종목 코드

        Returns:
            float: 현재 주가
        """
        try:
            from pykrx.stock import stock_api
            import datetime

            # 오늘 날짜
            today = datetime.datetime.now().strftime("%Y%m%d")

            # 가장 최근 영업일 구하기
            trade_date = stock_api.get_nearest_business_day_in_a_week(today, prev=True)
            logger.info(f"타겟 날짜: {trade_date}")

            # 해당 거래일의 OHLCV 데이터 가져오기
            df = stock_api.get_market_ohlcv_by_ticker(trade_date)

            # 특정 종목 데이터 추출
            if ticker in df.index:
                # 종가(Close) 추출
                current_price = df.loc[ticker, "종가"]
                logger.info(f"{ticker} 종목 현재가: {current_price:,.0f}원")
                return float(current_price)
            else:
                logger.warning(f"{ticker} 종목을 찾을 수 없습니다.")
                # DB에서 마지막 저장된 가격 확인
                try:
                    self.cursor.execute(
                        "SELECT current_price FROM stock_holdings WHERE ticker = ?",
                        (ticker,),
                    )
                    row = self.cursor.fetchone()
                    if row and row[0]:
                        last_price = float(row[0])
                        logger.warning(
                            f"{ticker} 현재가 조회 실패, 마지막 가격 사용: {last_price}"
                        )
                        return last_price
                except:
                    pass
                return 0.0

        except Exception as e:
            logger.error(f"{ticker} 현재 주가 조회 중 오류: {str(e)}")
            logger.error(traceback.format_exc())
            # 오류 발생 시 DB에서 마지막 저장된 가격 확인
            try:
                self.cursor.execute(
                    "SELECT current_price FROM stock_holdings WHERE ticker = ?",
                    (ticker,),
                )
                row = self.cursor.fetchone()
                if row and row[0]:
                    last_price = float(row[0])
                    logger.warning(
                        f"{ticker} 현재가 조회 실패, 마지막 가격 사용: {last_price}"
                    )
                    return last_price
            except:
                pass
            return 0.0

    async def _get_trading_value_rank_change(self, ticker: str) -> Tuple[float, str]:
        """
        종목의 거래대금 랭킹 변화를 계산

        Args:
            ticker: 종목 코드

        Returns:
            Tuple[float, str]: 랭킹 변화율, 분석 결과 메시지
        """
        try:
            from pykrx.stock import stock_api
            import datetime
            import pandas as pd

            # 오늘 날짜
            today = datetime.datetime.now().strftime("%Y%m%d")

            # 최근 2개 영업일 구하기
            recent_date = stock_api.get_nearest_business_day_in_a_week(today, prev=True)
            previous_date_obj = datetime.datetime.strptime(
                recent_date, "%Y%m%d"
            ) - timedelta(days=1)
            previous_date = stock_api.get_nearest_business_day_in_a_week(
                previous_date_obj.strftime("%Y%m%d"), prev=True
            )

            logger.info(f"최근 영업일: {recent_date}, 이전 영업일: {previous_date}")

            # 해당 거래일의 OHLCV 데이터 가져오기 (거래대금 포함)
            recent_df = stock_api.get_market_ohlcv_by_ticker(recent_date)
            previous_df = stock_api.get_market_ohlcv_by_ticker(previous_date)

            # 거래대금으로 정렬하여 랭킹 생성
            recent_rank = recent_df.sort_values(
                by="거래대금", ascending=False
            ).reset_index()
            previous_rank = previous_df.sort_values(
                by="거래대금", ascending=False
            ).reset_index()

            # 티커에 대한 랭킹 찾기
            if ticker in recent_rank["티커"].values:
                recent_ticker_rank = (
                    recent_rank[recent_rank["티커"] == ticker].index[0] + 1
                )
            else:
                recent_ticker_rank = 0

            if ticker in previous_rank["티커"].values:
                previous_ticker_rank = (
                    previous_rank[previous_rank["티커"] == ticker].index[0] + 1
                )
            else:
                previous_ticker_rank = 0

            # 랭킹이 없을 경우 리턴
            if recent_ticker_rank == 0 or previous_ticker_rank == 0:
                return 0, f"거래대금 랭킹 정보 없음"

            # 랭킹 변화 계산
            rank_change = (
                previous_ticker_rank - recent_ticker_rank
            )  # 양수면 순위 상승, 음수면 순위 하락
            rank_change_percentage = (rank_change / previous_ticker_rank) * 100

            # 랭킹 정보 및 거래대금 데이터
            recent_value = (
                int(recent_df.loc[ticker, "거래대금"])
                if ticker in recent_df.index
                else 0
            )
            previous_value = (
                int(previous_df.loc[ticker, "거래대금"])
                if ticker in previous_df.index
                else 0
            )
            value_change_percentage = (
                ((recent_value - previous_value) / previous_value * 100)
                if previous_value > 0
                else 0
            )

            result_msg = (
                f"거래대금 랭킹: {recent_ticker_rank}위 (이전: {previous_ticker_rank}위, "
                f"변화: {'▲' if rank_change > 0 else '▼' if rank_change < 0 else '='}{abs(rank_change)}), "
                f"거래대금: {recent_value:,}원 (이전: {previous_value:,}원, "
                f"변화: {'▲' if value_change_percentage > 0 else '▼' if value_change_percentage < 0 else '='}{abs(value_change_percentage):.1f}%)"
            )

            logger.info(f"{ticker} {result_msg}")
            return rank_change_percentage, result_msg

        except Exception as e:
            logger.error(f"{ticker} 거래대금 랭킹 분석 중 오류: {str(e)}")
            logger.error(traceback.format_exc())
            return 0, "거래대금 랭킹 분석 실패"

    async def _is_ticker_in_holdings(self, ticker: str) -> bool:
        """
        종목이 이미 보유 중인지 확인

        Args:
            ticker: 종목 코드

        Returns:
            bool: 보유 중이면 True, 아니면 False
        """
        try:
            self.cursor.execute(
                "SELECT COUNT(*) FROM stock_holdings WHERE ticker = ?", (ticker,)
            )
            count = self.cursor.fetchone()[0]
            return count > 0
        except Exception as e:
            logger.error(f"보유 종목 확인 중 오류: {str(e)}")
            return False

    async def _get_current_slots_count(self) -> int:
        """현재 보유 중인 종목 수 조회"""
        try:
            self.cursor.execute("SELECT COUNT(*) FROM stock_holdings")
            count = self.cursor.fetchone()[0]
            return count
        except Exception as e:
            logger.error(f"보유 종목 수 조회 중 오류: {str(e)}")
            return 0

    async def _get_current_balance(self, account_id: str = "default_account") -> float:
        """
        현재 잔고 조회

        Args:
            account_id: 계좌 식별자

        Returns:
            float: 현재 잔고
        """
        try:
            self.cursor.execute(
                "SELECT balance FROM portfolio_balance WHERE account_id = ? ORDER BY updated_at DESC LIMIT 1",
                (account_id,),
            )
            row = self.cursor.fetchone()
            if row:
                return float(row[0])
            else:
                # 잔고 정보가 없으면 0 반환
                return 0.0
        except Exception as e:
            logger.error(f"잔고 조회 중 오류: {str(e)}")
            return 0.0

    async def _update_balance(self, amount: float, account_id: str = "default_account"):
        """
        잔고 업데이트

        Args:
            amount: 변경할 금액 (양수: 입금, 음수: 출금)
            account_id: 계좌 식별자
        """
        try:
            current_balance = await self._get_current_balance(account_id)
            new_balance = current_balance + amount

            self.cursor.execute(
                """
                INSERT INTO portfolio_balance (account_id, balance, updated_at)
                VALUES (?, ?, ?)
                """,
                (account_id, new_balance, datetime.now()),
            )
            self.conn.commit()
            logger.info(
                f"잔고 업데이트: {current_balance:,.0f}원 -> {new_balance:,.0f}원"
            )
        except Exception as e:
            logger.error(f"잔고 업데이트 중 오류: {str(e)}")

    async def _get_stock_quantity(self, ticker: str) -> int:
        """
        보유 종목의 수량 조회

        Args:
            ticker: 종목 코드

        Returns:
            int: 보유 수량
        """
        try:
            self.cursor.execute(
                "SELECT quantity FROM stock_holdings WHERE ticker = ?", (ticker,)
            )
            row = self.cursor.fetchone()
            if row:
                return int(row[0])
            else:
                return 0
        except Exception as e:
            logger.error(f"{ticker} 보유 수량 조회 중 오류: {str(e)}")
            return 0

    async def _check_sector_diversity(self, sector: str) -> bool:
        """
        동일 산업군 과다 투자 여부 확인

        Args:
            sector: 산업군 이름

        Returns:
            bool: 투자 가능 여부 (True: 가능, False: 과다)
        """
        try:
            # 산업군 정보가 없거나 유효하지 않으면 제한하지 않음
            if not sector or sector == "알 수 없음":
                return True

            # 현재 보유 종목의 시나리오에서 산업군 정보 추출
            self.cursor.execute("SELECT scenario FROM stock_holdings")
            holdings_scenarios = self.cursor.fetchall()

            sectors = []
            for row in holdings_scenarios:
                if row[0]:
                    try:
                        scenario_data = json.loads(row[0])
                        if "sector" in scenario_data:
                            sectors.append(scenario_data["sector"])
                    except:
                        pass

            # 동일 산업군 종목 수 계산
            same_sector_count = sum(
                1 for s in sectors if s and s.lower() == sector.lower()
            )

            # 동일 산업군이 3개 이상이거나 전체의 30% 이상이면 제한
            if same_sector_count >= 3 or (
                sectors and same_sector_count / len(sectors) >= 0.3
            ):
                logger.warning(
                    f"산업군 '{sector}' 과다 투자 위험: 현재 {same_sector_count}개 보유 중"
                )
                return False

            return True

        except Exception as e:
            logger.error(f"산업군 다양성 확인 중 오류: {str(e)}")
            return True  # 오류 발생 시 기본적으로 제한하지 않음

    async def _extract_trading_scenario(
        self, report_content: str, rank_change_msg: str = ""
    ) -> Dict[str, Any]:
        """
        보고서에서 매매 시나리오 추출

        Args:
            report_content: 분석 보고서 내용
            rank_change_msg: 거래대금 랭킹 변화 정보

        Returns:
            Dict: 매매 시나리오 정보
        """
        try:
            # 현재 보유 종목 정보 및 산업군 분포를 가져옴
            current_slots = await self._get_current_slots_count()

            # 현재 포트폴리오 정보 수집
            self.cursor.execute("""
                SELECT ticker, company_name, buy_price, current_price, scenario 
                FROM stock_holdings
            """)
            holdings = [dict(row) for row in self.cursor.fetchall()]

            # 산업군 분포 분석
            sector_distribution = {}
            investment_periods = {"단기": 0, "중기": 0, "장기": 0}

            for holding in holdings:
                scenario_str = holding.get("scenario", "{}")
                try:
                    if isinstance(scenario_str, str):
                        scenario_data = json.loads(scenario_str)

                        # 산업군 정보 수집
                        sector = scenario_data.get("sector", "알 수 없음")
                        sector_distribution[sector] = (
                            sector_distribution.get(sector, 0) + 1
                        )

                        # 투자 기간 정보 수집
                        period = scenario_data.get("investment_period", "중기")
                        investment_periods[period] = (
                            investment_periods.get(period, 0) + 1
                        )
                except:
                    pass

            # 포트폴리오 정보 문자열
            portfolio_info = f"""
            현재 보유 종목 수: {current_slots}/{self.max_slots}
            산업군 분포: {json.dumps(sector_distribution, ensure_ascii=False)}
            투자 기간 분포: {json.dumps(investment_periods, ensure_ascii=False)}
            """

            # LLM 호출하여 매매 시나리오 생성
            llm = await self.trading_agent.attach_llm(OpenAIAugmentedLLM)

            response = await llm.generate_str(
                message=f"""
                다음은 주식 종목에 대한 AI 분석 보고서입니다. 이 보고서를 기반으로 매매 시나리오를 생성해주세요.
                
                ### 현재 포트폴리오 상황:
                {portfolio_info}
                
                ### 거래대금 분석:
                {rank_change_msg}
                
                ### 보고서 내용:
                {report_content}
                """,
                request_params=RequestParams(model="gpt-5", maxTokens=6000),
            )

            # JSON 파싱
            try:
                # 마크다운 코드 블록에서 JSON 추출 시도 (```json ... ```)
                markdown_match = re.search(
                    r"```(?:json)?\s*({[\s\S]*?})\s*```", response, re.DOTALL
                )
                if markdown_match:
                    json_str = markdown_match.group(1)
                    # 마지막 쉼표 제거
                    json_str = re.sub(r",(\s*})", r"\1", json_str)
                    scenario_json = json.loads(json_str)
                    logger.info(
                        f"마크다운 코드 블록에서 파싱된 시나리오: {json.dumps(scenario_json, ensure_ascii=False)}"
                    )
                    return scenario_json

                # 일반 JSON 객체 추출 시도
                json_match = re.search(
                    r"({[\s\S]*?})(?:\s*$|\n\n)", response, re.DOTALL
                )
                if json_match:
                    json_str = json_match.group(1)
                    # 마지막 쉼표 제거
                    json_str = re.sub(r",(\s*})", r"\1", json_str)
                    scenario_json = json.loads(json_str)
                    logger.info(
                        f"일반 JSON 형식에서 파싱된 시나리오: {json.dumps(scenario_json, ensure_ascii=False)}"
                    )
                    return scenario_json

                # 전체 응답이 JSON인 경우
                # 마지막 쉼표 제거
                clean_response = re.sub(r",(\s*})", r"\1", response)
                scenario_json = json.loads(clean_response)
                logger.info(
                    f"전체 응답 시나리오: {json.dumps(scenario_json, ensure_ascii=False)}"
                )
                return scenario_json

            except Exception as json_err:
                logger.error(f"매매 시나리오 JSON 파싱 오류: {json_err}")
                logger.error(f"원본 응답: {response}")

                # 추가 복구 시도: 원본 응답에서 중괄호 사이의 내용을 추출
                try:
                    clean_response = re.sub(r"```(?:json)?|```", "", response).strip()
                    # 마지막 쉼표 제거
                    clean_response = re.sub(r",(\s*})", r"\1", clean_response)
                    scenario_json = json.loads(clean_response)
                    logger.info(
                        f"추가 복구로 파싱된 시나리오: {json.dumps(scenario_json, ensure_ascii=False)}"
                    )
                    return scenario_json
                except:
                    logger.error("추가 복구 시도도 실패")

                # 모든 파싱 시도 실패 시 기본값 반환
                return self._default_scenario()

        except Exception as e:
            logger.error(f"매매 시나리오 추출 중 오류: {str(e)}")
            logger.error(traceback.format_exc())
            return self._default_scenario()

    def _default_scenario(self) -> Dict[str, Any]:
        """기본 매매 시나리오 반환"""
        return {
            "portfolio_analysis": "분석 실패",
            "buy_score": 0,
            "decision": "관망",
            "target_price": 0,
            "stop_loss": 0,
            "investment_period": "단기",
            "rationale": "분석 실패",
            "sector": "알 수 없음",
            "considerations": "분석 실패",
        }

    async def analyze_report(self, pdf_report_path: str) -> Dict[str, Any]:
        """
        주식 분석 보고서를 분석하여 매매 의사결정

        Args:
            pdf_report_path: pdf 분석 보고서 파일 경로

        Returns:
            Dict: 매매 의사결정 결과
        """
        try:
            logger.info(f"보고서 분석 시작: {pdf_report_path}")

            # 파일 경로에서 종목 코드와 이름 추출
            ticker, company_name = await self._extract_ticker_info(pdf_report_path)

            if not ticker or not company_name:
                logger.error(f"종목 정보 추출 실패: {pdf_report_path}")
                return {"success": False, "error": "종목 정보 추출 실패"}

            # 이미 보유 중인 종목인지 확인
            is_holding = await self._is_ticker_in_holdings(ticker)
            if is_holding:
                logger.info(f"{ticker}({company_name}) 이미 보유 중인 종목입니다.")
                return {
                    "success": True,
                    "decision": "보유 중",
                    "ticker": ticker,
                    "company_name": company_name,
                }

            # 현재 주가 조회
            current_price = await self._get_current_stock_price(ticker)
            if current_price <= 0:
                logger.error(f"{ticker} 현재 주가 조회 실패")
                return {"success": False, "error": "현재 주가 조회 실패"}

            # 거래대금 랭킹 변화 분석 추가
            (
                rank_change_percentage,
                rank_change_msg,
            ) = await self._get_trading_value_rank_change(ticker)

            # 보고서 내용 읽기
            from pdf_converter import pdf_to_markdown_text

            report_content = pdf_to_markdown_text(pdf_report_path)

            # 매매 시나리오 추출 (거래대금 랭킹 정보 전달)
            scenario = await self._extract_trading_scenario(
                report_content, rank_change_msg
            )

            # 산업군 다양성 확인
            sector = scenario.get("sector", "알 수 없음")
            is_sector_diverse = await self._check_sector_diversity(sector)

            # 결과 반환
            return {
                "success": True,
                "ticker": ticker,
                "company_name": company_name,
                "current_price": current_price,
                "scenario": scenario,
                "decision": scenario.get("decision", "관망"),
                "sector": sector,
                "sector_diverse": is_sector_diverse,
                "rank_change_percentage": rank_change_percentage,  # 추가된 부분
                "rank_change_msg": rank_change_msg,  # 추가된 부분
            }

        except Exception as e:
            logger.error(f"보고서 분석 중 오류: {str(e)}")
            logger.error(traceback.format_exc())
            return {"success": False, "error": str(e)}

    async def buy_stock(
        self,
        ticker: str,
        company_name: str,
        current_price: float,
        scenario: Dict[str, Any],
        rank_change_msg: str = "",
    ) -> bool:
        """
        주식 매수 처리

        Args:
            ticker: 종목 코드
            company_name: 종목 이름
            current_price: 현재 주가
            scenario: 매매 시나리오 정보
            rank_change_msg: 거래대금 랭킹 변화 정보

        Returns:
            bool: 매수 성공 여부
        """
        try:
            # 이미 보유 중인지 확인
            if await self._is_ticker_in_holdings(ticker):
                logger.warning(f"{ticker}({company_name}) 이미 보유 중인 종목입니다.")
                return False

            # 슬랏 여유 공간 확인
            current_slots = await self._get_current_slots_count()
            if current_slots >= self.max_slots:
                logger.warning(f"보유 종목이 이미 최대치({self.max_slots}개)입니다.")
                return False

            # 잔고 확인
            current_balance = await self._get_current_balance()
            if current_balance < current_price:
                logger.warning(
                    f"잔고 부족: 현재 잔고 {current_balance:,.0f}원, 매수 필요금액 {current_price:,.0f}원"
                )
                return False
            # 1/N 투자 전략, 투자 금액 계산
            invest_amount = current_balance / (self.max_slots - current_slots)
            if invest_amount < current_price and current_balance * 0.5 < current_price:
                logger.warning(
                    f"1/N 투자 전략에 따른 투자 금액이 현재 주가보다 적습니다: 투자 가능 금액 {invest_amount:,.0f}원, 현재 주가 {current_price:,.0f}원"
                )
                return False
            quantity = int(invest_amount // current_price)
            if quantity <= 0:
                quantity = 1  # 최소 1주 매수
            total_cost = quantity * current_price
            logger.info(
                f"{ticker}({company_name}) 매수 시도: 수량 {quantity}주, 총 매수금액 {total_cost:,.0f}원"
            )

            # 현재 시간
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # 보유종목 테이블에 추가
            self.cursor.execute(
                """
                INSERT INTO stock_holdings 
                (ticker, company_name, buy_price, buy_date, current_price, last_updated, scenario, target_price, stop_loss, quantity) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ticker,
                    company_name,
                    current_price,
                    now,
                    current_price,
                    now,
                    json.dumps(scenario, ensure_ascii=False),
                    scenario.get("target_price", 0),
                    scenario.get("stop_loss", 0),
                    quantity,
                ),
            )
            # 잔고 업데이트
            await self._update_balance(-total_cost)
            self.conn.commit()

            buy_score = scenario.get("buy_score", 0)
            rank_bonus = scenario.get("rank_bonus", 0)
            effective_buy_score = buy_score + rank_bonus

            # 매수 내역 메시지 추가
            message = (
                f"📈 신규 매수: {company_name}({ticker})-{quantity}주\n"
                f"매수가: {current_price:,.0f}원\n"
                f"목표가: {scenario.get('target_price', 0):,.0f}원\n"
                f"손절가: {scenario.get('stop_loss', 0):,.0f}원\n"
                f"투자기간: {scenario.get('investment_period', '단기')}\n"
                f"산업군: {scenario.get('sector', '알 수 없음')}\n"
                f"매수 점수: {effective_buy_score} (기본: {buy_score}, 랭킹 보너스: {rank_bonus})\n"
            )

            # 거래대금 랭킹 정보가 있으면 추가
            if rank_change_msg:
                message += f"거래대금 분석: {rank_change_msg}\n"

            message += f"투자근거: {scenario.get('rationale', '정보 없음')}"

            self.message_queue.append(message)
            logger.info(f"{ticker}({company_name}) 매수 완료")

            return True

        except Exception as e:
            logger.error(f"{ticker} 매수 처리 중 오류: {str(e)}")
            logger.error(traceback.format_exc())
            return False

    async def _analyze_sell_decision(
        self, stock_data: Dict[str, Any]
    ) -> Tuple[bool, str]:
        """
        매도 의사결정 분석

        Args:
            stock_data: 종목 정보

        Returns:
            Tuple[bool, str]: 매도 여부, 매도 이유
        """
        try:
            ticker = stock_data.get("ticker", "")
            buy_price = stock_data.get("buy_price", 0)
            buy_date = stock_data.get("buy_date", "")
            current_price = stock_data.get("current_price", 0)
            target_price = stock_data.get("target_price", 0)
            stop_loss = stock_data.get("stop_loss", 0)

            # 수익률 계산
            profit_rate = ((current_price - buy_price) / buy_price) * 100

            # 매수일로부터 경과 일수
            buy_datetime = datetime.strptime(buy_date, "%Y-%m-%d %H:%M:%S")
            days_passed = (datetime.now() - buy_datetime).days

            # 시나리오 정보 추출
            scenario_str = stock_data.get("scenario", "{}")
            investment_period = "중기"  # 기본값

            try:
                if isinstance(scenario_str, str):
                    scenario_data = json.loads(scenario_str)
                    investment_period = scenario_data.get("investment_period", "중기")
            except:
                pass

            # 손절매 조건 확인
            if stop_loss > 0 and current_price <= stop_loss:
                return True, f"손절매 조건 도달 (손절가: {stop_loss:,.0f}원)"

            # 목표가 도달 확인
            if target_price > 0 and current_price >= target_price:
                return True, f"목표가 달성 (목표가: {target_price:,.0f}원)"

            # 투자 기간별 매도 조건
            if investment_period == "단기":
                # 단기 투자의 경우 더 빠른 매도 (15일 이상 보유 + 5% 이상 수익)
                if days_passed >= 15 and profit_rate >= 5:
                    return (
                        True,
                        f"단기 투자 목표 달성 (보유일: {days_passed}일, 수익률: {profit_rate:.2f}%)",
                    )

                # 단기 투자 손실 방어 (10일 이상 + 3% 이상 손실)
                if days_passed >= 10 and profit_rate <= -3:
                    return (
                        True,
                        f"단기 투자 손실 방어 (보유일: {days_passed}일, 수익률: {profit_rate:.2f}%)",
                    )

            # 기존 매도 조건
            # 10% 이상 수익 시 매도
            if profit_rate >= 10:
                return True, f"수익률 10% 이상 달성 (현재 수익률: {profit_rate:.2f}%)"

            # 5% 이상 손실 시 매도
            if profit_rate <= -5:
                return True, f"손실 -5% 이상 발생 (현재 수익률: {profit_rate:.2f}%)"

            # 30일 이상 보유 시 손실이면 매도
            if days_passed >= 30 and profit_rate < 0:
                return (
                    True,
                    f"30일 이상 보유 중이며 손실 상태 (보유일: {days_passed}일, 수익률: {profit_rate:.2f}%)",
                )

            # 60일 이상 보유 시 3% 이상 수익이면 매도
            if days_passed >= 60 and profit_rate >= 3:
                return (
                    True,
                    f"60일 이상 보유 중이며 3% 이상 수익 (보유일: {days_passed}일, 수익률: {profit_rate:.2f}%)",
                )

            # 장기 투자 케이스 추가 (90일 이상 보유 + 손실 상태)
            if investment_period == "장기" and days_passed >= 90 and profit_rate < 0:
                return (
                    True,
                    f"장기 투자 손실 정리 (보유일: {days_passed}일, 수익률: {profit_rate:.2f}%)",
                )

            # 기본적으로 계속 보유
            return False, "계속 보유"

        except Exception as e:
            logger.error(
                f"{ticker if 'ticker' in locals() else '알 수 없는 종목'} 매도 분석 중 오류: {str(e)}"
            )
            return False, "분석 오류"

    async def sell_stock(self, stock_data: Dict[str, Any], sell_reason: str) -> bool:
        """
        주식 매도 처리

        Args:
            stock_data: 매도할 종목 정보
            sell_reason: 매도 이유

        Returns:
            bool: 매도 성공 여부
        """
        try:
            ticker = stock_data.get("ticker", "")
            company_name = stock_data.get("company_name", "")
            buy_price = stock_data.get("buy_price", 0)
            buy_date = stock_data.get("buy_date", "")
            current_price = stock_data.get("current_price", 0)
            scenario_json = stock_data.get("scenario", "{}")
            quantity = await self._get_stock_quantity(ticker)

            # 수익률 계산
            profit_rate = ((current_price - buy_price) / buy_price) * 100

            # 보유 기간 계산 (일수)
            buy_datetime = datetime.strptime(buy_date, "%Y-%m-%d %H:%M:%S")
            now_datetime = datetime.now()
            holding_days = (now_datetime - buy_datetime).days

            # 현재 시간
            now = now_datetime.strftime("%Y-%m-%d %H:%M:%S")

            # 매매 내역 테이블에 추가
            self.cursor.execute(
                """
                INSERT INTO trading_history 
                (ticker, company_name, buy_price, buy_date, sell_price, sell_date, profit_rate, holding_days, scenario, quantity) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    quantity,
                ),
            )

            # 보유종목에서 제거
            self.cursor.execute(
                "DELETE FROM stock_holdings WHERE ticker = ?", (ticker,)
            )
            # 잔고 업데이트
            await self._update_balance(current_price * quantity)

            # 변경사항 저장
            self.conn.commit()

            # 매도 메시지 추가
            arrow = "🔺" if profit_rate > 0 else "🔻-" if profit_rate < 0 else "➖"
            message = (
                f"📉 매도: {company_name}({ticker})-{quantity}주\n"
                f"매수가: {buy_price:,.0f}원\n"
                f"매도가: {current_price:,.0f}원\n"
                f"수익률: {arrow} {abs(profit_rate):.2f}%\n"
                f"보유기간: {holding_days}일\n"
                f"매도이유: {sell_reason}"
            )

            self.message_queue.append(message)
            logger.info(
                f"{ticker}({company_name}) 매도 완료 (수익률: {profit_rate:.2f}%)"
            )

            return True

        except Exception as e:
            logger.error(f"매도 처리 중 오류: {str(e)}")
            logger.error(traceback.format_exc())
            return False

    async def update_holdings(self) -> List[Dict[str, Any]]:
        """
        보유 종목 정보 업데이트 및 매도 의사결정

        Returns:
            List[Dict]: 매도된 종목 정보 리스트
        """
        try:
            logger.info("보유 종목 정보 업데이트 시작")

            # 보유 종목 목록 조회
            self.cursor.execute(
                """SELECT ticker, company_name, buy_price, buy_date, current_price, 
                   scenario, target_price, stop_loss, last_updated 
                   FROM stock_holdings"""
            )
            holdings = [dict(row) for row in self.cursor.fetchall()]

            if not holdings or len(holdings) == 0:
                logger.info("보유 중인 종목이 없습니다.")
                return []

            sold_stocks = []

            for stock in holdings:
                ticker = stock.get("ticker")
                company_name = stock.get("company_name")

                # 현재 주가 조회
                current_price = await self._get_current_stock_price(ticker)

                if current_price <= 0:
                    old_price = stock.get("current_price", 0)
                    logger.warning(
                        f"{ticker} 현재 주가 조회 실패, 이전 가격 유지: {old_price}"
                    )
                    current_price = old_price

                # 주가 정보 업데이트
                stock["current_price"] = current_price

                # 시나리오 JSON 문자열 확인
                scenario_str = stock.get("scenario", "{}")
                try:
                    if isinstance(scenario_str, str):
                        scenario_json = json.loads(scenario_str)

                        # 목표가/손절가 확인 및 업데이트
                        if (
                            "target_price" in scenario_json
                            and stock.get("target_price", 0) == 0
                        ):
                            stock["target_price"] = scenario_json["target_price"]

                        if (
                            "stop_loss" in scenario_json
                            and stock.get("stop_loss", 0) == 0
                        ):
                            stock["stop_loss"] = scenario_json["stop_loss"]
                except:
                    logger.warning(f"{ticker} 시나리오 JSON 파싱 실패")

                # 현재 시간
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                # 매도 여부 분석
                should_sell, sell_reason = await self._analyze_sell_decision(stock)

                if should_sell:
                    # 매도 처리
                    sell_success = await self.sell_stock(stock, sell_reason)

                    if sell_success:
                        # 실제 계좌 매매 함수 호출(비동기)
                        from trading.domestic_stock_trading import AsyncTradingContext

                        async with AsyncTradingContext() as trading:
                            # 비동기 매도 실행
                            trade_result = await trading.async_sell_stock(
                                stock_code=ticker
                            )

                        if trade_result["success"]:
                            logger.info(f"실제 매도 성공: {trade_result['message']}")
                        else:
                            logger.error(f"실제 매도 실패: {trade_result['message']}")

                    if sell_success:
                        sold_stocks.append(
                            {
                                "ticker": ticker,
                                "company_name": company_name,
                                "buy_price": stock.get("buy_price", 0),
                                "sell_price": current_price,
                                "profit_rate": (
                                    (current_price - stock.get("buy_price", 0))
                                    / stock.get("buy_price", 0)
                                    * 100
                                ),
                                "reason": sell_reason,
                            }
                        )
                else:
                    # 현재가 업데이트
                    self.cursor.execute(
                        """UPDATE stock_holdings 
                           SET current_price = ?, last_updated = ? 
                           WHERE ticker = ?""",
                        (current_price, now, ticker),
                    )
                    self.conn.commit()
                    logger.info(
                        f"{ticker}({company_name}) 현재가 업데이트: {current_price:,.0f}원 ({sell_reason})"
                    )

            return sold_stocks

        except Exception as e:
            logger.error(f"보유 종목 업데이트 중 오류: {str(e)}")
            logger.error(traceback.format_exc())
            return []

    async def generate_report_summary(self) -> str:
        """
        보유 종목 및 수익률 통계 요약 생성

        Returns:
            str: 요약 메시지
        """
        try:
            # 보유 종목 조회
            self.cursor.execute(
                "SELECT ticker, company_name, buy_price, current_price, buy_date, scenario FROM stock_holdings"
            )
            holdings = [dict(row) for row in self.cursor.fetchall()]

            # 거래 내역에서 총 수익률 계산
            self.cursor.execute("SELECT SUM(profit_rate) FROM trading_history")
            total_profit = self.cursor.fetchone()[0] or 0

            # 거래 내역 건수
            self.cursor.execute("SELECT COUNT(*) FROM trading_history")
            total_trades = self.cursor.fetchone()[0] or 0

            # 성공/실패 거래 건수
            self.cursor.execute(
                "SELECT COUNT(*) FROM trading_history WHERE profit_rate > 0"
            )
            successful_trades = self.cursor.fetchone()[0] or 0

            # 시장 수익률 계산 (KOSPI, KOSDAQ)
            first_kospi_index = None
            first_kosdaq_index = None
            last_kospi_index = None
            last_kosdaq_index = None
            kosdaq_return = 0
            kospi_return = 0
            self.cursor.execute(
                "select * from market_condition order by date desc limit 1"
            )
            last_day = self.cursor.fetchone()
            if last_day:
                last_kospi_index = last_day[1]
                last_kosdaq_index = last_day[2]
            self.cursor.execute(
                "select * from market_condition order by date asc limit 1"
            )
            first_day = self.cursor.fetchone()
            if first_day:
                first_kospi_index = first_day[1]
                first_kosdaq_index = first_day[2]
            if (
                first_kospi_index
                and first_kosdaq_index
                and last_kospi_index
                and last_kosdaq_index
            ):
                kospi_return = (
                    (last_kospi_index - first_kospi_index) / first_kospi_index
                ) * 100
                kosdaq_return = (
                    (last_kosdaq_index - first_kosdaq_index) / first_kosdaq_index
                ) * 100

            # 메시지 생성
            message = f"📊 프리즘 시뮬레이터 | 실시간 포트폴리오 ({datetime.now().strftime('%Y-%m-%d %H:%M')})\n\n"

            # 1. 포트폴리오 요약
            message += f"🔸 현재 보유 종목: {len(holdings) if holdings else 0}/{self.max_slots}개\n"

            # 최고 수익/손실 종목 정보 (있는 경우)
            if holdings and len(holdings) > 0:
                profit_rates = []
                for h in holdings:
                    buy_price = h.get("buy_price", 0)
                    current_price = h.get("current_price", 0)
                    if buy_price > 0:
                        profit_rate = ((current_price - buy_price) / buy_price) * 100
                        profit_rates.append(
                            (h.get("ticker"), h.get("company_name"), profit_rate)
                        )

                if profit_rates:
                    best = max(profit_rates, key=lambda x: x[2])
                    worst = min(profit_rates, key=lambda x: x[2])

                    message += f"✅ 최고 수익: {best[1]}({best[0]}) {'+' if best[2] > 0 else ''}{best[2]:.2f}%\n"
                    message += f"⚠️ 최저 수익: {worst[1]}({worst[0]}) {'+' if worst[2] > 0 else ''}{worst[2]:.2f}%\n"

            message += "\n"

            # 2. 산업군 분포 분석
            sector_counts = {}

            if holdings and len(holdings) > 0:
                message += f"🔸 보유 종목 목록:\n"
                for stock in holdings:
                    ticker = stock.get("ticker", "")
                    company_name = stock.get("company_name", "")
                    buy_price = stock.get("buy_price", 0)
                    current_price = stock.get("current_price", 0)
                    buy_date = stock.get("buy_date", "")
                    scenario_str = stock.get("scenario", "{}")

                    # 시나리오에서 섹터 정보 추출
                    sector = "알 수 없음"
                    try:
                        if isinstance(scenario_str, str):
                            scenario_data = json.loads(scenario_str)
                            sector = scenario_data.get("sector", "알 수 없음")
                    except:
                        pass

                    # 산업군 카운트 업데이트
                    sector_counts[sector] = sector_counts.get(sector, 0) + 1

                    profit_rate = (
                        ((current_price - buy_price) / buy_price) * 100
                        if buy_price
                        else 0
                    )
                    arrow = (
                        "🔺" if profit_rate > 0 else "🔻-" if profit_rate < 0 else "➖"
                    )

                    buy_datetime = (
                        datetime.strptime(buy_date, "%Y-%m-%d %H:%M:%S")
                        if buy_date
                        else datetime.now()
                    )
                    days_passed = (datetime.now() - buy_datetime).days

                    message += f"- {company_name}({ticker}) [{sector}]\n"
                    message += f"  매수가: {buy_price:,.0f}원 / 현재가: {current_price:,.0f}원\n"
                    message += f"  수익률: {arrow} {abs(profit_rate):.2f}% / 보유기간: {days_passed}일\n\n"

                # 산업군 분포 추가
                message += f"🔸 산업군 분포:\n"
                for sector, count in sector_counts.items():
                    percentage = (count / len(holdings)) * 100
                    message += f"- {sector}: {count}개 ({percentage:.1f}%)\n"
                message += "\n"
            else:
                message += "보유 중인 종목이 없습니다.\n\n"

            # 3. 매매 이력 통계
            message += f"🔸 매매 이력 통계\n"
            message += f"- 총 거래 건수: {total_trades}건\n"
            message += f"- 수익 거래: {successful_trades}건\n"
            message += f"- 손실 거래: {total_trades - successful_trades}건\n"

            if total_trades > 0:
                message += f"- 승률: {(successful_trades / total_trades * 100):.2f}%\n"
            else:
                message += f"- 승률: 0.00%\n"

            message += f"- 누적 수익률: {total_profit:.2f}%\n\n"

            if (
                first_kospi_index
                and first_kosdaq_index
                and last_kospi_index
                and last_kosdaq_index
            ):
                message += f"🔸 시장 수익률\n"
                message += f"- KOSPI: {first_kospi_index:,.2f} → {last_kospi_index:,.2f} ({'+' if kospi_return > 0 else ''}{kospi_return:.2f}%)\n"
                message += f"- KOSDAQ: {first_kosdaq_index:,.2f} → {last_kosdaq_index:,.2f} ({'+' if kosdaq_return > 0 else ''}{kosdaq_return:.2f}%)\n\n"

            return message

        except Exception as e:
            logger.error(f"보고서 요약 생성 중 오류: {str(e)}")
            error_msg = f"보고서 생성 중 오류가 발생했습니다: {str(e)}"
            return error_msg

    async def process_reports(self, pdf_report_paths: List[str]) -> Tuple[int, int]:
        """
        분석 보고서를 처리하여 매매 의사결정 수행

        Args:
            pdf_report_paths: pdf 분석 보고서 파일 경로 리스트

        Returns:
            Tuple[int, int]: 매수 건수, 매도 건수
        """
        try:
            logger.info(f"총 {len(pdf_report_paths)}개 보고서 처리 시작")

            # 매수, 매도 카운터
            buy_count = 0
            sell_count = 0

            # 1. 기존 보유 종목 업데이트 및 매도 의사결정
            sold_stocks = await self.update_holdings()
            sell_count = len(sold_stocks)

            if sold_stocks:
                logger.info(f"{len(sold_stocks)}개 종목 매도 완료")
                for stock in sold_stocks:
                    logger.info(
                        f"매도: {stock['company_name']}({stock['ticker']}) - 수익률: {stock['profit_rate']:.2f}% / 이유: {stock['reason']}"
                    )
            else:
                logger.info("매도된 종목이 없습니다.")

            # 2. 새로운 보고서 분석 및 매수 의사결정
            for pdf_report_path in pdf_report_paths:
                # 보고서 분석
                analysis_result = await self.analyze_report(pdf_report_path)

                if not analysis_result.get("success", False):
                    logger.error(
                        f"보고서 분석 실패: {pdf_report_path} - {analysis_result.get('error', '알 수 없는 오류')}"
                    )
                    continue

                # 이미 보유 중인 종목이면 스킵
                if analysis_result.get("decision") == "보유 중":
                    logger.info(
                        f"보유 중 종목 스킵: {analysis_result.get('ticker')} - {analysis_result.get('company_name')}"
                    )
                    continue

                # 종목 정보 및 시나리오
                ticker = analysis_result.get("ticker")
                company_name = analysis_result.get("company_name")
                current_price = analysis_result.get("current_price", 0)
                scenario = analysis_result.get("scenario", {})
                sector = analysis_result.get("sector", "알 수 없음")
                sector_diverse = analysis_result.get("sector_diverse", True)
                rank_change_msg = analysis_result.get("rank_change_msg", "")
                rank_change_percentage = analysis_result.get(
                    "rank_change_percentage", 0
                )

                # 산업군 다양성 체크 실패 시 스킵
                if not sector_diverse:
                    logger.info(
                        f"매수 보류: {company_name}({ticker}) - 산업군 '{sector}' 과다 투자 방지"
                    )
                    continue

                # 현재 보유 슬랏 수에 따라 매수 점수 기준 동적 조정
                current_slots = await self._get_current_slots_count()
                min_score = 8  # 기본 기준

                # 슬랏이 많이 차있을수록 더 높은 기준 적용
                if current_slots >= 7:  # 70% 이상 찼을 경우
                    min_score = 9

                # 진입 결정이면 매수 처리
                buy_score = scenario.get("buy_score", 0)
                logger.info(
                    f"매수 점수 체크: {company_name}({ticker}) - 점수: {buy_score}, 최소 요구 점수: {min_score}"
                )

                # 거래대금 랭킹 상승 시 가중치 부여
                rank_bonus = 0
                if rank_change_percentage >= 30:
                    rank_bonus = 2  # 1에서 2로 증가
                    logger.info(
                        f"거래대금 랭킹 큰 폭 상승으로 매수 점수 +2 보너스: {company_name}({ticker})"
                    )
                elif rank_change_percentage >= 15:
                    rank_bonus = 1  # 중간 수준 상승에도 보너스 추가
                    logger.info(
                        f"거래대금 랭킹 상승으로 매수 점수 +1 보너스: {company_name}({ticker})"
                    )

                effective_buy_score = buy_score + rank_bonus
                scenario["rank_bonus"] = rank_bonus  # 시나리오에 보너스 정보 추가
                logger.info(
                    f"최종 매수 점수: {effective_buy_score} (기본: {buy_score}, 랭킹 보너스: {rank_bonus})"
                )

                if (
                    analysis_result.get("decision") == "진입"
                    and effective_buy_score >= min_score
                ):
                    # 매수 처리
                    buy_success = await self.buy_stock(
                        ticker, company_name, current_price, scenario, rank_change_msg
                    )

                    if buy_success:
                        # 실제 계좌 매매 함수 호출(비동기)
                        from trading.domestic_stock_trading import AsyncTradingContext

                        async with AsyncTradingContext() as trading:
                            # 비동기 매수 실행
                            trade_result = await trading.async_buy_stock(
                                stock_code=ticker
                            )

                        if trade_result["success"]:
                            logger.info(f"실제 매수 성공: {trade_result['message']}")
                        else:
                            logger.error(f"실제 매수 실패: {trade_result['message']}")

                    if buy_success:
                        buy_count += 1
                        logger.info(
                            f"매수 완료: {company_name}({ticker}) @ {current_price:,.0f}원"
                        )
                    else:
                        logger.warning(f"매수 실패: {company_name}({ticker})")
                else:
                    reason = ""
                    if effective_buy_score < min_score:
                        reason = f"매수 점수 부족 ({effective_buy_score} < {min_score})"
                    elif analysis_result.get("decision") != "진입":
                        reason = (
                            f"진입 결정 아님 (결정: {analysis_result.get('decision')})"
                        )

                    logger.info(f"매수 보류: {company_name}({ticker}) - {reason}")

            logger.info(f"보고서 처리 완료 - 매수: {buy_count}건, 매도: {sell_count}건")
            return buy_count, sell_count

        except Exception as e:
            logger.error(f"보고서 처리 중 오류: {str(e)}")
            logger.error(traceback.format_exc())
            return 0, 0

    async def send_telegram_message(self, chat_id: str) -> bool:
        """
        텔레그램으로 메시지 전송

        Args:
            chat_id: 텔레그램 채널 ID

        Returns:
            bool: 전송 성공 여부
        """
        try:
            # 텔레그램 봇이 초기화되지 않았다면 로그만 출력
            if not self.telegram_bot:
                logger.warning(
                    "텔레그램 봇이 초기화되지 않았습니다. 토큰을 확인해주세요."
                )

                # 메시지 출력만 하고 실제 전송은 하지 않음
                for message in self.message_queue:
                    logger.info(f"[텔레그램 메시지] {message[:100]}...")

                return False

            # 요약 보고서 생성
            summary = await self.generate_report_summary()
            self.message_queue.append(summary)

            # 각 메시지 전송
            success = True
            for message in self.message_queue:
                logger.info(f"텔레그램 메시지 전송 중: {chat_id}")
                try:
                    await self.telegram_bot.send_message(
                        chat_id=chat_id,
                        text=message,
                        parse_mode="Markdown",  # Markdown 형식 지원
                    )
                    logger.info(f"텔레그램 메시지 전송 완료: {chat_id}")
                except TelegramError as e:
                    logger.error(f"텔레그램 메시지 전송 실패: {e}")
                    success = False

                # API 제한 방지를 위한 지연
                await asyncio.sleep(1)

            # 메시지 큐 초기화
            self.message_queue = []

            return success

        except Exception as e:
            logger.error(f"텔레그램 메시지 전송 중 오류: {str(e)}")
            logger.error(traceback.format_exc())
            return False

    async def run(
        self, pdf_report_paths: List[str], chat_id: str = None
    ) -> bool | None:
        """
        주식 트래킹 시스템 메인 실행 함수

        Args:
            pdf_report_paths: 분석 보고서 파일 경로 리스트
            chat_id: 텔레그램 채널 ID (설정되지 않으면 메시지를 전송하지 않음)

        Returns:
            bool: 실행 성공 여부
        """
        try:
            logger.info("트래킹 시스템 배치 실행 시작")

            # 초기화
            await self.initialize()

            try:
                # 보고서 처리
                buy_count, sell_count = await self.process_reports(pdf_report_paths)

                # 텔레그램 메시지 전송
                if chat_id:
                    message_sent = await self.send_telegram_message(chat_id)
                    if message_sent:
                        logger.info("텔레그램 메시지 전송 완료")
                    else:
                        logger.warning("텔레그램 메시지 전송 실패")

                logger.info("트래킹 시스템 배치 실행 완료")
                return True
            finally:
                # finally 블록으로 이동하여 항상 연결 종료 보장
                if self.conn:
                    self.conn.close()
                    logger.info("데이터베이스 연결 종료")

        except Exception as e:
            logger.error(f"트래킹 시스템 실행 중 오류: {str(e)}")
            logger.error(traceback.format_exc())

            # 데이터베이스 연결 확인 및 종료
            if hasattr(self, "conn") and self.conn:
                try:
                    self.conn.close()
                    logger.info("오류 발생 후 데이터베이스 연결 종료")
                except:
                    pass

            return False


async def main():
    """메인 함수"""
    import argparse
    import logging

    # 로거 가져오기
    local_logger = logging.getLogger(__name__)

    parser = argparse.ArgumentParser(description="주식 트래킹 및 매매 에이전트")
    parser.add_argument("--reports", nargs="+", help="분석 보고서 파일 경로 리스트")
    parser.add_argument("--chat-id", help="텔레그램 채널 ID")
    parser.add_argument("--telegram-token", help="텔레그램 봇 토큰")

    args = parser.parse_args()

    if not args.reports:
        local_logger.error("보고서 경로가 지정되지 않았습니다.")
        return False

    async with app.run():
        agent = StockTrackingAgent(telegram_token=args.telegram_token)
        success = await agent.run(args.reports, args.chat_id)

        return success


if __name__ == "__main__":
    try:
        # asyncio 실행
        asyncio.run(main())
    except Exception as e:
        logger.error(f"프로그램 실행 중 오류: {str(e)}")
        logger.error(traceback.format_exc())
        sys.exit(1)
