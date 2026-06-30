"""
Report generation and conversion module
"""
import asyncio
import atexit
import json
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import markdown
from mcp_agent.agents.agent import Agent
from mcp_agent.app import MCPApp
from mcp_agent.workflows.llm.augmented_llm import RequestParams
from mcp_agent.workflows.llm.augmented_llm_anthropic import AnthropicAugmentedLLM

# Logger setup
logger = logging.getLogger(__name__)

# ============================================================================
# Global MCPApp management (prevent process accumulation)
# ============================================================================
_global_mcp_app: Optional[MCPApp] = None
_app_lock = asyncio.Lock()
_app_initialized = False


async def get_or_create_global_mcp_app() -> MCPApp:
    """
    Get or create global MCPApp instance

    Using this approach:
    - Server process starts only once
    - No new process creation per request
    - Prevents resource leaks

    Returns:
        MCPApp: Global MCPApp instance
    """
    global _global_mcp_app, _app_initialized

    async with _app_lock:
        if _global_mcp_app is None or not _app_initialized:
            logger.info("Starting global MCPApp initialization")
            _global_mcp_app = MCPApp(name="telegram_ai_bot_global")
            await _global_mcp_app.initialize()
            _app_initialized = True
            logger.info(f"Global MCPApp initialization complete (Session ID: {_global_mcp_app.session_id})")
        return _global_mcp_app


async def cleanup_global_mcp_app():
    """Cleanup global MCPApp"""
    global _global_mcp_app, _app_initialized

    async with _app_lock:
        if _global_mcp_app is not None and _app_initialized:
            logger.info("Starting global MCPApp cleanup")
            try:
                await _global_mcp_app.cleanup()
                logger.info("Global MCPApp cleanup complete")
            except Exception as e:
                logger.error(f"Error during global MCPApp cleanup: {e}")
            finally:
                _global_mcp_app = None
                _app_initialized = False


async def reset_global_mcp_app():
    """Restart global MCPApp (on error)"""
    logger.warning("Attempting to restart global MCPApp")
    await cleanup_global_mcp_app()
    return await get_or_create_global_mcp_app()


def _cleanup_on_exit():
    """Cleanup on program exit"""
    global _global_mcp_app
    try:
        if _global_mcp_app is not None:
            logger.info("Cleaning up global MCPApp on program exit")
            asyncio.run(cleanup_global_mcp_app())
    except Exception as e:
        logger.error(f"Error during exit cleanup: {e}")


# Auto cleanup on program exit
atexit.register(_cleanup_on_exit)
# ============================================================================

# Constant definitions
REPORTS_DIR = Path("reports")
REPORTS_DIR.mkdir(exist_ok=True)  # Create directory if it doesn't exist
HTML_REPORTS_DIR = Path("html_reports")
HTML_REPORTS_DIR.mkdir(exist_ok=True)  # HTML reports directory
PDF_REPORTS_DIR = Path("pdf_reports")
PDF_REPORTS_DIR.mkdir(exist_ok=True)  # PDF reports directory



# =============================================================================
# US Stock Report Caching Functions
# =============================================================================



def save_pdf_report(stock_code: str, company_name: str, md_path: Path) -> Path:
    """마크다운 파일을 PDF로 변환하여 저장

    Args:
        stock_code: 종목 코드
        company_name: 회사명
        md_path: 마크다운 파일 경로

    Returns:
        Path: 생성된 PDF 파일 경로
    """
    from pdf_converter import markdown_to_pdf

    reference_date = datetime.now().strftime("%Y%m%d")
    pdf_filename = f"{stock_code}_{company_name}_{reference_date}_analysis.pdf"
    pdf_path = PDF_REPORTS_DIR / pdf_filename

    try:
        markdown_to_pdf(str(md_path), str(pdf_path), 'playwright', add_theme=True)
        logger.info(f"PDF 보고서 생성 완료: {pdf_path}")
    except Exception as e:
        logger.error(f"PDF 변환 중 오류: {e}")
        raise

    return pdf_path


