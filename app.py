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

def extract_with_fitz_v2(pdf_bytes, pw_list):
    try:
        import fitz
        # Try opening with password directly (newer fitz)
        for pw in pw_list:
            try:
                doc = fitz.open(stream=pdf_bytes, filetype="pdf")
                if doc.is_encrypted:
                    rc = doc.authenticate(pw)
                    if rc == 0:
                        # try open with password param
                        try:
                            doc = fitz.open(stream=pdf_bytes, filetype="pdf", password=pw)
                            if doc.is_encrypted:
                                continue
                        except:
                            continue
                txt = "\n".join([page.get_text("text") or page.get_text() or "" for page in doc])
                if len(txt.strip())>50:
                    return txt, f"fitz success with pw='{pw}' rc={doc.is_encrypted}"
            except Exception as e:
                continue
        # Last attempt no password
        try:
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            txt = "\n".join([page.get_text("text") or "" for page in doc])
            return txt, f"fitz no-pw -> {len(txt)} chars"
        except Exception as e:
            return "", f"fitz final error: {e}"
    except Exception as e:
        return "", f"fitz outer error: {e}"

def extract_with_pikepdf(pdf_bytes, pw_list):
    try:
        import pikepdf
        for pw in pw_list:
            try:
                pdf = pikepdf.open(io.BytesIO(pdf_bytes), password=pw)
                # Save decrypted to bytes
                out = io.BytesIO()
                pdf.save(out)
                out.seek(0)
                # Now try fitz on decrypted
                import fitz
                doc = fitz.open(stream=out.read(), filetype="pdf")
                txt = "\n".join([page.get_text("text") or "" for page in doc])
                if len(txt.strip())>50:
                    return txt, f"pikepdf+fitZ success with {pw}"
            except Exception as e:
                if "password" in str(e).lower():
                    continue
                continue
        return "", "pikepdf failed all pw"
    except Exception as e:
        return "", f"pikepdf not available or error: {e}"

def extract_with_pypdf2(pdf_bytes, pw_list):
    try:
        from PyPDF2 import PdfReader
        for pw in pw_list:
            try:
                reader = PdfReader(io.BytesIO(pdf_bytes))
                if reader.is_encrypted:
                    try:
                        rc = reader.decrypt(pw)
                    except:
                        rc = 0
                    if rc == 0:
                        continue
                txt = "\n".join([p.extract_text() or "" for p in reader.pages])
                if len(txt.strip())>50:
                    return txt, f"pypdf2 success with {pw}"
            except:
                continue
        return "", "pypdf2 failed"
    except Exception as e:
        return "", f"pypdf2 error: {e}"

def extract_text_robust(pdf_bytes, password=None):
    pw_attempts=[]
    if password:
        base=password.strip()
        # NSDL CAS: lowercase PAN, but also try uppercase, with spaces, first 8 chars etc
        variants=[
            base, base.lower(), base.upper(),
            base.lower()[:10], base.upper()[:10],
            base.lower().strip(), base.upper().strip(),
        ]
        # Deduplicate
        seen=set(); uniq=[]
        for p in variants:
            if p and p not in seen:
                uniq.append(p); seen.add(p)
        pw_attempts=uniq
    else:
        pw_attempts=[""]

    logs=[]
    # Try pikepdf first (most robust for encrypted)
    for extractor in [extract_with_pikepdf, extract_with_fitz_v2, extract_with_pypdf2]:
        txt, log = extractor(pdf_bytes, pw_attempts)
        logs.append(f"{extractor.__name__}: {log} -> {len(txt)} chars")
        if len(txt.strip())>50:
            return txt, "\n".join(logs)
    return "", "\n".join(logs)

def parse_holdings(full_text):
    holdings=[]
    isin_pat=r"\b([A-Z]{2}[A-Z0-9]{9}\d)\b"
    for line in full_text.split("\n"):
        m=re.search(isin_pat, line)
        if m:
            holdings.append({"isin": m.group(1), "symbol": m.group(1)[:6], "quantity": 1.0, "market_value": 100, "raw_line": line[:100]})
    d={}
    for h in holdings: d[h["isin"]]=h
    return list(d.values())

def main():
    st.title("CAS Portfolio Intelligence - v0.2.11")
    st.caption("Fixes 0 chars - tries pikepdf + fitz + pypdf2 with all PAN variants")

    with st.sidebar:
        pwd=st.text_input("PDF Password (PAN)", type="password", help="Enter PAN like ckcps3557g - will auto try lower/upper")
        if st.button("Clear Cache"): st.cache_data.clear(); st.rerun()

    data=load_data()
    st.success(f"Loaded instantly - {len(data)} rows - no cooking")

    uploaded=st.file_uploader("Upload CAS PDF", type=["pdf"])

    if uploaded:
        pdf_bytes=uploaded.read()
        st.write(f"File: {uploaded.name}, {len(pdf_bytes)} bytes")
        if not pwd:
            st.warning("Enter password in sidebar - NSDL uses lowercase PAN, e.g. ckcps3557g")
            return
        with st.spinner(f"Trying passwords: {pwd}, {pwd.lower()}, {pwd.upper()} with 3 decrypt engines..."):
            full_text, logs = extract_text_robust(pdf_bytes, password=pwd)

        st.code(logs)
        st.write(f"Extracted {len(full_text)} chars")

        if len(full_text.strip())<50:
            st.error(f"Failed: Text too short ({len(full_text)} chars) - password wrong or scanned PDF")
            st.markdown("""
            **Troubleshooting:**
            1. Your screenshot shows password `ckcps3557g` lowercase - correct for NSDL
            2. If still 0 chars, your CAS may be **KFintech/CAMS** which uses different password:
               - Try PAN + DOB: `CKCPS3557G01011990` or `ckcps3557g`
               - Try first 4 letters of PAN + DOB
            3. Check if file is corrupted - download fresh CAS from NSDL
            4. Logs above show which decrypt engine failed
            """)
            if full_text:
                st.code(full_text[:1000])
            return

        st.success(f"Extracted {len(full_text)} chars - parsing ISINs...")
        holdings=parse_holdings(full_text)
        if holdings:
            st.success(f"Found {len(holdings)} holdings!")
            st.dataframe(pd.DataFrame(holdings))
            with st.expander("Show text sample"):
                st.code(full_text[:4000])
        else:
            st.warning("No ISIN found - showing text sample:")
            st.code(full_text[:4000])

if __name__=="__main__":
    main()
