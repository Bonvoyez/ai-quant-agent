# dashboard_server.py
import os
import json
import hmac
import logging
from pathlib import Path
from aiohttp import web
from settings_manager import SettingsManager
from analytics_manager import analytics_manager

logger = logging.getLogger("StockReporter")

DASHBOARD_TOKEN = os.getenv("DASHBOARD_TOKEN", "")
DASHBOARD_HTML_PATH = Path(__file__).parent / "dashboard.html"
HISTORY_FILE = "history.json"
ACTIVE_TRADES_FILE = "active_trades.json"
POSITIONS_FILE = "breakout_positions.json"

settings_manager = SettingsManager()


def _check_token(request: web.Request) -> bool:
    """
    URL 쿼리스트링의 token 파라미터와 서버에 설정된 DASHBOARD_TOKEN을 비교합니다.
    hmac.compare_digest를 사용해 타이밍 공격을 방지합니다. DASHBOARD_TOKEN
    자체가 비어있으면(설정 안 함) 무조건 거부합니다.
    """
    if not DASHBOARD_TOKEN:
        return False
    token = request.query.get("token", "")
    return hmac.compare_digest(token, DASHBOARD_TOKEN)


def _read_json_file(path: str):
    p = Path(path)
    if not p.exists():
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"❌ 대시보드용 파일 읽기 실패 ({path}): {e}")
        return []


async def handle_history(request: web.Request) -> web.Response:
    if not _check_token(request):
        logger.warning(f"🚫 대시보드 API 인증 실패 (history) - IP: {request.remote}")
        return web.json_response({"error": "unauthorized"}, status=401)
    return web.json_response(_read_json_file(HISTORY_FILE))


async def handle_active_trades(request: web.Request) -> web.Response:
    if not _check_token(request):
        logger.warning(f"🚫 대시보드 API 인증 실패 (active_trades) - IP: {request.remote}")
        return web.json_response({"error": "unauthorized"}, status=401)
    return web.json_response(_read_json_file(ACTIVE_TRADES_FILE))


async def handle_get_settings(request: web.Request) -> web.Response:
    if not _check_token(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    return web.json_response(settings_manager.load())


async def handle_post_settings(request: web.Request) -> web.Response:
    """
    [신규] 대시보드에서 보낸 설정을 저장합니다. 이 값은 scheduler.py가 다음
    사이클 시작 시 읽어서 실제 매매 파라미터와 AI 프롬프트에 반영합니다.
    """
    if not _check_token(request):
        logger.warning(f"🚫 대시보드 설정 변경 인증 실패 - IP: {request.remote}")
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json body"}, status=400)
    saved = await settings_manager.save(body)
    return web.json_response(saved)


async def handle_summary(request: web.Request) -> web.Response:
    if not _check_token(request):
        logger.warning(f"🚫 대시보드 API 인증 실패 (summary) - IP: {request.remote}")
        return web.json_response({"error": "unauthorized"}, status=401)
    
    timeframe = request.query.get("timeframe", "all")
    scheduler = request.app.get("scheduler")
    
    balance_usd = 0.0
    active_positions = {}
    
    if scheduler:
        try:
            if hasattr(scheduler, "broker") and scheduler.broker:
                balance_usd = await scheduler.broker.get_balance()
        except Exception as e:
            logger.debug(f"Could not fetch live broker balance for dashboard: {e}")
            
        if hasattr(scheduler, "breakout_runner") and scheduler.breakout_runner:
            active_positions = scheduler.breakout_runner.active_positions
            
    if not active_positions:
        pos_data = _read_json_file(POSITIONS_FILE)
        if isinstance(pos_data, list):
            for item in pos_data:
                t = item.get("ticker")
                if t:
                    active_positions[t] = item

    summary = analytics_manager.get_dashboard_summary(
        current_balance_usd=balance_usd,
        active_positions=active_positions,
        selected_timeframe=timeframe,
    )
    return web.json_response(summary)


async def handle_dashboard(request: web.Request) -> web.Response:
    if not DASHBOARD_HTML_PATH.exists():
        return web.Response(text="dashboard.html 파일을 찾을 수 없습니다. 같은 폴더에 배치했는지 확인하세요.", status=500)
    return web.FileResponse(DASHBOARD_HTML_PATH)


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


def create_dashboard_app(scheduler=None) -> web.Application:
    app = web.Application()
    if scheduler is not None:
        app["scheduler"] = scheduler

    app.router.add_get("/api/summary", handle_summary)
    app.router.add_get("/api/history", handle_history)
    app.router.add_get("/api/active-trades", handle_active_trades)
    app.router.add_get("/api/settings", handle_get_settings)
    app.router.add_post("/api/settings", handle_post_settings)
    app.router.add_get("/dashboard", handle_dashboard)
    app.router.add_get("/", handle_dashboard)
    app.router.add_get("/health", handle_health)
    return app