#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from dotenv import load_dotenv
load_dotenv()  # Load environment variables from .env file (required before krx_data_client import)

import sys
import datetime
import time
import random
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
import pandas as pd
import numpy as np
import logging
from krx_data_client import (
    get_market_ohlcv_by_ticker,
    get_nearest_business_day_in_a_week,
    get_market_cap_by_ticker,
    get_market_ticker_name,
)

# pykrx compatibility wrapper (for existing code compatibility)
class stock_api:
    get_market_ohlcv_by_ticker = staticmethod(get_market_ohlcv_by_ticker)
    get_nearest_business_day_in_a_week = staticmethod(get_nearest_business_day_in_a_week)
    get_market_cap_by_ticker = staticmethod(get_market_cap_by_ticker)
    get_market_ticker_name = staticmethod(get_market_ticker_name)

# Logger configuration
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
ch = logging.StreamHandler()
formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
ch.setFormatter(formatter)
logger.addHandler(ch)


# --- Data collection and caching functions ---
def get_snapshot(trade_date: str) -> pd.DataFrame:
    """
    Return OHLCV snapshot for all stocks on specified trading date.
    Columns: "Open", "High", "Low", "Close", "Volume", "Amount"
    """
    logger.debug(f"get_snapshot called: {trade_date}")
    df = stock_api.get_market_ohlcv_by_ticker(trade_date)
    if df.empty:
        logger.error(f"No OHLCV data for {trade_date}.")
        raise ValueError(f"No OHLCV data for {trade_date}.")

    # Data verification
    logger.debug(f"Snapshot data sample: {df.head()}")
    logger.debug(f"Snapshot data columns: {df.columns}")

    return df

def get_previous_snapshot(trade_date: str) -> (pd.DataFrame, str):
    """
    Find the previous business day before specified trading date and return OHLCV snapshot with date.
    """
    # Convert to date object
    date_obj = datetime.datetime.strptime(trade_date, '%Y%m%d')

    # Move back one day
    prev_date_obj = date_obj - datetime.timedelta(days=1)

    # Convert to string for business day check
    prev_date_str = prev_date_obj.strftime('%Y%m%d')

    # Find previous business day
    prev_date = stock_api.get_nearest_business_day_in_a_week(prev_date_str, prev=True)

    logger.debug(f"Previous trading day check - Base date: {trade_date}, Day before: {prev_date_str}, Previous business day: {prev_date}")

    df = stock_api.get_market_ohlcv_by_ticker(prev_date)
    if df.empty:
        logger.error(f"No OHLCV data for {prev_date}.")
        raise ValueError(f"No OHLCV data for {prev_date}.")

    # Data verification
    logger.debug(f"Previous trading day data sample: {df.head()}")
    logger.debug(f"Previous trading day data columns: {df.columns}")

    return df, prev_date


@lru_cache(maxsize=512)
def get_multi_day_ohlcv(ticker: str, end_date: str, days: int = 10) -> pd.DataFrame:
    """
    Query N-day OHLCV data for specific stock.
    Uses FinanceDataReader and yfinance for optimization, falls back to krx_data_client.

    Args:
        ticker: Stock code
        end_date: End date (YYYYMMDD)
        days: Number of business days to query (default: 10 days)

    Returns:
        DataFrame with columns: Open, High, Low, Close, Volume, Amount
        Index: Date
    """
    # Calculate sufficient past date from end date (with margin for business days)
    end_dt = datetime.datetime.strptime(end_date, '%Y%m%d')
    start_dt = end_dt - datetime.timedelta(days=days * 2)  # 2x margin for business days
    
    # Date formats for FDR / yfinance
    start_date_str = start_dt.strftime('%Y-%m-%d')
    end_date_str = end_dt.strftime('%Y-%m-%d')

    # 1. Try FinanceDataReader (fastest and most reliable for Korean stocks)
    try:
        import FinanceDataReader as fdr
        df = fdr.DataReader(ticker, start_date_str, end_date_str)
        if not df.empty:
            # Clean index name and map columns to standard names
            df.index.name = 'Date'
            if 'Amount' not in df.columns and 'Close' in df.columns and 'Volume' in df.columns:
                df['Amount'] = df['Close'] * df['Volume']
            logger.debug(f"Successfully fetched {ticker} N-day data using FinanceDataReader")
            return df.tail(days).copy()
    except Exception as e:
        logger.debug(f"FinanceDataReader lookup failed for {ticker}: {e}")

    # 2. Try yfinance as a fallback
    try:
        import yfinance as yf
        # Try KOSPI (.KS) and KOSDAQ (.KQ)
        for suffix in [".KS", ".KQ"]:
            yf_ticker = f"{ticker}{suffix}"
            df = yf.download(yf_ticker, start=start_date_str, end=end_date_str, progress=False)
            if not df.empty:
                # Resolve multi-index columns if present
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.droplevel(1)
                df.index.name = 'Date'
                if 'Amount' not in df.columns and 'Close' in df.columns and 'Volume' in df.columns:
                    df['Amount'] = df['Close'] * df['Volume']
                logger.debug(f"Successfully fetched {ticker} N-day data using yfinance ({yf_ticker})")
                return df.tail(days).copy()
    except Exception as e:
        logger.debug(f"yfinance lookup failed for {ticker}: {e}")

    # 3. Fallback to legacy krx_data_client
    from krx_data_client import get_market_ohlcv_by_date
    start_date = start_dt.strftime('%Y%m%d')
    try:
        df = get_market_ohlcv_by_date(start_date, end_date, ticker)
        if df.empty:
            logger.warning(f"No {days}-day data for {ticker} using legacy client.")
            return pd.DataFrame()
        logger.debug(f"Successfully fetched {ticker} N-day data using legacy krx_data_client")
        return df.tail(days).copy()
    except Exception as e:
        logger.error(f"Legacy krx_data_client failed for {ticker}: {e}")
        return pd.DataFrame()


def calculate_rsi(ohlcv_df: pd.DataFrame, period: int = 14) -> float:
    """
    Calculate RSI for a given OHLCV DataFrame.
    """
    if len(ohlcv_df) < period + 1:
        return 50.0

    delta = ohlcv_df['Close'].diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)

    # Use exponential moving average (Wilder's style)
    ema_up = up.ewm(com=period - 1, adjust=False).mean()
    ema_down = down.ewm(com=period - 1, adjust=False).mean()

    rs = ema_up / ema_down.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.iloc[-1]


def apply_overheating_filter(candidates: pd.DataFrame, trade_date: str, snapshot: pd.DataFrame, threshold: float = 75.0, limit: int = 10) -> pd.DataFrame:
    """
    Filter out overheated stocks using RSI and return the best 'limit' candidates.
    Searches through candidates in safe parallel chunks (size 5) with random jitter to prevent API blocking.
    """
    if candidates.empty:
        return candidates

    # v2.1.0: 동적 RSI 과열 기준 적용 (강세장일 때는 85.0으로 완화)
    regime = determine_market_regime(trade_date)
    active_threshold = 85.0 if regime in ["strong_bull", "moderate_bull"] else threshold
    logger.info(f"Applying overheating filter (RSI threshold: {active_threshold:.1f}, market regime: {regime}) - Searching for top {limit} from {len(candidates)} candidates")
    
    tickers = list(candidates.index)
    final_indices = []

    def check_rsi_for_ticker(ticker):
        try:
            # API 분산 요청을 위한 미세한 무작위 지터 추가
            time.sleep(random.uniform(0.05, 0.25))

            hist = get_multi_day_ohlcv(ticker, trade_date, days=20)
            if hist.empty:
                return ticker, True, 50.0

            last_hist_date = hist.index[-1]
            last_hist_date_str = last_hist_date.strftime('%Y%m%d') if hasattr(last_hist_date, 'strftime') else str(last_hist_date).replace("-", "")[:8]

            if last_hist_date_str != trade_date:
                if ticker in snapshot.index:
                    today_data = snapshot.loc[[ticker]].copy()
                    if isinstance(hist.index, pd.DatetimeIndex):
                        today_data.index = [pd.to_datetime(trade_date)]
                    hist = pd.concat([hist, today_data])

            rsi_val = calculate_rsi(hist)
            if rsi_val >= active_threshold:
                return ticker, False, rsi_val
            return ticker, True, rsi_val
        except Exception as e:
            logger.warning(f"Failed to check RSI for {ticker}: {e}")
            return ticker, True, 50.0

    # API 과사용 방지를 위해 5개씩 청크 단위로 나누어 병렬 처리 진행
    chunk_size = 5
    for i in range(0, len(tickers), chunk_size):
        chunk_tickers = tickers[i:i + chunk_size]
        
        # 청크 단위로 5개 스레드로 동시 요청 제한
        with ThreadPoolExecutor(max_workers=len(chunk_tickers)) as executor:
            results = list(executor.map(check_rsi_for_ticker, chunk_tickers))

        # 청크 결과 적용
        for ticker, accepted, rsi_val in results:
            if accepted:
                final_indices.append(ticker)
            else:
                logger.info(f"Stock {ticker} skipped: Overheated (RSI: {rsi_val:.2f})")

        # 조기 종료 조건: 필요한 limit 수량을 채웠으면 다음 청크는 조회하지 않음
        if len(final_indices) >= limit:
            final_indices = final_indices[:limit]
            break

    return candidates.loc[final_indices]


