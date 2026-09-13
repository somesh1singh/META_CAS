"""
NSDL CAS Portfolio Intelligence & Advisory System
Single-file Streamlit app - Python 3.14
"""
import streamlit as st
import pandas as pd
import numpy as np
from datetime import date, datetime
from decimal import Decimal, getcontext
import hashlib, re, io, pathlib, json, math, uuid
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional
import sys

getcontext().prec = 28

st.set_page_config(page_title="CAS Portfolio Intelligence", layout="wide", page_icon="📈")

# XIRR
class XIRRError(Exception): pass

def xirr(cashflows: List[Tuple[date, Decimal]], guess: float = 0.1, max_iter: int = 100, tol: float = 1e-7) -> float:
    if len(cashflows) < 2:
        raise XIRRError("Need at least 2 cashflows")
    cashflows = sorted(cashflows, key=lambda x: x[0])
    amounts = [float(a) for _, a in cashflows]
    if not (any(a < 0 for a in amounts) and any(a > 0 for a in amounts)):
        raise XIRRError("XIRR needs both negative and positive flows")
    d0 = cashflows[0][0]
    def npv(rate: float) -> float:
        total = 0.0
        for d, amt in cashflows:
            days = (d - d0).days
            years = days / 365.0
            total += float(amt) / ((1 + rate) ** years) if rate > -0.9999 else 0
        return total
    def npv_deriv(rate: float) -> float:
        total = 0.0
        for d, amt in cashflows:
            days = (d - d0).days
            years = days / 365.0
            if years == 0: continue
            total += -years * float(amt) / ((1 + rate) ** (years + 1))
        return total
    r = guess
    for _ in range(max_iter):
        f = npv(r)
        df = npv_deriv(r)
        if abs(df) < 1e-12: break
        r_new = r - f/df
        if r_new < -0.9999: r_new = -0.9
        if abs(r_new - r) < tol: return r_new
        r = r_new
    low, high = -0.9, 10.0
    for _ in range(200):
        mid = (low+high)/2
        if npv(low)*npv(mid) <= 0: high = mid
        else: low = mid
        if abs(high-low) < tol: return (low+high)/2
    raise XIRRError(f"XIRR did not converge, last npv={npv(r)}")

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
    def holding_days(self, as_of: date) -> int:
        return (as_of - self.acquire_date).days
    def is_long_term(self, as_of: date, equity: bool = True) -> bool:
        return self.holding_days(as_of) > 365 if equity else self.holding_days(as_of) > 1095

