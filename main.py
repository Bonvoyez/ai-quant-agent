# main.py
import asyncio
import os
import aiohttp
import logging
import traceback
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
        logger.info("🧪 [Paper Trading Mode Active] MockBroker enabled. Skipping live broker credential requirement.")
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
        raise RuntimeError("Missing required environment variables:\n" + "\n".join(missing))

async def main() -> None:
    logger.info("=" * 60)
    logger.info("AI-Quant-Agent Initialized (Autonomous US Equity Quant System)")
    logger.info("=" * 60)

    validate_env()
    
    async with aiohttp.ClientSession() as session:
        # 서비스 객체들에 세션 및 설정 주입
        scheduler = DailyScheduler(session)
        scheduler.telegram.configure(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID)
        scheduler.ai.configure(LLM_BASE_URL, LLM_API_KEY, LLM_MODEL)

        dashboard_app = create_dashboard_app(scheduler=scheduler)
        runner = web.AppRunner(dashboard_app)
        await runner.setup()
        dashboard_port = int(os.getenv("DASHBOARD_PORT", "8899"))
        dashboard_host = os.getenv("DASHBOARD_HOST", "127.0.0.1")
        site = web.TCPSite(runner, dashboard_host, dashboard_port)
        await site.start()
        logger.info(f"📊 Dashboard Web Server running at http://{dashboard_host}:{dashboard_port} (HTTPS proxy recommended)")

        telegram = scheduler.telegram
        try:
            await scheduler.run()
        except (KeyboardInterrupt, asyncio.CancelledError):
            logger.info("Trading daemon stopped cleanly by user signal.")
            try:
                await telegram.send_message(
                    "🛑 [AI-Quant-Agent Stopped]\n"
                    "Trading daemon shut down cleanly by user signal."
                )
            except Exception as tg_err:
                logger.debug(f"Failed to send shutdown Telegram alert: {tg_err}")
        except Exception as e:
            tb_str = traceback.format_exc()
            logger.exception(f"Fatal crash in trading daemon: {e}")
            try:
                alert = (
                    f"🚨 [CRITICAL ALERT: Daemon Crashed]\n"
                    f"The autonomous trading daemon terminated unexpectedly.\n\n"
                    f"• Error Type: {type(e).__name__}\n"
                    f"• Reason: {e}\n"
                    f"• Traceback (Last 500 chars):\n{tb_str[-500:]}"
                )
                await telegram.send_message(alert)
            except Exception as tg_err:
                logger.debug(f"Failed to send crash Telegram alert: {tg_err}")
            raise

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Stock Report Bot daemon terminated by user interrupt.")
    except Exception as e:
        logger.exception(f"Fatal unhandled exception in main runner: {e}")