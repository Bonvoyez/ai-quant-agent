"""소액 계좌용 ETF 추세·모멘텀 로테이션. AI는 주문 판단에 사용하지 않는다."""
import asyncio, json, logging, math, os, tempfile
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
import yfinance as yf

logger = logging.getLogger("StockReporter")
# 신호는 고유동성 대표 ETF로만 계산한다. 개별 종목 뉴스/실적 위험을 피하기 위함이다.
SIGNAL_UNIVERSE=["SPY","QQQ","GLD","IEF"]; USE_PROXY_TICKERS=False
TRADE_PROXY={"SPY":"SPLG","QQQ":"QQQM","GLD":"GLDM","IEF":"IEF"}
SMA_TREND=200; ENTRY_BUFFER=1.00; EXIT_BUFFER=.98; MOM_WINDOWS=(63,126,252)
TOP_N=2; VOL_WINDOW=20; TARGET_VOL=.12; MAX_WEIGHT_PER_ASSET=.60; MAX_GROSS=.95
TRAILING_STOP={"SPY":.12,"QQQ":.15,"GLD":.12,"IEF":.07}; COOLDOWN_DAYS=5
REENTRY_NEED_SMA50=True; REBALANCE_WEEKDAY=0; REBALANCE_BAND_PCT=.03; MIN_ORDER_USD=10.
DD_BRAKE_ON=-.12; DD_BRAKE_OFF=-.06; NY=ZoneInfo("America/New_York"); STATE_FILE="rotation_state.json"

def _state():
    try:
        with open(STATE_FILE) as f: return json.load(f)
    except Exception: return {"positions":{},"cooldown":{},"equity_peak":0,"last_rebalance_date":"","last_order_date":"","stall_counter":0}
def _save(x):
    fd,path=tempfile.mkstemp(dir=".",prefix=".rotation-"); os.close(fd)
    with open(path,"w") as f: json.dump(x,f,ensure_ascii=False,indent=2)
    os.replace(path,STATE_FILE)
def compute_signals(prices,held,cooldown):
    out={}
    for t in SIGNAL_UNIVERSE:
        s=prices[t].dropna() if t in prices else pd.Series(dtype=float)
        if len(s)<260: out[t]={"eligible":False,"reason":"데이터부족"}; continue
        c=float(s.iloc[-1]); sma200=float(s.iloc[-200:].mean()); sma50=float(s.iloc[-50:].mean())
        score=sum(c/float(s.iloc[-w-1])-1 for w in MOM_WINDOWS)/len(MOM_WINDOWS)
        vol=float(s.pct_change().iloc[-VOL_WINDOW:].std()*math.sqrt(252)); isheld=t in held
        ok=(c>=sma200*EXIT_BUFFER if isheld else c>sma200*ENTRY_BUFFER) and score>0
        reason="추세·모멘텀 통과" if ok else "SMA200 또는 모멘텀 미통과"
        if not isheld and cooldown.get(t,0)>0: ok=False; reason="트레일링 스탑 쿨다운"
        if not isheld and REENTRY_NEED_SMA50 and c<=sma50: ok=False; reason="SMA50 아래"
        out[t]={"close":c,"sma200":sma200,"sma50":sma50,"score":score,"vol":vol,"eligible":ok,"reason":reason}
    return out
def compute_target_weights(selected,vol,brake):
    if not selected:return {}
    raw={t:1/max(vol[t],.01) for t in selected}; z=sum(raw.values()); w={t:v/z for t,v in raw.items()}
    avg=sum(w[t]*vol[t] for t in w); gross=min(MAX_GROSS,TARGET_VOL/max(avg,.01))*(.5 if brake else 1)
    return {t:min(MAX_WEIGHT_PER_ASSET,w[t]*gross) for t in w}
def plan_orders(target,current,equity,prices,cash):
    sells=[]; buys=[]; blocked=[]; band=max(REBALANCE_BAND_PCT*equity,MIN_ORDER_USD)
    for t,v in current.items():
        d=target.get(t,0)*equity-v
        if d<-band:sells.append((t,-d))
    available=cash+sum(x[1] for x in sells)
    for t,w in target.items():
        d=w*equity-current.get(t,0)
        if abs(d)<band: blocked.append(f"{t}: 밴드 내 유지"); continue
        if d>0:
            amt=min(d,available)
            if amt<MIN_ORDER_USD: blocked.append(f"{t}: 주문금액 부족 ${amt:.2f}")
            else: buys.append((t,amt)); available-=amt
    return {"sells":sells,"buys":buys,"blocked_by":blocked}
