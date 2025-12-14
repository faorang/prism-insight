import tiktoken
from pdf_converter import pdf_to_markdown_text

def num_tokens_from_messages(messages, model="gpt-4"):
    """Chat Completion 메시지의 정확한 토큰 계산"""
    
    try:
        encoding = tiktoken.encoding_for_model(model)
    except KeyError:
        encoding = tiktoken.get_encoding("cl100k_base")
    
    # 모델별 토큰 설정
    if model in {
        "gpt-3.5-turbo-0613", "gpt-3.5-turbo-16k-0613",
        "gpt-4-0314", "gpt-4-32k-0314", 
        "gpt-4-0613", "gpt-4-32k-0613",
        "gpt-4", "gpt-4o", "gpt-4.1", "gpt-5"
    }:
        tokens_per_message = 3
        tokens_per_name = 1
    elif model == "gpt-3.5-turbo-0301":
        tokens_per_message = 4
        tokens_per_name = -1
    else:
        tokens_per_message = 3  # 기본값
        tokens_per_name = 1
    
    num_tokens = 0
    
    # 각 메시지 처리
    for message in messages:
        num_tokens += tokens_per_message  # 메시지 시작 토큰
        
        for key, value in message.items():
            num_tokens += len(encoding.encode(value))  # 내용 토큰
            if key == "name":
                num_tokens += tokens_per_name  # 이름 토큰
    
    num_tokens += 3  # 응답 준비 토큰 (<|start|>assistant<|message|>)
    
    return num_tokens

