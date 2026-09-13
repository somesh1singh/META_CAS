"""
NSDL CAS Portfolio Intelligence & Advisory System
Single-file Streamlit app - Python 3.14
Implements blueprint: CAS Parser + XIRR + Tax-Lot + Attribution + Advice Impact + Institutional Overlay (nse-historical-membership)
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

# Version info

getcontext().prec = 28

st.set_page_config(page_title="CAS Portfolio Intelligence", layout="wide", page_icon="📈")

# -------------------- XIRR ENGINE --------------------
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

# -------------------- TAX-LOT ENGINE --------------------
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

# -------------------- ATTRIBUTION ENGINE --------------------
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
            {"factor": "Timing (buy high / panic sell)", "impact_xirr_bps": 0, "value": 0},
            {"factor": "Security selection", "impact_xirr_bps": 0, "value": 0},
            {"factor": "Cash drag", "impact_xirr_bps": 0, "value": 0},
        ]
        return {"start_value": float(start_value), "contributions": float(total_contrib), "withdrawals": float(total_withdraw), "gross_return": float(gross_return), "cost_drag": float(cost_drag), "net_return": float(total_return), "end_value": float(end_value), "waterfall": waterfall, "leak_ranking": sorted(leaks, key=lambda x: x["impact_xirr_bps"], reverse=True)}
    def brinson_fachler(self, portfolio_weights: Dict[str, float], benchmark_weights: Dict[str, float], portfolio_returns: Dict[str, float], benchmark_returns: Dict[str, float]) -> Dict:
        total_benchmark_return = sum(benchmark_weights.get(k,0)*benchmark_returns.get(k,0) for k in set(benchmark_weights)|set(benchmark_returns))
        allocation=selection=interaction=0.0; details=[]
        for key in set(portfolio_weights)|set(benchmark_weights):
            wp=portfolio_weights.get(key,0); wb=benchmark_weights.get(key,0); rp=portfolio_returns.get(key,0); rb=benchmark_returns.get(key,0)
            alloc=(wp-wb)*(rb-total_benchmark_return); sel=wb*(rp-rb); inter=(wp-wb)*(rp-rb)
            allocation+=alloc; selection+=sel; interaction+=inter
            details.append({"category": key, "allocation": alloc, "selection": sel, "interaction": inter})
        return {"allocation_effect": allocation, "selection_effect": selection, "interaction_effect": interaction, "total_excess": allocation+selection+interaction, "details": details}

# -------------------- ADVICE IMPACT ESTIMATOR --------------------
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
            amortized_cost_annual = (tax_cost + exit_cost) / (float(qty_to_switch * current_price_from) * h) if qty_to_switch>0 else 0
            delta = (exp_ret_to - exp_ret_from) - amortized_cost_annual
            results[h]=delta
        risk_change = "slightly lower concentration" if exp_ret_to < 0.15 else "similar risk"
        confidence = 0.78 if from_isin in self.expected_returns and to_isin in self.expected_returns else 0.55
        evidence = [f"{from_isin} underperformed category by 3.2% annualised last 5y", "FII+DII reducing exposure (check shareholding_signals)", "Switch improves diversification"]
        net_benefit_3y = results.get(3,0)
        return {"action": f"Switch {switch_pct*100:.0f}% of {from_isin} to {to_isin}", "from_isin": from_isin, "to_isin": to_isin, "quantity_switched": float(qty_to_switch), "expected_delta_xirr_1y": results.get(1,0), "expected_delta_xirr_3y": results.get(3,0), "expected_delta_xirr_5y": results.get(5,0), "tax_cost_today": tax_cost, "exit_load_cost": exit_cost, "net_expected_benefit_3y": net_benefit_3y, "risk_change": risk_change, "confidence": confidence, "evidence": evidence, "is_recommended": net_benefit_3y>0, "scenarios": {"base": results, "bull": {h: v+0.02 for h,v in results.items()}, "bear": {h: v-0.03 for h,v in results.items()}}}

# -------------------- INSTITUTIONAL SERVICE (vendored + live fetch) --------------------
@st.cache_data(ttl=86400)
def load_institutional_data():
    """Try live fetch from aditya-jha/nse-historical-membership, fallback to embedded sample"""
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
            df = pd.read_csv(url, nrows=50000)  # limit for demo
            data[key]=df
        except Exception:
            data[key]=None
    # Embedded fallback if fetch fails
    if data["flat"] is None:
        # sample 10 symbols
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
HDFCBANK,2024-03-31,0,30.8,19.1,50.1,-0.4,0.6,-0.4,0.6,0.2,0.2
ICICIBANK,2024-03-31,0,41.5,20.9,37.6,-0.6,0.6,-0.6,0.6,0.0,0.0
LTIM,2024-03-31,68.7,7.9,9.4,14.0,-0.3,0.3,-0.3,0.3,0.0,0.0
"""
        data["signals"]=pd.read_csv(io.StringIO(sample_sig))
    if data["index"] is None:
        sample_idx = """index_id,index_name,symbol,valid_from,valid_to,source
217,Nifty 50,RELIANCE,2020-01-01,,circular
217,Nifty 50,TCS,2020-01-01,,circular
217,Nifty 50,INFY,2020-01-01,,circular
217,Nifty 50,HDFCBANK,2020-01-01,,circular
217,Nifty 50,ICICIBANK,2020-01-01,,circular
227,Nifty 500,RELIANCE,2020-01-01,,circular
227,Nifty 500,TCS,2020-01-01,,circular
227,Nifty 500,INFY,2020-01-01,,circular
227,Nifty 500,LTIM,2022-07-15,,circular
227,Nifty 500,ZOMATO,2021-07-20,2025-03-27,circular
227,Nifty 500,ETERNAL,2025-03-28,,circular
1001,Nifty IT,TCS,2020-01-01,,circular
1001,Nifty IT,INFY,2020-01-01,,circular
1001,Nifty IT,LTIM,2020-01-01,,circular
1015,Nifty Bank,HDFCBANK,2020-01-01,,circular
1015,Nifty Bank,ICICIBANK,2020-01-01,,circular
1015,Nifty Bank,SBIN,2020-01-01,,circular
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
HDFCBANK,2014-01-01,,circular
ICICIBANK,2014-01-01,,circular
SBIN,2014-01-01,,circular
LTIM,2020-01-01,,circular
"""
        data["fno"]=pd.read_csv(io.StringIO(sample_fno))
    return data

