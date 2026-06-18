import sqlite3
import json
import asyncio
import os
import sys

# Add project root to path so we can import project modules
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(PROJECT_ROOT)

from tracking.journal import JournalManager

async def main():
    db_path = os.path.join(PROJECT_ROOT, "stock_tracking_db.sqlite")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Data defined based on cut_loss_20260618.log
    stocks_to_recover = [
        {
            "ticker": "088350",
            "company_name": "한화생명",
            "buy_price": 5940.0,
            "buy_date": "2026-06-17 09:48:42",
            "sell_price": 5465.0,
            "sell_date": "2026-06-18 09:15:00",
            "profit_rate": -8.0,
            "holding_days": 1,
            "trigger_type": "갭 상승 모멘텀 상위주",
            "trigger_mode": "morning",
            "sector": "보험",
            "scenario": '{"portfolio_analysis": "현재 보유 종목은 1개로 슬롯 여유는 충분합니다. 보유 업종은 운송장비·부품 1종목뿐이라 보험 업종 신규 편입에 따른 섹터 분산 효과는 있습니다.", "fundamental_check": {"F1_profitability": "통과 - 최근 2개 분기 모두 순이익 흑자이며 2026년 1분기 영업이익이 전년동기 대비 29.5% 증가해 수익성 개선이 확인됩니다.", "F2_balance_sheet": "통과 - 부채비율 974%로 절대 수준은 높지만 보험업 특성상 일반 제조업과 비교가 어렵고 업종 구조상 허용 범주로 보고서에서 설명됩니다.", "F3_growth": "통과 - 2025년 ROE 6.51%로 기준 5%를 상회하고 최근 2년 매출도 10% 이상 성장했습니다.", "F4_business_clarity": "통과 - 보장성보험 확대, 판매채널 강화, 금융계열 시너지라는 사업 모델과 경쟁우위가 명확히 식별됩니다.", "all_passed": true}, "valuation_analysis": "2026E PER 5.84배, 6/17 실시간 기준 PER 8.61배, PBR 0.32배로 보험업 평균 PER 16.09배 대비 30% 이상 저평가입니다. 극단적 고평가 구간은 아닙니다.", "sector_outlook": "보험 섹터는 최근 AI 활용 확대, 망분리 완화 기대, 그룹주 강세와 맞물려 상승 추세입니다. 한화생명은 섹터 상승을 주도한 종목 중 하나로 리더십은 양호합니다.", "buy_score": 8.0, "macro_adjustment": 0, "effective_score": 8.0, "min_score": 6.0, "momentum_signal_count": 4, "additional_confirmation_count": 0, "decision": "진입", "entry_checklist_passed": 6, "rejection_reason": "", "pivot_point": 5800.0, "pivot_buffer_pct": 5.0, "volume_profile_info": "1st Major Resistance: 5800 ~ 6160", "target_price": 6160.0, "buy_limit_price": 6089.0, "stop_loss": 5250.0, "risk_reward_ratio": 0.8, "expected_return_pct": 3.2, "expected_loss_pct": 12.1, "investment_period": "중기", "rationale": "2026년 1분기 실적 개선과 ROE 6.5%, 업종 대비 큰 할인으로 펀더멘털 베이스는 통과입니다. 외국인·기관이 최근 3거래일 연속 동반 순매수했고 갭 상승과 거래대금 확대가 모멘텀을 뒷받침합니다. 다만 오전장 급등으로 현재가 5970 기준 손절을 5250에 두면 손실폭이 12.1%로 시스템 허용 범위를 넘는다는 점이 가장 큰 불확실성이며, 보고서 종가 기준 5550에서는 유효했던 셋업이 실시간 가격 상승으로 악화됐습니다.", "sector": "보험", "market_condition": "strong_bull - KOSPI는 20일선 위에 있고 최근 2주 수익률도 강세이나, 6/17 오전장은 미완성 데이터라 전일 종가 기준으로 판단했습니다.", "max_portfolio_size": 8, "journal_reflection": {"referenced": true, "recent_exit_caution": "갭 상승 모멘텀 상위주 트리거의 과거 스킵 정확도는 49%에 불과해 단순 회피보다 현재 셋업 자체의 손절 가능성·R/R을 더 중시했습니다.", "applied_lessons": "과거 트리거 통계는 buy_score에 반영하지 않고, 현재 보고서 기준의 모멘텀 강도는 인정하되 실시간 가격 상승으로 손절폭이 시스템 한도를 초과하는지 별도로 검증하는 데 사용했습니다."}, "trading_scenarios": {"key_levels": {"primary_support": 5250, "secondary_support": 5000, "primary_resistance": 5800, "secondary_resistance": 6160, "volume_baseline": "최근 20일 평균 대비 대략 2배 이상 거래 시 강한 수급으로 판단"}, "sell_triggers": ["익절 마일스톤: 목표가·주요 저항선 도달은 1차 마일스톤이며 자동 매도 트리거가 아닙니다. parabolic/strong_bull/moderate_bull regime이면 즉시 매도 금지 — trailing stop으로 전환해 추세 지속 시 보유. sideways/moderate_bear/strong_bear regime에서만 도달 즉시 전량 매도", "추세 약화 (multi-condition AND): 종가 기준 ① 20일선 이탈 ② 거래량 평균 이상 동반 ③ 섹터/시장 동반 약세 — 이 중 2개 이상 동시 충족 시 전량 매도", "하드 스탑: 종가 기준 stop_loss 이탈 시에만 전량 매도. 장중 wick(intraday low)으로 일시 이탈한 것은 매도 사유로 인정하지 않음", "오닐 절대 룰: 종가 기준 -7% 이상 손실 도달 시 무조건 전량 매도", "시간 점검 (트리거 아님): 보유 N거래일 경과는 자동 매도 트리거가 아니라 추세 점검 시점일 뿐. 박스권 횡보가 종가·거래량 모두에서 명확히 확인될 때에만 매도 검토"], "hold_conditions": ["종가가 20일선 위에 있고 외국인·기관 합산 수급이 순매수 기조를 유지할 것", "보험 섹터 상대강도가 유지되고 한화그룹주 모멘텀이 급격히 꺾이지 않을 것", "5800 돌파 이후에도 거래량이 평균 이상 유지되며 종가가 돌파 구간 아래로 깊게 밀리지 않을 것"], "portfolio_context": "보험 업종 신규 편입으로 포트폴리오 분산에는 기여하지만, 현재 실시간 가격에서는 손절 관리가 시스템 규칙 밖으로 넓어져 실행 난도가 높습니다."}, "buy_score_original": 8.0, "effective_score_original": 8.0, "score_adjustment": 0.0, "score_adjustment_reasons": []}'
        },
        {
            "ticker": "064350",
            "company_name": "현대로템",
            "buy_price": 230000.0,
            "buy_date": "2026-06-16 09:49:17",
            "sell_price": 211600.0,
            "sell_date": "2026-06-18 09:15:00",
            "profit_rate": -8.0,
            "holding_days": 2,
            "trigger_type": "갭 상승 모멘텀 상위주",
            "trigger_mode": "morning",
            "sector": "운송장비·부품",
            "scenario": '{"portfolio_analysis": "현재 primary 포트폴리오는 0/10슬롯으로 비어 있어 신규 1슬롯 진입 여력은 충분합니다. 동일 섹터 중복이나 기간 분산 제약도 없습니다.", "fundamental_check": {"F1_profitability": "통과 - 최근 2개 분기 포함 실적 흐름이 영업이익 흑자이며 2026년 1분기 영업이익도 전년동기 대비 증가했습니다.", "F2_balance_sheet": "통과 - 2025년 부채비율 206.39%로 200%를 소폭 상회하지만 업종 평균 대비 과도하다는 근거는 없고 2026E 174.73%로 개선되며 FCF가 강합니다.", "F3_growth": "통과 - 2025년 ROE 30.05%, 2026E 26.55%로 기준 5%를 크게 상회하고 최근 2년 매출 성장도 매우 강합니다.", "F4_business_clarity": "통과 - 방산·철도 중심의 사업 구조와 해외 수주 경쟁력, 현대차그룹 계열 기반이라는 경쟁우위가 보고서에서 명확히 식별됩니다.", "all_passed": true}, "valuation_analysis": "현재 PER 30.19배는 업종 PER 39.29배보다 낮아 업종 평균 대비 극단적 고평가는 아닙니다. 절대 밸류에이션은 높지만 성장 프리미엄을 감안하면 CAN SLIM 관점에서 허용 가능한 범위입니다.", "sector_outlook": "방산 섹터는 유로사토리 참가, 대드론 체계 공개, 동종 방산주 동반 강세로 단기 주도 섹터 성격이 강합니다. 현대로템은 한화에어로스페이스, LIG넥스원, 한국항공우주와 함께 핵심 축으로 분류됩니다.", "buy_score": 9.0, "macro_adjustment": 1, "effective_score": 9.0, "min_score": 4.0, "momentum_signal_count": 3, "additional_confirmation_count": 0, "decision": "진입", "entry_checklist_passed": 6, "rejection_reason": "", "pivot_point": 224500.0, "pivot_buffer_pct": 5.0, "volume_profile_info": "1st Major Resistance: 231000 ~ 235000, 2nd Major Resistance: 270000 ~ 311944", "target_price": 270000.0, "buy_limit_price": 240720.0, "stop_loss": 224200.0, "risk_reward_ratio": 1.5, "expected_return_pct": 14.4, "expected_loss_pct": 5.0, "investment_period": "중기", "rationale": "방산·철도 이중 성장축, ROE 30%대, 강한 현금흐름으로 펀더멘털 게이트를 모두 통과했습니다. 금일 갭상승과 방산 섹터 주도 흐름, 52주 고가의 84% 수준 회복, 외국인 우위 수급으로 모멘텀이 살아 있습니다. KOSPI는 20일선 위이면서 최근 2주 강세가 매우 강해 strong_bull regime에 해당하며 이 조합은 지금 진입이 가능한 오닐식 성장주 셋업입니다.", "sector": "운송장비·부품", "market_condition": "strong_bull - KOSPI 8708.88로 20일선 위에 있고 최근 2주 수익률이 +14% 내외로 강합니다. 다만 트리거는 parabolic 후보이나 KOSPI 30일 수익률이 +10% 미만이라 strong_bull로 적용합니다.", "max_portfolio_size": 8, "journal_reflection": {"referenced": true, "recent_exit_caution": "갭 상승 모멘텀 상위주 트리거의 스킵 정확도는 49%로 낮아, 단순 회피보다 현재 셋업의 질을 직접 따지는 쪽이 유리했습니다.", "applied_lessons": "과거 통계가 아주 우세하지 않아도 강한 펀더멘털과 섹터 리더십, 시장 순풍이 동시에 있을 때는 트리거 자체보다 종목의 질을 우선해 판단했습니다."}, "trading_scenarios": {"key_levels": {"primary_support": 203000, "secondary_support": 196000, "primary_resistance": 231000, "secondary_resistance": 235000, "volume_baseline": "20일 평균 거래량 약 647000주"}, "sell_triggers": ["익절 마일스톤: 목표가·주요 저항선 도달은 1차 마일스톤이며 자동 매도 트리거가 아닙니다. parabolic/strong_bull/moderate_bull regime이면 즉시 매도 금지 — trailing stop으로 전환해 추세 지속 시 보유. sideways/moderate_bear/strong_bear regime에서만 도달 즉시 전량 매도", "추세 약화 (multi-condition AND): 종가 기준 ① 20일선 이탈 ② 거래량 평균 이상 동반 ③ 섹터/시장 동반 약세 — 이 중 2개 이상 동시 충족 시 전량 매도", "하드 스탑: 종가 기준 stop_loss 이탈 시에만 전량 매도. 장중 wick(intraday low)으로 일시 이탈한 것은 매도 사유로 인정하지 않음", "오닐 절대 룰: 종가 기준 -7% 이상 손실 도달 시 무조건 전량 매도", "시간 점검 (트리거 아님): 보유 N거래일 경과는 자동 매도 트리거가 아니라 추세 점검 시점일 뿐. 박스권 횡보가 종가·거래량 모두에서 명확히 확인될 때에만 매도 검토"], "hold_conditions": ["종가가 20일선 위를 유지하고 외국인·기관 합산 수급이 3~5거래일 기준 중립 이상이면 보유 지속", "방산 섹터가 시장 대비 상대강도를 유지하고 유로사토리 후속 수주 기대가 훼손되지 않으면 보유 지속", "고점 갱신 후에도 거래량이 급감하지 않고 종가가 stop_loss 위에 있으면 보유 지속"], "portfolio_context": "비어 있는 포트폴리오의 첫 슬롯으로 적합한 strong_bull 국면의 방산 리더 성장주 진입입니다."}, "buy_score_original": 8.0, "effective_score_original": 9.0, "score_adjustment": 0.0, "score_adjustment_reasons": []}'
        }
    ]

    for stock in stocks_to_recover:
        ticker = stock["ticker"]
        company_name = stock["company_name"]
        print(f"Re-journaling {company_name}({ticker})...")

        # 1. Delete previous journal record
        cursor.execute(
            "DELETE FROM trading_journal WHERE ticker = ? AND trade_date = ?",
            (ticker, stock["sell_date"])
        )
        conn.commit()
        print("  Deleted existing journal entry successfully.")

        # 2. Re-create trading_journal with 'Stop loss' as sell_reason
        journal_manager = JournalManager(
            cursor=cursor,
            conn=conn,
            language="ko",
            enable_journal=True
        )

        stock_data = {
            "ticker": ticker,
            "company_name": company_name,
            "buy_price": stock["buy_price"],
            "buy_date": stock["buy_date"],
            "scenario": stock["scenario"],
            "trigger_type": stock["trigger_type"],
            "trigger_mode": stock["trigger_mode"],
            "sector": stock["sector"]
        }

        success = await journal_manager.create_entry(
            stock_data=stock_data,
            sell_price=stock["sell_price"],
            profit_rate=stock["profit_rate"],
            holding_days=stock["holding_days"],
            sell_reason="Stop loss",
            trade_date=stock["sell_date"]
        )

        if success:
            print(f"  Re-created trading_journal with 'Stop loss' successfully.")
        else:
            print(f"  Failed to re-create trading_journal.")

    conn.close()
    print("Re-journaling completed.")

if __name__ == "__main__":
    asyncio.run(main())
