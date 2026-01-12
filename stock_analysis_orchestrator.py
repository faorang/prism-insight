#!/usr/bin/env python3
"""
주식 분석 및 텔레그램 전송 오케스트레이터

전체 프로세스:
1. 시간대별(오전/오후) 트리거 배치 작업 실행
2. 선정된 종목에 대한 상세 분석 보고서 생성
3. 보고서 PDF 변환
4. 텔레그램 채널 요약 메시지 생성 및 전송
5. 생성된 PDF 첨부파일 전송
"""
from dotenv import load_dotenv
load_dotenv()  # .env 파일에서 환경변수 로드
import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

# 로거 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"orchestrator_{datetime.now().strftime('%Y%m%d')}.log")
    ]
)
logger = logging.getLogger(__name__)

# 환경 설정
REPORTS_DIR = Path("reports")
TELEGRAM_MSGS_DIR = Path("telegram_messages")
PDF_REPORTS_DIR = Path("pdf_reports")

# 디렉토리 생성
REPORTS_DIR.mkdir(exist_ok=True)
TELEGRAM_MSGS_DIR.mkdir(exist_ok=True)
PDF_REPORTS_DIR.mkdir(exist_ok=True)
(TELEGRAM_MSGS_DIR / "sent").mkdir(exist_ok=True)