content = pdf_to_markdown_text("a.pdf")
print(len(content))
call_str = f'''
다음은 주식 종목에 대한 AI 분석 보고서입니다. 이 보고서를 기반으로 매매 시나리오를 생성해주세요.
                
                ### 분석 요구사항:
                1. 이 종목에 대한 매수 적정성을 평가해주세요 (1~10점).
                   - 9~10점: 매우 확실한 매수 기회 (명확한 근거 필요)
                   - 7~8점: 양호한 매수 기회
                   - 5~6점: 보통 (관망 권장)
                   - 1~4점: 매수 부적합
                
                2. 현재 포트폴리오 상황을 고려하여 진입 여부를 결정하세요 (진입/관망).
                   - 현재 보유 종목이 7개 이상인 경우 매수 점수 8점 이상인 경우만 '진입'
                   - 동일 산업군이 이미 많이 있다면 관망을 고려하세요
                   - 불확실성이 조금이라도 있다면 반드시 '관망'으로 결정하세요
                   - 거래대금 랭킹이 상승한 경우 매수 고려 요인으로 반영하세요
                   - 거래대금 랭킹이 30% 이상 상승했다면 매수 점수를 1점 상향 조정하세요
                   - 거래대금 랭킹이 30% 이상 하락했다면 매수 점수를 1점 하향 조정하세요
                
                3. 목표가, 손절가, 투자 기간, 투자 근거를 제시해주세요.
                4. 이 종목의 산업군(섹터)을 한 단어로 명시해주세요.
                5. 포트폴리오 맥락에서 이 종목을 선택하는 이유를 설명해주세요.
                
                ### 응답 형식:
                JSON 형식으로 다음과 같이 응답해주세요. 아래 양식을 정확히 지켜주세요. 단, 숫자형식에는 쉼표나 언더바같은 별도의 구분자가 없어야 합니다.:
                {{
                    "portfolio_analysis": "현재 포트폴리오 상황 요약 (슬랏 수, 산업군 분포 등)",
                    "buy_score": 1~10 사이의 점수,
                    "decision": "진입" 또는 "관망",
                    "target_price": 숫자 (원),
                    "stop_loss": 숫자 (원),
                    "investment_period": "단기" 또는 "중기" 또는 "장기",
                    "rationale": "핵심 투자 근거를 간략히 설명",
                    "sector": "이 종목의 산업군/섹터 이름",
                    "considerations": "포트폴리오 맥락에서 이 종목 선택의 이유"
                }}
                
                보고서 내용:
                {content}
'''
instruction="""당신은 신중하고 분석적인 주식 매매 시나리오 생성 전문가입니다.
            기본적으로는 가치투자 원칙을 따르되, 상승 모멘텀이 확인될 때는 보다 적극적으로 진입합니다.
            주식 분석 보고서를 읽고 매매 시나리오를 JSON 형식으로 생성해야 합니다.
            
            ### 중요: 현재 포트폴리오 분석
            먼저 현재 보유 중인 종목 현황을 확인하세요. stock_holdings 테이블에 있는 기존 포트폴리오 정보를 분석하고
            다음과 같은 사항을 고려하세요:
            
            1. 현재 보유 종목 수 (최대 10개 슬랏 제한)
            2. 산업군 분포 (특정 산업군에 과다 노출되어 있는지 확인)
            3. 투자 기간 분포 (단기/중기/장기 투자 비율)
            4. 현재 포트폴리오의 평균 수익률
            
            ### 종목 평가 및 결정
            위 포트폴리오 상황을 고려하여 신규 종목에 대해 다음을 평가하세요:
            
            1. 종목에 대한 매수 적정성을 평가하세요 (1~10점).
               - 8~10점: 우수한 매수 기회 (확실한 근거 + 모멘텀)
               - 7점: 양호한 매수 기회 (기본 조건 만족)
               - 6점: 보통 (모멘텀이 있다면 고려)
               - 5점 이하: 매수 부적합
            
            2. 현재 진입 여부를 결정하세요 (진입/관망).
               - "진입" 결정은 매우 신중하게 내려야 합니다
               - 다음과 같은 경우 반드시 "관망"으로 결정하세요:
                 * 현재 보유 종목이 7개 이상인 경우 매수 점수 8점 미만
                 * 현재 보유 종목과 동일 산업군이 이미 2개 이상 있는 경우
                 * 재무상태에 의구심이 있는 경우
                 * 명확한 성장 동력이 확인되지 않는 경우
            
            3. 목표가를 제시해주세요 (현실적이고 합리적인 수준).
            4. 손절매 가격을 제시해주세요 (최대 허용 손실 기준).
            5. 투자 기간을 제안해주세요 (단기: 1개월 이내, 중기: 1~3개월, 장기: 3개월 이상).
               - 현재 포트폴리오의 투자 기간 분포를 고려하여 균형을 맞추세요
            6. 핵심 투자 근거를 3줄 이내로 요약해주세요.
            7. 이 종목의 산업군(섹터)을 한 단어로 명시해주세요(예: 반도체, 자동차, 바이오, 소프트웨어 등).
            
            ### 종목 적합성 판단
            다음 질문들을 고려하여 매수 적정성을 판단하세요:
            - 이 종목은 현재 주가 대비 상승여력이 충분한가?
            - 이 종목의 재무 상태는 건전한가?
            - 이 종목의 성장 가능성은 명확한가?
            - 이 종목은 현재 거래활동 증가나 관심 상승을 보이는가? (신규 추가)
            - 이 종목은 현재 포트폴리오와 적절한 분산효과를 제공하는가?
            
            **모멘텀 요소 특별 고려사항:**
            - 최근 거래량이 평소보다 크게 증가한 경우: 긍정적 요소로 고려
            - 투자자별 거래량에서 기관/외인 매수 우위: 긍정적 신호
            - 개인 투자자 대비 기관 투자자 순매수 증가: 신뢰도 높은 신호
            - 이러한 모멘텀 신호들이 확인될 때는 기존 매수 기준을 다소 완화하여 적용
            
            분석 보고서의 '투자 전략 및 의견' 섹션에 특히 주목하세요.
            주가, 목표가 및 손절가 정보는 보고서의 기술적 분석 부분에서 찾을 수 있습니다.
            모멘텀 요소인 거래량, 투자자별 거래량은 too call(name : kospi_kosdaq-get_stock_ohlcv, kospi_kosdaq-get_stock_trading_volume)을 사용하여 조회할 수 있습니다. 
            tool call 시 보고서 최상단의 '발행일: '이라고 표기된 날짜를 기준으로 적절한 범위의 데이터를 조회하면 됩니다.
            
            ### 응답 형식
            JSON 형식으로 다음과 같이 응답해주세요:
            {
                "portfolio_analysis": "현재 포트폴리오 상황 요약 (슬랏 수, 산업군 분포 등)",
                "buy_score": 1~10 사이의 점수,
                "decision": "진입" 또는 "관망",
                "target_price": 숫자 (원),
                "stop_loss": 숫자 (원),
                "investment_period": "단기" 또는 "중기" 또는 "장기",
                "rationale": "핵심 투자 근거를 간략히 설명",
                "sector": "이 종목의 산업군/섹터 이름",
                "considerations": "포트폴리오 맥락에서 이 종목 선택의 이유"
            }
            """
messages = [
    {"role": "system", "content": f"{instruction}"},
    {"role": "user", "content": f"{call_str}"},
]

token = num_tokens_from_messages(messages, model="gpt-4.1")
print('gpt-4.1 token:', token)
token = num_tokens_from_messages(messages, model="gpt-5")
print('gpt-5 token:', token)