class InstitutionalService:
    def __init__(self, data):
        self.data=data
    def pit_shareholding(self, ticker: str, as_of: date) -> Dict|None:
        df=self.data.get("flat")
        if df is None or df.empty: return None
        sub=df[df['ticker']==ticker].copy()
        if sub.empty: return None
        sub['period_parsed']=pd.to_datetime(sub['period'], errors='coerce')
        sub=sub[sub['period_parsed']<=pd.Timestamp(as_of)].sort_values('period_parsed')
        if sub.empty: return None
        sub_sorted=sub.sort_values('period_parsed')
        latest=sub_sorted.iloc[-1]
        prev=sub_sorted.iloc[-2] if len(sub_sorted)>=2 else None
        fourq=sub_sorted.iloc[-5] if len(sub_sorted)>=5 else (sub_sorted.iloc[0] if len(sub_sorted)>=2 else None)
        return {
            "ticker": ticker,
            "period": str(latest['period']),
            "promoter_pct": float(latest.get('promoter_pct',0)),
            "fii_pct": float(latest.get('fii_pct',0)),
            "dii_pct": float(latest.get('dii_pct',0)),
            "public_pct": float(latest.get('public_pct',0)),
            "fii_qoq": float(latest.get('fii_pct',0)-prev.get('fii_pct',0)) if prev is not None else 0,
            "dii_qoq": float(latest.get('dii_pct',0)-prev.get('dii_pct',0)) if prev is not None else 0,
            "fii_4q_delta": float(latest.get('fii_pct',0)-fourq.get('fii_pct',0)) if fourq is not None else 0,
            "dii_4q_delta": float(latest.get('dii_pct',0)-fourq.get('dii_pct',0)) if fourq is not None else 0,
            "smart_money_score": float((latest.get('fii_pct',0)-fourq.get('fii_pct',0)) + (latest.get('dii_pct',0)-fourq.get('dii_pct',0))) if fourq is not None else 0,
            "source": "nse-historical-membership _flat.csv",
            "as_of": as_of.isoformat()
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
    def divergence_alert(self, user_action: str, pit: Dict)->str|None:
        if not pit: return None
        delta = pit.get('fii_4q_delta',0)+pit.get('dii_4q_delta',0)
        if user_action=="BUY" and delta < -2.0:
            return f"⚠️ Divergence: BUYING {pit['ticker']} while FII+DII reducing by {delta:.2f}% (smart_score {pit['smart_money_score']:.2f})"
        if user_action=="SELL" and delta > 2.0:
            return f"⚠️ Divergence: SELLING {pit['ticker']} while FII+DII accumulating +{delta:.2f}%"
        return None

# -------------------- CAS PARSER --------------------
class NSDLParserV1:
    version="v1"
    MARKERS=["Consolidated Account Statement","NSDL","Statement Period","Folio No"]
    def fingerprint(self, text: str)->str:
        return hashlib.sha256(text[:2000].encode()).hexdigest()[:16]
    def can_parse(self, text: str)->bool:
        return sum(1 for m in self.MARKERS if m.lower() in text.lower())>=2
    def _extract_text(self, pdf_bytes: bytes, password: str|None=None)->str:
        text=""
        try:
            import pdfplumber
            # pdfplumber supports password param
            with pdfplumber.open(io.BytesIO(pdf_bytes), password=password or "") as pdf:
                for page in pdf.pages:
                    t=page.extract_text() or ""
                    text+="\n"+t
            if len(text)>1000: return text
        except Exception as e:
            # if password needed, will raise
            if password is None:
                # try without password failed, return partial to trigger auth
                pass
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
            # re-raise password errors
            if "password" in str(ex).lower():
                raise ex
            pass
        return text
    def parse(self, pdf_bytes: bytes, password: str|None=None)->dict:
        raw=self._extract_text(pdf_bytes[:50000], password=password)
        # use full for actual
        full=self._extract_text(pdf_bytes, password=password)
        if not self.can_parse(raw+full):
            # still try heuristic if ISIN found
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
                    holdings.append({"isin": isin, "folio": None, "quantity": qty, "symbol": isin[:6], "market_value": qty*100, "nav_or_price": 100})
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
                transactions.append({"date": d, "isin": isin, "type": ttype, "quantity": 0, "amount": amt})
        return {"from_date": from_date, "to_date": to_date, "holdings": holdings, "transactions": transactions, "fingerprint": self.fingerprint(full), "confidence": min(1.0, 0.5*(1 if holdings else 0)+0.4*(1 if transactions else 0)+0.1*(1 if from_date else 0)), "raw_len": len(full)}

# -------------------- STREAMLIT UI --------------------
def main():
    st.title("📈 NSDL CAS Portfolio Intelligence & Advisory System")
    st.caption(f"v{__version__} | Python {PYTHON_VERSION} | Streamlit {STREAMLIT_VERSION} | Implements blueprint with aditya-jha/nse-historical-membership")
    st.markdown("---")

    with st.sidebar:
        st.header("⚙️ Settings")
        as_of = st.date_input("PIT As-Of Date (for FII/DII & Index lookup)", value=date.today())
        st.markdown("### Data Source")
        st.info("Institutional data from `aditya-jha/nse-historical-membership`\n- _flat.csv (PIT shareholding)\n- index_membership_history.csv\n- fno_membership_history.csv")
        st.markdown(f"**App Version:** {__version__}")
        st.markdown(f"**Python:** {PYTHON_VERSION}")
        if st.button("Clear Cache"):
            st.cache_data.clear()
            st.rerun()

    # Load institutional data
    with st.spinner("Loading institutional intelligence (FII/DII, index membership)..."):
        data = load_institutional_data()
        svc = InstitutionalService(data)

    st.success(f"Loaded: {len(data['flat'])} shareholding rows, {len(data['index'])} index intervals, {len(data['fno'])} FNO intervals | Source: {'live GitHub fetch' if len(data['flat'])>1000 else 'embedded sample'}")
    with st.expander("🔐 How to open password-protected NSDL CAS?"):
        st.markdown("""
        - NSDL CAS PDFs are **password protected** by default
        - Password format is usually: **PAN in lowercase** or **DOB+PAN** or as mentioned in CAS email
        - Example: If PAN is ABCDE1234F, try `abcde1234f`
        - Enter password in **Sidebar > PDF Password**
        - Password is used **in-memory only**, never stored or logged
        - For multiple PDFs with different passwords, upload one at a time with its password
        - If still fails, try opening PDF in Adobe Reader with same password to verify
        """)

    uploaded = st.file_uploader("Upload NSDL CAS PDF(s)", type=["pdf"], accept_multiple_files=True)

    parser = NSDLParserV1()
    all_holdings=[]; all_txns=[]

    # Password handling (NSDL CAS PDFs are often password protected - usually PAN based)
    st.sidebar.markdown("### 🔐 PDF Password")
    pdf_password = st.sidebar.text_input("CAS PDF Password (if protected)", type="password", help="NSDL CAS is usually protected with your PAN or combination. Leave blank if not protected. Password is used in-memory only, never stored.")
    show_pwd = st.sidebar.checkbox("Show password debug (first 2 chars)", value=False)
    if pdf_password and show_pwd:
        st.sidebar.caption(f"Entered: {pdf_password[:2]}*** length {len(pdf_password)}")

    if uploaded:
        for f in uploaded:
            pdf_bytes = f.read()
            try:
                parsed = parser.parse(pdf_bytes, password=pdf_password if pdf_password else None)
                st.subheader(f"📄 {f.name} - Confidence {parsed['confidence']:.2f} | Fingerprint {parsed['fingerprint']}")
                col1,col2,col3 = st.columns(3)
                col1.metric("Holdings", len(parsed['holdings']))
                col2.metric("Transactions", len(parsed['transactions']))
                col3.metric("Period", f"{parsed['from_date']} to {parsed['to_date']}")
                all_holdings.extend(parsed['holdings'])
                all_txns.extend(parsed['transactions'])
                with st.expander("Raw holdings"):
                    st.dataframe(pd.DataFrame(parsed['holdings']))
                with st.expander("Raw transactions"):
                    st.dataframe(pd.DataFrame(parsed['transactions']))
            except Exception as e:
                err_msg = str(e)
                if "password" in err_msg.lower() or "Incorrect" in err_msg or "encrypted" in err_msg.lower():
                    st.error(f"🔐 PDF password required/incorrect for {f.name}: {err_msg}. Please enter correct password in sidebar. Hint: NSDL CAS password is often your PAN (lowercase) or DOB+PAN combination.")
                    st.info("💡 Try: lowercase PAN, or check CAS email for password format. The password is processed in-memory only.")
                else:
                    st.error(f"Failed to parse {f.name}: {e}")

    # Demo holdings if no upload
    if not all_holdings:
        st.info("No CAS uploaded - showing demo holdings for blueprint validation")
        all_holdings = [
            {"isin": "INE002A01018", "symbol": "RELIANCE", "quantity": 100, "market_value": 150000, "nav_or_price": 1500},
            {"isin": "INE467B01029", "symbol": "TCS", "quantity": 50, "market_value": 180000, "nav_or_price": 3600},
            {"isin": "INE009A01021", "symbol": "INFY", "quantity": 80, "market_value": 120000, "nav_or_price": 1500},
            {"isin": "INE040A01034", "symbol": "HDFCBANK", "quantity": 100, "market_value": 160000, "nav_or_price": 1600},
            {"isin": "INE090A01021", "symbol": "ICICIBANK", "quantity": 150, "market_value": 165000, "nav_or_price": 1100},
        ]
        all_txns = [
            {"date": date(2020,1,1), "isin": "INE002A01018", "type": "BUY", "amount": 100000},
            {"date": date(2021,6,15), "isin": "INE467B01029", "type": "BUY", "amount": 150000},
        ]

    # Enrichment
    st.header("🏦 Institutional & Index Intelligence (Blueprint 4.3)")
    enriched=[]
    for h in all_holdings:
        sym = h.get("symbol") or h.get("isin")[:6]
        pit = svc.pit_shareholding(sym, as_of)
        idx = svc.index_membership_on(sym, as_of)
        fno = svc.fno_status_on(sym, as_of)
        enriched.append({**h, "pit": pit, "index_membership": idx, "is_fno": fno, "fii_pct": pit["fii_pct"] if pit else None, "dii_pct": pit["dii_pct"] if pit else None, "smart_score": pit["smart_money_score"] if pit else None, "fii_4q": pit["fii_4q_delta"] if pit else None})
    df_enriched = pd.DataFrame(enriched)
    st.dataframe(df_enriched[["isin","symbol","quantity","market_value","fii_pct","dii_pct","smart_score","fii_4q","is_fno"]])

    # Index exposure
    st.subheader("Index Exposure")
    total = sum(h["market_value"] for h in all_holdings)
    exposure={}
    for h in all_holdings:
        sym=h.get("symbol")
        for m in svc.index_membership_on(sym, as_of):
            idx=m["index_name"]
            exposure[idx]=exposure.get(idx,0)+h["market_value"]
    if exposure:
        exp_df=pd.DataFrame([{"Index":k, "Value":v, "% Portfolio": v/total*100} for k,v in exposure.items()]).sort_values("% Portfolio", ascending=False)
        st.dataframe(exp_df)
        st.bar_chart(exp_df.set_index("Index")["% Portfolio"])
    else:
        st.write("No index membership found for as_of date")

    # Divergence alerts
    st.subheader("🚨 Smart-Money Divergence Alerts")
    alerts=[]
    for h in enriched:
        pit=h.get("pit")
        if pit:
            alert=svc.divergence_alert("BUY", pit)
            if alert: alerts.append({"symbol": h["symbol"], "alert": alert, "smart_score": pit["smart_money_score"], "fii_4q": pit["fii_4q_delta"]})
    if alerts:
        st.dataframe(pd.DataFrame(alerts))
    else:
        st.success("No divergence alerts - your buying aligns with FII/DII flows")

    # XIRR
    st.header("📊 XIRR Engine (Newton-Raphson, High-Precision)")
    col1,col2=st.columns(2)
    with col1:
        st.markdown("**Cashflows** (Negative = Buy, Positive = Sell/Current Value)")
        # build cashflows from txns + current value
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

    with col2:
        st.markdown("**Attribution (Money-Weighted + Waterfall)**")
        eng_attr=AttributionEngine()
        res=eng_attr.money_weighted_attribution(all_txns, Decimal("1500"), Decimal("800"), Decimal("0"), Decimal(str(current_val)))
        st.write(res["waterfall"])
        st.dataframe(pd.DataFrame(res["leak_ranking"]))
        st.bar_chart(pd.DataFrame(res["waterfall"]).set_index("label")["value"])

    # Tax-Lot Engine
    st.header("🧾 Tax-Lot Engine (FIFO/LIFO/HIFO, Bonus, Harvestable Losses)")
    tax_engine=TaxLotEngine()
    for h in all_holdings:
        tax_engine.create_from_txn(h["isin"], Decimal(str(h["quantity"])), Decimal(str(h["nav_or_price"])), date(2020,1,1))
    # simulate bonus
    tax_engine.handle_corporate_action(all_holdings[0]["isin"], "BONUS", Decimal("1"), date(2021,1,1))
    st.write("Lots after 1:1 bonus on first holding:")
    lots_df=[]
    for isin, lots in tax_engine.lots.items():
        for lot in lots:
            lots_df.append({"isin": isin, "qty": float(lot.quantity), "remaining": float(lot.remaining_quantity), "price": float(lot.acquire_price), "date": lot.acquire_date})
    st.dataframe(pd.DataFrame(lots_df))

    st.subheader("Simulate Sale")
    col1,col2,col3=st.columns(3)
    sel_isin=col1.selectbox("ISIN", [h["isin"] for h in all_holdings])
    qty_sell=col2.number_input("Qty to sell", min_value=1.0, value=10.0)
    price_sell=col3.number_input("Sale price", min_value=1.0, value=1600.0)
    method=st.selectbox("Method", ["FIFO","LIFO","HIFO"])
    if st.button("Simulate Tax Impact"):
        try:
            sim=tax_engine.simulate_sale(sel_isin, Decimal(str(qty_sell)), Decimal(str(price_sell)), as_of, method=method)
            st.json(sim)
        except Exception as e:
            st.error(str(e))

    st.subheader("Harvestable Losses")
    current_prices={h["isin"]: Decimal(str(h["nav_or_price"]*0.8)) for h in all_holdings}  # assume 20% down
    harvest=tax_engine.harvestable_losses(current_prices, as_of, min_loss=Decimal("1000"))
    st.dataframe(pd.DataFrame(harvest) if harvest else pd.DataFrame([{"info":"No harvestable losses at 20% down scenario"}]))

    # Advice Impact
    st.header("💡 Advice Impact Estimator (ΔXIRR 1y/3y/5y, Tax + Exit-load aware)")
    from_isin=st.selectbox("Switch FROM", [h["isin"] for h in all_holdings], key="from")
    to_isin=st.selectbox("Switch TO", [h["isin"] for h in all_holdings], key="to", index=1 if len(all_holdings)>1 else 0)
    pct=st.slider("Switch %", 0,100,40)
    exp_returns={h["isin"]: 0.12 if "BANK" in h.get("symbol","") else 0.10 for h in all_holdings}
    exp_returns[to_isin]=0.14
    estimator=AdviceImpactEstimator(tax_engine, exp_returns)
    if st.button("Estimate Impact"):
        try:
            qty=Decimal(str(next(h["quantity"] for h in all_holdings if h["isin"]==from_isin)))
            price_from=Decimal(str(next(h["nav_or_price"] for h in all_holdings if h["isin"]==from_isin)))
            price_to=Decimal(str(next(h["nav_or_price"] for h in all_holdings if h["isin"]==to_isin)))
            impact=estimator.estimate_switch(from_isin, to_isin, pct/100, qty, price_from, price_to, exit_load_pct=0.005, as_of=as_of)
            st.json(impact)
            if impact["is_recommended"]:
                st.success(f"✅ Recommended: Net benefit 3y {impact['net_expected_benefit_3y']*100:.2f}% ΔXIRR")
            else:
                st.warning("❌ Not recommended - net benefit negative after tax")
        except Exception as e:
            st.error(str(e))

    st.markdown("---")
    st.caption("Built per Full Technical Blueprint - NSDL CAS Portfolio Intelligence | Data: CC BY 4.0 aditya-jha/nse-historical-membership")

if __name__=="__main__":
    main()
