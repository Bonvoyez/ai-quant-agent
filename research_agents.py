# research_agents.py
import logging
import json
from typing import Any, Dict, Optional
from ai_client import AIClient
from market_data import MarketDataCollector
from news_service import NewsService

logger = logging.getLogger("StockReporter")

# [신규] 대시보드의 리스크 모드 선택에 따라 AI에게 주는 지시문이 실제로 달라집니다.
RISK_MODE_PROMPT_HINTS = {
    "conservative": (
        "RISK MODE: CONSERVATIVE. Prefer LOWER-volatility, higher-liquidity, more established names. "
        "Avoid speculative/high-beta picks even if their momentum looks strong. Prioritize capital "
        "preservation over maximizing upside."
    ),
    "balanced": (
        "RISK MODE: BALANCED. Balance momentum strength against volatility risk. Avoid the most extreme "
        "speculative names unless momentum is exceptionally clear."
    ),
    "aggressive": (
        "RISK MODE: AGGRESSIVE. Prioritize maximum momentum and realistic upside potential even in "
        "higher-volatility, more speculative names, as long as the data supports it."
    ),
}


class MultiAgentOrchestrator:
    def __init__(self, ai_client: AIClient):
        self.ai = ai_client

    async def execute_pipeline(
        self, history_context: str, cumulative_lessons: str, settings: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        settings = settings or {}
        max_positions = settings.get("max_positions", 3)
        excluded_tickers = settings.get("excluded_tickers", [])
        risk_mode = settings.get("risk_mode", "balanced")
        risk_hint = RISK_MODE_PROMPT_HINTS.get(risk_mode, RISK_MODE_PROMPT_HINTS["balanced"])

        macro_data = await MarketDataCollector.fetch_realtime_metrics()
        news_data = await NewsService.fetch_market_news()

        # 실시간 종목 후보군 (ATR/섹터/실적발표 필터링 + 대시보드 블랙리스트 제외 포함)
        stock_candidates = await MarketDataCollector.fetch_stock_candidates(
            # 섹터별 리더를 포함해 반환하므로 15개만 AI에 주면 대형 우량주와
            # 성장 모멘텀 후보가 함께 살아남지 못한다. 선택 폭만 넓히며 실제
            # 주문 수는 max_positions와 후속 위험 규칙이 계속 제한한다.
            # 주문 규칙은 ETF·대형주까지 전체 유니버스를 비교해야 한다. AI에는 분석
            # 컨텍스트로만 제공되며, 주문 수와 실제 진입은 scheduler의 규칙이 제한한다.
            top_n=80, excluded_tickers=excluded_tickers
        )

        vix_val = macro_data.get("VIX", 0)
        is_high_risk = isinstance(vix_val, float) and vix_val > 25.0

        excluded_note = (
            f"The following tickers are BLACKLISTED by the operator and must NEVER be recommended: "
            f"{', '.join(excluded_tickers)}. "
            if excluded_tickers else ""
        )

        system_instruction = (
            "You are a skeptical, evidence-grounded market research analyst. "
            "You MUST ground every statement in the 'Candidate Universe' data list provided below "
            "(each item includes real price, 1-day % change, 5-day % change, relative volume, ATR%, and sector; "
            "stocks with earnings reports due in the next 2 trading days have ALREADY been excluded from this list). "
            "Do NOT invent or recall stocks from your own training memory that are not in the candidate list. "
            f"{excluded_note}"
            "You DO have access to a 'Recent Recommendation History' section below. Use it only to identify "
            "repeated failure patterns; do not claim it proves causality. "
            f"{risk_hint} "
            "Do not recommend securities, choose positions, predict target prices, or assign confidence scores. "
            "The execution engine owns all entry, sizing, and exit decisions through deterministic, tested risk "
            "rules. Your job is to produce a compact decision journal: identify the dominant regime evidence, "
            "the strongest counter-evidence, and an observable condition that would invalidate a risk-on thesis."
        )

        user_prompt = f"""
[리스크 모드: {risk_mode.upper()}]
{risk_hint}

[제외 대상 종목 (블랙리스트, 절대 추천 금지)]
{', '.join(excluded_tickers) if excluded_tickers else '없음'}

[과거 누적 학습 교훈 (계속 진화함)]
{cumulative_lessons}

[최근 추천 이력 - 실현수익률 및 성공/실패 원인 포함. 아래 종목은 원칙적으로 재추천 금지]
{history_context}

[오늘의 후보 유니버스 - 실시간 모멘텀/변동성/섹터 데이터 (실적발표 임박 종목·블랙리스트는 이미 제외됨)]
{json.dumps(stock_candidates, ensure_ascii=False)}

[현재 시장 데이터 (VIX 25 이상이면 하락장 주의)]
VIX: {vix_val}
Sector ETFs: {json.dumps(macro_data.get('Sector_ETFs', {}))}
고위험 장세 여부: {is_high_risk}

[금일 주요 뉴스]
{json.dumps(news_data, ensure_ascii=False)}

출력 JSON 형식:
{{
  "market_regime_hypothesis": "RISK_ON | RISK_OFF | MIXED",
  "evidence": ["후보/거시 데이터 숫자를 인용한 근거 최대 2개"],
  "counter_evidence": ["반증 또는 데이터 한계 최대 2개"],
  "invalidation_condition": "향후 관찰 가능한 무효화 조건 한 줄",
  "research_note": "사람이 검토할 2문장 이내의 요약"
}}
"""
        raw_report = await self.ai.fetch_raw_analysis(system_instruction, user_prompt)
        report = self.ai.parse_json_response(raw_report)

        # 모델은 리서치 기록만 만들며 매수 종목·주문·포지션 크기를 결정하지 않는다.
        # 예상과 다른 형식의 응답도 실행 로직에는 영향을 주지 않도록 정상화한다.
        report["evidence"] = report.get("evidence") if isinstance(report.get("evidence"), list) else []
        report["counter_evidence"] = report.get("counter_evidence") if isinstance(report.get("counter_evidence"), list) else []

        # AI가 아닌 시스템이 직접 수집한 신뢰 가능한 후보 메타데이터(ATR%, 섹터)를
        # scheduler.py가 손절/목표가 재계산, 섹터 필터링, 자동 보충에 쓸 수 있도록 함께 반환합니다.
        # dict는 stock_candidates가 이미 모멘텀 순으로 정렬돼 있으므로, 이 순서를 그대로 보존합니다.
        report["_candidates_meta"] = {c["ticker"]: c for c in stock_candidates}

        return report
