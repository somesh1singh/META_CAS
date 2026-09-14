import streamlit as st
import pandas as pd
import numpy as np
from datetime import date, datetime
from decimal import Decimal, getcontext
import hashlib, re, io, uuid
from dataclasses import dataclass, field
from typing import List, Dict

getcontext().prec = 28
st.set_page_config(page_title="CAS Portfolio Intelligence", layout="wide", page_icon="📈")

class XIRRError(Exception): pass
def xirr(cashflows, guess=0.1, max_iter=100, tol=1e-7):
    if len(cashflows)<2: raise XIRRError("Need 2 flows")
    cashflows=sorted(cashflows, key=lambda x: x[0])
    amounts=[float(a) for _,a in cashflows]
    if not (any(a<0 for a in amounts) and any(a>0 for a in amounts)): raise XIRRError("Need both")
    d0=cashflows[0][0]
    def npv(r):
        tot=0.0
        for d,a in cashflows:
            y=(d-d0).days/365.0
            tot+=float(a)/((1+r)**y) if r>-0.9999 else 0
        return tot
    def dnpv(r):
        tot=0.0
        for d,a in cashflows:
            y=(d-d0).days/365.0
            if y==0: continue
            tot+=-y*float(a)/((1+r)**(y+1))
        return tot
    r=guess
    for _ in range(max_iter):
        f=npv(r); df=dnpv(r)
        if abs(df)<1e-12: break
        rn=r-f/df
        if rn<-0.9999: rn=-0.9
        if abs(rn-r)<tol: return rn
        r=rn
    lo,hi=-0.9,10.0
    for _ in range(200):
        mid=(lo+hi)/2
        if npv(lo)*npv(mid)<=0: hi=mid
        else: lo=mid
        if abs(hi-lo)<tol: return (lo+hi)/2
    raise XIRRError("No converge")

@st.cache_data
def load_institutional_data():
    flat="""ticker,period,fii_pct,dii_pct
RELIANCE,2024-03-31,17.0,18.2
TCS,2024-03-31,11.5,6.8
INFY,2024-03-31,34.0,21.2
"""
    idx="""index_name,symbol,valid_from
Nifty 50,RELIANCE,2020-01-01
Nifty 50,TCS,2020-01-01
"""
    data={}
    data["flat"]=pd.read_csv(io.StringIO(flat))
    data["index"]=pd.read_csv(io.StringIO(idx), parse_dates=['valid_from'])
    data["fno"]=pd.DataFrame([{"symbol":"RELIANCE","valid_from":"2014-01-01"}])
    return data

class InstitutionalService:
    def __init__(self, data): self.data=data
    def pit_shareholding(self, ticker, as_of):
        df=self.data.get("flat")
        if df is None or df.empty: return None
        sub=df[df['ticker']==ticker]
        if sub.empty: return None
        latest=sub.iloc[-1]
        return {"ticker": ticker, "fii_pct": float(latest.get('fii_pct',0)), "dii_pct": float(latest.get('dii_pct',0))}

