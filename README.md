# NSDL CAS Portfolio Intelligence & Advisory System

**Python 3.14 | Streamlit | Institutional Intelligence via aditya-jha/nse-historical-membership**

Industry-grade portfolio analytics from NSDL CAS PDFs - reconstruct multi-year portfolios, enrich with point-in-time FII/DII/promoter shareholding, index membership (42 indices), F&O status, and generate evidence-based advice to improve XIRR.

## Architecture (Blueprint Compliance)

```
User Uploads CAS PDFs (Streamlit)
  -> NSDLParserV1 (pdfplumber + PyMuPDF fallback, fingerprinting, confidence scoring, password support)
  -> Holdings + Transactions Ledger (ISIN as primary key)
  -> Data Enrichment Layer
      -> nse-historical-membership (embedded fallback + live fetch)
      -> PIT shareholding lookup: latest quarterly <= T, QoQ, 4Q delta, smart_money_score
      -> Index membership exposure: Was stock in Nifty 50 / IT / Midcap on date X?
      -> F&O status
  -> Analytics Engine
      -> XIRR (Newton-Raphson, Decimal)
      -> Tax-Lot Engine (FIFO/LIFO/HIFO, bonus/split, STCG/LTCG, harvestable losses)
      -> Return Attribution (money-weighted waterfall + leak ranking + Brinson-Fachler)
  -> Advice Impact Estimator (tax + exit-load aware ΔXIRR 1y/3y/5y, confidence, evidence)
```

## Features

- **CAS Ingestion**: Versioned parser, fingerprint, confidence, multi-CAS reconciliation, password-protected PDF support
- **Institutional Overlay**: PIT FII/DII/promoter, smart_money_score
- **Index Intelligence**: 42 indices via index_membership_history.csv [valid_from, valid_to)
- **F&O Intelligence**: fno_membership_history.csv
- **High-XIRR Engines**: Tax-lot, attribution, advice impact
- **Streamlit UI**: Upload (with password prompt), holdings table, PIT enrichment, divergence alerts, XIRR, tax simulator

## Deployment (GitHub + Streamlit Cloud)

1. Push these 3 files to GitHub repo root: app.py, requirements.txt, README.md
2. Streamlit Cloud: New App -> Select repo -> Main file: app.py
3. Local: pip install -r requirements.txt && streamlit run app.py

## Password-Protected PDFs

NSDL CAS PDFs are password protected. App prompts for password in sidebar:
- Enter password in Sidebar > PDF Password (masked)
- Supports pdfplumber + PyMuPDF decryption
- In-memory only, never stored
- Hint: usually lowercase PAN

## Data Source

Vendored sample from [aditya-jha/nse-historical-membership](https://github.com/aditya-jha/nse-historical-membership) (MIT/CC BY 4.0)

## Disclaimer

Educational analysis only; not SEBI-regulated investment advice.

## License

MIT (code) + CC BY 4.0 (data)
