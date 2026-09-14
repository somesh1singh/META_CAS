"""
NSDL CAS Portfolio Intelligence & Advisory System
Single-file Streamlit app - Python 3.14 - Optimized for instant load
"""
import streamlit as st
import pandas as pd
import numpy as np
from datetime import date, datetime
from decimal import Decimal, getcontext
import hashlib, re, io, uuid
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional

getcontext().prec = 28
st.set_page_config(page_title="CAS Portfolio Intelligence", layout="wide", page_icon="📈")

class XIRRError(Exception): pass
def xirr(cashflows: List[Tuple[date, Decimal]], guess: float = 0.1, max_iter: int = 100, tol: float = 1e-7) -> float:
    if len(cashflows) < 2: raise XIRRError("Need at least 2 cashflows")
    cashflows = sorted(cashflows, key=lambda x: x[0])
    amounts = [float(a) for _, a in cashflows]
    if not (any(a < 0 for a in amounts) and any(a > 0 for a in amounts)): raise XIRRError("Need both flows")
    d0 = cashflows[0][0]
    def npv(rate: float) -> float:
        total=0.0
        for d,amt in cashflows:
            years=(d-d0).days/365.0
            total+=float(amt)/((1+rate)**years) if rate>-0.9999 else 0
        return total
    def npv_deriv(rate: float) -> float:
        total=0.0
        for d,amt in cashflows:
            years=(d-d0).days/365.0
            if years==0: continue
            total+=-years*float(amt)/((1+rate)**(years+1))
        return total
    r=guess
    for _ in range(max_iter):
        f=npv(r); df=npv_deriv(r)
        if abs(df)<1e-12: break
        r_new=r-f/df
        if r_new<-0.9999: r_new=-0.9
        if abs(r_new-r)<tol: return r_new
        r=r_new
    low,high=-0.9,10.0
    for _ in range(200):
        mid=(low+high)/2
        if npv(low)*npv(mid)<=0: high=mid
        else: low=mid
        if abs(high-low)<tol: return (low+high)/2
    raise XIRRError(f"XIRR no converge")

@dataclass
class Lot:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    isin: str = ""
    quantity: Decimal = Decimal(0)
    remaining_quantity: Decimal = Decimal(0)
    acquire_date: date = field(default_factory=date.today)
    acquire_price: Decimal = Decimal(0)
    acquire_value: Decimal = Decimal(0)
    lot_type: str = "BUY"
    corporate_action_adjusted: bool = False
    def holding_days(self, as_of: date) -> int: return (as_of - self.acquire_date).days
    def is_long_term(self, as_of: date, equity: bool = True) -> bool: return self.holding_days(as_of) > 365 if equity else self.holding_days(as_of) > 1095

class TaxLotEngine:
    def __init__(self): self.lots: Dict[str, List[Lot]] = {}
    def add_lot(self, lot: Lot):
        self.lots.setdefault(lot.isin, []).append(lot)
        self.lots[lot.isin].sort(key=lambda l: l.acquire_date)
    def create_from_txn(self, isin: str, qty: Decimal, price: Decimal, txn_date: date, lot_type: str="BUY") -> Lot:
        lot = Lot(isin=isin, quantity=qty, remaining_quantity=qty, acquire_date=txn_date, acquire_price=price, acquire_value=qty*price, lot_type=lot_type)
        self.add_lot(lot)
        return lot
    def simulate_sale(self, isin: str, qty: Decimal, sale_price: Decimal, sale_date: date, method: str="FIFO", equity: bool=True) -> Dict:
        if isin not in self.lots: raise Exception(f"No lots for {isin}")
        available = sum(l.remaining_quantity for l in self.lots[isin])
        if qty > available: raise Exception(f"Insufficient quantity")
        lots = self.lots[isin][:]
        if method == "LIFO": lots = list(reversed(lots))
        elif method == "HIFO": lots = sorted(lots, key=lambda l: l.acquire_price, reverse=True)
        remaining = qty
        realized_gain = Decimal(0); stcg = Decimal(0); ltcg = Decimal(0); consumed=[]
        for lot in lots:
            if remaining <= 0: break
            if lot.remaining_quantity <= 0: continue
            take = min(lot.remaining_quantity, remaining)
            gain = take * (sale_price - lot.acquire_price)
            realized_gain += gain
            if lot.is_long_term(sale_date, equity=equity): ltcg += gain
            else: stcg += gain
            consumed.append((lot.id, float(take), float(gain)))
            remaining -= take
        tax = stcg * Decimal("0.20") + max(Decimal(0), ltcg - Decimal("125000")) * Decimal("0.125") if equity else (stcg+ltcg)*Decimal("0.30")
        return {"isin": isin, "quantity_sold": float(qty), "realized_gain": float(realized_gain), "stcg": float(stcg), "ltcg": float(ltcg), "estimated_tax": float(tax), "consumed_lots": consumed, "method": method}