class StockAnalysisOrchestrator:
    """주식 분석 및 텔레그램 전송 오케스트레이터"""

    def __init__(self):
        """초기화"""
        self.selected_tickers = {}  # 선정된 종목 정보 저장

    async def run_trigger_batch(self, mode):
        """
        트리거 배치 실행 및 결과 저장 (비동기 버전)

        Args:
            mode (str): 'morning' 또는 'afternoon'

        Returns:
            list: 선정된 종목 코드 리스트
        """
        logger.info(f"트리거 배치 실행 시작: {mode}")
        try:
            # 배치 프로세스 실행
            # Direct import instead of subprocess to share KRX session
            from trigger_batch import run_batch
            # 임시 파일에 결과 저장
            results_file = f"trigger_results_{mode}_{datetime.now().strftime('%Y%m%d')}.json"

            # Run batch directly (synchronous call in async context)
            # run_batch is CPU-bound, so running it directly is acceptable
            loop = asyncio.get_event_loop()
            results = await loop.run_in_executor(
                None,
                lambda: run_batch(mode, "INFO", results_file)
            )

            if not results:
                logger.warning("Batch returned empty results")
                return []

            # 결과 파일 읽기
            if os.path.exists(results_file):
                with open(results_file, 'r', encoding='utf-8') as f:
                    full_results = json.load(f)

                # 결과 저장
                self.selected_tickers[mode] = full_results

            # Extract stock codes from results
            tickers = []
            ticker_codes = set()  # For duplicate checking

            # results is dict like {"거래량 급증 상위주": DataFrame, ...}
            for trigger_type, stocks_df in results.items():
                if hasattr(stocks_df, 'index'):  # It's a DataFrame
                    for ticker in stocks_df.index:
                        if ticker not in ticker_codes:
                            ticker_codes.add(ticker)
                            # Get stock name
                            name = ""
                            if "종목명" in stocks_df.columns:
                                name = stocks_df.loc[ticker, "종목명"]
                            tickers.append({
                                'code': ticker,
                                'name': name
                            })

            logger.info(f"Number of selected stocks: {len(tickers)}")
            return tickers

        except Exception as e:
            logger.error(f"트리거 배치 실행 중 오류: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return []

    async def convert_to_pdf(self, report_paths):
        """
        마크다운 보고서를 PDF로 변환

        Args:
            report_paths (list): 마크다운 보고서 파일 경로 리스트

        Returns:
            list: 생성된 PDF 파일 경로 리스트
        """
        logger.info(f"{len(report_paths)}개 보고서 PDF 변환 시작")
        pdf_paths = []

        # PDF 변환 모듈 임포트
        from pdf_converter import markdown_to_pdf

        for report_path in report_paths:
            try:
                report_file = Path(report_path)
                pdf_file = PDF_REPORTS_DIR / f"{report_file.stem}.pdf"

                # 마크다운을 PDF로 변환
                markdown_to_pdf(report_path, pdf_file, 'pdfkit', add_theme=True, enable_watermark=False)

                logger.info(f"PDF 변환 완료: {pdf_file}")
                pdf_paths.append(pdf_file)

            except Exception as e:
                logger.error(f"{report_path} PDF 변환 중 오류: {str(e)}")

        return pdf_paths

    async def generate_telegram_messages(self, report_pdf_paths):
        """
        텔레그램 메시지 생성

        Args:
            report_pdf_paths (list): 보고서 파일(pdf) 경로 리스트

        Returns:
            list: 생성된 텔레그램 메시지 파일 경로 리스트
        """
        logger.info(f"{len(report_pdf_paths)}개 보고서 텔레그램 메시지 생성 시작")

        # 텔레그램 요약 생성기 모듈 임포트
        from telegram_summary_agent import TelegramSummaryGenerator

        # 요약 생성기 초기화
        generator = TelegramSummaryGenerator()

        message_paths = []
        for report_pdf_path in report_pdf_paths:
            try:
                # 텔레그램 메시지 생성
                await generator.process_report(str(report_pdf_path), str(TELEGRAM_MSGS_DIR))

                # 생성된 메시지 파일 경로 추정
                report_file = Path(report_pdf_path)
                ticker = report_file.stem.split('_')[0]
                company_name = report_file.stem.split('_')[1]

                message_path = TELEGRAM_MSGS_DIR / f"{ticker}_{company_name}_telegram.txt"

                if message_path.exists():
                    logger.info(f"텔레그램 메시지 생성 완료: {message_path}")
                    message_paths.append(message_path)
                else:
                    logger.warning(f"텔레그램 메시지 파일이 예상 경로에 없습니다: {message_path}")

            except Exception as e:
                logger.error(f"{report_pdf_path} 텔레그램 메시지 생성 중 오류: {str(e)}")

        return message_paths

    async def send_telegram_messages(self, message_paths, pdf_paths):
        """
        텔레그램 메시지 및 PDF 파일 전송

        Args:
            message_paths (list): 텔레그램 메시지 파일 경로 리스트
            pdf_paths (list): PDF 파일 경로 리스트
        """
        logger.info(f"{len(message_paths)}개 텔레그램 메시지 전송 시작")

        # 환경 변수에서 채널 ID 가져오기
        from dotenv import load_dotenv
        load_dotenv()

        chat_id = os.getenv("TELEGRAM_CHANNEL_ID")
        if not chat_id:
            logger.error("텔레그램 채널 ID가 설정되지 않았습니다.")
            return

        # 텔레그램 봇 에이전트 초기화
        from telegram_bot_agent import TelegramBotAgent

        try:
            bot_agent = TelegramBotAgent()

            # 메시지 전송
            await bot_agent.process_messages_directory(
                str(TELEGRAM_MSGS_DIR),
                chat_id,
                str(TELEGRAM_MSGS_DIR / "sent")
            )

            # PDF 파일 전송
            for pdf_path in pdf_paths:
                logger.info(f"PDF 파일 전송: {pdf_path}")
                success = await bot_agent.send_document(chat_id, str(pdf_path))
                if success:
                    logger.info(f"PDF 파일 전송 성공: {pdf_path}")
                else:
                    logger.error(f"PDF 파일 전송 실패: {pdf_path}")

                # 전송 간격
                await asyncio.sleep(1)

        except Exception as e:
            logger.error(f"텔레그램 메시지 전송 중 오류: {str(e)}")

    async def send_trigger_alert(self, mode, trigger_results_file):
        """
        트리거 실행 결과 정보를 텔레그램 채널로 즉시 전송
        """
        logger.info(f"프리즘 시그널 얼럿 전송 시작 - 모드: {mode}")

        try:
            # JSON 파일 읽기
            with open(trigger_results_file, 'r', encoding='utf-8') as f:
                results = json.load(f)

            # 메타데이터 추출
            metadata = results.get("metadata", {})
            trade_date = metadata.get("trade_date", datetime.now().strftime("%Y%m%d"))

            # 트리거 종목 정보 추출 - 직접 리스트인 경우 처리
            all_results = {}
            for key, value in results.items():
                if key != "metadata" and isinstance(value, list):
                    # value가 직접 종목 리스트인 경우
                    all_results[key] = value

            if not all_results:
                logger.warning("트리거 결과가 없습니다.")
                return False

            # 텔레그램 메시지 생성
            message = self._create_trigger_alert_message(mode, all_results, trade_date)

            # 환경 변수에서 채널 ID 가져오기
            from dotenv import load_dotenv
            load_dotenv()

            chat_id = os.getenv("TELEGRAM_CHANNEL_ID")
            if not chat_id:
                logger.error("텔레그램 채널 ID가 설정되지 않았습니다.")
                return False

            # 텔레그램 봇 에이전트 초기화
            from telegram_bot_agent import TelegramBotAgent

            try:
                bot_agent = TelegramBotAgent()

                # 메시지 전송
                success = await bot_agent.send_message(chat_id, message)

                if success:
                    logger.info("프리즘 시그널 얼럿 전송 성공")
                    return True
                else:
                    logger.error("프리즘 시그널 얼럿 전송 실패")
                    return False

            except Exception as e:
                logger.error(f"텔레그램 봇 초기화 또는 메시지 전송 중 오류: {str(e)}")
                return False

        except Exception as e:
            logger.error(f"프리즘 시그널 얼럿 생성 중 오류: {str(e)}")
            return False

    def _create_trigger_alert_message(self, mode, results, trade_date):
        """
        트리거 결과를 기반으로 텔레그램 알림 메시지 생성
        """
        # 날짜 포맷 변환
        formatted_date = f"{trade_date[:4]}.{trade_date[4:6]}.{trade_date[6:8]}"

        # 모드에 따른 제목 설정
        if mode == "morning":
            title = "🔔 오전 프리즘 시그널 얼럿"
            time_desc = "장 시작 후 10분 시점"
        else:
            title = "🔔 오후 프리즘 시그널 얼럿"
            time_desc = "장 마감 후"

        # 메시지 헤더
        message = f"{title}\n"
        message += f"📅 {formatted_date} {time_desc} 포착된 관심종목\n\n"

        # 트리거별 종목 정보 추가
        for trigger_type, stocks in results.items():
            # 트리거 타입에 따른 이모지 설정
            emoji = self._get_trigger_emoji(trigger_type)

            message += f"{emoji} *{trigger_type}*\n"

            # 각 종목 정보 추가
            for stock in stocks:
                code = stock.get("code", "")
                name = stock.get("name", "")
                current_price = stock.get("current_price", 0)
                change_rate = stock.get("change_rate", 0)

                # 등락률에 따른 화살표
                arrow = "🔺" if change_rate > 0 else "🔻" if change_rate < 0 else "➖"

                # 기본 정보
                message += f"· *{name}* ({code})\n"
                message += f"  {current_price:,.0f}원 {arrow} {abs(change_rate):.2f}%\n"

                # 트리거 타입에 따른 추가 정보
                if "volume_increase" in stock and trigger_type.startswith("거래량"):
                    volume_increase = stock.get("volume_increase", 0)
                    message += f"  거래량 증가율: {volume_increase:.2f}%\n"

                elif "gap_rate" in stock and trigger_type.startswith("갭 상승"):
                    gap_rate = stock.get("gap_rate", 0)
                    message += f"  갭 상승률: {gap_rate:.2f}%\n"

                elif "trade_value_ratio" in stock and "시총 대비" in trigger_type:
                    trade_value_ratio = stock.get("trade_value_ratio", 0)
                    market_cap = stock.get("market_cap", 0) / 100000000  # 억원 단위로 변환
                    message += f"  거래대금/시총 비율: {trade_value_ratio:.2f}%\n"
                    message += f"  시가총액: {market_cap:.2f}억원\n"

                elif "closing_strength" in stock and "마감 강도" in trigger_type:
                    closing_strength = stock.get("closing_strength", 0) * 100
                    message += f"  마감 강도: {closing_strength:.2f}%\n"

                message += "\n"

        # 푸터 메시지
        message += "💡 상세 분석 보고서는 약 10-30분 내 제공 예정\n"
        message += "⚠️ 본 정보는 투자 참고용이며, 투자 결정과 책임은 투자자에게 있습니다."

        return message

    def _get_trigger_emoji(self, trigger_type):
        """
        트리거 유형에 맞는 이모지 반환
        """
        if "거래량" in trigger_type:
            return "📊"
        elif "갭 상승" in trigger_type:
            return "📈"
        elif "시총 대비" in trigger_type:
            return "💰"
        elif "상승률" in trigger_type:
            return "🚀"
        elif "마감 강도" in trigger_type:
            return "🔨"
        elif "횡보" in trigger_type:
            return "↔️"
        else:
            return "🔎"

    async def run_full_pipeline(self, mode):
        """
        전체 파이프라인 실행

        Args:
            mode (str): 'morning' 또는 'afternoon'
        """
        logger.info(f"전체 파이프라인 시작 - 모드: {mode}")

        try:
            # 0. 현재 보유 중인 포트폴리오 종목 갯수 확인
            import my_portfolio
            from stock_tracking_agent import StockTrackingAgent
            portfolio_count = await my_portfolio.get_portfolio_stock_count()
            logger.info(f"현재 보유 중인 포트폴리오 종목 갯수: {portfolio_count}/{StockTrackingAgent.MAX_SLOTS}")

            is_portfolio_full = portfolio_count >= StockTrackingAgent.MAX_SLOTS
            if is_portfolio_full:
                logger.warning("포트폴리오 종목 갯수가 최대치에 도달했습니다. 신규 종목 추가가 제한될 수 있습니다.")
            else:
                # 1. 트리거 배치 실행 - 비동기 방식으로 변경 (asyncio 리소스 관리 개선)
                results_file = f"trigger_results_{mode}_{datetime.now().strftime('%Y%m%d')}.json"
                # [{ 'code': '012345', 'name': '111' }, { 'code': '003720', 'name': '123' }]
                tickers = await self.run_trigger_batch(mode)

                try:
                    tickers_backup = tickers.copy()
                    # [{'ticker': '003720'}, {'ticker': '005930'}, {'ticker': '036570'}, {'ticker': '092200'}]
                    portfolio_tickers = await my_portfolio.get_portfolio_stock()

                    # 포트폴리오 종목은 제외
                    tickers = [t for t in tickers if t['code'] not in {p['ticker'] for p in portfolio_tickers}]
                except Exception as e:
                    logger.error(f"포트폴리오 종목 필터 중 오류: {str(e)}")
                    tickers = tickers_backup

                if not tickers:
                    logger.warning("선정된 종목이 없습니다. 프로세스 종료.")
                    return

                # 1-1. 트리거 결과를 텔레그램으로 즉시 전송
                if os.path.exists(results_file):
                    logger.info(f"트리거 결과 파일 확인됨: {results_file}")
                    alert_sent = await self.send_trigger_alert(mode, results_file)
                    if alert_sent:
                        logger.info("프리즘 시그널 얼럿 전송 완료")
                    else:
                        logger.warning("프리즘 시그널 얼럿 전송 실패")
                else:
                    logger.warning(f"트리거 결과 파일이 없습니다: {results_file}")

                # 2. 보고서 생성 - 중요: 여기에 await 추가!
                report_paths = await self.generate_reports(tickers, mode, timeout=600)
                if not report_paths:
                    logger.warning("생성된 보고서가 없습니다. 프로세스 종료.")
                    return

                # 3. PDF 변환
                pdf_paths = await self.convert_to_pdf(report_paths)

                # 4. 텔레그램 메시지 생성
                message_paths = await self.generate_telegram_messages(pdf_paths)

                # 5. 텔레그램 메시지 및 PDF 전송
                await self.send_telegram_messages(message_paths, pdf_paths)

            # 6. 트랙킹 시스템 배치
            if report_paths or is_portfolio_full:
                try:
                    logger.info("주식 트래킹 시스템 배치 실행 시작")

                    # 트래킹 에이전트 임포트
                    from stock_tracking_enhanced_agent import EnhancedStockTrackingAgent as StockTrackingAgent
                    from stock_tracking_agent import app as tracking_app

                    # 환경 변수에서 채널 ID 및 봇 토큰 가져오기
                    from dotenv import load_dotenv
                    load_dotenv()

                    chat_id = os.getenv("TELEGRAM_CHANNEL_ID")
                    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")

                    if not chat_id:
                        logger.error("텔레그램 채널 ID가 설정되지 않았습니다.")
                        return

                    if not telegram_token:
                        logger.warning("텔레그램 봇 토큰이 설정되지 않았습니다. 텔레그램 메시지 전송이 제한될 수 있습니다.")

                    # MCPApp 컨텍스트 매니저 사용
                    async with tracking_app.run():
                        # 텔레그램 토큰과 함께 에이전트 초기화
                        tracking_agent = StockTrackingAgent(telegram_token=telegram_token)

                        # 보고서 경로와 채널 ID 전달
                        tracking_success = await tracking_agent.run(report_paths, chat_id)

                        if tracking_success:
                            logger.info("트래킹 시스템 배치 실행 완료")
                        else:
                            logger.error("트래킹 시스템 배치 실행 실패")

                except Exception as e:
                    logger.error(f"트래킹 시스템 배치 실행 중 오류: {str(e)}")
                    import traceback
                    logger.error(traceback.format_exc())
            else:
                logger.warning("생성된 보고서가 없어 트래킹 시스템 배치를 실행하지 않습니다.")

            logger.info(f"전체 파이프라인 완료 - 모드: {mode}")

        except Exception as e:
            logger.error(f"파이프라인 실행 중 오류: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())

    async def generate_reports(self, tickers, mode, timeout: int = None) -> list:
        """
        모든 종목에 대해 보고서를 단순 직렬로 생성합니다.
        한 번에 하나의 종목만 처리하여 OpenAI rate limit 문제를 방지합니다.
        """

        logger.info(f"총 {len(tickers)}개 종목 보고서 생성 시작 (직렬 처리)")

        successful_reports = []

        # 각 종목에 대해 순차적으로 처리
        for idx, ticker_info in enumerate(tickers, 1):
            # ticker_info가 dict일 경우
            if isinstance(ticker_info, dict):
                ticker = ticker_info.get('code')
                company_name = ticker_info.get('name', f"종목_{ticker}")
            else:
                ticker = ticker_info
                company_name = f"종목_{ticker}"

            logger.info(f"[{idx}/{len(tickers)}] 종목 분석 시작: {company_name}({ticker})")

            # 출력 파일 경로 설정
            reference_date = datetime.now().strftime("%Y%m%d")
            output_file = str(REPORTS_DIR / f"{ticker}_{company_name}_{reference_date}_{mode}_gpt4.1.md")

            try:
                # main.py에서 직접 함수 임포트
                from cores.main import analyze_stock

                # 이미 비동기 환경이므로 직접 await 사용
                logger.info(f"[{idx}/{len(tickers)}] analyze_stock 함수 호출 시작")
                report = await analyze_stock(
                    company_code=ticker,
                    company_name=company_name,
                    reference_date=reference_date
                )

                # 결과 저장
                if report and len(report.strip()) > 0:
                    with open(output_file, "w", encoding="utf-8") as f:
                        f.write(report)
                    logger.info(f"[{idx}/{len(tickers)}] 보고서 생성 완료: {company_name}({ticker}) - {len(report)} 글자")
                    successful_reports.append(output_file)
                else:
                    logger.error(f"[{idx}/{len(tickers)}] 보고서 생성 실패: {company_name}({ticker}) - 내용이 비어 있음")

            except Exception as e:
                logger.error(f"[{idx}/{len(tickers)}] 분석 중 오류 발생: {company_name}({ticker}) - {str(e)}")
                import traceback
                logger.error(traceback.format_exc())


        logger.info(f"보고서 생성 완료: 총 {len(successful_reports)}/{len(tickers)}개 성공")

        return successful_reports

async def main():
    """
    메인 함수 - 명령줄 인터페이스
    """
    parser = argparse.ArgumentParser(description="주식 분석 및 텔레그램 전송 오케스트레이터")
    parser.add_argument("--mode", choices=["morning", "afternoon", "both"], default="both",
                        help="실행 모드 (morning, afternoon, both)")

    args = parser.parse_args()

    orchestrator = StockAnalysisOrchestrator()

    if args.mode == "morning" or args.mode == "both":
        await orchestrator.run_full_pipeline("morning")

    if args.mode == "afternoon" or args.mode == "both":
        await orchestrator.run_full_pipeline("afternoon")

if __name__ == "__main__":
    # 휴일 체크
    from check_market_day import is_market_day

    if not is_market_day():
        current_date = datetime.now().date()  # datetime.now()를 사용
        logger.info(f"오늘({current_date})은 주식시장 휴일입니다. 배치 작업을 실행하지 않습니다.")
        sys.exit(0)

    # 영업일인 경우에만 타이머 스레드 시작 및 메인 함수 실행
    import threading

    # 120분 후에 프로세스를 종료하는 타이머 함수
    def exit_after_timeout():
        import time
        import os
        import signal
        time.sleep(7200)  # 120분 대기
        logger.warning("120분 타임아웃 도달: 프로세스 강제 종료")
        os.kill(os.getpid(), signal.SIGTERM)

    # 백그라운드 스레드로 타이머 시작
    timer_thread = threading.Thread(target=exit_after_timeout, daemon=True)
    timer_thread.start()

    asyncio.run(main())