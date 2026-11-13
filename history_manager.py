# history_manager.py
import asyncio
import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional
from utils import atomic_write


class HistoryManager:
    def __init__(self, history_file: str = "history.json"):
        self.history_file = history_file
        self.lock = asyncio.Lock()

        # 누적 학습 교훈(cumulative_lessons)은 history.json(리스트 스키마)과 별개로 저장
        base, ext = os.path.splitext(self.history_file)
        self.lessons_file = f"{base}_lessons{ext or '.json'}"
        self.lessons_lock = asyncio.Lock()

    def load_all_records(self) -> List[Dict[str, Any]]:
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        return data
            except Exception:
                pass
        return []

    def load_last_record(self) -> Dict[str, Any]:
        records = self.load_all_records()
        if records:
            return records[-1]
        return {"date": "", "stocks": []}

    async def append_or_update_history(self, current_date_str: str, stocks_list: List[Dict[str, Any]], cache_data: Dict[str, Any]) -> None:
        """AI가 과거 실패 및 성공 원인을 학습할 수 있도록 메타데이터 확장 적용"""
        history_records = self.load_all_records()

        enriched_stocks = []
        for s in stocks_list:
            ticker = s.get("ticker")
            cache_key = f"{ticker}_{current_date_str}"
            actual_return = cache_data.get(cache_key)

            # [수정] 기존에는 수익률 구간(< -2% / > 3%)에 따라 "거시경제 금리 역풍",
            # "기관 수급 유입" 같은 고정 문장을 실제 원인 분석 없이 기계적으로 찍어냈습니다.
            # 이 문장은 그 종목의 실제 하락/상승 이유와 무관한 허구였는데, 그대로
            # build_history_context / get_recent_history_text를 통해 AI 프롬프트에
            # "과거 성공/실패 원인"으로 주입되고 있었습니다. AI는 이걸 진짜 인과관계로
            # 받아들여 존재하지 않는 패턴을 학습(new_cumulative_lesson)할 위험이 있었습니다.
            # 원인을 실제로 알 수 없는 이상 None으로 두는 것이 거짓 신호를 섞는 것보다 낫습니다.
            fail_reason = None
            success_reason = None

            enriched_stocks.append({
                "ticker": ticker,
                "reason": s.get("reason"),
                "confidence": s.get("confidence"),
                "actual_return": actual_return,
                "success_cause": success_reason,
                "fail_cause": fail_reason,
                "recommended_at": current_date_str,
                # 진입 당시의 모멘텀·변동성·오프닝 확인값을 보존해, 미래의
                # 성과 검증이 LLM 문장 대신 실제 관측치로 이루어지게 한다.
                "strategy_metadata": s.get("strategy_metadata"),
            })

        new_record = {
            "date": current_date_str,
            "stocks": enriched_stocks
        }

        updated = False
        for i, item in enumerate(history_records):
            if item.get("date") == current_date_str:
                history_records[i] = new_record
                updated = True
                break
        if not updated:
            history_records.append(new_record)

        cutoff_date = datetime.now().timestamp() - (30 * 86400)
        filtered = []
        for item in history_records:
            try:
                dt = datetime.strptime(item.get("date"), "%Y-%m-%d").timestamp()
                if dt >= cutoff_date:
                    filtered.append(item)
            except Exception:
                continue

        async with self.lock:
            await asyncio.to_thread(atomic_write, self.history_file, filtered)

    def build_history_context(self, cache_data: Dict[str, Any]) -> str:
        records = self.load_all_records()[-10:]
        if not records:
            return "No previous recommendation history available."

        lines = []
        for rec in records:
            date_str = rec.get("date")
            stocks = rec.get("stocks", [])
            briefs = []
            for s in stocks:
                ticker = s.get("ticker")
                cache_key = f"{ticker}_{date_str}"
                ret = cache_data.get(cache_key)
                ret_str = f"{ret:.2f}%" if ret is not None else "N/A"

                cause = f" (Success Cause: {s.get('success_cause')})" if s.get('success_cause') else ""
                if not cause and s.get('fail_cause'):
                    cause = f" (Failure Cause: {s.get('fail_cause')})"

                briefs.append(f"{ticker} (Return: {ret_str}{cause})")

            lines.append(f"- Date: {date_str}, Assets: {', '.join(briefs)}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # [이전에 추가] 중복추천 방지 + 프롬프트용 이력 요약 + 학습교훈 영속화
    # ------------------------------------------------------------------

    def get_recent_tickers(self, days: int = 5) -> List[str]:
        """최근 N거래일 동안 추천된 티커 목록 (최신순 중복제거). scheduler.py의
        코드 레벨 중복추천 필터링에 사용."""
        records = self.load_all_records()[-days:]
        seen: List[str] = []
        for rec in reversed(records):
            for s in rec.get("stocks", []):
                ticker = s.get("ticker")
                if ticker and ticker not in seen:
                    seen.append(ticker)
        return seen

    def get_recent_history_text(self, days: int = 5) -> str:
        """AI 프롬프트에 바로 넣을 수 있는 최근 추천 이력 요약. 저장된
        actual_return / success_cause / fail_cause를 그대로 사용."""
        records = self.load_all_records()[-days:]
        if not records:
            return "과거 추천 이력이 없습니다 (최초 거래일)."

        lines = []
        for rec in records:
            date_str = rec.get("date", "N/A")
            for s in rec.get("stocks", []):
                ticker = s.get("ticker", "N/A")
                reason = s.get("reason", "")
                ret = s.get("actual_return")
                ret_str = f"{ret:.2f}%" if isinstance(ret, (int, float)) else "N/A"

                cause = ""
                if s.get("success_cause"):
                    cause = f" (성공 요인: {s.get('success_cause')})"
                elif s.get("fail_cause"):
                    cause = f" (실패 요인: {s.get('fail_cause')})"

                lines.append(
                    f"- {date_str} | {ticker} | 실현수익률 {ret_str}{cause} | 당시 추천 사유: {reason}"
                )

        return "\n".join(lines)

    async def save_lessons(self, lesson_text: str) -> None:
        """AI가 생성한 new_cumulative_lesson을 별도 파일에 영속 저장."""
        if not lesson_text:
            return
        payload = {
            "cumulative_lessons": lesson_text,
            "updated_at": datetime.now().isoformat()
        }
        async with self.lessons_lock:
            await asyncio.to_thread(atomic_write, self.lessons_file, payload)

    def get_lessons(self, default: str = "") -> str:
        """저장된 누적 학습 교훈을 조회."""
        if os.path.exists(self.lessons_file):
            try:
                with open(self.lessons_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict) and data.get("cumulative_lessons"):
                        return data["cumulative_lessons"]
            except Exception:
                pass
        return default

    # ------------------------------------------------------------------
    # [신규 추가] 실현손익 피드백 루프 + 연패 감지
    # ------------------------------------------------------------------

    async def update_actual_return(self, date_str: str, ticker: str, return_pct: float) -> None:
        """
        [핵심 수정] 실제 매도(익절/손절/강제청산) 시점에 실현수익률을 해당 이력
        레코드에 다시 기록합니다.

        기존 문제: scheduler.py가 append_or_update_history를 호출할 때 항상
        cache_data={}를 넘겨서, actual_return이 영원히 None으로 남아있었고
        AI는 "과거에 얼마나 벌었는지/잃었는지"를 전혀 학습할 수 없었습니다.
        이 메서드를 매도 체결 시점마다 호출해서 그 공백을 메웁니다.
        """
        async with self.lock:
            records = self.load_all_records()
            found = False
            for rec in records:
                if rec.get("date") == date_str:
                    for s in rec.get("stocks", []):
                        if s.get("ticker") == ticker:
                            s["actual_return"] = round(return_pct, 2)
                            # [수정] append_or_update_history와 동일한 이유로, 근거 없는
                            # 고정 원인 문구 자동 삽입을 제거했습니다. 실제 원인을 파악하고
                            # 싶다면 이 지점에서 당시 뉴스/매크로 데이터를 실제로 조회해
                            # 채우는 것이 맞고, 텍스트를 하드코딩하는 것은 오히려 해롭습니다.
                            found = True
                            break
                    break

            if not found:
                logger = __import__("logging").getLogger("StockReporter")
                logger.warning(f"⚠️ 실현손익 기록 실패: {date_str}에 {ticker} 추천 레코드를 찾을 수 없습니다.")
                return

            await asyncio.to_thread(atomic_write, self.history_file, records)

    def get_consecutive_losing_streak(self) -> int:
        """
        [신규] 가장 최근 기록부터 거꾸로 훑어, 그날 추천 종목들의 평균 실현수익률이
        연속으로 마이너스였던 거래일 수를 계산합니다. actual_return이 아직 하나도
        기록되지 않은(매도 전) 날짜를 만나면 그 지점에서 계산을 멈춥니다.
        scheduler.py가 연패 시 베팅 비중을 자동으로 줄이는 서킷브레이커에 사용합니다.
        """
        records = self.load_all_records()
        streak = 0
        for rec in reversed(records):
            stocks = rec.get("stocks", [])
            returns = [s.get("actual_return") for s in stocks if isinstance(s.get("actual_return"), (int, float))]
            if not returns:
                break
            avg_return = sum(returns) / len(returns)
            if avg_return < 0:
                streak += 1
            else:
                break
        return streak
