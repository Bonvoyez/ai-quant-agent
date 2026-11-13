# config.py
import os
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

# LLM Configuration (OpenAI-compatible standard)
LLM_BASE_URL: str = os.getenv("LLM_BASE_URL") or os.getenv("AI_BASE_URL", "")
LLM_API_KEY: str = os.getenv("LLM_API_KEY") or os.getenv("AI_API_KEY", "")
LLM_MODEL: str = os.getenv("LLM_MODEL") or os.getenv("AI_MODEL", "gpt-4o")

# Backward-compatibility aliases
AI_BASE_URL = LLM_BASE_URL
AI_API_KEY = LLM_API_KEY
AI_MODEL = LLM_MODEL

# Telegram Configuration
TELEGRAM_TOKEN: str = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

HTTP_TIMEOUT: int = int(os.getenv("HTTP_TIMEOUT", "180"))
MAX_RETRY: int = int(os.getenv("MAX_RETRY", "3"))
TELEGRAM_MAX_LEN: int = 4000

KST = ZoneInfo("Asia/Seoul")

# 섹터 ETF 티커 맵핑 (데이터 수집용)
SECTOR_ETF_MAPPING = {
    "Technology": "XLK",
    "Semiconductor": "SOXX",
    "Biotech": "IBB",
    "Finance": "XLF",
    "Healthcare": "XLV",
    "Consumer Discretionary": "XLY",
    "Energy": "XLE",
    "Defense": "ITA",
    "Big Tech": "IYW",
    "S&P 500": "SPY",
}
