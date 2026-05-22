#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from dotenv import load_dotenv
load_dotenv()  # Load environment variables from .env file (required before krx_data_client import)

import sys
import datetime
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
            return df.tail(days)
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
                return df.tail(days)
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
        return df.tail(days)
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
    Searches through candidates until 'limit' non-overheated stocks are found.
    """
    if candidates.empty:
        return candidates

    logger.info(f"Applying overheating filter (RSI threshold: {threshold}) - Searching for top {limit} from {len(candidates)} candidates")
    final_indices = []

    for ticker in candidates.index:
        try:
            # Fetch 20 days of data for RSI(14)
            hist = get_multi_day_ohlcv(ticker, trade_date, days=20)
            if hist.empty:
                # If data missing, we cautiously include it but move on
                final_indices.append(ticker)
                if len(final_indices) >= limit:
                    break
                continue

            # Ensure current snapshot data is included
            last_hist_date = hist.index[-1]
            if hasattr(last_hist_date, 'strftime'):
                last_hist_date_str = last_hist_date.strftime('%Y%m%d')
            else:
                last_hist_date_str = str(last_hist_date).replace("-", "")[:8]

            if last_hist_date_str != trade_date:
                if ticker in snapshot.index:
                    today_data = snapshot.loc[[ticker]].copy()
                    if isinstance(hist.index, pd.DatetimeIndex):
                        today_data.index = [pd.to_datetime(trade_date)]
                    hist = pd.concat([hist, today_data])

            rsi_val = calculate_rsi(hist)
            
            if rsi_val >= threshold:
                logger.info(f"Stock {ticker} skipped: Overheated (RSI: {rsi_val:.2f})")
                continue

            logger.debug(f"Stock {ticker} accepted (RSI: {rsi_val:.2f})")
            final_indices.append(ticker)
            
            if len(final_indices) >= limit:
                break
        except Exception as e:
            logger.warning(f"Failed to check RSI for {ticker}: {e}")
            final_indices.append(ticker)
            if len(final_indices) >= limit:
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
    "default": {"rr_target": 1.5, "sl_max": 0.07}
}


def calculate_agent_fit_metrics(ticker: str, current_price: float, trade_date: str, lookback_days: int = 10, trigger_type: str = None) -> dict:
    """
    Calculate metrics that fit buy/sell agent criteria.

    v1.16.6: Changed to fixed stop-loss method (15% annual return system)
    - Core change: 10-day support level based → current price based fixed stop-loss
    - Reason: Improved to allow surge stocks to meet agent criteria
    - Risk-reward ratio: Maintain resistance level based, guarantee minimum +15%

    Criteria by trigger type (synchronized with trading_agents.py):
    - Volume surge/Gap up/Intraday rise: Risk-reward 1.2+, Stop-loss 5%
    - Closing strength/Fund inflow: Risk-reward 1.3+, Stop-loss 5%
    - Sideways: Risk-reward 1.5+, Stop-loss 7%

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
    }

    if current_price <= 0:
        return result

    # v1.16.6: Query criteria by trigger type (query first)
    criteria = TRIGGER_CRITERIA.get(trigger_type, TRIGGER_CRITERIA["default"])
    sl_max = criteria["sl_max"]
    rr_target = criteria["rr_target"]

    # v1.16.6 Core change: Apply fixed stop-loss method
    # Before: 10-day low based → 48%+ stop-loss on surge stocks → agent rejection
    # After: Current price based fixed ratio → always meets agent criteria
    stop_loss_price = current_price * (1 - sl_max)
    stop_loss_pct = sl_max  # Fixed value (5% or 7%)

    # Target price & Pivot point calculation: Query 20 days of data for pivot calculation
    multi_day_df = get_multi_day_ohlcv(ticker, trade_date, 20)
    
    pivot_point = 0.0
    is_pivot_valid = False
    
    if not multi_day_df.empty and len(multi_day_df) >= 2:
        high_col = "High" if "High" in multi_day_df.columns else "고가"
        if high_col in multi_day_df.columns:
            # 당일(마지막 행)을 제외한 과거 데이터에서의 최고가
            past_df = multi_day_df.iloc[:-1]
            if not past_df.empty:
                pivot_point = float(past_df[high_col].max())
            else:
                pivot_point = float(current_price)
        else:
            pivot_point = float(current_price)
    else:
        pivot_point = float(current_price)

    # 피벗 포인트 조건 사전 필터링 (pivot_point <= current_price <= pivot_point * 1.07)
    if pivot_point and pivot_point > 0:
        if pivot_point <= current_price <= pivot_point * 1.07:
            is_pivot_valid = True

    if multi_day_df.empty or len(multi_day_df) < 3:
        # Default to current price + 15% when data is insufficient
        target_price = current_price * 1.15
        logger.debug(f"{ticker}: Insufficient data, applying default target price ({target_price:.0f})")
    else:
        # Check column name (English/Korean compatibility)
        high_col = "High" if "High" in multi_day_df.columns else "고가"

        if high_col not in multi_day_df.columns:
            target_price = current_price * 1.15
            logger.debug(f"{ticker}: No high column, applying default target price")
        else:
            # Filter out 0 values (market holidays or data errors)
            valid_highs = multi_day_df[high_col][multi_day_df[high_col] > 0]
            if valid_highs.empty:
                target_price = current_price * 1.15
            else:
                # Resistance level (highest among recent N-day highs)
                target_price = valid_highs.max()

    # v1.16.6 Residual risk mitigation: Guarantee minimum +15% target
    min_target = current_price * 1.15
    if target_price <= current_price:
        target_price = min_target
        logger.debug(f"{ticker}: Target price below current price, applying minimum ({target_price:.0f})")
    elif target_price < min_target:
        # Raise to minimum if resistance is below +15%
        logger.debug(f"{ticker}: Target price {target_price:.0f} → raised to minimum {min_target:.0f}")
        target_price = min_target

    # Calculate risk-reward ratio
    potential_gain = target_price - current_price
    potential_loss = current_price - stop_loss_price

    if potential_loss > 0 and potential_gain > 0:
        risk_reward_ratio = potential_gain / potential_loss
    else:
        risk_reward_ratio = 0

    # v1.16.6: Calculate agent fit score (simplified)
    # sl_score = 1.0 since stop-loss is always within criteria
    rr_score = min(risk_reward_ratio / rr_target, 1.0) if risk_reward_ratio > 0 else 0
    sl_score = 1.0  # Always perfect score since stop-loss is fixed

    # Final score (risk-reward 60%, stop-loss 40%)
    agent_fit_score = rr_score * 0.6 + sl_score * 0.4

    # 피벗 돌파 조건 미충족 시 agent_fit_score를 0으로 강제 하향하여 1차 종목 선정에서 제외
    if not is_pivot_valid:
        logger.info(f"{ticker}: Pivot condition not met (Pivot: {pivot_point:.0f}, Current: {current_price:.0f}). Filtering out.")
        agent_fit_score = 0.0

    result = {
        "stop_loss_price": stop_loss_price,
        "target_price": target_price,
        "stop_loss_pct": stop_loss_pct,
        "risk_reward_ratio": risk_reward_ratio,
        "agent_fit_score": agent_fit_score,
        "pivot_point": pivot_point,
    }

    logger.debug(f"{ticker}: Stop-loss={stop_loss_price:.0f}, Target={target_price:.0f}, "
                 f"Stop-loss%={stop_loss_pct*100:.1f}% (fixed), Risk-reward={risk_reward_ratio:.2f}, "
                 f"Agent score={agent_fit_score:.3f}, Pivot={pivot_point:.0f}")

    return result


