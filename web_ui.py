"""
Meesho E2E Interactive Web Dashboard.
Provides a local web interface for:
- Global Proxy Configuration & Sticky Session Gateway (10x Proxybin -> 15x ProxyScrape pool -> Direct Backup)
- Live Account-Specific First Order Discount (FOD) dynamic monitoring & multi-format classification
- Product pricing comparison: Given Pincodes vs Nationwide All-India Scan (8 Postal Zones)
- Bulk account classification with live order history verification and 1-by-1 txt / combined JSON exports
- Deterministic bulk product & address assignment with configurable input/output formats
- Direct token refresh and session health monitoring (/api/2.0/xo)

STRICT PRIVACY: Operator personal account (7977748514) is blacklisted and never displayed or used.
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Dict, Any, List, Optional

from flask import Flask, render_template_string, request, jsonify

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
    atomic_save_account_json,
    refresh_accounts_from_source
)
from account_classifier import (
    classify_multiple_accounts,
    classify_account_file,
    classify_accounts_from_source,
    AccountClassification
)
from fod_classifier import (
    fetch_live_fod,
    classify_multiple_fod_files,
    classify_fod_from_source
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
    run_bulk_assignment_from_source
)
from meesho_client import MeeshoClient

logger = logging.getLogger("WebUI")

PROJECT_DIR = Path(__file__).resolve().parent

def resolve_file_path(filename: Optional[str]) -> Optional[Path]:
    """Resolves a file path relative to current working directory or project root."""
    if not filename:
        return None
    p = Path(filename)
    if p.is_file():
        return p
    proj_p = PROJECT_DIR / filename
    if proj_p.is_file():
        return proj_p
    return None

def find_local_account_files() -> List[Path]:
    """Finds all candidate account JSON files from project directory or cwd, omitting 7977748514."""
    candidates = {}
    for search_dir in [PROJECT_DIR, Path(".")]:
        try:
            if search_dir.is_dir():
                for f in search_dir.glob("*.json"):
                    if f.name[0].isdigit() and "7977748514" not in f.name:
                        candidates[f.name] = f
        except Exception:
            pass
    return list(candidates.values())

app = Flask(__name__)

HTML_TEMPLATE = """

