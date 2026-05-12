import asyncio
import sqlite3
from typing import Dict, Any

from streamlit import cursor

async def get_portfolio_stock_count(db_path: str = "stock_tracking_db.sqlite") -> int:
    """현재 보유 중인 포트폴리오 종목 갯수를 반환합니다."""
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row  # 결과를 딕셔너리 형태로 반환
        cursor = conn.cursor()

        query = "SELECT COUNT(*) FROM stock_holdings"
        cursor.execute(query)

        count = cursor.fetchone()[0]

        cursor.close()
        conn.close()
    except Exception as e:
        print(e)
        count = 0

    return count

async def get_portfolio_stock(db_path: str = "stock_tracking_db.sqlite") -> list:
# return [{'ticker': '003720'}, {'ticker': '005930'}, {'ticker': '036570'}, {'ticker': '092200'}]
    """현재 보유 중인 포트폴리오 종목 리스트를 반환합니다."""
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row  # 결과를 딕셔너리 형태로 반환
        cursor = conn.cursor()

        query = "SELECT ticker FROM stock_holdings"
        cursor.execute(query)

        stocks = cursor.fetchall()

        cursor.close()
        conn.close()
    except Exception as e:
        print(e)
        stocks = []

    return [dict(stock) for stock in stocks]

async def sell_real_stock(ticker: str):
    try:
        # 실제 계좌 매매 함수 호출(비동기)
        from trading.domestic_stock_trading import AsyncTradingContext
        async with AsyncTradingContext() as trading:
            # 비동기 매도 실행
            trade_result = await trading.async_sell_stock(stock_code=ticker)

        if trade_result['success']:
            print(f"{ticker} 매도 성공: {trade_result}")
        else:
            print(f"{ticker} 매도 실패: {trade_result}")
        return trade_result['success']
    except Exception as e:
        print(e)
        print(f"에러 {ticker} 매도 실패")
        return False