class TaxLotEngine:
    def __init__(self):
        self.lots: Dict[str, List[Lot]] = {}
    def add_lot(self, lot: Lot):
        self.lots.setdefault(lot.isin, []).append(lot)
        self.lots[lot.isin].sort(key=lambda l: l.acquire_date)
    def create_from_txn(self, isin: str, qty: Decimal, price: Decimal, txn_date: date, lot_type: str="BUY") -> Lot:
        lot = Lot(isin=isin, quantity=qty, remaining_quantity=qty, acquire_date=txn_date, acquire_price=price, acquire_value=qty*price, lot_type=lot_type)
        self.add_lot(lot)
        return lot
    def handle_corporate_action(self, isin: str, action_type: str, ratio: Decimal, ex_date: date):
        for lot in self.lots.get(isin, []):
            if lot.acquire_date >= ex_date: continue
            if action_type == "BONUS":
                bonus_qty = lot.quantity * ratio
                lot.quantity += bonus_qty
                lot.remaining_quantity += bonus_qty
                if lot.quantity > 0: lot.acquire_price = lot.acquire_value / lot.quantity
                lot.corporate_action_adjusted = True
            elif action_type == "SPLIT":
                lot.quantity *= ratio
                lot.remaining_quantity *= ratio
                if ratio != 0: lot.acquire_price /= ratio
                lot.corporate_action_adjusted = True
    def simulate_sale(self, isin: str, qty: Decimal, sale_price: Decimal, sale_date: date, method: str="FIFO", equity: bool=True) -> Dict:
        if isin not in self.lots: raise Exception(f"No lots for {isin}")
        available = sum(l.remaining_quantity for l in self.lots[isin])
        if qty > available: raise Exception(f"Insufficient quantity: need {qty}, have {available}")
        lots = self.lots[isin][:]
        if method == "LIFO": lots = list(reversed(lots))
        elif method == "HIFO": lots = sorted(lots, key=lambda l: l.acquire_price, reverse=True)
        remaining = qty
        realized_gain = Decimal(0); stcg = Decimal(0); ltcg = Decimal(0); consumed=[]
        for lot in lots:
            if remaining <= 0: break
            if lot.remaining_quantity <= 0: continue
            take = min(lot.remaining_quantity, remaining)
            cost = take * lot.acquire_price
            proceeds = take * sale_price
            gain = proceeds - cost
            realized_gain += gain
            if lot.is_long_term(sale_date, equity=equity): ltcg += gain
            else: stcg += gain
            consumed.append((lot.id, float(take), float(gain)))
            remaining -= take
        tax = stcg * Decimal("0.20") + max(Decimal(0), ltcg - Decimal("125000")) * Decimal("0.125") if equity else (stcg+ltcg)*Decimal("0.30")
        return {"isin": isin, "quantity_sold": float(qty), "realized_gain": float(realized_gain), "stcg": float(stcg), "ltcg": float(ltcg), "estimated_tax": float(tax), "consumed_lots": consumed, "method": method}
    def get_lots(self, isin: str, as_of: Optional[date]=None) -> List[Lot]:
        lots = self.lots.get(isin, [])
        if as_of: return [l for l in lots if l.acquire_date <= as_of and l.remaining_quantity>0]
        return [l for l in lots if l.remaining_quantity>0]
    def unrealised_gains(self, isin: str, current_price: Decimal, as_of: date) -> Dict:
        lots = self.get_lots(isin, as_of)
        total_cost = sum(l.remaining_quantity * l.acquire_price for l in lots)
        total_qty = sum(l.remaining_quantity for l in lots)
        market_value = total_qty * current_price
        unrealised = market_value - total_cost
        return {"market_value": float(market_value), "cost": float(total_cost), "unrealised": float(unrealised), "quantity": float(total_qty)}
    def harvestable_losses(self, current_prices: Dict[str, Decimal], as_of: date, min_loss: Decimal = Decimal("5000")) -> List[Dict]:
        harvestable=[]
        for isin, lots in self.lots.items():
            price = current_prices.get(isin)
            if not price: continue
            for lot in lots:
                if lot.remaining_quantity <= 0: continue
                loss = (price - lot.acquire_price) * lot.remaining_quantity
                if loss < -min_loss:
                    harvestable.append({"isin": isin, "lot_id": lot.id, "quantity": float(lot.remaining_quantity), "acquire_date": lot.acquire_date.isoformat(), "unrealised_loss": float(loss), "holding_days": lot.holding_days(as_of), "is_long_term": lot.is_long_term(as_of)})
        return sorted(harvestable, key=lambda x: x["unrealised_loss"])

class AttributionEngine:
    def money_weighted_attribution(self, transactions: List[Dict], cost_paid: Decimal, tax_paid: Decimal, start_value: Decimal, end_value: Decimal) -> Dict:
        total_contrib = sum(Decimal(str(t.get('amount',0))) for t in transactions if t.get('type') in ('BUY','SIP'))
        total_withdraw = sum(Decimal(str(t.get('amount',0))) for t in transactions if t.get('type') in ('SELL','REDEEM'))
        total_return = end_value - start_value - total_contrib + total_withdraw
        cost_drag = cost_paid + tax_paid
        cost_drag_pct = float(cost_drag / (start_value+total_contrib)) if (start_value+total_contrib)>0 else 0
        gross_return = total_return + cost_drag
        waterfall = [
            {"label": "Starting Value", "value": float(start_value)},
            {"label": "Contributions", "value": float(total_contrib)},
            {"label": "Withdrawals", "value": -float(total_withdraw)},
            {"label": "Gross Returns", "value": float(gross_return)},
            {"label": "Costs & Taxes", "value": -float(cost_drag)},
            {"label": "Ending Value", "value": float(end_value), "is_total": True}
        ]
        leaks = [
            {"factor": "Cost drag (expense + exit + tax)", "impact_xirr_bps": cost_drag_pct*10000, "value": float(cost_drag)},
            {"factor": "Timing", "impact_xirr_bps": 0, "value": 0},
            {"factor": "Security selection", "impact_xirr_bps": 0, "value": 0},
            {"factor": "Cash drag", "impact_xirr_bps": 0, "value": 0},
        ]
        return {"start_value": float(start_value), "contributions": float(total_contrib), "withdrawals": float(total_withdraw), "gross_return": float(gross_return), "cost_drag": float(cost_drag), "net_return": float(total_return), "end_value": float(end_value), "waterfall": waterfall, "leak_ranking": sorted(leaks, key=lambda x: x["impact_xirr_bps"], reverse=True)}

