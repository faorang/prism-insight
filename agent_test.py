from mcp_agent.agents.agent import Agent
from mcp_agent.workflows.llm.augmented_llm import RequestParams
from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM
import asyncio


async def main():
    sell_decision_agent = Agent(
        name="sell_decision_agent",
        instruction="""
        ### tool 사용 지침
            **time-get_current_time으로 현재 시간 획득**
            **sqlite tool로 확인할 데이터:**
            현재 포트폴리오 종목 현황
        ### 출력 내용
        현재 시간, 포트 폴리오 종목 이름
        """,
        server_names=["sqlite", "time"]
    )
    # LLM 호출하여 매도 의사결정 생성
    llm = await sell_decision_agent.attach_llm(OpenAIAugmentedLLM)

    response = await llm.generate_str(
        message=f"""
        HI
        """,
        request_params=RequestParams(
            model="gpt-5",
            maxTokens=6000,
            metadata={
                "service_tier":"flex"
            }
        )
    )
    print(response)


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    print(exit_code)