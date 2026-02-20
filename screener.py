import FinanceDataReader as fdr
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
import logging

def get_integrated_candidates_v2(market_type="KOSDAQ", top_n=3):
    # 1. 날짜 설정
    end_date = datetime.now()
    start_date = end_date - timedelta(days=40) # 지표 계산을 위해 넉넉히 40일

    logging.info(f"[{end_date.strftime('%Y-%m-%d')}] 데이터 분석 시작...")

    # 2. 시장 지수 데이터 및 수익률 계산 (RS 기준점)
    symbol = "^KQ11" if market_type == "KOSDAQ" else "^KS11"
    index_data = yf.download(symbol, start=start_date, end=end_date, progress=False)
    index_return = (index_data['Close'].iloc[-1] / index_data['Close'].iloc[0] - 1) * 100

    # 3. 전 종목 리스트 및 거래대금 상위 필터링 (FinanceDataReader 활용)
    df_krx = fdr.StockListing(market_type)

    # 거래대금 50억 이상
    df_krx = df_krx[df_krx['Amount'] >= 5e9]

    # 최근 거래대금이 높은 상위 50개 종목으로 후보 압축 (API 호출 최적화)
    # df_krx = df_krx.sort_values(by='Amount', ascending=False).head(50)
    # print(f"거래대금 상위 50개 종목으로 후보 압축 완료. 분석 중...")
    logging.info(f"거래대금 상위 50개 종목으로 후보 압축 완료. 분석 중...")
    logging.info(df_krx[['Code', 'Name', 'Amount']].head(5))  # 상위 5개 종목 미리보기

    final_list = []

    for _, row in df_krx.iterrows():
        ticker = row['Code']
        name = row['Name']
        # yfinance용 심볼 변환 (KOSDAQ: .KQ, KOSPI: .KS)
        yf_ticker = f"{ticker}.KQ" if market_type == "KOSDAQ" else f"{ticker}.KS"

        try:
            # 주가 데이터 다운로드
            data = yf.download(yf_ticker, start=start_date, end=end_date, progress=False)
            if len(data) < 25: continue
            logging.info(f"분석 중: {name} ({ticker}) - 데이터 포인트: {len(data)}")

            close = data['Close']

            # --- 지표 계산 ---
            # RS (상대적 강도): 종목 수익률 - 지수 수익률
            stock_return = (close.iloc[-1] / close.iloc[0] - 1) * 100
            rs_score = stock_return - index_return

            # 이평선
            ma5 = close.rolling(window=5).mean()
            ma20 = close.rolling(window=20).mean()

            # 볼린저 밴드 (20, 2)
            std = close.rolling(window=20).std()
            upper_band = ma20 + (std * 2)

            curr_price = close.iloc[-1]

            # --- 조건 검증 ---
            # 1. 지수보다 강함 (RS > 0)
            # 2. 이평선 정배열 (5 > 20)
            # 3. 볼린저 밴드 상단 근접 또는 돌파
            if rs_score > 0 and ma5.iloc[-1] > ma20.iloc[-1]:
                if curr_price >= upper_band.iloc[-1] * 0.97:
                    final_list.append({
                        'ticker': ticker,
                        '종목명': name,
                        'RS': rs_score,
                        '현재가': int(curr_price),
                        '수익률': round(stock_return, 2)
                    })
        except:
            continue

    # 4. 결과 정렬 및 상위 n개 반출
    result_df = pd.DataFrame(final_list)
    if not result_df.empty:
        return result_df.sort_values(by='RS', ascending=False).head(top_n)
    return "조건에 부합하는 종목이 없습니다."

import FinanceDataReader as fdr
import pandas as pd
from datetime import datetime, timedelta

