# ai_client.py
import asyncio
import re
import aiohttp
import json
import logging
import random
from typing import Any, Dict
from config import MAX_RETRY, HTTP_TIMEOUT

logger = logging.getLogger("StockReporter")

class AIClient:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self.base_url = ""
        self.api_key = ""
        self.model = ""

    def configure(self, base_url: str, api_key: str, model: str):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model

    async def _request_ai(self, url: str, headers: Dict[str, str], payload: Dict[str, Any]) -> str:
        timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT)
        async with self.session.post(url, headers=headers, json=payload, timeout=timeout) as response:
            if response.status == 200:
                result = await response.json()
                return result["choices"][0]["message"]["content"]
            else:
                error_text = await response.text()
                logger.error(f"AI API 에러 [{response.status}]: {error_text}")
                raise aiohttp.ClientResponseError(
                    response.request_info, response.history, status=response.status, message=error_text
                )

    async def fetch_raw_analysis(self, system_instruction: str, user_prompt: str) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        url = self.base_url.rstrip("/")
        # 마인드로직 FactChat 엔드포인트 자동 정규화:
        # /v1/gateway/openai -> /v1/gateway/chat/completions/
        if "mindlogic.ai" in url:
            url = re.sub(r'/v1/gateway/openai/?$', '/v1/gateway', url)
            url = re.sub(r'/chat/completions/?$', '', url)
            url = f"{url.rstrip('/')}/chat/completions/"
        elif url and not url.endswith("/chat/completions"):
            url = f"{url}/chat/completions"

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.1,
            "response_format": {"type": "text"}
        }

        for attempt in range(MAX_RETRY):
            try:
                logger.info(f"AI 모델({self.model}) 분석 요청 중 (시도 {attempt+1})...")
                return await self._request_ai(url, headers, payload)
            except Exception as e:
                if "400" in str(e) or "invalid" in str(e).lower():
                    logger.warning("response_format(json_object) 미지원 서버 감지. 포맷 제거 후 재시도합니다.")
                    payload.pop("response_format", None)
                    try:
                        return await self._request_ai(url, headers, payload)
                    except Exception as retry_e:
                        logger.warning(f"재요청 실패: {retry_e}")
                else:
                    logger.warning(f"AI API 예외 발생 (시도 {attempt+1}/{MAX_RETRY}): {e}")
            
            await asyncio.sleep((2 ** attempt) + random.uniform(0.5, 1.5))
        
        raise RuntimeError("AI 분석 수행 중 오류 발생")

    def parse_json_response(self, raw_text: str) -> Dict[str, Any]:
        match = re.search(r'(\{.*\})', raw_text, re.DOTALL)
        if not match:
            raise ValueError("AI 응답에서 JSON 객체 영역을 찾을 수 없습니다.")
        return json.loads(match.group(0))