<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Meesho E2E Engine — Control Dashboard</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
  <style>
    * { box-sizing: border-box; }
    html, body {
      height: 100%;
      margin: 0;
      padding: 0;
      background-color: #0f172a;
      color: #f8fafc;
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    }
    .hidden { display: none !important; }
    .nav-btn {
      display: flex;
      align-items: center;
      width: 100%;
      padding: 0.65rem 0.75rem;
      border-radius: 0.5rem;
      font-size: 0.875rem;
      font-weight: 500;
      text-align: left;
      color: #94a3b8;
      background: transparent;
      border: none;
      cursor: pointer;
      transition: background-color 0.15s, color 0.15s;
    }
    .nav-btn:hover { background-color: #1e293b; color: #f8fafc; }
    .nav-btn.active { background-color: #f43f5e !important; color: #ffffff !important; }
    .card { background-color: #1e293b; border: 1px solid #334155; border-radius: 0.75rem; }
    .badge-fresh { background-color: #10b981; color: #ffffff; }
    .badge-used { background-color: #3b82f6; color: #ffffff; }
    .badge-unknown { background-color: #f59e0b; color: #000000; }
    .badge-error { background-color: #ef4444; color: #ffffff; }
    select, input, textarea {
      color: #f8fafc !important;
      background-color: #020617 !important;
      border-color: #334155 !important;
    }
    select option {
      background-color: #0f172a !important;
      color: #f8fafc !important;
    }
    aside { flex-shrink: 0 !important; width: 16rem !important; }
  </style>
  <script>
    // Immediate global switchTab definition
    window.switchTab = function(tabId) {
      try {
        var sections = document.querySelectorAll('main > section');
        for (var i = 0; i < sections.length; i++) {
          sections[i].classList.add('hidden');
          sections[i].style.display = 'none';
        }
        var btns = document.querySelectorAll('.nav-btn');
        for (var j = 0; j < btns.length; j++) {
          btns[j].classList.remove('active');
        }
        var activeSec = document.getElementById(tabId);
        if (activeSec) {
          activeSec.classList.remove('hidden');
          activeSec.style.display = 'block';
        }
        var btnId = 'btn-' + tabId.replace('tab-', '');
        var activeBtn = document.getElementById(btnId);
        if (activeBtn) activeBtn.classList.add('active');
      } catch(err) {
        console.error('Error switching tab:', err);
      }
    };
  </script>
</head>
<body class="min-h-screen flex flex-col">

  <!-- Top Navigation Header with Global Proxy Bar -->
  <header class="bg-slate-900 border-b border-slate-800 px-6 py-3 flex flex-wrap items-center justify-between gap-4">
    <div class="flex items-center space-x-3">
      <div class="w-10 h-10 rounded-lg bg-rose-600 flex items-center justify-center font-bold text-xl text-white shadow">M</div>
      <div>
        <h1 class="text-xl font-bold tracking-tight">Meesho E2E Engine</h1>
        <p class="text-xs text-slate-400">Direct API Engine &bull; Sticky Proxies &bull; Multi-Format I/O &bull; All-India Pricing</p>
      </div>
    </div>

    <!-- GLOBAL PROXY CONTROL BAR -->
    <div class="flex flex-wrap items-center gap-3 bg-slate-950 p-2 rounded-xl border border-slate-800 text-xs">
      <div class="flex items-center space-x-1.5 text-slate-400 font-medium uppercase tracking-wider text-[11px]">
        <i class="fa-solid fa-network-wired text-rose-500"></i>
        <span>Global Proxy:</span>
      </div>

      <select id="global-proxy-mode" onchange="updateGlobalProxyConfig()" class="bg-slate-900 border border-slate-700 text-slate-200 rounded-lg px-2.5 py-1.5 focus:border-rose-500 focus:outline-none">
        <option value="proxybin_in" selected>🇮🇳 Proxybin (Strict India — 10 Retries)</option>
        <option value="proxybin_any">🌐 Proxybin (Any Global — 10 Retries)</option>
        <option value="pool">🇮🇳 ProxyScrape Pool (15 Retries)</option>
        <option value="custom">⚙️ Custom Proxy</option>
        <option value="direct">🚀 Direct Local IP</option>
      </select>

      <input type="text" id="global-custom-proxy" placeholder="http://host:port" onchange="updateGlobalProxyConfig()" class="hidden bg-slate-900 border border-slate-700 text-slate-200 rounded-lg px-2 py-1.5 w-44 focus:border-rose-500 focus:outline-none">

      <label class="flex items-center space-x-1.5 text-slate-300 cursor-pointer pl-2 border-l border-slate-800">
        <input type="checkbox" id="global-allow-backup" onchange="updateGlobalProxyConfig()" class="w-3.5 h-3.5 text-rose-600 rounded bg-slate-900 border-slate-700">
        <span title="Local system IP is used ONLY when explicitly selected as backup">Local IP Backup</span>
      </label>

      <span id="global-proxy-badge" class="px-2 py-0.5 rounded-full text-[10px] font-mono bg-emerald-950 text-emerald-400 border border-emerald-800 flex items-center gap-1">
        <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span> Sticky Active
      </span>
    </div>
  </header>

  <!-- Main Container -->
  <div class="flex-1 flex overflow-hidden">
    <!-- Sidebar Navigation -->
    <aside style="flex-shrink: 0; width: 16rem; min-width: 16rem;" class="w-64 bg-slate-900/60 border-r border-slate-800 p-4 space-y-1">
      <button type="button" onclick="switchTab('tab-pricing')" id="btn-pricing" class="nav-btn active w-full flex items-center space-x-3 px-3 py-2.5 rounded-lg text-sm font-medium transition text-left hover:bg-slate-800">
        <i class="fa-solid fa-tags w-5 text-center"></i>
        <span>Pricing Comparator</span>
      </button>
      <button type="button" onclick="switchTab('tab-fod')" id="btn-fod" class="nav-btn w-full flex items-center space-x-3 px-3 py-2.5 rounded-lg text-sm font-medium transition text-left hover:bg-slate-800">
        <i class="fa-solid fa-gift w-5 text-center"></i>
        <span>Live FOD Explorer</span>
      </button>
      <button type="button" onclick="switchTab('tab-accounts')" id="btn-accounts" class="nav-btn w-full flex items-center space-x-3 px-3 py-2.5 rounded-lg text-sm font-medium transition text-left hover:bg-slate-800">
        <i class="fa-solid fa-users-gear w-5 text-center"></i>
        <span>Account Classification</span>
      </button>
      <button type="button" onclick="switchTab('tab-assignment')" id="btn-assignment" class="nav-btn w-full flex items-center space-x-3 px-3 py-2.5 rounded-lg text-sm font-medium transition text-left hover:bg-slate-800">
        <i class="fa-solid fa-cubes-stacked w-5 text-center"></i>
        <span>Bulk Assignment</span>
      </button>
      <button type="button" onclick="switchTab('tab-sessions')" id="btn-sessions" class="nav-btn w-full flex items-center space-x-3 px-3 py-2.5 rounded-lg text-sm font-medium transition text-left hover:bg-slate-800">
        <i class="fa-solid fa-key w-5 text-center"></i>
        <span>Token & Sessions</span>
      </button>
    </aside>

    <!-- Content Workspace -->
    <main class="flex-1 p-6 overflow-y-auto">

      <!-- TAB 1: PRICING COMPARATOR -->
      <section id="tab-pricing" class="space-y-6">
        <div class="card p-6 rounded-xl">
          <h2 class="text-lg font-semibold flex items-center space-x-2">
            <i class="fa-solid fa-tags text-rose-500"></i>
            <span>Product Pricing Comparison Across Delivery Pincodes</span>
          </h2>
          <p class="text-sm text-slate-400 mt-1">Evaluates displayed product price, delivery charges, eligible discounts, UPI savings, and ranks lowest payable prices.</p>
          
          <div class="grid grid-cols-1 md:grid-cols-3 gap-4 mt-4">
            <div>
              <label class="block text-xs font-semibold uppercase tracking-wider text-slate-400 mb-1">Product ID or URL</label>
              <input type="text" id="pricing-product" value="624039638" class="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-sm focus:border-rose-500 focus:outline-none">
            </div>

            <div>
              <div class="flex items-center justify-between mb-1">
                <label class="block text-xs font-semibold uppercase tracking-wider text-slate-400">Account Credentials</label>
                <div class="text-[11px] space-x-1">
                  <button type="button" onclick="setPricingAccMode('upload')" id="btn-mode-upload" class="px-1.5 py-0.5 rounded text-xs bg-rose-950 text-rose-400 border border-rose-800 font-medium">Upload File</button>
                  <button type="button" onclick="setPricingAccMode('paste')" id="btn-mode-paste" class="px-1.5 py-0.5 rounded text-xs bg-slate-900 text-slate-400 hover:text-white border border-slate-700">Paste JSON</button>
                  <button type="button" onclick="setPricingAccMode('select')" id="btn-mode-select" class="px-1.5 py-0.5 rounded text-xs bg-slate-900 text-slate-400 hover:text-white border border-slate-700">Saved</button>
                </div>
              </div>

              <!-- Mode 1: File Upload (Default, perfect for cloud deployment) -->
              <div id="pricing-acc-box-upload">
                <input type="file" id="pricing-file-input" accept=".json,.txt" onchange="handlePricingAccountFile(this)" class="w-full text-xs text-slate-400 file:mr-2 file:py-1.5 file:px-3 file:rounded-lg file:border-0 file:text-xs file:font-semibold file:bg-slate-800 file:text-rose-300 hover:file:bg-slate-700 cursor-pointer">
                <div id="pricing-file-status" class="text-xs text-emerald-400 mt-1 font-mono hidden"></div>
              </div>

              <!-- Mode 2: Paste JSON -->
              <div id="pricing-acc-box-paste" class="hidden" style="display: none;">
                <textarea id="pricing-paste-json" rows="2" placeholder='{"mobile": "...", "xo": "...", "ox": "..."}' class="w-full bg-slate-950 border border-slate-700 rounded-lg p-2 text-xs font-mono text-slate-200 focus:border-rose-500 focus:outline-none"></textarea>
              </div>

              <!-- Mode 3: Dropdown Select -->
              <div id="pricing-acc-box-select" class="hidden" style="display: none;">
                <div class="flex items-center space-x-2">
                  <select id="pricing-account" class="w-full bg-slate-950 border border-slate-700 text-slate-200 rounded-lg px-3 py-2 text-sm focus:border-rose-500 focus:outline-none">
                    <option value="">No local accounts on server (Use Upload File)</option>
                  </select>
                  <button type="button" onclick="loadAccountsList()" title="Refresh Accounts List" class="bg-slate-800 hover:bg-slate-700 text-slate-300 p-2 rounded-lg text-xs flex-shrink-0 transition">
                    <i class="fa-solid fa-arrows-rotate"></i>
                  </button>
                </div>
              </div>
            </div>

            <div>
              <label class="block text-xs font-semibold uppercase tracking-wider text-slate-400 mb-1">Pincode Scan Scope</label>
              <div class="flex items-center space-x-4 mt-2">
                <label class="flex items-center space-x-1.5 text-sm cursor-pointer">
                  <input type="radio" name="pricing-scope" value="custom" checked onchange="togglePricingScope()" class="text-rose-600">
                  <span>Custom Pincodes</span>
                </label>
                <label class="flex items-center space-x-1.5 text-sm cursor-pointer">
                  <input type="radio" name="pricing-scope" value="all_india" onchange="togglePricingScope()" class="text-rose-600">
                  <span class="text-rose-400 font-medium">🇮🇳 All-India Scan (8 Zones)</span>
                </label>
              </div>
            </div>
          </div>

          <div id="pricing-custom-pincodes-container" class="mt-4">
            <label class="block text-xs font-semibold uppercase tracking-wider text-slate-400 mb-1">Candidate Pincodes (comma-separated)</label>
            <input type="text" id="pricing-pincodes" value="500092, 110025, 560001, 400001" class="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-sm focus:border-rose-500 focus:outline-none">
          </div>

          <div id="pricing-all-india-info" class="mt-4 p-3 bg-slate-950/70 border border-slate-800 rounded-lg hidden text-xs text-slate-300">
            <span class="font-semibold text-rose-400">All-India Scan Active:</span> Will evaluate delivery charges across 35 representative hubs spanning all 8 Indian Postal Zones (Delhi, UP, Rajasthan, Gujarat, Maharashtra, AP/Telangana, Karnataka, Tamil Nadu, Kerala, West Bengal, Assam, Bihar).
          </div>

          <div class="mt-5 flex items-center space-x-3">
            <button onclick="runPricingComparison()" id="btn-run-pricing" class="bg-rose-600 hover:bg-rose-500 text-white font-medium px-4 py-2 rounded-lg text-sm transition flex items-center space-x-2">
              <i class="fa-solid fa-calculator"></i>
              <span>Compare Pricing</span>
            </button>
            <span id="pricing-spinner" class="hidden text-sm text-slate-400 flex items-center space-x-2">
              <i class="fa-solid fa-circle-notch fa-spin text-rose-500"></i>
              <span>Querying Meesho pricing quotes...</span>
            </span>
          </div>
        </div>

        <div id="pricing-results-card" class="card p-6 rounded-xl hidden">
          <div class="flex items-center justify-between border-b border-slate-700 pb-3 mb-4">
            <div>
              <h3 id="pricing-prod-name" class="font-semibold text-white">Product Results</h3>
              <p id="pricing-summary-text" class="text-xs text-slate-400"></p>
            </div>
            <div id="pricing-savings-pill" class="hidden bg-emerald-950 text-emerald-400 border border-emerald-800 px-3 py-1 rounded-full text-xs font-medium"></div>
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
                  <th class="py-2.5 px-3 font-bold text-rose-400">Payable Price</th>
                  <th class="py-2.5 px-3">Status</th>
                </tr>
              </thead>
              <tbody id="pricing-table-body" class="divide-y divide-slate-800">
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <!-- TAB 2: LIVE FOD EXPLORER -->
      <section id="tab-fod" class="space-y-6 hidden">
        <div class="card p-6 rounded-xl">
          <div class="flex items-center justify-between">
            <div>
              <h2 class="text-lg font-semibold flex items-center space-x-2">
                <i class="fa-solid fa-bolt text-amber-400"></i>
                <span>Live Real-Time First Order Discount (FOD)</span>
              </h2>
              <p class="text-sm text-slate-400 mt-1">Queries Meesho's live production endpoint directly with account-specific credentials and sticky proxy routing.</p>
            </div>
            <div class="flex items-center space-x-3">
              <select id="fod-account-select" class="bg-slate-950 border border-slate-700 text-xs text-slate-200 rounded-lg px-2.5 py-1.5 focus:border-amber-500 focus:outline-none">
                <!-- Dynamically populated -->
              </select>
              <button onclick="fetchLiveFodData()" class="bg-amber-600 hover:bg-amber-500 text-white font-medium px-4 py-2 rounded-lg text-sm transition flex items-center space-x-2">
                <i class="fa-solid fa-arrows-rotate"></i>
                <span>Fetch Live FOD</span>
              </button>
            </div>
          </div>

          <div id="live-fod-result" class="mt-6 bg-slate-950 border border-slate-800 rounded-lg p-5">
            <div class="text-slate-400 text-sm">Click "Fetch Live FOD" to retrieve today's real-time promotional offer directly from Meesho for the selected account.</div>
          </div>
        </div>

        <!-- Bulk Multi-Format FOD Classification -->
        <div class="card p-6 rounded-xl">
          <h3 class="text-base font-semibold flex items-center space-x-2">
            <i class="fa-solid fa-folder-tree text-amber-400"></i>
            <span>Bulk FOD Classification (Multi-Format Input & Output)</span>
          </h3>
          <p class="text-sm text-slate-400 mt-1">Accepts single JSON, directory, merged JSON array, or JSONL. Exports 1-by-1 .txt files in category subfolders or combined JSON.</p>

          <div class="grid grid-cols-1 md:grid-cols-3 gap-4 mt-4">
            <div>
              <label class="block text-xs font-semibold uppercase text-slate-400 mb-1">Input Source</label>
              <select id="fod-input-type" onchange="toggleInputType('fod')" class="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-sm focus:border-amber-500 focus:outline-none">
                <option value="upload">Upload / Select Account Files</option>
                <option value="directory">All Working Directory JSONs</option>
                <option value="file_or_dir">Custom File / Folder Path</option>
                <option value="raw_text">Paste Merged Array or JSONL Text</option>
              </select>
            </div>

            <div id="fod-file-upload-container">
              <label class="block text-xs font-semibold uppercase text-slate-400 mb-1">Select Account JSON / JSONL File(s)</label>
              <input type="file" id="fod-file-input" multiple accept=".json,.jsonl,.txt" onchange="handleFileSelection('fod')" class="w-full text-xs text-slate-400 file:mr-2 file:py-1.5 file:px-3 file:rounded-lg file:border-0 file:text-xs file:font-semibold file:bg-slate-800 file:text-amber-300 hover:file:bg-slate-700 cursor-pointer">
              <div id="fod-file-status" class="text-xs text-amber-400 mt-1 hidden"></div>
            </div>

            <div id="fod-input-path-container" class="hidden">
              <label class="block text-xs font-semibold uppercase text-slate-400 mb-1">Path to File / Folder</label>
              <input type="text" id="fod-custom-path" placeholder="./accounts or merged.json" class="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-sm focus:border-amber-500 focus:outline-none">
            </div>

            <div>
              <label class="block text-xs font-semibold uppercase text-slate-400 mb-1">Output Format</label>
              <select id="fod-output-format" class="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-sm focus:border-amber-500 focus:outline-none">
                <option value="combined_json">Combined JSON Files (by Tier)</option>
                <option value="individual_txt">1-by-1 .txt Files in Tier Subfolders</option>
              </select>
            </div>
          </div>

          <div id="fod-raw-text-container" class="mt-4 hidden">
            <label class="block text-xs font-semibold uppercase text-slate-400 mb-1">Raw JSON Array [ {...} ] or Line-Delimited JSONL</label>
            <textarea id="fod-raw-text" rows="4" placeholder="[{...}] or {...}\n{...}" class="w-full bg-slate-950 border border-slate-700 rounded-lg p-2.5 text-xs font-mono focus:border-amber-500 focus:outline-none"></textarea>
          </div>

          <div class="mt-4 flex items-center space-x-6">
            <label class="flex items-center space-x-2 text-sm text-slate-300 cursor-pointer">
              <input type="checkbox" id="fod-live-toggle" class="w-4 h-4 text-amber-600 rounded bg-slate-950 border-slate-700 focus:ring-amber-500">
              <span>Query Live Meesho FOD API per account</span>
            </label>
            <button onclick="runFodClassification()" class="bg-amber-600 hover:bg-amber-500 text-white px-4 py-2 rounded-lg text-sm font-medium transition flex items-center space-x-2">
              <i class="fa-solid fa-play"></i>
              <span>Run FOD Classification</span>
            </button>
            <span id="fod-scan-spinner" class="hidden text-sm text-slate-400"><i class="fa-solid fa-circle-notch fa-spin mr-1"></i> Scanning...</span>
          </div>
          <div id="fod-bulk-results" class="mt-4"></div>
        </div>
      </section>

      <!-- TAB 3: ACCOUNT CLASSIFICATION -->
      <section id="tab-accounts" class="space-y-6 hidden">
        <div class="card p-6 rounded-xl">
          <h2 class="text-lg font-semibold flex items-center space-x-2">
            <i class="fa-solid fa-users-gear text-blue-400"></i>
            <span>Account Classification (Fresh vs Used vs Unknown)</span>
          </h2>
          <p class="text-sm text-slate-400 mt-1">Classifies records based on complete order history. Missing or unverified data is strictly classified as unknown.</p>

          <div class="grid grid-cols-1 md:grid-cols-3 gap-4 mt-4">
            <div>
              <label class="block text-xs font-semibold uppercase text-slate-400 mb-1">Input Source</label>
              <select id="acc-input-type" onchange="toggleInputType('acc')" class="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-sm focus:border-blue-500 focus:outline-none">
                <option value="upload">Upload / Select Account Files</option>
                <option value="directory">All Working Directory JSONs</option>
                <option value="file_or_dir">Custom File / Folder Path</option>
                <option value="raw_text">Paste Merged Array or JSONL Text</option>
              </select>
            </div>

            <div id="acc-file-upload-container">
              <label class="block text-xs font-semibold uppercase text-slate-400 mb-1">Select Account JSON / JSONL File(s)</label>
              <input type="file" id="acc-file-input" multiple accept=".json,.jsonl,.txt" onchange="handleFileSelection('acc')" class="w-full text-xs text-slate-400 file:mr-2 file:py-1.5 file:px-3 file:rounded-lg file:border-0 file:text-xs file:font-semibold file:bg-slate-800 file:text-blue-300 hover:file:bg-slate-700 cursor-pointer">
              <div id="acc-file-status" class="text-xs text-blue-400 mt-1 hidden"></div>
            </div>

            <div id="acc-input-path-container" class="hidden">
              <label class="block text-xs font-semibold uppercase text-slate-400 mb-1">Path to File / Folder</label>
              <input type="text" id="acc-custom-path" placeholder="./accounts or merged.json" class="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-sm focus:border-blue-500 focus:outline-none">
            </div>

            <div>
              <label class="block text-xs font-semibold uppercase text-slate-400 mb-1">Output Format</label>
              <select id="acc-output-format" class="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-sm focus:border-blue-500 focus:outline-none">
                <option value="combined_json">Combined JSON Files (fresh / used / unknown)</option>
                <option value="individual_txt">1-by-1 .txt Files in Category Subfolders</option>
              </select>
            </div>
          </div>

          <div id="acc-raw-text-container" class="mt-4 hidden">
            <label class="block text-xs font-semibold uppercase text-slate-400 mb-1">Raw JSON Array [ {...} ] or Line-Delimited JSONL</label>
            <textarea id="acc-raw-text" rows="4" placeholder="[{...}] or {...}\n{...}" class="w-full bg-slate-950 border border-slate-700 rounded-lg p-2.5 text-xs font-mono focus:border-blue-500 focus:outline-none"></textarea>
          </div>

          <div class="mt-4 flex items-center space-x-6">
            <label class="flex items-center space-x-2 text-sm text-slate-300 cursor-pointer">
              <input type="checkbox" id="classify-live-toggle" class="w-4 h-4 text-blue-600 rounded bg-slate-950 border-slate-700 focus:ring-blue-500">
              <span>Enable Live Order History API Query (<code>POST /api/3.0/user/orders</code>)</span>
            </label>
            <button onclick="runAccountClassification()" class="bg-blue-600 hover:bg-blue-500 text-white font-medium px-4 py-2 rounded-lg text-sm transition flex items-center space-x-2">
              <i class="fa-solid fa-play"></i>
              <span>Classify Accounts</span>
            </button>
            <span id="account-spinner" class="hidden text-sm text-slate-400"><i class="fa-solid fa-circle-notch fa-spin mr-1"></i> Processing...</span>
          </div>
        </div>

        <div id="account-results-area" class="space-y-4"></div>
      </section>

      <!-- TAB 4: BULK ASSIGNMENT -->
      <section id="tab-assignment" class="space-y-6 hidden">
        <div class="card p-6 rounded-xl">
          <h2 class="text-lg font-semibold flex items-center space-x-2">
            <i class="fa-solid fa-cubes-stacked text-purple-400"></i>
            <span>Bulk Product & Address Deterministic Assignment</span>
          </h2>
          <p class="text-sm text-slate-400 mt-1">Assigns configurable product and delivery address pools across test accounts with a reproducible random seed.</p>

          <!-- Target Accounts Source Input -->
          <div class="grid grid-cols-1 md:grid-cols-3 gap-4 mt-4">
            <div>
              <label class="block text-xs font-semibold uppercase text-slate-400 mb-1">Accounts Source</label>
              <select id="assign-input-type" onchange="toggleInputType('assign')" class="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-sm focus:border-purple-500 focus:outline-none">
                <option value="upload">Upload / Select Account Files</option>
                <option value="directory">All Working Directory JSONs</option>
                <option value="file_or_dir">Custom File / Folder Path</option>
                <option value="raw_text">Paste Merged Array or JSONL Text</option>
              </select>
            </div>

            <div id="assign-file-upload-container">
              <label class="block text-xs font-semibold uppercase text-slate-400 mb-1">Select Target Account File(s)</label>
              <input type="file" id="assign-file-input" multiple accept=".json,.jsonl,.txt" onchange="handleFileSelection('assign')" class="w-full text-xs text-slate-400 file:mr-2 file:py-1.5 file:px-3 file:rounded-lg file:border-0 file:text-xs file:font-semibold file:bg-slate-800 file:text-purple-300 hover:file:bg-slate-700 cursor-pointer">
              <div id="assign-file-status" class="text-xs text-purple-400 mt-1 hidden"></div>
            </div>

            <div id="assign-input-path-container" class="hidden">
              <label class="block text-xs font-semibold uppercase text-slate-400 mb-1">Path to File / Folder</label>
              <input type="text" id="assign-custom-path" placeholder="./accounts or merged.json" class="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-sm focus:border-purple-500 focus:outline-none">
            </div>

            <div>
              <label class="block text-xs font-semibold uppercase text-slate-400 mb-1">Output Format</label>
              <select id="assign-output-format" class="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-sm focus:border-purple-500 focus:outline-none">
                <option value="combined_json">Combined JSON File (assignment_plan.json)</option>
                <option value="individual_txt">1-by-1 .txt File per Account in Subfolder</option>
              </select>
            </div>
          </div>

          <div id="assign-raw-text-container" class="mt-4 hidden">
            <label class="block text-xs font-semibold uppercase text-slate-400 mb-1">Raw JSON Array [ {...} ] or Line-Delimited JSONL</label>
            <textarea id="assign-raw-text" rows="4" placeholder="[{...}] or {...}\n{...}" class="w-full bg-slate-950 border border-slate-700 rounded-lg p-2.5 text-xs font-mono focus:border-purple-500 focus:outline-none"></textarea>
          </div>

          <!-- Dataset & Rules -->
          <div class="grid grid-cols-1 md:grid-cols-4 gap-4 mt-4">
            <div>
              <label class="block text-xs font-semibold uppercase text-slate-400 mb-1">Products Dataset</label>
              <input type="text" id="assign-products-file" value="sample_products.json" class="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-sm focus:border-purple-500 focus:outline-none">
            </div>
            <div>
              <label class="block text-xs font-semibold uppercase text-slate-400 mb-1">Addresses Dataset</label>
              <input type="text" id="assign-addresses-file" value="sample_addresses.json" class="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-sm focus:border-purple-500 focus:outline-none">
            </div>
            <div>
              <label class="block text-xs font-semibold uppercase text-slate-400 mb-1">Random Seed</label>
              <input type="number" id="assign-seed" value="42" class="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-sm focus:border-purple-500 focus:outline-none">
            </div>
            <div>
              <label class="block text-xs font-semibold uppercase text-slate-400 mb-1">Products / Account</label>
              <input type="number" id="assign-count" value="2" class="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-sm focus:border-purple-500 focus:outline-none">
            </div>
          </div>

          <div class="mt-4 flex items-center space-x-4">
            <label class="flex items-center space-x-2 text-sm text-slate-300 cursor-pointer">
              <input type="checkbox" id="assign-dryrun" checked class="w-4 h-4 text-purple-600 rounded bg-slate-950 border-slate-700 focus:ring-purple-500">
              <span>Dry-Run Simulation Mode (Safe, deterministic plan generation)</span>
            </label>
            <button onclick="runAssignment()" class="bg-purple-600 hover:bg-purple-500 text-white font-medium px-4 py-2 rounded-lg text-sm transition flex items-center space-x-2">
              <i class="fa-solid fa-play"></i>
              <span>Generate Assignment</span>
            </button>
            <span id="assign-spinner" class="hidden text-sm text-slate-400"><i class="fa-solid fa-circle-notch fa-spin mr-1"></i> Planning...</span>
          </div>
        </div>

        <div id="assignment-results-card" class="card p-6 rounded-xl hidden"></div>
      </section>

      <!-- TAB 5: TOKEN & SESSIONS -->
      <section id="tab-sessions" class="space-y-6 hidden">
        <div class="card p-6 rounded-xl">
          <div class="flex items-center justify-between">
            <div>
              <h2 class="text-lg font-semibold flex items-center space-x-2">
                <i class="fa-solid fa-key text-emerald-400"></i>
                <span>Direct API Token Refresh & Session Monitor</span>
              </h2>
              <p class="text-sm text-slate-400 mt-1">Refreshes expired or expiring composite XO tokens directly in ~350ms via official <code>POST /api/2.0/xo</code>.</p>
            </div>
            <button onclick="loadAccountsList()" class="bg-slate-800 hover:bg-slate-700 text-slate-300 px-3 py-2 rounded-lg text-xs transition flex items-center space-x-1.5 border border-slate-700">
              <i class="fa-solid fa-arrows-rotate"></i>
              <span>Refresh List</span>
            </button>
          </div>

          <div class="mt-6 overflow-x-auto">
            <table class="w-full text-left text-sm">
              <thead class="text-xs uppercase bg-slate-950 text-slate-400 border-b border-slate-800">
                <tr>
                  <th class="py-2.5 px-3">Account File</th>
                  <th class="py-2.5 px-3">User ID</th>
                  <th class="py-2.5 px-3">Mobile Number</th>
                  <th class="py-2.5 px-3">Token Expiry Status</th>
                  <th class="py-2.5 px-3">Actions</th>
                </tr>
              </thead>
              <tbody id="sessions-table-body" class="divide-y divide-slate-800">
              </tbody>
            </table>
          </div>
        </div>

        <!-- Card 2: Batch Token Refresh (Multi-Format Import & Dual Output) -->
        <div class="card p-6 rounded-xl">
          <h3 class="text-base font-semibold flex items-center space-x-2">
            <i class="fa-solid fa-cloud-arrow-up text-emerald-400"></i>
            <span>Batch Token Refresh (Multi-Format Import & Dual Output)</span>
          </h3>
          <p class="text-sm text-slate-400 mt-1">Import accounts from files, merged JSON array, or JSONL, refresh their tokens via <code>POST /api/2.0/xo</code>, and export refreshed accounts as 1-by-1 .txt files in subfolders or combined JSON.</p>

          <div class="grid grid-cols-1 md:grid-cols-3 gap-4 mt-4">
            <div>
              <label class="block text-xs font-semibold uppercase text-slate-400 mb-1">Input Source</label>
              <select id="refresh-input-type" onchange="toggleInputType('refresh')" class="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-sm focus:border-emerald-500 focus:outline-none">
                <option value="upload">Upload / Select Account Files</option>
                <option value="directory">All Working Directory JSONs</option>
                <option value="file_or_dir">Custom File / Folder Path</option>
                <option value="raw_text">Paste Merged Array or JSONL Text</option>
              </select>
            </div>

            <div id="refresh-file-upload-container">
              <label class="block text-xs font-semibold uppercase text-slate-400 mb-1">Select Account JSON / JSONL File(s)</label>
              <input type="file" id="refresh-file-input" multiple accept=".json,.jsonl,.txt" onchange="handleFileSelection('refresh')" class="w-full text-xs text-slate-400 file:mr-2 file:py-1.5 file:px-3 file:rounded-lg file:border-0 file:text-xs file:font-semibold file:bg-slate-800 file:text-emerald-300 hover:file:bg-slate-700 cursor-pointer">
              <div id="refresh-file-status" class="text-xs text-emerald-400 mt-1 hidden"></div>
            </div>

            <div id="refresh-input-path-container" class="hidden">
              <label class="block text-xs font-semibold uppercase text-slate-400 mb-1">Path to File / Folder</label>
              <input type="text" id="refresh-custom-path" placeholder="./accounts or merged.json" class="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-sm focus:border-emerald-500 focus:outline-none">
            </div>

            <div>
              <label class="block text-xs font-semibold uppercase text-slate-400 mb-1">Output Format</label>
              <select id="refresh-output-format" class="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-sm focus:border-emerald-500 focus:outline-none">
                <option value="combined_json">Combined JSON File (refreshed_accounts.json)</option>
                <option value="individual_txt">1-by-1 .txt Files in Subfolders (refreshed/)</option>
              </select>
            </div>
          </div>

          <div id="refresh-raw-text-container" class="mt-4 hidden">
            <label class="block text-xs font-semibold uppercase text-slate-400 mb-1">Raw JSON Array [ {...} ] or Line-Delimited JSONL</label>
            <textarea id="refresh-raw-text" rows="4" placeholder="[{...}] or {...}\n{...}" class="w-full bg-slate-950 border border-slate-700 rounded-lg p-2.5 text-xs font-mono focus:border-emerald-500 focus:outline-none"></textarea>
          </div>

          <div class="mt-4 flex items-center space-x-4">
            <button onclick="runBatchTokenRefresh()" class="bg-emerald-600 hover:bg-emerald-500 text-white font-medium px-4 py-2 rounded-lg text-sm transition flex items-center space-x-2">
              <i class="fa-solid fa-arrows-rotate"></i>
              <span>Refresh All Accounts</span>
            </button>
            <span id="refresh-spinner" class="hidden text-sm text-slate-400"><i class="fa-solid fa-circle-notch fa-spin mr-1"></i> Refreshing accounts via /api/2.0/xo...</span>
          </div>

          <div id="refresh-results-area" class="mt-4"></div>
        </div>
      </section>

    </main>
  </div>

  <script>
    // Tab switching (re-attached to window for safety)
    window.switchTab = function(tabId) {
      try {
        const allSections = document.querySelectorAll('main > section');
        allSections.forEach(sec => {
          sec.classList.add('hidden');
          sec.style.display = 'none';
        });
        document.querySelectorAll('.nav-btn').forEach(btn => btn.classList.remove('active'));
        const activeSec = document.getElementById(tabId);
        if (activeSec) {
          activeSec.classList.remove('hidden');
          activeSec.style.display = 'block';
        }
        const btnId = 'btn-' + tabId.replace('tab-', '');
        const activeBtn = document.getElementById(btnId);
        if (activeBtn) activeBtn.classList.add('active');
      } catch (err) {
        console.error('switchTab error:', err);
      }
    };

    window.pricingAccountData = null;
    window.pricingAccMode = 'upload';

    function setPricingAccMode(mode) {
      window.pricingAccMode = mode;
      const bUpload = document.getElementById('pricing-acc-box-upload');
      const bPaste = document.getElementById('pricing-acc-box-paste');
      const bSelect = document.getElementById('pricing-acc-box-select');

      const btnUpload = document.getElementById('btn-mode-upload');
      const btnPaste = document.getElementById('btn-mode-paste');
      const btnSelect = document.getElementById('btn-mode-select');

      if (bUpload) bUpload.style.display = (mode === 'upload') ? 'block' : 'none';
      if (bPaste) bPaste.style.display = (mode === 'paste') ? 'block' : 'none';
      if (bSelect) bSelect.style.display = (mode === 'select') ? 'block' : 'none';

      const activeClass = 'px-1.5 py-0.5 rounded text-xs bg-rose-950 text-rose-400 border border-rose-800 font-medium';
      const inactiveClass = 'px-1.5 py-0.5 rounded text-xs bg-slate-900 text-slate-400 hover:text-white border border-slate-700';

      if (btnUpload) btnUpload.className = (mode === 'upload') ? activeClass : inactiveClass;
      if (btnPaste) btnPaste.className = (mode === 'paste') ? activeClass : inactiveClass;
      if (btnSelect) btnSelect.className = (mode === 'select') ? activeClass : inactiveClass;
    }

    async function handlePricingAccountFile(input) {
      const statusDiv = document.getElementById('pricing-file-status');
      if (input && input.files && input.files[0]) {
        const file = input.files[0];
        try {
          const text = await file.text();
          const parsed = JSON.parse(text);
          window.pricingAccountData = parsed;
          if (statusDiv) {
            statusDiv.textContent = `✓ Loaded: ${file.name} (User: ${parsed.mobile || parsed.phone || parsed.user_id || 'OK'})`;
            statusDiv.classList.remove('hidden');
          }
        } catch(err) {
          window.pricingAccountData = null;
          if (statusDiv) {
            statusDiv.textContent = `✗ Invalid JSON: ${err.message || err}`;
            statusDiv.classList.remove('hidden');
          }
        }
      }
    }

    function handleFileSelection(prefix) {
      const fileInput = document.getElementById(`${prefix}-file-input`);
      const statusDiv = document.getElementById(`${prefix}-file-status`);
      if (fileInput && fileInput.files && fileInput.files.length > 0) {
        const names = Array.from(fileInput.files).map(f => f.name);
        statusDiv.textContent = `✓ ${fileInput.files.length} file(s) selected: ${names.slice(0, 3).join(', ')}${names.length > 3 ? '...' : ''}`;
        statusDiv.classList.remove('hidden');
      } else if (statusDiv) {
        statusDiv.classList.add('hidden');
      }
    }

    function toggleInputType(prefix) {
      const typeEl = document.getElementById(`${prefix}-input-type`);
      if (!typeEl) return;
      const type = typeEl.value;
      const uploadBox = document.getElementById(`${prefix}-file-upload-container`);
      const pathBox = document.getElementById(`${prefix}-input-path-container`);
      const rawBox = document.getElementById(`${prefix}-raw-text-container`);

      if (uploadBox) {
        uploadBox.classList.toggle('hidden', type !== 'upload');
        uploadBox.style.display = type === 'upload' ? 'block' : 'none';
      }
      if (pathBox) {
        pathBox.classList.toggle('hidden', type !== 'file_or_dir');
        pathBox.style.display = type === 'file_or_dir' ? 'block' : 'none';
      }
      if (rawBox) {
        rawBox.classList.toggle('hidden', type !== 'raw_text');
        rawBox.style.display = type === 'raw_text' ? 'block' : 'none';
      }
    }

    async function getSourceFromTab(prefix) {
      const type = document.getElementById(`${prefix}-input-type`).value;
      if (type === 'upload') {
        const fileInput = document.getElementById(`${prefix}-file-input`);
        if (!fileInput || !fileInput.files || fileInput.files.length === 0) {
          throw new Error("Please select at least one account file to upload.");
        }
        const accounts = [];
        for (let i = 0; i < fileInput.files.length; i++) {
          const file = fileInput.files[i];
          const text = await file.text();
          try {
            const parsed = JSON.parse(text);
            if (Array.isArray(parsed)) {
              parsed.forEach((item, idx) => {
                if (typeof item === 'object' && item !== null) {
                  item.source_reference = `${file.name}[${idx}]`;
                  accounts.push(item);
                }
              });
            } else if (typeof parsed === 'object' && parsed !== null) {
              parsed.source_reference = file.name;
              accounts.push(parsed);
            }
          } catch (err) {
            // Line-delimited JSONL fallback
            const lines = text.split('\n');
            let parsedAny = false;
            for (let lIdx = 0; lIdx < lines.length; lIdx++) {
              const trimmed = lines[lIdx].trim();
              if (trimmed.startsWith('{')) {
                try {
                  const obj = JSON.parse(trimmed);
                  obj.source_reference = `${file.name}:${lIdx + 1}`;
                  accounts.push(obj);
                  parsedAny = true;
                } catch (e) {}
              }
            }
            if (!parsedAny) {
              throw new Error(`File ${file.name} could not be parsed as JSON or JSONL.`);
            }
          }
        }
        if (accounts.length === 0) {
          throw new Error("No valid accounts found in selected file(s).");
        }
        return accounts;
      } else if (type === 'file_or_dir') {
        const p = document.getElementById(`${prefix}-custom-path`).value.trim();
        return p || '.';
      } else if (type === 'raw_text') {
        const raw = document.getElementById(`${prefix}-raw-text`).value.trim();
        if (!raw) throw new Error("Please paste JSON array or JSONL text.");
        return raw;
      } else {
        return 'directory';
      }
    }

    function togglePricingScope() {
      const radios = document.getElementsByName('pricing-scope');
      let val = 'custom';
      for (const r of radios) { if (r.checked) val = r.value; }
      const customContainer = document.getElementById('pricing-custom-pincodes-container');
      const allIndiaInfo = document.getElementById('pricing-all-india-info');
      if (val === 'all_india') {
        if (customContainer) {
          customContainer.classList.add('hidden');
          customContainer.style.display = 'none';
        }
        if (allIndiaInfo) {
          allIndiaInfo.classList.remove('hidden');
          allIndiaInfo.style.display = 'block';
        }
      } else {
        if (customContainer) {
          customContainer.classList.remove('hidden');
          customContainer.style.display = 'block';
        }
        if (allIndiaInfo) {
          allIndiaInfo.classList.add('hidden');
          allIndiaInfo.style.display = 'none';
        }
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
        } else {
          document.getElementById('global-custom-proxy').classList.add('hidden');
        }
      } catch (e) {
        console.error('Error fetching global proxy config:', e);
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
        console.error('Error updating global proxy:', e);
      }
    }

    // Load accounts list (Strictly omitting 7977748514)
    async function loadAccountsList() {
      const pricingSelect = document.getElementById('pricing-account');
      const fodSelect = document.getElementById('fod-account-select');
      const sessionsTbody = document.getElementById('sessions-table-body');

      if (pricingSelect) pricingSelect.innerHTML = '<option value="">Checking for saved accounts...</option>';
      if (fodSelect) fodSelect.innerHTML = '<option value="">Anonymous Device Mode</option>';

      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 4000);

      try {
        const res = await fetch('/api/accounts', { signal: controller.signal });
        clearTimeout(timeoutId);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        const accounts = data.accounts || [];

        if (sessionsTbody) sessionsTbody.innerHTML = '';
        if (pricingSelect) pricingSelect.innerHTML = '';
        if (fodSelect) fodSelect.innerHTML = '';

        if (accounts.length === 0) {
          if (sessionsTbody) {
            sessionsTbody.innerHTML = '<tr><td colspan="5" class="py-4 text-center text-slate-500">No local account files on server. Use direct upload or Supabase.</td></tr>';
          }
          if (pricingSelect) {
            pricingSelect.innerHTML = '<option value="">No saved accounts on server (Use Upload File)</option>';
          }
          if (fodSelect) {
            fodSelect.innerHTML = '<option value="">Anonymous Device Mode</option>';
          }
          return;
        }

        if (fodSelect) {
          const anonOpt = document.createElement('option');
          anonOpt.value = '';
          anonOpt.textContent = 'Anonymous Device Mode';
          fodSelect.appendChild(anonOpt);
        }

        accounts.forEach(acc => {
          if (pricingSelect) {
            const opt = document.createElement('option');
            opt.value = acc.filename;
            const statusLabel = acc.is_expired ? 'Expired' : `${Math.round(acc.remaining_seconds / 3600)}h left`;
            opt.textContent = `${acc.filename} (${acc.mobile || acc.user_id || 'ID'}) [${statusLabel}]`;
            pricingSelect.appendChild(opt);
          }

          if (fodSelect) {
            const fodOpt = document.createElement('option');
            fodOpt.value = acc.filename;
            fodOpt.textContent = `Account: ${acc.mobile || acc.user_id || acc.filename}`;
            fodSelect.appendChild(fodOpt);
          }

          if (sessionsTbody) {
            const tr = document.createElement('tr');
            const isExp = acc.is_expired;
            const statusBadge = isExp
              ? '<span class="px-2 py-0.5 rounded text-xs bg-rose-950 text-rose-400 border border-rose-800">Expired</span>'
              : `<span class="px-2 py-0.5 rounded text-xs bg-emerald-950 text-emerald-400 border border-emerald-800">Valid (${Math.round(acc.remaining_seconds / 3600)}h remaining)</span>`;

            tr.innerHTML = `
              <td class="py-2.5 px-3 font-mono text-xs text-slate-300">${acc.filename}</td>
              <td class="py-2.5 px-3 font-mono text-xs text-slate-400">${acc.user_id || 'N/A'}</td>
              <td class="py-2.5 px-3 text-xs text-slate-300">${acc.mobile || 'N/A'}</td>
              <td class="py-2.5 px-3">${statusBadge}</td>
              <td class="py-2.5 px-3">
                <button onclick="refreshTokenDirect('${acc.filename}')" class="bg-rose-600/80 hover:bg-rose-500 text-white text-xs px-2.5 py-1 rounded transition">
                  <i class="fa-solid fa-arrows-rotate mr-1"></i> Refresh via /api/2.0/xo
                </button>
              </td>
            `;
            sessionsTbody.appendChild(tr);
          }
        });
      } catch (e) {
        clearTimeout(timeoutId);
        console.warn('Accounts fetch notice:', e.message || e);
        if (pricingSelect) {
          pricingSelect.innerHTML = '<option value="">No local accounts on server (Use Upload File)</option>';
        }
        if (fodSelect) {
          fodSelect.innerHTML = '<option value="">Anonymous Device Mode</option>';
        }
        if (sessionsTbody) {
          sessionsTbody.innerHTML = '<tr><td colspan="5" class="py-4 text-center text-slate-500">No local accounts found on server. Upload JSON directly above.</td></tr>';
        }
      }
    }

    async function refreshTokenDirect(filename) {
      try {
        const res = await fetch('/api/refresh-token', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ filename: filename })
        });
        const data = await res.json();
        if (data.success) {
          alert(`Success: ${data.message}`);
          loadAccountsList();
        } else {
          alert(`Refresh failed: ${data.error}`);
        }
      } catch (e) {
        alert(`Error: ${e}`);
      }
    }

    // Run Pricing Comparison
    async function runPricingComparison() {
      const product = document.getElementById('pricing-product').value.trim();
      const spinner = document.getElementById('pricing-spinner');
      const resultsCard = document.getElementById('pricing-results-card');
      const tbody = document.getElementById('pricing-table-body');
      const savingsPill = document.getElementById('pricing-savings-pill');

      const radios = document.getElementsByName('pricing-scope');
      let scope = 'custom';
      for (const r of radios) { if (r.checked) scope = r.value; }
      const isAllIndia = (scope === 'all_india');
      const pincodes = isAllIndia ? '' : document.getElementById('pricing-pincodes').value.trim();

      let accData = null;
      let accFile = '';

      if (window.pricingAccMode === 'upload') {
        accData = window.pricingAccountData;
        if (!accData) {
          accFile = document.getElementById('pricing-account').value;
          if (!accFile) {
            alert("Please select an account JSON file to upload or pick a saved account.");
            return;
          }
        }
      } else if (window.pricingAccMode === 'paste') {
        const pasteText = document.getElementById('pricing-paste-json').value.trim();
        if (!pasteText) {
          alert("Please paste account JSON credentials into the text box.");
          return;
        }
        try {
          accData = JSON.parse(pasteText);
        } catch(err) {
          alert("Invalid pasted JSON: " + err.message);
          return;
        }
      } else {
        accFile = document.getElementById('pricing-account').value;
      }

      spinner.classList.remove('hidden');
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
            account: accFile,
            account_data: accData
          })
        });
        const data = await res.json();
        spinner.classList.add('hidden');

        if (!data.success) {
          alert(`Pricing comparison error: ${data.error}`);
          return;
        }

        const rep = data.report;
        document.getElementById('pricing-prod-name').textContent = `Product: ${rep.product_name || rep.product_id}`;
        document.getElementById('pricing-summary-text').textContent = `Tested ${rep.results.length} delivery pincodes across India &bull; Ranked by lowest final payable price`;

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

          tr.className = isCheapest ? 'bg-emerald-950/20' : '';
          tr.innerHTML = `
            <td class="py-2.5 px-3 font-semibold ${isCheapest ? 'text-emerald-400' : 'text-slate-400'}">#${idx + 1}</td>
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
      } catch (e) {
        spinner.classList.add('hidden');
        alert(`Error: ${e}`);
      }
    }

    // Fetch Live FOD
    async function fetchLiveFodData() {
      const resContainer = document.getElementById('live-fod-result');
      const accFilename = document.getElementById('fod-account-select').value;
      resContainer.innerHTML = '<div class="text-sm text-slate-400 flex items-center space-x-2"><i class="fa-solid fa-circle-notch fa-spin text-amber-500"></i><span>Querying live Meesho FOD endpoint with sticky session...</span></div>';

      try {
        const res = await fetch(`/api/live-fod?account=${encodeURIComponent(accFilename || '')}`);
        const data = await res.json();
        if (data.error) {
          resContainer.innerHTML = `<div class="text-rose-400 text-sm">Failed to retrieve FOD: ${data.error}</div>`;
          return;
        }

        resContainer.innerHTML = `
          <div class="flex items-center justify-between">
            <div>
              <span class="text-xs uppercase font-semibold text-slate-400">Identified Active Offer Tier</span>
              <h3 class="text-2xl font-bold text-amber-400 mt-1">${data.advertised_tier.toUpperCase()}</h3>
              <p class="text-xs text-slate-300 mt-1">${data.eligibility_reason}</p>
            </div>
            <div class="text-right">
              <span class="text-xs uppercase font-semibold text-slate-400">Advertised Discount Amount</span>
              <div class="text-2xl font-bold text-emerald-400 mt-1">${data.advertised_amount ? '₹' + data.advertised_amount : 'N/A'}</div>
              <span class="px-2 py-0.5 rounded text-xs bg-slate-800 text-slate-300 border border-slate-700 mt-1 inline-block">Account Context: ${accFilename ? 'Logged-In' : 'Anonymous'}</span>
            </div>
          </div>
        `;
      } catch (e) {
        resContainer.innerHTML = `<div class="text-rose-400 text-sm">Error: ${e}</div>`;
      }
    }

    // Run FOD Bulk Classification
    async function runFodClassification() {
      const spinner = document.getElementById('fod-scan-spinner');
      const resultsDiv = document.getElementById('fod-bulk-results');
      const outputFormat = document.getElementById('fod-output-format').value;
      const live = document.getElementById('fod-live-toggle').checked;

      try {
        const source = await getSourceFromTab('fod');
        spinner.classList.remove('hidden');
        resultsDiv.innerHTML = '';

        const res = await fetch('/api/classify-fod', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            source: source,
            live: live,
            format_type: outputFormat
          })
        });
        const data = await res.json();
        spinner.classList.add('hidden');

        if (!data.success) {
          resultsDiv.innerHTML = `<div class="p-3 bg-rose-950 text-rose-300 text-sm rounded">Error: ${data.error}</div>`;
          return;
        }

        const rep = data.summary;
        let html = `
          <div class="bg-slate-950 border border-slate-800 rounded-lg p-4 mt-3">
            <h4 class="font-semibold text-white text-sm mb-2">FOD Classification Outcome</h4>
            <div class="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs mb-3">
              <div class="p-2 bg-slate-900 rounded">Scanned Records: <strong>${rep.total_scanned}</strong></div>
              <div class="p-2 bg-slate-900 rounded">Unique Accounts: <strong>${rep.unique_accounts_count}</strong></div>
              <div class="p-2 bg-slate-900 rounded">Duplicates: <strong>${rep.duplicates_count}</strong></div>
              <div class="p-2 bg-slate-900 rounded text-amber-400">Export: <strong>${data.export_summary ? data.export_summary.format_type : outputFormat}</strong></div>
            </div>
            <div class="text-xs text-slate-300">
              <strong>Tiers Found:</strong> ${Object.entries(rep.tier_counts).map(([k, v]) => `<span class="bg-amber-950/70 border border-amber-800 text-amber-300 px-2 py-0.5 rounded mr-1">${k}: ${v}</span>`).join(' ')}
            </div>
          </div>
        `;
        resultsDiv.innerHTML = html;
      } catch (e) {
        spinner.classList.add('hidden');
        resultsDiv.innerHTML = `<div class="text-rose-400 text-sm">Error: ${e.message || e}</div>`;
      }
    }

    // Run Account Classification
    async function runAccountClassification() {
      const spinner = document.getElementById('account-spinner');
      const resultsArea = document.getElementById('account-results-area');
      const outputFormat = document.getElementById('acc-output-format').value;
      const live = document.getElementById('classify-live-toggle').checked;

      try {
        const source = await getSourceFromTab('acc');
        spinner.classList.remove('hidden');
        resultsArea.innerHTML = '';

        const res = await fetch('/api/classify-accounts', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            source: source,
            live: live,
            format_type: outputFormat
          })
        });
        const data = await res.json();
        spinner.classList.add('hidden');

        if (!data.success) {
          resultsArea.innerHTML = `<div class="p-3 bg-rose-950 text-rose-300 text-sm rounded">Error: ${data.error}</div>`;
          return;
        }

        const s = data.summary;
        let html = `
          <div class="grid grid-cols-1 md:grid-cols-4 gap-4 mb-4">
            <div class="card p-4 rounded-xl text-center">
              <span class="text-xs text-slate-400 uppercase">Total Scanned</span>
              <div class="text-2xl font-bold mt-1">${s.total_scanned}</div>
            </div>
            <div class="card p-4 rounded-xl text-center border-emerald-900/40">
              <span class="text-xs text-emerald-400 uppercase font-semibold">Fresh Accounts</span>
              <div class="text-2xl font-bold text-emerald-400 mt-1">${s.fresh_count}</div>
            </div>
            <div class="card p-4 rounded-xl text-center border-blue-900/40">
              <span class="text-xs text-blue-400 uppercase font-semibold">Used Accounts</span>
              <div class="text-2xl font-bold text-blue-400 mt-1">${s.used_count}</div>
            </div>
            <div class="card p-4 rounded-xl text-center border-amber-900/40">
              <span class="text-xs text-amber-400 uppercase font-semibold">Unknown / Incomplete</span>
              <div class="text-2xl font-bold text-amber-400 mt-1">${s.unknown_count}</div>
            </div>
          </div>
          <div class="p-3 bg-slate-950 border border-slate-800 rounded-lg text-xs text-slate-300">
            Export format: <span class="font-mono text-rose-400">${data.export_summary ? data.export_summary.format_type : outputFormat}</span> &bull; Output path: <span class="font-mono text-slate-400">${data.export_summary ? data.export_summary.output_directory : 'classified_accounts'}</span>
          </div>
        `;
        resultsArea.innerHTML = html;
      } catch (e) {
        spinner.classList.add('hidden');
        resultsArea.innerHTML = `<div class="text-rose-400 text-sm">Error: ${e.message || e}</div>`;
      }
    }

    // Run Bulk Assignment
    async function runAssignment() {
      const spinner = document.getElementById('assign-spinner');
      const area = document.getElementById('assignment-results-card');
      const prodsFile = document.getElementById('assign-products-file').value.trim();
      const addrsFile = document.getElementById('assign-addresses-file').value.trim();
      const seed = parseInt(document.getElementById('assign-seed').value) || 42;
      const count = parseInt(document.getElementById('assign-count').value) || 2;
      const outputFormat = document.getElementById('assign-output-format').value;
      const dryRun = document.getElementById('assign-dryrun').checked;

      try {
        const source = await getSourceFromTab('assign');
        spinner.classList.remove('hidden');
        area.innerHTML = '';
        area.classList.add('hidden');

        const res = await fetch('/api/run-assignment', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            source: source,
            products_file: prodsFile,
            addresses_file: addrsFile,
            seed: seed,
            count: count,
            dry_run: dryRun,
            format_type: outputFormat
          })
        });
        const data = await res.json();
        spinner.classList.add('hidden');

        if (!data.success) {
          alert(`Assignment error: ${data.error}`);
          return;
        }

        const rep = data.report;
        let html = `
          <div class="flex items-center justify-between border-b border-slate-700 pb-3 mb-4">
            <div>
              <h3 class="font-semibold text-white">Assignment Report (Mode: ${rep.mode})</h3>
              <p class="text-xs text-slate-400">Random Seed: ${data.seed} &bull; Total Accounts: ${rep.total_accounts} &bull; Export: ${outputFormat}</p>
            </div>
          </div>
          <div class="space-y-3">
        `;
        rep.results.forEach(res => {
          html += `
            <div class="p-3 bg-slate-950 border border-slate-800 rounded-lg flex items-center justify-between">
              <div>
                <span class="font-mono text-sm text-purple-400 font-semibold">${res.account_id}</span>
                <span class="text-xs text-slate-400 ml-2">&bull; Pincode: ${res.assigned_address_pincode}</span>
                <span class="text-xs text-slate-400 ml-2">&bull; ${res.assigned_products_count} product(s) assigned</span>
              </div>
              <span class="px-2 py-0.5 rounded text-xs bg-purple-950 text-purple-400 border border-purple-800">${res.status}</span>
            </div>
          `;
        });
        html += '</div>';
        area.innerHTML = html;
        area.classList.remove('hidden');
      } catch (e) {
        spinner.classList.add('hidden');
        alert(`Error: ${e.message || e}`);
      }
    }

    // Run Batch Token Refresh (Requirement 3)
    async function runBatchTokenRefresh() {
      const spinner = document.getElementById('refresh-spinner');
      const resultsArea = document.getElementById('refresh-results-area');
      const outputFormat = document.getElementById('refresh-output-format').value;

      try {
        const source = await getSourceFromTab('refresh');
        spinner.classList.remove('hidden');
        resultsArea.innerHTML = '';

        const res = await fetch('/api/refresh-accounts', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            source: source,
            format_type: outputFormat
          })
        });
        const data = await res.json();
        spinner.classList.add('hidden');

        if (!data.success) {
          resultsArea.innerHTML = `<div class="p-3 bg-rose-950 text-rose-300 text-sm rounded">Error: ${data.error}</div>`;
          return;
        }

        const s = data.summary;
        let html = `
          <div class="grid grid-cols-1 md:grid-cols-3 gap-4 mb-4">
            <div class="card p-4 rounded-xl text-center">
              <span class="text-xs text-slate-400 uppercase font-semibold">Total Accounts</span>
              <div class="text-2xl font-bold mt-1 text-white">${s.total}</div>
            </div>
            <div class="card p-4 rounded-xl text-center border-emerald-900/40">
              <span class="text-xs text-emerald-400 uppercase font-semibold">Refreshed Successfully</span>
              <div class="text-2xl font-bold text-emerald-400 mt-1">${s.refreshed_count}</div>
            </div>
            <div class="card p-4 rounded-xl text-center border-rose-900/40">
              <span class="text-xs text-rose-400 uppercase font-semibold">Failed</span>
              <div class="text-2xl font-bold text-rose-400 mt-1">${s.failed_count}</div>
            </div>
          </div>
          <div class="p-3 bg-slate-950 border border-slate-800 rounded-lg text-xs text-slate-300 mb-3">
            Export format: <span class="font-mono text-emerald-400">${data.format_type}</span> &bull; Output path: <span class="font-mono text-slate-400">refreshed_accounts/</span>
          </div>
        `;

        if (s.refreshed && s.refreshed.length > 0) {
          html += `
            <div class="mt-3 bg-slate-950 border border-slate-800 rounded-lg p-3">
              <h4 class="text-xs font-semibold uppercase text-emerald-400 mb-2">Refreshed Accounts (${s.refreshed.length})</h4>
              <div class="space-y-1.5 max-h-48 overflow-y-auto">
                ${s.refreshed.map(acc => `
                  <div class="text-xs font-mono text-slate-300 flex justify-between border-b border-slate-900 py-1">
                    <span>Account ID: ${acc.user_id || acc.mobile || 'User'}</span>
                    <span class="text-emerald-400">✓ Token Valid (Composite XO Active)</span>
                  </div>
                `).join('')}
              </div>
            </div>
          `;
        }

        if (s.failed && s.failed.length > 0) {
          html += `
            <div class="mt-3 bg-slate-950 border border-slate-800 rounded-lg p-3">
              <h4 class="text-xs font-semibold uppercase text-rose-400 mb-2">Failed Accounts (${s.failed.length})</h4>
              <div class="space-y-1.5 max-h-48 overflow-y-auto">
                ${s.failed.map(f => `
                  <div class="text-xs font-mono text-slate-300 border-b border-slate-900 py-1">
                    <span class="text-rose-400 font-semibold">${f.account_id}:</span> ${f.error}
                  </div>
                `).join('')}
              </div>
            </div>
          `;
        }

        resultsArea.innerHTML = html;
        loadAccountsList();
      } catch (e) {
        spinner.classList.add('hidden');
        resultsArea.innerHTML = `<div class="p-3 bg-rose-950 text-rose-300 text-sm rounded">Error: ${e.message || e}</div>`;
      }
    }

    // Initialize on page load
    function initializeDashboard() {
      switchTab('tab-pricing');
      loadGlobalProxyConfig();
      loadAccountsList();
    }

    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', initializeDashboard);
    } else {
      initializeDashboard();
    }
  </script>

</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route("/api/get-global-proxy", methods=["GET"])
def api_get_global_proxy():
    """Returns the current global proxy settings."""
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
    return jsonify({"success": True, "config": cfg.to_dict()})

@app.route("/api/accounts", methods=["GET"])
def api_accounts():
    """Lists accounts, strictly omitting blacklisted personal account (7977748514)."""
    acc_files = find_local_account_files()
    accounts = []
    seen_mobiles = set()
    for f in acc_files:
        try:
            with open(f, "r", encoding="utf-8") as fp:
                data = json.load(fp)
            if is_blacklisted_account(data, f.name):
                continue
            xo = data.get("xo", "")
            meta = parse_xo_token(xo) if xo else None
            mobile = data.get("mobile") or data.get("phone")
            if mobile:
                seen_mobiles.add(str(mobile))
            accounts.append({
                "filename": f.name,
                "user_id": data.get("user_id"),
                "mobile": mobile,
                "is_expired": meta.is_expired if meta else True,
                "remaining_seconds": max(0.0, meta.remaining_seconds) if meta else 0.0
            })
        except Exception:
            pass

    # Optional Supabase Cloud accounts integration
    if supabase_db.is_supabase_enabled():
        try:
            sb_accounts = supabase_db.fetch_accounts_from_supabase()
            for sba in sb_accounts:
                mob = str(sba.get("mobile") or sba.get("phone") or "")
                if mob in seen_mobiles or "7977748514" in mob:
                    continue
                xo = sba.get("xo", "")
                meta = parse_xo_token(xo) if xo else None
                accounts.append({
                    "filename": f"supabase_{mob}.json",
                    "user_id": sba.get("user_id"),
                    "mobile": mob,
                    "is_expired": meta.is_expired if meta else True,
                    "remaining_seconds": max(0.0, meta.remaining_seconds) if meta else 0.0
                })
        except Exception as e:
            logger.warning(f"Could not load accounts from Supabase: {e}")

    return jsonify({"accounts": accounts})

@app.route("/api/refresh-token", methods=["POST"])
def api_refresh_token():
    """Refreshes an account token directly using /api/2.0/xo."""
    req = request.json or {}
    filename = req.get("filename")
    target_path = resolve_file_path(filename)
    if not target_path or "7977748514" in target_path.name:
        return jsonify({"success": False, "error": "Invalid or blacklisted account filename"}), 400

    try:
        acc_data = load_account_json(target_path)
        if is_blacklisted_account(acc_data, target_path.name):
            return jsonify({"success": False, "error": "Unauthorized account"}), 403

        provider = DirectApiTokenRefreshProvider()
        refreshed = provider.refresh(acc_data)
        atomic_save_account_json(refreshed, target_path)
        return jsonify({"success": True, "message": "Session refreshed via /api/2.0/xo"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/live-fod", methods=["GET"])
def api_live_fod():
    """Fetches real-time dynamic FOD from Meesho production API with account context."""
    acc_param = request.args.get("account", "").strip()
    account_data = None
    target_path = resolve_file_path(acc_param)
    if target_path and "7977748514" not in target_path.name:
        try:
            account_data = load_account_json(target_path)
        except Exception:
            pass

    try:
        record = fetch_live_fod(account_data=account_data)
        return jsonify(record.to_dict(include_raw=False))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/classify-fod", methods=["POST"])
def api_classify_fod():
    """Runs FOD classification from single file, directory, merged array, or JSONL."""
    req = request.json or {}
    source = req.get("source", "directory")
    live = req.get("live", False)
    format_type = req.get("format_type", "combined_json")

    if isinstance(source, (list, dict)):
        src_input = source
    elif source == "directory":
        src_input = find_local_account_files()
    else:
        resolved = resolve_file_path(str(source))
        src_input = str(resolved) if resolved else source

    try:
        summary = classify_fod_from_source(
            source=src_input,
            live=live,
            output_dir="classified_fod",
            format_type=format_type
        )
        return jsonify({
            "success": True,
            "summary": summary.to_dict()["summary"],
            "format_type": format_type
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/compare-pricing", methods=["POST"])
def api_compare_pricing():
    """Compares product pricing across candidate pincodes or All-India benchmark."""
    req = request.json or {}
    product = req.get("product", "624039638")
    all_india = req.get("all_india", False)
    pincodes_str = req.get("pincodes", "")
    account_file = req.get("account")
    account_data = req.get("account_data")

    acc_path = None
    if account_data and isinstance(account_data, dict):
        if is_blacklisted_account(account_data, "direct_upload"):
            return jsonify({"success": False, "error": "Unauthorized account"}), 403
        acc_dict = account_data
    else:
        acc_path = resolve_file_path(account_file) if account_file else None
        if not acc_path or "7977748514" in str(acc_path):
            json_files = find_local_account_files()
            if json_files:
                acc_path = json_files[0]
            else:
                return jsonify({"success": False, "error": "No account credentials provided. Please upload or select an account JSON."}), 400
        acc_dict = load_account_json(acc_path)

    try:
        client = MeeshoClient(
            account_data=acc_dict,
            account_path=str(acc_path) if acc_path else None,
            auto_refresh=True
        )
        comparator = ProductPricingComparator(client=client)

        if all_india:
            report = comparator.compare_product_pricing(product, all_india=True)
        else:
            pincodes = [p.strip() for p in pincodes_str.split(",") if p.strip()]
            if not pincodes:
                pincodes = ["500092", "110025", "560001", "400001"]
            report = comparator.compare_product_pricing(product, pincodes=pincodes)

        return jsonify({"success": True, "report": report.to_dict()})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/classify-accounts", methods=["POST"])
def api_classify_accounts():
    """Classifies accounts with optional live order verification and flexible I/O."""
    req = request.json or {}
    source = req.get("source", "directory")
    live = req.get("live", False)
    format_type = req.get("format_type", "combined_json")

    if isinstance(source, (list, dict)):
        src_input = source
    elif source == "directory":
        src_input = find_local_account_files()
    else:
        resolved = resolve_file_path(str(source))
        src_input = str(resolved) if resolved else source

    try:
        summary = classify_accounts_from_source(
            source=src_input,
            output_dir="classified_accounts",
            format_type=format_type,
            live=live
        )
        return jsonify({
            "success": True,
            "summary": summary.to_dict()["summary"]
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/run-assignment", methods=["POST"])
def api_run_assignment():
    """Runs deterministic bulk assignment with flexible input and output formats."""
    req = request.json or {}
    products_file = req.get("products_file", "sample_products.json")
    addresses_file = req.get("addresses_file", "sample_addresses.json")
    seed = req.get("seed", 42)
    count = req.get("count", 2)
    dry_run = req.get("dry_run", True)
    format_type = req.get("format_type", "combined_json")
    source = req.get("source") or req.get("accounts_source") or "directory"

    if isinstance(source, (list, dict)):
        src_input = source
    elif source == "directory":
        src_input = find_local_account_files()
    else:
        resolved = resolve_file_path(str(source))
        src_input = str(resolved) if resolved else source

    prods_resolved = resolve_file_path(str(products_file))
    addrs_resolved = resolve_file_path(str(addresses_file))

    try:
        cfg = AssignmentRuleConfig(products_per_account=count, random_seed=seed)
        report = run_bulk_assignment_from_source(
            products_source=str(prods_resolved or products_file),
            addresses_source=str(addrs_resolved or addresses_file),
            accounts_source=src_input,
            config=cfg,
            dry_run=dry_run,
            output_dir="assignment_outputs",
            format_type=format_type
        )
        return jsonify({"success": True, "seed": seed, "report": report})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/refresh-accounts", methods=["POST"])
def api_refresh_accounts():
    """Batch refreshes accounts from any format and exports 1-by-1 .txt or combined .json."""
    req = request.json or {}
    source = req.get("source", "directory")
    format_type = req.get("format_type", "combined_json")

    if isinstance(source, (list, dict)):
        src_input = source
    elif source == "directory":
        src_input = find_local_account_files()
    else:
        resolved = resolve_file_path(str(source))
        src_input = str(resolved) if resolved else source

    try:
        summary = refresh_accounts_from_source(
            source=src_input,
            output_dir="refreshed_accounts",
            format_type=format_type
        )
        return jsonify({"success": True, "summary": summary, "format_type": format_type})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


def main():
    parser = argparse.ArgumentParser(description="Meesho E2E Web Dashboard")
    default_port = int(os.environ.get("PORT", 5000))
    default_host = os.environ.get("HOST", "0.0.0.0")
    parser.add_argument("--port", type=int, default=default_port, help=f"Port to bind dashboard (default: {default_port})")
    parser.add_argument("--host", default=default_host, help=f"Host interface (default: {default_host})")
    args = parser.parse_args()

    print(f"\n========================================================================")
    print(f"             Meesho E2E Interactive Web Dashboard")
    print(f"========================================================================")
    print(f"[*] Starting dashboard on http://{args.host}:{args.port}")
    print(f"[*] Operator privacy: 7977748514 strictly filtered.")
    print(f"[*] Cloud & Render Readiness: ACTIVE (Gunicorn / Procfile / .gitignore)")
    print(f"[*] Press CTRL+C to stop.\n")
    app.run(host=args.host, port=args.port, threaded=True, debug=False)


if __name__ == "__main__":
    main()
