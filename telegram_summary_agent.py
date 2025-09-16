import asyncio
import re
import os
import json
import logging
from datetime import datetime
from pathlib import Path

from mcp_agent.agents.agent import Agent
from mcp_agent.app import MCPApp
from mcp_agent.workflows.llm.augmented_llm import RequestParams
from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM
from mcp_agent.workflows.evaluator_optimizer.evaluator_optimizer import (
    EvaluatorOptimizerLLM,
    QualityRating,
)

# 로깅 설정
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# MCPApp 인스턴스 생성
app = MCPApp(name="telegram_summary")


class TelegramSummaryGenerator:
    """
    보고서 파일을 읽어 텔레그램 메시지 요약을 생성하는 클래스
    """

    def __init__(self):
        """생성자"""
        pass

    async def read_report(self, report_path):
        """
        보고서 파일 읽기
        """
        try:
            with open(report_path, "r", encoding="utf-8") as file:
                content = file.read()
            return content
        except Exception as e:
            logger.error(f"보고서 파일 읽기 실패: {e}")
            raise

    def extract_metadata_from_filename(self, filename):
        """
        파일 이름에서 종목코드, 종목명, 날짜 등을 추출
        """
        pattern = r"(\w+)_(.+)_(\d{8})_.*\.pdf"
        match = re.match(pattern, filename)

        if match:
            stock_code = match.group(1)
            stock_name = match.group(2)
            date_str = match.group(3)

            # YYYYMMDD 형식을 YYYY.MM.DD 형식으로 변환
            formatted_date = f"{date_str[:4]}.{date_str[4:6]}.{date_str[6:8]}"

            return {
                "stock_code": stock_code,
                "stock_name": stock_name,
                "date": formatted_date,
            }
        else:
            # 파일명에서 정보를 추출할 수 없는 경우, 기본값 설정
            return {
                "stock_code": "N/A",
                "stock_name": Path(filename).stem,
                "date": datetime.now().strftime("%Y.%m.%d"),
            }

    def determine_trigger_type(self, stock_code: str, report_date=None):
        """
        트리거 결과 파일에서 해당 종목의 트리거 유형을 결정

        Args:
            stock_code: 종목 코드
            report_date: 보고서 날짜 (YYYYMMDD)

        Returns:
            tuple: (트리거 유형, 트리거 모드)
        """
        logger.info(f"종목 {stock_code}의 트리거 유형 결정 시작")

        # 날짜가 주어지지 않으면 현재 날짜 사용
        if report_date is None:
            report_date = datetime.now().strftime("%Y%m%d")
        elif report_date and "." in report_date:
            # YYYY.MM.DD 형식을 YYYYMMDD로 변환
            report_date = report_date.replace(".", "")

        # 가능한 모드 (morning, afternoon)
        for mode in ["morning", "afternoon"]:
            # 트리거 결과 파일 경로
            results_file = f"trigger_results_{mode}_{report_date}.json"

            logger.info(f"트리거 결과 파일 확인: {results_file}")

            if os.path.exists(results_file):
                try:
                    with open(results_file, "r", encoding="utf-8") as f:
                        results = json.load(f)

                    # free와 premium 계정 모두 확인
                    for account_type in ["free", "premium"]:
                        account_results = results.get(account_type, {})

                        # 각 트리거 유형 확인
                        for trigger_type, stocks in account_results.items():
                            for stock in stocks:
                                if stock.get("code") == stock_code:
                                    logger.info(
                                        f"종목 {stock_code}의 트리거 유형: {trigger_type}, 모드: {mode}"
                                    )
                                    return trigger_type, mode
                except Exception as e:
                    logger.error(f"트리거 결과 파일 읽기 오류: {e}")

        # 트리거 유형을 찾지 못한 경우 기본값 반환
        logger.warning(
            f"종목 {stock_code}의 트리거 유형을 결과 파일에서 찾지 못함, 기본값 사용"
        )

        # 기본 트리거 유형과 모드 (이전 방식 유지)
        return "주목할 패턴", "unknown"

    def create_optimizer_agent(self, metadata, current_date):
        """
        텔레그램 요약 생성 에이전트 생성
        """
        warning_message = ""
        if metadata.get("trigger_mode") == "morning":
            warning_message = '메시지 중간에 "⚠️ 주의: 본 정보는 장 시작 후 10분 시점 데이터 기준으로, 현재 시장 상황과 차이가 있을 수 있습니다." 문구를 반드시 포함해 주세요.'

        return Agent(
            name="telegram_summary_optimizer",
            instruction=f"""당신은 주식 정보 요약 전문가입니다. 
                        상세한 주식 분석 보고서를 읽고, 일반 투자자를 위한 가치 있는 텔레그램 메시지로 요약해야 합니다.
                        메시지는 핵심 정보와 통찰력을 포함해야 하며, 아래 형식을 따라야 합니다:
                        
                        1. 이모지와 함께 트리거 유형 표시 (📊, 📈, 💰 등 적절한 이모지)
                        2. 종목명(코드) 정보 및 간략한 사업 설명 (1-2문장)
                        3. 핵심 거래 정보 - 현재 날짜({current_date}) 기준으로 통일하여 작성하고, 
                            get_stock_ohlcv tool을 사용하여 현재 날짜({current_date})로부터 
                            약 5일간의 데이터를 조회해서 메모리에 저장한 뒤 참고하여 작성합니다.:
                           - 현재가
                           - 전일 대비 등락률
                           - 최근 거래량 (전일 대비 증감 퍼센트 포함)
                        4. 시가총액 정보 및 동종 업계 내 위치 (시가총액은 get_stock_market_cap tool 사용해서 현재 날짜({current_date})로부터 약 5일간의 데이터를 조회해서 참고)
                        5. 가장 관련 있는 최근 뉴스 1개와 잠재적 영향 (출처 링크 반드시 포함)
                        6. 핵심 기술적 패턴 2-3개 (지지선/저항선 수치 포함)
                        7. 투자 관점 - 단기/중기 전망 또는 주요 체크포인트
                        
                        전체 메시지는 400자 내외로 작성하세요. 투자자가 즉시 활용할 수 있는 실질적인 정보에 집중하세요.
                        수치는 가능한 구체적으로 표현하고, 주관적 투자 조언이나 '추천'이라는 단어는 사용하지 마세요.
                        
                        {warning_message}
                        
                        메시지 끝에는 "본 정보는 투자 참고용이며, 투자 결정과 책임은 투자자에게 있습니다." 문구를 반드시 포함하세요.
                        
                        ##주의사항 : load_all_tickers tool은 절대 사용 금지!!
                        """,
            server_names=["kospi_kosdaq"],
        )

    def create_evaluator_agent(self, current_date):
        """
        텔레그램 요약 평가 에이전트 생성
        """
        return Agent(
            name="telegram_summary_evaluator",
            instruction=f"""당신은 주식 정보 요약 메시지를 평가하는 전문가입니다.
                        주식 분석 보고서와 생성된 텔레그램 메시지를 비교하여 다음 기준에 따라 평가해야 합니다:
                        
                        1. 정확성: 메시지가 보고서의 사실을 정확하게 반영하는가? 할루시네이션이나 오류가 없는가?
                        (이 때, 거래 정보 검증은 get_stock_ohlcv tool을 사용하여 현재 날짜({current_date})로부터 약 5일간의 데이터를 조회해서 검증 진행함.)
                        또한, 시가총액은 get_stock_market_cap tool을 사용해서 마찬가지로 현재 날짜({current_date})로부터 약 5일간의 데이터를 조회해서 검증 진행.)
                        
                        2. 포맷 준수: 지정된 형식(이모지, 종목 정보, 거래 정보 등)을 올바르게 따르고 있는가?
                        3. 명확성: 정보가 명확하고 이해하기 쉽게 전달되는가?
                        4. 관련성: 가장 중요하고 관련성 높은 정보를 포함하고 있는가?
                        5. 경고 문구: 트리거 모드에 따른 경고 문구를 적절히 포함하고 있는가?
                        6. 길이: 메시지 길이가 400자 내외로 적절한가?

                        각 기준에 대해:
                        - EXCELLENT, GOOD, FAIR, POOR 중 하나의 등급을 매기세요.
                        - 구체적인 피드백과 개선 제안을 제공하세요.
                        
                        최종 평가는 다음 구조로 제공하세요:
                        - 전체 품질 등급
                        - 각 기준별 세부 평가
                        - 개선을 위한 구체적인 제안
                        - 특히 할루시네이션이 있다면 명확하게 지적
                        
                        ##주의사항 : load_all_tickers tool은 절대 사용 금지!!
                        """,
            server_names=["kospi_kosdaq"],
        )

    async def generate_telegram_message(self, report_content, metadata, trigger_type):
        """
        텔레그램 메시지 생성 (평가 및 최적화 기능 추가)
        """
        return f"""📊 {metadata["stock_name"]}({metadata["stock_code"]}) - 분석 요약은 진행하지 않음."""

        # 현재 날짜 설정 (YYYY.MM.DD 형식)
        current_date = datetime.now().strftime("%Y.%m.%d")

        # 최적화 에이전트 생성
        optimizer = self.create_optimizer_agent(metadata, current_date)

        # 평가 에이전트 생성
        evaluator = self.create_evaluator_agent(current_date)

        # 평가-최적화 워크플로우 설정
        evaluator_optimizer = EvaluatorOptimizerLLM(
            optimizer=optimizer,
            evaluator=evaluator,
            llm_factory=OpenAIAugmentedLLM,
            min_rating=QualityRating.EXCELLENT,
        )

        # 메시지 프롬프트 구성
        prompt_message = f"""다음은 {metadata["stock_name"]}({metadata["stock_code"]}) 종목에 대한 상세 분석 보고서입니다. 
            이 종목은 {trigger_type} 트리거에 포착되었습니다. 
            
            보고서 내용:
            {report_content}
            """

        # 트리거 모드가 morning인 경우 경고 문구 추가
        if metadata.get("trigger_mode") == "morning":
            logger.info("장 시작 후 10분 시점 데이터 경고 문구 추가")
            prompt_message += "\n이 종목은 장 시작 후 10분 시점에 포착되었으며, 현재 상황과 차이가 있을 수 있습니다."

        # 평가-최적화 워크플로우를 사용하여 텔레그램 메시지 생성
        response = await evaluator_optimizer.generate_str(
            message=prompt_message,
            request_params=RequestParams(
                model="gpt-4.1", maxTokens=6000, max_iterations=2
            ),
        )

        # 응답 처리 - 개선된 방식
        logger.info(f"응답 유형: {type(response)}")

        # 응답이 문자열인 경우 (가장 이상적인 케이스)
        if isinstance(response, str):
            logger.info("응답이 문자열 형식입니다.")
            # 이미 메시지 형식인지 확인
            if response.startswith(("📊", "📈", "📉", "💰", "⚠️", "🔍")):
                return response

            # 파이썬 객체 표현 찾아서 제거
            cleaned_response = re.sub(r"[A-Za-z]+\([^)]*\)", "", response)

            # 실제 메시지 내용만 추출 시도
            emoji_start = re.search(r"(📊|📈|📉|💰|⚠️|🔍)", cleaned_response)
            message_end = re.search(
                r"본 정보는 투자 참고용이며, 투자 결정과 책임은 투자자에게 있습니다\.",
                cleaned_response,
            )

            if emoji_start and message_end:
                return cleaned_response[emoji_start.start() : message_end.end()]

        # OpenAI API의 응답 객체인 경우 (content 속성이 있음)
        if hasattr(response, "content") and response.content is not None:
            logger.info("응답에 content 속성이 있습니다.")
            return response.content

        # ChatCompletionMessage 케이스 - tool_calls가 있는 경우
        if hasattr(response, "tool_calls") and response.tool_calls:
            logger.info("응답에 tool_calls가 있습니다.")

            # tool_calls 정보는 무시하고, function_call 결과가 있으면 그것을 반환
            if hasattr(response, "function_call") and response.function_call:
                logger.info("응답에 function_call 결과가 있습니다.")
                return f"함수 호출 결과: {response.function_call}"

            # 이 부분에서는 후속 처리를 위해 텍스트 형식의 응답만 생성
            # 실제 tool_calls 처리는 별도 로직으로 구현 필요
            return (
                "도구 호출 결과에서 텍스트를 추출할 수 없습니다. 관리자에게 문의하세요."
            )

        # 마지막 시도: 문자열로 변환하고 정규식으로 메시지 형식 추출
        response_str = str(response)
        logger.debug(f"정규식 적용 전 응답 문자열: {response_str[:100]}...")

        # 정규식으로 텔레그램 메시지 형식 추출 시도
        content_match = re.search(
            r"(📊|📈|📉|💰|⚠️|🔍).*?본 정보는 투자 참고용이며, 투자 결정과 책임은 투자자에게 있습니다\.",
            response_str,
            re.DOTALL,
        )

        if content_match:
            logger.info("정규식으로 메시지 내용을 추출했습니다.")
            return content_match.group(0)

        # 정규식으로도 찾지 못한 경우, 기본 메시지 반환
        logger.warning("응답에서 유효한 텔레그램 메시지를 추출할 수 없습니다.")
        logger.warning(
            f"정규식으로 추출하지 못한 원본 메시지 : {response_str[:100]}..."
        )

        # 기본 메시지 생성
        default_message = f"""📊 {metadata["stock_name"]}({metadata["stock_code"]}) - 분석 요약
        
    1. 현재 주가: (정보 없음)
    2. 최근 동향: (정보 없음)
    3. 주요 체크포인트: 상세 분석 보고서를 참고하세요.
    
    ⚠️ 자동 생성 메시지 오류로 인해 상세 정보를 표시할 수 없습니다. 전체 보고서를 확인해 주세요.
    본 정보는 투자 참고용이며, 투자 결정과 책임은 투자자에게 있습니다."""

        return default_message

    def save_telegram_message(self, message, output_path):
        """
        생성된 텔레그램 메시지를 파일로 저장
        """
        try:
            # 디렉토리가 없으면 생성
            os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

            with open(output_path, "w", encoding="utf-8") as file:
                file.write(message)
            logger.info(f"텔레그램 메시지가 {output_path}에 저장되었습니다.")
        except Exception as e:
            logger.error(f"텔레그램 메시지 저장 실패: {e}")
            raise

    async def process_report(self, report_pdf_path, output_dir="telegram_messages"):
        """
        보고서 파일을 처리하여 텔레그램 요약 메시지 생성
        """
        try:
            # 출력 디렉토리 생성
            os.makedirs(output_dir, exist_ok=True)

            # 파일 이름에서 메타데이터 추출
            filename = os.path.basename(report_pdf_path)
            metadata = self.extract_metadata_from_filename(filename)

            logger.info(
                f"처리 중: {filename} - {metadata['stock_name']}({metadata['stock_code']})"
            )

            # 보고서 내용 읽기
            from pdf_converter import pdf_to_markdown_text

            report_content = pdf_to_markdown_text(report_pdf_path)

            # 트리거 유형과 모드 결정
            trigger_type, trigger_mode = self.determine_trigger_type(
                metadata["stock_code"],
                metadata.get("date", "").replace(".", ""),  # YYYY.MM.DD → YYYYMMDD
            )
            logger.info(f"감지된 트리거 유형: {trigger_type}, 모드: {trigger_mode}")

            # 메타데이터에 트리거 모드 추가
            metadata["trigger_mode"] = trigger_mode

            # 텔레그램 요약 메시지 생성
            telegram_message = await self.generate_telegram_message(
                report_content, metadata, trigger_type
            )

            # 출력 파일 경로 생성
            output_file = os.path.join(
                output_dir,
                f"{metadata['stock_code']}_{metadata['stock_name']}_telegram.txt",
            )

            # 메시지 저장
            self.save_telegram_message(telegram_message, output_file)

            logger.info(f"텔레그램 메시지 생성 완료: {output_file}")

            return telegram_message

        except Exception as e:
            logger.error(f"보고서 처리 중 오류 발생: {e}")
            raise


