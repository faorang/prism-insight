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
        친절한 AI.
        URL 접속 시 firecrawl_scrape tool을 사용하고 formats 파라미터는 ["markdown"]로, onlyMainContent 파라미터는 true로 설정하세요.
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