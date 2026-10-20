"""
Unit and Regression Tests for:
1. Multi-Format Data Ingestion (Single, Folder, Merged Array, JSONL)
2. Strict Operator Privacy Blacklisting (7977748514 never exposed or processed)
3. Output Export Formats (Combined JSON vs Individual 1-by-1 .txt in Subfolders)
4. Sticky Proxy Sessions per Account (Session Stickiness across calls)
5. Multi-Tier Proxy Fallback Chain (10x Proxybin -> 15x Pool -> Direct Backup)
6. All-India Nationwide Pincode Pricing Benchmark (8 Postal Zones)
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import requests

from data_io import (
    load_accounts_from_source,
    export_categorized_results,
    is_blacklisted_account,
    BLACKLISTED_NUMBERS
)
import proxy_manager
from proxy_manager import (
    GlobalProxyConfig,
    StickySessionManager,
    ProxyExhaustedError,
    get_global_proxy_config,
    set_global_proxy_config
)
from pricing_comparator import (
    ALL_INDIA_PINCODES,
    get_all_india_pincodes,
    ProductPricingComparator,
    ComparisonStatus
)
from account_classifier import (
    classify_accounts_from_source,
    AccountClassification
)
from fod_classifier import (
    classify_fod_from_source
)
from assignment_engine import (
    run_bulk_assignment_from_source,
    AssignmentRuleConfig
)


class TestEnhancements(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.tmp_path = Path(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        # Reset global proxy config to default
        set_global_proxy_config(mode="proxybin_in", allow_direct_backup=False)

    def test_strict_privacy_blacklisting(self):
        """Verifies that 7977748514 is strictly identified as blacklisted and never ingested."""
        # 1. Blacklisted mobile / phone
        self.assertTrue(is_blacklisted_account({"mobile": "7977748514"}))
        self.assertTrue(is_blacklisted_account({"phone": "+917977748514"}))
        self.assertTrue(is_blacklisted_account({"user_id": "7977748514"}))
        self.assertTrue(is_blacklisted_account({}, "7977748514.json"))

        # Authorized account must not be blacklisted
        self.assertFalse(is_blacklisted_account({"mobile": "8869049741", "user_id": "492252136"}))

        # 2. Ingestion test with blacklisted mixed into accounts
        sample_list = [
            {"user_id": 492252136, "mobile": "8869049741", "xo": "eyJ0eXBlIjoiY29tcG9zaXRlIn0=.eyJqd3QiOiJleUpoYkdjaU9pSklVekkxTmlJc0ltaDBkSEJ6T2k4dmJXVmxjMmh2TG1OdmJTOXBjMjlmWTI5MWJuUnllVjlqYjJSbElqb2lTVTRpTENKb2RIUndjem92TDIxbFpYTm9ieTVqYjIwdmRtVnljMmx2YmlJNklqRWlMQ0owZVhBaU9pSktWMVFpZlEuZXlKbGVIQWlPakU1TkRrek5ERTJNek1zSW1oMGRIQnpPaTh2YldWbGMyaHZMbU52YlM5aGJtOXVlVzF2ZFhOZmRYTmxjbDlwWkNJNkltWXdPRFk1WXpZeExUUTJaRGt0TkROak1DMWlOek0xTFdZeFpESm1aREF4WlRReU15SXNJbWgwZEhCek9pOHZiV1ZsYzJodkxtTnZiUzlwYm5OMFlXNWpaVjlwWkNJNklqSXdZVFF5T1dFNVl6VmxOalF4T1dWaE1EQmlOekk0T0RrM01XSm1PVEkySWl3aWFXRjBJam94TnpreE5qWXhOak16ZlEueDB6ZDZDT0pkVU1tTk5XeFVib2RKcHMyNUJ5cDZFcXVKSTNaTVBpbkpoQSIsInhvIjoiIn0="},
            {"user_id": 999999999, "mobile": "7977748514", "xo": "test.xo"}  # Personal account
        ]
        loaded = load_accounts_from_source(sample_list)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(str(loaded[0]["mobile"]), "8869049741")

    def test_multi_format_ingestion(self):
        """Verifies ingestion from Merged Array, JSONL file, and Directory."""
        # 1. Merged JSON Array file
        merged_file = self.tmp_path / "merged_accounts.json"
        merged_data = [
            {"user_id": 101, "mobile": "9876543210", "orders": []},
            {"user_id": 102, "mobile": "9876543211", "orders": [{"id": 1}]}
        ]
        with open(merged_file, "w", encoding="utf-8") as f:
            json.dump(merged_data, f)

        recs = load_accounts_from_source(merged_file)
        self.assertEqual(len(recs), 2)
        self.assertEqual(recs[0]["user_id"], 101)
        self.assertEqual(recs[1]["user_id"], 102)

        # 2. JSONL file
        jsonl_file = self.tmp_path / "accounts.jsonl"
        with open(jsonl_file, "w", encoding="utf-8") as f:
            f.write(json.dumps({"user_id": 201, "mobile": "9876543220", "orders": []}) + "\n")
            f.write(json.dumps({"user_id": 202, "mobile": "9876543221", "order_count": 3}) + "\n")

        recs_jsonl = load_accounts_from_source(jsonl_file)
        self.assertEqual(len(recs_jsonl), 2)
        self.assertEqual(recs_jsonl[0]["user_id"], 201)
        self.assertEqual(recs_jsonl[1]["user_id"], 202)

    def test_output_export_formats(self):
        """Verifies both 1-by-1 .txt in subfolders and combined .json exports."""
        test_items = {
            "fresh": [
                {"account_id": "acc_101", "mobile": "9876543210", "order_count": 0},
                {"account_id": "acc_102", "mobile": "9876543211", "order_count": 0}
            ],
            "used": [
                {"account_id": "acc_201", "mobile": "9876543220", "order_count": 2}
            ]
        }

        # Format 1: individual_txt
        txt_out = self.tmp_path / "txt_export"
        exp_summary_txt = export_categorized_results(test_items, output_dir=txt_out, format_type="individual_txt")
        self.assertTrue((txt_out / "fresh" / "acc_101.txt").is_file())
        self.assertTrue((txt_out / "fresh" / "acc_102.txt").is_file())
        self.assertTrue((txt_out / "used" / "acc_201.txt").is_file())
        self.assertEqual(exp_summary_txt["total_records"], 3)

        # Format 2: combined_json
        json_out = self.tmp_path / "json_export"
        exp_summary_json = export_categorized_results(test_items, output_dir=json_out, format_type="combined_json")
        self.assertTrue((json_out / "fresh.json").is_file())
        self.assertTrue((json_out / "used.json").is_file())
        self.assertTrue((json_out / "export_summary.json").is_file())
        with open(json_out / "fresh.json", "r", encoding="utf-8") as f:
            fresh_loaded = json.load(f)
            self.assertEqual(len(fresh_loaded), 2)

    def test_sticky_proxy_session_manager(self):
        """Verifies that an account sticks to the same proxy node / direct_url across calls."""
        mgr = StickySessionManager()
        account_id = "user_492252136"

        self.assertIsNone(mgr.get_sticky_direct_url(account_id))

        # First request discovers node URL
        node_url = "https://195.3.220.147/api?__cpo=abc"
        mgr.set_sticky_direct_url(account_id, node_url)

        # Subsequent requests must retrieve the exact same node URL
        self.assertEqual(mgr.get_sticky_direct_url(account_id), node_url)

        # Different account has no sticky session yet
        self.assertIsNone(mgr.get_sticky_direct_url("user_999999"))

    def test_fallback_chain_and_local_backup_behavior(self):
        """
        Verifies:
        - When proxy fails and allow_direct_backup=False, raises ProxyExhaustedError
        - When proxy fails and allow_direct_backup=True, falls back to direct connection
        """
        # Set proxy mode to proxybin, local backup DISABLED, low retry count for speed
        set_global_proxy_config(
            mode="proxybin_in",
            allow_direct_backup=False,
            max_proxybin_attempts=2,
            max_pool_attempts=2
        )

        sess = requests.Session()

        # Mock request_via_proxybin to simulate failures, and mock pool to avoid real network calls
        with patch("proxy_manager.request_via_proxybin", side_effect=Exception("Proxybin network dropped")), \
             patch.object(proxy_manager._POOL, "get_active_proxy", return_value="http://1.2.3.4:8080"), \
             patch.object(proxy_manager._POOL, "get_next_proxy", return_value="http://1.2.3.4:8080"), \
             patch.object(sess, "request", side_effect=requests.exceptions.ConnectionError("Pool node down")), \
             patch("proxy_manager.time.sleep", return_value=None):
            with self.assertRaises(ProxyExhaustedError):
                proxy_manager.request_with_retry(
                    session=sess,
                    method="GET",
                    url="https://prod.meeshoapi.com/api/test",
                    account_key="test_acc"
                )

        # Now enable local backup
        set_global_proxy_config(
            mode="proxybin_in",
            allow_direct_backup=True,
            max_proxybin_attempts=2,
            max_pool_attempts=2
        )
        mock_direct_resp = MagicMock(status_code=200, json=lambda: {"ok": True})

        with patch("proxy_manager.request_via_proxybin", side_effect=Exception("Proxybin offline")), \
             patch.object(proxy_manager._POOL, "get_active_proxy", return_value=None), \
             patch.object(proxy_manager._POOL, "get_next_proxy", return_value=None), \
             patch("proxy_manager.time.sleep", return_value=None), \
             patch.object(sess, "request", return_value=mock_direct_resp):
            resp = proxy_manager.request_with_retry(
                session=sess,
                method="GET",
                url="https://prod.meeshoapi.com/api/test",
                account_key="test_acc"
            )
            self.assertEqual(resp.status_code, 200)

    def test_all_india_pincodes_dataset(self):
        """Verifies that All-India pincode dataset spans all 8 postal zones."""
        pins = get_all_india_pincodes()
        self.assertGreaterEqual(len(pins), 30)

        # Check coverage of each zone 1 to 8
        first_digits = {p[0] for p in pins}
        for expected_zone in ("1", "2", "3", "4", "5", "6", "7", "8"):
            self.assertIn(expected_zone, first_digits, f"Zone {expected_zone} missing from All-India benchmark dataset!")

    def test_all_india_pricing_comparison(self):
        """Verifies compare_product_pricing with all_india=True."""
        mock_client = MagicMock()
        mock_client.account_data = {"user_id": "492252136", "mobile": "8869049741"}

        from pricing_comparator import PriceBreakdown, ComparisonStatus

        with patch("pricing_comparator.get_product_info") as mock_info, \
             patch.object(ProductPricingComparator, "_evaluate_single_pincode") as mock_eval:
            mock_info.return_value = {
                "name": "Benchmark Saree",
                "supplier_id": 99,
                "price": 299.0,
                "variations": [{"variation_id": 1, "variation_name": "Free Size", "in_stock": True}]
            }
            mock_eval.return_value = PriceBreakdown(
                product_id=624039638,
                pincode="110001",
                status=ComparisonStatus.VERIFIED_AVAILABLE,
                city="New Delhi",
                state="Delhi",
                displayed_product_price=299.0,
                delivery_charges=40.0,
                final_payable_price=339.0
            )

            comparator = ProductPricingComparator(client=mock_client)
            report = comparator.compare_product_pricing(
                product_url_or_id="624039638",
                all_india=True
            )
            self.assertGreaterEqual(len(report.tested_pincodes), 30)
            self.assertIsNotNone(report.cheapest_verified_option)

    def test_meesho_client_url_construction(self):
        """Verifies that MeeshoClient.api_call defines and uses url without NameError."""
        from meesho_client import MeeshoClient
        acc = {"user_id": "492252136", "mobile": "8869049741", "xo": "test.xo"}
        client = MeeshoClient(account_data=acc, auto_refresh=False)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b'{"ok": true}'
        mock_resp.json.return_value = {"ok": True}

        with patch("proxy_manager.request_with_retry", return_value=mock_resp) as mock_req:
            res = client.api_call("GET", "/api/3.0/test")
            self.assertTrue(res.get("ok"))
            call_kwargs = mock_req.call_args[1]
            self.assertIn("/api/3.0/test", call_kwargs.get("url"))

    def test_web_ui_endpoints_with_uploaded_accounts_list(self):
        """Verifies Web UI endpoints (/api/run-assignment, /api/refresh-accounts, /api/classify-accounts) with uploaded list."""
        import web_ui
        test_client = web_ui.app.test_client()

        sample_uploaded_accounts = [
            {"user_id": 1001, "mobile": "9999900001", "xo": "mock.xo", "orders": []},
            {"user_id": 1002, "mobile": "9999900002", "xo": "mock.xo", "orders": [{"id": 1}]}
        ]

        # 1. Test /api/run-assignment with uploaded accounts list
        assign_resp = test_client.post("/api/run-assignment", json={
            "source": sample_uploaded_accounts,
            "products_file": "sample_products.json",
            "addresses_file": "sample_addresses.json",
            "seed": 42,
            "count": 1,
            "dry_run": True,
            "format_type": "combined_json"
        })
        self.assertEqual(assign_resp.status_code, 200)
        assign_data = assign_resp.get_json()
        self.assertTrue(assign_data["success"])
        self.assertEqual(assign_data["report"]["total_accounts"], 2)

        # 2. Test /api/classify-accounts with uploaded accounts list
        classify_resp = test_client.post("/api/classify-accounts", json={
            "source": sample_uploaded_accounts,
            "live": False,
            "format_type": "combined_json"
        })
        self.assertEqual(classify_resp.status_code, 200)
        classify_data = classify_resp.get_json()
        self.assertTrue(classify_data["success"])
        self.assertEqual(classify_data["summary"]["fresh_count"], 1)
        self.assertEqual(classify_data["summary"]["used_count"], 1)

        # 3. Test /api/refresh-accounts with mock refresh provider
        with patch("account_manager.DirectApiTokenRefreshProvider.refresh", side_effect=lambda a: {**a, "xo": "refreshed.xo"}):
            refresh_resp = test_client.post("/api/refresh-accounts", json={
                "source": sample_uploaded_accounts,
                "format_type": "combined_json"
            })
            self.assertEqual(refresh_resp.status_code, 200)
            ref_data = refresh_resp.get_json()
            self.assertTrue(ref_data["success"])
        # 4. Test /api/compare-pricing with uploaded account_data directly
        with patch.object(ProductPricingComparator, "compare_product_pricing") as mock_comp:
            mock_comp.return_value = MagicMock(
                to_dict=lambda: {"product_id": "624039638", "product_name": "Test Saree", "results": []}
            )
            pricing_resp = test_client.post("/api/compare-pricing", json={
                "product": "624039638",
                "pincodes": "500092, 110025",
                "account_data": {
                    "mobile": "8869049741",
                    "user_id": 602413725,
                    "xo": "test.composite.xo"
                }
            })
            self.assertEqual(pricing_resp.status_code, 200)
            p_data = pricing_resp.get_json()
            self.assertTrue(p_data["success"])
            self.assertEqual(p_data["report"]["product_id"], "624039638")

        # 5. Pricing comparison must fail with 400 when account credentials are not provided
        no_acc_resp = test_client.post("/api/compare-pricing", json={
            "product": "624039638",
            "pincodes": "500092, 110025"
        })
        self.assertEqual(no_acc_resp.status_code, 400)
        self.assertFalse(no_acc_resp.get_json()["success"])
        self.assertIn("Account credentials are required", no_acc_resp.get_json()["error"])

        # 6. Test /api/live-fod with POST account_data
        with patch("web_ui.fetch_live_fod") as mock_fod:
            mock_fod.return_value = MagicMock(
                advertised_tier="tier_170",
                advertised_amount=170,
                to_dict=lambda include_raw=False: {"advertised_tier": "tier_170", "advertised_amount": 170}
            )
            fod_resp = test_client.post("/api/live-fod", json={
                "account_data": {"mobile": "8869049741", "user_id": 602413725, "xo": "test.xo"}
            })
            self.assertEqual(fod_resp.status_code, 200)
            self.assertEqual(fod_resp.get_json()["advertised_tier"], "tier_170")

        # 7. Test /api/run-assignment with missing files falling back to built-in defaults
        fallback_assign_resp = test_client.post("/api/run-assignment", json={
            "accounts_source": sample_uploaded_accounts,
            "products_source": "non_existent_products.json",
            "addresses_source": "non_existent_addresses.json",
            "seed": 42,
            "count": 1,
            "dry_run": True
        })
        self.assertEqual(fallback_assign_resp.status_code, 200)
        self.assertTrue(fallback_assign_resp.get_json()["success"])

    def test_supabase_module_and_gitignore(self):
        """Verifies Supabase module graceful degradation and .gitignore rules."""
        import supabase_db
        # Without env vars, client is None and not enabled
        self.assertFalse(supabase_db.is_supabase_enabled())
        self.assertIsNone(supabase_db.get_supabase_client())
        self.assertEqual(supabase_db.fetch_accounts_from_supabase(), [])

        # Verify .gitignore exists and contains *.json
        gitignore_path = Path(".gitignore")
        self.assertTrue(gitignore_path.is_file())
        with open(gitignore_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("*.json", content)
        self.assertIn("7977748514", content)

    def test_robust_jwt_parsing_with_malformed_quote(self):
        """Verifies parse_xo_token parses Meesho JWTs even with malformed quotes on user_id."""
        import base64
        from account_manager import parse_xo_token

        # Construct JWT with missing closing quote after user_id
        raw_claims = '{"exp":1791561584,"https://meesho.com/instance_id":"afb8ae73167b4f6eb93ddf9824d87efc","https://meesho.com/user_id":"602863348,"iat":1791388078}'
        header_b64 = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').decode().rstrip("=")
        claims_b64 = base64.urlsafe_b64encode(raw_claims.encode()).decode().rstrip("=")
        fake_jwt = f"{header_b64}.{claims_b64}.mock_sig"
        outer = json.dumps({"jwt": fake_jwt, "xo": "mock_xo"})
        outer_b64 = base64.urlsafe_b64encode(outer.encode()).decode().rstrip("=")
        composite_xo = f"eyJ0eXBlIjoiY29tcG9zaXRlIn0=.{outer_b64}"

        meta = parse_xo_token(composite_xo)
        self.assertEqual(meta.user_id, "602863348")
        self.assertEqual(meta.instance_id, "afb8ae73167b4f6eb93ddf9824d87efc")
        self.assertEqual(meta.expires_at, 1791561584.0)

    def test_order_classifier_referral_done_and_list_keys(self):
        """Verifies account_classifier accurately detects used status from order list keys."""
        from account_classifier import extract_order_history_info, classify_account_data, AccountClassification

        # 1. order_list inside account
        acc1 = {
            "mobile": "9232582410",
            "user_id": 602863348,
            "xo": "fake.xo",
            "order_list": [{"id": 101}]
        }
        res1 = classify_account_data(acc1, live=False)
        self.assertEqual(res1.classification, AccountClassification.USED)
        self.assertEqual(res1.order_count, 1)

        # 2. sub_order_list
        acc2 = {
            "mobile": "9232582411",
            "user_id": 602863349,
            "xo": "fake.xo",
            "sub_order_list": [{"id": 1}, {"id": 2}]
        }
        res2 = classify_account_data(acc2, live=False)
        self.assertEqual(res2.classification, AccountClassification.USED)
        self.assertEqual(res2.order_count, 2)

    def test_fod_record_bucket_field(self):
        """Verifies FODRecord captures and serializes offer_bucket."""
        from fod_classifier import classify_single_fod_record

        sample_fod_payload = {
            "surgical_first_order_discount_v3": {
                "offer_bucket": "170",
                "enabled": True,
                "offer": {
                    "max_offer_value": 170,
                    "offer_text": "Flat ₹170 off on first order"
                }
            }
        }
        rec = classify_single_fod_record(sample_fod_payload)
        d = rec.to_dict()
        self.assertEqual(d["advertised_tier"], "tier_170")
        self.assertEqual(d["advertised_amount"], 170)
        self.assertEqual(d["offer_bucket"], "170")

    def test_live_assignment_auto_initializes_clients(self):
        """Verifies run_bulk_assignment_from_source in live mode initializes clients without SKIPPED_NO_CLIENT."""
        from unittest.mock import patch
        from assignment_engine import run_bulk_assignment_from_source, DEFAULT_ADDRESSES

        accs = [{"mobile": "8869049741", "user_id": 602413725, "xo": "fake.xo"}]
        with patch("assignment_engine.add_address") as mock_addr, \
             patch("assignment_engine.get_product_info") as mock_info, \
             patch("assignment_engine.add_product_to_cart") as mock_cart:
            mock_addr.return_value = {"id": "addr_999"}
            mock_info.return_value = {
                "supplier_id": 1,
                "variations": [{"variation_id": 1, "variation_name": "Free Size", "in_stock": True}]
            }
            mock_cart.return_value = {"success": True}

            result = run_bulk_assignment_from_source(
                products_source=["624039638"],
                addresses_source=DEFAULT_ADDRESSES,
                accounts_source=accs,
                dry_run=False
            )
            self.assertEqual(result["mode"], "LIVE_EXECUTION")
            self.assertEqual(result["total_accounts"], 1)
            item = result["results"][0]
            self.assertNotEqual(item["status"], "SKIPPED_NO_CLIENT")
            self.assertEqual(item["status"], "SUCCESS")


if __name__ == "__main__":
    unittest.main()