class AdviceImpactEstimator:
    def __init__(self, tax_engine: TaxLotEngine, expected_returns: Dict[str, float]):
        self.tax_engine = tax_engine
        self.expected_returns = expected_returns
    def estimate_switch(self, from_isin: str, to_isin: str, switch_pct: float, quantity: Decimal, current_price_from: Decimal, current_price_to: Decimal, exit_load_pct: float = 0.0, as_of: date = date.today()) -> Dict:
        qty_to_switch = quantity * Decimal(str(switch_pct))
        sim = self.tax_engine.simulate_sale(from_isin, qty_to_switch, current_price_from, as_of, method="FIFO")
        tax_cost = sim["estimated_tax"]
        exit_cost = float(qty_to_switch * current_price_from * Decimal(str(exit_load_pct)))
        exp_ret_from = self.expected_returns.get(from_isin, 0.10)
        exp_ret_to = self.expected_returns.get(to_isin, 0.12)
        results={}
        for h in [1,3,5]:
            amortized = (tax_cost + exit_cost) / (float(qty_to_switch * current_price_from) * h) if qty_to_switch>0 else 0
            results[h]= (exp_ret_to - exp_ret_from) - amortized
        return {"action": f"Switch {switch_pct*100:.0f}% of {from_isin} to {to_isin}", "expected_delta_xirr_1y": results.get(1,0), "expected_delta_xirr_3y": results.get(3,0), "expected_delta_xirr_5y": results.get(5,0), "tax_cost_today": tax_cost, "exit_load_cost": exit_cost, "net_expected_benefit_3y": results.get(3,0), "is_recommended": results.get(3,0)>0, "scenarios": {"base": results}}

@st.cache_data(ttl=86400)
def load_institutional_data():
    base = "https://raw.githubusercontent.com/aditya-jha/nse-historical-membership/main"
    files = {
        "flat": f"{base}/shareholding_history/data/parsed/_flat.csv",
        "signals": f"{base}/shareholding_history/data/parsed/_signals.csv",
        "index": f"{base}/index_history/data/index_membership_history.csv",
        "fno": f"{base}/fno_history/data/fno_membership_history.csv"
    }
    data={}
    for key, url in files.items():
        try:
            df = pd.read_csv(url, nrows=50000)
            data[key]=df
        except Exception:
            data[key]=None
    if data["flat"] is None:
        sample = """ticker,period,promoter_pct,fii_pct,dii_pct,public_pct,pledge_pct
RELIANCE,2023-03-31,49.2,17.1,18.0,15.7,1.2
RELIANCE,2023-06-30,49.6,16.5,18.4,15.5,3.7
RELIANCE,2023-09-30,50.0,15.8,19.1,15.1,2.1
RELIANCE,2023-12-31,50.3,16.2,18.9,14.6,1.8
RELIANCE,2024-03-31,50.5,17.0,18.2,14.3,1.5
TCS,2023-03-31,72.3,12.1,6.2,9.4,0
TCS,2023-06-30,72.3,11.8,6.5,9.4,0
TCS,2023-09-30,72.3,11.5,6.8,9.4,0
INFY,2023-03-31,13.1,32.5,22.4,32.0,0
INFY,2023-06-30,13.0,33.1,21.9,32.0,0
INFY,2023-09-30,12.9,34.0,21.2,31.9,0
HDFCBANK,2023-03-31,0,31.2,18.5,50.3,0
HDFCBANK,2023-06-30,0,30.8,19.1,50.1,0
ICICIBANK,2023-03-31,0,42.1,20.3,37.6,0
ICICIBANK,2023-06-30,0,41.5,20.9,37.6,0
LTIM,2023-03-31,68.7,8.2,9.1,14.0,0
LTIM,2023-06-30,68.7,7.9,9.4,14.0,0
ZOMATO,2023-03-31,0,18.5,12.1,69.4,0
ZOMATO,2023-06-30,0,20.1,13.2,66.7,0
ETERNAL,2025-03-31,0,21.0,14.0,65.0,0
SBIN,2023-03-31,56.9,10.2,22.1,10.8,0
"""
        data["flat"]=pd.read_csv(io.StringIO(sample))
    if data["signals"] is None:
        sample_sig = """ticker,latest_period,promoter_pct,fii_pct,dii_pct,public_pct,fii_qoq,dii_qoq,fii_4q_delta,dii_4q_delta,smart_money_score,fii_dii_4q
RELIANCE,2024-03-31,50.5,17.0,18.2,14.3,0.8,-0.7,2.5,1.2,3.7,3.7
TCS,2024-03-31,72.3,11.5,6.8,9.4,-0.3,0.3,-0.6,0.6,0.0,0.0
INFY,2024-03-31,12.9,34.0,21.2,31.9,0.9,-0.7,1.5,-1.2,0.3,0.3
"""
        data["signals"]=pd.read_csv(io.StringIO(sample_sig))
    if data["index"] is None:
        sample_idx = """index_id,index_name,symbol,valid_from,valid_to,source
217,Nifty 50,RELIANCE,2020-01-01,,circular
217,Nifty 50,TCS,2020-01-01,,circular
217,Nifty 50,INFY,2020-01-01,,circular
227,Nifty 500,RELIANCE,2020-01-01,,circular
227,Nifty 500,TCS,2020-01-01,,circular
227,Nifty 500,ZOMATO,2021-07-20,2025-03-27,circular
227,Nifty 500,ETERNAL,2025-03-28,,circular
1001,Nifty IT,TCS,2020-01-01,,circular
1001,Nifty IT,INFY,2020-01-01,,circular
"""
        data["index"]=pd.read_csv(io.StringIO(sample_idx), parse_dates=['valid_from','valid_to'])
    else:
        try:
            data["index"]['valid_from']=pd.to_datetime(data["index"]['valid_from'], errors='coerce')
            data["index"]['valid_to']=pd.to_datetime(data["index"]['valid_to'], errors='coerce')
        except: pass
    if data["fno"] is None:
        sample_fno = """symbol,valid_from,valid_to,source
RELIANCE,2014-01-01,,circular
TCS,2014-02-01,,circular
INFY,2014-01-01,,circular
"""
        data["fno"]=pd.read_csv(io.StringIO(sample_fno))
    return data

