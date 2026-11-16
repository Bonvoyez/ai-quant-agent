# main.py
import asyncio
import os
import aiohttp
import logging
from aiohttp import web
from config import LLM_BASE_URL, LLM_API_KEY, LLM_MODEL, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
from scheduler import DailyScheduler
from telegram_service import TelegramService
from ai_client import AIClient
from dashboard_server import create_dashboard_app

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("StockReporter")

def validate_env() -> None:
    is_paper = os.getenv("STRATEGY_MODE", "").lower() == "paper" or os.getenv("ROTATION_DRY_RUN", "1") != "0"
    if is_paper:
        logger.info("🧪 [Paper Trading Mode Detected] 외부 증권사 및 텔레그램 필수 자격증명 검증을 건너뜁니다.")
        return

    required = {
        "LLM_BASE_URL (or AI_BASE_URL)": LLM_BASE_URL,
        "LLM_API_KEY (or AI_API_KEY)": LLM_API_KEY,
        "TELEGRAM_TOKEN": TELEGRAM_TOKEN,
        "TELEGRAM_CHAT_ID": TELEGRAM_CHAT_ID,
        "TOSS_APP_KEY": os.getenv("TOSS_APP_KEY", ""),
        "TOSS_APP_SECRET": os.getenv("TOSS_APP_SECRET", ""),
        "TOSS_ACCOUNT_NO": os.getenv("TOSS_ACCOUNT_NO", ""),
        "DASHBOARD_TOKEN": os.getenv("DASHBOARD_TOKEN", ""),
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise RuntimeError("환경변수가 누락되었습니다.\n" + "\n".join(missing))

async def main() -> None:
    logger.info("=" * 60)
    logger.info("Stock Report Bot 구동 (완전 자율형 AI 퀀트 시스템 - 테스트/디버그 모드)")
    logger.info("=" * 60)

    validate_env()
    
    async with aiohttp.ClientSession() as session:
        # 서비스 객체들에 세션 및 설정 주입
        scheduler = DailyScheduler(session)
        scheduler.telegram.configure(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID)
        scheduler.ai.configure(LLM_BASE_URL, LLM_API_KEY, LLM_MODEL)

        # [신규] 대시보드 웹서버를 스케줄러와 같은 이벤트 루프 안에서 백그라운드로
        # 구동합니다. site.start()는 서버를 시작만 하고 즉시 반환하므로, 이후
        # scheduler.run()이 메인 루프로 계속 동작하는 동안 웹서버도 함께 응답합니다.
        # [수정] 0.0.0.0(모든 인터페이스) 대신 127.0.0.1(로컬)에만 바인딩합니다.
        # 도메인/공인 IP로 접속하려면 이 서버와 같은 머신에서 nginx나 Caddy 같은
        # 리버스 프록시를 띄워 443(HTTPS)을 공인으로 열고 127.0.0.1:DASHBOARD_PORT로
        # 전달(proxy_pass)하도록 구성하세요. 평문 HTTP로 직접 공인 노출하면 토큰과
        # 계좌 관련 데이터가 네트워크 경로상에서 그대로 노출될 수 있습니다.
        dashboard_app = create_dashboard_app()
        runner = web.AppRunner(dashboard_app)
        await runner.setup()
        dashboard_port = int(os.getenv("DASHBOARD_PORT", "8899"))
        dashboard_host = os.getenv("DASHBOARD_HOST", "127.0.0.1")
        site = web.TCPSite(runner, dashboard_host, dashboard_port)
        await site.start()
        logger.info(f"📊 대시보드 서버 시작: {dashboard_host}:{dashboard_port} (리버스 프록시로 HTTPS 연결 권장)")

        await scheduler.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Stock Report Bot 종료 (사용자 인터럽트)")
    except Exception as e:
        logger.exception(f"치명적인 예외 발생으로 인한 종료: {e}")