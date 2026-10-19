"""
Meesho E2E Engine — Modern Interactive Web Dashboard.
Cloud-Ready for Render & Local Execution.

Features:
- Global Proxy Configuration & Sticky Egress Gateway (Proxybin -> ProxyScrape pool -> Direct)
- Product Pricing Comparison across Candidate Pincodes & Nationwide 8 Postal Zones (Requires Account)
- Live Real-Time First Order Discount (FOD) Check & Bulk Classification (Always Live)
- Bulk Account Classification with Live Order History Verification (Always Live)
- Deterministic Bulk Product & Address Assignment with Seed & Dry-Run
- Direct API Token Refresh via POST /api/2.0/xo

STRICT PRIVACY: Operator account 7977748514 is strictly blacklisted and never processed or displayed.
"""

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Dict, Any, List, Optional, Union

from flask import Flask, render_template_string, request, jsonify, Response

# Core Engine Modules
from data_io import (
    load_accounts_from_source,
    export_categorized_results,
    is_blacklisted_account,
    BLACKLISTED_NUMBERS
)
import proxy_manager
import supabase_db
from account_manager import (
    load_account_json,
    validate_account_schema,
    parse_xo_token,
    is_token_expired,
    DirectApiTokenRefreshProvider,
    refresh_accounts_from_source
)
from account_classifier import (
    classify_accounts_from_source,
    AccountClassification,
    classify_account_data
)
from fod_classifier import (
    fetch_live_fod,
    classify_fod_from_source,
    classify_single_fod_record
)
from pricing_comparator import (
    ProductPricingComparator,
    ComparisonStatus,
    get_all_india_pincodes,
    ALL_INDIA_PINCODES
)
from assignment_engine import (
    AssignmentPlanner,
    AssignmentExecutor,
    AssignmentRuleConfig,
    run_bulk_assignment_from_source,
    DEFAULT_PRODUCTS,
    DEFAULT_ADDRESSES
)
from meesho_client import MeeshoClient

# Configure Logging with stdout flush for cloud deployment (Render)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout
)
logger = logging.getLogger("WebUI")

PROJECT_DIR = Path(__file__).resolve().parent