def get_robust_candidates(market_type="KOSDAQ", top_n=3):
    # 1. 날짜 설정 (최근 20거래일 데이터 확보)
    end_date = datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')

    logging.info(f"[{end_date}] 분석 시작...")

    # 2. 지수 데이터 가져오기 (상대 강도 계산용)
    index_symbol = "KQ11" if market_type == "KOSDAQ" else "KS11"
    index_df = fdr.DataReader(index_symbol, start_date, end_date)
    index_return = (index_df['Close'].iloc[-1] / index_df['Close'].iloc[0] - 1)

    # 3. 전 종목 시세 데이터 한 번에 가져오기 (가장 안정적인 방식)
    df_list = fdr.StockListing(market_type)

    # [필터 1] 거래대금이 어느 정도 있는 종목 (유동성 필터 - 10억으로 완화)
    # FDR의 'Amount' 컬럼 활용
    df_list = df_list[df_list['Amount'] > 1_000_000_000]

    # 4. 상대적 강도(RS) 계산 (개별 종목 등락률 - 지수 등락률)
    # FDR 'ChangesRatio'는 당일 기준이므로, 최근 흐름을 위해 종가 데이터 활용 권장
    # 여기서는 빠른 처리를 위해 당일 등락률 기반으로 1차 정렬
    df_list['RS'] = df_list['ChangesRatio'] - (index_return * 100)

    # 5. 기술적 조건 적용 (조건이 너무 빡빡하면 제외되는 것을 방지)
    # - 가격이 5일 이평선 위에 있는가? (단기 추세)
    # - RS 점수가 높은가?

    candidates = df_list.sort_values(by='RS', ascending=False)

    # 6. 최종 후보 추출 (결과가 없으면 거래대금/RS 순으로 강제 추출)
    final_candidates = candidates.head(top_n)

    return final_candidates[['Code', 'Name', 'ChangesRatio', 'RS', 'Amount']]

def get_final_candidates(market_type="KOSDAQ", top_n=3):
    # 1. 날짜 설정
    end_date = datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.now() - timedelta(days=5)).strftime('%Y-%m-%d')

    logging.info(f"[{end_date}] 분석 엔진 가동...")

    # 2. 전 종목 리스트 및 당일 시세 가져오기
    df_list = fdr.StockListing(market_type)

    # 컬럼명 유연하게 대응 (ChangesRatio가 없으면 Rate 또는 계산으로 대체)
    if 'ChangesRatio' not in df_list.columns:
        if 'Rate' in df_list.columns:
            df_list['ChangesRatio'] = df_list['Rate']
        else:
            # 직접 계산: (종가 - 전일종가) / 전일종가 * 100
            df_list['ChangesRatio'] = (df_list['Changes'] / (df_list['Close'] - df_list['Changes'])) * 100

    # 3. 시장 지수 등락률 가져오기 (RS 계산용)
    index_symbol = "KQ11" if market_type == "KOSDAQ" else "KS11"
    index_df = fdr.DataReader(index_symbol, start_date, end_date)
    # 지수의 당일 등락률 (마지막 데이터 사용)
    market_return = ((index_df['Close'].iloc[-1] / index_df['Close'].iloc[-2]) - 1) * 100

    # 4. 필터링 로직
    # [필터 A] 유동성: 거래대금 상위 20% 이내 (시장에 소외된 종목 배제)
    # 'Amount' 컬럼은 보통 거래대금을 의미합니다.
    amount_threshold = df_list['Amount'].quantile(0.8)
    df_filtered = df_list[df_list['Amount'] >= amount_threshold].copy()

    # [필터 B] 상대적 강도(RS): 지수보다 수익률이 높은 종목
    df_filtered['RS'] = df_filtered['ChangesRatio'] - market_return

    # [필터 C] 기술적 위치: 당일 주가가 '고가' 근처에 있는지 (매수세 확인)
    # 고가와 저가의 중간값보다 현재가가 높으면 가산점
    df_filtered['Position'] = (df_filtered['Close'] - df_filtered['Low']) / (df_filtered['High'] - df_filtered['Low'] + 1e-5)

    # 5. 최종 점수 계산 및 정렬
    # RS가 높을수록, 고가권에서 마감할수록 점수 높음
    df_filtered['FinalScore'] = df_filtered['RS'] + (df_filtered['Position'] * 2)

    # 6. 결과 산출 (무조건 top_n개를 뽑도록 설계)
    candidates = df_filtered.sort_values(by='FinalScore', ascending=False).head(top_n)

    return candidates[['Code', 'Name', 'Close', 'ChangesRatio', 'RS', 'Amount']]

