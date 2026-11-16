# main.py
import asyncio
import os
import signal
import aiohttp
import logging
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo
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
NEW_YORK = ZoneInfo("America/New_York")

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

        # Send Telegram boot notification on system launch
        try:
            dry_run = os.getenv("ROTATION_DRY_RUN", "1") != "0"
            mode_desc = "DRY-RUN / PAPER (Safe Simulation)" if dry_run else "LIVE CAPITAL EXECUTION"
            now_ny = datetime.now(NEW_YORK).strftime("%Y-%m-%d %H:%M:%S EST")
            await telegram.send_message(
                f"🟢 [AI-Quant-Agent Online]\n"
                f"Autonomous quant trading daemon initialized successfully.\n\n"
                f"• Execution Mode: {mode_desc}\n"
                f"• Dashboard: https://danieljheo.duckdns.org\n"
                f"• System Time: {now_ny}"
            )
        except Exception as boot_err:
            logger.debug(f"Failed to send boot Telegram message: {boot_err}")

        # Setup graceful signal handling for SIGTERM / SIGINT (e.g. systemctl restart, kill, Ctrl+C)
        loop = asyncio.get_running_loop()
        stop_event = asyncio.Event()

        def _handle_signal():
            logger.info("Termination signal received (SIGTERM/SIGINT). Initiating graceful shutdown.")
            stop_event.set()

        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, _handle_signal)
            except (NotImplementedError, RuntimeError):
                # Fallback on Windows or non-main threads
                try:
                    signal.signal(sig, lambda s, f: stop_event.set())
                except Exception:
                    pass

        scheduler_task = asyncio.create_task(scheduler.run())
        stop_waiter = asyncio.create_task(stop_event.wait())

        try:
            done, pending = await asyncio.wait(
                [scheduler_task, stop_waiter],
                return_when=asyncio.FIRST_COMPLETED
            )

            if stop_event.is_set():
                logger.info("Trading daemon stopped cleanly by system signal.")
                scheduler_task.cancel()
                try:
                    await scheduler_task
                except (asyncio.CancelledError, Exception):
                    pass
                try:
                    await telegram.send_message(
                        "🛑 [AI-Quant-Agent Stopped]\n"
                        "Trading daemon shut down cleanly by system signal."
                    )
                except Exception as tg_err:
                    logger.debug(f"Failed to send shutdown Telegram alert: {tg_err}")
            else:
                for task in done:
                    if task is scheduler_task and task.exception():
                        exc = task.exception()
                        tb_str = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
                        logger.exception(f"Fatal crash in trading daemon: {exc}")
                        try:
                            alert = (
                                f"🚨 [CRITICAL ALERT: Daemon Crashed]\n"
                                f"The autonomous trading daemon terminated unexpectedly.\n\n"
                                f"• Error Type: {type(exc).__name__}\n"
                                f"• Reason: {exc}\n"
                                f"• Traceback (Last 500 chars):\n{tb_str[-500:]}"
                            )
                            await telegram.send_message(alert)
                        except Exception as tg_err:
                            logger.debug(f"Failed to send crash Telegram alert: {tg_err}")
                        raise exc
        except (KeyboardInterrupt, asyncio.CancelledError):
            logger.info("Trading daemon interrupted.")
            try:
                await telegram.send_message(
                    "🛑 [AI-Quant-Agent Stopped]\n"
                    "Trading daemon shut down cleanly by user signal."
                )
            except Exception as tg_err:
                logger.debug(f"Failed to send shutdown Telegram alert: {tg_err}")
        finally:
            for task in [scheduler_task, stop_waiter]:
                if not task.done():
                    task.cancel()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Stock Report Bot daemon terminated by user interrupt.")
    except Exception as e:
        logger.exception(f"Fatal unhandled exception in main runner: {e}")