app = Flask(__name__)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Meesho E2E Engine — Cloud Control Dashboard</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
  <style>
    * { box-sizing: border-box; }
    html, body {
      height: 100%;
      margin: 0;
      padding: 0;
      background-color: #0b1120;
      color: #f8fafc;
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    }
    .hidden { display: none !important; }
    .nav-btn {
      display: flex;
      align-items: center;
      gap: 0.75rem;
      width: 100%;
      padding: 0.75rem 1rem;
      border-radius: 0.5rem;
      font-size: 0.875rem;
      font-weight: 500;
      text-align: left;
      color: #94a3b8;
      background: transparent;
      border: none;
      cursor: pointer;
      transition: all 0.15s ease-in-out;
    }
    .nav-btn:hover { background-color: #1e293b; color: #f8fafc; }
    .nav-btn.active { background-color: #e11d48 !important; color: #ffffff !important; box-shadow: 0 4px 12px rgba(225, 29, 72, 0.3); }
    .card { background-color: #0f172a; border: 1px solid #1e293b; border-radius: 0.75rem; }
    .badge-fresh { background-color: #065f46; color: #34d399; border: 1px solid #059669; }
    .badge-used { background-color: #1e3a8a; color: #60a5fa; border: 1px solid #2563eb; }
    .badge-unknown { background-color: #78350f; color: #fbbf24; border: 1px solid #d97706; }
    .badge-error { background-color: #881337; color: #fda4af; border: 1px solid #e11d48; }
    input, select, textarea {
      background-color: #020617 !important;
      border-color: #334155 !important;
      color: #f8fafc !important;
    }
    input:focus, select:focus, textarea:focus {
      border-color: #f43f5e !important;
      outline: none !important;
      ring: 1px solid #f43f5e;
    }
    aside { flex-shrink: 0 !important; width: 17rem !important; }
  </style>
</head>
<body class="min-h-screen flex flex-col">

  <!-- Top Header Bar -->
  <header class="bg-slate-900 border-b border-slate-800 px-6 py-3.5 flex flex-wrap items-center justify-between gap-4 sticky top-0 z-50">
    <div class="flex items-center space-x-3">
      <div class="w-10 h-10 rounded-xl bg-gradient-to-tr from-rose-600 to-pink-500 flex items-center justify-center font-black text-xl text-white shadow-lg shadow-rose-900/40">M</div>
      <div>
        <div class="flex items-center space-x-2">
          <h1 class="text-lg font-bold tracking-tight text-white">Meesho E2E Engine</h1>
          <span class="text-[10px] font-mono px-2 py-0.5 rounded-full bg-rose-950 text-rose-400 border border-rose-800">Cloud Edition</span>
        </div>
        <p class="text-xs text-slate-400">Direct API &bull; Live FOD & Orders &bull; Sticky Proxies &bull; All-India UPI Pricing</p>
      </div>
    </div>

    <!-- Active Account Memory Pill & Global Proxy Bar -->
    <div class="flex flex-wrap items-center gap-3">
      <!-- Active Account Pill -->
      <div id="active-account-bar" class="hidden items-center gap-2 bg-slate-950 border border-slate-800 px-3 py-1.5 rounded-xl text-xs">
        <span class="text-slate-400">Active Account:</span>
        <span id="active-account-label" class="font-mono text-emerald-400 font-semibold">None</span>
        <button onclick="clearActiveAccount()" title="Clear active account" class="text-slate-500 hover:text-rose-400 ml-1">
          <i class="fa-solid fa-xmark"></i>
        </button>
      </div>

      <!-- Proxy Config Bar -->
      <div class="flex items-center gap-2.5 bg-slate-950 px-3 py-1.5 rounded-xl border border-slate-800 text-xs">
        <span class="text-slate-400 font-medium flex items-center gap-1.5">
          <i class="fa-solid fa-network-wired text-rose-500"></i>
          <span>Proxy:</span>
        </span>
        <select id="global-proxy-mode" onchange="updateGlobalProxyConfig()" class="bg-slate-900 border border-slate-700 text-slate-200 rounded-lg px-2 py-1 text-xs">
          <option value="proxybin_in" selected>🇮🇳 Proxybin (Strict India — 10 Retries)</option>
          <option value="proxybin_any">🌐 Proxybin (Global Rotating)</option>
          <option value="pool">🇮🇳 ProxyScrape Indian Pool</option>
          <option value="custom">⚙️ Custom Proxy</option>
          <option value="direct">🚀 Direct IP (Render Outbound)</option>
        </select>
        <input type="text" id="global-custom-proxy" placeholder="http://host:port" onchange="updateGlobalProxyConfig()" class="hidden bg-slate-900 border border-slate-700 text-slate-200 rounded-lg px-2 py-1 text-xs w-36">
        <label class="flex items-center space-x-1 text-slate-400 cursor-pointer text-[11px] pl-1.5 border-l border-slate-800">
          <input type="checkbox" id="global-allow-backup" onchange="updateGlobalProxyConfig()" class="w-3.5 h-3.5 text-rose-600 rounded bg-slate-900 border-slate-700">
          <span title="Fallback to direct IP if proxy fails">Direct Fallback</span>
        </label>
      </div>
    </div>
  </header>

  <!-- Main Layout -->
  <div class="flex-1 flex overflow-hidden">
    <!-- Sidebar Navigation -->
    <aside class="w-64 bg-slate-900/70 border-r border-slate-800 p-4 space-y-1.5 select-none">
      <button type="button" onclick="switchTab('tab-pricing')" id="btn-pricing" class="nav-btn active">
        <i class="fa-solid fa-tags w-5 text-center text-rose-400"></i>
        <span>Pricing Comparator</span>
      </button>
      <button type="button" onclick="switchTab('tab-fod')" id="btn-fod" class="nav-btn">
        <i class="fa-solid fa-gift w-5 text-center text-amber-400"></i>
        <span>Live FOD Explorer</span>
      </button>
      <button type="button" onclick="switchTab('tab-accounts')" id="btn-accounts" class="nav-btn">
        <i class="fa-solid fa-users-gear w-5 text-center text-blue-400"></i>
        <span>Account Classification</span>
      </button>
      <button type="button" onclick="switchTab('tab-assignment')" id="btn-assignment" class="nav-btn">
        <i class="fa-solid fa-cubes-stacked w-5 text-center text-purple-400"></i>
        <span>Bulk Assignment</span>
      </button>
      <button type="button" onclick="switchTab('tab-sessions')" id="btn-sessions" class="nav-btn">
        <i class="fa-solid fa-key w-5 text-center text-emerald-400"></i>
        <span>Token Refresh</span>
      </button>
    </aside>

    <!-- Content Workspace -->
    <main class="flex-1 p-6 overflow-y-auto">

      <!-- ======================================================== -->
      <!-- TAB 1: PRICING COMPARATOR                                -->
      <!-- ======================================================== -->
      <section id="tab-pricing" class="space-y-6">
        <div class="card p-6">
          <div class="flex items-center justify-between border-b border-slate-800 pb-4 mb-4">
            <div>
              <h2 class="text-base font-semibold flex items-center space-x-2 text-white">
                <i class="fa-solid fa-tags text-rose-500"></i>
                <span>Product Pricing Comparison Across Delivery Pincodes</span>
              </h2>
              <p class="text-xs text-slate-400 mt-1">
                Ranks candidate delivery pincodes by lowest payable price, taking into account base price, shipping fees, and UPI discounts. Requires account credentials.
              </p>
            </div>
            <span class="px-2.5 py-1 rounded-full text-[11px] font-mono bg-rose-950 text-rose-400 border border-rose-800 flex items-center gap-1.5">
              <i class="fa-solid fa-user-lock"></i> Account-Specific
            </span>
          </div>

          <!-- Account Input for Pricing -->
          <div class="mb-5 bg-slate-950 p-4 rounded-xl border border-slate-800">
            <div class="flex items-center justify-between mb-2">
              <label class="text-xs font-semibold uppercase tracking-wider text-slate-300 flex items-center gap-1.5">
                <i class="fa-solid fa-id-card text-rose-500"></i>
                <span>Account Credentials (Required for Pricing)</span>
              </label>
              <div class="flex items-center gap-2 text-xs">
                <label class="flex items-center space-x-1 cursor-pointer text-slate-300">
                  <input type="radio" name="pricing-input-mode" value="upload" checked onchange="toggleAccountInputMode('pricing')" class="text-rose-600">
                  <span>Upload File</span>
                </label>
                <label class="flex items-center space-x-1 cursor-pointer text-slate-300">
                  <input type="radio" name="pricing-input-mode" value="paste" onchange="toggleAccountInputMode('pricing')" class="text-rose-600">
                  <span>Paste JSON</span>
                </label>
                <label id="pricing-use-active-label" class="hidden flex items-center space-x-1 cursor-pointer text-emerald-400 font-medium">
                  <input type="radio" name="pricing-input-mode" value="active" onchange="toggleAccountInputMode('pricing')" class="text-emerald-500">
                  <span>Use Active Account</span>
                </label>
              </div>
            </div>

            <!-- Upload mode -->
            <div id="pricing-box-upload">
              <input type="file" id="pricing-file-input" accept=".json,.txt" onchange="handleSingleAccountFile(this, 'pricing')" class="w-full text-xs text-slate-400 file:mr-3 file:py-2 file:px-4 file:rounded-lg file:border-0 file:text-xs file:font-semibold file:bg-slate-800 file:text-rose-300 hover:file:bg-slate-700 cursor-pointer">
            </div>

            <!-- Paste mode -->
            <div id="pricing-box-paste" class="hidden">
              <textarea id="pricing-paste-textarea" rows="3" placeholder='{"mobile": "8869049741", "user_id": "...", "xo": "..."}' oninput="handlePastedAccount('pricing')" class="w-full bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-xs font-mono text-slate-200 focus:outline-none"></textarea>
            </div>

            <!-- Status indicator -->
            <div id="pricing-account-status" class="mt-2 text-xs hidden"></div>
          </div>

          <!-- Product & Pincode Scope -->
          <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label class="block text-xs font-semibold uppercase tracking-wider text-slate-400 mb-1">Product ID or URL</label>
              <input type="text" id="pricing-product" value="624039638" placeholder="e.g. 624039638 or https://www.meesho.com/..." class="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-sm">
            </div>

            <div>
              <label class="block text-xs font-semibold uppercase tracking-wider text-slate-400 mb-1">Pincode Scan Scope</label>
              <div class="flex items-center space-x-4 mt-2 text-sm">
                <label class="flex items-center space-x-1.5 cursor-pointer">
                  <input type="radio" name="pricing-scope" value="custom" checked onchange="togglePricingScope()" class="text-rose-600">
                  <span>Custom Pincodes</span>
                </label>
                <label class="flex items-center space-x-1.5 cursor-pointer">
                  <input type="radio" name="pricing-scope" value="all_india" onchange="togglePricingScope()" class="text-rose-600">
                  <span class="text-rose-400 font-medium">🇮🇳 All-India Scan (35 Hubs / 8 Zones)</span>
                </label>
              </div>
            </div>
          </div>

          <!-- Custom Pincodes Input -->
          <div id="pricing-custom-pincodes-container" class="mt-4">
            <label class="block text-xs font-semibold uppercase tracking-wider text-slate-400 mb-1">Candidate Delivery Pincodes (Comma-separated)</label>
            <input type="text" id="pricing-pincodes" value="500092, 110025, 560001, 400001" class="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-sm font-mono">
          </div>

          <div id="pricing-all-india-info" class="mt-4 p-3 bg-slate-950/70 border border-slate-800 rounded-lg hidden text-xs text-slate-300">
            <span class="font-semibold text-rose-400">All-India Benchmark Active:</span> Will query shipping and UPI discounts across 35 postal hubs spanning North, West, South, East, Central, and North-East India to identify nationwide cheapest delivery.
          </div>

          <!-- Action & Error Banner -->
          <div id="pricing-error-banner" class="mt-4 hidden p-3 rounded-lg bg-rose-950 border border-rose-800 text-rose-300 text-xs flex items-center justify-between">
            <div class="flex items-center gap-2">
              <i class="fa-solid fa-triangle-exclamation text-rose-400"></i>
              <span id="pricing-error-text"></span>
            </div>
            <button onclick="document.getElementById('pricing-error-banner').classList.add('hidden')" class="text-rose-400 hover:text-white"><i class="fa-solid fa-xmark"></i></button>
          </div>

          <div class="mt-5 flex items-center space-x-3">
            <button onclick="runPricingComparison()" id="btn-run-pricing" class="bg-rose-600 hover:bg-rose-500 text-white font-medium px-5 py-2.5 rounded-lg text-sm transition flex items-center space-x-2 shadow-lg shadow-rose-950">
              <i class="fa-solid fa-calculator"></i>
              <span>Compare Pricing</span>
            </button>
            <span id="pricing-spinner" class="hidden text-sm text-slate-400 flex items-center space-x-2">
              <i class="fa-solid fa-circle-notch fa-spin text-rose-500"></i>
              <span>Querying Meesho pricing quotes...</span>
            </span>
          </div>
        </div>

        <!-- Pricing Results -->
        <div id="pricing-results-card" class="card p-6 hidden">
          <div class="flex items-center justify-between border-b border-slate-800 pb-3 mb-4">
            <div>
              <h3 id="pricing-prod-name" class="font-semibold text-white text-base">Product Results</h3>
              <p id="pricing-summary-text" class="text-xs text-slate-400"></p>
            </div>
            <div class="flex items-center space-x-3">
              <div id="pricing-savings-pill" class="hidden bg-emerald-950 text-emerald-400 border border-emerald-800 px-3 py-1 rounded-full text-xs font-semibold"></div>
              <button onclick="downloadJsonData(window.lastPricingReport, 'pricing_report.json')" class="bg-slate-800 hover:bg-slate-700 text-slate-300 px-3 py-1.5 rounded-lg text-xs flex items-center gap-1.5 border border-slate-700">
                <i class="fa-solid fa-download"></i>
                <span>Download Report</span>
              </button>
            </div>
          </div>
          <div class="overflow-x-auto">
            <table class="w-full text-left text-sm">
              <thead class="text-xs uppercase bg-slate-950 text-slate-400 border-b border-slate-800">
                <tr>
                  <th class="py-2.5 px-3">Rank</th>
                  <th class="py-2.5 px-3">Pincode</th>
                  <th class="py-2.5 px-3">City / State</th>
                  <th class="py-2.5 px-3">Base Price</th>
                  <th class="py-2.5 px-3">Delivery</th>
                  <th class="py-2.5 px-3">UPI Discount</th>
                  <th class="py-2.5 px-3 font-bold text-rose-400">Final Price</th>
                  <th class="py-2.5 px-3">Status</th>
                </tr>
              </thead>
              <tbody id="pricing-table-body" class="divide-y divide-slate-800 font-mono text-xs"></tbody>
            </table>
          </div>
        </div>
      </section>

      <!-- ======================================================== -->
      <!-- TAB 2: LIVE FOD EXPLORER & CLASSIFICATION                -->
      <!-- ======================================================== -->
      <section id="tab-fod" class="space-y-6 hidden">
        <!-- Part 1: Single Account Live FOD -->
        <div class="card p-6">
          <div class="flex items-center justify-between border-b border-slate-800 pb-4 mb-4">
            <div>
              <h2 class="text-base font-semibold flex items-center space-x-2 text-white">
                <i class="fa-solid fa-bolt text-amber-400"></i>
                <span>Single Account Live First Order Discount (FOD)</span>
              </h2>
              <p class="text-xs text-slate-400 mt-1">Queries Meesho's live production FOD endpoint directly with account context.</p>
            </div>
            <span class="px-2.5 py-1 rounded-full text-[11px] font-mono bg-amber-950 text-amber-400 border border-amber-800 flex items-center gap-1.5">
              <i class="fa-solid fa-signal"></i> Always Live
            </span>
          </div>

          <!-- Account Input for Single FOD -->
          <div class="bg-slate-950 p-4 rounded-xl border border-slate-800">
            <div class="flex items-center justify-between mb-2">
              <label class="text-xs font-semibold uppercase tracking-wider text-slate-300">Account Credentials</label>
              <div class="flex items-center gap-2 text-xs">
                <label class="flex items-center space-x-1 cursor-pointer text-slate-300">
                  <input type="radio" name="fod-single-input-mode" value="upload" checked onchange="toggleAccountInputMode('fod-single')" class="text-amber-500">
                  <span>Upload File</span>
                </label>
                <label class="flex items-center space-x-1 cursor-pointer text-slate-300">
                  <input type="radio" name="fod-single-input-mode" value="paste" onchange="toggleAccountInputMode('fod-single')" class="text-amber-500">
                  <span>Paste JSON</span>
                </label>
                <label id="fod-single-use-active-label" class="hidden flex items-center space-x-1 cursor-pointer text-emerald-400 font-medium">
                  <input type="radio" name="fod-single-input-mode" value="active" onchange="toggleAccountInputMode('fod-single')" class="text-emerald-500">
                  <span>Use Active Account</span>
                </label>
              </div>
            </div>

            <div id="fod-single-box-upload">
              <input type="file" id="fod-single-file-input" accept=".json,.txt" onchange="handleSingleAccountFile(this, 'fod-single')" class="w-full text-xs text-slate-400 file:mr-3 file:py-2 file:px-4 file:rounded-lg file:border-0 file:text-xs file:font-semibold file:bg-slate-800 file:text-amber-300 hover:file:bg-slate-700 cursor-pointer">
            </div>
            <div id="fod-single-box-paste" class="hidden">
              <textarea id="fod-single-paste-textarea" rows="2" placeholder='{"mobile": "8869049741", "xo": "..."}' oninput="handlePastedAccount('fod-single')" class="w-full bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-xs font-mono text-slate-200 focus:outline-none"></textarea>
            </div>
            <div id="fod-single-account-status" class="mt-2 text-xs hidden"></div>
          </div>

          <div class="mt-4 flex items-center space-x-3">
            <button onclick="checkSingleLiveFod()" id="btn-check-single-fod" class="bg-amber-600 hover:bg-amber-500 text-white font-medium px-4 py-2 rounded-lg text-sm transition flex items-center space-x-2">
              <i class="fa-solid fa-bolt"></i>
              <span>Check Live FOD</span>
            </button>
            <span id="fod-single-spinner" class="hidden text-sm text-slate-400 flex items-center space-x-2">
              <i class="fa-solid fa-circle-notch fa-spin text-amber-500"></i>
              <span>Querying live Meesho FOD endpoint...</span>
            </span>
          </div>

          <div id="fod-single-result" class="mt-4 bg-slate-950 border border-slate-800 rounded-lg p-4 text-xs text-slate-400">
            Click "Check Live FOD" above to retrieve live daily promotional tier for this account.
          </div>
        </div>

        <!-- Part 2: Bulk Live FOD Classification -->
        <div class="card p-6">
          <div class="flex items-center justify-between border-b border-slate-800 pb-4 mb-4">
            <div>
              <h3 class="text-base font-semibold flex items-center space-x-2 text-white">
                <i class="fa-solid fa-folder-tree text-amber-400"></i>
                <span>Bulk FOD Classification (Always Live)</span>
              </h3>
              <p class="text-xs text-slate-400 mt-1">Upload multiple account files or paste JSON array / JSONL to classify each account via live Meesho FOD API.</p>
            </div>
            <span class="px-2.5 py-1 rounded-full text-[11px] font-mono bg-amber-950 text-amber-400 border border-amber-800 flex items-center gap-1.5">
              <i class="fa-solid fa-check-double"></i> Live API Query Per Account
            </span>
          </div>

          <div class="bg-slate-950 p-4 rounded-xl border border-slate-800">
            <div class="flex items-center justify-between mb-2">
              <label class="text-xs font-semibold uppercase tracking-wider text-slate-300">Accounts Input Source</label>
              <div class="flex items-center gap-3 text-xs">
                <label class="flex items-center space-x-1 cursor-pointer text-slate-300">
                  <input type="radio" name="fod-bulk-input-mode" value="upload" checked onchange="toggleAccountInputMode('fod-bulk')" class="text-amber-500">
                  <span>Upload Files (.json, .jsonl, .txt)</span>
                </label>
                <label class="flex items-center space-x-1 cursor-pointer text-slate-300">
                  <input type="radio" name="fod-bulk-input-mode" value="paste" onchange="toggleAccountInputMode('fod-bulk')" class="text-amber-500">
                  <span>Paste Merged Array / JSONL</span>
                </label>
              </div>
            </div>

            <div id="fod-bulk-box-upload">
              <input type="file" id="fod-bulk-file-input" multiple accept=".json,.jsonl,.txt" onchange="handleMultipleAccountFiles(this, 'fod-bulk')" class="w-full text-xs text-slate-400 file:mr-3 file:py-2 file:px-4 file:rounded-lg file:border-0 file:text-xs file:font-semibold file:bg-slate-800 file:text-amber-300 hover:file:bg-slate-700 cursor-pointer">
            </div>
            <div id="fod-bulk-box-paste" class="hidden">
              <textarea id="fod-bulk-paste-textarea" rows="4" placeholder="[{...}, {...}] or {...}\n{...}" oninput="handlePastedMultipleAccounts('fod-bulk')" class="w-full bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-xs font-mono text-slate-200 focus:outline-none"></textarea>
            </div>
            <div id="fod-bulk-account-status" class="mt-2 text-xs hidden"></div>
          </div>

          <div class="mt-4 flex items-center space-x-4">
            <button onclick="runBulkFodClassification()" id="btn-run-bulk-fod" class="bg-amber-600 hover:bg-amber-500 text-white font-medium px-4 py-2 rounded-lg text-sm transition flex items-center space-x-2">
              <i class="fa-solid fa-play"></i>
              <span>Run Live FOD Classification</span>
            </button>
            <span id="fod-bulk-spinner" class="hidden text-sm text-slate-400 flex items-center space-x-2">
              <i class="fa-solid fa-circle-notch fa-spin text-amber-500"></i>
              <span>Processing accounts with live Meesho FOD API...</span>
            </span>
          </div>

          <div id="fod-bulk-results" class="mt-4"></div>
        </div>
      </section>

      <!-- ======================================================== -->
      <!-- TAB 3: ACCOUNT CLASSIFICATION                            -->
      <!-- ======================================================== -->
      <section id="tab-accounts" class="space-y-6 hidden">
        <div class="card p-6">
          <div class="flex items-center justify-between border-b border-slate-800 pb-4 mb-4">
            <div>
              <h2 class="text-base font-semibold flex items-center space-x-2 text-white">
                <i class="fa-solid fa-users-gear text-blue-400"></i>
                <span>Account Classification (Fresh vs Used vs Unknown)</span>
              </h2>
              <p class="text-xs text-slate-400 mt-1">Classifies accounts based on live Meesho order history (/api/3.0/user/orders). Auto-refreshes tokens if expired.</p>
            </div>
            <span class="px-2.5 py-1 rounded-full text-[11px] font-mono bg-blue-950 text-blue-400 border border-blue-800 flex items-center gap-1.5">
              <i class="fa-solid fa-cart-shopping"></i> Live Order History Active
            </span>
          </div>

          <div class="bg-slate-950 p-4 rounded-xl border border-slate-800">
            <div class="flex items-center justify-between mb-2">
              <label class="text-xs font-semibold uppercase tracking-wider text-slate-300">Accounts Input Source</label>
              <div class="flex items-center gap-3 text-xs">
                <label class="flex items-center space-x-1 cursor-pointer text-slate-300">
                  <input type="radio" name="acc-input-mode" value="upload" checked onchange="toggleAccountInputMode('acc')" class="text-blue-500">
                  <span>Upload Files (.json, .jsonl, .txt)</span>
                </label>
                <label class="flex items-center space-x-1 cursor-pointer text-slate-300">
                  <input type="radio" name="acc-input-mode" value="paste" onchange="toggleAccountInputMode('acc')" class="text-blue-500">
                  <span>Paste Merged Array / JSONL</span>
                </label>
              </div>
            </div>

            <div id="acc-box-upload">
              <input type="file" id="acc-file-input" multiple accept=".json,.jsonl,.txt" onchange="handleMultipleAccountFiles(this, 'acc')" class="w-full text-xs text-slate-400 file:mr-3 file:py-2 file:px-4 file:rounded-lg file:border-0 file:text-xs file:font-semibold file:bg-slate-800 file:text-blue-300 hover:file:bg-slate-700 cursor-pointer">
            </div>
            <div id="acc-box-paste" class="hidden">
              <textarea id="acc-paste-textarea" rows="4" placeholder="[{...}, {...}] or {...}\n{...}" oninput="handlePastedMultipleAccounts('acc')" class="w-full bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-xs font-mono text-slate-200 focus:outline-none"></textarea>
            </div>
            <div id="acc-account-status" class="mt-2 text-xs hidden"></div>
          </div>

          <div class="mt-4 flex items-center space-x-4">
            <button onclick="runAccountClassification()" id="btn-run-acc" class="bg-blue-600 hover:bg-blue-500 text-white font-medium px-4 py-2 rounded-lg text-sm transition flex items-center space-x-2">
              <i class="fa-solid fa-play"></i>
              <span>Classify Accounts (Live Orders)</span>
            </button>
            <span id="acc-spinner" class="hidden text-sm text-slate-400 flex items-center space-x-2">
              <i class="fa-solid fa-circle-notch fa-spin text-blue-500"></i>
              <span>Checking live order history via POST /api/3.0/user/orders...</span>
            </span>
          </div>

          <div id="acc-results-area" class="mt-5 space-y-4"></div>
        </div>
      </section>

      <!-- ======================================================== -->
      <!-- TAB 4: BULK ASSIGNMENT                                   -->
      <!-- ======================================================== -->
      <section id="tab-assignment" class="space-y-6 hidden">
        <div class="card p-6">
          <div class="flex items-center justify-between border-b border-slate-800 pb-4 mb-4">
            <div>
              <h2 class="text-base font-semibold flex items-center space-x-2 text-white">
                <i class="fa-solid fa-cubes-stacked text-purple-400"></i>
                <span>Bulk Product & Address Deterministic Assignment</span>
              </h2>
              <p class="text-xs text-slate-400 mt-1">Assigns configurable product and delivery address pools across test accounts with a reproducible random seed.</p>
            </div>
            <span class="px-2.5 py-1 rounded-full text-[11px] font-mono bg-purple-950 text-purple-400 border border-purple-800">
              Deterministic Plan
            </span>
          </div>

          <div class="bg-slate-950 p-4 rounded-xl border border-slate-800">
            <div class="flex items-center justify-between mb-2">
              <label class="text-xs font-semibold uppercase tracking-wider text-slate-300">Target Accounts</label>
              <div class="flex items-center gap-3 text-xs">
                <label class="flex items-center space-x-1 cursor-pointer text-slate-300">
                  <input type="radio" name="assign-input-mode" value="upload" checked onchange="toggleAccountInputMode('assign')" class="text-purple-500">
                  <span>Upload Files (.json, .jsonl, .txt)</span>
                </label>
                <label class="flex items-center space-x-1 cursor-pointer text-slate-300">
                  <input type="radio" name="assign-input-mode" value="paste" onchange="toggleAccountInputMode('assign')" class="text-purple-500">
                  <span>Paste Merged Array / JSONL</span>
                </label>
              </div>
            </div>

            <div id="assign-box-upload">
              <input type="file" id="assign-file-input" multiple accept=".json,.jsonl,.txt" onchange="handleMultipleAccountFiles(this, 'assign')" class="w-full text-xs text-slate-400 file:mr-3 file:py-2 file:px-4 file:rounded-lg file:border-0 file:text-xs file:font-semibold file:bg-slate-800 file:text-purple-300 hover:file:bg-slate-700 cursor-pointer">
            </div>
            <div id="assign-box-paste" class="hidden">
              <textarea id="assign-paste-textarea" rows="4" placeholder="[{...}, {...}] or {...}\n{...}" oninput="handlePastedMultipleAccounts('assign')" class="w-full bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-xs font-mono text-slate-200 focus:outline-none"></textarea>
            </div>
            <div id="assign-account-status" class="mt-2 text-xs hidden"></div>
          </div>

          <!-- Configuration & Rules -->
          <div class="grid grid-cols-1 md:grid-cols-4 gap-4 mt-4">
            <div>
              <label class="block text-xs font-semibold uppercase text-slate-400 mb-1">Random Seed</label>
              <input type="number" id="assign-seed" value="42" class="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-sm">
            </div>
            <div>
              <label class="block text-xs font-semibold uppercase text-slate-400 mb-1">Products / Account</label>
              <input type="number" id="assign-count" value="2" class="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-sm">
            </div>
            <div class="md:col-span-2 flex items-center pt-5">
              <label class="flex items-center space-x-2 text-sm text-slate-300 cursor-pointer">
                <input type="checkbox" id="assign-dryrun" checked class="w-4 h-4 text-purple-600 rounded bg-slate-950 border-slate-700">
                <span class="font-medium">Dry-Run Simulation Mode (Safe plan generation without API cart mutations)</span>
              </label>
            </div>
          </div>

          <div class="mt-5 flex items-center space-x-4">
            <button onclick="runAssignment()" id="btn-run-assign" class="bg-purple-600 hover:bg-purple-500 text-white font-medium px-4 py-2 rounded-lg text-sm transition flex items-center space-x-2">
              <i class="fa-solid fa-play"></i>
              <span>Generate Assignment Plan</span>
            </button>
            <span id="assign-spinner" class="hidden text-sm text-slate-400 flex items-center space-x-2">
              <i class="fa-solid fa-circle-notch fa-spin text-purple-500"></i>
              <span>Generating deterministic plan...</span>
            </span>
          </div>

          <div id="assignment-results-card" class="mt-5 hidden"></div>
        </div>
      </section>

      <!-- ======================================================== -->
      <!-- TAB 5: TOKEN REFRESH & SESSIONS                          -->
      <!-- ======================================================== -->
      <section id="tab-sessions" class="space-y-6 hidden">
        <div class="card p-6">
          <div class="flex items-center justify-between border-b border-slate-800 pb-4 mb-4">
            <div>
              <h2 class="text-base font-semibold flex items-center space-x-2 text-white">
                <i class="fa-solid fa-key text-emerald-400"></i>
                <span>Direct API Token Refresh (/api/2.0/xo)</span>
              </h2>
              <p class="text-xs text-slate-400 mt-1">Refreshes expired or expiring composite XO tokens in ~350ms per account directly via official endpoint.</p>
            </div>
            <span class="px-2.5 py-1 rounded-full text-[11px] font-mono bg-emerald-950 text-emerald-400 border border-emerald-800">
              Fast ~350ms Refresh
            </span>
          </div>

          <div class="bg-slate-950 p-4 rounded-xl border border-slate-800">
            <div class="flex items-center justify-between mb-2">
              <label class="text-xs font-semibold uppercase tracking-wider text-slate-300">Accounts Input Source</label>
              <div class="flex items-center gap-3 text-xs">
                <label class="flex items-center space-x-1 cursor-pointer text-slate-300">
                  <input type="radio" name="refresh-input-mode" value="upload" checked onchange="toggleAccountInputMode('refresh')" class="text-emerald-500">
                  <span>Upload Files (.json, .jsonl, .txt)</span>
                </label>
                <label class="flex items-center space-x-1 cursor-pointer text-slate-300">
                  <input type="radio" name="refresh-input-mode" value="paste" onchange="toggleAccountInputMode('refresh')" class="text-emerald-500">
                  <span>Paste Merged Array / JSONL</span>
                </label>
              </div>
            </div>

            <div id="refresh-box-upload">
              <input type="file" id="refresh-file-input" multiple accept=".json,.jsonl,.txt" onchange="handleMultipleAccountFiles(this, 'refresh')" class="w-full text-xs text-slate-400 file:mr-3 file:py-2 file:px-4 file:rounded-lg file:border-0 file:text-xs file:font-semibold file:bg-slate-800 file:text-emerald-300 hover:file:bg-slate-700 cursor-pointer">
            </div>
            <div id="refresh-box-paste" class="hidden">
              <textarea id="refresh-paste-textarea" rows="4" placeholder="[{...}, {...}] or {...}\n{...}" oninput="handlePastedMultipleAccounts('refresh')" class="w-full bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-xs font-mono text-slate-200 focus:outline-none"></textarea>
            </div>
            <div id="refresh-account-status" class="mt-2 text-xs hidden"></div>
          </div>

          <div class="mt-4 flex items-center space-x-4">
            <button onclick="runBatchTokenRefresh()" id="btn-run-refresh" class="bg-emerald-600 hover:bg-emerald-500 text-white font-medium px-4 py-2 rounded-lg text-sm transition flex items-center space-x-2">
              <i class="fa-solid fa-arrows-rotate"></i>
              <span>Refresh Account Tokens</span>
            </button>
            <span id="refresh-spinner" class="hidden text-sm text-slate-400 flex items-center space-x-2">
              <i class="fa-solid fa-circle-notch fa-spin text-emerald-500"></i>
              <span>Refreshing tokens via /api/2.0/xo...</span>
            </span>
          </div>

          <div id="refresh-results-area" class="mt-5"></div>
        </div>
      </section>

    </main>
  </div>

  <!-- JavaScript Client Core -->
  <script>
    // State management
    window.activeAccount = null;
    window.parsedAccounts = {
      pricing: null,
      'fod-single': null,
      'fod-bulk': [],
      acc: [],
      assign: [],
      refresh: []
    };
    window.lastPricingReport = null;

    // Navigation Tab Switching
    function switchTab(tabId) {
      document.querySelectorAll('main > section').forEach(sec => sec.classList.add('hidden'));
      document.querySelectorAll('.nav-btn').forEach(btn => btn.classList.remove('active'));
      const activeSec = document.getElementById(tabId);
      if (activeSec) activeSec.classList.remove('hidden');
      const btnId = 'btn-' + tabId.replace('tab-', '');
      const activeBtn = document.getElementById(btnId);
      if (activeBtn) activeBtn.classList.add('active');
    }

    // Input Mode Toggle
    function toggleAccountInputMode(prefix) {
      const mode = document.querySelector(`input[name="${prefix}-input-mode"]:checked`).value;
      const bUpload = document.getElementById(`${prefix}-box-upload`);
      const bPaste = document.getElementById(`${prefix}-box-paste`);
      if (bUpload) bUpload.classList.toggle('hidden', mode !== 'upload');
      if (bPaste) bPaste.classList.toggle('hidden', mode !== 'paste');

      if (mode === 'active' && window.activeAccount) {
        setAccountStatus(prefix, `✓ Using Active Account: ${window.activeAccount.mobile || window.activeAccount.user_id || 'Loaded'}`, 'emerald');
      }
    }

    // Set Account Status pill
    function setAccountStatus(prefix, msg, color = 'emerald') {
      const el = document.getElementById(`${prefix}-account-status`);
      if (!el) return;
      if (!msg) {
        el.classList.add('hidden');
        el.innerHTML = '';
        return;
      }
      el.className = `mt-2 text-xs font-mono text-${color}-400`;
      el.innerHTML = msg;
      el.classList.remove('hidden');
    }

    // Set active account across all single-account tabs
    function setActiveAccount(acc) {
      window.activeAccount = acc;
      const bar = document.getElementById('active-account-bar');
      const label = document.getElementById('active-account-label');
      if (acc) {
        const idStr = acc.mobile || acc.phone || acc.user_id || 'Valid Account';
        label.textContent = idStr;
        bar.classList.remove('hidden');
        bar.classList.add('flex');
        document.querySelectorAll('[id$="-use-active-label"]').forEach(el => el.classList.remove('hidden'));
      } else {
        bar.classList.add('hidden');
        bar.classList.remove('flex');
        document.querySelectorAll('[id$="-use-active-label"]').forEach(el => el.classList.add('hidden'));
      }
    }

    function clearActiveAccount() {
      setActiveAccount(null);
    }

    // Parse text to account dictionaries
    function parseTextToAccounts(text, sourceRef = '<input>') {
      text = text.trim();
      if (!text) return [];
      try {
        let parsed = JSON.parse(text);
        // Unpack wrapper objects containing account arrays
        if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
          if (parsed.summary && typeof parsed.summary === 'object') {
            for (const k of ['refreshed', 'accounts', 'records', 'data', 'scanned', 'fresh_accounts', 'used_accounts']) {
              if (Array.isArray(parsed.summary[k]) && parsed.summary[k].length > 0 && typeof parsed.summary[k][0] === 'object') {
                parsed = parsed.summary[k];
                break;
              }
            }
          }
          if (!Array.isArray(parsed)) {
            for (const k of ['refreshed', 'accounts', 'records', 'data', 'assignments', 'items', 'detailed_results']) {
              if (Array.isArray(parsed[k]) && parsed[k].length > 0 && typeof parsed[k][0] === 'object') {
                parsed = parsed[k];
                break;
              }
            }
          }
        }
        if (Array.isArray(parsed)) {
          return parsed.map((item, idx) => {
            if (typeof item === 'object' && item !== null) {
              item.source_reference = item.source_reference || `${sourceRef}[${idx}]`;
              return item;
            }
            return null;
          }).filter(Boolean);
        } else if (typeof parsed === 'object' && parsed !== null) {
          parsed.source_reference = parsed.source_reference || sourceRef;
          return [parsed];
        }
      } catch (e) {
        const lines = text.split('\\n');
        const accs = [];
        for (let i = 0; i < lines.length; i++) {
          const l = lines[i].trim();
          if (l.startsWith('{')) {
            try {
              const obj = JSON.parse(l);
              obj.source_reference = `${sourceRef}:${i+1}`;
              accs.push(obj);
            } catch (err) {}
          }
        }
        if (accs.length > 0) return accs;
      }
      return [];
    }

    // Single account file handler
    async function handleSingleAccountFile(input, prefix) {
      if (!input.files || !input.files[0]) return;
      const file = input.files[0];
      try {
        const text = await file.text();
        const accs = parseTextToAccounts(text, file.name);
        if (accs.length === 0) throw new Error("Could not parse valid account JSON.");
        const acc = accs[0];
        window.parsedAccounts[prefix] = acc;
        setActiveAccount(acc);
        setAccountStatus(prefix, `✓ Loaded: ${file.name} (Mobile: ${acc.mobile || 'N/A'}, ID: ${acc.user_id || 'OK'})`, 'emerald');
      } catch (err) {
        window.parsedAccounts[prefix] = null;
        setAccountStatus(prefix, `✗ Error: ${err.message}`, 'rose');
      }
    }

    // Single account paste handler
    function handlePastedAccount(prefix) {
      const text = document.getElementById(`${prefix}-paste-textarea`).value.trim();
      if (!text) {
        window.parsedAccounts[prefix] = null;
        setAccountStatus(prefix, null);
        return;
      }
      const accs = parseTextToAccounts(text, 'pasted');
      if (accs.length > 0) {
        const acc = accs[0];
        window.parsedAccounts[prefix] = acc;
        setActiveAccount(acc);
        setAccountStatus(prefix, `✓ Valid JSON: Mobile ${acc.mobile || 'N/A'}, ID ${acc.user_id || 'OK'}`, 'emerald');
      } else {
        window.parsedAccounts[prefix] = null;
        setAccountStatus(prefix, `✗ Incomplete or invalid JSON object`, 'rose');
      }
    }

    // Multiple accounts file handler
    async function handleMultipleAccountFiles(input, prefix) {
      if (!input.files || input.files.length === 0) return;
      const allAccs = [];
      try {
        for (let i = 0; i < input.files.length; i++) {
          const file = input.files[i];
          const text = await file.text();
          const accs = parseTextToAccounts(text, file.name);
          allAccs.push(...accs);
        }
        window.parsedAccounts[prefix] = allAccs;
        setAccountStatus(prefix, `✓ Parsed ${allAccs.length} account(s) across ${input.files.length} file(s)`, 'emerald');
      } catch (err) {
        window.parsedAccounts[prefix] = [];
        setAccountStatus(prefix, `✗ Error parsing files: ${err.message}`, 'rose');
      }
    }

    // Multiple accounts paste handler
    function handlePastedMultipleAccounts(prefix) {
      const text = document.getElementById(`${prefix}-paste-textarea`).value.trim();
      if (!text) {
        window.parsedAccounts[prefix] = [];
        setAccountStatus(prefix, null);
        return;
      }
      const accs = parseTextToAccounts(text, 'pasted_batch');
      window.parsedAccounts[prefix] = accs;
      if (accs.length > 0) {
        setAccountStatus(prefix, `✓ Parsed ${accs.length} account(s) from pasted text`, 'emerald');
      } else {
        setAccountStatus(prefix, `✗ Could not extract accounts from text. Ensure JSON array or JSONL.`, 'rose');
      }
    }

    // Helper: Retrieve accounts for action
    function getAccountsForAction(prefix, single = false) {
      const mode = document.querySelector(`input[name="${prefix}-input-mode"]:checked`).value;
      if (mode === 'active') {
        if (!window.activeAccount) throw new Error("No active account stored. Please upload or paste account JSON.");
        return single ? window.activeAccount : [window.activeAccount];
      }
      const accs = window.parsedAccounts[prefix];
      if (single) {
        if (!accs) throw new Error("Please upload or paste an account JSON file first.");
        return accs;
      } else {
        if (!accs || accs.length === 0) throw new Error("Please upload or paste at least one account record.");
        return accs;
      }
    }

    // Toggle custom vs all-india pincode scope
    function togglePricingScope() {
      const radios = document.getElementsByName('pricing-scope');
      let val = 'custom';
      for (const r of radios) { if (r.checked) val = r.value; }
      const customContainer = document.getElementById('pricing-custom-pincodes-container');
      const allIndiaInfo = document.getElementById('pricing-all-india-info');
      if (val === 'all_india') {
        customContainer.classList.add('hidden');
        allIndiaInfo.classList.remove('hidden');
      } else {
        customContainer.classList.remove('hidden');
        allIndiaInfo.classList.add('hidden');
      }
    }

    // Download JSON helper
    function downloadJsonData(data, filename) {
      if (!data) return;
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    }

    // ==========================================
    // ACTION 1: RUN PRICING COMPARISON
    // ==========================================
    async function runPricingComparison() {
      const errBanner = document.getElementById('pricing-error-banner');
      const errText = document.getElementById('pricing-error-text');
      const spinner = document.getElementById('pricing-spinner');
      const btn = document.getElementById('btn-run-pricing');
      const resultsCard = document.getElementById('pricing-results-card');
      const tbody = document.getElementById('pricing-table-body');
      const savingsPill = document.getElementById('pricing-savings-pill');

      errBanner.classList.add('hidden');

      let accData;
      try {
        accData = getAccountsForAction('pricing', true);
      } catch (err) {
        errText.textContent = err.message;
        errBanner.classList.remove('hidden');
        return;
      }

      const product = document.getElementById('pricing-product').value.trim();
      if (!product) {
        errText.textContent = "Please provide a product ID or Meesho product URL.";
        errBanner.classList.remove('hidden');
        return;
      }

      const radios = document.getElementsByName('pricing-scope');
      let scope = 'custom';
      for (const r of radios) { if (r.checked) scope = r.value; }
      const isAllIndia = (scope === 'all_india');
      const pincodes = isAllIndia ? '' : document.getElementById('pricing-pincodes').value.trim();

      spinner.classList.remove('hidden');
      btn.disabled = true;
      tbody.innerHTML = '';
      resultsCard.classList.add('hidden');

      try {
        const res = await fetch('/api/compare-pricing', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            product: product,
            pincodes: pincodes,
            all_india: isAllIndia,
            account_data: accData
          })
        });

        const data = await res.json();
        spinner.classList.add('hidden');
        btn.disabled = false;

        if (!data.success) {
          errText.textContent = `Pricing Error: ${data.error || 'Request failed'}`;
          errBanner.classList.remove('hidden');
          return;
        }

        const rep = data.report;
        window.lastPricingReport = rep;

        document.getElementById('pricing-prod-name').textContent = `Product: ${rep.product_name || rep.product_id}`;
        document.getElementById('pricing-summary-text').textContent = `Evaluated ${rep.results.length} delivery pincodes across India &bull; Ranked by lowest final price`;

        if (rep.max_savings_vs_worst > 0) {
          savingsPill.textContent = `Max Savings: ₹${rep.max_savings_vs_worst.toFixed(2)}`;
          savingsPill.classList.remove('hidden');
        } else {
          savingsPill.classList.add('hidden');
        }

        rep.results.forEach((r, idx) => {
          const tr = document.createElement('tr');
          const isCheapest = idx === 0 && r.status === 'VERIFIED_AVAILABLE';
          const loc = r.city ? `${r.city}, ${r.state}` : (r.state || 'N/A');

          tr.className = isCheapest ? 'bg-emerald-950/30' : '';
          tr.innerHTML = `
            <td class="py-2.5 px-3 font-semibold ${isCheapest ? 'text-emerald-400 font-bold' : 'text-slate-400'}">#${idx + 1}</td>
            <td class="py-2.5 px-3 font-mono font-medium">${r.pincode}</td>
            <td class="py-2.5 px-3 text-xs text-slate-300">${loc}</td>
            <td class="py-2.5 px-3">₹${r.displayed_product_price.toFixed(2)}</td>
            <td class="py-2.5 px-3">₹${r.delivery_charges.toFixed(2)}</td>
            <td class="py-2.5 px-3 text-emerald-400">₹${r.payment_method_discounts.toFixed(2)}</td>
            <td class="py-2.5 px-3 font-bold text-rose-400">₹${r.final_payable_price.toFixed(2)}</td>
            <td class="py-2.5 px-3"><span class="px-2 py-0.5 rounded text-[11px] ${r.status === 'VERIFIED_AVAILABLE' ? 'badge-fresh' : 'badge-error'}">${r.status}</span></td>
          `;
          tbody.appendChild(tr);
        });

        resultsCard.classList.remove('hidden');
      } catch (err) {
        spinner.classList.add('hidden');
        btn.disabled = false;
        errText.textContent = `Network / Execution Error: ${err.message || err}`;
        errBanner.classList.remove('hidden');
      }
    }

    // ==========================================
    // ACTION 2A: CHECK SINGLE LIVE FOD
    // ==========================================
    async function checkSingleLiveFod() {
      const container = document.getElementById('fod-single-result');
      const spinner = document.getElementById('fod-single-spinner');
      const btn = document.getElementById('btn-check-single-fod');

      let accData;
      try {
        accData = getAccountsForAction('fod-single', true);
      } catch (err) {
        container.innerHTML = `<div class="text-rose-400">${err.message}</div>`;
        return;
      }

      spinner.classList.remove('hidden');
      btn.disabled = true;
      container.innerHTML = '<div class="text-slate-400 flex items-center space-x-2"><i class="fa-solid fa-circle-notch fa-spin text-amber-500"></i><span>Querying live Meesho FOD endpoint...</span></div>';

      try {
        const res = await fetch('/api/live-fod', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ account_data: accData })
        });
        const data = await res.json();
        spinner.classList.add('hidden');
        btn.disabled = false;

        if (data.error) {
          container.innerHTML = `<div class="text-rose-400">Failed to query live FOD: ${data.error}</div>`;
          return;
        }

        const bucketBadge = data.offer_bucket ? `<span class="ml-2 px-2 py-0.5 rounded text-xs font-mono font-semibold bg-amber-950 text-amber-300 border border-amber-800">Bucket: ${data.offer_bucket}</span>` : '';

        container.innerHTML = `
          <div class="flex items-center justify-between">
            <div>
              <div class="flex items-center">
                <span class="text-xs uppercase font-semibold text-slate-400">Active Advertised Tier</span>
                ${bucketBadge}
              </div>
              <h3 class="text-2xl font-bold text-amber-400 mt-1">${(data.advertised_tier || 'NO_OFFER').toUpperCase()}</h3>
              <p class="text-xs text-slate-300 mt-1">${data.eligibility_reason || 'Live promotional offer status'}</p>
            </div>
            <div class="text-right">
              <span class="text-xs uppercase font-semibold text-slate-400">Advertised Discount</span>
              <div class="text-2xl font-bold text-emerald-400 mt-1">${data.advertised_amount ? '₹' + data.advertised_amount : '₹0'}</div>
              <span class="px-2 py-0.5 rounded text-xs bg-slate-800 text-slate-300 border border-slate-700 mt-1 inline-block">Account Context: Logged-In</span>
            </div>
          </div>
        `;
      } catch (err) {
        spinner.classList.add('hidden');
        btn.disabled = false;
        container.innerHTML = `<div class="text-rose-400">Error: ${err.message || err}</div>`;
      }
    }

    // ==========================================
    // ACTION 2B: RUN BULK LIVE FOD CLASSIFICATION
    // ==========================================
    async function runBulkFodClassification() {
      const spinner = document.getElementById('fod-bulk-spinner');
      const btn = document.getElementById('btn-run-bulk-fod');
      const resultsDiv = document.getElementById('fod-bulk-results');

      let accounts;
      try {
        accounts = getAccountsForAction('fod-bulk', false);
      } catch (err) {
        resultsDiv.innerHTML = `<div class="p-3 bg-rose-950 text-rose-300 text-xs rounded">${err.message}</div>`;
        return;
      }

      spinner.classList.remove('hidden');
      btn.disabled = true;
      resultsDiv.innerHTML = '';

      try {
        const res = await fetch('/api/classify-fod', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ source: accounts, live: true })
        });
        const data = await res.json();
        spinner.classList.add('hidden');
        btn.disabled = false;

        if (!data.success) {
          resultsDiv.innerHTML = `<div class="p-3 bg-rose-950 text-rose-300 text-xs rounded">Error: ${data.error}</div>`;
          return;
        }

        const rep = data.summary;
        window.lastFodSummary = data;

        resultsDiv.innerHTML = `
          <div class="bg-slate-950 border border-slate-800 rounded-lg p-4 mt-3">
            <div class="flex items-center justify-between mb-3">
              <h4 class="font-semibold text-white text-sm">FOD Classification Outcome (Live)</h4>
              <button onclick="downloadJsonData(window.lastFodSummary, 'fod_classification.json')" class="bg-slate-800 hover:bg-slate-700 text-slate-300 px-2.5 py-1 rounded text-xs flex items-center gap-1 border border-slate-700">
                <i class="fa-solid fa-download"></i> Download JSON
              </button>
            </div>
            <div class="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs mb-3">
              <div class="p-2.5 bg-slate-900 rounded border border-slate-800">Scanned Records: <strong class="text-white">${rep.total_scanned}</strong></div>
              <div class="p-2.5 bg-slate-900 rounded border border-slate-800">Unique Accounts: <strong class="text-white">${rep.unique_accounts_count}</strong></div>
              <div class="p-2.5 bg-slate-900 rounded border border-slate-800">Duplicates: <strong class="text-amber-400">${rep.duplicates_count}</strong></div>
              <div class="p-2.5 bg-slate-900 rounded border border-slate-800 text-emerald-400">Live API: <strong>VERIFIED</strong></div>
            </div>
            <div class="text-xs text-slate-300 mb-3">
              <strong>Tiers Found:</strong> ${Object.entries(rep.tier_counts || {}).map(([k, v]) => `<span class="bg-amber-950/70 border border-amber-800 text-amber-300 px-2 py-0.5 rounded mr-1">${k}: ${v}</span>`).join(' ')}
            </div>
            ${data.records && data.records.length > 0 ? `
              <div class="mt-3 overflow-x-auto max-h-96 border border-slate-800 rounded-lg">
                <table class="w-full text-xs text-left text-slate-300">
                  <thead class="text-[11px] uppercase bg-slate-900 text-slate-400 border-b border-slate-800 sticky top-0">
                    <tr>
                      <th class="px-3 py-2">Account / Mobile</th>
                      <th class="px-3 py-2">Advertised Tier</th>
                      <th class="px-3 py-2">Offer Bucket</th>
                      <th class="px-3 py-2">Amount</th>
                      <th class="px-3 py-2">Offer Text</th>
                      <th class="px-3 py-2">Status</th>
                    </tr>
                  </thead>
                  <tbody class="divide-y divide-slate-800 font-mono">
                    ${data.records.map(r => `
                      <tr class="hover:bg-slate-900/50">
                        <td class="px-3 py-2 font-semibold text-slate-200">${r.mobile || r.account_id || r.source_reference}</td>
                        <td class="px-3 py-2"><span class="px-2 py-0.5 rounded bg-amber-950 text-amber-300 border border-amber-800">${r.advertised_tier}</span></td>
                        <td class="px-3 py-2 font-bold text-amber-400">${r.offer_bucket || 'N/A'}</td>
                        <td class="px-3 py-2 font-bold text-emerald-400">${r.advertised_amount ? '₹' + r.advertised_amount : '₹0'}</td>
                        <td class="px-3 py-2 text-slate-300">${r.raw_offer_text || '-'}</td>
                        <td class="px-3 py-2"><span class="px-2 py-0.5 rounded text-[10px] ${r.eligibility_status === 'ELIGIBLE' ? 'bg-emerald-950 text-emerald-400 border border-emerald-800' : 'bg-slate-800 text-slate-400'}">${r.eligibility_status}</span></td>
                      </tr>
                    `).join('')}
                  </tbody>
                </table>
              </div>
            ` : ''}
          </div>
        `;
      } catch (err) {
        spinner.classList.add('hidden');
        btn.disabled = false;
        resultsDiv.innerHTML = `<div class="text-rose-400 text-sm">Error: ${err.message || err}</div>`;
      }
    }

    // ==========================================
    // ACTION 3: ACCOUNT CLASSIFICATION (LIVE)
    // ==========================================
    async function runAccountClassification() {
      const spinner = document.getElementById('acc-spinner');
      const btn = document.getElementById('btn-run-acc');
      const resultsArea = document.getElementById('acc-results-area');

      let accounts;
      try {
        accounts = getAccountsForAction('acc', false);
      } catch (err) {
        resultsArea.innerHTML = `<div class="p-3 bg-rose-950 text-rose-300 text-xs rounded">${err.message}</div>`;
        return;
      }

      spinner.classList.remove('hidden');
      btn.disabled = true;
      resultsArea.innerHTML = '';

      try {
        const res = await fetch('/api/classify-accounts', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ source: accounts, live: true })
        });
        const data = await res.json();
        spinner.classList.add('hidden');
        btn.disabled = false;

        if (!data.success) {
          resultsArea.innerHTML = `<div class="p-3 bg-rose-950 text-rose-300 text-xs rounded">Error: ${data.error}</div>`;
          return;
        }

        const s = data.summary;
        window.lastAccSummary = data;

        resultsArea.innerHTML = `
          <div class="grid grid-cols-1 md:grid-cols-4 gap-4 mb-4">
            <div class="card p-4 text-center">
              <span class="text-xs text-slate-400 uppercase">Total Scanned</span>
              <div class="text-2xl font-bold mt-1 text-white">${s.total_scanned}</div>
            </div>
            <div class="card p-4 text-center border-emerald-900/40">
              <span class="text-xs text-emerald-400 uppercase font-semibold">Fresh Accounts (0)</span>
              <div class="text-2xl font-bold text-emerald-400 mt-1">${s.fresh_count}</div>
            </div>
            <div class="card p-4 text-center border-blue-900/40">
              <span class="text-xs text-blue-400 uppercase font-semibold">Used Accounts (≥1)</span>
              <div class="text-2xl font-bold text-blue-400 mt-1">${s.used_count}</div>
            </div>
            <div class="card p-4 text-center border-amber-900/40">
              <span class="text-xs text-amber-400 uppercase font-semibold">Unknown / Errors</span>
              <div class="text-2xl font-bold text-amber-400 mt-1">${s.unknown_count}</div>
            </div>
          </div>
          ${data.detailed_results && data.detailed_results.length > 0 ? `
            <div class="mb-4 overflow-x-auto max-h-96 border border-slate-800 rounded-lg">
              <table class="w-full text-xs text-left text-slate-300">
                <thead class="text-[11px] uppercase bg-slate-900 text-slate-400 border-b border-slate-800 sticky top-0">
                  <tr>
                    <th class="px-3 py-2">Account / Mobile</th>
                    <th class="px-3 py-2">Classification</th>
                    <th class="px-3 py-2">Order Count</th>
                    <th class="px-3 py-2">Reason / Proof</th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-slate-800 font-mono">
                  ${data.detailed_results.map(r => `
                    <tr class="hover:bg-slate-900/50">
                      <td class="px-3 py-2 font-semibold text-slate-200">${r.mobile || r.account_id || r.source_file}</td>
                      <td class="px-3 py-2"><span class="px-2 py-0.5 rounded ${r.classification === 'fresh' ? 'bg-emerald-950 text-emerald-400 border border-emerald-800' : (r.classification === 'used' ? 'bg-blue-950 text-blue-400 border border-blue-800' : 'bg-amber-950 text-amber-300 border border-amber-800')}">${r.classification.toUpperCase()}</span></td>
                      <td class="px-3 py-2 font-bold ${r.order_count > 0 ? 'text-blue-400' : 'text-emerald-400'}">${r.order_count !== null && r.order_count !== undefined ? r.order_count : '-'}</td>
                      <td class="px-3 py-2 text-slate-300">${r.reason || '-'}</td>
                    </tr>
                  `).join('')}
                </tbody>
              </table>
            </div>
          ` : ''}
          <div class="p-3 bg-slate-950 border border-slate-800 rounded-lg flex items-center justify-between text-xs text-slate-300">
            <span>Verified with live Meesho order history API (/api/3.0/user/orders)</span>
            <button onclick="downloadJsonData(window.lastAccSummary, 'classified_accounts.json')" class="bg-blue-600 hover:bg-blue-500 text-white px-3 py-1 rounded text-xs flex items-center gap-1 transition">
              <i class="fa-solid fa-download"></i> Download Classified JSON
            </button>
          </div>
        `;
      } catch (err) {
        spinner.classList.add('hidden');
        btn.disabled = false;
        resultsArea.innerHTML = `<div class="text-rose-400 text-sm">Error: ${err.message || err}</div>`;
      }
    }

    // ==========================================
    // ACTION 4: BULK ASSIGNMENT
    // ==========================================
    async function runAssignment() {
      const spinner = document.getElementById('assign-spinner');
      const btn = document.getElementById('btn-run-assign');
      const area = document.getElementById('assignment-results-card');
      const seed = parseInt(document.getElementById('assign-seed').value) || 42;
      const count = parseInt(document.getElementById('assign-count').value) || 2;
      const dryRun = document.getElementById('assign-dryrun').checked;

      let accounts;
      try {
        accounts = getAccountsForAction('assign', false);
      } catch (err) {
        area.innerHTML = `<div class="p-3 bg-rose-950 text-rose-300 text-xs rounded">${err.message}</div>`;
        area.classList.remove('hidden');
        return;
      }

      spinner.classList.remove('hidden');
      btn.disabled = true;
      area.innerHTML = '';
      area.classList.add('hidden');

      try {
        const res = await fetch('/api/run-assignment', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            accounts_source: accounts,
            seed: seed,
            count: count,
            dry_run: dryRun
          })
        });
        const data = await res.json();
        spinner.classList.add('hidden');
        btn.disabled = false;

        if (!data.success) {
          area.innerHTML = `<div class="p-3 bg-rose-950 text-rose-300 text-xs rounded">Assignment Error: ${data.error}</div>`;
          area.classList.remove('hidden');
          return;
        }

        const rep = data.report;
        window.lastAssignmentReport = rep;

        let html = `
          <div class="flex items-center justify-between border-b border-slate-800 pb-3 mb-4">
            <div>
              <h3 class="font-semibold text-white">Assignment Report (Mode: ${rep.mode})</h3>
              <p class="text-xs text-slate-400">Random Seed: ${data.seed} &bull; Total Accounts: ${rep.total_accounts}</p>
            </div>
            <button onclick="downloadJsonData(window.lastAssignmentReport, 'assignment_plan.json')" class="bg-purple-600 hover:bg-purple-500 text-white px-3 py-1 rounded text-xs flex items-center gap-1 transition">
              <i class="fa-solid fa-download"></i> Download Assignment Plan
            </button>
          </div>
          <div class="space-y-2 max-h-96 overflow-y-auto">
        `;
        rep.results.forEach(res => {
          let badgeClass = 'bg-purple-950 text-purple-400 border border-purple-800';
          if (res.status === 'SUCCESS') badgeClass = 'bg-emerald-950 text-emerald-400 border border-emerald-800';
          else if (res.status === 'FAILED') badgeClass = 'bg-rose-950 text-rose-400 border border-rose-800';
          else if (res.status && res.status.includes('SKIPPED')) badgeClass = 'bg-amber-950 text-amber-300 border border-amber-800';

          html += `
            <div class="p-3 bg-slate-950 border border-slate-800 rounded-lg">
              <div class="flex items-center justify-between">
                <div>
                  <span class="font-mono text-sm text-purple-400 font-semibold">${res.account_id}</span>
                  <span class="text-xs text-slate-400 ml-2">&bull; Pincode: ${res.assigned_address_pincode || 'N/A'}</span>
                  <span class="text-xs text-slate-400 ml-2">&bull; ${res.assigned_products_count} product(s)</span>
                </div>
                <span class="px-2 py-0.5 rounded text-xs ${badgeClass}">${res.status}</span>
              </div>
              ${res.error ? `<div class="text-xs text-rose-400 mt-1 font-mono"><i class="fa-solid fa-circle-exclamation mr-1"></i>${res.error}</div>` : ''}
            </div>
          `;
        });
        html += '</div>';
        area.innerHTML = html;
        area.classList.remove('hidden');
      } catch (err) {
        spinner.classList.add('hidden');
        btn.disabled = false;
        area.innerHTML = `<div class="p-3 bg-rose-950 text-rose-300 text-xs rounded">Error: ${err.message || err}</div>`;
        area.classList.remove('hidden');
      }
    }

    // ==========================================
    // ACTION 5: BATCH TOKEN REFRESH
    // ==========================================
    async function runBatchTokenRefresh() {
      const spinner = document.getElementById('refresh-spinner');
      const btn = document.getElementById('btn-run-refresh');
      const resultsArea = document.getElementById('refresh-results-area');

      let accounts;
      try {
        accounts = getAccountsForAction('refresh', false);
      } catch (err) {
        resultsArea.innerHTML = `<div class="p-3 bg-rose-950 text-rose-300 text-xs rounded">${err.message}</div>`;
        return;
      }

      spinner.classList.remove('hidden');
      btn.disabled = true;
      resultsArea.innerHTML = '';

      try {
        const res = await fetch('/api/refresh-accounts', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ source: accounts })
        });
        const data = await res.json();
        spinner.classList.add('hidden');
        btn.disabled = false;

        if (!data.success) {
          resultsArea.innerHTML = `<div class="p-3 bg-rose-950 text-rose-300 text-xs rounded">Error: ${data.error}</div>`;
          return;
        }

        const s = data.summary;
        window.lastRefreshSummary = data;

        let html = `
          <div class="grid grid-cols-1 md:grid-cols-3 gap-4 mb-4">
            <div class="card p-4 text-center">
              <span class="text-xs text-slate-400 uppercase font-semibold">Total Accounts</span>
              <div class="text-2xl font-bold mt-1 text-white">${s.total}</div>
            </div>
            <div class="card p-4 text-center border-emerald-900/40">
              <span class="text-xs text-emerald-400 uppercase font-semibold">Refreshed Successfully</span>
              <div class="text-2xl font-bold text-emerald-400 mt-1">${s.refreshed_count}</div>
            </div>
            <div class="card p-4 text-center border-rose-900/40">
              <span class="text-xs text-rose-400 uppercase font-semibold">Failed</span>
              <div class="text-2xl font-bold text-rose-400 mt-1">${s.failed_count}</div>
            </div>
          </div>
          <div class="p-3 bg-slate-950 border border-slate-800 rounded-lg flex items-center justify-between text-xs text-slate-300 mb-3">
            <span>Direct token refresh via /api/2.0/xo executed successfully</span>
            <button onclick="downloadJsonData(window.lastRefreshSummary, 'refreshed_accounts.json')" class="bg-emerald-600 hover:bg-emerald-500 text-white px-3 py-1 rounded text-xs flex items-center gap-1 transition">
              <i class="fa-solid fa-download"></i> Download Refreshed JSON
            </button>
          </div>
        `;

        if (s.refreshed && s.refreshed.length > 0) {
          html += `
            <div class="mt-3 bg-slate-950 border border-slate-800 rounded-lg p-3">
              <h4 class="text-xs font-semibold uppercase text-emerald-400 mb-2">Refreshed Accounts (${s.refreshed.length})</h4>
              <div class="space-y-1 max-h-48 overflow-y-auto">
                ${s.refreshed.map(acc => `
                  <div class="text-xs font-mono text-slate-300 flex justify-between border-b border-slate-900 py-1">
                    <span>${acc.user_id || acc.mobile || 'Account'}</span>
                    <span class="text-emerald-400">✓ Valid Token Active</span>
                  </div>
                `).join('')}
              </div>
            </div>
          `;
        }
        resultsArea.innerHTML = html;
      } catch (err) {
        spinner.classList.add('hidden');
        btn.disabled = false;
        resultsArea.innerHTML = `<div class="p-3 bg-rose-950 text-rose-300 text-xs rounded">Error: ${err.message || err}</div>`;
      }
    }

    // Global Proxy Config Management
    async function loadGlobalProxyConfig() {
      try {
        const res = await fetch('/api/get-global-proxy');
        const data = await res.json();
        document.getElementById('global-proxy-mode').value = data.mode;
        document.getElementById('global-allow-backup').checked = data.allow_direct_backup;
        if (data.mode === 'custom') {
          document.getElementById('global-custom-proxy').classList.remove('hidden');
          document.getElementById('global-custom-proxy').value = data.custom_proxy || '';
        }
      } catch (e) {
        console.warn('Error fetching global proxy:', e);
      }
    }

    async function updateGlobalProxyConfig() {
      const mode = document.getElementById('global-proxy-mode').value;
      const custom = document.getElementById('global-custom-proxy').value;
      const allowBackup = document.getElementById('global-allow-backup').checked;
      const customInput = document.getElementById('global-custom-proxy');

      if (mode === 'custom') {
        customInput.classList.remove('hidden');
      } else {
        customInput.classList.add('hidden');
      }

      try {
        await fetch('/api/set-global-proxy', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            mode: mode,
            custom_proxy: custom,
            allow_direct_backup: allowBackup
          })
        });
      } catch (e) {
        console.warn('Error updating global proxy:', e);
      }
    }

    // Initialize Dashboard
    document.addEventListener('DOMContentLoaded', () => {
      switchTab('tab-pricing');
      loadGlobalProxyConfig();
    });
  </script>