async def process_all_reports(
    reports_dir="pdf_reports", output_dir="telegram_messages", date_filter=None
):
    """
    지정된 디렉토리 내의 모든 보고서 파일을 처리
    """
    # 텔레그램 요약 생성기 초기화
    generator = TelegramSummaryGenerator()

    # PDF 보고서 디렉토리 확인
    reports_path = Path(reports_dir)
    if not reports_path.exists() or not reports_path.is_dir():
        logger.error(f"보고서 디렉토리가 존재하지 않습니다: {reports_dir}")
        return

    # 보고서 파일 찾기
    report_files = list(reports_path.glob("*.md"))

    # 날짜 필터 적용
    if date_filter:
        report_files = [f for f in report_files if date_filter in f.name]

    if not report_files:
        logger.warning(
            f"처리할 보고서 파일이 없습니다. 디렉토리: {reports_dir}, 필터: {date_filter or '없음'}"
        )
        return

    logger.info(f"{len(report_files)}개의 보고서 파일을 처리합니다.")

    # 각 보고서 처리
    for report_file in report_files:
        try:
            await generator.process_report(str(report_file), output_dir)
        except Exception as e:
            logger.error(f"{report_file.name} 처리 중 오류 발생: {e}")

    logger.info("모든 보고서 처리가 완료되었습니다.")


