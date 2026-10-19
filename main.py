"""
Meesho E2E Engine CLI & Orchestrator.
Automates:
1. Randomized product cart additions from links/IDs
2. Randomized address assignment from an address pool
3. Multi-pincode delivery fee optimization & final UPI pricing
4. Multi-pincode product pricing comparison with component breakdown & caching
5. Bulk deterministic product and address assignment across test accounts
6. Account classification from JSON records (fresh vs used vs unknown)
7. FOD offer promotional extraction and tier classification
8. Headless token refresh and session verification
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, Any, List, Optional

# Ensure UTF-8 output on Windows
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from auth import load_account_json
from meesho_client import MeeshoClient
from cart import add_random_products_to_cart, view_cart
from address import add_random_address_from_pool, get_account_addresses
from optimizer import find_optimal_pincode_and_upi_price
from pricing_comparator import ProductPricingComparator, PricingCache, get_all_india_pincodes
from assignment_engine import (
    AssignmentPlanner,
    AssignmentExecutor,
    AssignmentRuleConfig,
    run_bulk_assignment_from_source,
    validate_product_reference,
    validate_test_address
)
from account_classifier import classify_multiple_accounts, classify_accounts_from_source
from fod_classifier import classify_multiple_fod_files, classify_fod_from_source, fetch_live_fod
from account_manager import is_token_expired, parse_xo_token, validate_account_schema, refresh_accounts_from_source
import proxy_manager
from data_io import load_accounts_from_source, export_categorized_results, is_blacklisted_account



def print_banner():
    banner = """
========================================================================
             Meesho E2E Cart, Address & UPI Pricing Engine              
