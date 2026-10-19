# Meesho E2E Engine: Cart, Address, UPI Pricing & Account Intelligence

An enterprise automation and intelligence engine for Meesho accounts that manages session refreshes, multi-pincode delivery fee optimization, verified UPI price comparisons across India, deterministic bulk product/address assignment, and account order & FOD classification.

---

## 🚀 Key Features

1. **Flexible Multi-Format Data Ingestion & Export**:
   - Ingest account data from single JSON files, folder of accounts, merged JSON array `[...]`, or `.jsonl` lines.
   - Export categorized results as either **separate 1-by-1 `.txt` files organized into category subfolders** (`fresh/`, `used/`, `fod_tier_155/`, etc.) or **consolidated `.json` files** (`fresh.json`, `used.json`, etc.) with comprehensive summaries.
   - Built-in strict operator privacy blacklisting ensuring private accounts are never exposed in UI dropdowns, file lists, or processed.

2. **Global Proxy Management & Sticky Egress Sessions**:
   - Centralized thread-safe proxy configuration applying globally across all Meesho requests.
   - Seamless Proxybin integration (`https://proxybin.netlify.app/api/proxy`) supporting strictly Indian residential/datacenter nodes (`proxybin-in`) or global rotating nodes (`proxybin-any`).
   - Sticky Session Manager pins each user account to the identical egress node across consecutive requests to maintain realistic traffic patterns.
   - Multi-tier fallback chain: 10 attempts via Proxybin $\rightarrow$ 15 attempts via ProxyScrape Indian pool $\rightarrow$ Local IP backup **only** when explicitly enabled; otherwise raises `ProxyExhaustedError`.

3. **Product Pricing Comparison (Custom Pincodes vs 🇮🇳 All-India Scan)**:
   - Compares the final payable price of any product across candidate delivery pincodes or across the entire nation.
   - **All-India Benchmark Mode**: Evaluates pricing across 40 representative postal hubs covering all 8 Indian postal zones (Delhi, UP, Rajasthan, Gujarat, Maharashtra, Telangana/AP, Karnataka, Tamil Nadu, Kerala, West Bengal, Assam, Bihar).
   - Dissects all pricing components: displayed product price, delivery charges, eligible general discounts, payment-method-specific (UPI) discounts, and final payable amount.
   - Explicitly reports `VERIFIED_AVAILABLE`, `UNSUPPORTED_PINCODE`, `OUT_OF_STOCK`, `PRODUCT_UNAVAILABLE`, and `PRICE_MISSING`.
   - Built-in TTL caching (`PricingCache`) avoids redundant queries.

4. **Deterministic Bulk Product and Address Assignment**:
   - Supports configurable input sets of products (`sample_products.json`) and test addresses (`sample_addresses.json`).
   - Reproducible assignments using a configurable random seed (`--seed`).
   - Strict input validation reporting invalid product identifiers or incomplete test addresses.
   - Clean decoupling between Assignment Planner, Assignment Persistence, and Assignment Execution (dry-run simulation vs live execution).

5. **Account Classification from JSON Files**:
   - Inspects bulk account records based on order history.
   - Classifies accounts as:
     - `fresh`: available, complete order history explicitly shows 0 orders.
     - `used`: available order history shows 1 or more orders.
     - `unknown`: order information is missing, ambiguous, incomplete, or malformed (strictly never misclassified as fresh).
   - Supports optional live verification via Meesho's authenticated order history API.

6. **First Order Discount (FOD) Promotional Classification**:
   - Extracts and normalizes advertised promotional discount tiers (e.g. ₹155, ₹170, ₹190, ₹75 off).
   - Real-time live dynamic FOD fetching from Meesho production personalization endpoints.
   - Explicitly distinguishes advertised discount information from verified account eligibility (`ELIGIBLE`, `INELIGIBLE`, `UNVERIFIED`).

7. **Hardened JSON-Based Session & Headless Token Refresh**:
   - Uses account JSON files with composite access tokens (`xo`) and refresh tokens (`ox`).
   - Official direct token exchange via `POST https://prod.meeshoapi.com/api/2.0/xo` in < 0.5s without Telegram or OTP prompts.
   - Atomic temporary file replacement (`atomic_save_account_json`) with automatic `.bak` backups.

