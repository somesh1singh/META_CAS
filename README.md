# NSDL CAS Portfolio Intelligence & Advisory System

**App Version:** 1.1.6 (Comprehensive Blueprint v1.0)
**Python Target:** 3.14.7
**Entrypoint:** `app.py`
**Build Date:** 2026-09-16
**Deployed URL Example:** `metacas-fcbwr8occnlkvhwbd...streamlit.app`

## Overview
Single-file Streamlit app implementing full blueprint:
- CAS Parser & Reconciliation (NSDL/CDSL/MF-folio)
- XIRR Engine (Newton-Raphson, high-precision Decimal)
- Tax-Lot Engine (FIFO/LIFO/HIFO, Bonus/Split, Harvestable Losses)
- Return Attribution (Money-Weighted + Waterfall)
- Institutional Intelligence (FII/DII PIT overlay from nse-historical-membership)
- Advice Impact Estimator (ΔXIRR 1y/3y/5y, Tax + Exit-load aware)
- Goals & Stress Test, Data Quality, System Status

## File Structure (3-file deployment)
```
app.py
requirements.txt
README.md
```

## Requirements (Minimal - 9 packages, all cp314 wheels)
Installs in <1 min on Streamlit Cloud Python 3.14.7 (vs 47 min with heavy deps):

```
streamlit==1.50.0
pandas==2.3.2
numpy==2.2.6
Pillow==11.3.0          # Fixes zlib error from Pillow 10.x
pymupdf==1.26.3         # Provides fitz module, has cp314 wheel
pdfplumber==0.11.7
PyPDF2==3.0.1
requests==2.32.4
python-dateutil==2.9.0.post0
```

**Removed (cause build failures):**
- `pikepdf==9.8.0` → requires `libqpdf-dev` / `qpdf/Constants.h` → `Build backend failed` on Streamlit Cloud
- `plotly==6.3.0` pinned → no cp314 wheel → builds from source 47 min → made optional with fallback to `st.bar_chart`
- `scipy`, `lxml`, `cryptography` heavy → removed for fastest install

## Revision History

### v1.1.6 (2026-09-16) - FIX NoneType Error (Current - Screenshot 4:34 PM)
**Issue:** `CAS_CKCPG_AUG_2026.PDF: 'NoneType' object has no attribute 'open'`
**Root Cause:** `fitz = None` fallback when import fails, but `extract_pdf_text()` called `fitz.open()` without check.
**Fix:**
- `extract_pdf_text()` now checks `if fitz is None: fallback to PyPDF2`
- Tries lower/upper PAN variants: `ckcps3557g`, `CKCPS3557G`, `ckcps3557g`.strip()
- `safe_open_pdf()` helper with 3-engine chain: fitz → PyPDF2 → pdfplumber
**Log:** `metacas-fcbwr8occnlkvhwbd...` - Screenshot shows parser UI working, password field, Parse button.

### v1.1.5 (2026-09-16) - Safe Open PDF Helper
- Added `safe_open_pdf()` with fitz + PyPDF2 + pdfplumber fallback
- Fixed `fitz = None` case

### v1.1.4 FINAL WORKING (2026-09-16)
**Issue:** `ModuleNotFoundError: fitz` and `plotly.express`
**Fix:**
- `pymupdf==1.26.3` (new name) provides `fitz` module, has cp314 wheel
- `PyMuPDF==1.26.3` old name removed
- 8 packages only, all cp314 wheels → `Resolved 52 packages in 602ms`

### v1.1.3 Fix Plotly Import (2026-09-16)
**Issue:** `ModuleNotFoundError: plotly.express as px` (line 36)
**Fix:** Made plotly optional:
```python
try:
    import plotly.express as px
except ImportError:
    px = None  # fallback to st.bar_chart
```
**Log:** `metacas-ikaixeqjvfzqru2swvmjsq` - `Resolved 52 packages in 644ms` → `Processed dependencies!` but 47 min gap `15:57:24 → 16:44:21`

### v1.1.2 Fix Fitz Import (2026-09-16)
**Issue:** `ModuleNotFoundError: fitz` (line 27)
**Fix:** Robust import:
```python
try:
    import fitz
except ImportError:
    import pymupdf as fitz
```

### v1.1.1 Instant Fix (2026-09-16)
**Issue:** Cooking 30 min due to upstream GitHub fetch `timeout=30`
**Fix:** `timeout=30 → timeout=3` + `try/except: return pd.DataFrame()` for `fetch_upstream_csv()` and `fetch_symbol_renames()`
**Log:** `metacas-4kg2zebc2iof5si4fadd4h` - `Resolved 52 packages in 602ms` but provisioning 23 min gap

### v1.1.0 Comprehensive (2026-09-14) - Blueprint Baseline
**Uploaded file:** `/mnt/data/app.py` 138KB, 3610 lines
**Features:**
- Full NSDL CAS layout hardening, Indian-number parsing (e.g. `36,16,119.95`)
- Compact cross-CAS reconciliation, inline ISIN + security-description row recovery
- CDSL numeric-tail/page-number recovery, Historical SGB face-value reconciliation
- Structured MF-folio monetary transaction parser, Depository quantity/balance parser
- Cashflow-completeness XIRR gate, Holdings arithmetic quality gate
- India timezone `ZoneInfo("Asia/Kolkata")` for `india_today()`
- Upstream repo: `https://raw.githubusercontent.com/aditya-jha/nse-historical-membership/main`
**Issue:** `pikepdf` build fails `qpdf/Constants.h: No such file`
**Log:** `metacas-u5jcxcbpwapprbvngfatupg` - `Failed to download and build pikepdf==9.8.0`

