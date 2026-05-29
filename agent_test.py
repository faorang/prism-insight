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
        간결한응답
        """,
        server_names=["time"]
    )
    # LLM 호출하여 매도 의사결정 생성
    llm = await sell_decision_agent.attach_llm(OpenAIAugmentedLLM)

    response = await llm.generate_str(
        message="""
        안녕, 지금 시간은?
        """,
        request_params=RequestParams(
            model="gpt-5.4-mini",
            reasoning_effort="none",
            maxTokens=32000,
            parallel_tool_calls=True,
            use_history=True,
        )
    )
    print(response)

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    print(exit_code)