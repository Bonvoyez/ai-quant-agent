# test_toss_api.py
import asyncio
import aiohttp
import os
import logging
from dotenv import load_dotenv
from toss_service import TossInvestService

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("StockReporter")

async def run_tests():
    load_dotenv()
    
    client_id = os.getenv("TOSS_APP_KEY", "")
    client_secret = os.getenv("TOSS_APP_SECRET", "")
    account_seq = os.getenv("TOSS_ACCOUNT_NO", "")

    if not all([client_id, client_secret, account_seq]):
        logger.error("❌ 환경변수 누락.")
        return

    # [신규] "코드는 그대로인데 어제까지 되던 게 갑자기 안 됨" 같은 상황을 잡기 위한
    # 진단 로그. 눈에는 안 보이지만 값 끝에 공백/개행문자가 붙어 있거나, 값 자체가
    # 예상과 다르면 repr()로 바로 드러납니다.
    logger.info(f"🔍 client_id repr: {client_id!r} (길이: {len(client_id)})")
    logger.info(f"🔍 account_seq repr: {account_seq!r} (길이: {len(account_seq)})")
    if account_seq != account_seq.strip():
        logger.warning("⚠️ TOSS_ACCOUNT_NO 값 앞/뒤에 공백 또는 개행문자가 포함되어 있습니다! .env 파일을 확인하세요.")

    logger.info("🚀 토스 API 단독 테스트를 시작합니다...")

    async with aiohttp.ClientSession() as session:
        toss = TossInvestService(
            session=session,
            client_id=client_id,
            client_secret=client_secret,
            account_seq=account_seq
        )

        # [신규] 잔고/시세 조회보다 먼저, 실제 accountSeq가 뭔지부터 확인합니다.
        logger.info("--- [테스트 0] 계좌 목록 조회 (accountSeq 확인) ---")
        accounts = await toss.list_accounts()
        logger.info(f"✅ 조회된 계좌 목록: {accounts}")
        if not accounts:
            logger.error("❌ 계좌 목록이 비어있습니다. 여기서 실패하면 IP 허용목록/키 문제일 가능성이 큽니다.")
        else:
            for acc in accounts:
                logger.info(
                    f"   -> accountNo(화면상 계좌번호): {acc.get('accountNo')} "
                    f"/ accountSeq(헤더에 넣을 값): {acc.get('accountSeq')}"
                )
            logger.info(
                "👉 위 accountSeq 값을 .env의 TOSS_ACCOUNT_NO에 넣어주세요 (화면상 계좌번호가 아닙니다)."
            )
            # [신규] 계좌 목록 조회 API는 초당 1회 제한(rate-limit)이 있습니다. 방금 위에서 이미
            # 조회했으니, 바로 이어서 toss._resolve_account_seq()가 또 호출되지 않도록 여기서
            # 얻은 accountSeq를 클라이언트에 직접 세팅해둡니다 (계좌가 1개인 가장 흔한 경우).
            if len(accounts) == 1:
                toss.account_seq = str(accounts[0].get("accountSeq"))
                logger.info(f"🔧 accountSeq를 바로 설정했습니다: {toss.account_seq}")

        # 1. 잔고 조회
        logger.info("--- [테스트 1] 잔고 조회 ---")
        balance = await toss.get_balance()
        logger.info(f"✅ 현재 달러(USD) 잔고: ${balance}")

        # 2. 현재가 조회
        logger.info("--- [테스트 2] 현재가 조회 (AAPL) ---")
        price = await toss.get_realtime_price("AAPL")
        logger.info(f"✅ AAPL 현재가: ${price}")

        # 3. 시장가 매수 주문 (5달러만 소액 테스트)
        logger.info("--- [테스트 3] 시장가 매수 주문 시도 ($5) ---")
        buy_success = await toss.buy_market_order("AAPL", budget_usd=5.0)
        logger.info(f"✅ 매수 성공 여부: {buy_success}")

        # 4. 전량 매도 주문 (매수가 성공했을 때만 실행하여 현금 복구)
        if buy_success:
            logger.info("--- [테스트 4] 전량 매도 주문 (원상복구) ---")
            sell_success = await toss.sell_market_order("AAPL")
            logger.info(f"✅ 매도 성공 여부: {sell_success}")

        logger.info("🏁 토스 API 통신 테스트가 모두 종료되었습니다.")

if __name__ == "__main__":
    asyncio.run(run_tests())