class InstitutionalService:
    def __init__(self, data):
        self.data=data
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
        return {
            "ticker": ticker,
            "period": str(latest['period']),
            "fii_pct": float(latest.get('fii_pct',0)),
            "dii_pct": float(latest.get('dii_pct',0)),
            "fii_4q_delta": float(latest.get('fii_pct',0)-fourq.get('fii_pct',0)) if fourq is not None else 0,
            "dii_4q_delta": float(latest.get('dii_pct',0)-fourq.get('dii_pct',0)) if fourq is not None else 0,
            "smart_money_score": float((latest.get('fii_pct',0)-fourq.get('fii_pct',0)) + (latest.get('dii_pct',0)-fourq.get('dii_pct',0))) if fourq is not None else 0,
        }
    def index_membership_on(self, symbol: str, as_of: date):
        df=self.data.get("index")
        if df is None: return []
        on=pd.Timestamp(as_of)
        matches=df[(df['symbol']==symbol) & (df['valid_from']<=on) & (df['valid_to'].isna() | (df['valid_to']>on))]
        return matches.to_dict('records')
    def fno_status_on(self, symbol: str, as_of: date)->bool:
        df=self.data.get("fno")
        if df is None: return False
        try:
            df['valid_from']=pd.to_datetime(df['valid_from'], errors='coerce')
            df['valid_to']=pd.to_datetime(df['valid_to'], errors='coerce')
        except: pass
        on=pd.Timestamp(as_of)
        matches=df[(df['symbol']==symbol) & (df['valid_from']<=on) & (df['valid_to'].isna() | (df['valid_to']>on))]
        return not matches.empty

