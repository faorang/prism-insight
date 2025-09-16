"""
빠른 테스트 스크립트 - 핵심 기능만 간단히 테스트

사용법:
python quick_test.py [buy|sell|portfolio] [--mode demo|real]
python quick_test.py [buy|sell|portfolio] [demo|real]  # 간단한 형태
"""

import asyncio
import sys
import os
import logging
import argparse

# 상위 디렉토리의 trading 모듈 import
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trading.domestic_stock_trading import AsyncTradingContext

# 간단한 로깅 설정
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def parse_arguments():
    """명령행 인자 파싱"""
    parser = argparse.ArgumentParser(
        description="주식 거래 빠른 테스트",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
사용 예시:
  python quick_test.py portfolio              # 모의투자로 포트폴리오 조회
  python quick_test.py portfolio --mode demo  # 모의투자로 포트폴리오 조회
  python quick_test.py buy --mode real        # 실전투자로 매수 (주의!)
  python quick_test.py sell real              # 실전투자로 매도 (주의!)
        """,
    )

    parser.add_argument(
        "command",
        choices=["buy", "sell", "portfolio"],
        help="실행할 명령 (buy: 매수, sell: 매도, portfolio: 포트폴리오 조회)",
    )

    parser.add_argument(
        "--mode",
        choices=["demo", "real"],
        default="demo",
        help="거래 모드 (demo: 모의투자, real: 실전투자, 기본값: demo)",
    )

    # 위치 인자로도 mode 받을 수 있도록 (하위 호환성)
    parser.add_argument(
        "mode_pos",
        nargs="?",
        choices=["demo", "real"],
        help="거래 모드 (위치 인자, --mode와 동일)",
    )

    args = parser.parse_args()

    # 위치 인자로 mode가 주어진 경우 우선 적용
    if args.mode_pos:
        args.mode = args.mode_pos

    return args


async def quick_portfolio_check(mode="demo"):
    """포트폴리오 빠른 조회"""
    print(f"📊 포트폴리오 조회 중... (모드: {mode})")

    async with AsyncTradingContext(mode=mode) as trader:
        portfolio = await asyncio.to_thread(trader.get_portfolio)
        summary = await asyncio.to_thread(trader.get_account_summary)

        print(f"\n💼 보유 종목: {len(portfolio)}개")

        if summary:
            print(f"💰 총평가: {summary.get('total_eval_amount', 0):,.0f}원")
            print(f"📈 총손익: {summary.get('total_profit_amount', 0):+,.0f}원")
            print(f"📊 수익률: {summary.get('total_profit_rate', 0):+.2f}%")

        for i, stock in enumerate(portfolio[:3]):
            print(
                f"  {i + 1}. {stock['stock_name']}: {stock['quantity']}주 ({stock['profit_rate']:+.2f}%)"
            )

        if len(portfolio) > 3:
            print(f"  ... 외 {len(portfolio) - 3}개 종목")


async def quick_buy_test(stock_code="061040", amount=10000, mode="demo"):
    """빠른 매수 테스트"""
    print(f"💳 {stock_code} 매수 테스트 중... (금액: {amount:,}원, 모드: {mode})")

    if mode == "real":
        print("⚠️ 실전투자 모드입니다! 실제 매매가 발생합니다!")
        confirmation = input("정말 실전투자로 매수하시겠습니까? (yes/no): ")
        if confirmation.lower() != "yes":
            print("매수가 취소되었습니다.")
            return {"success": False, "message": "사용자 취소"}

    async with AsyncTradingContext(mode=mode, buy_amount=amount) as trader:
        result = await trader.async_buy_stock(stock_code, timeout=20.0)

        if result["success"]:
            print(f"✅ 매수 성공!")
            print(f"   종목: {result['stock_code']}")
            print(f"   수량: {result['quantity']}주")
            print(f"   현재가: {result['current_price']:,}원")
            print(f"   총액: {result['total_amount']:,}원")
        else:
            print(f"❌ 매수 실패: {result['message']}")

        return result


async def quick_sell_test(stock_code="061040", mode="demo"):
    """빠른 매도 테스트"""
    print(f"💸 {stock_code} 매도 테스트 중... (모드: {mode})")

    if mode == "real":
        print("⚠️ 실전투자 모드입니다! 실제 매매가 발생합니다!")
        confirmation = input("정말 실전투자로 매도하시겠습니까? (yes/no): ")
        if confirmation.lower() != "yes":
            print("매도가 취소되었습니다.")
            return {"success": False, "message": "사용자 취소"}

    async with AsyncTradingContext(mode=mode) as trader:
        result = await trader.async_sell_stock(stock_code, timeout=20.0)

        if result["success"]:
            print(f"✅ 매도 성공!")
            print(f"   종목: {result['stock_code']}")
            print(f"   수량: {result['quantity']}주")
            print(f"   예상금액: {result['estimated_amount']:,}원")
            if "profit_rate" in result:
                print(f"   수익률: {result['profit_rate']:+.2f}%")
        else:
            print(f"❌ 매도 실패: {result['message']}")

        return result


async def main():
    """메인 함수"""
    try:
        args = parse_arguments()
    except SystemExit:
        return

    mode = args.mode
    command = args.command

    # 모드별 표시
    mode_emoji = "🟢" if mode == "demo" else "🔴"
    mode_text = "모의투자" if mode == "demo" else "실전투자"

    print(f"🚀 빠른 테스트 시작 ({mode_emoji} {mode_text})")
    print("=" * 40)

    if mode == "real":
        print("⚠️ 경고: 실전투자 모드입니다!")
        print("⚠️ 실제 매매가 발생할 수 있습니다!")
        print("=" * 40)

    try:
        if command == "portfolio":
            await quick_portfolio_check(mode)

        elif command == "buy":
            await quick_buy_test("061040", 10000, mode)  # 알에프텍 1만원

        elif command == "sell":
            await quick_sell_test("061040", mode)  # 알에프텍 전량매도

    except Exception as e:
        logger.error(f"테스트 중 오류: {e}")

    print(f"\n✅ 테스트 완료 ({mode_text})")


def show_usage():
    """사용법 표시"""
    print("🚀 빠른 테스트 스크립트")
    print("=" * 40)
    print("사용법:")
    print("  python quick_test.py [명령] [모드]")
    print()
    print("명령:")
    print("  portfolio - 포트폴리오 조회")
    print("  buy       - 알에프텍 1만원 매수")
    print("  sell      - 알에프텍 전량 매도")
    print()
    print("모드:")
    print("  demo - 모의투자 (기본값, 안전)")
    print("  real - 실전투자 (⚠️ 실제 매매 발생!)")
    print()
    print("예시:")
    print("  python quick_test.py portfolio")
    print("  python quick_test.py portfolio demo")
    print("  python quick_test.py buy --mode demo")
    print("  python quick_test.py sell --mode real")


if __name__ == "__main__":
    # 인자 없이 실행된 경우 사용법 표시
    if len(sys.argv) == 1:
        show_usage()
    else:
        asyncio.run(main())