async def run_daily_cycle(toss,telegram):
    """매일 청산은 점검하고, 신규·리밸런싱은 월요일(또는 주 첫 실행)에만 한다."""
    dry=os.getenv("ROTATION_DRY_RUN","1")!="0"; st=_state(); today=datetime.now(NY).date().isoformat()
    try: df=await asyncio.to_thread(yf.download,SIGNAL_UNIVERSE,period="2y",auto_adjust=True,progress=False)
    except Exception as e: await telegram.send_message(f"[로테이션] 데이터 다운로드 실패: {e}"); return
    prices=df["Close"] if isinstance(df.columns,pd.MultiIndex) else df
    # 장중 미완성 일봉은 신호에 쓰지 않는다.
    if len(prices) and prices.index[-1].date()>=datetime.now(NY).date(): prices=prices.iloc[:-1]
    pos=st["positions"]; sig=compute_signals(prices,set(pos),st["cooldown"]); cash=await toss.get_balance()
    current={}; liveqty={}
    for t,p in pos.items():
        q=await toss.get_sellable_quantity(t)
        if q is None:
            # API 장애를 '0주 보유'로 해석하면 정상 포지션을 현금으로 오인한다.
            await telegram.send_message(f"[로테이션 경고] {t} 실제 수량 조회 실패: 이번 회차 주문 보류")
            return
        liveqty[t]=q; current[t]=q*(await toss.get_realtime_price(t) or 0)
        if q!=p.get("qty"): await telegram.send_message(f"[로테이션 경고] {t} 상태/계좌 수량 불일치, 계좌값 사용")
    equity=cash+sum(current.values()); cap=float(os.getenv("ROTATION_CAPITAL_CAP_USD","0") or 0); equity=min(equity,cap) if cap>0 else equity
    if os.getenv("ROTATION_RESET_PEAK")=="1": st["equity_peak"]=equity
    st["equity_peak"]=max(st.get("equity_peak",0),equity); dd=equity/st["equity_peak"]-1 if st["equity_peak"] else 0
    brake=dd<=DD_BRAKE_ON or (dd<DD_BRAKE_OFF and st.get("dd_brake",False)); st["dd_brake"]=brake
    for t in list(st["cooldown"]): st["cooldown"][t]=max(0,st["cooldown"][t]-1)
    selected=sorted([t for t,x in sig.items() if x.get("eligible")],key=lambda t:sig[t]["score"],reverse=True)[:TOP_N]
    target=compute_target_weights(selected,{t:sig[t]["vol"] for t in selected},brake)
    # 일봉 추세 이탈/트레일링 스탑은 요일과 무관하게 전량 청산한다.
    for t in list(pos):
        high=max(pos[t].get("highest_close",0),sig.get(t,{}).get("close",0)); pos[t]["highest_close"]=high
        x=sig.get(t,{}); stop=x.get("close",0)<high*(1-TRAILING_STOP.get(t,.12))
        if not x.get("eligible",False) or stop: target.pop(t,None); st["cooldown"][t]=COOLDOWN_DAYS if stop else 0
    plan=plan_orders(target,current,equity,{t:sig[t].get("close",0) for t in sig},cash)
    if datetime.now(NY).weekday()!=REBALANCE_WEEKDAY and st.get("last_rebalance_date")!=today: plan["buys"]=[]; plan["blocked_by"].append("월요일 정기 리밸런싱 대기")
    actions=[]
    for t,_ in plan["sells"]:
        if dry: actions.append(f"[DRY_RUN] 주문 차단: SELL {t}")
        elif await toss.sell_market_order(t): pos.pop(t,None); actions.append(f"SELL {t}")
    for t,amt in plan["buys"]:
        trade=TRADE_PROXY.get(t,t) if USE_PROXY_TICKERS else t
        if dry: actions.append(f"[DRY_RUN] 주문 차단: BUY {trade} ${amt:.2f}")
        elif await toss.buy_market_order(trade,amt): pos[t]={"qty":0,"entry_date":today,"highest_close":sig[t]["close"]}; actions.append(f"BUY {trade} ${amt:.2f}")
    if datetime.now(NY).weekday()==REBALANCE_WEEKDAY: st["last_rebalance_date"]=today
    st["positions"]=pos; _save(st)
    lines=[f"[로테이션] {today} | 총자산 ${equity:.2f} | 현금 ${cash:.2f} | DD {dd:.1%} (브레이크 {'ON' if brake else 'OFF'})"]
    lines += [f" {t} close {x.get('close','-')} / score {x.get('score',0):+.2%} / {'O' if x.get('eligible') else 'X'} {x.get('reason')}" for t,x in sig.items()]
    lines.append(" 목표: "+(" ".join(f"{t} {w:.0%}" for t,w in target.items()) or "현금 100%")); lines.append(" 주문: "+(", ".join(actions) or "없음")+" | 차단: "+("; ".join(plan["blocked_by"]) or "없음")+f" | {'DRY_RUN' if dry else 'LIVE'}")
    await telegram.send_message("\n".join(lines))
