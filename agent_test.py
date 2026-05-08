from mcp_agent.agents.agent import Agent
from mcp_agent.workflows.llm.augmented_llm import RequestParams
from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM
import asyncio

import logging
from datetime import datetime

# Logger configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"agent_test_{datetime.now().strftime('%Y%m%d')}.log")
    ]
)
logger = logging.getLogger(__name__)


async def main():
    sell_decision_agent = Agent(
        name="agent",
        instruction="""
        1. 주가/거래량 데이터: tool call(name : kospi_kosdaq-get_stock_ohlcv)을 사용하여 {max_years_ago}~{reference_date} 기간의 데이터 수집 (수집 기간(년) : {max_years})
        2. **기술적 지표 - OHLCV 데이터에서 반드시 직접 계산:**
                           - RSI (14일): 종가 기준 계산. RS = 평균상승폭 / 평균하락폭, RSI = 100 - (100 / (1 + RS)). 정확한 수치 제시 (예: RSI = 72.5)
                           - MACD: 12일 EMA - 26일 EMA, 시그널선 = MACD의 9일 EMA. MACD 값과 시그널선 값 제시
                           - 볼린저밴드 (20일): 중심선 = 20일 SMA, 상단/하단 = 중심선 ± 2×표준편차. 현재가의 밴드 내 위치 제시

        ## 보고서 구성
        1. 주요 기술적 지표 및 해석 - 이동평균선, 지지/저항선, 기타 지표

        """,
        server_names=["firecrawl"]
    )
    # LLM 호출하여 매도 의사결정 생성
    llm = await sell_decision_agent.attach_llm(OpenAIAugmentedLLM)

    response = await llm.generate_str(
        message="""
        안녕
        https://finance.naver.com/item/news.naver?code=005930 에 접속하여 markdown 형식으로 주요 뉴스 내용을 알려줘
        """,
        request_params=RequestParams(
            model="gpt-5.4-mini",
            maxTokens=6000,
            reasoning_effort="none",
            parallel_tool_calls=True,
            use_history=True,
            metadata={
                "service_tier":"flex",
            }
        )
    )
    print(response)

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    print(exit_code)