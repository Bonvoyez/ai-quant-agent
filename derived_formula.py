"""
[Auto-Generated Mathematical Formula for Small-Cap 10%+ Breakouts]
Generated at: 2026-11-15 00:31:41
Training Stats:
- Precision: 75.00%
- Total Test Signals: 8
- False Positive Rate: 25.00%
- Optimal Threshold: 663.4788

NOTE: Pure Python implementation with ZERO external dependencies (no ML libraries).
Safe to run in resource-constrained environments (Oracle Cloud Free Tier).
"""

import math
from typing import Dict, Any


def _safe_div(a: float, b: float) -> float:
    return a / b if abs(b) > 1e-6 else 1.0


def _safe_sqrt(a: float) -> float:
    return math.sqrt(abs(a))


def _safe_pow(a: float, b: float) -> float:
    try:
        return math.pow(abs(a), b)
    except Exception:
        return 1.0


def _safe_log(a: float) -> float:
    return math.log(abs(a) + 1e-6)


REQUIRED_FEATURE_KEYS = [
    'rvol_prev', 'gap_open_pct', 'condensation_prev', 'bb_width', 'atr_pct',
    'm5_dominance', 'date_numerology', 'ticker_gematria', 'resonance_score',
    'moon_phase', 'is_full_moon', 'is_new_moon', 'moon_fullness',
    'is_mercury_retrograde', 'zodiac_sign', 'celestial_harmony', 'fact_score',
    'rvol_x_condensation', 'fact_x_resonance', 'fact_x_moon', 'celestial_x_rvol',
    'm5_dom_per_gap', 'bb_squeeze_rvol'
]


def _fill_missing_features(f: Dict[str, float]) -> Dict[str, float]:
    filled = dict(f)
    for k in REQUIRED_FEATURE_KEYS:
        if k not in filled:
            filled[k] = 0.0
    return filled


def calculate_breakout_score(f: Dict[str, float]) -> float:
    """
    수식 계산: 점수가 높을수록 당일 +10% 이상 폭등 확률 높음.
    입력 피처 f의 키:
    ['rvol_prev', 'gap_open_pct', 'condensation_prev', 'bb_width', 'atr_pct', 'm5_dominance', 'date_numerology', 'ticker_gematria', 'resonance_score', 'moon_phase', 'is_full_moon', 'is_new_moon', 'moon_fullness', 'is_mercury_retrograde', 'zodiac_sign', 'celestial_harmony', 'fact_score', 'rvol_x_condensation', 'fact_x_resonance', 'fact_x_moon', 'celestial_x_rvol', 'm5_dom_per_gap', 'bb_squeeze_rvol']
    """
    try:
        f = _fill_missing_features(f)
        score = max(_safe_div((-(f['atr_pct'] - abs((f['bb_squeeze_rvol'] - f['bb_width']) * _safe_log(abs(f['fact_score']) + 1e-06)) - abs(_safe_div((f['bb_width'] * f['atr_pct'] + f['ticker_gematria'] * _safe_log(abs(f['rvol_x_condensation']) + 1e-06) * min(f['moon_fullness'] * f['bb_width'], abs(f['gap_open_pct']))) * _safe_log(abs(f['fact_score']) + 1e-06), f['atr_pct']))) * min(f['moon_fullness'], _safe_div(f['atr_pct'], f['ticker_gematria'])) - abs(f['gap_open_pct'])) * _safe_sqrt(abs(min((_safe_log(abs(_safe_log(abs(f['is_new_moon']) + 1e-06)) + 1e-06) + abs(2 * f['moon_fullness'] - _safe_sqrt(abs((f['bb_width'] + _safe_log(abs(f['fact_score']) + 1e-06)) * _safe_log(abs(f['celestial_x_rvol']) + 1e-06))))) * _safe_sqrt(abs(min(f['rvol_prev'] + f['moon_phase'], abs((f['bb_width'] + min(f['fact_score'], f['condensation_prev'])) * abs(f['fact_score']))))), (-2 * f['moon_fullness'] + _safe_log(abs(_safe_log(abs(f['is_new_moon']) + 1e-06)) + 1e-06) + _safe_sqrt(abs((f['bb_width'] + min(f['gap_open_pct'], f['fact_score'])) * _safe_log(abs(f['celestial_x_rvol']) + 1e-06)))) * _safe_sqrt(abs(min(f['atr_pct'] * f['m5_dominance'], f['rvol_prev'] + f['moon_phase']))), f['rvol_prev'] + f['moon_phase']))), min(f['moon_fullness'], _safe_div(f['atr_pct'], f['ticker_gematria']))), f['bb_squeeze_rvol'] + f['bb_width'])
        return float(score)
    except Exception:
        return -999.0


def should_enter_breakout_trade(f: Dict[str, float], threshold: float = 663.4788) -> bool:
    """
    진입 판정 함수:
    1. 독성 재료(유상증자, CB 등)가 있으면 즉시 기각
    2. 수식 점수가 임계값을 초과할 때만 매수 신호(True) 반환
    """
    if f.get("is_toxic_material", 0.0) > 0.5:
        return False
        
    score = calculate_breakout_score(f)
    return score >= threshold