def get_technical_candidates(market_type="KOSDAQ", top_n=3):
    # 1. 날짜 설정 (기술적 지표 계산을 위해 충분한 데이터 확보)
    end_date = datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.now() - timedelta(days=60)).strftime('%Y-%m-%d')

    logging.info(f"[{end_date}] 기술적 지표 분석 엔진 가동 (BB/MA20)...")

    # 2. 전 종목 시세 가져오기 (1차 필터용)
    df_list = fdr.StockListing(market_type)

    # 3. 1차 필터링: 거래대금 상위 100개만 추출 (API 호출 최적화)
    # 거래대금이 너무 적은 종목은 기술적 분석의 신뢰도가 낮음
    df_top = df_list.sort_values(by='Amount', ascending=False).head(100).copy()

    final_candidates = []

    for _, row in df_top.iterrows():
        ticker = row['Code']
        name = row['Name']

        try:
            # 개별 종목의 60일치 주가 데이터 가져오기
            ohlcv = fdr.DataReader(ticker, start_date, end_date)
            if len(ohlcv) < 20: continue

            # --- 기술적 지표 계산 ---
            close = ohlcv['Close']

            # 20일 이동평균선
            ma20 = close.rolling(window=20).mean()

            # 볼린저 밴드 (20, 2)
            std = close.rolling(window=20).std()
            upper_band = ma20 + (std * 2)

            curr_price = close.iloc[-1]
            curr_ma20 = ma20.iloc[-1]
            curr_upper = upper_band.iloc[-1]

            # --- 조건 검증 ---
            # 조건 1: 주가가 20일 이평선 위에 있음 (중기 추세 생존)
            is_above_ma20 = curr_price > curr_ma20

            # 조건 2: 볼린저 밴드 상단 돌파 혹은 상단 3% 이내 접근 (강한 돌파 에너지)
            is_bb_breakout = curr_price >= curr_upper * 0.97

            if is_above_ma20 and is_bb_breakout:
                # 지수 대비 강도(RS) 계산 (지수 데이터는 코드 간소화를 위해 종목 수익률로 대체)
                stock_return = (curr_price / close.iloc[0] - 1) * 100

                final_candidates.append({
                    'Code': ticker,
                    'Name': name,
                    'Close': int(curr_price),
                    'MA20': round(curr_ma20, 0),
                    'UpperBB': round(curr_upper, 0),
                    'Return': round(stock_return, 2),
                    'Amount': row['Amount']
                })

        except Exception:
            continue

    # 4. 결과 정리
    if not final_candidates:
        logging.info("조건에 부합하는 종목이 없습니다. 거래대금 상위 종목으로 대체합니다.")
        # 만약 조건에 맞는 게 하나도 없다면, 거래대금 상위 3개라도 반환 (시스템 중단 방지)
        return df_top[['Code', 'Name', 'Close', 'Amount']].head(top_n)

    result_df = pd.DataFrame(final_candidates)
    # 수익률(Return)이 높은 순으로 정렬하여 상위 n개 반출
    return result_df.sort_values(by='Return', ascending=False).head(top_n)

def get_safe_changes_ratio(df):
    """ChangesRatio 컬럼을 안전하게 추출 및 수치화"""
    for col in ['ChangesRatio', 'Rate', 'ChgRate']:
        if col in df.columns:
            # 문자열일 경우를 대비해 숫자로 변환
            return pd.to_numeric(df[col], errors='coerce').fillna(0)

    if 'Changes' in df.columns and 'Close' in df.columns:
        # 전일 종가 계산 후 등락률 산출
        prev_close = df['Close'] - df['Changes']
        return (df['Changes'] / prev_close.replace(0, 1)) * 100 # 0으로 나누기 방지
    # 3. 최후의 수단: 0으로 채움 (오류 방지)
    return 0

