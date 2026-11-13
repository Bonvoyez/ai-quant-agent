"""독립 백테스트: python backtest_rotation.py. 라이브 주문/API를 호출하지 않는다."""
import yfinance as yf, pandas as pd
from rotation_strategy import SIGNAL_UNIVERSE, compute_signals, compute_target_weights
def main():
    d=yf.download(SIGNAL_UNIVERSE,start="2005-01-01",auto_adjust=True,progress=False)["Close"].dropna(how="all")
    equity=1.; peak=1.; rows=[]; weights={}
    for i in range(260,len(d)-1):
        hist=d.iloc[:i+1]; s=compute_signals(hist,set(weights),{}); pick=sorted([t for t,x in s.items() if x.get("eligible")],key=lambda t:s[t]["score"],reverse=True)[:2]
        target=compute_target_weights(pick,{t:s[t]["vol"] for t in pick},False); turnover=sum(abs(target.get(t,0)-weights.get(t,0)) for t in set(target)|set(weights)); weights=target
        r=sum(weights.get(t,0)*(d[t].iloc[i+1]/d[t].iloc[i]-1) for t in weights)-turnover*.001; equity*=1+r; peak=max(peak,equity); rows.append((d.index[i+1],equity,r, equity/peak-1))
    x=pd.DataFrame(rows,columns=["date","equity","return","dd"]); years=(x.date.iloc[-1]-x.date.iloc[0]).days/365.25; cagr=x.equity.iloc[-1]**(1/years)-1; vol=x['return'].std()*(252**.5); mdd=x.dd.min(); calmar=cagr/abs(mdd) if mdd else 0
    print(f"CAGR {cagr:.2%} | Vol {vol:.2%} | MDD {mdd:.2%} | Calmar {calmar:.2f} | trades/yr (approx) N/A")
if __name__=="__main__": main()
