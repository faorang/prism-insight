import asyncio
import os

from mcp_agent.app import MCPApp
from mcp_agent.agents.agent import Agent
from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM

app = MCPApp(name="hello_world_agent")

async def example_usage():
    async with app.run() as mcp_agent_app:
        # This agent can read the filesystem or fetch URLs
        finder_agent = Agent(
            name="echo",
            instruction="""Echo string back to the user.""",
            server_names=[], # MCP servers this Agent can use
        )

        async with finder_agent:
            # Attach an OpenAI LLM to the agent (defaults to GPT-4o)
            llm = await finder_agent.attach_llm(OpenAIAugmentedLLM)

            # This will perform a file lookup and read using the filesystem server
            result = await llm.generate_str(
                message="hello"
            )
            print(f"Agent result: {result}")

if __name__ == "__main__":
    asyncio.run(example_usage())