# telegram_service.py
import asyncio
import random
import aiohttp
import logging
from config import TELEGRAM_MAX_LEN, MAX_RETRY, HTTP_TIMEOUT
from utils import split_message

logger = logging.getLogger("StockReporter")

class TelegramService:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self.token = "" # 환경변수 주입용
        self.chat_id = "" # 환경변수 주입용

    def configure(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id

    async def send_message(self, text: str) -> None:
        if not self.token or not self.chat_id:
            logger.error("Telegram credentials are not configured.")
            return
            
        chunks = split_message(text, TELEGRAM_MAX_LEN)
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        
        # 400 Bad Request 방지를 위해 parse_mode 제거
        payload = {
            "chat_id": self.chat_id
        }
        
        timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT)
        for chunk in chunks:
            payload["text"] = chunk
            for attempt in range(MAX_RETRY):
                try:
                    async with self.session.post(url, json=payload, timeout=timeout) as response:
                        if response.status == 200:
                            logger.info("텔레그램 알림 전송 완료")
                            break
                        else:
                            logger.error(f"텔레그램 전송 실패 상태코드: {response.status}")
                except Exception as e:
                    logger.exception(f"텔레그램 전송 예외 발생 (시도 {attempt+1}/{MAX_RETRY}): {e}")
                
                await asyncio.sleep((2 ** attempt) + random.uniform(0.5, 1.5))