</body>
</html>
"""

# ==============================================================================
# FLASK BACKEND ROUTES (With explicit stdout logging for Render)
# ==============================================================================

@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)


@app.route("/api/get-global-proxy", methods=["GET"])
def api_get_global_proxy():
    """Returns current global proxy settings."""
    cfg = proxy_manager.get_global_proxy_config()
    return jsonify(cfg.to_dict())


@app.route("/api/set-global-proxy", methods=["POST"])
def api_set_global_proxy():
    """Updates global proxy settings across all Meesho requests."""
    req = request.json or {}
    mode = req.get("mode")
    custom = req.get("custom_proxy")
    backup = req.get("allow_direct_backup")

    cfg = proxy_manager.set_global_proxy_config(
        mode=mode,
        custom_proxy=custom,
        allow_direct_backup=backup
    )
    print(f"[*] Proxy config updated: mode={cfg.mode}, allow_direct_backup={cfg.allow_direct_backup}", flush=True)
    return jsonify({"success": True, "config": cfg.to_dict()})


@app.route("/api/compare-pricing", methods=["POST"])
def api_compare_pricing():
    """
    Compares product pricing across candidate pincodes or nationwide benchmark.
    Requires an authenticated account.
    """
    req = request.json or {}
    product = str(req.get("product", "624039638")).strip()
    all_india = req.get("all_india", False)
    pincodes_str = req.get("pincodes", "")
    account_data = req.get("account_data")

    if not account_data or not isinstance(account_data, dict):
        print("[-] /api/compare-pricing: Rejected — No account credentials provided", flush=True)
        return jsonify({"success": False, "error": "Account credentials are required for pricing comparison. Please upload or paste account JSON."}), 400

    if is_blacklisted_account(account_data, "direct_upload"):
        print("[-] /api/compare-pricing: Rejected — Blacklisted operator account", flush=True)
        return jsonify({"success": False, "error": "Unauthorized account"}), 403

    user_ident = str(account_data.get("mobile") or account_data.get("user_id") or "account")
    print(f"[*] /api/compare-pricing: Product={product}, All-India={all_india}, Account={user_ident}", flush=True)

    try:
        start_time = time.time()
        client = MeeshoClient(account_data=account_data, auto_refresh=True)
        comparator = ProductPricingComparator(client=client)

        if all_india:
            report = comparator.compare_product_pricing(product, all_india=True)
        else:
            pincodes = [p.strip() for p in pincodes_str.split(",") if p.strip()]
            if not pincodes:
                pincodes = ["500092", "110025", "560001", "400001"]
            report = comparator.compare_product_pricing(product, pincodes=pincodes)

        elapsed = time.time() - start_time
        print(f"[✓] /api/compare-pricing: Finished {len(report.results)} pincodes in {elapsed:.2f}s", flush=True)
        return jsonify({"success": True, "report": report.to_dict()})
    except Exception as e:
        logger.exception(f"Pricing comparison failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/live-fod", methods=["GET", "POST"])
def api_live_fod():
    """
    Fetches real-time dynamic First Order Discount (FOD) directly from Meesho.
    Always live query.
    """
    account_data = None
    if request.method == "POST":
        req = request.json or {}
        account_data = req.get("account_data")
    
    if account_data and is_blacklisted_account(account_data, "live_fod"):
        return jsonify({"error": "Unauthorized account"}), 403

    user_ident = str(account_data.get("mobile") or account_data.get("user_id") or "anonymous") if account_data else "anonymous"
    print(f"[*] /api/live-fod: Querying live Meesho FOD for {user_ident}", flush=True)

    try:
        record = fetch_live_fod(account_data=account_data)
        print(f"[✓] /api/live-fod: Retrieved tier={record.advertised_tier}, amount={record.advertised_amount}", flush=True)
        return jsonify(record.to_dict(include_raw=False))
    except Exception as e:
        logger.exception(f"Live FOD fetch failed: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/classify-fod", methods=["POST"])
def api_classify_fod():
    """
    Classifies FOD offers across accounts. Always Live as requested.
    """
    req = request.json or {}
    source = req.get("source")
    format_type = req.get("format_type", "combined_json")

    if not source:
        return jsonify({"success": False, "error": "No accounts provided"}), 400

    print(f"[*] /api/classify-fod: Running live classification for {len(source) if isinstance(source, list) else 1} accounts", flush=True)

    try:
        summary = classify_fod_from_source(
            source=source,
            live=True,  # ALWAYS LIVE
            output_dir=None,
            format_type=format_type
        )
        print(f"[✓] /api/classify-fod: Completed {summary.total_scanned} accounts", flush=True)
        return jsonify({
            "success": True,
            "summary": summary.to_dict()["summary"],
            "records": summary.to_dict().get("records", []),
            "format_type": format_type
        })
    except Exception as e:
        logger.exception(f"FOD classification failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/classify-accounts", methods=["POST"])
def api_classify_accounts():
    """
    Classifies accounts into fresh vs used vs unknown. Always Live order history check as requested.
    """
    req = request.json or {}
    source = req.get("source")
    format_type = req.get("format_type", "combined_json")

    if not source:
        return jsonify({"success": False, "error": "No accounts provided"}), 400

    print(f"[*] /api/classify-accounts: Running live classification for {len(source) if isinstance(source, list) else 1} accounts", flush=True)

    try:
        summary = classify_accounts_from_source(
            source=source,
            output_dir=None,
            format_type=format_type,
            live=True  # ALWAYS LIVE
        )
        print(f"[✓] /api/classify-accounts: Completed {summary.total_scanned} accounts (Fresh: {summary.fresh_count}, Used: {summary.used_count}, Unknown: {summary.unknown_count})", flush=True)
        return jsonify({
            "success": True,
            "summary": summary.to_dict()["summary"],
            "detailed_results": [r.to_dict() for r in (summary.fresh_accounts + summary.used_accounts + summary.unknown_accounts)]
        })
    except Exception as e:
        logger.exception(f"Account classification failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/run-assignment", methods=["POST"])
def api_run_assignment():
    """
    Runs deterministic bulk assignment with built-in fallbacks.
    """
    req = request.json or {}
    seed = req.get("seed", 42)
    count = req.get("count", 2)
    dry_run = req.get("dry_run", True)
    format_type = req.get("format_type", "combined_json")
    accounts_source = req.get("accounts_source") or req.get("source")
    products_source = req.get("products_source") or DEFAULT_PRODUCTS
    addresses_source = req.get("addresses_source") or DEFAULT_ADDRESSES

    if not accounts_source:
        return jsonify({"success": False, "error": "No target accounts provided"}), 400

    print(f"[*] /api/run-assignment: seed={seed}, count={count}, dry_run={dry_run}", flush=True)

    try:
        cfg = AssignmentRuleConfig(products_per_account=count, random_seed=seed)
        report = run_bulk_assignment_from_source(
            products_source=products_source,
            addresses_source=addresses_source,
            accounts_source=accounts_source,
            config=cfg,
            dry_run=dry_run,
            output_dir=None,
            format_type=format_type
        )
        print(f"[✓] /api/run-assignment: Generated plan for {report.get('total_accounts', 0)} accounts", flush=True)
        return jsonify({"success": True, "seed": seed, "report": report})
    except Exception as e:
        logger.exception(f"Assignment generation failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/refresh-accounts", methods=["POST"])
def api_refresh_accounts():
    """
    Batch refreshes account tokens via /api/2.0/xo in ~350ms per account.
    """
    req = request.json or {}
    source = req.get("source")
    format_type = req.get("format_type", "combined_json")

    if not source:
        return jsonify({"success": False, "error": "No accounts provided"}), 400

    print(f"[*] /api/refresh-accounts: Refreshing tokens for accounts", flush=True)

    try:
        summary = refresh_accounts_from_source(
            source=source,
            output_dir=None,
            format_type=format_type
        )
        print(f"[✓] /api/refresh-accounts: Total={summary.get('total', 0)}, Refreshed={summary.get('refreshed_count', 0)}, Failed={summary.get('failed_count', 0)}", flush=True)
        return jsonify({"success": True, "summary": summary, "format_type": format_type})
    except Exception as e:
        logger.exception(f"Token refresh failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


def main():
    parser = argparse.ArgumentParser(description="Meesho E2E Web Dashboard")
    default_port = int(os.environ.get("PORT", 5000))
    default_host = os.environ.get("HOST", "0.0.0.0")
    parser.add_argument("--port", type=int, default=default_port, help=f"Port (default: {default_port})")
    parser.add_argument("--host", default=default_host, help=f"Host (default: {default_host})")
    args = parser.parse_args()

    print(f"\n========================================================================")
    print(f"             Meesho E2E Interactive Web Dashboard")
    print(f"========================================================================")
    print(f"[*] Starting dashboard on http://{args.host}:{args.port}")
    print(f"[*] Operator privacy: 7977748514 strictly filtered.")
    print(f"[*] Cloud & Render Readiness: ACTIVE (Gunicorn / Procfile / .gitignore)")
    print(f"[*] Press CTRL+C to stop.\n", flush=True)
    app.run(host=args.host, port=args.port, threaded=True, debug=False)


if __name__ == "__main__":
    main()