def get_market_cap_df(trade_date: str, market: str = "ALL") -> pd.DataFrame:
    """
    Return market cap data for all stocks on specified trading date as DataFrame.
    Index is stock code, includes market cap column.
    """
    logger.debug(f"get_market_cap_df called: {trade_date}, market={market}")
    cap_df = stock_api.get_market_cap_by_ticker(trade_date, market=market)
    if cap_df.empty:
        logger.error(f"No market cap data for {trade_date}.")
        raise ValueError(f"No market cap data for {trade_date}.")
    return cap_df

def filter_low_liquidity(df: pd.DataFrame, threshold: float = 0.2) -> pd.DataFrame:
    """
    Filter out stocks in bottom N% by volume (low liquidity filtering)
    """
    volume_cutoff = np.percentile(df['Volume'], threshold * 100)
    return df[df['Volume'] > volume_cutoff]

def apply_absolute_filters(df: pd.DataFrame, min_value: int = 500000000) -> pd.DataFrame:
    """
    Absolute criteria filtering:
    - Minimum trade value (500M KRW or more)
    - Sufficient liquidity
    """
    # Minimum trade value filter (500M KRW or more)
    filtered_df = df[df['Amount'] >= min_value]

    # Volume filter: at least 20% of market average
    avg_volume = df['Volume'].mean()
    min_volume = avg_volume * 0.2
    filtered_df = filtered_df[filtered_df['Volume'] >= min_volume]

    return filtered_df

def normalize_and_score(df: pd.DataFrame, ratio_col: str, abs_col: str,
                        ratio_weight: float = 0.6, abs_weight: float = 0.4,
                        ascending: bool = False) -> pd.DataFrame:
    """
    Calculate composite score by normalizing columns and applying weights.

    ratio_col: Relative ratio column (e.g., volume ratio)
    abs_col: Absolute value column (e.g., volume)
    ratio_weight: Weight for relative ratio (default: 0.6)
    abs_weight: Weight for absolute value (default: 0.4)
    ascending: Sort direction (default: False, descending)
    """
    if df.empty:
        return df

    # Calculate max/min values for normalization
    ratio_max = df[ratio_col].max()
    ratio_min = df[ratio_col].min()
    abs_max = df[abs_col].max()
    abs_min = df[abs_col].min()

    # Prevent division by zero
    ratio_range = ratio_max - ratio_min if ratio_max > ratio_min else 1
    abs_range = abs_max - abs_min if abs_max > abs_min else 1

    # Normalize each column (to 0-1 range)
    df[f"{ratio_col}_norm"] = (df[ratio_col] - ratio_min) / ratio_range
    df[f"{abs_col}_norm"] = (df[abs_col] - abs_min) / abs_range

    # Calculate composite score
    df["composite_score"] = (df[f"{ratio_col}_norm"] * ratio_weight) + (df[f"{abs_col}_norm"] * abs_weight)

    # Sort by composite score
    return df.sort_values("composite_score", ascending=ascending)