========================================================================
"""
    print(banner)


def main():
    parser = argparse.ArgumentParser(description="Meesho E2E Automation Engine")
    parser.add_argument(
        "--account",
        "-a",
        default="8869049741.json",
        help="Path to account JSON file (default: 8869049741.json)"
    )
    parser.add_argument(
        "--action",
        choices=["all", "cart", "address", "optimize", "pricing", "assign", "classify-accounts", "classify-fod", "refresh"],
        default="all",
        help="Action to execute (default: all)"
    )
    parser.add_argument(
        "--data",
        "-d",
        default="sample_data.json",
        help="Path to sample data JSON containing products, addresses, and pincodes"
    )
    parser.add_argument(
        "--count",
        "-c",
        type=int,
        default=2,
        help="Number of random products to add to cart (default: 2)"
    )
    parser.add_argument(
        "--pincodes",
        "-p",
        help="Comma-separated list of candidate pincodes to test"
    )
    parser.add_argument(
        "--all-india",
        action="store_true",
        help="Scan representative postal hubs spanning all 8 Indian zones to find nationwide lowest price"
    )
    parser.add_argument(
        "--product",
        help="Product URL or numerical product ID for pricing comparison"
    )
    parser.add_argument(
        "--source",
        "-s",
        help="Account source: single JSON, folder, merged JSON array, or JSONL file"
    )
    parser.add_argument(
        "--format",
        "-f",
        choices=["combined_json", "individual_txt"],
        default="combined_json",
        help="Export format: 'combined_json' or 'individual_txt' (default: combined_json)"
    )
    parser.add_argument(
        "--input-dir",
        help="Directory containing account JSON files for batch classification"
    )
    parser.add_argument(
        "--output-dir",
        help="Output directory to save classification reports and consolidated JSON/TXT files"
    )
    parser.add_argument(
        "--products-file",
        help="Path to JSON file with candidate products list for bulk assignment"
    )
    parser.add_argument(
        "--addresses-file",
        help="Path to JSON file with candidate addresses list for bulk assignment"
    )
    parser.add_argument(
        "--config-file",
        help="Path to JSON assignment configuration file"
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for deterministic assignment (default: 42)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Execute assignment in dry-run simulation mode without network mutations (default: True)"
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Execute live assignment/query against Meesho API (overrides dry-run)"
    )
    parser.add_argument(
        "--proxy",
        default="proxybin_in",
        help="Proxy mode: 'proxybin_in' (default), 'proxybin_any', 'pool', 'custom', or 'direct'"
    )
    parser.add_argument(
        "--allow-local-backup",
        action="store_true",
        help="Allow local system IP as explicit fallback if proxy attempts are exhausted"
    )

    args = parser.parse_args()

    # Configure Global Proxy Engine
    proxy_manager.set_global_proxy_config(
        mode=args.proxy,
        allow_direct_backup=args.allow_local_backup
    )

    print_banner()

    # --- STANDALONE ACTION 1: CLASSIFY ACCOUNTS ---
    if args.action == "classify-accounts":
        print("[*] Running Bulk Account Classification based on Order History...")
        source = args.source or (Path(args.input_dir) if args.input_dir else Path("."))
        out_dir = args.output_dir or "classified_accounts"
        summary = classify_accounts_from_source(
            source=source,
            output_dir=out_dir,
            format_type=args.format,
            live=args.live
        )

        print("\n" + "=" * 60)
        print("                 ACCOUNT CLASSIFICATION SUMMARY             ")
        print("=" * 60)
        print(f"  Total Scanned       : {summary.total_scanned}")
        print(f"  Fresh Accounts (0)  : {summary.fresh_count}")
        print(f"  Used Accounts (>=1) : {summary.used_count}")
        print(f"  Unknown Accounts    : {summary.unknown_count}")
        print(f"  Schema Errors       : {summary.invalid_files_count}")
        print(f"  Export Format       : {args.format}")
        print(f"  Outputs Written To  : {Path(out_dir).resolve()}")
        print("=" * 60 + "\n")
        return

    # --- STANDALONE ACTION 2: CLASSIFY FOD ---
    if args.action == "classify-fod":
        source = args.source or (Path(args.input_dir) if args.input_dir else Path("."))
        out_dir = args.output_dir or "classified_fod"
        print(f"[*] Running FOD Promotional Classification (Live: {args.live})...")
        summary = classify_fod_from_source(
            source=source,
            live=args.live,
            output_dir=out_dir,
            format_type=args.format
        )

        print("\n" + "=" * 60)
        print("                   FOD CLASSIFICATION SUMMARY               ")
        print("=" * 60)
        print(f"  Total Scanned        : {summary.total_scanned}")
        print(f"  Unique Accounts      : {summary.unique_accounts_count}")
        print(f"  Duplicates Detected  : {summary.duplicates_count}")
        print(f"  Export Format        : {args.format}")
        print(f"  Tier Breakdown       :")
        for tier, cnt in sorted(summary.tier_counts.items()):
            print(f"    - {tier:<18} : {cnt}")
        print(f"  Eligibility Status   :")
        for st, cnt in sorted(summary.eligibility_counts.items()):
            print(f"    - {st:<18} : {cnt}")
        print(f"  Outputs Written To   : {Path(out_dir).resolve()}")
        print("=" * 60 + "\n")
        return

    # --- STANDALONE ACTION 3: BULK ASSIGNMENT ---
    if args.action == "assign":
        print("[*] Running Bulk Product and Address Assignment...")

        # Load Products
        prod_source = args.products_file or "sample_products.json"
        if not Path(prod_source).is_file():
            data_p = Path(args.data)
            if data_p.is_file():
                with open(data_p, "r", encoding="utf-8") as f:
                    prods = json.load(f).get("products", [])
            else:
                print(f"[!] Error: Products file not found: {prod_source}")
                sys.exit(1)
        else:
            with open(prod_source, "r", encoding="utf-8") as f:
                prods = json.load(f)

        # Load Addresses
        addr_source = args.addresses_file or "sample_addresses.json"
        if not Path(addr_source).is_file():
            data_p = Path(args.data)
            if data_p.is_file():
                with open(data_p, "r", encoding="utf-8") as f:
                    addrs = json.load(f).get("address_pool", [])
            else:
                print(f"[!] Error: Addresses file not found: {addr_source}")
                sys.exit(1)
        else:
            with open(addr_source, "r", encoding="utf-8") as f:
                addrs = json.load(f)

        acc_source = args.source or (Path(args.input_dir) if args.input_dir else Path("."))
        out_dir = args.output_dir or "assignment_outputs"

        rule_cfg = AssignmentRuleConfig(
            products_per_account=args.count,
            random_seed=args.seed
        )
        if args.config_file and Path(args.config_file).is_file():
            with open(args.config_file, "r", encoding="utf-8") as f:
                custom_cfg = json.load(f)
                rule_cfg = AssignmentRuleConfig(**custom_cfg)

        is_dry_run = not args.live
        exec_report = run_bulk_assignment_from_source(
            products_source=prods,
            addresses_source=addrs,
            accounts_source=acc_source,
            config=rule_cfg,
            dry_run=is_dry_run,
            output_dir=out_dir,
            format_type=args.format
        )

        print(f"[+] Execution Completed (Mode: {exec_report['mode']}) for {exec_report['total_accounts']} accounts.")
        print(f"[+] Export format: {args.format} | Directory: {Path(out_dir).resolve()}")
        for item in exec_report["results"]:
            print(f"    - Account: {item['account_id']} | Assigned {item['assigned_products_count']} products | Pincode: {item['assigned_address_pincode']} | Status: {item['status']}")

        return

    # --- CLIENT-BASED ACTIONS: all, cart, address, optimize, pricing, refresh ---
    # 1. Load Account
    account_path = Path(args.account)
    if not account_path.is_file() or is_blacklisted_account({}, account_path.name):
        json_files = [f for f in Path(".").glob("*.json") if f.name[0].isdigit() and not is_blacklisted_account({}, f.name)]
        if json_files:
            account_path = json_files[0]
            print(f"[*] Account file '{args.account}' not found or excluded, using authorized '{account_path}'")
        else:
            print(f"[!] Error: Authorized account JSON not found: {args.account}")
            sys.exit(1)

    print(f"[*] Loading account: {account_path.resolve()}")
    account_data = load_account_json(account_path)

    # Validate Schema
    is_valid_sch, sch_errors = validate_account_schema(account_data)
    if not is_valid_sch:
        print(f"[!] Warning: Account schema issues detected: {'; '.join(sch_errors)}")

    # Check Token Metadata
    try:
        t_meta = parse_xo_token(account_data.get("xo", ""))
        status_txt = "EXPIRED" if t_meta.is_expired else f"VALID ({int(t_meta.remaining_seconds)}s remaining)"
        print(f"[*] Account User ID: {account_data.get('user_id')} | Mobile: {account_data.get('mobile')} | Token: {status_txt}")
    except Exception as e:
        print(f"[*] Token inspection note: {e}")

    # 2. Load Sample Data
    data_path = Path(args.data)
    data_pool = {}
    if data_path.is_file():
        with open(data_path, "r", encoding="utf-8") as f:
            data_pool = json.load(f)

    # 3. Initialize Client
    client = MeeshoClient(
        account_data=account_data,
        account_path=str(account_path),
        custom_proxy=args.proxy,
        auto_refresh=True
    )

    # --- ACTION: REFRESH ---
    if args.action == "refresh":
        print("\n" + "=" * 60)
        print("EXPLICIT TOKEN REFRESH")
        print("=" * 60)
        if args.source:
            out_dir = args.output_dir or "refreshed_accounts"
            res = refresh_accounts_from_source(args.source, output_dir=out_dir, format_type=args.format)
            print(f"[+] Bulk refresh completed: {res['refreshed_count']}/{res['total']} successful.")
            print(f"[+] Saved to: {Path(out_dir).resolve()} (format: {args.format})")
            return

        try:
            client.refresh_session()
            print("[+] Session refreshed and saved successfully via Direct API /api/2.0/xo!")
            profile = client.get_profile()
            print(f"[+] Verified session active! Profile Name: {profile.get('name') or 'Customer'}")
        except Exception as e:
            print(f"[!] Refresh failed: {e}")
        return

    # --- ACTION: PRICING COMPARISON ---
    if args.action == "pricing":
        print("\n" + "=" * 60)
        print("PRODUCT PRICING COMPARISON ACROSS DELIVERY PINCODES")
        print("=" * 60)

        target_prod = args.product or (data_pool.get("products", [None])[0])
        if not target_prod:
            print("[!] Error: No product specified. Use --product <url_or_id>")
            sys.exit(1)

        comparator = ProductPricingComparator(client=client)

        if args.all_india:
            print(f"[*] Product Reference : {target_prod}")
            print(f"[*] Scan Scope        : 🇮🇳 All-India Benchmark (35 Representative Postal Hubs across all 8 Zones)")
            report = comparator.compare_product_pricing(
                product_url_or_id=target_prod,
                all_india=True
            )
        else:
            candidate_pins = (
                [p.strip() for p in args.pincodes.split(",")]
                if args.pincodes
                else data_pool.get("candidate_pincodes", ["500092", "110025", "560001", "400001"])
            )
            print(f"[*] Product Reference : {target_prod}")
            print(f"[*] Testing Pincodes  : {', '.join(candidate_pins)}")
            report = comparator.compare_product_pricing(
                product_url_or_id=target_prod,
                pincodes=candidate_pins
            )

        print("\n" + report.summary_table() + "\n")

        # Save structured report
        out_report_path = Path("pricing_comparison_report.json")
        with open(out_report_path, "w", encoding="utf-8") as f:
            json.dump(report.to_dict(), f, indent=2, ensure_ascii=False)
        print(f"[+] Structured report saved to {out_report_path.resolve()}")
        return

    # Verify session profile for standard actions
    try:
        profile = client.get_profile()
        print(f"[+] Account Session Valid! Logged in as: {profile.get('name') or profile.get('userId') or 'Customer'}")
    except Exception as e:
        print(f"[!] Session verification warning: {e}")

    created_address = None

    # --- ACTION 1: ADD RANDOM PRODUCTS TO CART ---
    if args.action in ("all", "cart"):
        print("\n" + "=" * 60)
        print("STEP 1: ADDING RANDOM PRODUCTS TO CART")
        print("=" * 60)
        products = data_pool.get("products", [])
        if not products:
            print("[!] No products found in data pool.")
        else:
            added = add_random_products_to_cart(
                client=client,
                product_links_or_ids=products,
                count=args.count
            )
            print(f"\n[+] Successfully added {len(added)} items to cart:")
            for idx, it in enumerate(added, 1):
                print(f"    {idx}. Product ID: {it['product_id']} | {it['product_name'][:40]} | Size: {it['variation']}")

    # --- ACTION 2: ADD RANDOM ADDRESS TO ACCOUNT ---
    if args.action in ("all", "address"):
        print("\n" + "=" * 60)
        print("STEP 2: ADDING RANDOM ADDRESS TO ACCOUNT")
        print("=" * 60)
        address_pool = data_pool.get("address_pool", [])
        if not address_pool:
            print("[!] No addresses found in data pool.")
        else:
            created_address = add_random_address_from_pool(client, address_pool)
            print(f"\n[+] Address Registered Successfully!")
            print(f"    Address ID : {created_address.get('id')}")
            print(f"    Recipient  : {created_address.get('name')} ({created_address.get('mobile')})")
            print(f"    Location   : {created_address.get('city')}, {created_address.get('state')} - {created_address.get('pin')}")

    # --- ACTION 3: OPTIMIZE PINCODE, DELIVERY FEES & GET FINAL UPI PRICE ---
    if args.action in ("all", "optimize"):
        print("\n" + "=" * 60)
        print("STEP 3: PINCODE OPTIMIZATION & FINAL UPI PRICING")
        print("=" * 60)

        candidate_pins = (
            [p.strip() for p in args.pincodes.split(",")]
            if args.pincodes
            else data_pool.get("candidate_pincodes", [])
        )

        address_id = created_address.get("id") if created_address else None
        if not address_id:
            try:
                existing = get_account_addresses(client)
                if existing:
                    address_id = existing[0].get("id")
                    print(f"[*] Using existing address ID: {address_id}")
            except Exception as e:
                print(f"[!] Address lookup notice: {e}")

        print(f"[*] Testing {len(candidate_pins)} candidate pincodes: {', '.join(candidate_pins)}")
        try:
            opt_result = find_optimal_pincode_and_upi_price(
                client=client,
                candidate_pincodes=candidate_pins,
                address_id=address_id
            )

            print("\n" + "-" * 60)
            print("                 PINCODE EVALUATION SUMMARY                 ")
            print("-" * 60)
            print(f"{'Pincode':<10} | {'City':<16} | {'Delivery':<10} | {'Total':<8} | {'Final UPI Price':<15}")
            print("-" * 60)
            for ev in opt_result["all_evaluations"]:
                print(
                    f"{ev['pincode']:<10} | "
                    f"{ev['city'][:16]:<16} | "
                    f"₹{ev['delivery_charges']:<9} | "
                    f"₹{ev['total']:<7} | "
                    f"₹{ev['upi_price']:<15}"
                )
            print("-" * 60)

            print("\n" + "=" * 60)
            print("                       OPTIMAL RESULT                       ")
            print("=" * 60)
            print(f"  🏆 Best Pincode        : {opt_result['best_pincode']} ({opt_result['best_city']}, {opt_result['best_state']})")
            print(f"  🚚 Delivery Charges    : ₹{opt_result['delivery_charges']}")
            print(f"  🏷️ Standard Total Price : ₹{opt_result['standard_total']}")
            print(f"  ⚡ FINAL UPI PRICE     : ₹{opt_result['final_upi_price']} (After Instant UPI Prepay Discount)")
            if opt_result["savings_vs_worst"] > 0:
                print(f"  💰 Max Price Savings   : ₹{opt_result['savings_vs_worst']}")
            print("=" * 60 + "\n")

        except Exception as e:
            print(f"[!] Error during pincode optimization: {e}")


if __name__ == "__main__":
    main()