@st.cache_data
def load_institutional_data():
    # INSTANT - no network, avoids "cooking" on Streamlit Cloud
    flat_csv = """ticker,period,promoter_pct,fii_pct,dii_pct,public_pct,pledge_pct
RELIANCE,2023-03-31,49.2,17.1,18.0,15.7,1.2
RELIANCE,2023-06-30,49.6,16.5,18.4,15.5,3.7
RELIANCE,2023-09-30,50.0,15.8,19.1,15.1,2.1
RELIANCE,2023-12-31,50.3,16.2,18.9,14.6,1.8
RELIANCE,2024-03-31,50.5,17.0,18.2,14.3,1.5
TCS,2023-03-31,72.3,12.1,6.2,9.4,0
TCS,2023-06-30,72.3,11.8,6.5,9.4,0
INFY,2023-03-31,13.1,32.5,22.4,32.0,0
HDFCBANK,2023-03-31,0,31.2,18.5,50.3,0
ICICIBANK,2023-03-31,0,42.1,20.3,37.6,0
LTIM,2023-03-31,68.7,8.2,9.1,14.0,0
ZOMATO,2023-03-31,0,18.5,12.1,69.4,0
SBIN,2023-03-31,56.9,10.2,22.1,10.8,0
"""
    signals_csv = """ticker,latest_period,promoter_pct,fii_pct,dii_pct,public_pct,fii_qoq,dii_qoq,fii_4q_delta,dii_4q_delta,smart_money_score
RELIANCE,2024-03-31,50.5,17.0,18.2,14.3,0.8,-0.7,2.5,1.2,3.7
TCS,2024-03-31,72.3,11.5,6.8,9.4,-0.3,0.3,-0.6,0.6,0.0
INFY,2024-03-31,12.9,34.0,21.2,31.9,0.9,-0.7,1.5,-1.2,0.3
"""
    index_csv = """index_id,index_name,symbol,valid_from,valid_to,source
217,Nifty 50,RELIANCE,2020-01-01,,circular
217,Nifty 50,TCS,2020-01-01,,circular
217,Nifty 50,INFY,2020-01-01,,circular
227,Nifty 500,RELIANCE,2020-01-01,,circular
1001,Nifty IT,TCS,2020-01-01,,circular
1001,Nifty IT,INFY,2020-01-01,,circular
1015,Nifty Bank,HDFCBANK,2020-01-01,,circular
"""
    fno_csv = """symbol,valid_from,valid_to,source
RELIANCE,2014-01-01,,circular
TCS,2014-02-01,,circular
INFY,2014-01-01,,circular
"""
    data={}
    data["flat"]=pd.read_csv(io.StringIO(flat_csv))
    data["signals"]=pd.read_csv(io.StringIO(signals_csv))
    data["index"]=pd.read_csv(io.StringIO(index_csv), parse_dates=['valid_from','valid_to'])
    data["fno"]=pd.read_csv(io.StringIO(fno_csv))
    return data