def process_ticker(item, start_date, end_date):
    """개별 종목 기술적 지표 계산 (스레드용)"""
    ticker = item['Code']
    try:
        df = fdr.DataReader(ticker, start_date, end_date)
        if len(df) < 20: return None

        close = df['Close']
        high = df['High'].iloc[-1]
        low = df['Low'].iloc[-1]
        open_price = df['Open'].iloc[-1]
        curr_price = close.iloc[-1]
        ma20 = close.rolling(window=20).mean().iloc[-1]
        std = close.rolling(window=20).std().iloc[-1]
        upper_bb = ma20 + (std * 2)
        lower_limit = upper_bb * 0.90
        upper_limit = upper_bb * 1.01
        curr_price = close.iloc[-1]

        # --- [추가] 과열 방지 필터 ---
        # 1. 이격도 계산 (20일선 대비 너무 멀면 제외)
        disparity = (curr_price / ma20) * 100
        if disparity > 115: return None # 15% 이상 뜨면 "너무 비싸다"고 판단

        # --- [신규 필터 2] 윗꼬리 제한 (매물 저항 확인) ---
        # 몸통(종가-시가)보다 윗꼬리가 너무 길면 매도세가 강하다고 판단
        body_size = abs(curr_price - open_price)
        upper_shadow = high - max(open_price, curr_price)
        # 몸통이 거의 없는 도지형이거나 윗꼬리가 몸통의 60%를 넘으면 제외
        if body_size == 0 or upper_shadow > (body_size * 0.6): return None

        # 2. 당일 급등주 제외 (단타 방지)
        # 당일 10% 이상 오른 종목은 LLM이 분석하기엔 이미 변동성이 너무 큼
        if item['CalcRatio'] > 10: return None

        # --- [수정] 안정적 추세 조건 ---
        # 이평선보다는 위에 있고, 볼린저 밴드 상단보다는 살짝 아래인 '안정 구간'
        if curr_price > ma20 and (lower_limit <= curr_price <= upper_limit) and item['RS'] > 0:
            return {
                'Code': ticker,
                'Name': item['Name'],
                'Close': int(curr_price),
                'RS': item['RS'],  # 상위 함수에서 계산한 RS 값을 그대로 사용
                'ChangesRatio': round(item['CalcRatio'], 2),
                'Amount': item['Amount']
            }
    except:
        return None
    return None

def get_final_refined_candidates(market_type="KOSDAQ", top_n=3):
    end_date = datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.now() - timedelta(days=40)).strftime('%Y-%m-%d')

    # 1. 전 종목 리스트 확보
    df_list = fdr.StockListing(market_type)

    # 2. 등락률(ChangesRatio) 안전하게 확보
    df_list['CalcRatio'] = get_safe_changes_ratio(df_list)

    # 3. 1차 필터링 (거래대금 상위 50개)
    # 데이터가 아예 없는 경우를 대비해 Amount 컬럼 존재 여부 확인 후 정렬
    sort_col = 'Amount' if 'Amount' in df_list.columns else 'MarCap'
    df_filtered = df_list.sort_values(by=sort_col, ascending=False).head(50)

    # 4. 멀티스레딩 분석
    ticker_items = df_filtered.to_dict('records')
    logging.info(f"[{datetime.now().time()}] {len(ticker_items)}개 종목 기술적 분석 시작...")

    with ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(lambda x: process_ticker(x, start_date, end_date), ticker_items))

    # 결과 정리
    final_candidates = [r for r in results if r is not None]

    if not final_candidates:
        logging.info("조건 일치 종목 없음: 수급 상위주로 대체합니다.")
        return df_filtered[['Code', 'Name', 'Close', 'CalcRatio', sort_col]].head(top_n)

    result_df = pd.DataFrame(final_candidates)
    # 등락률과 수급이 조화로운 순서로 정렬
    return result_df.sort_values(by=['ChangesRatio', 'Amount'], ascending=False).head(top_n)