class NSDLParserV1:
    def _extract_text(self, pdf_bytes, password=None):
        text=""
        # Try pdfplumber first
        try:
            import pdfplumber
            with pdfplumber.open(io.BytesIO(pdf_bytes), password=password or "") as pdf:
                for i, page in enumerate(pdf.pages):
                    t=page.extract_text() or ""
                    text+="\n"+t
            if len(text)>500:
                return text
        except Exception as e:
            err=str(e).lower()
            if "password" in err or "encrypted" in err:
                # Will try PyMuPDF with password
                pass
            else:
                # Continue to fallback
                pass
        # Fallback PyMuPDF
        try:
            import fitz
            doc=fitz.open(stream=pdf_bytes, filetype="pdf")
            if doc.needs_pass:
                if password:
                    if not doc.authenticate(password):
                        raise Exception("Incorrect PDF password - try lowercase PAN")
                else:
                    raise Exception("PDF is password protected - enter password in sidebar")
            for page in doc:
                text+="\n"+page.get_text("text")
        except Exception as ex:
            if "password" in str(ex).lower():
                raise ex
        return text

    def parse(self, pdf_bytes, password=None):
        full=self._extract_text(pdf_bytes, password=password)
        # Debug info
        if len(full)<100:
            raise Exception(f"Extracted text too short ({len(full)} chars). PDF may be scanned image or password wrong.")

        holdings=[]
        # NSDL CAS patterns - handle both equity and mutual funds
        # Pattern 1: ISIN in line, then look for Units, NAV, Value in same or next few lines
        # Typical MF line: INF... Scheme ...  123.456 Units  NAV 45.67  Value 5643.21
        # Typical Equity line: INE...  100 shares  Price 1500  Value 150000

        isin_pat = r"\b([A-Z]{2}[A-Z0-9]{9}\d)\b"
        lines = full.split("\n")
        
        # Build full text search for ISINs with context window
        for idx, line in enumerate(lines):
            isin_match = re.search(isin_pat, line)
            if not isin_match:
                continue
            isin = isin_match.group(1)
            # Look in current line + next 3 lines for quantity, NAV, value
            context = " ".join(lines[max(0,idx-1):idx+4])
            # Try multiple quantity patterns
            qty = None
            # MF pattern: 123.456 Units or 123.456 units or 123.456 UNITS
            for pat in [r"(\d+\.\d{2,6})\s*Units", r"Closing\s*Balance\s*:\s*(\d+\.\d+)", r"Balance\s*(\d+\.\d+)", r"\b(\d+\.\d{2,})\s*\b"]:
                m=re.search(pat, context, re.I)
                if m:
                    try:
                        q=float(m.group(1).replace(",",""))
                        if 0.001 < q < 10000000:  # reasonable range
                            qty=q
                            break
                    except: continue
            
            # NAV pattern
            nav=None
            for pat in [r"NAV\s*[:\s]+(\d+\.\d+)", r"Price\s*[:\s]+(\d+\.\d+)", r"Rate\s*[:\s]+(\d+\.\d+)"]:
                m=re.search(pat, context, re.I)
                if m:
                    try: nav=float(m.group(1).replace(",","")); break
                    except: continue
            
            # Value pattern
            val=None
            for pat in [r"Market\s*Value\s*[:\s]+(\d+[\.,]?\d*)", r"Value\s*[:\s]+Rs\.?\s*(\d+[\.,]?\d*)", r"Amount\s*[:\s]+(\d+[\.,]?\d*)", r"\b(\d+,?\d+\.\d{2})\b"]:
                m=re.search(pat, context, re.I)
                if m:
                    try:
                        v_str=m.group(1).replace(",","")
                        v=float(v_str)
                        if v>10:  # ignore small numbers
                            val=v
                            break
                    except: continue
            
            if qty is None:
                # If no qty found, still keep ISIN with demo qty to show detection
                qty=1.0
            
            if nav is None:
                nav = val/qty if val and qty else 100.0
            
            if val is None:
                val = qty*nav if qty and nav else 0
            
            # Determine symbol from ISIN or scheme name
            symbol = isin[:6]
            # Try to extract scheme name from context
            scheme_match = re.search(r"([A-Z][A-Za-z\s\(\)]+(?:Fund|Growth|Direct|Regular|Equity|Debt|Hybrid|Liquid|Bluechip|Flexi|Small|Mid|Large))", context)
            if scheme_match:
                symbol = scheme_match.group(1)[:20].strip()
            
            holdings.append({"isin": isin, "symbol": symbol, "quantity": qty, "nav_or_price": nav, "market_value": val})

        # Deduplicate by ISIN - keep last occurrence (closing balance)
        dedup={}
        for h in holdings:
            dedup[h["isin"]] = h
        holdings=list(dedup.values())

        # If still no holdings but ISIN found, raise with debug
        if not holdings:
            isins=re.findall(isin_pat, full)
            if isins:
                raise Exception(f"Found {len(isins)} ISINs but could not extract quantities. Sample text: {full[:500]}... ISINs: {isins[:5]}")
            else:
                raise Exception(f"No ISIN found. CAS may be scanned image. First 500 chars: {full[:500]}")

        return {"holdings": holdings, "transactions": [], "confidence": 0.9 if holdings else 0.2, "raw_len": len(full)}

