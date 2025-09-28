from stock_analysis_orchestrator import StockAnalysisOrchestrator
from datetime import datetime
import os
import asyncio

async def main():
    log_file = os.path.join(os.getcwd(), f"orchestrator_{datetime.now().strftime('%Y%m%d')}.log")
    orchestrator = StockAnalysisOrchestrator()
    log_paths = [log_file]
    await orchestrator.send_telegram_messages([], log_paths)


if __name__ == "__main__":
    asyncio.run(main())