def get_cached_report(stock_code: str) -> tuple:
    """캐시된 보고서 검색

    Returns:
        tuple: (is_cached, content, md_path, pdf_path)
    """
    # Find all report files starting with stock code
    report_files = list(REPORTS_DIR.glob(f"{stock_code}_*.md"))

    if not report_files:
        return False, "", None, None

    # Sort by latest
    latest_file = max(report_files, key=lambda p: p.stat().st_mtime)

    # Check if file was created within 24 hours
    file_age = datetime.now() - datetime.fromtimestamp(latest_file.stat().st_mtime)
    if file_age.days >= 1:  # Don't use files older than 24 hours as cache
        return False, "", None, None

    # Check if corresponding PDF file exists
    pdf_file = None
    pdf_files = list(PDF_REPORTS_DIR.glob(f"{stock_code}_*.pdf"))
    if pdf_files:
        pdf_file = max(pdf_files, key=lambda p: p.stat().st_mtime)

    with open(latest_file, "r", encoding="utf-8") as f:
        content = f.read()

    # Generate PDF if it doesn't exist
    if not pdf_file:
        # Extract company name (filename format: {code}_{name}_{date}_analysis.md)
        company_name = os.path.basename(latest_file).split('_')[1]
        pdf_file = save_pdf_report(stock_code, company_name, latest_file)

    return True, content, latest_file, pdf_file


def save_report(stock_code: str, company_name: str, content: str) -> Path:
    """보고서를 파일로 저장"""
    reference_date = datetime.now().strftime("%Y%m%d")
    filename = f"{stock_code}_{company_name}_{reference_date}_analysis.md"
    filepath = REPORTS_DIR / filename

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    return filepath


def convert_to_html(markdown_content: str) -> str:
    """마크다운을 HTML로 변환"""
    try:
        # 마크다운을 HTML로 변환
        html_content = markdown.markdown(
            markdown_content,
            extensions=['markdown.extensions.fenced_code', 'markdown.extensions.tables']
        )

        # HTML 템플릿에 내용 삽입
        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>주식 분석 보고서</title>
            <style>
                body {{
                    font-family: 'Pretendard', -apple-system, system-ui, sans-serif;
                    line-height: 1.6;
                    color: #333;
                    max-width: 900px;
                    margin: 0 auto;
                    padding: 20px;
                }}
                h1, h2, h3, h4 {{
                    color: #2563eb;
                }}
                table {{
                    border-collapse: collapse;
                    width: 100%;
                    margin: 15px 0;
                }}
                th, td {{
                    border: 1px solid #ddd;
                    padding: 8px 12px;
                }}
                th {{
                    background-color: #f1f5f9;
                }}
                code {{
                    background-color: #f1f5f9;
                    padding: 2px 4px;
                    border-radius: 4px;
                }}
                pre {{
                    background-color: #f1f5f9;
                    padding: 15px;
                    border-radius: 8px;
                    overflow-x: auto;
                }}
            </style>
        </head>
        <body>
            {html_content}
        </body>
        </html>
        """
    except Exception as e:
        logger.error(f"HTML 변환 중 오류: {str(e)}")
        return f"<p>보고서 변환 중 오류가 발생했습니다: {str(e)}</p>"


def save_html_report_from_content(stock_code: str, company_name: str, html_content: str) -> Path:
    """HTML 내용을 파일로 저장"""
    reference_date = datetime.now().strftime("%Y%m%d")
    filename = f"{stock_code}_{company_name}_{reference_date}_analysis.html"
    filepath = HTML_REPORTS_DIR / filename

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html_content)

    return filepath


def save_html_report(stock_code: str, company_name: str, markdown_content: str) -> Path:
    """마크다운 보고서를 HTML로 변환하여 저장"""
    html_content = convert_to_html(markdown_content)
    return save_html_report_from_content(stock_code, company_name, html_content)


def generate_report_response_sync(stock_code: str, company_name: str) -> str:
    """
    종목 상세 보고서를 동기 방식으로 생성 (백그라운드 스레드에서 호출됨)
    """
    # subprocess 로그 파일 경로 설정
    log_dir = Path(os.path.dirname(os.path.abspath(__file__))) / "logs" / "subprocess"
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"report_{stock_code}_{timestamp}.log"

    try:
        logger.info(f"동기식 보고서 생성 시작: {stock_code} ({company_name})")
        logger.info(f"Subprocess 로그 파일: {log_file}")

        # 현재 날짜를 YYYYMMDD 형식으로 변환
        reference_date = datetime.now().strftime("%Y%m%d")

        # 별도의 프로세스로 분석 수행
        # 이 방법은 새로운 Python 프로세스를 생성하여 분석을 수행하므로 이벤트 루프 충돌 없음
        cmd = [
            sys.executable,  # 현재 Python 인터프리터
            "-c",
            f"""
import asyncio
import json
import sys
import logging
from datetime import datetime

# subprocess 내부 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stderr)]
)
subprocess_logger = logging.getLogger("subprocess_report")
subprocess_logger.info("Subprocess 시작: {stock_code} ({company_name})")

from cores.analysis import analyze_stock

