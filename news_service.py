# news_service.py
import aiohttp
import logging
from typing import Dict, List, Any

logger = logging.getLogger("StockReporter")

class NewsService:
    @staticmethod
    async def fetch_market_news() -> List[Dict[str, str]]:
        """야후 파이낸스 API를 활용한 실시간 글로벌 금융 뉴스 수집"""
        logger.info("야후 파이낸스 실시간 뉴스 데이터 수집 중...")
        url = "https://query2.finance.yahoo.com/v1/finance/search?q=market&quotesCount=0&newsCount=5"
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=15) as response:
                    if response.status == 200:
                        data = await response.json()
                        news_items = data.get("news", [])
                        
                        result = []
                        for item in news_items:
                            result.append({
                                "source": item.get("publisher", "Yahoo Finance"),
                                "title": item.get("title", "No Title"),
                                "link": item.get("link", "")
                            })
                        return result
                    else:
                        logger.warning(f"뉴스 수집 실패 상태코드: {response.status}")
        except Exception as e:
            logger.error(f"실시간 뉴스 수집 중 예외 발생: {e}")
        
        # 수집 실패 시 AI에게 자체 검색을 지시하는 fallback 메시지
        return [{
            "source": "System",
            "title": "실시간 뉴스 피드 수집 지연.",
            "impact": "AI 모델이 직접 최신 웹 검색(Web Search) 기능을 활용해 오늘 개장 전 가장 핫한 호재를 찾아 반영할 것."
        }]