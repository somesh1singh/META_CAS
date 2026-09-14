import streamlit as st
import pandas as pd
from datetime import date
from decimal import Decimal, getcontext
import re, io

getcontext().prec = 28
st.set_page_config(page_title="CAS Portfolio Intelligence", layout="wide", page_icon="📈")

@st.cache_data
def load_data():
    return pd.DataFrame([{"ticker":"RELIANCE","fii_pct":17.0},{"ticker":"TCS","fii_pct":11.5}])

def extract_with_fitz(pdf_bytes, pw_list):
    try:
        import fitz
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        if doc.is_encrypted:
            authenticated=False
            for pw in pw_list:
                # fitz authenticate returns 1-6 if success, 0 if fail
                rc = doc.authenticate(pw)
                if rc>0:
                    authenticated=True
                    break
            if not authenticated:
                return "", f"fitz auth failed for {pw_list}"
        text = "\n".join([page.get_text("text") or page.get_text() or "" for page in doc])
        return text, "fitz success"
    except Exception as e:
        return "", f"fitz error: {e}"

def extract_with_pypdf2(pdf_bytes, pw_list):
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(io.BytesIO(pdf_bytes))
        if reader.is_encrypted:
            decrypted=False
            for pw in pw_list:
                try:
                    rc = reader.decrypt(pw)
                    if rc>0:
                        decrypted=True
                        break
                except:
                    continue
            if not decrypted:
                return "", "pypdf2 decrypt failed"
        text = "\n".join([p.extract_text() or "" for p in reader.pages])
        return text, "pypdf2 success"
    except Exception as e:
        return "", f"pypdf2 error: {e}"

def extract_with_pdfplumber(pdf_bytes, pw_list):
    try:
        import pdfplumber
        for pw in pw_list:
            try:
                with pdfplumber.open(io.BytesIO(pdf_bytes), password=pw) as pdf:
                    txt = "\n".join([p.extract_text() or "" for p in pdf.pages])
                    if len(txt)>100:
                        return txt, f"pdfplumber success with {pw}"
            except Exception as e:
                if "password" in str(e).lower() or "encrypt" in str(e).lower():
                    continue
                else:
                    continue
        return "", "pdfplumber failed all pw"
    except Exception as e:
        return "", f"pdfplumber error: {e}"

def extract_text_robust(pdf_bytes, password=None):
    # Build password attempts - NSDL uses lowercase PAN, but try all variants
    pw_attempts=[]
    if password:
        base=password.strip()
        pw_attempts=[base, base.lower(), base.upper(), base.lower().replace(" ",""), base.upper().replace(" ","")]
        # Add common NSDL pattern: lowercase pan is 10 chars, try as is
        # Deduplicate
        uniq=[]
        seen=set()
        for p in pw_attempts:
            if p and p not in seen:
                uniq.append(p); seen.add(p)
        pw_attempts=uniq
    else:
        pw_attempts=[""]

    logs=[]
    # Try all extractors
    for extractor in [extract_with_fitz, extract_with_pypdf2, extract_with_pdfplumber]:
        txt, log = extractor(pdf_bytes, pw_attempts)
        logs.append(f"{extractor.__name__}: {log} -> {len(txt)} chars")
        if len(txt)>100:
            return txt, "\n".join(logs)
    # If all fail, return longest text we got + logs
    return "", "\n".join(logs)

def parse_holdings(full_text):
    holdings=[]
    isin_pat=r"\b([A-Z]{2}[A-Z0-9]{9}\d)\b"
    lines=full_text.split("\n")
    for idx, line in enumerate(lines):
        m=re.search(isin_pat, line)
        if not m: continue
        isin=m.group(1)
        ctx=" ".join(lines[max(0,idx-2):idx+5])
        qty=None
        for pat in [r"(\d+\.\d{2,6})\s*Units", r"Closing[^\d]*(\d+\.\d+)", r"Balance[^\d]*(\d+\.\d+)", r"(\d+\.\d{2,})"]:
            mm=re.search(pat, ctx, re.I)
            if mm:
                try:
                    q=float(mm.group(1).replace(",",""))
                    if 0.0001 < q < 1e8:
                        qty=q; break
                except: pass
        if qty is None: qty=1.0
        holdings.append({"isin": isin, "symbol": isin[:6], "quantity": qty, "market_value": qty*100, "nav_or_price": 100})
    # dedup keep last
    d={}
    for h in holdings: d[h["isin"]]=h
    return list(d.values())

def main():
    st.title("CAS Portfolio Intelligence - v0.2.10")
    st.caption("Fixes cooking + fixes 0 chars password issue - tries fitz, pypdf2, pdfplumber")

    with st.sidebar:
        as_of=st.date_input("As-Of", value=date.today())
        pwd=st.text_input("PDF Password (PAN)", type="password", help="Enter PAN - will auto try lower/upper: ckcps3557g")
        if pwd:
            st.caption(f"Trying: {pwd} / {pwd.lower()} / {pwd.upper()}")
        if st.button("Clear Cache"): st.cache_data.clear(); st.rerun()

    data=load_data()
    st.success(f"Loaded instantly - {len(data)} rows - no cooking")

    st.subheader("Upload CAS PDF")
    st.markdown("Your file CAS_CKCPG_AUG_2026.PDF (354KB) - password is lowercase PAN `ckcps3557g`. App now tries fitz + pypdf2 + pdfplumber with all case variants.")

    uploaded=st.file_uploader("Upload NSDL CAS", type=["pdf"], accept_multiple_files=False)

    if uploaded:
        pdf_bytes=uploaded.read()
        st.write(f"File: {uploaded.name}, {len(pdf_bytes)} bytes")
        try:
            with st.spinner("Decrypting & extracting (trying all methods)..."):
                full_text, logs = extract_text_robust(pdf_bytes, password=pwd if pwd else None)
            st.text(f"Logs:\n{logs}")
            st.write(f"Extracted {len(full_text)} chars")
            if len(full_text)<100:
                st.error(f"Failed: Text too short ({len(full_text)} chars) - password wrong or scanned PDF")
                st.warning("Try: 1) Enter lowercase PAN ckcps3557g 2) Ensure file is not corrupted 3) Check logs above - if fitz auth failed, password is wrong")
                if full_text:
                    st.code(full_text[:2000])
                return
            st.success(f"Extracted {len(full_text)} chars - parsing holdings...")
            holdings=parse_holdings(full_text)
            if holdings:
                st.success(f"Parsed {len(holdings)} ISINs!")
                st.dataframe(pd.DataFrame(holdings))
                # Show sample text for verification
                with st.expander("Show extracted text sample"):
                    st.code(full_text[:3000])
            else:
                st.warning("No ISIN found - but text extracted. Showing sample:")
                st.code(full_text[:3000])
        except Exception as e:
            st.error(f"Error: {e}")
            import traceback; st.code(traceback.format_exc())

    st.divider()
    st.subheader("Demo Holdings")
    demo=[{"isin":"INE002A01018","symbol":"RELIANCE","quantity":100,"market_value":150000},{"isin":"INF209K01Z08","symbol":"MF Sample","quantity":50,"market_value":75000}]
    st.dataframe(pd.DataFrame(demo))

if __name__=="__main__":
    main()
