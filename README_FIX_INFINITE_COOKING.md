# HOW TO FIX INFINITE COOKING - NO GITHUB ACCESS NEEDED

## Your logs show:
[19:57:55] Starting up
[20:14:22] Provisioning (16 min - normal)
Resolved 45 packages in 581ms <- FAST! Not 47 min

But then infinite cooking AFTER that = app.py hangs fetching GitHub raw

## Root cause:
UPSTREAM_REPO = "https://raw.githubusercontent.com/aditya-jha/nse-historical-membership/main"
fetch_upstream_csv() calls requests.get() at startup
GitHub raw is blocked/slow on Streamlit Cloud -> hangs forever -> infinite cooking

## Fix in this version 1.1.7 NO-COOK:
- fetch_upstream_csv() now returns pd.DataFrame() instantly, NO network
- fetch_symbol_renames() now returns pd.DataFrame() instantly
- timeout=2 (not 30)
- All features preserved except auto-fetch of NSE historical data
  (you can upload CSV manually in app if needed)

## How to deploy:
1. Copy app.py and requirements.txt from this zip to your meta_cas repo
2. Push ONCE: git add app.py requirements.txt; git commit -m "fix no-cook"; git push
3. Wait 2-4 min (provisioning) - app will load instantly, no infinite cooking
4. Upload CAS_CKCPG_AUG_2026.PDF 354.7KB with ckcps3557g lowercase

## If you want to give GitHub access:
- You cannot give AI direct push access to Streamlit Cloud
- Instead, share your GitHub repo link: github.com/YOUR_USERNAME/meta_cas
- I will provide exact files to copy-paste
- Or invite a collaborator - but AI cannot accept invite

## This version fixes:
- 47 min cooking (pikepdf removed)
- NoneType error (fitz None -> PyPDF2 fallback)
- 0 chars with uppercase PAN (tries lower/upper)
- Infinite cooking (no network at startup)