class InstitutionalService:
    def __init__(self, data): self.data=data
    def pit_shareholding(self, ticker: str, as_of: date):
        df=self.data.get("flat")
        if df is None or df.empty: return None
        sub=df[df['ticker']==ticker].copy()
        if sub.empty: return None
        sub['period_parsed']=pd.to_datetime(sub['period'], errors='coerce')
        sub=sub[sub['period_parsed']<=pd.Timestamp(as_of)].sort_values('period_parsed')
        if sub.empty: return None
        latest=sub.iloc[-1]
        prev=sub.iloc[-2] if len(sub)>=2 else None
        fourq=sub.iloc[-5] if len(sub)>=5 else (sub.iloc[0] if len(sub)>=2 else None)
        return {"ticker": ticker, "period": str(latest['period']), "fii_pct": float(latest.get('fii_pct',0)), "dii_pct": float(latest.get('dii_pct',0)), "fii_4q_delta": float(latest.get('fii_pct',0)-fourq.get('fii_pct',0)) if fourq is not None else 0, "dii_4q_delta": float(latest.get('dii_pct',0)-fourq.get('dii_pct',0)) if fourq is not None else 0, "smart_money_score": float((latest.get('fii_pct',0)-fourq.get('fii_pct',0)) + (latest.get('dii_pct',0)-fourq.get('dii_pct',0))) if fourq is not None else 0}
    def index_membership_on(self, symbol: str, as_of: date):
        df=self.data.get("index")
        if df is None: return []
        on=pd.Timestamp(as_of)
        matches=df[(df['symbol']==symbol) & (df['valid_from']<=on) & (df['valid_to'].isna() | (df['valid_to']>on))]
        return matches.to_dict('records')
    def fno_status_on(self, symbol: str, as_of: date)->bool:
        df=self.data.get("fno")
        if df is None: return False
        on=pd.Timestamp(as_of)
        matches=df[(df['symbol']==symbol) & (df['valid_from']<=on) & (df['valid_to'].isna() | (df['valid_to']>on))]
        return not matches.empty

class NSDLParserV1:
    MARKERS=["Consolidated Account Statement","NSDL","Statement Period","Folio No"]
    def fingerprint(self, text: str)->str: return hashlib.sha256(text[:2000].encode()).hexdigest()[:16]
    def can_parse(self, text: str)->bool: return sum(1 for m in self.MARKERS if m.lower() in text.lower())>=2
    def _extract_text(self, pdf_bytes: bytes, password: str|None=None)->str:
        text=""
        try:
            import pdfplumber
            with pdfplumber.open(io.BytesIO(pdf_bytes), password=password or "") as pdf:
                for page in pdf.pages: text+="\n"+(page.extract_text() or "")
            if len(text)>1000: return text
        except Exception as e:
            if password is None and "password" in str(e).lower(): raise Exception("PDF is password protected - password required")
        try:
            import fitz
            doc=fitz.open(stream=pdf_bytes, filetype="pdf")
            if doc.needs_pass:
                if password:
                    if not doc.authenticate(password): raise Exception("Incorrect PDF password")
                else: raise Exception("PDF is password protected - password required")
            for page in doc: text+="\n"+page.get_text("text")
        except Exception as ex:
            if "password" in str(ex).lower(): raise ex
        return text
    def parse(self, pdf_bytes: bytes, password: str|None=None)->dict:
        full=self._extract_text(pdf_bytes, password=password)
        if not self.can_parse(full):
            if not re.search(r"[A-Z]{2}[A-Z0-9]{9}\d", full): raise Exception("Not a NSDL CAS")
        from_date=to_date=None
        m=re.search(r"Statement Period\s*:?\s*(\d{2}[-/]\d{2}[-/]\d{4})\s*to\s*(\d{2}[-/]\d{2}[-/]\d{4})", full, re.I)
        if m:
            for fmt in ("%d-%m-%Y","%d/%m/%Y"):
                try:
                    from_date=datetime.strptime(m.group(1), fmt).date()
                    to_date=datetime.strptime(m.group(2), fmt).date()
                    break
                except: continue
        holdings=[]; isin_pat=r"([A-Z]{2}[A-Z0-9]{9}\d)"
        for line in full.split("\n"):
            if re.search(isin_pat, line):
                isin_m=re.search(isin_pat, line)
                isin=isin_m.group(1) if isin_m else None
                qty_m=re.search(r"(\d+\.\d+)\s+Units", line, re.I) or re.search(r"\b(\d+\.\d{2,})\b", line)
                qty=float(qty_m.group(1)) if qty_m else 0
                if isin and qty: holdings.append({"isin": isin, "quantity": qty, "symbol": isin[:6], "market_value": qty*100, "nav_or_price": 100})
        transactions=[]
        txn_re=re.compile(r"(\d{2}[-/]\d{2}[-/]\d{4}).*?(Purchase|Redemption|Buy|Sell|SIP)", re.I)
        for line in full.split("\n"):
            mt=txn_re.search(line)
            if mt:
                try: d=datetime.strptime(mt.group(1), "%d-%m-%Y").date()
                except:
                    try: d=datetime.strptime(mt.group(1), "%d/%m/%Y").date()
                    except: continue
                isin_m=re.search(isin_pat, line)
                isin=isin_m.group(1) if isin_m else "UNKNOWN"
                amt_m=re.search(r"(\d+,?\d*\.\d{2})", line)
                amt=float(amt_m.group(1).replace(",","")) if amt_m else 0
                ttype="BUY" if "purch" in mt.group(2).lower() or "buy" in mt.group(2).lower() or "sip" in mt.group(2).lower() else "SELL"
                transactions.append({"date": d, "isin": isin, "type": ttype, "amount": amt})
        return {"from_date": from_date, "to_date": to_date, "holdings": holdings, "transactions": transactions, "fingerprint": self.fingerprint(full), "confidence": 0.8 if holdings else 0.3}

