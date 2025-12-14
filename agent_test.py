from mcp_agent.agents.agent import Agent
from mcp_agent.workflows.llm.augmented_llm import RequestParams
from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM
import asyncio


async def main():
    sell_decision_agent = Agent(
        name="agent",
        instruction="""
        친절한 AI
        """,
        server_names=[]
    )
    # LLM 호출하여 매도 의사결정 생성
    llm = await sell_decision_agent.attach_llm(OpenAIAugmentedLLM)

    response = await llm.generate_str(
        message=f"""
        HI
        """,
        request_params=RequestParams(
            model="gpt-5.1",
            maxTokens=6000,
            metadata={
                "service_tier":"flex",
                "reasoning_effort":"none"
            }
        )
    )
    print(response)


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    print(exit_code)