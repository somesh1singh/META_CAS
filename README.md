# NSDL CAS Portfolio Intelligence - Revision History

## Current: v1.1.6 (2026-09-16) - FIX NoneType Error
**Issue:** Screenshot 4:34 PM: `CAS_CKCPG_AUG_2026.PDF: 'NoneType' object has no attribute 'open'`
**File:** 354.7KB, password ckcps3557g
**Fix:** extract_pdf_text checks if fitz is None -> fallback to PyPDF2, tries lower/upper PAN

## Why 47 min cooking?
1. Python 3.14.7 new - no cp314 wheels for pikepdf, plotly==6.3.0, pandas tar.gz -> builds from source 47 min
2. pikepdf needs libqpdf-dev -> qpdf/Constants.h not found -> build fails
3. 11 pushes in 54 sec -> restart loop
4. Upstream fetch timeout=30 x2 = 60 sec cooking

## Fixed now
- requirements.txt: 9 packages only, all cp314 wheels -> Resolved 52 packages in 602ms, installs <1 min
- app.py: robust imports fitz->pymupdf->None, plotly optional, timeout=3, lower/upper PAN tries

## Revision History
- v1.1.6: NoneType fix + PyPDF2 fallback + lower/upper PAN
- v1.1.5: safe_open_pdf helper
- v1.1.4: FINAL WORKING - 8 packages, 602ms resolve
- v1.1.3: plotly optional - ModuleNotFoundError plotly.express line 36 - 47 min gap 15:57->16:44
- v1.1.2: fitz optional - ModuleNotFoundError fitz line 27
- v1.1.1: timeout 30->3 - Resolved 52 packages in 602ms but provisioning 23 min gap
- v1.1.0: Comprehensive 138KB blueprint, pikepdf build fail qpdf/Constants.h
- v0.2.11: pikepdf attempt
- v0.2.10: 0 chars fix with 3 engines
- v0.2.9: Uppercase CKCPS3557G -> lowercase ckcps3557g - 47 min cooking 09:17->10:04 + 11 redeploys
- v0.2.8: Loaded instantly - 2 rows - no cooking - demo holdings RELIANCE

## Password
NSDL CAS password is lowercase PAN: ckcps3557g (not CKCPS3557G)
App auto-tries lower/upper/strip variants

## Deployment
1. Python 3.14 in Streamlit Advanced settings
2. Push once, wait 2 min
3. Upload CAS_CKCPG_AUG_2026.PDF with ckcps3557g