def main():
    st.title("CAS Portfolio Intelligence")
    st.caption("NSDL CAS + Institutional Intelligence | Password-protected PDF support | Instant load version")

    with st.sidebar:
        st.header("Settings")
        as_of = st.date_input("PIT As-Of Date", value=date.today())
        st.markdown("### PDF Password")
        pdf_password = st.text_input("CAS PDF Password", type="password", help="Usually lowercase PAN. In-memory only.")
        if st.button("Clear Cache"):
            st.cache_data.clear()
            st.rerun()

    # Instant load - no network
    data = load_institutional_data()
    svc = InstitutionalService(data)
    st.success(f"Loaded: {len(data['flat'])} rows instant (no network fetch)")

    with st.expander("How to open password-protected CAS?"):
        st.markdown("- Try lowercase PAN\n- Enter in Sidebar")

    uploaded = st.file_uploader("Upload NSDL CAS PDF(s)", type=["pdf"], accept_multiple_files=True)
    parser = NSDLParserV1()
    all_holdings=[]; all_txns=[]

    if uploaded:
        for f in uploaded:
            pdf_bytes = f.read()
            try:
                parsed = parser.parse(pdf_bytes, password=pdf_password if pdf_password else None)
                st.subheader(f"{f.name} - Confidence {parsed['confidence']:.2f}")
                all_holdings.extend(parsed['holdings'])
                all_txns.extend(parsed['transactions'])
                if parsed['holdings']: st.dataframe(pd.DataFrame(parsed['holdings']))
                else: st.warning("No holdings detected - showing demo")
            except Exception as e:
                err=str(e)
                if "password" in err.lower(): st.error(f"Password required/incorrect: {err}")
                else: st.error(f"Failed: {e}")

    if not all_holdings:
        st.info("No CAS uploaded - demo holdings")
        all_holdings = [
            {"isin": "INE002A01018", "symbol": "RELIANCE", "quantity": 100, "market_value": 150000, "nav_or_price": 1500},
            {"isin": "INE467B01029", "symbol": "TCS", "quantity": 50, "market_value": 180000, "nav_or_price": 3600},
            {"isin": "INE009A01021", "symbol": "INFY", "quantity": 80, "market_value": 120000, "nav_or_price": 1500},
        ]
        all_txns = [{"date": date(2020,1,1), "isin": "INE002A01018", "type": "BUY", "amount": 100000}]

    st.header("Institutional Intelligence")
    enriched=[]
    for h in all_holdings:
        sym = h.get("symbol") or h.get("isin")[:6]
        pit = svc.pit_shareholding(sym, as_of)
        enriched.append({**h, "fii_pct": pit["fii_pct"] if pit else None, "dii_pct": pit["dii_pct"] if pit else None, "smart_score": pit["smart_money_score"] if pit else None})
    st.dataframe(pd.DataFrame(enriched))

    st.header("XIRR")
    cfs=[]
    for txn in all_txns:
        amt=Decimal(str(txn["amount"]))
        if txn["type"]=="BUY": cfs.append((txn["date"], -amt))
        else: cfs.append((txn["date"], amt))
    current_val = sum(h["market_value"] for h in all_holdings)
    cfs.append((as_of, Decimal(str(current_val))))
    try:
        r=xirr(cfs)
        st.metric("Portfolio XIRR", f"{r*100:.2f}%")
    except Exception as e:
        st.error(f"XIRR error: {e}")
    st.write(pd.DataFrame([{"date": str(d), "amount": str(a)} for d,a in cfs]))

if __name__=="__main__":
    main()