async def sell_stock(stock_data: Dict[str, Any], sell_reason: str) -> bool:
    """
    주식 매도 처리

    Args:
        stock_data: 매도할 종목 정보
        sell_reason: 매도 이유

    Returns:
        bool: 매도 성공 여부
    """
    from datetime import datetime
    conn = None  # conn을 try 블록 외부에서 초기화
    try:
        ticker = stock_data.get('ticker', '')
        company_name = stock_data.get('company_name', '')
        buy_price = stock_data.get('buy_price', 0)
        buy_date = stock_data.get('buy_date', '')
        current_price = stock_data.get('current_price', 0)
        scenario_json = stock_data.get('scenario', '{}')

        # 수익률 계산
        profit_rate = ((current_price - buy_price) / buy_price) * 100

        # 보유 기간 계산 (일수)
        buy_datetime = datetime.strptime(buy_date, "%Y-%m-%d %H:%M:%S")
        now_datetime = datetime.now()
        holding_days = (now_datetime - buy_datetime).days

        # 현재 시간
        now = now_datetime.strftime("%Y-%m-%d %H:%M:%S")

        db_path = "stock_tracking_db.sqlite"
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # 매매 내역 테이블에 추가
        cursor.execute(
            """
            INSERT INTO trading_history
            (ticker, company_name, buy_price, buy_date, sell_price, sell_date, profit_rate, holding_days, scenario)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ticker,
                company_name,
                buy_price,
                buy_date,
                current_price,
                now,
                profit_rate,
                holding_days,
                scenario_json
            )
        )

        # 보유종목에서 제거
        cursor.execute(
            "DELETE FROM stock_holdings WHERE ticker = ?",
            (ticker,)
        )

        # 변경사항 저장
        conn.commit()
        return True

    except Exception as e:
        print(e)
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()


async def get_current_stock_price(ticker: str) -> float:
    """주식의 현재 가격을 반환하는 더미 함수입니다. 실제 구현에서는 API 호출 등을 통해 가격을 가져와야 합니다."""
    from trading.domestic_stock_trading import DomesticStockTrading

    # 1. 초기화
    trader = DomesticStockTrading()

    # 2. 연동테스트 - 현재가 조회
    # print("\n=== 1. 현재가 조회 (연동 테스트) ===")
    price_info = trader.get_current_price(ticker)  # 알에프텍
    if price_info:
        # print(f"종목명: {price_info['stock_name']}")
        # print(f"현재가: {price_info['current_price']:,}원")
        # print(f"등락률: {price_info['change_rate']:+.2f}%")
        return price_info['current_price']
    return None

async def get_trading_data():
    """
    트레이딩 데이터를 가져옴

    Returns:
        portfolio
    """
    from trading.domestic_stock_trading import DomesticStockTrading
    try:
        trader = DomesticStockTrading()

        portfolio = trader.get_portfolio(error=True)
        print(portfolio)

        return portfolio
    except Exception as e:
        print(e)
    return None

async def get_account():
    from trading.domestic_stock_trading import DomesticStockTrading
    try:
        trader = DomesticStockTrading()

        account = trader.get_account_summary()
        print(account)

        return account
    except Exception as e:
        print(e)
    return None



async def check_stop_loss_triggered(db_path: str = "stock_tracking_db.sqlite"):
    """현재 가격이 손절가 이하인지 확인합니다."""
    import time
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row  # 결과를 딕셔너리 형태로 반환
    cursor = conn.cursor()

    query = "SELECT * FROM stock_holdings"
    cursor.execute(query)

    real_portfolio = await get_trading_data()
    if real_portfolio is None:
        print("실제 계좌에서 포트폴리오 데이터를 가져오는 데 실패했습니다. 손절 체크를 중단합니다.")
        return
    stock_codes = [item['stock_code'] for item in real_portfolio]

    triggered_stocks = []
    for s in cursor.fetchall():
        # clone stock to dict
        stock = dict(s)

        ticker = stock["ticker"]
        if ticker not in stock_codes:
            print(f"{ticker} 종목은 실제 계좌에 없습니다. 건너뜁니다.")
            continue
        stop_loss = stock["stop_loss"]
        target_price = stock["target_price"]

        # 현재 가격을 가져오는 로직 (예: API 호출)
        current_portpolio = await get_current_stock_price(ticker)
        if current_portpolio is not None:
            print(f"{ticker} 현재가: {current_portpolio}, 손절가: {stop_loss}, 목표가: {target_price}")
            if current_portpolio <= stop_loss:
                # 주가 정보 업데이트
                stock['current_price'] = current_portpolio
                triggered_stocks.append(stock)
                await sell_stock(stock, sell_reason="손절가 도달")
                await sell_real_stock(ticker)
            ''' 잠시 비활성화, canslim 전략과 충돌
            elif current_price >= target_price:
                print(f"{ticker} 종목이 목표가에 도달했으니 매도합니다.")
                # 주가 정보 업데이트
                stock['current_price'] = current_price
                triggered_stocks.append(ticker)
                await sell_stock(stock, sell_reason="목표가 도달")
                await sell_real_stock(ticker)
            '''


        time.sleep(5)  # API 호출 제한을 피하기 위해 약간의 지연 추가
    if triggered_stocks:
        message = f"손절가/목표가 발동된 종목: {format_telegram_trigger_message(triggered_stocks)}"
        current_portpolio = await get_trading_data()
        print(f"현재 포트폴리오: {current_portpolio}")
        message += f"\n\n현재 포트폴리오: {format_for_current_telegram(current_portpolio)}"
        await send_telegram_message(message)

def escape_md(text):
    special = r'_*[]()~`>#+-=|{}.!'
    for ch in special:
        text = text.replace(ch, f'\\{ch}')
    return text

def format_for_current_telegram(data):
    lines = ["📊 **현재 포트폴리오 상태**\n"]
    for item in data:
        emoji = "🔴" if item['profit_rate'] > 0 else "🔵"
        line = f"{emoji} **{item['stock_name']}**: `{item['profit_rate']:+.2f}%` ({item['profit_amount']:+,.0f}원)"
        lines.append(line)
    return "\n".join(lines)

def format_telegram_trigger_message(triggered_stocks):
    lines = ["📢 *손절가 / 목표가 발동*"]

    for s in triggered_stocks:
        emoji = "✅" if "목표" in s["scenario"] else "❌"
        company = escape_md(s["company_name"])
        ticker = escape_md(s["ticker"])
        scenario = escape_md(s["scenario"])

        lines.append(
            f"{emoji} *{company}* \\({ticker}\\)\n"
            f"• 매수가: {s['buy_price']:,}\n"
            f"• 현재가: {s['current_price']:,}\n"
            f"• 목표가: {s['target_price']:,} / 손절가: {s['stop_loss']:,}"
        )

    return "\n\n".join(lines)

async def send_telegram_message(message: str):
    from telegram_bot_agent import TelegramBotAgent
    import os
    from dotenv import load_dotenv

    # env파일 로드
    load_dotenv(dotenv_path=str('./.env'))


    # 텔레그램 설정
    telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHANNEL_ID")

    if not telegram_token:
        raise ValueError("텔레그램 봇 토큰이 필요합니다. 환경 변수 TELEGRAM_BOT_TOKEN 또는 파라미터로 제공해주세요.")

    if not chat_id:
        raise ValueError("텔레그램 채널 ID가 필요합니다. 환경 변수 TELEGRAM_CHANNEL_ID 또는 파라미터로 제공해주세요.")

    telegram_bot = TelegramBotAgent(token=telegram_token)
    # 텔레그램 전송
    success = await telegram_bot.send_message(chat_id, message)

    if success:
        print("포트폴리오 리포트 전송 성공!")
        return True
    else:
        print("포트폴리오 리포트 전송 실패!")
        return False

async def syncup_portfolio_data():
    db_path = "stock_tracking_db.sqlite"
    conn = None  # conn을 try 블록 외부에서 초기화
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # DB에서 종목 가져오기
        cursor.execute("SELECT * FROM stock_holdings")
        db_stocks = cursor.fetchall()
        db_tickers = {stock['ticker'] for stock in db_stocks}
        db_full_data = [dict(stock) for stock in db_stocks]

        # 실제 계좌에서 종목 가져오기
        real_portfolio = await get_trading_data()
        if real_portfolio is None:
            print("실제 계좌에서 포트폴리오 데이터를 가져오는 데 실패했습니다. 동기화를 중단합니다.")
            return
        real_tickers = {item['stock_code'] for item in real_portfolio}
        real_full_data = real_portfolio

        print(f"DB 종목: {db_tickers}")
        print(f"실제 계좌 종목: {real_tickers}")

        # DB에만 있는 종목 찾기 (실제 계좌에는 없음)
        not_in_real_account = db_tickers - real_tickers
        if not_in_real_account:
            print(f"다음 종목들은 실제 계좌에 없으므로 DB에서 삭제합니다: {', '.join(not_in_real_account)}")
            print(db_full_data)
            placeholders = ','.join('?' for _ in not_in_real_account)
            delete_query = f"DELETE FROM stock_holdings WHERE ticker IN ({placeholders})"
            cursor.execute(delete_query, tuple(not_in_real_account))
            conn.commit()
            message = f"실제 계좌에 없어 DB에서 삭제된 종목: {', '.join(not_in_real_account)}"
            await send_telegram_message(message)

        # 실제 계좌에만 있는 종목 찾기 (DB에는 없음)
        not_in_db = real_tickers - db_tickers
        if not_in_db:
            message = f"실제 계좌에 있으나 DB에 없는 종목: {', '.join(not_in_db)}. 수동 추가가 필요합니다."
            await send_telegram_message(message)

        # DB와 실제 계좌의 포트폴리오의 가격 정보 비교
        for stock in real_full_data:
            ticker = stock['stock_code']
            buy_price = stock['avg_price']
            # DB에 해당 종목이 있는지 확인
            db_stock = next((item for item in db_full_data if item['ticker'] == ticker), None)
            if db_stock:
                db_price = db_stock['buy_price']
                db_stop_loss = db_stock['stop_loss']
                ticker_name = stock.get('stock_name', ticker)
                if db_price != buy_price:
                    print(f"{ticker_name}({ticker}) 종목의 가격이 DB({db_price})와 실제 계좌({buy_price})에서 다릅니다. DB를 업데이트합니다.")
                    cursor.execute(
                        "UPDATE stock_holdings SET buy_price = ? WHERE ticker = ?",
                        (buy_price, ticker)
                    )
                    if db_stop_loss and buy_price < db_stop_loss:
                        print(f"{ticker_name}({ticker}) 종목의 가격이 DB의 손절가({db_stop_loss})보다 낮습니다. 손절가도 함께 업데이트합니다.")
                        cursor.execute(
                            "UPDATE stock_holdings SET stop_loss = ? WHERE ticker = ?",
                            (buy_price * 0.95, ticker)
                        )
                    conn.commit()
                    message = f"{ticker_name}({ticker}) 종목의 가격이 DB와 실제 계좌에서 달라서 DB를 업데이트했습니다. 구매 가격: {db_price} -> {buy_price}"
                    await send_telegram_message(message)


        if not not_in_real_account and not not_in_db:
            print("DB와 실제 계좌의 포트폴리오가 일치합니다.")
            await send_telegram_message("DB와 실제 계좌의 포트폴리오가 일치합니다.")

    except sqlite3.Error as e:
        error_message = f"DB 동기화 중 SQLite 오류 발생: {e}"
        print(error_message)
        await send_telegram_message(error_message)
    except Exception as e:
        error_message = f"DB 동기화 중 알 수 없는 오류 발생: {e}"
        print(error_message)
        await send_telegram_message(error_message)
    finally:
        if conn:
            conn.close()
            print("DB 연결이 종료되었습니다.")





async def main():
    # count = await get_portfolio_stock_count()
    # print(f"현재 보유 중인 포트폴리오 종목 갯수: {count}")
    # await check_stop_loss_triggered()
    # r = await get_account()
    # total_cash = r.get('total_cash')
    # msg = f"현재 총 보유 현금: {total_cash:,.0f}원"
    # await send_telegram_message(msg)

    message = "포트폴리오 점검 시작!"
    current_portpolio = await get_trading_data()
    message += f"\n\n현재 포트폴리오: {format_for_current_telegram(current_portpolio)}"
    await send_telegram_message(message)

    # await send_telegram_message('포트폴리오 점검 완료!')

if __name__ == "__main__":
    import sys
    from datetime import datetime
    # 휴일 체크
    from check_market_day import is_market_day

    if not is_market_day():
        current_date = datetime.now().date()  # datetime.now()를 사용
        print(f"오늘({current_date})은 주식시장 휴일입니다. 배치 작업을 실행하지 않습니다.")
        sys.exit(0)

    async def run():
        if len(sys.argv) > 1:
            command = sys.argv[1]
            if command == "stop_loss":
                await check_stop_loss_triggered()
            elif command == "syncup":
                await syncup_portfolio_data()
            else:
                print(f"Unknown command: {command}")
                print("Usage: python my_portfolio.py [stop_loss|syncup]")
        else:
            await main()


    try:
        # asyncio 실행
        asyncio.run(run())
    except Exception as e:
        print(f"에러 발생: {e}")