---

## 📂 Project Structure

```
c:\Users\chatu\Desktop\Meesho E2E\
├── config.py                   # Headers, API hosts, app version constants
├── data_io.py                  # Multi-format ingestion (JSON/JSONL/folder) & export (1-by-1 .txt vs combined JSON)
├── proxy_manager.py            # Global proxy config, Proxybin integration, sticky sessions & fallback chain
├── account_manager.py          # Schema validation, token metadata, atomic persistence & refresh providers
├── auth.py                     # Account loader, header builder & session integration
├── meesho_client.py            # Base HTTP client with auto-refresh, sticky proxy routing & error handling
├── cart.py                     # Product link resolution, variation picker & cart addition
├── address.py                  # Pincode check & delivery address registration
├── optimizer.py                # Multi-pincode delivery fee optimizer & final UPI price evaluation
├── pricing_comparator.py       # Multi-pincode pricing engine, All-India 8-zone benchmark & TTL caching
├── assignment_engine.py        # Bulk deterministic product & address assignment with seed planner
├── account_classifier.py       # Account order history classification (fresh vs used vs unknown)
├── fod_classifier.py           # First Order Discount (FOD) tier extraction & dynamic live fetching
├── web_ui.py                   # Full-featured Web Dashboard with Global Proxy bar & interactive controls
├── main.py                     # Unified CLI orchestrator for all engine actions
├── CLI_COMMANDS.md             # Complete CLI reference manual with ready-to-run commands
├── sample_data.json            # Candidate products, addresses, and pincodes
├── sample_products.json        # Test product links/IDs for bulk assignment
├── sample_addresses.json       # Validated test addresses for bulk assignment
├── sample_assignment_config.json # Configurable assignment rules
├── requirements.txt            # Python dependencies
└── tests\                      # Comprehensive unit and regression test suite (40 tests)
    ├── test_account_manager.py     # Schema validation, JWT inspection, atomic write tests
    ├── test_pricing_comparator.py  # Price breakdown, eligibility, ranking, cache tests
    ├── test_assignment_engine.py   # Seeding determinism, input validation, execution tests
    ├── test_account_classifier.py  # Order history fresh/used/unknown classification tests
    ├── test_fod_classifier.py      # FOD normalization, tier extraction, eligibility tests
    └── test_enhancements.py        # Multi-format I/O, privacy blacklist, sticky proxies, All-India scan tests
```

---

## 🛠️ Installation & Setup

1. Open PowerShell / Command Prompt inside the project directory:
   ```powershell
   cd "c:\Users\chatu\Desktop\Meesho E2E"
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

---

## 💻 CLI Usage Examples

### 1. Product Pricing Comparison (All-India Scan)
Scans 40 representative postal hubs across all 8 Indian postal zones:
```powershell
python main.py --account 8869049741.json --action pricing --product 624039638 --all-india --proxy proxybin-in
```

### 2. First Order Discount (FOD) Classification
Classifies FOD with 1-by-1 `.txt` files in categorized subfolders:
```powershell
python main.py --action classify-fod --source . --output-dir classified_fod --format individual_txt
```

### 3. Account Classification from Order History
Classifies accounts into `fresh`, `used`, and `unknown` with combined JSON output:
```powershell
python main.py --action classify-accounts --source . --output-dir classified_accounts --format combined_json
```

### 4. Bulk Product & Address Assignment
Generates a reproducible assignment plan using seed 42:
```powershell
python main.py --action assign --products-file sample_products.json --addresses-file sample_addresses.json --seed 42 --count 2 --format individual_txt
```

### 5. Direct Official Token Refresh (/api/2.0/xo)
Refreshes tokens directly via Meesho's native API via sticky proxy:
```powershell
python main.py --account 8869049741.json --action refresh --proxy proxybin-in
```

### 6. Interactive Web UI Dashboard
Launch the web dashboard:
```powershell
python web_ui.py --port 5000
```
Open in browser: `http://127.0.0.1:5000`

---

## 🧪 Testing

Run all 40 automated unit and regression tests:
```powershell
python -m unittest discover -s tests -v
```

All 40 unit and regression tests pass in ~0.45 seconds.