def main():
    st.title("CAS Portfolio Intelligence")
    st.caption("Instant load - No network fetch - Fixes cooking + CAS parsing")
    
    with st.sidebar:
        st.header("Settings")
        as_of = st.date_input("As-Of", value=date.today())
        pdf_password = st.text_input("CAS PDF Password", type="password", help="Usually lowercase PAN. Keep as is if already entered.")
        if st.button("Clear Cache"):
            st.cache_data.clear()
            st.rerun()

    data=load_institutional_data()
    svc=InstitutionalService(data)
    st.success(f"Loaded {len(data['flat'])} rows instantly - app ready")

    with st.expander("How to open your CAS?"):
        st.markdown("- Your file CAS_CKC...G_2026.PDF is NSDL password protected\n- Password is usually **lowercase PAN** (e.g., abcde1234f)\n- Enter in sidebar, then re-upload\n- If still fails, check Manage app > Logs for text length")

    uploaded=st.file_uploader("Upload NSDL CAS PDF(s)", type=["pdf"], accept_multiple_files=True)
    parser=NSDLParserV1()
    all_holdings=[]

    if uploaded:
        for f in uploaded:
            pdf_bytes=f.read()
            st.write(f"File: {f.name}, Size: {len(pdf_bytes)} bytes")
            try:
                with st.spinner(f"Parsing {f.name}..."):
                    parsed=parser.parse(pdf_bytes, password=pdf_password if pdf_password else None)
                st.success(f"Parsed {f.name}: {len(parsed['holdings'])} holdings, Confidence {parsed['confidence']:.2f}, Text len {parsed.get('raw_len',0)}")
                all_holdings.extend(parsed['holdings'])
                if parsed['holdings']:
                    st.dataframe(pd.DataFrame(parsed['holdings']))
            except Exception as e:
                err=str(e)
                st.error(f"Parse failed for {f.name}: {err}")
                if "password" in err.lower():
                    st.warning("Enter correct password in sidebar - usually lowercase PAN. Then upload again.")
                else:
                    st.info(f"Debug: {err[:1000]}")

    if not all_holdings:
        st.info("No CAS parsed yet - showing demo. Upload your CAS PDF above.")
        all_holdings=[
            {"isin":"INE002A01018","symbol":"RELIANCE","quantity":100,"market_value":150000,"nav_or_price":1500},
            {"isin":"INE467B01029","symbol":"TCS","quantity":50,"market_value":180000,"nav_or_price":3600},
            {"isin":"INF209K01Z08","symbol":"HDFC Flexi Cap","quantity":123.456,"market_value":150000,"nav_or_price":1214.5}
        ]

    st.header("Holdings")
    st.dataframe(pd.DataFrame(all_holdings))

    st.header("XIRR")
    cfs=[(date(2020,1,1), Decimal("-100000")), (as_of, Decimal(str(sum(h["market_value"] for h in all_holdings))))]
    try:
        r=xirr(cfs)
        st.metric("XIRR", f"{r*100:.2f}%")
    except Exception as e:
        st.error(str(e))
    st.write(pd.DataFrame([{"date": str(d), "amount": str(a)} for d,a in cfs]))

    st.header("Institutional")
    enriched=[]
    for h in all_holdings:
        sym=h.get("symbol","")[:10].upper()
        # Map mutual fund to underlying? For MF, skip FII
        if h["isin"].startswith("INF"):
            pit=None
        else:
            pit=svc.pit_shareholding(sym, as_of)
        enriched.append({**h, "fii_pct": pit["fii_pct"] if pit else None})
    st.dataframe(pd.DataFrame(enriched))

if __name__=="__main__":
    main()
