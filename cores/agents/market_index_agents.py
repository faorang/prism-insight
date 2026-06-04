from mcp_agent.agents.agent import Agent


def create_market_index_analysis_agent(reference_date, max_years_ago, max_years, language: str = "ko", prefetched_kospi: str = None, prefetched_kosdaq: str = None):
    """Create market index analysis agent

    Args:
        reference_date: Analysis reference date (YYYYMMDD)
        max_years_ago: Analysis start date (YYYYMMDD)
        max_years: Analysis period (years)
        language: Language code ("ko" or "en")
        prefetched_kospi: Pre-collected KOSPI index data (optional)
        prefetched_kosdaq: Pre-collected KOSDAQ index data (optional)

    Returns:
        Agent: Market index analysis agent
    """

    instruction = f"""## 역할: 한국 주식 시장 전문 애널리스트
        목표: KOSPI/KOSDAQ 지수 및 거시경제 데이터를 분석하여 종합 시장 동향/투자 전략 보고서 작성.

        ## 데이터 수집 (필수)
        1. KOSPI 지수 데이터: kospi_kosdaq-get_index_ohlcv ({max_years_ago}~{reference_date}, ticker: "1001", {max_years}년, 일봉)
        2. KOSDAQ 지수 데이터: kospi_kosdaq-get_index_ohlcv ({max_years_ago}~{reference_date}, ticker: "2001", {max_years}년, 일봉)
        3. 거시경제/글로벌: perplexity_ask로 "KOSPI KOSDAQ {reference_date[:4]}년 {reference_date[4:6]}월 {reference_date[6:]}일 시장 변동 요인, 한국 거시경제 동향, 주요국 경제지표 영향 종합분석" 1회 검색

        ## 도구 사용 제한
        - kospi_kosdaq 도구는 get_index_ohlcv만 사용. load_all_tickers 절대 금지.
        - 개별 종목 정보 검색 금지. 시장/지수 정보만 확인.

        ## 분석 방향
        1. 당일 시장 변동 요인 (최우선): {reference_date} 기준 지수 변동 직접적 원인, 이슈, 거래량
        2. 거시경제/글로벌: 경제지표 현황, 정책 변화, 원자재 가격 영향
        3. 추세 및 모멘텀: 단/중/장기 이동평균선, RSI, MACD, 거래량
        4. 기술적 레벨: 지지/저항선, 패턴, 시장 사이클, KOSPI/KOSDAQ 상대 강도
        5. 투자 전략: Risk-On/Off 판단, 투자 적기 여부(현금 보유 비율 등)

        ## 출력 형식 (엄격 준수)
        - 시작: 개행문자 2번(\\n\\n) 후 "### 4. 시장 분석"
        - 섹션 1: "#### 당일 시장 변동 요인 분석" (필수)
        - 기타 소제목: "#### 소제목명" 형식 (마크다운 #### 필수)
        - 핵심 지표는 표(Table)로, 중요 정보는 **굵은 글씨** 강조
        - 명사형 종결(~함, ~임) 및 음슴체 사용. (부연 설명, 수식어, 인사말 등 불필요한 텍스트 극도 생략)
        - 투자/현금 비중 조절에 대한 명확한 타이밍 의견 제시
        - 현재 시장 리스크 레벨 (Low/Medium/High) 명시
        - 향후 1~3개월 내 핵심 관전 포인트 제시
        - 할루시네이션 방지: 실제 수집 데이터만 기반으로 객관적 서술 (출처 표시 예: [1])
        - 도구 사용 안내(Calling tool 등), 인사말, 의도표현 금지. 즉시 본문 시작.
        - 데이터 부족 시 "~에 대한 데이터 불충분" 명시.

        기준일: {reference_date}"""

    # Inject prefetched index data if available
    if prefetched_kospi and prefetched_kosdaq:
        prefetched_index_block = f"{prefetched_kospi}\n\n{prefetched_kosdaq}"
        instruction = instruction.replace(
            f"1. KOSPI 지수 데이터: kospi_kosdaq-get_index_ohlcv ({max_years_ago}~{reference_date}, ticker: \"1001\", {max_years}년, 일봉)\n        2. KOSDAQ 지수 데이터: kospi_kosdaq-get_index_ohlcv ({max_years_ago}~{reference_date}, ticker: \"2001\", {max_years}년, 일봉)",
            f"## 사전 수집된 데이터 (시장 지수)\n다음 KOSPI, KOSDAQ 데이터가 사전 수집되었습니다. 이 데이터를 분석에 직접 사용하세요 (도구 호출 생략).\n\n{prefetched_index_block}"
        )
        # Update precautions
        instruction = instruction.replace("- 할루시네이션 방지: 실제 수집 데이터만 기반으로 객관적 서술", "- 할루시네이션 방지: 사전 수집 데이터 및 perplexity 검색 결과만 기반으로 객관적 서술")

    # When index data is prefetched, only need perplexity for market news
    if prefetched_kospi and prefetched_kosdaq:
        server_list = ["perplexity"]
    else:
        server_list = ["kospi_kosdaq", "perplexity"]

    return Agent(
        name="market_index_analysis_agent",
        instruction=instruction,
        server_names=server_list
    )