async def run():
    try:
        subprocess_logger.info("analyze_stock 호출 시작")
        result = await analyze_stock(
            company_code="{stock_code}",
            company_name="{company_name}",
            reference_date="{reference_date}"
        )
        subprocess_logger.info(f"analyze_stock 완료: {{len(result) if result else 0}} 글자")
        # 구분자를 사용하여 결과 출력의 시작과 끝을 표시
        print("RESULT_START")
        print(json.dumps({{"success": True, "result": result}}))
        print("RESULT_END")
    except Exception as e:
        subprocess_logger.error(f"analyze_stock 오류: {{str(e)}}", exc_info=True)
        # 구분자를 사용하여 에러 출력의 시작과 끝을 표시
        print("RESULT_START")
        print(json.dumps({{"success": False, "error": str(e)}}))
        print("RESULT_END")

if __name__ == "__main__":
    asyncio.run(run())
            """
        ]

        # Set project root directory (required for cores module import)
        project_root = os.path.dirname(os.path.abspath(__file__))

        logger.info(f"External process execution: {stock_code} (cwd: {project_root})")

        # Run with Popen to save real-time logs
        with open(log_file, "w", encoding="utf-8") as f:
            f.write(f"=== Subprocess Log for {stock_code} ({company_name}) ===\n")
            f.write(f"Started at: {datetime.now().isoformat()}\n")
            f.write(f"Timeout: 1800 seconds (30 min)\n")
            f.write("=" * 60 + "\n\n")
            f.flush()

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=project_root
            )

            try:
                stdout, stderr = process.communicate(timeout=1800)  # 30 min timeout

                # Write to log file
                f.write("\n=== STDOUT ===\n")
                f.write(stdout or "(empty)")
                f.write("\n\n=== STDERR ===\n")
                f.write(stderr or "(empty)")
                f.write(f"\n\n=== Completed at: {datetime.now().isoformat()} ===\n")

            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate()

                # Save log even on timeout
                f.write("\n=== TIMEOUT OCCURRED ===\n")
                f.write(f"Timeout at: {datetime.now().isoformat()}\n")
                f.write("\n=== STDOUT (before timeout) ===\n")
                f.write(stdout or "(empty)")
                f.write("\n\n=== STDERR (before timeout) ===\n")
                f.write(stderr or "(empty)")

                logger.error(f"External process timeout: {stock_code}, log file: {log_file}")
                return f"Analysis time exceeded. Check log file: {log_file}"

        # Log stderr (for debugging)
        if stderr:
            logger.warning(f"External process stderr (full log: {log_file}): {stderr[:500]}")

        # Parse output - extract only actual JSON output using delimiters
        try:
            # Extract only JSON data between RESULT_START and RESULT_END from log output
            if "RESULT_START" in stdout and "RESULT_END" in stdout:
                result_start = stdout.find("RESULT_START") + len("RESULT_START")
                result_end = stdout.find("RESULT_END")
                json_str = stdout[result_start:result_end].strip()

                # Parse JSON
                parsed_output = json.loads(json_str)

                if parsed_output.get('success', False):
                    result = parsed_output.get('result', '')
                    logger.info(f"External process result: {len(result)} characters")
                    return result
                else:
                    error = parsed_output.get('error', 'Unknown error')
                    logger.error(f"External process error: {error}, log file: {log_file}")
                    return f"Error occurred during analysis: {error}"
            else:
                # If delimiters not found - process execution itself may have issues
                logger.error(f"Could not find result delimiters in external process output. Log file: {log_file}")
                logger.error(f"stdout excerpt: {stdout[:500] if stdout else '(empty)'}")
                if stderr:
                    logger.error(f"stderr excerpt: {stderr[:500]}")
                return f"Could not find analysis result. Log file: {log_file}"
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse external process output: {e}, log file: {log_file}")
            logger.error(f"Output content: {stdout[:1000] if stdout else '(empty)'}")
            return f"Error occurred while parsing analysis result. Log file: {log_file}"
    except Exception as e:
        logger.error(f"동기식 보고서 생성 중 오류: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return f"보고서 생성 중 오류가 발생했습니다: {str(e)}"

import re

def clean_model_response(response):
    # 마지막 평가 문장 패턴
    final_analysis_pattern = r'이제 수집한 정보를 바탕으로.*평가를 해보겠습니다\.'

    # 중간 과정 및 도구 호출 관련 정보 제거
    # 1. '[Calling tool' 포함 라인 제거
    lines = response.split('\n')
    cleaned_lines = [line for line in lines if '[Calling tool' not in line]
    temp_response = '\n'.join(cleaned_lines)

    # 2. 마지막 평가 문장이 있다면, 그 이후 내용만 유지
    final_statement_match = re.search(final_analysis_pattern, temp_response)
    if final_statement_match:
        final_statement_pos = final_statement_match.end()
        cleaned_response = temp_response[final_statement_pos:].strip()
    else:
        # 패턴을 찾지 못한 경우 그냥 도구 호출만 제거된 버전 사용
        cleaned_response = temp_response

    # 앞부분 빈 줄 제거
    cleaned_response = cleaned_response.lstrip()

    return cleaned_response





async def generate_journal_conversation_response(
    user_id: int,
    user_message: str,
    memory_context: str,
    ticker: str = None,
    ticker_name: str = None,
    conversation_history: list = None
) -> str:
    """
    저널/일기 대화에 대한 AI 응답 생성

    Args:
        user_id: 사용자 ID
        user_message: 사용자의 메시지
        memory_context: 사용자의 기억 컨텍스트 (저널, 평가 기록 등)
        ticker: 관련 종목 코드 (선택)
        ticker_name: 관련 종목명 (선택)
        conversation_history: 이전 대화 히스토리 (선택)

    Returns:
        str: AI 응답
    """
    try:
        # Use global MCPApp
        app = await get_or_create_global_mcp_app()
        app_logger = app.logger

        # Current date
        current_date = datetime.now().strftime('%Y년 %m월 %d일')

        # Ticker context
        ticker_context = ""
        if ticker and ticker_name:
            ticker_context = f"\n현재 대화 중인 종목: {ticker_name} ({ticker})"

        # Conversation history
        history_text = ""
        if conversation_history:
            history_items = []
            for item in conversation_history[-5:]:  # Last 5 items only
                role = "사용자" if item.get('role') == 'user' else "AI"
                content = item.get('content', '')[:200]
                history_items.append(f"[{role}] {content}")
            if history_items:
                history_text = "\n\n## 최근 대화 히스토리\n" + "\n".join(history_items)

        # Create agent
        agent = Agent(
            name="journal_conversation_agent",
            instruction=f"""당신은 사용자의 투자 파트너이자 친구입니다. 텔레그램에서 자유로운 대화를 나눕니다.

