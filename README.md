# NSDL CAS Portfolio Intelligence & Advisory System

Industry-grade portfolio analytics from NSDL CAS PDFs - reconstruct multi-year portfolios, enrich with point-in-time FII/DII/promoter shareholding, index membership, F&O status, and generate advice to improve XIRR.

## Architecture

```
User Uploads CAS PDFs (Streamlit, with password support)
  -> NSDLParserV1 (pdfplumber + PyMuPDF fallback, fingerprinting, confidence)
  -> Holdings + Transactions Ledger
  -> Institutional Overlay (nse-historical-membership)
  -> XIRR, Tax-Lot, Attribution, Advice Impact
```

## Deployment

Push app.py, requirements.txt, README.md to GitHub repo root. Streamlit Cloud: Main file app.py.

Local: pip install -r requirements.txt && streamlit run app.py

## Password-Protected PDFs

NSDL CAS PDFs are password protected. Enter password in Sidebar > PDF Password (masked, in-memory only). Usually lowercase PAN.

## Data Source

aditya-jha/nse-historical-membership (MIT/CC BY 4.0)

## Note on Python 3.14

If Streamlit Cloud fails to build with Python 3.14 (pillow/zlib), use Python 3.12 in Advanced Settings or add runtime.txt with python-3.12. Requirements are now unpinned to allow latest wheels for 3.14.