class NSDLParserV1:
    MARKERS=["Consolidated Account Statement","NSDL","Statement Period","Folio No"]
    def fingerprint(self, text: str)->str:
        return hashlib.sha256(text[:2000].encode()).hexdigest()[:16]
    def can_parse(self, text: str)->bool:
        return sum(1 for m in self.MARKERS if m.lower() in text.lower())>=2
    def _extract_text(self, pdf_bytes: bytes, password: str|None=None)->str:
        text=""
        try:
            import pdfplumber
            with pdfplumber.open(io.BytesIO(pdf_bytes), password=password or "") as pdf:
                for page in pdf.pages:
                    text+="\n"+(page.extract_text() or "")
            if len(text)>1000: return text
        except Exception as e:
            if password is None and "password" in str(e).lower():
                raise Exception("PDF is password protected - password required")
        try:
            import fitz
            doc=fitz.open(stream=pdf_bytes, filetype="pdf")
            if doc.needs_pass:
                if password:
                    if not doc.authenticate(password):
                        raise Exception("Incorrect PDF password")
                else:
                    raise Exception("PDF is password protected - password required")
            for page in doc:
                text+="\n"+page.get_text("text")
        except Exception as ex:
            if "password" in str(ex).lower():
                raise ex
        return text
    def parse(self, pdf_bytes: bytes, password: str|None=None)->dict:
        raw=self._extract_text(pdf_bytes[:50000], password=password)
        full=self._extract_text(pdf_bytes, password=password)
        if not self.can_parse(raw+full):
            if not re.search(r"[A-Z]{2}[A-Z0-9]{9}\d", full):
                raise Exception("Not a NSDL CAS (markers not found)")
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
                if isin and qty:
                    holdings.append({"isin": isin, "quantity": qty, "symbol": isin[:6], "market_value": qty*100, "nav_or_price": 100})
        transactions=[]
        txn_re=re.compile(r"(\d{2}[-/]\d{2}[-/]\d{4}).*?(Purchase|Redemption|Buy|Sell|SIP)", re.I)
        for line in full.split("\n"):
            mt=txn_re.search(line)
            if mt:
                try:
                    d=datetime.strptime(mt.group(1), "%d-%m-%Y").date()
                except:
                    try: d=datetime.strptime(mt.group(1), "%d/%m/%Y").date()
                    except: continue
                isin_m=re.search(isin_pat, line)
                isin=isin_m.group(1) if isin_m else "UNKNOWN"
                amt_m=re.search(r"(\d+,?\d*\.\d{2})", line)
                amt=float(amt_m.group(1).replace(",","")) if amt_m else 0
                ttype="BUY" if "purch" in mt.group(2).lower() or "buy" in mt.group(2).lower() or "sip" in mt.group(2).lower() else "SELL"
                transactions.append({"date": d, "isin": isin, "type": ttype, "amount": amt})
        return {"from_date": from_date, "to_date": to_date, "holdings": holdings, "transactions": transactions, "fingerprint": self.fingerprint(full), "confidence": min(1.0, 0.5*(1 if holdings else 0)+0.4*(1 if transactions else 0)+0.1*(1 if from_date else 0))}

def main():
    st.title("CAS Portfolio Intelligence")
    st.caption("NSDL CAS + Institutional Intelligence | Password-protected PDF support")

    with st.sidebar:
        st.header("Settings")
        as_of = st.date_input("PIT As-Of Date", value=date.today())
        st.markdown("### PDF Password")
        pdf_password = st.text_input("CAS PDF Password (if protected)", type="password", help="Usually lowercase PAN. In-memory only.")
        if st.button("Clear Cache"):
            st.cache_data.clear()
            st.rerun()

    with st.spinner("Loading institutional data..."):
        data = load_institutional_data()
        svc = InstitutionalService(data)
    st.success(f"Loaded: {len(data['flat'])} rows, {len(data['index'])} index intervals")

    with st.expander("How to open password-protected CAS?"):
        st.markdown("- NSDL CAS is password protected\n- Try lowercase PAN\n- Enter in Sidebar > PDF Password")

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
                st.dataframe(pd.DataFrame(parsed['holdings']))
            except Exception as e:
                err=str(e)
                if "password" in err.lower():
                    st.error(f"PDF password required/incorrect for {f.name}: {err}. Enter correct password in sidebar.")
                else:
                    st.error(f"Failed to parse {f.name}: {e}")

    if not all_holdings:
        st.info("No CAS uploaded - showing demo")
        all_holdings = [
            {"isin": "INE002A01018", "symbol": "RELIANCE", "quantity": 100, "market_value": 150000, "nav_or_price": 1500},
            {"isin": "INE467B01029", "symbol": "TCS", "quantity": 50, "market_value": 180000, "nav_or_price": 3600},
            {"isin": "INE009A01021", "symbol": "INFY", "quantity": 80, "market_value": 120000, "nav_or_price": 1500},
        ]
        all_txns = [
            {"date": date(2020,1,1), "isin": "INE002A01018", "type": "BUY", "amount": 100000},
        ]

    st.header("Institutional Intelligence")
    enriched=[]
    for h in all_holdings:
        sym = h.get("symbol") or h.get("isin")[:6]
        pit = svc.pit_shareholding(sym, as_of)
        enriched.append({**h, "fii_pct": pit["fii_pct"] if pit else None, "dii_pct": pit["dii_pct"] if pit else None})
    st.dataframe(pd.DataFrame(enriched))

    st.header("XIRR Engine")
    cfs=[]
    for txn in all_txns:
        amt=Decimal(str(txn["amount"]))
        if txn["type"]=="BUY": cfs.append((txn["date"], -amt))
        else: cfs.append((txn["date"], amt))
    current_val = sum(h["market_value"] for h in all_holdings)
    cfs.append((as_of, Decimal(str(current_val))))
    st.write(pd.DataFrame([{"date": str(d), "amount": str(a)} for d,a in cfs]))
    try:
        r=xirr(cfs)
        st.metric("Portfolio XIRR", f"{r*100:.2f}%")
    except Exception as e:
        st.error(f"XIRR error: {e}")

if __name__=="__main__":
    main()