## 현재 날짜
{current_date}
{ticker_context}

## 사용자의 투자 기록과 과거 대화
{memory_context if memory_context else "(아직 기록된 내용이 없습니다)"}
{history_text}

## 역할과 성격
1. 사용자의 오랜 투자 친구처럼 대화하세요
2. 사용자가 과거에 기록한 저널과 평가 내용을 기억하고 활용하세요
3. 자연스럽고 친근한 대화체로 응답하세요
4. 필요하다면 주식 관련 질문에 답변할 수 있습니다

## 주식 데이터 조회 (필요한 경우에만)
- perplexity_ask: 최신 뉴스나 정보 검색
- kospi_kosdaq: 한국 주식 정보 (get_stock_ohlcv, get_stock_trading_volume)
사용자가 특정 종목에 대해 물어보면 도구를 사용해 최신 정보를 제공할 수 있습니다.

## 응답 가이드
1. 자연스러운 대화체로 응답하세요
2. 이모티콘을 적절히 사용하세요 (📈 💭 🤔 💡 😊 등)
3. 마크다운을 사용하지 마세요
4. 2000자 이내로 작성하세요
5. 사용자의 과거 기록을 자연스럽게 언급할 수 있습니다
6. 투자 조언을 할 때는 항상 "의견"임을 명시하세요

## 중요
- 사용자가 일반적인 대화를 원하면 주식 얘기를 강요하지 마세요
- "나에 대해 알아?" 같은 질문에는 기록된 내용을 바탕으로 답하세요
- 사용자를 존중하고 공감하는 태도를 유지하세요
""",
            server_names=["perplexity", "kospi_kosdaq"]
        )

        # Connect to LLM
        llm = await agent.attach_llm(AnthropicAugmentedLLM)

        # Generate response
        response = await llm.generate_str(
            message=f"""사용자 메시지: {user_message}

위 메시지에 자연스럽게 응답해주세요. 사용자의 과거 기록(저널, 평가 등)을 참고하여 개인화된 답변을 제공하세요.""",
            request_params=RequestParams(
                model="claude-sonnet-4-6",
                maxTokens=2000
            )
        )
        app_logger.info(f"Journal conversation response generated: user_id={user_id}, response_len={len(response)}")

        return clean_model_response(response)

    except Exception as e:
        logger.error(f"Error generating journal conversation response: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())

        # Try restarting global app on error
        try:
            await reset_global_mcp_app()
        except Exception:
            pass

        return "죄송해요, 응답 생성 중 문제가 생겼어요. 다시 말씀해주시겠어요? 💭"