def score_candidates_by_agent_criteria(candidates_df: pd.DataFrame, trade_date: str, lookback_days: int = 10, trigger_type: str = None) -> pd.DataFrame:
    """
    Calculate agent criteria scores for candidate stocks and add to DataFrame.

    v1.16.6: Apply differentiated criteria by trigger type

    Args:
        candidates_df: Candidate stocks DataFrame (index: stock code, Close column required)
        trade_date: Reference trading date
        lookback_days: Number of past business days to query
        trigger_type: Trigger type (used for differentiated criteria)

    Returns:
        DataFrame with agent criteria scores added
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

    for ticker in result_df.index:
        current_price = result_df.loc[ticker, "Close"]
        metrics = calculate_agent_fit_metrics(ticker, current_price, trade_date, lookback_days, trigger_type)

        result_df.loc[ticker, "stop_loss_price"] = metrics["stop_loss_price"]
        result_df.loc[ticker, "target_price"] = metrics["target_price"]
        result_df.loc[ticker, "stop_loss_pct"] = metrics["stop_loss_pct"]
        result_df.loc[ticker, "risk_reward_ratio"] = metrics["risk_reward_ratio"]
        result_df.loc[ticker, "agent_fit_score"] = metrics["agent_fit_score"]
        result_df.loc[ticker, "pivot_point"] = metrics["pivot_point"]

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
            trigger_candidates[name] = candidates
            all_tickers.update(candidates.index.tolist())

    if not trigger_candidates:
        logger.warning("No candidates from all triggers.")
        return final_result

    # 2. Hybrid mode: Calculate agent scores
    if use_hybrid and trade_date:
        logger.info(f"Hybrid selection mode - Calculate agent scores with {lookback_days}-day data")

        for name, candidates_df in trigger_candidates.items():
            # v1.16.6: Calculate agent scores by trigger type
            scored_df = score_candidates_by_agent_criteria(candidates_df, trade_date, lookback_days, trigger_type=name)

            # v1.16.6: Calculate final score: composite score (30%) + agent score (70%)
            # Increase agent score weight to prioritize stocks likely to be approved by agents
            if "composite_score" in scored_df.columns and "agent_fit_score" in scored_df.columns:
                # Filter out candidates that do not meet the criteria (agent_fit_score <= 0)
                scored_df = scored_df[scored_df["agent_fit_score"] > 0.0]

                if not scored_df.empty:
                    # Normalize composite score (0~1)
                    cp_max = scored_df["composite_score"].max()
                    cp_min = scored_df["composite_score"].min()
                    cp_range = cp_max - cp_min if cp_max > cp_min else 1
                    scored_df["composite_score_norm"] = (scored_df["composite_score"] - cp_min) / cp_range

                    # Calculate final score (v1.16.6: adjusted weights)
                    scored_df["final_score"] = (
                        scored_df["composite_score_norm"] * 0.3 +
                        scored_df["agent_fit_score"] * 0.7
                    )

                    # Sort by final score
                    scored_df = scored_df.sort_values("final_score", ascending=False)

                    # Logging
                    logger.info(f"[{name}] Hybrid score calculation complete:")
                    for ticker in scored_df.index[:3]:
                        logger.info(f"  - {ticker} ({scored_df.loc[ticker, 'stock_name'] if 'stock_name' in scored_df.columns else ''}): "
                                   f"Composite={scored_df.loc[ticker, 'composite_score']:.3f}, "
                                   f"Agent={scored_df.loc[ticker, 'agent_fit_score']:.3f}, "
                                   f"Final={scored_df.loc[ticker, 'final_score']:.3f}, "
                                   f"Risk-reward={scored_df.loc[ticker, 'risk_reward_ratio']:.2f}, "
                                   f"Stop-loss={scored_df.loc[ticker, 'stop_loss_pct']*100:.1f}%")

            trigger_candidates[name] = scored_df

    # 3. Final stock selection
    selected_tickers = set()
    score_column = "final_score" if use_hybrid and trade_date else "composite_score"

    # Select top 1 stock from each trigger
    for name, df in trigger_candidates.items():
        if not df.empty and len(selected_tickers) < 3:
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

    # 4. Add more by overall score if less than 3 (only for candidates meeting the criteria)
    if len(selected_tickers) < 3:
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
            if ticker not in selected_tickers and len(selected_tickers) < 3:
                if trigger_name in final_result:
                    final_result[trigger_name] = pd.concat([final_result[trigger_name], ticker_df])
                else:
                    final_result[trigger_name] = ticker_df
                selected_tickers.add(ticker)
                logger.info(f"[{trigger_name}] Additional selection: {ticker}")

    return final_result

# --- Batch execution function ---
def run_batch(trigger_time: str = "morning", log_level: str = "INFO", output_file: str = None):
    """
    trigger_time: Execution mode (morning only)
    log_level: "DEBUG", "INFO", "WARNING", etc. (INFO recommended for production)
    output_file: JSON file path to save results (optional)
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

    prev_snapshot, prev_date = get_previous_snapshot(trade_date)
    logger.debug(f"Previous trading date: {prev_date}")

    cap_df = get_market_cap_df(trade_date, market="ALL")
    logger.debug(f"Market cap data stock count: {len(cap_df)}")

    logger.info("=== Morning batch execution ===")
    # Execute morning triggers - pass cap_df
    res1 = trigger_morning_volume_surge(trade_date, snapshot, prev_snapshot, cap_df)
    res2 = trigger_morning_gap_up_momentum(trade_date, snapshot, prev_snapshot, cap_df)
    res3 = trigger_morning_value_to_cap_ratio(trade_date, snapshot, prev_snapshot, cap_df)
    triggers = {"거래량 급증 상위주": res1, "갭 상승 모멘텀 상위주": res2, "시총 대비 집중 자금 유입 상위주": res3}

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
                    elif "gap_up_rate" in stocks_df.columns:
                        stock_info["gap_rate"] = float(stocks_df.loc[ticker, "gap_up_rate"])
                    elif "trade_value_ratio" in stocks_df.columns:
                        stock_info["trade_value_ratio"] = float(stocks_df.loc[ticker, "trade_value_ratio"])
                        stock_info["market_cap"] = float(stocks_df.loc[ticker, "시가총액"])

                    # Add agent score information (hybrid mode)
                    if "agent_fit_score" in stocks_df.columns:
                        stock_info["agent_fit_score"] = float(stocks_df.loc[ticker, "agent_fit_score"])
                        stock_info["risk_reward_ratio"] = float(stocks_df.loc[ticker, "risk_reward_ratio"]) if "risk_reward_ratio" in stocks_df.columns else 0
                        stock_info["stop_loss_pct"] = float(stocks_df.loc[ticker, "stop_loss_pct"]) * 100 if "stop_loss_pct" in stocks_df.columns else 0
                        stock_info["stop_loss_price"] = float(stocks_df.loc[ticker, "stop_loss_price"]) if "stop_loss_price" in stocks_df.columns else 0
                        stock_info["target_price"] = float(stocks_df.loc[ticker, "target_price"]) if "target_price" in stocks_df.columns else 0
                        stock_info["pivot_point"] = float(stocks_df.loc[ticker, "pivot_point"]) if "pivot_point" in stocks_df.columns else 0
                    if "final_score" in stocks_df.columns:
                        stock_info["final_score"] = float(stocks_df.loc[ticker, "final_score"])

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