def get_expanded_candidates(market_type="KOSDAQ", top_n=3):
    # 1. 날짜 설정
    end_date = datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.now() - timedelta(days=40)).strftime('%Y-%m-%d')

    # 2. 전 종목 리스트 확보 및 지수 수익률 계산
    df_list = fdr.StockListing(market_type)

    # 지수 기표 가져오기 (RS 계산용)
    index_symbol = "KQ11" if market_type == "KOSDAQ" else "KS11"
    idx_df = fdr.DataReader(index_symbol, start_date, end_date)
    idx_return = (idx_df['Close'].iloc[-1] / idx_df['Close'].iloc[-2] - 1) * 100

    print(f"시장 지수({index_symbol}) 수익률: {round(idx_return, 2)}%")
    print(f"{idx_df['Close'].iloc[-1]}, {idx_df['Close'].iloc[-2]}")

    # 3. RS(상대적 강도) 미리 계산
    df_list['CalcRatio'] = get_safe_changes_ratio(df_list) # 이전 단계에서 만든 안전 함수
    df_list['RS'] = df_list['CalcRatio'] - idx_return


    # 4. 시가총액 1,000억 이상 & 주가 2,000원 이상인 종목만 먼저 선별
    # (FDR 데이터 기준 MarCap 단위가 다를 수 있으니 확인이 필요합니다)
    min_marcap = 100_000_000_000

    from trading.domestic_stock_trading import DEFAULT_BUY_AMOUNT
    max_price = DEFAULT_BUY_AMOUNT
    df_filtered = df_list[
        (df_list['Marcap'] >= min_marcap) &  # 예: 시총 1,000억 이상
        (df_list['Close'] >= 2000) &    # 주가 2,000원 이상
        (df_list['Close'] <= max_price)   # 주가 400,000원 이하
    ].copy()

    # 4. 그중에서 거래대금이 높은 순으로 300개 추출
    sort_col = 'Amount' if 'Amount' in df_filtered.columns else 'Marcap'
    df_expanded = df_filtered.sort_values(by=sort_col, ascending=False).head(300).copy()

    # 당일 등락이 양수인 것만 (필요시 제거 가능)
    df_pre_filtered = df_expanded[df_expanded['CalcRatio'] > 0]

    ticker_items = df_pre_filtered.to_dict('records')

    # 5. 멀티스레딩 분석
    logging.info(f"[{datetime.now().time()}] 300개 중 {len(ticker_items)}개 대상 정밀 분석...")
    with ThreadPoolExecutor(max_workers=20) as executor:
        results = list(executor.map(lambda x: process_ticker(x, start_date, end_date), ticker_items))

    final_candidates = [r for r in results if r is not None]

    # 6. 결과 반환 (KeyError 방지 로직)
    if final_candidates:
        result_df = pd.DataFrame(final_candidates)
        # 여기서 'RS' 키가 반드시 존재하므로 안전하게 정렬 가능
        return result_df.sort_values(by='RS', ascending=False).head(top_n)

    return pd.DataFrame() # 빈 데이터프레임 반환

def get_candidates(market_type="KOSPI", top_n=3):
    print(f"[{datetime.now().time()}] 분석 엔진 가동: 시장={market_type}, 후보 수={top_n}")
    cand = get_expanded_candidates(market_type, top_n)
    print(f"[{datetime.now().time()}] 최종 후보 선정 완료: {len(cand)}개")
    return cand

if __name__ == "__main__":
    # candidates= get_robust_candidates("KOSPI", 3)
    # candidates = get_integrated_candidates_v2("KOSPI", 3)
    # candidates = get_final_candidates("KOSPI", 1)
    candidates = get_candidates("KOSPI", 3)
    if candidates.empty:
        print("조건에 부합하는 종목이 없습니다.")
    else:
        my_screen_tickers = candidates.apply(lambda row: {'code': row['Code'], 'name': row['Name'],'RS': row['RS']}, axis=1).to_list()
        # 리스트 내 각 딕셔너리에 값 추가 (기존 리스트 업데이트)
        for item in my_screen_tickers:
            item['trigger_type'] = "My Custom Screen"
            item['trigger_mode'] = "mode"
            item['risk_reward_ratio'] = 0
        print(f"My screen tickers: {my_screen_tickers}")
        print("\n--- 선정된 매수 점수 분석 후보 ---")
        print(candidates.apply(lambda row: {'code': row['Code'], 'name': row['Name']}, axis=1).to_list())