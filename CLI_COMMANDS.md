# Meesho E2E Engine — CLI Reference Manual

This manual provides ready-to-run copy-paste commands for all features of the Meesho E2E Engine, optimized for Windows PowerShell and Command Prompt.

---

## Quick Navigation
1. [Global Proxy & Egress Configuration](#1-global-proxy--egress-configuration)
2. [Product Pricing Comparison (Custom Pincodes vs 🇮🇳 All-India Scan)](#2-product-pricing-comparison)
3. [First Order Discount (FOD) Classification](#3-first-order-discount-fod-classification)
4. [Account Classification from Order History](#4-account-classification-from-order-history)
5. [Bulk Product & Address Assignment](#5-bulk-product--address-assignment)
6. [Direct Token & Session Refresh](#6-direct-token--session-refresh)
7. [Web UI Dashboard](#7-web-ui-dashboard)
8. [Automated Test Suite](#8-automated-test-suite)

---

## 1. Global Proxy & Egress Configuration

The engine features a unified global proxy architecture and sticky session management:
- **Proxybin Indian Egress (`proxybin-in`)**: Routes requests via strictly Indian residential/datacenter nodes.
- **Proxybin Any Egress (`proxybin-any`)**: Routes requests via rotating international nodes (US, CA, DE, SG, etc.).
- **ProxyScrape Indian Pool (`pool`)**: Rotates through verified Indian proxy endpoints with automatic failover.
- **Custom Proxy (`http://...` or `socks5://...`)**: Connects through user-specified egress nodes.
- **Direct Mode (`direct`)**: Direct local system connection.
- **Sticky Sessions per Account**: Maintains the identical egress node for a given user account across consecutive requests.
- **Multi-Tier Fallback Chain**: 10 attempts via Proxybin $\rightarrow$ 15 attempts via Indian pool $\rightarrow$ Local IP backup **only** when `--allow-local-backup` is explicitly passed; otherwise halts safely with `ProxyExhaustedError`.

---

## 2. Product Pricing Comparison

Compares final payable price across delivery pincodes, breaking down:
- Displayed product price
- Pincode-specific delivery / shipping fees
- General eligible promotional discounts
- Payment-method-specific UPI discounts (verified via cart review)
- Final payable price, ranked cheapest first

### Option A: Scan 🇮🇳 All Indian Postal Zones (Nationwide Cheapest Search)
Evaluates pricing across 40 representative postal hubs covering all 8 Indian postal zones (Delhi, UP, Rajasthan, Gujarat, Maharashtra, Telangana/AP, Karnataka, Tamil Nadu, Kerala, West Bengal, Assam, Bihar):
```powershell
python main.py --action pricing --account 8869049741.json --product 624039638 --all-india --proxy proxybin-in
```

### Option B: Search Specific Delivery Pincodes
```powershell
python main.py --action pricing --account 8869049741.json --product 624039638 --pincodes "500092,110025,560001,400001" --proxy proxybin-in
```

### Run with Global Rotating Proxy (Any Country)
```powershell
python main.py --action pricing --account 8869049741.json --product 624039638 --pincodes "500092,110025" --proxy proxybin-any
```

### Run with Direct Local IP Backup Enabled
```powershell
python main.py --action pricing --account 8869049741.json --product 624039638 --all-india --proxy proxybin-in --allow-local-backup
```
*Output report saved to:* `pricing_comparison_report.json`

---

## 3. First Order Discount (FOD) Classification

Extracts, normalizes, and groups advertised promotional tiers (e.g. ₹155, ₹170, ₹190, ₹75 off) while strictly distinguishing advertised discounts from verified account eligibility.

Supports flexible multi-format input:
- Single `.json` account file
- Directory of multiple `.json` files
- Merged JSON array `[ {...}, {...} ]`
- Line-delimited `.jsonl` file

Supports two output formats (`--format`):
- `individual_txt`: Separate 1-by-1 `.txt` file per account organized into categorized subfolders (`fod_tier_155/`, `fod_tier_170/`, `fod_no_offer/`).
- `combined_json`: Consolidated `.json` files by category (`fod_tier_155.json`, etc.) plus `fod_summary.json`.

### Fetch Real-Time Live FOD from Meesho API (Dynamic Daily Offer)
```powershell
python main.py --action classify-fod --live --proxy proxybin-in
```

### Classify FOD with Individual 1-by-1 .txt Output in Subfolders
```powershell
python main.py --action classify-fod --source . --output-dir classified_fod --format individual_txt
```

### Classify FOD from a Merged JSON Array or .jsonl File into Combined JSON
```powershell
python main.py --action classify-fod --source accounts.jsonl --output-dir classified_fod --format combined_json
```

---

## 4. Account Classification from Order History

Classifies accounts based on order history:
- **`fresh`**: Available order history explicitly shows 0 orders.
- **`used`**: Available order history shows $\ge 1$ orders.
- **`unknown`**: Order history missing, malformed, or unverified (strictly never misclassified as fresh).

### Fast Local Inspection with 1-by-1 .txt Output
Exports individual `.txt` files into `classified_accounts/fresh/`, `classified_accounts/used/`, and `classified_accounts/unknown/`:
```powershell
python main.py --action classify-accounts --source . --output-dir classified_accounts --format individual_txt
```

### Combined JSON Output Mode
Generates `fresh_accounts.json`, `used_accounts.json`, `unknown_accounts.json`, and `classification_summary.json`:
```powershell
python main.py --action classify-accounts --source . --output-dir classified_accounts --format combined_json
```

### Classify with Live Meesho Order History API Verification
Queries `POST /api/3.0/user/orders` directly using live account session tokens via sticky proxy:
```powershell
python main.py --action classify-accounts --source . --output-dir classified_accounts --live --proxy proxybin-in
```

---

## 5. Bulk Product & Address Assignment

Deterministically pairs products and delivery addresses to multiple accounts using a configurable random seed (`--seed`).

Supports flexible multi-format input (directory, merged array, or `.jsonl`) and export format (`individual_txt` or `combined_json`).

### Dry-Run Simulation (Default, Safe, No Network Calls)
```powershell
python main.py --action assign --products-file sample_products.json --addresses-file sample_addresses.json --seed 42 --count 2 --format individual_txt
```

### Reproduce Exact Assignment with a Specific Seed
```powershell
python main.py --action assign --products-file sample_products.json --addresses-file sample_addresses.json --seed 12345 --count 1 --format combined_json
```

### Custom Assignment Using Config File
```powershell
python main.py --action assign --products-file sample_products.json --addresses-file sample_addresses.json --config-file sample_assignment_config.json
```

### Live Assignment Execution (Mutates Cart & Address on Meesho)
```powershell
python main.py --action assign --products-file sample_products.json --addresses-file sample_addresses.json --seed 42 --live --proxy proxybin-in
```
*Output plan saved to:* `assignment_plan.json`

---

## 6. Direct Token & Session Refresh

The engine uses Meesho's native official token refresh endpoint (`POST https://prod.meeshoapi.com/api/2.0/xo`), directly exchanging the refresh token `ox` for fresh composite session `xo` in < 0.5s without OTP or Telegram bot dependency.

All writes use atomic temporary file replacement with automatic `.bak` backups to eliminate data corruption.

### Refresh Single Account
```powershell
python main.py --action refresh --account 8869049741.json --proxy proxybin-in
```

### Refresh Accounts from Merged JSON Array or .jsonl File
```powershell
python main.py --action refresh --source accounts.jsonl --output-dir refreshed_accounts --format individual_txt --proxy proxybin-in
```

### Refresh with Fallback to Local IP Allowed
```powershell
python main.py --action refresh --account 8869049741.json --proxy proxybin-in --allow-local-backup
```

---

## 7. Web UI Dashboard

Start the local web dashboard for point-and-click operations:
```powershell
python web_ui.py --port 5000
```
Open in your browser:
`http://127.0.0.1:5000`

### Dashboard Capabilities:
- **Global Proxy Bar**: Instant toggle between `Proxybin (India)`, `Proxybin (Any)`, `ProxyScrape Pool`, `Custom`, or `Direct`.
- **Local IP Backup Checkbox**: Explicit control over whether local system IP is used when proxies are exhausted.
- **Pricing Tab**: Radio button toggle between "Custom Pincodes" and "🇮🇳 All-India Scan (8 Zones, 40 Hubs)".
- **Input Source Selector**: Switch between directory path, single file, or pasted Merged JSON array / JSONL text.
- **Output Format Selector**: Toggle between 1-by-1 `.txt` in categorized subfolders vs Combined `.json`.
- **Operator Privacy Safeguard**: Account `7977748514` is permanently blacklisted and never shown in the UI.

---

## 8. Automated Test Suite

Execute the complete automated unit and regression test suite:
```powershell
python -m pytest
```
All 43 unit and regression tests run in ~3 seconds.

---

## 9. Render Cloud Deployment & Supabase Database

The entire backend and interactive frontend run as a unified single web service on Render.

### Render Deployment Instructions:
1. **Push Repository to GitHub / GitLab**:
   - The `.gitignore` automatically prevents all account credentials, JSON files, and operator numbers (`7977748514`) from being committed.
2. **Create New Web Service on Render**:
   - Connect your Git repository.
   - **Environment**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn web_ui:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 120`
   - Or deploy automatically using the included [`render.yaml`](file:///c:/Users/chatu/Desktop/Meesho%20E2E/render.yaml) blueprint!
3. **Optional Supabase Integration (Persistent Cloud Storage)**:
   - If you wish to store sessions and reports persistently in the cloud, add the following Environment Variables in the Render Dashboard:
     - `SUPABASE_URL`: `https://<your-project>.supabase.co`
     - `SUPABASE_KEY`: `<your-anon-or-service-role-key>`
4. **Cloud-Native Direct JSON Input**:
   - Because local `.json` files are not stored on Render, the Web Dashboard includes direct **Upload File** and **Paste JSON** modes on the **Pricing Comparator**, **Live FOD**, and **Account Classifier** tabs, allowing you to drag-and-drop or paste accounts right from your browser!

