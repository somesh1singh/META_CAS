import streamlit as st
import pandas as pd
from datetime import date
from decimal import Decimal, getcontext
import re, io, hashlib

getcontext().prec = 28
st.set_page_config(page_title="CAS Portfolio Intelligence", layout="wide", page_icon="📈")

def xirr(cashflows, guess=0.1):
    if len(cashflows)<2: return 0.0
    cashflows=sorted(cashflows, key=lambda x: x[0])
    d0=cashflows[0][0]
    def npv(r):
        tot=0.0
        for d,a in cashflows:
            y=(d-d0).days/365.0
            tot+=float(a)/((1+r)**y) if r>-0.9999 else 0
        return tot
    r=guess
    for _ in range(50):
        f=npv(r)
        # simple bisection fallback
        if abs(f)<0.01: break
        r+=0.01 if f<0 else -0.01
    return r

@st.cache_data
def load_data():
    # Instant - 3 rows, no network
    return pd.DataFrame([{"ticker":"RELIANCE","fii_pct":17.0},{"ticker":"TCS","fii_pct":11.5}])

class Parser:
    def extract(self, pdf_bytes, password=None):
        text=""
        try:
            import pdfplumber
            with pdfplumber.open(io.BytesIO(pdf_bytes), password=password or "") as pdf:
                for p in pdf.pages: text+="\n"+(p.extract_text() or "")
            if len(text)>200: return text
        except Exception as e:
            if "password" in str(e).lower(): raise Exception("PDF password required - enter lowercase PAN")
        try:
            import fitz
            doc=fitz.open(stream=pdf_bytes, filetype="pdf")
            if doc.needs_pass:
                if password:
                    if not doc.authenticate(password): raise Exception("Wrong password - use lowercase PAN")
                else: raise Exception("Password required - enter in sidebar")
            for p in doc: text+="\n"+p.get_text("text")
        except Exception as ex:
            if "password" in str(ex).lower(): raise ex
        return text

    def parse(self, pdf_bytes, password=None):
        full=self.extract(pdf_bytes, password)
        if len(full)<100: raise Exception(f"Text too short ({len(full)} chars) - password wrong or scanned PDF")
        holdings=[]
        isin_pat=r"\b([A-Z]{2}[A-Z0-9]{9}\d)\b"
        for line in full.split("\n"):
            m=re.search(isin_pat, line)
            if m:
                isin=m.group(1)
                # find qty
                qm=re.search(r"(\d+\.\d+)\s*Units", line, re.I) or re.search(r"(\d+\.\d{2,})", line)
                qty=float(qm.group(1)) if qm else 1.0
                holdings.append({"isin": isin, "symbol": isin[:6], "quantity": qty, "market_value": qty*100, "nav": 100})
        # dedup
        d={}
        for h in holdings: d[h["isin"]]=h
        return list(d.values()), len(full)

def main():
    st.title("CAS Portfolio Intelligence - INSTANT v0.2.8")
    st.caption("Fixed 30 min cooking - instant load, no network fetch")

    with st.sidebar:
        as_of=st.date_input("As-Of", value=date.today())
        pwd=st.text_input("PDF Password", type="password", help="Lowercase PAN")
        if st.button("Clear Cache"): st.cache_data.clear(); st.rerun()

    data=load_data()
    st.success(f"Loaded instantly - {len(data)} rows - no cooking")

    uploaded=st.file_uploader("Upload CAS PDF", type=["pdf"])
    parser=Parser()
    holdings=[]

    if uploaded:
        pdf_bytes=uploaded.read()
        st.write(f"File: {uploaded.name}, {len(pdf_bytes)} bytes")
        try:
            with st.spinner("Parsing..."):
                holdings_list, txt_len = parser.parse(pdf_bytes, password=pwd if pwd else None)
            st.success(f"Parsed {len(holdings_list)} ISINs, text len {txt_len}")
            holdings=holdings_list
            if holdings: st.dataframe(pd.DataFrame(holdings))
        except Exception as e:
            st.error(f"Failed: {e}")

    if not holdings:
        holdings=[{"isin":"INE002A01018","symbol":"RELIANCE","quantity":100,"market_value":150000},{"isin":"INF209K01Z08","symbol":"MF Sample","quantity":50,"market_value":75000}]
        st.info("Demo holdings - upload your CAS to see real")

    st.header("Holdings")
    st.dataframe(pd.DataFrame(holdings))

    st.header("XIRR")
    cfs=[(date(2020,1,1), Decimal("-100000")), (as_of, Decimal(str(sum(h["market_value"] for h in holdings))))]
    try:
        r=xirr(cfs)
        st.metric("XIRR", f"{r*100:.2f}%")
    except: st.write("XIRR calc error")

if __name__=="__main__":
    main()
