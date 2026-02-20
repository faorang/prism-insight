from stock_analysis_orchestrator import StockAnalysisOrchestrator
from datetime import datetime
import os
import asyncio

async def main():
    log_file = os.path.join(os.getcwd(), f"orchestrator_{datetime.now().strftime('%Y%m%d')}.log")
    log_file2 = os.path.join(os.getcwd(), f"cut_loss_{datetime.now().strftime('%Y%m%d')}.log")
    orchestrator = StockAnalysisOrchestrator()
    log_paths = [log_file, log_file2]
    await orchestrator.send_telegram_messages([], log_paths)


if __name__ == "__main__":
    asyncio.run(main())