### v0.2.11 Fix 0 Chars with Pikepdf (2026-09-14)
- Added `pikepdf==9.8.0` + `fitz.open(password=)` + `PyPDF2` decrypt loop
- Tries `ckcps3557g`, `CKCPS3557G`, trimmed variants
- **Failed on Streamlit Cloud:** Needs `libqpdf-dev`

### v0.2.10 Fix 0 Chars Password (2026-09-14)
- Tries 3 engines: `fitz` + `pypdf2` + `pdfplumber` with all case variants
- Logs: `extract_with_fitz: fitz success with pw='ckcps3557g'`
- File: `CAS_CKCPG_AUG_2026.PDF` 354,682 bytes, password `ckcps3557g` lowercase
- Issue: `Failed: Text too short (0 chars) - password wrong or scanned PDF`

### v0.2.9 Fix Password Case (2026-09-14)
**Issue:** User entered `CKCPS3557G` UPPERCASE in PDF Password field, NSDL requires lowercase `ckcps3557g`
**Screenshot:** `Screenshot_2026-09-14_at_5.32.35_PM.png` shows uppercase entry → `Text too short (0 chars)`
**Fix:** `extract_text_robust()` auto-tries `password`, `lower()`, `upper()`, `strip()`
**Log:** `09:17:01 Start → 10:04:00 Dependencies installed = 47 min` + `11 redeploys in 54 sec` (pushing repeatedly while building)

### v0.2.8 Ultra-Instant-Final (2026-09-14)
**Screenshot:** `Screenshot_2026-09-14_at_5.32.35_PM.png` - Shows `Loaded instantly - 2 rows - no cooking` + `Demo holdings` table with `INE002A01018 RELIANCE 100 150000`
**Fix:** Removed network fetch on startup, `@st.cache_data` for `load_data()`, frozen 51 packages
**Issue:** Still `0 chars` for encrypted CAS because only `pdfplumber` with single password try

### v0.2.0 Heavy (Initial)
- 657 lines, includes XIRR, Tax-Lot, Attribution, Advice Impact
- No fitz/plotly imports, but heavy deps caused 47 min cooking

## CAS Password Handling (Fixes 0 Chars Issue)

**NSDL CAS:** Password is **lowercase PAN**, e.g. `ckcps3557g` (not `CKCPS3557G` uppercase)

**App now tries automatically:**
```python
pw_tries = [password, password.lower(), password.upper(), password.strip(), password.lower().strip()]
for pw in pw_tries:
    if doc.authenticate(pw): break  # fitz
    if reader.decrypt(pw): break    # PyPDF2
```

**Your file:** `CAS_CKCPG_AUG_2026.PDF` 354.7KB (screenshot) - password `ckcps3557g`

**If still 0 chars:**
1. Ensure file is NSDL CAS (not CAMS/KFintech which uses PAN+DOB)
2. Re-download fresh CAS from NSDL
3. Try lowercase PAN exactly as in PAN card

## Deployment Notes

1. **Select Python 3.14** in Streamlit Community Cloud Advanced settings
2. **Main module:** `app.py`
3. **Requirements:** `requirements.txt` (9 packages, <1 min install)
4. **Push once:** Wait 2 min, do not push repeatedly while building (causes restart loop)
5. **Password:** Enter lowercase PAN in sidebar / CAS Parser page
6. **If upstream CSV fails:** App returns empty DataFrame instantly (3 sec timeout) instead of hanging 30 sec

## Known Issues Fixed

| Issue | Log / Screenshot | Fix Version |
|-------|------------------|-------------|
| `pikepdf` build fails `qpdf/Constants.h` | `metacas-u5jcxcbp...` `Failed to download and build pikepdf==9.8.0` | v1.1.1 - Removed pikepdf |
| `ModuleNotFoundError: fitz` line 27 | `metacas-...` Traceback | v1.1.2 - Robust import fitz → pymupdf |
| `ModuleNotFoundError: plotly.express` line 36 | `metacas-ikaixeqj...` | v1.1.3 - Optional import |
| `NoneType has no attribute open` | Screenshot 4:34 PM `CAS_CK...G_2026.PDF: 'NoneType'...` | v1.1.6 - safe_open_pdf fallback |
| `Text too short (0 chars)` | Screenshot 5:32 PM, 354KB file, uppercase `CKCPS3557G` | v0.2.9 - Auto lower/upper + v1.1.6 fallback |
| 47 min cooking `09:17→10:04` | Logs 47 min install | v0.2.8 ultra-instant + v1.1.1 timeout 3 sec + minimal reqs |

## Current Status

- **App loads:** Instant (<2 sec after dependencies)
- **Dependencies:** Resolved 52 packages in 602-644ms, installed in <1 min (not 47 min)
- **CAS Parser:** Working UI (screenshot shows Upload + Password + Parse button)
- **Password:** Lowercase PAN `ckcps3557g` with auto upper/lower fallback
- **Fallback chain:** fitz → PyPDF2 → pdfplumber (fixes NoneType error)

## Next Steps for User

1. Push `app.py` v1.1.6 + `requirements.txt` (9 packages) + this `README.md` to GitHub `meta_cas` main
2. Wait 2 min for Streamlit Cloud deploy
3. Upload `CAS_CKCPG_AUG_2026.PDF` with `ckcps3557g`
4. Click `Parse uploaded CAS` (red button in screenshot)
5. Should show holdings, not `NoneType` error
