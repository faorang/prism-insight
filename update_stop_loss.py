from datetime import datetime, timedelta
import pandas as pd


def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    ATR(평균 진폭 범위) 계산

    Parameters
    ----------
    df : pd.DataFrame
        '고가', '저가', '종가' 컬럼 포함
    period : int
        ATR 계산 기간 (기본 14일)

    Returns
    -------
    atr : pd.Series
        ATR 시리즈
    """
    high = df["고가"]
    low = df["저가"]
    close = df["종가"]

    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()

    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.ewm(span=period, adjust=False).mean()
    return atr


def atr_trailing_stop_ratchet(
    ticker: str, k: float = 3.0, atr_period: int = 20
) -> pd.Series:
    """
    출구 래칫이 적용된 ATR 트레일링 스탑 계산
    ATR: 일정 기간 (보통 14일) 동안 TR의 이동평균으로 계산

    Parameters
    ----------
    ticker : str
        종목 코드
    k : float
        ATR 배수 (기본값 3.0)
    atr_period : int
        ATR 계산 기간 (기본 14)

    Returns
    -------
    trailing_stop : pd.Series
        손절선 시리즈 (종가와 동일 인덱스)
    """
    try:
        # 데이터 가져오기
        today = datetime.now()
        start_date = (today - timedelta(days=atr_period)).strftime("%Y%m%d")
        end_date = today.strftime("%Y%m%d")

        from pykrx.stock import stock_api

        df = stock_api.get_market_ohlcv_by_date(start_date, end_date, ticker)

        if df.empty or len(df) < 14:  # 최소 데이터 부족
            print(f"Not enough data to calculate ATR trailing stop for {ticker}")
            return 0  # 중립 (데이터 없음)

        atr = calculate_atr(df, period=atr_period)
        close = df["종가"]

        trailing_stop = pd.Series(index=df.index, dtype="float64")
        for i, idx in enumerate(df.index):
            atr_stop = close[idx] - k * atr[idx] if not pd.isna(atr[idx]) else np.nan

            if i == 0:
                trailing_stop[idx] = atr_stop
            else:
                prev_stop = trailing_stop.iloc[i - 1]
                if pd.isna(atr_stop):
                    trailing_stop[idx] = prev_stop
                else:
                    trailing_stop[idx] = max(prev_stop, atr_stop)

        return trailing_stop.iloc[-1]  # 마지막 값 반환
    except Exception as e:
        print(f"Error calculating ATR trailing stop for {ticker}: {e}")
        return 0  # 중립 (오류 발생 시)