def enhance_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add additional information like stock name, sector to DataFrame
    """
    if not df.empty:
        df = df.copy()  # Explicitly create copy to prevent SettingWithCopyWarning
        df["stock_name"] = df.index.map(lambda ticker: stock_api.get_market_ticker_name(ticker))
    return df


# v1.16.6: Agent criteria by trigger type (synchronized with trading_agents.py)
TRIGGER_CRITERIA = {
    "거래량 급증 상위주": {"rr_target": 1.2, "sl_max": 0.05},
    "갭 상승 모멘텀 상위주": {"rr_target": 1.2, "sl_max": 0.05},
    "시총 대비 집중 자금 유입 상위주": {"rr_target": 1.3, "sl_max": 0.05},
    "20일 신고가 눌림목 첫 양봉": {"rr_target": 1.2, "sl_max": 0.05},
    "default": {"rr_target": 1.5, "sl_max": 0.07}
}

# v2.0.0: Market regime criteria (synchronized with trading_agents.py)
REGIME_CRITERIA = {
    "strong_bull":   {"rr_target": 1.0, "sl_max": 0.07},
    "moderate_bull": {"rr_target": 1.2, "sl_max": 0.07},
    "sideways":      {"rr_target": 1.3, "sl_max": 0.06},
    "moderate_bear": {"rr_target": 1.5, "sl_max": 0.05},
    "strong_bear":   {"rr_target": 1.8, "sl_max": 0.05},
}


@lru_cache(maxsize=128)
def determine_market_regime(trade_date: str) -> str:
    """
    Determine KOSPI market regime based on KOSPI 20-day SMA and 2-week return.
    Calculations align with the logic defined in cores/agents/trading_agents.py.
    """
    try:
        import FinanceDataReader as fdr
        import datetime

        end_dt = datetime.datetime.strptime(trade_date, "%Y%m%d")
        start_dt = end_dt - datetime.timedelta(days=50)  # Enough room for 30 business days

        # Fetch KOSPI index data (KS11)
        df = fdr.DataReader("KS11", start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d"))
        if df.empty or len(df) < 20:
            logger.warning("Insufficient KOSPI data to determine regime. Defaulting to sideways.")
            return "sideways"

        close = df['Close']
        current_val = float(close.iloc[-1])
        ma20 = float(close.rolling(window=20).mean().iloc[-1])

        # 2-week return (approx 10 business days)
        idx_prev = max(0, len(close) - 11)
        prev_val = float(close.iloc[idx_prev])
        return_2w = ((current_val / prev_val) - 1) * 100

        logger.info(f"Regime Check - Current KOSPI: {current_val:.2f}, 20-day SMA: {ma20:.2f}, 2-week return: {return_2w:.2f}%")

        if abs(current_val - ma20) / ma20 <= 0.005:
            return "sideways"
        elif current_val > ma20:
            if return_2w >= 5.0:
                return "strong_bull"
            else:
                return "moderate_bull"
        else: # current_val < ma20
            if return_2w <= -5.0:
                return "strong_bear"
            else:
                return "moderate_bear"

    except Exception as e:
        logger.error(f"Error determining market regime: {e}")
        return "sideways"


def calculate_agent_fit_metrics(ticker: str, current_price: float, trade_date: str, lookback_days: int = 10, trigger_type: str = None) -> dict:
    """
    Calculate metrics that fit buy/sell agent criteria.

    v1.16.6: Changed to fixed stop-loss method (15% annual return system)
    - Core change: 10-day support level based → current price based fixed stop-loss
    - Reason: Improved to allow surge stocks to meet agent criteria
    - Risk-reward ratio: Maintain resistance level based, guarantee minimum +15%

    v2.0.0: Modified to adapt dynamically to market regime.

    Args:
        ticker: Stock code
        current_price: Current price
        trade_date: Reference trading date
        lookback_days: Number of past business days to query (forced to 20 for pivot calculations)
        trigger_type: Trigger type (used for differentiated criteria)

    Returns:
        dict with keys: stop_loss_price, target_price, stop_loss_pct, risk_reward_ratio, agent_fit_score, pivot_point
    """
    result = {
        "stop_loss_price": 0,
        "target_price": 0,
        "stop_loss_pct": 1.0,  # Default: unfavorable value
        "risk_reward_ratio": 0,
        "agent_fit_score": 0,
        "pivot_point": 0.0,
        "trade_value": 0.0,
        "volume_profile_info": "No significant upper resistance",
    }

    if current_price <= 0:
        return result

    # v2.0.0: Query KOSPI regime and dynamically adjust limits
    regime = determine_market_regime(trade_date)
    regime_criteria = REGIME_CRITERIA.get(regime, REGIME_CRITERIA["sideways"])
    
    # Retrieve trigger-specific default criteria as fallback / combination
    trigger_criteria = TRIGGER_CRITERIA.get(trigger_type, TRIGGER_CRITERIA["default"])
    
    # Select the more conservative parameter to satisfy both trigger intent and LLM regime rules
    sl_max = min(regime_criteria["sl_max"], trigger_criteria["sl_max"])
    rr_target = max(regime_criteria["rr_target"], trigger_criteria["rr_target"])
    
    logger.info(f"{ticker}: Active regime is {regime}. Dynamic limits: sl_max={sl_max*100:.1f}%, rr_target={rr_target:.2f}")

    # Apply stop-loss calculation
    stop_loss_price = current_price * (1 - sl_max)
    stop_loss_pct = sl_max  # Fixed value (5% or 7%)

    # Query 60 days of data for Volume Profile and Pivot Point calculations
    multi_day_df = get_multi_day_ohlcv(ticker, trade_date, 60)
    
    # 1. Pivot Point & Moving Average calculation based on trigger type
    pivot_point = 0.0
    is_pivot_valid = False
    is_ma_valid = False
    ma_value = 0.0

    lookback = 20
    use_close_for_pivot = False

    if trigger_type == "갭 상승 모멘텀 상위주":
        lookback = 20
        use_close_for_pivot = True
    elif trigger_type in ["거래량 급증 상위주", "시총 대비 집중 자금 유입 상위주"]:
        lookback = 10
        use_close_for_pivot = False
    elif trigger_type == "20일 신고가 눌림목 첫 양봉":
        lookback = 20
        use_close_for_pivot = False
    else:
        lookback = 20
        use_close_for_pivot = False

    # Extract historical df for pivot calculation
    pivot_df = multi_day_df.tail(lookback) if not multi_day_df.empty else pd.DataFrame()
    if not pivot_df.empty and len(pivot_df) >= 2:
        high_col = "High" if "High" in pivot_df.columns else "고가"
        close_col = "Close" if "Close" in pivot_df.columns else "종가"
        
        last_date_str = pivot_df.index[-1].strftime('%Y%m%d') if hasattr(pivot_df.index[-1], 'strftime') else str(pivot_df.index[-1]).replace("-", "")[:8]
        if last_date_str == trade_date:
            past_df = pivot_df.iloc[:-1]
        else:
            past_df = pivot_df

        if not past_df.empty:
            target_col = close_col if use_close_for_pivot else high_col
            if target_col in past_df.columns:
                pivot_point = float(past_df[target_col].max())
            else:
                pivot_point = float(current_price)
        else:
            pivot_point = float(current_price)
    else:
        pivot_point = float(current_price)

    # Pivot Point breakout range check (v2.2.0: align pivot buffer dynamically with market regime)
    is_bull = regime in ["strong_bull", "moderate_bull"]
    pivot_buffer_pct = 15.0 if is_bull else 8.0
    pivot_multiplier = 1.0 + (pivot_buffer_pct / 100.0)

    if pivot_point and pivot_point > 0:
        if trigger_type == "20일 신고가 눌림목 첫 양봉":
            # 눌림목은 20일 최고점 대비 일정 범위 아래에 위치하므로 하방 버퍼를 12%까지 넓게 둠
            if pivot_point * 0.88 <= current_price <= pivot_point * pivot_multiplier:
                is_pivot_valid = True
        else:
            if pivot_point <= current_price <= pivot_point * pivot_multiplier:
                is_pivot_valid = True

    # 2. Moving Average (MA) filter: 5-day SMA check (excluding today)
    ma_df = multi_day_df.tail(6) if not multi_day_df.empty else pd.DataFrame()
    if not ma_df.empty and len(ma_df) >= 2:
        close_col = "Close" if "Close" in ma_df.columns else "종가"
        if close_col in ma_df.columns:
            last_date_str = ma_df.index[-1].strftime('%Y%m%d') if hasattr(ma_df.index[-1], 'strftime') else str(ma_df.index[-1]).replace("-", "")[:8]
            if last_date_str == trade_date:
                past_ma_df = ma_df.iloc[:-1].tail(5)
            else:
                past_ma_df = ma_df.tail(5)
            if not past_ma_df.empty:
                ma_value = float(past_ma_df[close_col].mean())
                if current_price >= ma_value:
                    is_ma_valid = True
            else:
                is_ma_valid = True
        else:
            is_ma_valid = True
    else:
        is_ma_valid = True

    # 3. Absolute trading value filter (minimum 5 billion KRW)
    is_value_valid = False
    trade_value = 0.0
    if not multi_day_df.empty:
        amount_col = "Amount" if "Amount" in multi_day_df.columns else "거래대금"
        close_col = "Close" if "Close" in multi_day_df.columns else "종가"
        volume_col = "Volume" if "Volume" in multi_day_df.columns else "거래량"
        
        if amount_col in multi_day_df.columns:
            trade_value = float(multi_day_df.iloc[-1][amount_col])
        elif close_col in multi_day_df.columns and volume_col in multi_day_df.columns:
            trade_value = float(multi_day_df.iloc[-1][close_col] * multi_day_df.iloc[-1][volume_col])
            
        if trade_value >= 5_000_000_000: # 5 Billion KRW
            is_value_valid = True

    # 3. Volume Profile & Dynamic Target Price (60-day base)
    target_price = current_price * 1.15 # Default
    has_volume_profile = False
    volume_profile_info = "No significant upper resistance"
    
    if not multi_day_df.empty and len(multi_day_df) >= 5:
        close_col = "Close" if "Close" in multi_day_df.columns else "종가"
        volume_col = "Volume" if "Volume" in multi_day_df.columns else "거래량"
        
        if close_col in multi_day_df.columns and volume_col in multi_day_df.columns:
            clean_df = multi_day_df[(multi_day_df[close_col] > 0) & (multi_day_df[volume_col] > 0)].copy()
            if len(clean_df) >= 5:
                min_p = float(clean_df[close_col].min())
                max_p = float(clean_df[close_col].max())
                
                if min_p < max_p:
                    num_bins = 10
                    bins = np.linspace(min_p, max_p, num_bins + 1)
                    
                    # Cut data into bins
                    clean_df["bin"] = pd.cut(clean_df[close_col], bins=bins, labels=False, include_lowest=True)
                    
                    # Sum volumes by bin
                    bin_volumes = clean_df.groupby("bin", observed=False)[volume_col].sum().to_dict()
                    total_volume = clean_df[volume_col].sum()
                    
                    # Find High Volume Nodes (HVNs) - sorted by volume descending
                    sorted_bins = sorted(bin_volumes.items(), key=lambda x: x[1], reverse=True)
                    
                    # Find the first high volume bin that is above current_price
                    hvn_target_bin = None
                    for bin_idx, vol in sorted_bins:
                        bin_min, bin_max = bins[int(bin_idx)], bins[int(bin_idx)+1]
                        if bin_min >= current_price:
                            hvn_target_bin = int(bin_idx)
                            break
                    
                    if hvn_target_bin is not None:
                        # Dynamic target is the lower bound of the first high volume resistance bin
                        target_price = float(bins[hvn_target_bin])
                        has_volume_profile = True
                        
                        vol_ratio = (bin_volumes[hvn_target_bin] / total_volume) * 100 if total_volume > 0 else 0
                        volume_profile_info = f"1st Major Resistance: {bins[hvn_target_bin]:,.0f} ~ {bins[hvn_target_bin+1]:,.0f} KRW (Vol share: {vol_ratio:.1f}%)"

    # Guarantee minimum +15% target
    min_target = current_price * 1.15
    if target_price <= current_price:
        target_price = min_target
    elif target_price < min_target:
        target_price = min_target

    # Calculate risk-reward ratio
    potential_gain = target_price - current_price
    potential_loss = current_price - stop_loss_price

    if potential_loss > 0 and potential_gain > 0:
        risk_reward_ratio = potential_gain / potential_loss
    else:
        risk_reward_ratio = 0

    # 4. Expectation Risk/Reward Ratio validation
    is_rr_valid = False
    if risk_reward_ratio >= rr_target:
        is_rr_valid = True

    # Calculate agent fit score (risk-reward 60%, stop-loss 40%)
    rr_score = min(risk_reward_ratio / rr_target, 1.0) if risk_reward_ratio > 0 else 0
    sl_score = 1.0  # Always perfect score since stop-loss is fixed
    agent_fit_score = rr_score * 0.6 + sl_score * 0.4

    # Enforce strict hybrid rule criteria
    is_qualified = is_pivot_valid and is_value_valid and is_rr_valid and is_ma_valid
    if not is_qualified:
        rejections = []
        if not is_pivot_valid:
            rejections.append(f"Pivot Rule Failed (Pivot: {pivot_point:.0f}, Current: {current_price:.0f})")
        if not is_value_valid:
            rejections.append(f"Trading Value Rule Failed (Value: {trade_value/1e9:.1f}B < 5.0B)")
        if not is_rr_valid:
            rejections.append(f"Risk/Reward Rule Failed (Ratio: {risk_reward_ratio:.2f} < {rr_target:.2f})")
        if not is_ma_valid:
            rejections.append(f"MA Rule Failed (MA5: {ma_value:.0f}, Current: {current_price:.0f})")
            
        logger.info(f"{ticker}: Filtering out. Reasons: {', '.join(rejections)}")
        agent_fit_score = 0.0

    result = {
        "stop_loss_price": stop_loss_price,
        "target_price": target_price,
        "stop_loss_pct": stop_loss_pct,
        "risk_reward_ratio": risk_reward_ratio,
        "agent_fit_score": agent_fit_score,
        "pivot_point": pivot_point,
        "trade_value": trade_value,
        "volume_profile_info": volume_profile_info,
    }

    logger.debug(f"{ticker}: Stop-loss={stop_loss_price:.0f}, Target={target_price:.0f}, "
                 f"Stop-loss%={stop_loss_pct*100:.1f}% (fixed), Risk-reward={risk_reward_ratio:.2f}, "
                 f"Agent score={agent_fit_score:.3f}, Pivot={pivot_point:.0f}, Value={trade_value/1e9:.1f}B")

    return result


def score_candidates_by_agent_criteria(candidates_df: pd.DataFrame, trade_date: str, lookback_days: int = 10, trigger_type: str = None) -> pd.DataFrame:
    """
    Calculate agent criteria scores for candidate stocks and add to DataFrame.

    v1.16.6: Apply differentiated criteria by trigger type
    """
    if candidates_df.empty:
        return candidates_df

    result_df = candidates_df.copy()

    # Initialize agent-related columns
    result_df["stop_loss_price"] = 0.0
    result_df["target_price"] = 0.0
    result_df["stop_loss_pct"] = 0.0
    result_df["risk_reward_ratio"] = 0.0
    result_df["agent_fit_score"] = 0.0
    result_df["pivot_point"] = 0.0
    result_df["trade_value"] = 0.0
    result_df["volume_profile_info"] = ""

    def fetch_metrics(ticker):
        # API 요청이 동시에 뭉치지 않도록 미세한 지터 추가
        time.sleep(random.uniform(0.05, 0.25))
        current_price = result_df.loc[ticker, "Close"]
        metrics = calculate_agent_fit_metrics(ticker, current_price, trade_date, lookback_days, trigger_type)
        return ticker, metrics

    # 통과된 소수 종목(통상 10개 미만)에 한해 병렬 처리 (최대 3개 스레드로 보수적으로 제어)
    tickers = list(result_df.index)
    with ThreadPoolExecutor(max_workers=min(len(tickers), 3)) as executor:
        results = list(executor.map(fetch_metrics, tickers))

    for ticker, metrics in results:
        result_df.loc[ticker, "stop_loss_price"] = metrics["stop_loss_price"]
        result_df.loc[ticker, "target_price"] = metrics["target_price"]
        result_df.loc[ticker, "stop_loss_pct"] = metrics["stop_loss_pct"]
        result_df.loc[ticker, "risk_reward_ratio"] = metrics["risk_reward_ratio"]
        result_df.loc[ticker, "agent_fit_score"] = metrics["agent_fit_score"]
        result_df.loc[ticker, "pivot_point"] = metrics["pivot_point"]
        result_df.loc[ticker, "trade_value"] = metrics["trade_value"]
        result_df.loc[ticker, "volume_profile_info"] = metrics["volume_profile_info"]

    return result_df


# --- Morning trigger functions (based on market open snapshot) ---
def trigger_morning_volume_surge(trade_date: str, snapshot: pd.DataFrame, prev_snapshot: pd.DataFrame, cap_df: pd.DataFrame = None, top_n: int = 10) -> pd.DataFrame:
    """
    [Morning Trigger 1] Top stocks with intraday volume surge
    - Absolute criteria: Minimum trade value 500M KRW + at least 20% of market average volume
    - Additional filter: Volume increase of 30% or more
    - Composite score: Volume increase rate (60%) + Absolute volume (40%)
    - Secondary filtering: Select only rising stocks (current price > opening price)
    - Penny stock filter: Market cap 50B KRW or more
    """
    logger.debug("trigger_morning_volume_surge started")
    common = snapshot.index.intersection(prev_snapshot.index)
    snap = snapshot.loc[common].copy()
    prev = prev_snapshot.loc[common].copy()

    # Merge and filter market cap data (v1.16.6: adjusted to 500B or more)
    if cap_df is not None and not cap_df.empty:
        snap = snap.merge(cap_df[["시가총액"]], left_index=True, right_index=True, how="inner")
        # Select stocks with market cap 500B KRW or more (v1.16.6: expanded opportunity pool, 518 stocks)
        snap = snap[snap["시가총액"] >= 500000000000]
        logger.debug(f"Stock count after market cap filtering: {len(snap)}")
        if snap.empty:
            logger.warning("No stocks after market cap filtering")
            return pd.DataFrame()

    # Debug information
    logger.debug(f"Previous day close data sample: {prev['Close'].head()}")
    logger.debug(f"Current day close data sample: {snap['Close'].head()}")

    # Apply absolute criteria (raised to 10B KRW trade value)
    snap = apply_absolute_filters(snap, min_value=10000000000)

    # Calculate volume ratio
    snap["volume_ratio"] = snap["Volume"] / prev["Volume"].replace(0, np.nan)
    # Calculate volume increase rate (percentage)
    snap["volume_increase_rate"] = (snap["volume_ratio"] - 1) * 100

    # Calculate two types of change rates
    snap["intraday_change_rate"] = (snap["Close"] / snap["Open"] - 1) * 100  # Current vs opening price

    # Calculate change rate vs previous day - modified method
    snap["prev_day_change_rate"] = ((snap["Close"] - prev["Close"]) / prev["Close"]) * 100

    # v1.16.6: Change rate upper limit (20% or less, surge stocks can enter with fixed stop-loss)
    snap = snap[snap["prev_day_change_rate"] <= 20.0]

    # Debug calculation process for first 5 stocks' change rate vs previous day
    for ticker in snap.index[:5]:
        try:
            today_close = snap.loc[ticker, "Close"]
            yesterday_close = prev.loc[ticker, "Close"]
            change_rate = ((today_close - yesterday_close) / yesterday_close) * 100
            logger.debug(f"Stock {ticker} - Today close: {today_close}, Yesterday close: {yesterday_close}, Change rate: {change_rate:.2f}%")
        except Exception as e:
            logger.debug(f"Error during debugging: {e}")

    snap["is_rising"] = snap["Close"] > snap["Open"]

    # Filter for volume increase rate 30% or more
    snap = snap[snap["volume_increase_rate"] >= 30.0]

    if snap.empty:
        logger.debug("trigger_morning_volume_surge: No stocks with volume increase")
        return pd.DataFrame()

    # Primary filtering: Select top stocks by composite score
    scored = normalize_and_score(snap, "volume_increase_rate", "Volume", 0.6, 0.4)
    # Secondary filtering: Select only rising stocks (Full pool for RSI search)
    rising_candidates = snap[snap["is_rising"] == True].copy()
    if rising_candidates.empty:
        return pd.DataFrame()

    # Apply overheating filter (Search for top 10 non-overheated stocks from the ranked pool)
    filtered_result = apply_overheating_filter(
        scored.loc[scored.index.intersection(rising_candidates.index)], 
        trade_date, snap, limit=10
    )
    
    if filtered_result.empty:
        logger.debug("trigger_morning_volume_surge: No stocks meeting criteria after RSI filter")
        return pd.DataFrame()

    return enhance_dataframe(filtered_result)

def trigger_morning_gap_up_momentum(trade_date: str, snapshot: pd.DataFrame, prev_snapshot: pd.DataFrame, cap_df: pd.DataFrame = None, top_n: int = 15) -> pd.DataFrame:
    """
    [Morning Trigger 2] Top gap-up momentum stocks
    - Absolute criteria: Minimum trade value 500M KRW or more
    - Composite score: Gap rate (50%) + Intraday rise (30%) + Trade value (20%)
    - Secondary filtering: Select only stocks with current price > opening price (sustained rise)
    - Penny stock filter: Market cap 50B KRW or more
    """
    logger.debug("trigger_morning_gap_up_momentum started")
    common = snapshot.index.intersection(prev_snapshot.index)
    snap = snapshot.loc[common].copy()
    prev = prev_snapshot.loc[common].copy()

    # Merge and filter market cap data (v1.16.6: adjusted to 500B or more)
    if cap_df is not None and not cap_df.empty:
        snap = snap.merge(cap_df[["시가총액"]], left_index=True, right_index=True, how="inner")
        # Select stocks with market cap 500B KRW or more (v1.16.6: expanded opportunity pool, 518 stocks)
        snap = snap[snap["시가총액"] >= 500000000000]
        logger.debug(f"Stock count after market cap filtering: {len(snap)}")
        if snap.empty:
            logger.warning("No stocks after market cap filtering")
            return pd.DataFrame()

    # Apply absolute criteria (raised to 10B KRW trade value)
    snap = apply_absolute_filters(snap, min_value=10000000000)

    # Calculate gap rate
    snap["gap_up_rate"] = (snap["Open"] / prev["Close"] - 1) * 100
    snap["intraday_change_rate"] = (snap["Close"] / snap["Open"] - 1) * 100  # Intraday change rate vs opening
    snap["prev_day_change_rate"] = ((snap["Close"] - prev["Close"]) / prev["Close"]) * 100  # Change rate vs previous close
    snap["sustained_rise"] = snap["Close"] > snap["Open"]

    # Primary filtering: Gap rate 1% or more, change rate 20% or less (v1.16.6: surge stocks can enter)
    snap = snap[(snap["gap_up_rate"] >= 1.0) & (snap["prev_day_change_rate"] <= 15.0)]

    # Score calculation (custom composite score)
    if not snap.empty:
        # Normalize each indicator
        for col in ["gap_up_rate", "intraday_change_rate", "Amount"]:
            col_max = snap[col].max()
            col_min = snap[col].min()
            col_range = col_max - col_min if col_max > col_min else 1
            snap[f"{col}_norm"] = (snap[col] - col_min) / col_range

        # Calculate composite score (apply weights)
        snap["composite_score"] = (
                snap["gap_up_rate_norm"] * 0.5 +
                snap["intraday_change_rate_norm"] * 0.3 +
                snap["Amount_norm"] * 0.2
        )

        # Select candidates by score (Full pool for RSI search)
        scored_candidates = snap.sort_values("composite_score", ascending=False)
    else:
        scored_candidates = snap

    # Secondary filtering: Select only stocks with sustained rise
    rising_candidates = scored_candidates[scored_candidates["sustained_rise"] == True].copy()

    if rising_candidates.empty:
        logger.debug("trigger_morning_gap_up_momentum: No stocks meeting criteria")
        return pd.DataFrame()

    # Apply overheating filter (Search for top 10 non-overheated stocks)
    filtered_result = apply_overheating_filter(rising_candidates, trade_date, snap, limit=10)
    
    if filtered_result.empty:
        return pd.DataFrame()

    # Calculate additional information
    filtered_result["total_momentum"] = filtered_result["gap_up_rate"] + filtered_result["intraday_change_rate"]

    logger.debug(f"Gap-up momentum stocks detected: {len(filtered_result)}")
    return enhance_dataframe(filtered_result)


def trigger_morning_value_to_cap_ratio(trade_date: str, snapshot: pd.DataFrame, prev_snapshot: pd.DataFrame, cap_df: pd.DataFrame, top_n: int = 10) -> pd.DataFrame:
    """
    [Morning Trigger 3] Top stocks with concentrated fund inflow vs market cap
    - Absolute criteria: Minimum trade value 500M KRW or more
    - Composite score: Trade value ratio (50%) + Absolute trade value (30%) + Intraday change (20%)
    - Secondary filtering: Select only rising stocks (current price > opening price)
    """
    logger.info("Starting analysis of top stocks with concentrated fund inflow vs market cap")

    # Defense code 1: Input data validation
    if snapshot.empty:
        logger.error("snapshot data is empty")
        return pd.DataFrame()

    if prev_snapshot.empty:
        logger.error("prev_snapshot data is empty")
        return pd.DataFrame()

    if cap_df.empty:
        logger.error("cap_df data is empty")
        return pd.DataFrame()

    # Defense code 2: Check market cap column exists
    if '시가총액' not in cap_df.columns:
        logger.error(f"'market cap' column not found in cap_df. Actual columns: {list(cap_df.columns)}")
        return pd.DataFrame()

    logger.info(f"Input data validation complete - snapshot: {len(snapshot)} items, cap_df: {len(cap_df)} items")

    try:
        # Merge market cap and OHLCV data
        logger.debug("Starting market cap data merge")
        merged = snapshot.merge(cap_df[["시가총액"]], left_index=True, right_index=True, how="inner").copy()
        logger.info(f"Data merge complete: {len(merged)} stocks")

        # Defense code 3: Recheck market cap column after merge
        if '시가총액' not in merged.columns:
            logger.error(f"'market cap' column not found after merge. Post-merge columns: {list(merged.columns)}")
            return pd.DataFrame()

        # Merge with previous trading day data
        common = merged.index.intersection(prev_snapshot.index)
        if len(common) == 0:
            logger.error("No common stocks")
            return pd.DataFrame()

        if len(common) < 50:
            logger.warning(f"Low number of common stocks ({len(common)}). Result quality may be poor")

        merged = merged.loc[common].copy()
        prev = prev_snapshot.loc[common].copy()
        logger.debug(f"Previous day data merge complete - Common stocks: {len(common)}")

        # Apply absolute criteria (raised to 10B KRW trade value)
        logger.debug("Starting absolute criteria filtering")
        merged = apply_absolute_filters(merged, min_value=10000000000)
        if merged.empty:
            logger.warning("No stocks after absolute criteria filtering")
            return pd.DataFrame()

        logger.info(f"Filtering complete: {len(merged)} stocks")

        # Defense code 4: Recheck required columns
        required_columns = ['Amount', '시가총액', 'Close', 'Open']
        missing_columns = [col for col in required_columns if col not in merged.columns]
        if missing_columns:
            logger.error(f"Missing required columns: {missing_columns}")
            return pd.DataFrame()

        # Calculate trade value / market cap ratio
        logger.debug("Starting trade value ratio calculation")
        merged["trade_value_ratio"] = (merged["Amount"] / merged["시가총액"]) * 100

        # Calculate two types of change rates
        merged["intraday_change_rate"] = (merged["Close"] / merged["Open"] - 1) * 100  # Current vs opening price
        merged["prev_day_change_rate"] = ((merged["Close"] - prev["Close"]) / prev["Close"]) * 100  # Same as brokerage app
        merged["is_rising"] = merged["Close"] > merged["Open"]

        # v1.16.6: Change rate upper limit (20% or less, surge stocks can enter with fixed stop-loss)
        merged = merged[merged["prev_day_change_rate"] <= 20.0]
        if merged.empty:
            logger.warning("No stocks after change rate upper limit filtering")
            return pd.DataFrame()

        # Market cap filtering - minimum 500B KRW (v1.16.6: expanded opportunity pool)
        merged = merged[merged["시가총액"] >= 500000000000]
        if merged.empty:
            logger.warning("No stocks after market cap filtering")
            return pd.DataFrame()

        logger.debug(f"Market cap filtering complete - Remaining stocks: {len(merged)}")

        # Calculate composite score
        if not merged.empty:
            # Normalize each indicator
            for col in ["trade_value_ratio", "Amount", "intraday_change_rate"]:
                col_max = merged[col].max()
                col_min = merged[col].min()
                col_range = col_max - col_min if col_max > col_min else 1
                merged[f"{col}_norm"] = (merged[col] - col_min) / col_range

            # Calculate composite score
            merged["composite_score"] = (
                    merged["trade_value_ratio_norm"] * 0.5 +
                    merged["Amount_norm"] * 0.3 +
                    merged["intraday_change_rate_norm"] * 0.2
            )

        # Select candidates (Full pool for RSI search)
        if not merged.empty:
            scored_candidates = merged.sort_values("composite_score", ascending=False)
        else:
            scored_candidates = merged

        # Secondary filtering: Select only rising stocks
        rising_candidates = scored_candidates[scored_candidates["is_rising"] == True].copy()

        if rising_candidates.empty:
            logger.info("No stocks meeting criteria")
            return pd.DataFrame()

        # Apply overheating filter (Search for top 10 non-overheated stocks)
        filtered_result = apply_overheating_filter(rising_candidates, trade_date, merged, limit=10)

        if filtered_result.empty:
            return pd.DataFrame()

        logger.info(f"Analysis complete: {len(filtered_result)} stocks selected")
        return enhance_dataframe(filtered_result)

    except Exception as e:
        logger.error(f"Exception occurred during function execution: {e}")
        import traceback
        logger.debug(f"Detailed error:\n{traceback.format_exc()}")
        return pd.DataFrame()


def trigger_morning_pullback_from_high(trade_date: str, snapshot: pd.DataFrame, prev_snapshot: pd.DataFrame, cap_df: pd.DataFrame = None, top_n: int = 10) -> pd.DataFrame:
    """
    [Morning Trigger 4] Top 20-day high pullback first bull candle stocks
    - Market cap 500B KRW or more, minimum trade value 10B KRW
    - Daily bull candle (Close > Open) and change rate <= 15.0%
    - Pullback check: D-1 Close is -3% ~ -12% compared to past 20-day High (excluding today)
    - Rebound check: Today's close (9:10) is higher than D-1 Close
    - Composite score: normalized pullback_ratio (60%) + normalized Amount (40%)
    """
    logger.debug("trigger_morning_pullback_from_high started")
    common = snapshot.index.intersection(prev_snapshot.index)
    snap = snapshot.loc[common].copy()
    prev = prev_snapshot.loc[common].copy()

    # Filter market cap data (500B KRW or more)
    if cap_df is not None and not cap_df.empty:
        snap = snap.merge(cap_df[["시가총액"]], left_index=True, right_index=True, how="inner")
        snap = snap[snap["시가총액"] >= 500000000000]
        logger.debug(f"Stock count after market cap filtering: {len(snap)}")
        if snap.empty:
            return pd.DataFrame()

    # Apply absolute filters (minimum 10B KRW trade value)
    snap = apply_absolute_filters(snap, min_value=10000000000)
    if snap.empty:
        return pd.DataFrame()

    # Calculate change rates
    common_after_filter = snap.index.intersection(prev.index)
    snap = snap.loc[common_after_filter]
    prev = prev.loc[common_after_filter]
    snap["intraday_change_rate"] = (snap["Close"] / snap["Open"] - 1) * 100
    snap["prev_day_change_rate"] = ((snap["Close"] - prev["Close"]) / prev["Close"]) * 100

    # Filter for:
    # 1. Bull candle today (Close > Open)
    # 2. Today's close > D-1 close (price rebound)
    # 3. Prevent overheating (change rate <= 15.0%)
    snap = snap[
        (snap["Close"] > snap["Open"]) &
        (snap["prev_day_change_rate"] <= 15.0) &
        (snap["Close"] > prev["Close"])
    ]
    if snap.empty:
        logger.debug("No stocks meeting initial candle/gain rules for pullback trigger.")
        return pd.DataFrame()

    # Query 20-day data for each remaining candidate in parallel to verify pullback
    tickers = list(snap.index)
    valid_tickers = []
    pullback_ratios = {}

    def check_pullback(ticker):
        try:
            # Prevent rate-limiting with random jitter
            time.sleep(random.uniform(0.05, 0.15))
            # Fetch 21 days of data (including today, tail(20) of historical will be D-1 to D-20)
            hist = get_multi_day_ohlcv(ticker, trade_date, days=21)
            if hist.empty or len(hist) < 3:
                return ticker, False, 0.0

            # Exclude today (the last row) only if historical data contains today
            last_date_str = hist.index[-1].strftime('%Y%m%d') if hasattr(hist.index[-1], 'strftime') else str(hist.index[-1]).replace("-", "")[:8]
            if last_date_str == trade_date:
                past_hist = hist.iloc[:-1].tail(20).copy()
            else:
                past_hist = hist.tail(20).copy()
                
            if past_hist.empty or len(past_hist) < 2:
                return ticker, False, 0.0

            high_col = "High" if "High" in past_hist.columns else "고가"
            close_col = "Close" if "Close" in past_hist.columns else "종가"

            high_20 = float(past_hist[high_col].max())
            close_prev = float(past_hist[close_col].iloc[-1])

            if high_20 <= 0 or close_prev <= 0:
                return ticker, False, 0.0

            # Calculate ratio of D-1 Close / 20-day High
            ratio = close_prev / high_20
            # Pullback range: -3% to -12% from high (i.e. ratio is 0.88 to 0.97)
            if 0.88 <= ratio <= 0.97:
                return ticker, True, ratio
            return ticker, False, 0.0
        except Exception as e:
            logger.warning(f"Error checking pullback for {ticker}: {e}")
            return ticker, False, 0.0

    # Execute checks in parallel (max 5 threads for conservative API use)
    with ThreadPoolExecutor(max_workers=min(len(tickers), 5)) as executor:
        results = list(executor.map(check_pullback, tickers))

    for ticker, is_valid, ratio in results:
        if is_valid:
            valid_tickers.append(ticker)
            pullback_ratios[ticker] = ratio

    if not valid_tickers:
        logger.debug("No stocks met the 20-day high pullback criteria.")
        return pd.DataFrame()

    snap = snap.loc[valid_tickers].copy()
    snap["pullback_ratio"] = snap.index.map(pullback_ratios)

    # Normalize pullback_ratio (closer to 1.0 is better - shallower pullback) and Amount
    col_max = snap["pullback_ratio"].max()
    col_min = snap["pullback_ratio"].min()
    col_range = col_max - col_min if col_max > col_min else 1
    snap["pullback_ratio_norm"] = (snap["pullback_ratio"] - col_min) / col_range

    amount_max = snap["Amount"].max()
    amount_min = snap["Amount"].min()
    amount_range = amount_max - amount_min if amount_max > amount_min else 1
    snap["Amount_norm"] = (snap["Amount"] - amount_min) / amount_range

    # Composite score
    snap["composite_score"] = snap["pullback_ratio_norm"] * 0.6 + snap["Amount_norm"] * 0.4

    scored_candidates = snap.sort_values("composite_score", ascending=False)

    # Apply overheating filter (RSI)
    filtered_result = apply_overheating_filter(scored_candidates, trade_date, snap, limit=10)

    if filtered_result.empty:
        return pd.DataFrame()

    return enhance_dataframe(filtered_result)

# --- Comprehensive selection function ---
def select_final_tickers(triggers: dict, trade_date: str = None, use_hybrid: bool = True, lookback_days: int = 10) -> dict:
    """
    Consolidate stocks selected from each trigger and choose final stocks.

    Hybrid method (use_hybrid=True):
    1. Collect top 10 candidates from each trigger
    2. Calculate agent criteria scores for all candidates (analyze 10-20 day data)
    3. Calculate final score with composite score (40%) + agent score (60%)
    4. Select rank 1 by final score from each trigger

    Args:
        triggers: Dictionary of DataFrame results by trigger
        trade_date: Reference trading date (required in hybrid mode)
        use_hybrid: Whether to use hybrid selection (default: True)
        lookback_days: Number of past business days for agent score calculation (default: 10)

    Returns:
        Dictionary of finally selected stocks
    """
    final_result = {}

    # 1. Collect candidates from each trigger
    trigger_candidates = {}  # Trigger name -> DataFrame
    all_tickers = set()  # For duplicate checking

    for name, df in triggers.items():
        if not df.empty:
            # Max 10 candidates from each trigger (already returned with head(10))
            candidates = df.copy()
            candidates["is_fallback"] = False
            trigger_candidates[name] = candidates
            all_tickers.update(candidates.index.tolist())

    if not trigger_candidates:
        logger.warning("No candidates from all triggers.")
        return final_result

    discarded_candidates = []

    # 2. Hybrid mode: Calculate agent scores
    if use_hybrid and trade_date:
        logger.info(f"Hybrid selection mode - Calculate agent scores with {lookback_days}-day data")

        for name, candidates_df in trigger_candidates.items():
            # v1.16.6: Calculate agent scores by trigger type
            scored_df = score_candidates_by_agent_criteria(candidates_df, trade_date, lookback_days, trigger_type=name)

            # v1.16.6: Calculate final score: composite score (30%) + agent score (70%)
            # Increase agent score weight to prioritize stocks likely to be approved by agents
            if "composite_score" in scored_df.columns and "agent_fit_score" in scored_df.columns:
                valid_df = scored_df[scored_df["agent_fit_score"] > 0.0].copy()
                invalid_df = scored_df[scored_df["agent_fit_score"] <= 0.0].copy()

                if not valid_df.empty:
                    # Normalize composite score (0~1)
                    cp_max = valid_df["composite_score"].max()
                    cp_min = valid_df["composite_score"].min()
                    cp_range = cp_max - cp_min if cp_max > cp_min else 1
                    valid_df["composite_score_norm"] = (valid_df["composite_score"] - cp_min) / cp_range

                    # Calculate final score (v1.16.6: adjusted weights)
                    valid_df["final_score"] = (
                        valid_df["composite_score_norm"] * 0.3 +
                        valid_df["agent_fit_score"] * 0.7
                    )

                    # Sort by final score
                    valid_df = valid_df.sort_values("final_score", ascending=False)

                    # Logging
                    logger.info(f"[{name}] Hybrid score calculation complete:")
                    for ticker in valid_df.index[:3]:
                        logger.info(f"  - {ticker} ({valid_df.loc[ticker, 'stock_name'] if 'stock_name' in valid_df.columns else ''}): "
                                   f"Composite={valid_df.loc[ticker, 'composite_score']:.3f}, "
                                   f"Agent={valid_df.loc[ticker, 'agent_fit_score']:.3f}, "
                                   f"Final={valid_df.loc[ticker, 'final_score']:.3f}, "
                                   f"Risk-reward={valid_df.loc[ticker, 'risk_reward_ratio']:.2f}, "
                                   f"Stop-loss={valid_df.loc[ticker, 'stop_loss_pct']*100:.1f}%")

                trigger_candidates[name] = valid_df
                
                # Save invalid/discarded candidates for fallback
                if not invalid_df.empty:
                    invalid_df["is_fallback"] = True
                    invalid_df["final_score"] = invalid_df["composite_score"]
                    discarded_candidates.append((name, invalid_df))
            else:
                trigger_candidates[name] = scored_df

    # 3. Final stock selection
    selected_tickers = set()
    score_column = "final_score" if use_hybrid and trade_date else "composite_score"

    # Select top 1 stock from each trigger
    for name, df in trigger_candidates.items():
        if not df.empty and len(selected_tickers) < 4:
            # Check sort column
            if score_column in df.columns:
                sorted_df = df.sort_values(score_column, ascending=False)
            else:
                sorted_df = df

            # Select rank 1 excluding duplicates
            for ticker in sorted_df.index:
                if ticker not in selected_tickers:
                    # Double check to ensure agent score is valid
                    if use_hybrid and trade_date and "agent_fit_score" in sorted_df.columns:
                        if sorted_df.loc[ticker, "agent_fit_score"] <= 0.0:
                            continue
                    final_result[name] = sorted_df.loc[[ticker]]
                    selected_tickers.add(ticker)
                    logger.info(f"[{name}] Final selection: {ticker}")
                    break

    # 4. Add more by overall score if less than 4 (only for candidates meeting the criteria)
    if len(selected_tickers) < 4:
        # Sort all candidates by score
        all_candidates = []
        for name, df in trigger_candidates.items():
            if df.empty:
                continue
            for ticker in df.index:
                if ticker not in selected_tickers:
                    # Filter out candidates with agent_fit_score <= 0.0 in hybrid mode
                    if use_hybrid and trade_date and "agent_fit_score" in df.columns:
                        if df.loc[ticker, "agent_fit_score"] <= 0.0:
                            continue
                    score = df.loc[ticker, score_column] if score_column in df.columns else 0
                    all_candidates.append((name, ticker, score, df.loc[[ticker]]))

        all_candidates.sort(key=lambda x: x[2], reverse=True)

        for trigger_name, ticker, _, ticker_df in all_candidates:
            if ticker not in selected_tickers and len(selected_tickers) < 4:
                if trigger_name in final_result:
                    final_result[trigger_name] = pd.concat([final_result[trigger_name], ticker_df])
                else:
                    final_result[trigger_name] = ticker_df
                selected_tickers.add(ticker)
                logger.info(f"[{trigger_name}] Additional selection: {ticker}")

    # 5. Fallback selection using discarded candidates if less than 4
    if len(selected_tickers) < 4 and discarded_candidates:
        logger.info(f"Selected only {len(selected_tickers)} stocks. Performing fallback selection to reach target of 4...")
        fallback_candidates = []
        for name, df in discarded_candidates:
            for ticker in df.index:
                if ticker not in selected_tickers:
                    score = df.loc[ticker, "final_score"] if "final_score" in df.columns else 0
                    fallback_candidates.append((name, ticker, score, df.loc[[ticker]]))

        fallback_candidates.sort(key=lambda x: x[2], reverse=True)

        for trigger_name, ticker, _, ticker_df in fallback_candidates:
            if ticker not in selected_tickers and len(selected_tickers) < 4:
                ticker_df = ticker_df.copy()
                ticker_df["is_fallback"] = True
                if trigger_name in final_result:
                    final_result[trigger_name] = pd.concat([final_result[trigger_name], ticker_df])
                else:
                    final_result[trigger_name] = ticker_df
                selected_tickers.add(ticker)
                logger.info(f"[{trigger_name}] Fallback selection: {ticker} (Score: {ticker_df.loc[ticker, 'composite_score']:.3f})")

    return final_result

# --- Batch execution function ---
def run_batch(trigger_time: str = "morning", log_level: str = "INFO", output_file: str = None, exclude_codes: list = None):
    """
    trigger_time: Execution mode (morning only)
    log_level: "DEBUG", "INFO", "WARNING", etc. (INFO recommended for production)
    output_file: JSON file path to save results (optional)
    exclude_codes: List of stock codes to exclude (already in portfolio)
    """
    if trigger_time != "morning":
        logger.warning(f"Requested trigger_time is '{trigger_time}', but only 'morning' is supported. Executing morning batch.")
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)
    logger.setLevel(numeric_level)
    ch.setLevel(numeric_level)
    logger.info(f"Log level: {log_level.upper()}")

    today_str = datetime.datetime.today().strftime("%Y%m%d")
    trade_date = stock_api.get_nearest_business_day_in_a_week(today_str, prev=True)
    logger.info(f"Batch reference trading date: {trade_date}")

    try:
        snapshot = get_snapshot(trade_date)
    except ValueError as e:
        logger.error(f"Snapshot query failed: {e}")
        trade_date = stock_api.get_nearest_business_day_in_a_week(trade_date, prev=True)
        logger.info(f"Retry batch reference trading date: {trade_date}")
        snapshot = get_snapshot(trade_date)

    # 시작가가 내 slot보다 낮은 종목들만 대상으로 (v1.16.6: 슬로트 조건 추가)
    from trading.domestic_stock_trading import DEFAULT_BUY_AMOUNT
    snapshot = snapshot[snapshot["Open"] < DEFAULT_BUY_AMOUNT]

    # 포트폴리오 보유 종목 제외
    if exclude_codes:
        snapshot = snapshot[~snapshot.index.isin(exclude_codes)]
        logger.info(f"Excluded {len(exclude_codes)} portfolio stocks from snapshot. Remaining: {len(snapshot)}")

    prev_snapshot, prev_date = get_previous_snapshot(trade_date)
    logger.debug(f"Previous trading date: {prev_date}")

    cap_df = get_market_cap_df(trade_date, market="ALL")
    logger.debug(f"Market cap data stock count: {len(cap_df)}")

    logger.info("=== Morning batch execution ===")
    # Execute morning triggers - pass cap_df
    res1 = trigger_morning_volume_surge(trade_date, snapshot, prev_snapshot, cap_df)
    res2 = trigger_morning_gap_up_momentum(trade_date, snapshot, prev_snapshot, cap_df)
    res3 = trigger_morning_value_to_cap_ratio(trade_date, snapshot, prev_snapshot, cap_df)
    res4 = trigger_morning_pullback_from_high(trade_date, snapshot, prev_snapshot, cap_df)
    triggers = {
        "거래량 급증 상위주": res1,
        "갭 상승 모멘텀 상위주": res2,
        "시총 대비 집중 자금 유입 상위주": res3,
        "20일 신고가 눌림목 첫 양봉": res4
    }

    # Log results by trigger
    for name, df in triggers.items():
        if df.empty:
            logger.info(f"{name}: No stocks meet the criteria.")
        else:
            logger.info(f"{name} detected stocks ({len(df)} stocks):")
            for ticker in df.index:
                stock_name = df.loc[ticker, "stock_name"] if "stock_name" in df.columns else ""
                logger.info(f"- {ticker} ({stock_name})")

            # Output detailed information only at debug level
            logger.debug(f"Detailed information:\n{df}\n{'-'*40}")

    # Final selection results
    final_results = select_final_tickers(triggers, trade_date=trade_date)

    # Save results as JSON (if requested)
    if output_file:
        import json

        # Include detailed information of selected stocks
        output_data = {}

        # Process by trigger type
        for trigger_type, stocks_df in final_results.items():
            if not stocks_df.empty:
                if trigger_type not in output_data:
                    output_data[trigger_type] = []

                for ticker in stocks_df.index:
                    stock_info = {
                        "code": ticker,
                        "name": stocks_df.loc[ticker, "stock_name"] if "stock_name" in stocks_df.columns else "",
                        "current_price": float(stocks_df.loc[ticker, "Close"]) if "Close" in stocks_df.columns else 0,
                        "change_rate": float(stocks_df.loc[ticker, "prev_day_change_rate"]) if "prev_day_change_rate" in stocks_df.columns else 0,
                        "volume": int(stocks_df.loc[ticker, "Volume"]) if "Volume" in stocks_df.columns else 0,
                        "trade_value": float(stocks_df.loc[ticker, "Amount"]) if "Amount" in stocks_df.columns else 0,
                    }

                    # Add trigger type specific data
                    if "volume_increase_rate" in stocks_df.columns and trigger_type == "거래량 급증 상위주":
                        stock_info["volume_increase"] = float(stocks_df.loc[ticker, "volume_increase_rate"])
                    elif "gap_up_rate" in stocks_df.columns and trigger_type == "갭 상승 모멘텀 상위주":
                        stock_info["gap_rate"] = float(stocks_df.loc[ticker, "gap_up_rate"])
                    elif "trade_value_ratio" in stocks_df.columns and trigger_type == "시총 대비 집중 자금 유입 상위주":
                        stock_info["trade_value_ratio"] = float(stocks_df.loc[ticker, "trade_value_ratio"])
                        stock_info["market_cap"] = float(stocks_df.loc[ticker, "시가총액"])
                    elif "pullback_ratio" in stocks_df.columns and trigger_type == "20일 신고가 눌림목 첫 양봉":
                        stock_info["pullback_ratio"] = float(stocks_df.loc[ticker, "pullback_ratio"])

                    # Add agent score information (hybrid mode)
                    if "agent_fit_score" in stocks_df.columns:
                        stock_info["agent_fit_score"] = float(stocks_df.loc[ticker, "agent_fit_score"])
                        stock_info["risk_reward_ratio"] = float(stocks_df.loc[ticker, "risk_reward_ratio"]) if "risk_reward_ratio" in stocks_df.columns else 0
                        stock_info["stop_loss_pct"] = float(stocks_df.loc[ticker, "stop_loss_pct"]) * 100 if "stop_loss_pct" in stocks_df.columns else 0
                        stock_info["stop_loss_price"] = float(stocks_df.loc[ticker, "stop_loss_price"]) if "stop_loss_price" in stocks_df.columns else 0
                        stock_info["target_price"] = float(stocks_df.loc[ticker, "target_price"]) if "target_price" in stocks_df.columns else 0
                        stock_info["pivot_point"] = float(stocks_df.loc[ticker, "pivot_point"]) if "pivot_point" in stocks_df.columns else 0
                        stock_info["volume_profile_info"] = str(stocks_df.loc[ticker, "volume_profile_info"]) if "volume_profile_info" in stocks_df.columns else "No significant upper resistance"

                    if "final_score" in stocks_df.columns:
                        stock_info["final_score"] = float(stocks_df.loc[ticker, "final_score"])

                    if "is_fallback" in stocks_df.columns:
                        stock_info["is_fallback"] = bool(stocks_df.loc[ticker, "is_fallback"])
                    else:
                        stock_info["is_fallback"] = False

                    output_data[trigger_type].append(stock_info)

        # Add execution time and metadata
        output_data["metadata"] = {
            "run_time": datetime.datetime.now().isoformat(),
            "trigger_mode": "morning",
            "trade_date": trade_date,
            "selection_mode": "hybrid",
            "lookback_days": 10
        }

        # Save JSON file
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)

        logger.info(f"Selection results saved to {output_file}.")

    return final_results

if __name__ == "__main__":
    # Usage: python trigger_batch.py [morning] [DEBUG|INFO|...] [--output filepath]
    import argparse

    parser = argparse.ArgumentParser(description="Execute trigger batch (Morning mode only)")
    parser.add_argument("mode", nargs="?", default="morning", choices=["morning"], help="Execution mode (fixed to 'morning')")
    parser.add_argument("log_level", nargs="?", default="INFO", help="Logging level")
    parser.add_argument("--output", help="JSON file path to save results")

    args = parser.parse_args()

    run_batch(args.mode, args.log_level, args.output)