async def main():
    """
    메인 함수
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="보고서 디렉토리의 모든 파일을 텔레그램 메시지로 요약합니다."
    )
    parser.add_argument(
        "--reports-dir", default="reports", help="보고서 파일이 저장된 디렉토리 경로"
    )
    parser.add_argument(
        "--output-dir",
        default="telegram_messages",
        help="텔레그램 메시지 저장 디렉토리 경로",
    )
    parser.add_argument("--date", help="특정 날짜의 보고서만 처리 (YYYYMMDD 형식)")
    parser.add_argument(
        "--today", action="store_true", help="오늘 날짜의 보고서만 처리"
    )
    parser.add_argument("--report", help="특정 보고서 파일만 처리")

    args = parser.parse_args()

    async with app.run() as parallel_app:
        logger = parallel_app.logger

        # 특정 보고서만 처리
        if args.report:
            report_pdf_path = args.report
            if not os.path.exists(report_pdf_path):
                logger.error(
                    f"지정된 보고서 파일이 존재하지 않습니다: {report_pdf_path}"
                )
                return

            generator = TelegramSummaryGenerator()
            telegram_message = await generator.process_report(
                report_pdf_path, args.output_dir
            )

            # 생성된 메시지 출력
            print("\n생성된 텔레그램 메시지:")
            print("-" * 50)
            print(telegram_message)
            print("-" * 50)

        else:
            # 오늘 날짜 필터 적용
            date_filter = None
            if args.today:
                date_filter = datetime.now().strftime("%Y%m%d")
            elif args.date:
                date_filter = args.date

            # 모든 pdf 보고서 처리
            await process_all_reports(
                reports_dir=args.reports_dir,
                output_dir=args.output_dir,
                date_filter=date_filter,
            )


if __name__ == "__main__":
    asyncio.run(main())
