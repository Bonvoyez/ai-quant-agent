# toss_service.py
import aiohttp
import logging
import json
import uuid
from typing import Optional
from broker import BaseBroker

logger = logging.getLogger("StockReporter")


class TossInvestService(BaseBroker):
    def __init__(self, session: aiohttp.ClientSession, client_id: str, client_secret: str, account_seq: str):
        self.session = session
        self.client_id = client_id
        self.client_secret = client_secret
        # [핵심 수정] 지금까지 여기 들어오던 값(.env의 TOSS_ACCOUNT_NO)은 화면에 보이는
        # "진짜 계좌번호"(예: 1234567890)였습니다. 하지만 X-Tossinvest-Account 헤더에는
        # 이 번호가 아니라 GET /api/v1/accounts가 반환하는 내부 식별자 accountSeq(보통
        # "1"처럼 아주 짧은 값)를 넣어야 합니다. 이 둘의 혼동이 "account-not-found"의
        # 실제 원인이었습니다. 생성자에 들어온 값은 "매칭용 힌트"로만 보관하고, 실제
        # 헤더에 쓸 accountSeq는 최초 요청 시 서버에 물어봐서 자동으로 알아냅니다.
        self._account_hint = str(account_seq or "").strip()
        self.account_seq = None  # 실제 헤더에 사용할 값 (아래에서 자동으로 채워짐)
        self.token = None
        self.base_url = "https://openapi.tossinvest.com"

    async def _issue_token(self):
        """[공식 규격] OAuth2 토큰 발급"""
        url = f"{self.base_url}/oauth2/token"
        payload = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret
        }
        try:
            async with self.session.post(url, data=payload) as res:
                if res.status == 200:
                    data = await res.json()
                    self.token = data.get("access_token")
                    logger.info("🔑 [토스 API] 액세스 토큰 (재)발급 완료")
                    return True
                else:
                    logger.error(f"❌ [토스 API] 토큰 발급 실패: {await res.text()}")
                    return False
        except Exception as e:
            logger.exception("토큰 발급 중 예외 발생")
            return False

    async def list_accounts(self) -> list:
        """
        [신규] 계좌 목록 조회. accountSeq를 알아내는 용도이며, 계좌 식별 이전 단계이므로
        X-Tossinvest-Account 헤더 없이 토큰만으로 호출합니다.
        """
        if not self.token:
            await self._issue_token()

        url = f"{self.base_url}/api/v1/accounts"
        headers = {"Authorization": f"Bearer {self.token}"}
        try:
            async with self.session.get(url, headers=headers) as res:
                res_text = await res.text()
                try:
                    res_json = json.loads(res_text)
                except json.JSONDecodeError:
                    res_json = {}
                if res.status != 200:
                    logger.error(f"❌ [토스 API] 계좌 목록 조회 실패: {res_text}")
                    return []
                return res_json.get("result", []) or []
        except Exception:
            logger.exception("계좌 목록 조회 중 예외 발생")
            return []

    async def _resolve_account_seq(self) -> Optional[str]:
        """
        [신규] GET /api/v1/accounts로 실제 accountSeq를 찾습니다. 계좌가 여러 개면
        생성자에 넘어온 힌트(.env의 TOSS_ACCOUNT_NO, 화면상 계좌번호일 가능성이 높음)와
        매칭을 시도하고, 매칭이 안 되면 첫 번째 계좌를 쓰면서 경고를 남깁니다.
        """
        accounts = await self.list_accounts()
        if not accounts:
            logger.error(
                "❌ 계좌 목록이 비어있습니다. client_id/client_secret 또는 허용 IP 설정을 다시 확인해주세요."
            )
            return None

        if len(accounts) == 1:
            resolved = str(accounts[0].get("accountSeq"))
            logger.info(f"🔎 [계좌 자동탐색] 계좌가 1개뿐이라 자동 선택: accountSeq={resolved}")
            return resolved

        hint_digits = "".join(ch for ch in self._account_hint if ch.isdigit())
        for acc in accounts:
            # [수정] 실제 응답 필드명은 accountNumber가 아니라 accountNo였습니다.
            acc_no_digits = "".join(ch for ch in str(acc.get("accountNo", "")) if ch.isdigit())
            if hint_digits and hint_digits == acc_no_digits:
                resolved = str(acc.get("accountSeq"))
                logger.info(f"🔎 [계좌 자동탐색] .env의 계좌번호와 일치하는 계좌를 찾았습니다: accountSeq={resolved}")
                return resolved

        resolved = str(accounts[0].get("accountSeq"))
        logger.warning(
            f"⚠️ [계좌 자동탐색] .env의 TOSS_ACCOUNT_NO('{self._account_hint}')와 정확히 일치하는 "
            f"계좌를 찾지 못해 첫 번째 계좌(accountSeq={resolved})를 사용합니다. 계좌가 여러 개라면 "
            f"TOSS_ACCOUNT_NO에 정확한 accountSeq 값을 직접 넣어주는 것을 권장합니다."
        )
        return resolved

    async def _request(self, method: str, endpoint: str, **kwargs):
        """API 요청 전송 공통 메서드"""
        if not self.token:
            await self._issue_token()

        # [핵심 수정] account_seq가 아직 없으면(최초 호출), 실제 accountSeq를 먼저 조회합니다.
        # 기존 버그: 조회가 실패(예: rate-limit)해도 `or ""`로 빈 문자열을 캐싱해버려서,
        # 그 이후 모든 요청이 "실패했었다"는 사실을 영원히 기억하며 계좌 헤더 없이 나갔습니다.
        # 실패 시엔 None으로 남겨둬서 다음 호출이 다시 조회를 시도하도록 고쳤습니다.
        if self.account_seq is None:
            resolved = await self._resolve_account_seq()
            if resolved:
                self.account_seq = resolved

        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self.token}"
        if self.account_seq:
            headers["X-Tossinvest-Account"] = str(self.account_seq)
        kwargs["headers"] = headers

        url = f"{self.base_url}{endpoint}"

        try:
            async with self.session.request(method, url, **kwargs) as res:
                res_text = await res.text()
                try:
                    res_json = json.loads(res_text)
                except json.JSONDecodeError:
                    res_json = {}

                error_code = res_json.get("error", {}).get("code")
                is_expired = res.status in [401, 403] or error_code == "expired-token"
                is_account_not_found = error_code == "account-not-found"

                if is_expired:
                    logger.warning("🔄 [토스 API] 토큰 만료 감지. 재발급 시도.")
                    success = await self._issue_token()
                    if success:
                        headers["Authorization"] = f"Bearer {self.token}"
                        kwargs["headers"] = headers
                        async with self.session.request(method, url, **kwargs) as retry_res:
                            return await retry_res.json()

                elif is_account_not_found:
                    # [신규] 자동탐색이 잘못된 계좌를 골랐거나, 캐싱된 accountSeq가 더 이상
                    # 유효하지 않은 경우를 대비한 1회성 재조회+재시도.
                    logger.warning("🔄 [토스 API] account-not-found 감지. accountSeq 재조회 후 재시도.")
                    self.account_seq = None
                    resolved = await self._resolve_account_seq()
                    if resolved:
                        self.account_seq = resolved
                        headers["X-Tossinvest-Account"] = str(self.account_seq)
                        kwargs["headers"] = headers
                        async with self.session.request(method, url, **kwargs) as retry_res:
                            retry_json = await retry_res.json()
                            if retry_res.status != 200:
                                logger.error(f"❌ [토스 API] accountSeq 재조회 후에도 실패: {retry_json}")
                            return retry_json

                if res.status != 200:
                    logger.error(f"❌ [토스 API] 통신 에러: {res_text}")

                return res_json
        except Exception as e:
            logger.exception(f"토스 API 요청 중 예외 발생 ({endpoint})")
            return {}

    async def get_balance(self) -> float:
        """[공식 규격 반영] 가용 잔고(주문가능 금액 USD) 조회"""
        data = await self._request("GET", "/api/v1/buying-power?currency=USD")
        try:
            return float(data.get("result", {}).get("cashBuyingPower", 0.0))
        except Exception:
            return 0.0

    async def get_realtime_price(self, ticker: str) -> Optional[float]:
        """
        [공식 규격 반영] 실시간 현재가 조회

        [수정됨] 조회 실패 시 0.0이 아닌 None을 반환합니다. 0.0을 반환하면 호출부가
        "가격 조회 실패"와 "실제로 가격이 변하지 않음(0% 보합)"을 구분할 수 없어서,
        정산 시 진입가로 대체하며 잘못된 0% 수익률이 기록되는 문제가 있었습니다.
        """
        data = await self._request("GET", f"/api/v1/prices?symbols={ticker}")
        try:
            result_list = data.get("result", [])
            if result_list:
                price = float(result_list[0].get("lastPrice", 0.0))
                return price if price > 0 else None
            return None
        except Exception:
            return None

    async def buy_market_order(self, ticker: str, budget_usd: float) -> bool:
        """[강화된 로직] 소수점 매수 시도 -> 제한 시 정수 주문 자동 전환"""
        price = await self.get_realtime_price(ticker)
        if not price or price <= 0:
            return False

        # 1. 1차 시도: 소수점 주문 (orderAmount)
        payload = {
            "clientOrderId": str(uuid.uuid4()),
            "symbol": ticker,
            "side": "BUY",
            "orderType": "MARKET",
            "orderAmount": str(budget_usd)
        }
        logger.info(f"📤 [1차 매수 시도 - 소수점] Payload: {payload}")

        data = await self._request("POST", "/api/v1/orders", json=payload)

        # 2. 에러 발생 시 처리 (stock-restricted면 정수 주문으로 자동 전환)
        if "error" in data and data["error"].get("code") == "stock-restricted":
            logger.warning(f"⚠️ {ticker} 소수점 거래 제한 감지. 정수 주문(Quantity)으로 전환 시도.")

            qty = int(budget_usd // price)
            if qty < 1:
                logger.error(f"❌ {ticker} 예산(${budget_usd})이 부족하여 1주도 살 수 없습니다.")
                return False

            payload = {
                "clientOrderId": str(uuid.uuid4()),
                "symbol": ticker,
                "side": "BUY",
                "orderType": "MARKET",
                "quantity": str(qty)
            }
            logger.info(f"📤 [2차 매수 시도 - 정수] Payload: {payload}")
            data = await self._request("POST", "/api/v1/orders", json=payload)

        logger.info(f"📥 [매수 주문 응답]: {data}")
        return "error" not in data and "result" in data

    async def get_sellable_quantity(self, ticker: str) -> Optional[float]:
        """[공식 규격] 매도 가능 수량 조회.

        ``None``은 API 조회 실패이고 ``0.0``은 실제 미보유다. 둘을 같은 값으로
        취급하면 일시적인 네트워크 오류만으로 수동 매도로 잘못 기록할 수 있다.
        """
        data = await self._request("GET", f"/api/v1/sellable-quantity?symbol={ticker}")
        try:
            result = data.get("result")
            if not isinstance(result, dict) or "sellableQuantity" not in result:
                return None
            return float(result["sellableQuantity"])
        except Exception:
            return None

    async def sell_market_order(self, ticker: str) -> bool:
        """[공식 규격 반영] 보유 수량 조회 후 정확한 수량으로 매도"""
        sellable_qty = await self.get_sellable_quantity(ticker)
        if sellable_qty is None:
            logger.error("❌ 매도 가능 수량 조회에 실패했습니다. 주문을 보내지 않습니다.")
            return False
        if sellable_qty <= 0:
            logger.error(f"❌ 매도할 수량이 없습니다 (조회값: {sellable_qty})")
            return False

        payload = {
            "clientOrderId": str(uuid.uuid4()),
            "symbol": ticker,
            "side": "SELL",
            "orderType": "MARKET",
            "quantity": f"{sellable_qty:.6f}"
        }
        logger.info(f"📤 [매도 주문 시도] Payload: {payload}")

        data = await self._request("POST", "/api/v1/orders", json=payload)
        logger.info(f"📥 [매도 주문 응답]: {data}")

        return "error" not in data and "result" in data

    async def sell_all_positions(self, tickers: list) -> bool:
        """목록에 있는 종목 전량 매도"""
        success = True
        for ticker in tickers:
            res = await self.sell_market_order(ticker)
            if not res:
                success = False
        return success
