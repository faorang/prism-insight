"""
Created on 20250601
"""

import sys
import time
from typing import Optional
import logging
from pathlib import Path

import pandas as pd

# 현재 파일이 있는 디렉토리의 경로
TRADING_DIR = Path(__file__).parent

# kis_auth import (같은 디렉토리)
import sys
sys.path.insert(0, str(TRADING_DIR))
import kis_auth as ka

# 로깅 설정
logging.basicConfig(level=logging.INFO)

##############################################################################################
# [국내주식] 업종/기타 > 국내휴장일조회[국내주식-040]
##############################################################################################

# 상수 정의
API_URL = "/uapi/domestic-stock/v1/quotations/chk-holiday"


def chk_holiday(
        bass_dt: str,  # 기준일자 (YYYYMMDD)
        NK100: str = "",  # 연속조회키
        FK100: str = "",  # 연속조회검색조건
        tr_cont: str = "",  # 연속거래여부
        dataframe: Optional[pd.DataFrame] = None,  # 누적 데이터프레임
        depth: int = 0,  # 내부 재귀깊이 (자동관리)
        max_depth: int = 10  # 최대 재귀 횟수 제한
) -> pd.DataFrame:
    """
    (★중요) 국내휴장일조회(TCA0903R) 서비스는 당사 원장서비스와 연관되어 있어
    단시간 내 다수 호출시 서비스에 영향을 줄 수 있어 가급적 1일 1회 호출 부탁드립니다.

    국내휴장일조회 API입니다.
    영업일, 거래일, 개장일, 결제일 여부를 조회할 수 있습니다.
    주문을 넣을 수 있는지 확인하고자 하실 경우 개장일여부(opnd_yn)을 사용하시면 됩니다.

    Args:
        bass_dt (str): [필수] 기준일자 (ex. YYYYMMDD)
        NK100 (str): 연속조회키
        FK100 (str): 연속조회검색조건
        tr_cont (str): 연속거래여부
        dataframe (Optional[pd.DataFrame]): 누적 데이터프레임
        depth (int): 내부 재귀깊이 (자동관리)
        max_depth (int): 최대 재귀 횟수 제한

    Returns:
        pd.DataFrame: 국내휴장일조회 데이터

    Example:
        >>> df = chk_holiday(bass_dt="20250630")
        >>> print(df)
    """

    if bass_dt == "":
        raise ValueError("bass_dt is required (e.g. 'YYYYMMDD')")

    if depth > max_depth:
        logging.warning("Max recursive depth reached.")
        if dataframe is None:
            return pd.DataFrame()
        else:
            return dataframe

    tr_id = "CTCA0903R"  # 국내휴장일조회

    params = {
        "BASS_DT": bass_dt,
        "CTX_AREA_FK": FK100,
        "CTX_AREA_NK": NK100
    }

    res = ka._url_fetch(f'{API_URL}?trd_dd={bass_dt}', tr_id, tr_cont, params)

    if res.isOK():
        if hasattr(res.getBody(), 'output'):
            output_data = res.getBody().output
            if not isinstance(output_data, list):
                output_data = [output_data]
            current_data = pd.DataFrame(output_data)
        else:
            current_data = pd.DataFrame()

        if dataframe is not None:
            dataframe = pd.concat([dataframe, current_data], ignore_index=True)
        else:
            dataframe = current_data

        tr_cont = res.getHeader().tr_cont
        FK100 = res.getBody().ctx_area_fk
        NK100 = res.getBody().ctx_area_nk

        # ka.smart_sleep()  # 시스템 안정적 운영을 위한 지연

        logging.info("Data fetch complete.")
        return dataframe
    else:
        res.printError(url=API_URL)
        return pd.DataFrame()

def is_market_day(bass_dt):
    """한국 주식 시장 영업일인지 확인"""
    try:
        # 인증 토큰 발급
        ka.auth()

        # case1 조회
        logging.info("=== case1 조회 ===")
        result = chk_holiday(bass_dt=bass_dt)

        logging.info("사용 가능한 컬럼: %s", result.columns.tolist())

        # 컬럼명 한글 변환 및 데이터 출력
        """     result = result.rename(columns=COLUMN_MAPPING)

        # 숫자형 컬럼 소수점 둘째자리까지 표시
        for col in NUMERIC_COLUMNS:
            if col in result.columns:
                result[col] = pd.to_numeric(result[col], errors='coerce').round(2)
        """
        logging.info("결과:")
        logging.info(result.loc[result["bass_dt"] == bass_dt].iloc[0])
        is_market_open = result.loc[result["bass_dt"] == bass_dt, "opnd_yn"].iloc[0]
        return is_market_open == "Y"
    except ValueError as e:
        logging.error("에러 발생: %s" % str(e))
        logging.info("기본값으로 영업일로 간주합니다.")
        return True # 에러 시 기본값으로 영업일로 간주
    except Exception as e:
        logging.error("예상치 못한 에러 발생: %s" % str(e))
        try:

            from krx_data_client import (
                get_nearest_business_day_in_a_week,
            )
            business_day = get_nearest_business_day_in_a_week(bass_dt, prev=True)
            return business_day == bass_dt
        except Exception:
            pass

        return True # 에러 시 기본값으로 영업일로 간주

if __name__ == "__main__":
    is_open = is_market_day("20251231")
    print(f"2025-12-31 is market open: {is_open}")