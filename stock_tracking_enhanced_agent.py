from dotenv import load_dotenv
load_dotenv()  # .env 파일에서 환경변수 로드

import numpy as np
from scipy import stats
from typing import List, Tuple, Dict, Any
from datetime import datetime, timedelta
from stock_tracking_agent import StockTrackingAgent
import logging
import json
import traceback
import re

from mcp_agent.agents.agent import Agent
from mcp_agent.workflows.llm.augmented_llm import RequestParams
from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"enhanced_stock_tracking_{datetime.now().strftime('%Y%m%d')}.log")
    ]
)
logger = logging.getLogger(__name__)


class EnhancedStockTrackingAgent(StockTrackingAgent):
    """개선된 주식 트래킹 및 매매 에이전트"""

    def __init__(self, db_path: str = "stock_tracking_db.sqlite", telegram_token: str = None):
        """에이전트 초기화"""
        super().__init__(db_path, telegram_token)
        # 시장 상태 저장 변수 (1: 강세장, 0: 중립, -1: 약세장)
        self.simple_market_condition = 0
        # 변동성 테이블 (종목별 변동성 저장)
        self.volatility_table = {}

    async def initialize(self):
        """필요한 테이블 생성 및 초기화"""
        await super().initialize()

        # 매도 결정 에이전트 초기화
        self.sell_decision_agent = Agent(
            name="sell_decision_agent",
            instruction="""당신은 윌리엄 오닐의 CAN SLIM과
“손실은 7–8%에서 제한하고 상승 흐름은 길게 가져간다”는 리스크 관리 원칙을 참고하는 매매 판단 전문가입니다.
분할매매는 불가능하며, 항상 “보유” vs “전량 매도” 두 시나리오만 비교합니다.

## 역할
- 보유 중인 개별 종목에 대해 데이터 기반으로
  보유/전량 매도 시나리오를 비교·설명합니다.
- 판단의 근거가 되는 위험 신호, 추세 변화, 시장 환경을 명확히 제시합니다.


## 핵심 판단 원칙

- 손실 -7% 전후 구간은 구조적 위험 신호로 간주합니다.
- 다만 가격·거래량·수급이 동시에 강하게 개선되는 경우,
  단기 유예 시나리오(최대 1거래일)를 조건부로 설명할 수 있습니다.
- 급락(-5% 이상) 발생 시:
  시장 전체 충격인지, 개별 종목 이슈인지 구분합니다.
- 강세장에서는 추세 추종 시나리오 비중을 높이고,
  약세장·횡보장에서는 방어적 또는 수익 실현 시나리오 비중을 높입니다.
- 잦은 포트폴리오 조정은 지양하며,
  펀더멘털·시장 구조·기술적 추세 변화가 명확할 때만 조정을 제안합니다.


## 분석 시 고려 요소

- 수익률: 현재 수익률 vs 목표 수익률, 허용 손실 범위, 보유 기간 대비 성과
- 기술적 요소:
  추세 방향과 강도, 거래량 패턴, 주요 지지·저항,
  박스권 위치, 모멘텀 변화
- 시장 환경:
  코스피/코스닥 지수 흐름, 변동성 수준
- 포트폴리오 영향:
  비중, 리스크 집중도, 전체 전략과의 정합성

## ⭐ Trailing Stop 관리 원칙 (매 실행 시 적용)

Trailing Stop은 **이익 구간에서 수익을 보호하기 위한 장치**이며,
손실을 허용하는 수단이 아닙니다.

### 1. Trailing Stop 활성화 조건
- 종목의 최고가가 **진입가 대비 +10% 이상 상승한 이후부터** 적용합니다.
- 활성화 이전에는 기존 손절 규칙(-5% ~ -7%)을 우선 적용합니다.

### 2. Trailing Stop 계산 기준
1. 진입 이후 형성된 최고가를 기준으로 계산합니다.
2. 최고가가 갱신될 때마다 손절가를 상향 조정할 수 있습니다.
3. 손절가는 **상향만 가능하며, 절대 하향 조정하지 않습니다.**

### 3. 시장 환경별 Trailing Stop 가이드
- 강세장:
  - 최고가 대비 약 -7% ~ -8% 범위에서 손절가 설정 가능
- 약세장 또는 변동성 확대 구간:
  - 최고가 대비 약 -4% ~ -5% 수준으로 보수적으로 적용

※ 이는 판단 가이드이며, 시장·종목 특성에 따라 해석 비중을 조절합니다.

### 4. 기존 손절 규칙과의 관계 (중요)
- Trailing Stop으로 계산된 손절가와
  기존 손절가 중 **더 보수적인 가격(더 높은 손절가)**을
  실제 손절 기준으로 사용합니다.

### 5. Trailing Stop 이탈 시
- 종가 기준 또는 명확한 추세 이탈 확인 시
  → `should_sell: true` 시나리오를 우선 검토합니다.

#### 예시
- 진입가: 10,000원 / 초기 손절: 9,300원
- 최고가 12,000원 → new_stop_loss: 11,040원 (12,000 × 0.92)
- 최고가 15,000원 → new_stop_loss: 13,800원 (15,000 × 0.92)
- 이후 13,500원 하락 이탈 → should_sell: true

## 도구 사용 지침(필요 시에만 사용):
- time-get_current_time: 현재 시간 확인
- kospi_kosdaq:
  - get_stock_ohlcv: 가격·거래량 기반 추세 확인
  - get_stock_trading_volume: 기관/외국인 수급 확인
  - get_index_ohlcv: 시장 지수 확인
- sqlite:
  - 현재 포트폴리오 전체 현황, 해당 종목 매매 정보 확인
  - 필요한 경우에만 목표가/손절가 UPDATE 실행

항상 도구로 최신 데이터를 확인한 뒤, 보유/전량 매도 두 시나리오를 비교해 판단하세요.

## 응답 형식 (반드시 이 JSON 스키마만 사용):
{
  "should_sell": true 또는 false,
  "sell_reason": "매도 또는 보유 판단의 핵심 이유",
  "confidence": 1~10 사이 숫자,
  "analysis_summary": {
    "technical_trend": "상승/하락/중립 + 강도 한 줄",
    "volume_analysis": "거래량 패턴 한 줄",
    "market_condition_impact": "시장 환경 영향 한 줄",
    "time_factor": "보유 기간 관련 고려사항 한 줄"
  },
  "portfolio_adjustment": {
    "needed": true 또는 false,
    "reason": "조정이 필요한 구체적 이유 (또는 '현재 전략 유지')",
    "new_target_price": 숫자 또는 null,  // 쉼표·단위 없이 예: 85000
    "new_stop_loss": 숫자 또는 null,    // 쉼표·단위 없이 예: 70000
    "urgency": "high" 또는 "medium" 또는 "low"
  }
}

### 가이드:
- portfolio_adjustment.needed = true 는
  시장 급변, 펀더멘털 변화, 기술적 구조 붕괴 등
  **명확한 근거가 있을 때만 사용합니다.
- 전략 유지 시 price 값은 null로 유지합니다.
- 모든 가격은 85000처럼 숫자만 사용합니다.
            """,
            server_names=["kospi_kosdaq", "sqlite", "time"]
        )

        # 시장 상태 분석 테이블 생성
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS market_condition (
                date TEXT PRIMARY KEY,
                kospi_index REAL,
                kosdaq_index REAL,
                condition INTEGER,  -- 1: 강세장, 0: 중립, -1: 약세장
                volatility REAL
            )
        """)

        # 매수 보류(관망) 종목 추적 테이블 생성
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS watchlist_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                company_name TEXT NOT NULL,
                current_price REAL NOT NULL,
                analyzed_date TEXT NOT NULL,
                buy_score INTEGER NOT NULL,
                min_score INTEGER NOT NULL,
                decision TEXT NOT NULL,
                skip_reason TEXT NOT NULL,
                target_price REAL,
                stop_loss REAL,
                investment_period TEXT,
                sector TEXT,
                scenario TEXT,
                portfolio_analysis TEXT,
                valuation_analysis TEXT,
                sector_outlook TEXT,
                market_condition TEXT,
                rationale TEXT
            )
        """)

        # 보유 종목 매도 판단 테이블 생성 (AI의 홀딩/매도 결정 저장)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS holding_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                decision_date TEXT NOT NULL,
                decision_time TEXT NOT NULL,

                current_price REAL NOT NULL,
                should_sell BOOLEAN NOT NULL,
                sell_reason TEXT,
                confidence INTEGER,

                technical_trend TEXT,
                volume_analysis TEXT,
                market_condition_impact TEXT,
                time_factor TEXT,

                portfolio_adjustment_needed BOOLEAN,
                adjustment_reason TEXT,
                new_target_price REAL,
                new_stop_loss REAL,
                adjustment_urgency TEXT,

                full_json_data TEXT NOT NULL,

                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                FOREIGN KEY (ticker) REFERENCES stock_holdings(ticker)
            )
        """)

        self.conn.commit()

        # 시장 상태 분석 실행
        await self._analyze_simple_market_condition()

        return True

    async def _analyze_simple_market_condition(self):
        """시장 상태 분석 (강세장/약세장)"""
        try:
            from krx_data_client import get_index_ohlcv_by_date
            import datetime as dt

            # 오늘 날짜
            today = dt.datetime.now().strftime("%Y%m%d")

            # 1달 전 날짜
            one_month_ago = (dt.datetime.now() - dt.timedelta(days=30)).strftime("%Y%m%d")

            # 코스피, 코스닥 지수 데이터 가져오기
            kospi_df = get_index_ohlcv_by_date(one_month_ago, today, "1001")
            kosdaq_df = get_index_ohlcv_by_date(one_month_ago, today, "2001")

            # 지수 추세 분석
            kospi_trend = self._calculate_trend(kospi_df['Close'])
            kosdaq_trend = self._calculate_trend(kosdaq_df['Close'])

            # 전체 시장 상태 결정
            # 두 지수 모두 상승 추세면 강세장(1), 두 지수 모두 하락 추세면 약세장(-1), 그 외는 중립(0)
            if kospi_trend > 0 and kosdaq_trend > 0:
                market_condition = 1  # 강세장
            elif kospi_trend < 0 and kosdaq_trend < 0:
                market_condition = -1  # 약세장
            else:
                market_condition = 0  # 중립

            # 시장 변동성 계산 (코스피, 코스닥 변동성의 평균)
            kospi_volatility = self._calculate_volatility(kospi_df['Close'])
            kosdaq_volatility = self._calculate_volatility(kosdaq_df['Close'])
            avg_volatility = (kospi_volatility + kosdaq_volatility) / 2

            # 시장 상태 저장
            self.simple_market_condition = market_condition

            # DB에 저장
            current_date = dt.datetime.now().strftime("%Y-%m-%d")
            self.cursor.execute(
                """
                INSERT OR REPLACE INTO market_condition
                (date, kospi_index, kosdaq_index, condition, volatility)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    current_date,
                    kospi_df['Close'].iloc[-1],
                    kosdaq_df['Close'].iloc[-1],
                    market_condition,
                    avg_volatility
                )
            )
            self.conn.commit()

            logger.info(f"시장 상태 분석 완료: {'강세장' if market_condition == 1 else '약세장' if market_condition == -1 else '중립'}, 변동성: {avg_volatility:.2f}%")

            return market_condition, avg_volatility

        except Exception as e:
            logger.error(f"시장 상태 분석 중 오류: {str(e)}")
            return 0, 0  # 오류 시 중립 상태로 가정

    def _calculate_trend(self, price_series):
        """가격 시리즈의 추세 분석 (양수: 상승, 음수: 하락)"""
        # 단순 선형 회귀로 추세 계산
        x = np.arange(len(price_series))
        slope, _, _, _, _ = stats.linregress(x, price_series)
        return slope

    def _calculate_volatility(self, price_series):
        """가격 시리즈의 변동성 계산 (일간 수익률의 표준편차, 연율화)"""
        daily_returns = price_series.pct_change().dropna()
        daily_volatility = daily_returns.std()
        return daily_volatility * 100  # 퍼센트로 변환

    async def _get_stock_volatility(self, ticker):
        """개별 종목의 변동성 계산"""
        try:
            # 캐시된 변동성이 있으면 사용
            if ticker in self.volatility_table:
                return self.volatility_table[ticker]

            # 60일간의 가격 데이터 가져오기
            today = datetime.now()
            start_date = (today - timedelta(days=60)).strftime("%Y%m%d")
            end_date = today.strftime("%Y%m%d")

            # Fetch stock price data using krx_data_client
            from krx_data_client import get_market_ohlcv_by_date
            df = get_market_ohlcv_by_date(start_date, end_date, ticker)

            if df.empty:
                logger.warning(f"{ticker} 가격 데이터를 가져올 수 없습니다.")
                return 15.0  # 기본 변동성 (15%)

            # 일간 수익률의 표준편차 계산
            daily_returns = df['Close'].pct_change().dropna()
            volatility = daily_returns.std() * 100  # 퍼센트로 변환

            # 변동성 테이블에 저장
            self.volatility_table[ticker] = volatility

            return volatility

        except Exception as e:
            logger.error(f"{ticker} 변동성 계산 중 오류: {str(e)}")
            return 15.0  # 오류 시 기본 변동성 반환

    async def _dynamic_stop_loss(self, ticker, buy_price):
        """종목별 변동성에 기반한 동적 손절 가격 계산"""
        try:
            # 종목의 변동성 가져오기
            volatility = await self._get_stock_volatility(ticker)

            # 변동성에 따른 손절폭 계산 (변동성이 클수록 더 넓게 설정)
            # 기본 손절폭 5%에 변동성 조정치 적용
            base_stop_loss_pct = 5.0

            # 시장 평균 변동성 (15% 가정) 대비 상대적 변동성 비율
            relative_volatility = volatility / 15.0

            # 조정된 손절폭 계산 (최소 3%, 최대 15%)
            adjusted_stop_loss_pct = min(max(base_stop_loss_pct * relative_volatility, 3.0), 15.0)

            # 시장 상태에 따른 추가 조정
            if self.simple_market_condition == -1:  # 약세장
                adjusted_stop_loss_pct = adjusted_stop_loss_pct * 0.8  # 더 타이트하게
            elif self.simple_market_condition == 1:  # 강세장
                adjusted_stop_loss_pct = adjusted_stop_loss_pct * 1.2  # 더 넓게

            # 손절가 계산
            stop_loss = buy_price * (1 - adjusted_stop_loss_pct/100)

            logger.info(f"{ticker} 동적 손절가 계산: {stop_loss:,.0f}원 (변동성: {volatility:.2f}%, 손절폭: {adjusted_stop_loss_pct:.2f}%)")

            return stop_loss

        except Exception as e:
            logger.error(f"{ticker} 동적 손절가 계산 중 오류: {str(e)}")
            return buy_price * 0.95  # 오류 시 기본 5% 손절폭 적용

    async def _dynamic_target_price(self, ticker, buy_price):
        """종목별 변동성에 기반한 동적 목표가 계산"""
        try:
            # 종목의 변동성 가져오기
            volatility = await self._get_stock_volatility(ticker)

            # 변동성에 따른 목표가 계산 (변동성이 클수록 더 높게 설정)
            # 기본 목표 수익률 10%에 변동성 조정치 적용
            base_target_pct = 10.0

            # 시장 평균 변동성 (15% 가정) 대비 상대적 변동성 비율
            relative_volatility = volatility / 15.0

            # 조정된 목표 수익률 계산 (최소 5%, 최대 30%)
            adjusted_target_pct = min(max(base_target_pct * relative_volatility, 5.0), 30.0)

            # 시장 상태에 따른 추가 조정
            if self.simple_market_condition == 1:  # 강세장
                adjusted_target_pct = adjusted_target_pct * 1.3  # 더 높게
            elif self.simple_market_condition == -1:  # 약세장
                adjusted_target_pct = adjusted_target_pct * 0.7  # 더 낮게

            # 목표가 계산
            target_price = buy_price * (1 + adjusted_target_pct/100)

            logger.info(f"{ticker} 동적 목표가 계산: {target_price:,.0f}원 (변동성: {volatility:.2f}%, 목표 수익률: {adjusted_target_pct:.2f}%)")

            return target_price

        except Exception as e:
            logger.error(f"{ticker} 동적 목표가 계산 중 오류: {str(e)}")
            return buy_price * 1.1  # 오류 시 기본 10% 목표 수익률 적용

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
                    logger.info(f"매도: {stock['company_name']}({stock['ticker']}) - 수익률: {stock['profit_rate']:.2f}% / 이유: {stock['reason']}")
            else:
                logger.info("매도된 종목이 없습니다.")

            # 2. 새로운 보고서 분석 및 매수 의사결정
            for pdf_report_path in pdf_report_paths:
                # 보고서 분석
                analysis_result = await self.analyze_report(pdf_report_path)

                if not analysis_result.get("success", False):
                    logger.error(f"보고서 분석 실패: {pdf_report_path} - {analysis_result.get('error', '알 수 없는 오류')}")
                    continue

                # 이미 보유 중인 종목이면 스킵
                if analysis_result.get("decision") == "보유 중":
                    logger.info(f"보유 중 종목 스킵: {analysis_result.get('ticker')} - {analysis_result.get('company_name')}")
                    continue

                # 종목 정보 및 시나리오
                ticker = analysis_result.get("ticker")
                company_name = analysis_result.get("company_name")
                current_price = analysis_result.get("current_price", 0)
                scenario = analysis_result.get("scenario", {})
                sector = analysis_result.get("sector", "알 수 없음")
                sector_diverse = analysis_result.get("sector_diverse", True)
                # rank_change_percentage = analysis_result.get("rank_change_percentage", 0)
                rank_change_msg = analysis_result.get("rank_change_msg", "")

                # 진입 결정 확인
                buy_score = scenario.get("buy_score", 0)
                min_score = scenario.get("min_score", 0)
                decision = analysis_result.get("decision")
                logger.info(f"매수 점수 체크: {company_name}({ticker}) - 점수: {buy_score}, 최소 요구 점수: {min_score}")

                # 매수하지 않는 경우 (관망/점수 부족/산업군 제약) 메시지 생성
                if decision != "진입" or buy_score < min_score or not sector_diverse:
                    # 매수하지 않는 이유 결정
                    reason = ""
                    if not sector_diverse:
                        reason = f"산업군 '{sector}' 과다 투자 방지"
                    elif buy_score < min_score:
                        if decision == "진입":
                            decision = "관망"  # "진입"에서 "관망"으로 변경
                            logger.info(f"매수 점수 부족으로 결정 변경: {company_name}({ticker}) - 진입 → 관망 (점수: {buy_score} < {min_score})")
                        reason = f"매수 점수 부족 ({buy_score} < {min_score})"
                    elif decision != "진입":
                        reason = "분석 결정이 '관망'"

                    # 시장 상태 정보
                    market_condition_text = scenario.get("market_condition")

                    # 관망 메시지 생성
                    skip_message = f"⚠️ 매수 보류: {company_name}({ticker})\n" \
                                   f"현재가: {current_price:,.0f}원\n" \
                                   f"매수 점수: {buy_score}/10\n" \
                                   f"결정: {decision}\n" \
                                   f"시장 상태: {market_condition_text}\n" \
                                   f"산업군: {scenario.get('sector', '알 수 없음')}\n" \
                                   f"보류 이유: {reason}\n" \
                                   f"분석 의견: {scenario.get('rationale', '정보 없음')}"

                    self.message_queue.append(skip_message)
                    logger.info(f"매수 보류: {company_name}({ticker}) - {reason}")

                    # 관망 종목을 watchlist_history 테이블에 저장
                    await self._save_watchlist_item(
                        ticker=ticker,
                        company_name=company_name,
                        current_price=current_price,
                        buy_score=buy_score,
                        min_score=min_score,
                        decision=decision,
                        skip_reason=reason,
                        scenario=scenario,
                        sector=sector
                    )

                    continue

                # 진입 결정이면 매수 처리
                if decision == "진입" and buy_score >= min_score and sector_diverse:
                    # 매수 처리
                    buy_success = await self.buy_stock(ticker, company_name, current_price, scenario, rank_change_msg)

                    if buy_success:
                        # 실제 계좌 매매 함수 호출(비동기)
                        from trading.domestic_stock_trading import AsyncTradingContext
                        async with AsyncTradingContext() as trading:
                            # 비동기 매수 실행
                            trade_result = await trading.async_buy_stock(stock_code=ticker, buy_price=current_price)

                        if trade_result['success']:
                            logger.info(f"실제 매수 성공: {trade_result['message']}")
                        else:
                            logger.error(f"실제 매수 실패: {trade_result['message']}")

                    if buy_success:
                        buy_count += 1
                        logger.info(f"매수 완료: {company_name}({ticker}) @ {current_price:,.0f}원")
                    else:
                        logger.warning(f"매수 실패: {company_name}({ticker})")

            logger.info(f"보고서 처리 완료 - 매수: {buy_count}건, 매도: {sell_count}건")
            return buy_count, sell_count

        except Exception as e:
            logger.error(f"보고서 처리 중 오류: {str(e)}")
            logger.error(traceback.format_exc())
            return 0, 0

    async def buy_stock(self, ticker: str, company_name: str, current_price: float, scenario: Dict[str, Any], rank_change_msg: str = "") -> bool:
        """
        주식 매수 처리 (부모 클래스 메서드 오버라이드)
        """
        try:
            # 시나리오에 목표가/손절가가 없거나 0이면 동적으로 계산
            if scenario.get('target_price', 0) <= 0:
                target_price = await self._dynamic_target_price(ticker, current_price)
                scenario['target_price'] = target_price
                logger.info(f"{ticker} 동적 목표가 계산: {target_price:,.0f}원")

            if scenario.get('stop_loss', 0) <= 0:
                stop_loss = await self._dynamic_stop_loss(ticker, current_price)
                scenario['stop_loss'] = stop_loss
                logger.info(f"{ticker} 동적 손절가 계산: {stop_loss:,.0f}원")

            # 부모 클래스의 buy_stock 메서드 호출
            return await super().buy_stock(ticker, company_name, current_price, scenario, rank_change_msg)

        except Exception as e:
            logger.error(f"{ticker} 매수 처리 중 오류: {str(e)}")
            logger.error(traceback.format_exc())
            return False

    async def _save_watchlist_item(
        self,
        ticker: str,
        company_name: str,
        current_price: float,
        buy_score: int,
        min_score: int,
        decision: str,
        skip_reason: str,
        scenario: Dict[str, Any],
        sector: str
    ) -> bool:
        """
        매수하지 않는 종목을 watchlist_history 테이블에 저장

        Args:
            ticker: 종목 코드
            company_name: 종목명
            current_price: 현재가
            buy_score: 매수 점수
            min_score: 최소 요구 점수
            decision: 결정 (진입/관망)
            skip_reason: 보류 이유
            scenario: 시나리오 전체 정보
            sector: 산업군

        Returns:
            bool: 저장 성공 여부
        """
        try:
            # 현재 시간
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # 시나리오에서 필요한 정보 추출
            target_price = scenario.get('target_price', 0)
            stop_loss = scenario.get('stop_loss', 0)
            investment_period = scenario.get('investment_period', '단기')
            portfolio_analysis = scenario.get('portfolio_analysis', '')
            valuation_analysis = scenario.get('valuation_analysis', '')
            sector_outlook = scenario.get('sector_outlook', '')
            market_condition = scenario.get('market_condition', '')
            rationale = scenario.get('rationale', '')

            # DB에 저장
            self.cursor.execute(
                """
                INSERT INTO watchlist_history
                (ticker, company_name, current_price, analyzed_date, buy_score, min_score,
                 decision, skip_reason, target_price, stop_loss, investment_period, sector,
                 scenario, portfolio_analysis, valuation_analysis, sector_outlook,
                 market_condition, rationale)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ticker,
                    company_name,
                    current_price,
                    now,
                    buy_score,
                    min_score,
                    decision,
                    skip_reason,
                    target_price,
                    stop_loss,
                    investment_period,
                    sector,
                    json.dumps(scenario, ensure_ascii=False),
                    portfolio_analysis,
                    valuation_analysis,
                    sector_outlook,
                    market_condition,
                    rationale
                )
            )
            self.conn.commit()

            logger.info(f"{ticker}({company_name}) 관망 종목 저장 완료 - 점수: {buy_score}/{min_score}, 이유: {skip_reason}")
            return True

        except Exception as e:
            logger.error(f"{ticker} watchlist 저장 중 오류: {str(e)}")
            logger.error(traceback.format_exc())
            return False

    async def _analyze_trend(self, ticker, days=14):
        """종목의 단기 추세 분석"""
        try:
            # 데이터 가져오기
            today = datetime.now()
            start_date = (today - timedelta(days=days)).strftime("%Y%m%d")
            end_date = today.strftime("%Y%m%d")

            from krx_data_client import get_market_ohlcv_by_date
            df = get_market_ohlcv_by_date(start_date, end_date, ticker)

            if df.empty:
                return 0  # 중립 (데이터 없음)

            # 추세 계산
            prices = df['Close'].values
            x = np.arange(len(prices))

            # 선형 회귀로 추세 계산
            slope, _, _, _, _ = stats.linregress(x, prices)

            # 가격 변화량 대비 추세 강도 계산
            price_range = np.max(prices) - np.min(prices)
            normalized_slope = slope * len(prices) / price_range if price_range > 0 else 0

            # 임계값 기반 추세 판단
            if normalized_slope > 0.15:  # 강한 상승 추세
                return 2
            elif normalized_slope > 0.05:  # 약한 상승 추세
                return 1
            elif normalized_slope < -0.15:  # 강한 하락 추세
                return -2
            elif normalized_slope < -0.05:  # 약한 하락 추세
                return -1
            else:  # 중립 추세
                return 0

        except Exception as e:
            logger.error(f"{ticker} 추세 분석 중 오류: {str(e)}")
            return 0  # 오류 발생 시 중립 추세로 가정

    async def _analyze_sell_decision(self, stock_data):
        """AI 에이전트 기반 매도 의사결정 분석"""
        try:
            ticker = stock_data.get('ticker', '')
            company_name = stock_data.get('company_name', '')
            buy_price = stock_data.get('buy_price', 0)
            buy_date = stock_data.get('buy_date', '')
            current_price = stock_data.get('current_price', 0)
            target_price = stock_data.get('target_price', 0)
            stop_loss = stock_data.get('stop_loss', 0)

            # 수익률 계산
            profit_rate = ((current_price - buy_price) / buy_price) * 100

            # 매수일로부터 경과 일수
            buy_datetime = datetime.strptime(buy_date, "%Y-%m-%d %H:%M:%S")
            days_passed = (datetime.now() - buy_datetime).days

            # 시나리오 정보 추출
            scenario_str = stock_data.get('scenario', '{}')
            period = "중기"  # 기본값
            sector = "알 수 없음"
            trading_scenarios = {}

            try:
                if isinstance(scenario_str, str):
                    scenario_data = json.loads(scenario_str)
                    period = scenario_data.get('investment_period', '중기')
                    sector = scenario_data.get('sector', '알 수 없음')
                    trading_scenarios = scenario_data.get('trading_scenarios', {})
            except:
                pass

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
                scenario_str = holding.get('scenario', '{}')
                try:
                    # 산업군 정보 수집
                    sector_distribution[sector] = sector_distribution.get(sector, 0) + 1
                    # 투자 기간 정보 수집
                    investment_periods[period] = investment_periods.get(period, 0) + 1
                except:
                    pass

            # 포트폴리오 정보 문자열
            portfolio_info = f"""
            현재 보유 종목 수: {len(holdings)}/{self.max_slots}
            산업군 분포: {json.dumps(sector_distribution, ensure_ascii=False)}
            투자 기간 분포: {json.dumps(investment_periods, ensure_ascii=False)}
            """
            index_profit_rate_result = await self.get_index_profit_rate()
            if index_profit_rate_result:
                try:
                    portfolio_info += f"""시장 지수 수익률: {json.dumps(index_profit_rate_result, ensure_ascii=False)}"""
                except:
                    pass
            logger.info(f"{portfolio_info}")

            # LLM 호출하여 매도 의사결정 생성
            llm = await self.sell_decision_agent.attach_llm(OpenAIAugmentedLLM)

            response = await llm.generate_str(
                message=f"""
                다음 보유 종목에 대한 매도 의사결정을 수행해주세요.

                ### 종목 기본 정보:
                - 종목명: {company_name}({ticker})
                - 매수가: {buy_price:,.0f}원
                - 현재가: {current_price:,.0f}원
                - 목표가: {target_price:,.0f}원
                - 손절가: {stop_loss:,.0f}원
                - 수익률: {profit_rate:.2f}%
                - 보유기간: {days_passed}일
                - 투자기간: {period}
                - 섹터: {sector}

                ### 현재 포트폴리오 상황:
                {portfolio_info}

                ### 매매 시나리오 정보:
                {json.dumps(trading_scenarios, ensure_ascii=False) if trading_scenarios else "시나리오 정보 없음"}

                ### 분석 요청:
                위 정보를 바탕으로 kospi_kosdaq과 sqlite 도구를 활용하여 최신 데이터를 확인하고,
                매도할지 계속 보유할지 결정해주세요.
                """,
                request_params=RequestParams(
                    model="gpt-5.1",
                    maxTokens=16000,
                    metadata={
                        "service_tier":"flex",
                        "reasoning_effort":"high",
                    }
                )
            )

            # JSON 파싱
            try:
                # 마크다운 코드 블록에서 JSON 추출 시도
                markdown_match = re.search(r'```(?:json)?\s*({[\s\S]*?})\s*```', response, re.DOTALL)
                if markdown_match:
                    json_str = markdown_match.group(1)
                    json_str = re.sub(r',(\s*})', r'\1', json_str)
                    decision_json = json.loads(json_str)
                    logger.info(f"매도 결정 파싱 성공: {json.dumps(decision_json, ensure_ascii=False)}")
                else:
                    # 일반 JSON 객체 추출 시도
                    json_match = re.search(r'({[\s\S]*?})(?:\s*$|\n\n)', response, re.DOTALL)
                    if json_match:
                        json_str = json_match.group(1)
                        json_str = re.sub(r',(\s*})', r'\1', json_str)
                        decision_json = json.loads(json_str)
                        logger.info(f"매도 결정 파싱 성공: {json.dumps(decision_json, ensure_ascii=False)}")
                    else:
                        # 전체 응답이 JSON인 경우
                        clean_response = re.sub(r',(\s*})', r'\1', response)
                        decision_json = json.loads(clean_response)
                        logger.info(f"매도 결정 파싱 성공: {json.dumps(decision_json, ensure_ascii=False)}")

                # 결과 추출 - 기존 단일 형식 사용
                should_sell = decision_json.get("should_sell", False)
                sell_reason = decision_json.get("sell_reason", "AI 분석 결과")
                confidence = decision_json.get("confidence", 5)
                analysis_summary = decision_json.get("analysis_summary", {})
                portfolio_adjustment = decision_json.get("portfolio_adjustment", {})

                logger.info(f"{ticker}({company_name}) AI 매도 결정: {'매도' if should_sell else '보유'} (확신도: {confidence}/10)")
                logger.info(f"매도 사유: {sell_reason}")

                # ===== 핵심: should_sell 분기에 따른 DB 처리 (에러가 나도 메인 플로우는 계속 진행) =====
                try:
                    if should_sell:
                        # 매도 결정 시: holding_decisions 테이블에서 삭제
                        await self._delete_holding_decision(ticker)

                        # 매도 시 analysis_summary를 sell_reason에 추가
                        if analysis_summary:
                            detailed_reason = self._format_sell_reason_with_analysis(sell_reason, analysis_summary)
                            return should_sell, detailed_reason
                    else:
                        # 보유 결정 시: holding_decisions 테이블에 저장/업데이트
                        await self._save_holding_decision(ticker, current_price, decision_json)

                        # portfolio_adjustment 처리
                        if portfolio_adjustment.get("needed", False):
                            await self._process_portfolio_adjustment(ticker, company_name, portfolio_adjustment, analysis_summary)
                except Exception as db_err:
                    # DB 조작 실패해도 메인 플로우는 계속 진행
                    logger.error(f"{ticker} holding_decisions DB 처리 중 오류 (메인 플로우 계속 진행): {str(db_err)}")
                    logger.error(traceback.format_exc())

                return should_sell, sell_reason

            except Exception as json_err:
                logger.error(f"매도 결정 JSON 파싱 오류: {json_err}")
                logger.error(f"원본 응답: {response}")

                # 파싱 실패 시 기존 알고리즘으로 폴백
                logger.warning(f"{ticker} AI 분석 실패, 기존 알고리즘으로 폴백")
                return await self._fallback_sell_decision(stock_data)

        except Exception as e:
            logger.error(f"{stock_data.get('ticker', '') if 'ticker' in locals() else '알 수 없는 종목'} AI 매도 분석 중 오류: {str(e)}")
            logger.error(traceback.format_exc())

            # 오류 시 기존 알고리즘으로 폴백
            return await self._fallback_sell_decision(stock_data)

    async def _fallback_sell_decision(self, stock_data):
        """기존 알고리즘 기반 매도 의사결정 (폴백용)"""
        try:
            ticker = stock_data.get('ticker', '')
            buy_price = stock_data.get('buy_price', 0)
            buy_date = stock_data.get('buy_date', '')
            current_price = stock_data.get('current_price', 0)
            target_price = stock_data.get('target_price', 0)
            stop_loss = stock_data.get('stop_loss', 0)

            # 수익률 계산
            profit_rate = ((current_price - buy_price) / buy_price) * 100

            # 매수일로부터 경과 일수
            buy_datetime = datetime.strptime(buy_date, "%Y-%m-%d %H:%M:%S")
            days_passed = (datetime.now() - buy_datetime).days

            # 시나리오 정보 추출
            scenario_str = stock_data.get('scenario', '{}')
            investment_period = "중기"  # 기본값

            try:
                if isinstance(scenario_str, str):
                    scenario_data = json.loads(scenario_str)
                    investment_period = scenario_data.get('investment_period', '중기')
            except:
                pass

            # 종목의 추세 분석(7일 선형회귀 분석)
            trend = await self._analyze_trend(ticker, days=7)

            # 매도 의사결정 우선순위에 따라 조건 체크

            # 1. 손절매 조건 확인 (가장 높은 우선순위)
            if stop_loss > 0 and current_price <= stop_loss:
                # 강한 상승 추세에서는 손절 유예 (예외 케이스)
                if trend >= 2 and profit_rate > -7:  # 강한 상승 추세 & 손실이 7% 미만
                    return False, "손절 유예 (강한 상승 추세)"
                return True, f"손절매 조건 도달 (손절가: {stop_loss:,.0f}원)"

            # 2. 목표가 도달 확인
            if target_price > 0 and current_price >= target_price:
                # 강한 상승 추세면 계속 보유 (예외 케이스)
                if trend >= 2:
                    return False, "목표가 달성했으나 강한 상승 추세로 보유 유지"
                return True, f"목표가 달성 (목표가: {target_price:,.0f}원)"

            # 3. 시장 상태와 추세에 따른 매도 조건 (시장 환경 고려)
            if self.simple_market_condition == -1 and trend < 0 and profit_rate > 3:
                return True, f"약세장 + 하락 추세에서 수익 확보 (수익률: {profit_rate:.2f}%)"

            # 4. 투자 기간별 조건 (투자 유형에 따른 분화)
            if investment_period == "단기":
                # 단기 투자 수익 목표 달성
                if days_passed >= 15 and profit_rate >= 5 and trend < 2:
                    return True, f"단기 투자 목표 달성 (보유일: {days_passed}일, 수익률: {profit_rate:.2f}%)"

                # 단기 투자 손실 방어 (단, 강한 상승 추세면 유지)
                if days_passed >= 10 and profit_rate <= -3 and trend < 2:
                    return True, f"단기 투자 손실 방어 (보유일: {days_passed}일, 수익률: {profit_rate:.2f}%)"

            # 5. 일반적인 수익 목표 달성 (특별한 기간이 아닌 일반 투자)
            if profit_rate >= 10 and trend < 2:
                return True, f"수익률 10% 이상 달성 (현재 수익률: {profit_rate:.2f}%)"

            # 6. 장기 보유 후 상태 점검 (시간 경과에 따른 판단)
            # 손절가보다 높지만 장기간 손실이 지속되는 경우
            if days_passed >= 30 and profit_rate < 0 and trend < 1:
                return True, f"30일 이상 보유 중이며 손실 상태 (보유일: {days_passed}일, 수익률: {profit_rate:.2f}%)"

            if days_passed >= 60 and profit_rate >= 3 and trend < 1:
                return True, f"60일 이상 보유 중이며 3% 이상 수익 (보유일: {days_passed}일, 수익률: {profit_rate:.2f}%)"

            # 7. 투자 유형별 장기 점검 (투자 기간 특화)
            if investment_period == "장기" and days_passed >= 90 and profit_rate < 0 and trend < 1:
                return True, f"장기 투자 손실 정리 (보유일: {days_passed}일, 수익률: {profit_rate:.2f}%)"

            # 8. 손절가는 아니지만 급격한 손실 발생 (비상 대응)
            # 일반 손실 매도 조건은 손절가 이하가 아닌 경우에만 적용
            # 손절가가 설정되지 않았거나(0) 손절가보다 현재가가 높으면서 큰 손실(-5% 이상)이 있는 경우
            if (stop_loss == 0 or current_price > stop_loss) and profit_rate <= -5 and trend < 1:
                return True, f"심각한 손실 발생 (현재 수익률: {profit_rate:.2f}%)"

            # 기본적으로 계속 보유
            trend_text = {
                2: "강한 상승 추세", 1: "약한 상승 추세", 0: "중립 추세",
                -1: "약한 하락 추세", -2: "강한 하락 추세"
            }.get(trend, "알 수 없는 추세")

            return False, f"계속 보유 (추세: {trend_text}, 수익률: {profit_rate:.2f}%)"

        except Exception as e:
            logger.error(f"폴백 매도 분석 중 오류: {str(e)}")
            return False, "분석 오류"

    async def _process_portfolio_adjustment(self, ticker: str, company_name: str, portfolio_adjustment: Dict[str, Any], analysis_summary: Dict[str, Any]):
        """portfolio_adjustment에 따른 DB 업데이트 및 텔레그램 알림 처리"""
        try:
            # 조정이 필요하지 않으면 리턴
            if not portfolio_adjustment.get("needed", False):
                return

            # 긴급도 확인 - low인 경우 실제 업데이트는 하지 않고 로그만
            urgency = portfolio_adjustment.get("urgency", "low").lower()
            if urgency == "low":
                logger.info(f"{ticker} 포트폴리오 조정 제안 (urgency=low): {portfolio_adjustment.get('reason', '')}")
                return

            db_updated = False
            update_message = ""
            adjustment_reason = portfolio_adjustment.get("reason", "AI 분석 결과")

            # 목표가 조정
            new_target_price = portfolio_adjustment.get("new_target_price")
            if new_target_price is not None:
                # 안전한 숫자 변환 (쉼표 제거 포함)
                target_price_num = self._safe_number_conversion(new_target_price)
                if target_price_num > 0:
                    self.cursor.execute(
                        "UPDATE stock_holdings SET target_price = ? WHERE ticker = ?",
                        (target_price_num, ticker)
                    )
                    self.conn.commit()
                    db_updated = True
                    update_message += f"목표가: {target_price_num:,.0f}원으로 조정\n"
                    logger.info(f"{ticker} 목표가 AI 조정: {target_price_num:,.0f}원 (긴급도: {urgency})")

            # 손절가 조정
            new_stop_loss = portfolio_adjustment.get("new_stop_loss")
            if new_stop_loss is not None:
                # 안전한 숫자 변환 (쉼표 제거 포함)
                stop_loss_num = self._safe_number_conversion(new_stop_loss)
                if stop_loss_num > 0:
                    self.cursor.execute(
                        "UPDATE stock_holdings SET stop_loss = ? WHERE ticker = ?",
                        (stop_loss_num, ticker)
                    )
                    self.conn.commit()
                    db_updated = True
                    update_message += f"손절가: {stop_loss_num:,.0f}원으로 조정\n"
                    logger.info(f"{ticker} 손절가 AI 조정: {stop_loss_num:,.0f}원 (긴급도: {urgency})")

            # DB가 업데이트되었으면 텔레그램 메시지 생성
            if db_updated:
                urgency_emoji = {"high": "🚨", "medium": "⚠️", "low": "💡"}.get(urgency, "🔄")
                message = f"{urgency_emoji} 포트폴리오 조정: {company_name}({ticker})\n"
                message += update_message
                message += f"조정 근거: {adjustment_reason}\n"
                message += f"긴급도: {urgency.upper()}\n"

                # 분석 요약 추가
                if analysis_summary:
                    message += f"기술적 추세: {analysis_summary.get('technical_trend', 'N/A')}\n"
                    message += f"시장 환경 영향: {analysis_summary.get('market_condition_impact', 'N/A')}"

                self.message_queue.append(message)
                logger.info(f"{ticker} AI 기반 포트폴리오 조정 완료: {update_message.strip()}")
            else:
                # 조정이 필요하다고 했지만 실제 값이 없는 경우
                logger.warning(f"{ticker} 포트폴리오 조정 요청됐지만 구체적 값 없음: {portfolio_adjustment}")

        except Exception as e:
            logger.error(f"{ticker} portfolio adjustment 처리 중 오류: {str(e)}")
            logger.error(traceback.format_exc())

    def _safe_number_conversion(self, value) -> float:
        """다양한 형태의 값을 안전하게 숫자로 변환"""
        try:
            # 이미 숫자 타입인 경우
            if isinstance(value, (int, float)):
                return float(value)

            # 문자열인 경우
            if isinstance(value, str):
                # 쉼표 제거하고 공백 제거
                cleaned_value = value.replace(',', '').replace(' ', '')
                # "원" 제거 (혹시 포함되어 있을 경우)
                cleaned_value = cleaned_value.replace('원', '')

                # 빈 문자열 체크
                if not cleaned_value:
                    return 0.0

                # 숫자로 변환
                return float(cleaned_value)

            # null이나 기타 타입인 경우
            return 0.0

        except (ValueError, TypeError) as e:
            logger.warning(f"숫자 변환 실패: {value} -> {str(e)}")
            return 0.0

    async def _save_holding_decision(self, ticker: str, current_price: float, decision_json: Dict[str, Any]) -> bool:
        """
        보유 종목의 AI 매도 판단 결과를 holding_decisions 테이블에 저장
        (실패해도 메인 플로우에 영향 없음)

        Args:
            ticker: 종목 코드
            current_price: 현재가
            decision_json: AI 판단 결과 JSON

        Returns:
            bool: 저장 성공 여부
        """
        try:
            now = datetime.now()
            decision_date = now.strftime("%Y-%m-%d")
            decision_time = now.strftime("%H:%M:%S")

            # JSON에서 데이터 추출
            should_sell = decision_json.get("should_sell", False)
            sell_reason = decision_json.get("sell_reason", "")
            confidence = decision_json.get("confidence", 0)

            analysis_summary = decision_json.get("analysis_summary", {})
            technical_trend = analysis_summary.get("technical_trend", "")
            volume_analysis = analysis_summary.get("volume_analysis", "")
            market_condition_impact = analysis_summary.get("market_condition_impact", "")
            time_factor = analysis_summary.get("time_factor", "")

            portfolio_adjustment = decision_json.get("portfolio_adjustment", {})
            adjustment_needed = portfolio_adjustment.get("needed", False)
            adjustment_reason = portfolio_adjustment.get("reason", "")
            new_target_price = self._safe_number_conversion(portfolio_adjustment.get("new_target_price"))
            new_stop_loss = self._safe_number_conversion(portfolio_adjustment.get("new_stop_loss"))
            adjustment_urgency = portfolio_adjustment.get("urgency", "low")

            # 전체 JSON을 문자열로 저장
            full_json_data = json.dumps(decision_json, ensure_ascii=False)

            # 기존 데이터 삭제 후 새로 삽입 (같은 ticker의 최신 판단만 유지)
            self.cursor.execute("DELETE FROM holding_decisions WHERE ticker = ?", (ticker,))

            # 새 판단 삽입
            self.cursor.execute("""
                INSERT INTO holding_decisions (
                    ticker, decision_date, decision_time, current_price, should_sell,
                    sell_reason, confidence, technical_trend, volume_analysis,
                    market_condition_impact, time_factor, portfolio_adjustment_needed,
                    adjustment_reason, new_target_price, new_stop_loss, adjustment_urgency,
                    full_json_data
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                ticker, decision_date, decision_time, current_price, should_sell,
                sell_reason, confidence, technical_trend, volume_analysis,
                market_condition_impact, time_factor, adjustment_needed,
                adjustment_reason, new_target_price, new_stop_loss, adjustment_urgency,
                full_json_data
            ))

            self.conn.commit()
            logger.info(f"{ticker} 보유 판단 저장 완료 - should_sell: {should_sell}, confidence: {confidence}")
            return True

        except Exception as e:
            logger.error(f"{ticker} 보유 판단 저장 실패 (메인 플로우 계속): {str(e)}")
            logger.error(traceback.format_exc())
            return False

    async def _delete_holding_decision(self, ticker: str) -> bool:
        """
        매도된 종목의 판단 데이터를 holding_decisions 테이블에서 삭제
        (실패해도 메인 플로우에 영향 없음)

        Args:
            ticker: 종목 코드

        Returns:
            bool: 삭제 성공 여부
        """
        try:
            self.cursor.execute("DELETE FROM holding_decisions WHERE ticker = ?", (ticker,))
            self.conn.commit()
            logger.info(f"{ticker} 매도 판단 데이터 삭제 완료")
            return True

        except Exception as e:
            logger.error(f"{ticker} 매도 판단 삭제 실패 (메인 플로우 계속): {str(e)}")
            return False

    def _format_sell_reason_with_analysis(self, sell_reason: str, analysis_summary: Dict[str, Any]) -> str:
        """매도 이유에 분석 요약 추가"""
        try:
            detailed_reason = sell_reason

            if analysis_summary:
                detailed_reason += "\n\n📊 상세 분석:"

                if analysis_summary.get('technical_trend'):
                    detailed_reason += f"\n• 기술적 추세: {analysis_summary['technical_trend']}"

                if analysis_summary.get('volume_analysis'):
                    detailed_reason += f"\n• 거래량 분석: {analysis_summary['volume_analysis']}"

                if analysis_summary.get('market_condition_impact'):
                    detailed_reason += f"\n• 시장 환경: {analysis_summary['market_condition_impact']}"

                if analysis_summary.get('time_factor'):
                    detailed_reason += f"\n• 시간 요인: {analysis_summary['time_factor']}"

            return detailed_reason

        except Exception as e:
            logger.error(f"매도 이유 포맷팅 중 오류: {str(e)}")
            return sell_reason