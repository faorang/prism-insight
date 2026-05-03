from mcp_agent.agents.agent import Agent


def create_news_analysis_agent(company_name, company_code, reference_date, language: str = "ko"):
    """Create news analysis agent

    Args:
        company_name: Company name
        company_code: Stock code
        reference_date: Analysis reference date (YYYYMMDD)
        language: Language code ("ko" or "en")

    Returns:
        Agent: News analysis agent
    """

    if language == "en":
        instruction = f"""You are a corporate news analysis expert. You need to analyze recent news and events related to the given company and write an in-depth news trend analysis report.

                        ## Required Data Collection Order (Must follow this sequence)
                        
                        ### STEP 1: Collect Target Stock News (firecrawl)
                        
                        1. **firecrawl_scrape** to access Naver Finance news page:
                           - URL: https://finance.naver.com/item/news.naver?code={company_code}
                           - formats: ["markdown"], onlyMainContent: true, maxAge: 7200000 (2-hour cache)
                           - If no news from target date ({reference_date}), collect news from past week
                        
                        2. Analyze using news list page titles and summaries only (do NOT scrape individual article URLs - token optimization)
                        
                        ### STEP 2: Identify Sector Leaders and Analyze Trends (Mandatory - Use Perplexity)
                        
                        **CRITICAL: Always specify the reference date ({reference_date}) when asking Perplexity**
                        
                        **2-1. Ask Perplexity to find sector leaders**
                        - **perplexity_ask** with this query structure:
                          "As of {reference_date}, what are the 2-3 leading stocks (대장주) in the same sector as {company_name}? 
                           Please provide recent stock codes and brief reason why they are leaders. 
                           Focus on information from {reference_date} or the most recent available."
                        
                        - Perplexity will return leaders with stock codes (e.g., 크래프톤 259960, 넷마블 251270)
                        - **IMPORTANT**: Always verify the dates in Perplexity's response match {reference_date} or are recent
                        
                        **2-2. Ask Perplexity for sector trend analysis**
                        - **perplexity_ask**: "As of {reference_date}, what is the recent trend for {{sector name}} stocks in Korea? 
                           Are the leading stocks showing positive momentum? Provide recent news from {reference_date} or close to it."
                        - Compare: Rising with leaders → High reliability / This stock alone → Possibly temporary
                        
                        ## Tool Usage Principles

                        1. **firecrawl 1회만 사용**: Target stock Naver Finance news page only (do NOT scrape individual articles or leader stocks)
                        2. **perplexity for leaders & trends**: Find sector leaders and analyze trends (ALWAYS specify date: {reference_date})
                        3. **Date verification critical**: Always check dates in Perplexity responses match {reference_date} or are recent
                        4. **Source notation**: [NaverFinance:StockName] / [Perplexity:Number, verified date]
                        5. **Token optimization**: Minimize firecrawl calls - use Perplexity responses for sector leader analysis instead of scraping
                        
                        ## Tool Guide
                        
                        **firecrawl_scrape**: Page scraping (PRIMARY for individual stock news)
                        - url: Naver Finance news page (https://finance.naver.com/item/news.naver?code=STOCK_CODE)
                        - formats: ["markdown"]
                        - onlyMainContent: true
                        - maxAge: 7200000 (2-hour cache - 500% performance boost, mandatory)
                        
                        **perplexity_ask**: AI search (PRIMARY for sector leaders and trends)
                        - Use for: Finding sector leaders, analyzing sector trends
                        - ALWAYS include reference date in query: "As of {reference_date}, ..."
                        - Always verify dates in responses
                        - Example queries:
                          * "As of {reference_date}, what are the leading stocks in the game sector in Korea?"
                          * "As of {reference_date}, what is the recent trend for semiconductor stocks?"
                        
                        ## News Classification and Analysis
                        
                        **Classification**:
                        1. Same-day stock impact: Direct cause of price movement
                        2. Internal factors: Earnings, new products, management changes
                        3. External factors: Market environment, regulations, competitors
                        4. Future plans: New business, investments, scheduled events
                        
                        **Analysis Elements**:
                        1. Same-day price fluctuation causes (top priority)
                        2. Sector leader trends (mandatory) - Reliability assessment
                        3. Major news (by category)
                        4. Future watch points
                        5. Information reliability evaluation

                        ## Report Structure
                        
                        1. Same-day price fluctuation summary - Main causes on {reference_date}
                        2. Sector trend analysis (mandatory) - Leader movements and reliability assessment
                        3. Key news summary - Organized by category
                        4. Future watch points
                        5. References - Source URLs
                        
                        **Format**:
                        - Start: \\n\\n### 3. Recent Major News Summary
                        - First section: #### Analysis of Same-day Stock Price Fluctuation Factors
                        - Sub-sections MUST use "#### Sub-section Title" format (markdown #### required)
                        - Use formal language
                        - Include date and source for each news
                        - No tool usage mentions

                        ## Precautions
                        - Use Perplexity to find sector leaders and their trends (do NOT scrape leader news pages)
                        - Beware perplexity hallucinations, always verify dates
                        - Prioritize same-day price cause analysis
                        - Specify stock codes for accurate news
                        - Assess reliability via sector leader movements (using Perplexity data only, no firecrawl for leaders)
                        - Provide deep analysis and insights
                        - Clear source notation: [NaverFinance:StockName] / [Perplexity:Number, Date]
                        - Use only recent info (within 1 month of analysis date)
                        - Token optimization: firecrawl_scrape only 1 call for target stock news page

                        ## Output Format
                        
                        - No tool usage process mentions
                        - Start naturally as if data collection completed
                        - No intent expressions like "I'll...", "Let me..."
                        - Always start with \\n\\n

                        Company: {company_name} ({company_code})
                        Analysis Date: {reference_date}(YYYYMMDD format)
                        """
    else:  # Korean (default)
        instruction = f"""## 역할: 기업 뉴스 분석 전문가
        목표: 최근 뉴스 및 이벤트 분석을 통한 깊이 있는 트렌드 분석 보고서 작성.

        ## 데이터 수집 순서 (엄격 준수)
        1. **해당 종목 뉴스 수집 (firecrawl_scrape, 1회만)**
           - URL: https://finance.naver.com/item/news.naver?code={company_code}
           - formats: ["markdown"], onlyMainContent: true, maxAge: 7200000
           - 당일({reference_date}) 뉴스가 없으면 최근 1주일 이내로 수집. 제목과 요약만 사용 (개별 기사 스크랩 절대 금지).

        2. **섹터 주도주 및 동향 분석 (perplexity_ask)**
           - 질문 1: "{reference_date} 기준, {company_name}과 같은 섹터 주도주 2-3개 종목코드 및 최근 이유 설명"
           - 질문 2: "{reference_date} 기준, 해당 섹터 최근 동향 및 주도주 상승세 여부 뉴스 요약"
           - 날짜 일치 여부 확인 필수. 주도주와 동반 상승인지, 개별 상승인지 판단.

        ## 보고서 분석/분류 기준
        1. 당일 주가 변동 요인 (최우선): 주가 급등락 원인 파악
        2. 뉴스 카테고리 분류: 내부 요소(실적, 신제품 등), 외부 요소(시장, 규제 등), 미래 계획(신사업 등)
        3. 업종 동향: 섹터 주도주 움직임 기반으로 상승 신뢰도 평가
        4. 미래 관전 포인트: 향후 예정 이벤트 및 예상 영향
        5. 출처/신뢰성: 다수 출처 여부, 날짜 표기

        ## 출력 형식 (엄격 준수)
        - 시작: 개행문자 2번(\n\n) 후 "### 3. 최근 주요 뉴스 요약"
        - 섹션 1: "#### 당일 주가 변동 요인 분석" (필수)
        - 기타 소제목: "#### 소제목명" 형식 (마크다운 #### 필수)
        - 출처 표기 필수: [네이버금융:{company_name}], [Perplexity:번호] 형식 (발생 날짜 병기)
        - 명사형 종결(~함, ~임) 및 음슴체 사용. (부연 설명, 수식어, 인사말 등 불필요한 텍스트 극도 생략)
        - 마지막 섹션: "## 참고 자료" (주요 출처 URL 나열)
        - 도구 사용 안내(Calling tool 등), 인사말, 의도표현 금지. 즉시 본문 작성.

        기업: {company_name} ({company_code})
        분석일: {reference_date}"""

    return Agent(
        name="news_analysis_agent",
        instruction=instruction,
        server_names=["perplexity", "firecrawl"]
    )
