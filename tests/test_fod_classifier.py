"""
Unit tests for fod_classifier.py: FOD tier categorization, normalized amount extraction, eligibility distinction.
"""

import json
import tempfile
from pathlib import Path
import unittest

from fod_classifier import (
    normalize_discount_amount,
    classify_single_fod_record,
    classify_multiple_fod_files,
    EligibilityStatus
)


class TestFODClassifier(unittest.TestCase):

    def test_normalize_discount_amount(self):
        self.assertEqual(normalize_discount_amount("₹170 OFF"), 170)
        self.assertEqual(normalize_discount_amount("Flat ₹190 off on 1st order"), 190)
        self.assertEqual(normalize_discount_amount("Rs. 75 Discount"), 75)
        self.assertEqual(normalize_discount_amount("Get INR 100 off"), 100)
        self.assertEqual(normalize_discount_amount("200/-"), 200)
        self.assertEqual(normalize_discount_amount("pure text without digits"), None)

    def test_classify_tier_170_record(self):
        record = {
            "user_id": 1001,
            "mobile": "9876543211",
            "fod": {
                "offer_text": "₹170 OFF on first order",
                "status": "UNVERIFIED"
            }
        }
        res = classify_single_fod_record(record, "test_170.json")
        self.assertEqual(res.advertised_tier, "tier_170")
        self.assertEqual(res.advertised_amount, 170)
        self.assertEqual(res.eligibility_status, EligibilityStatus.UNVERIFIED)

    def test_classify_tier_190_record(self):
        record = {
            "user_id": 1002,
            "mobile": "9876543212",
            "first_order_discount": {
                "title": "Get ₹190 off on your first order",
                "status": "ELIGIBLE"
            }
        }
        res = classify_single_fod_record(record, "test_190.json")
        self.assertEqual(res.advertised_tier, "tier_190")
        self.assertEqual(res.advertised_amount, 190)
        self.assertEqual(res.eligibility_status, EligibilityStatus.ELIGIBLE)

    def test_classify_surgical_personalisation_structure(self):
        record = {
            "user_id": 1003,
            "mobile": "9876543213",
            "surgical_first_order_discount_v3": {
                "enabled": True,
                "offer_bucket": 75,
                "offer": {
                    "max_offer_value": 75,
                    "offer_text": "₹75 OFF"
                }
            }
        }
        res = classify_single_fod_record(record, "test_surgical.json")
        self.assertEqual(res.advertised_tier, "tier_75")
        self.assertEqual(res.advertised_amount, 75)

    def test_classify_no_offer_record(self):
        record = {
            "user_id": 1004,
            "mobile": "9876543214",
            "xo": "some.token"
        }
        res = classify_single_fod_record(record, "test_no_offer.json")
        self.assertEqual(res.advertised_tier, "no_offer")
        self.assertIsNone(res.advertised_amount)

    def test_advertised_vs_eligibility_distinction(self):
        """Must distinguish advertised offer presence from verified eligibility."""
        record_with_ineligible_flag = {
            "user_id": 1005,
            "mobile": "9876543215",
            "fod": {
                "offer_text": "₹170 OFF",
                "status": "INELIGIBLE"
            }
        }
        res = classify_single_fod_record(record_with_ineligible_flag)
        self.assertEqual(res.advertised_tier, "tier_170")
        self.assertEqual(res.advertised_amount, 170)
        self.assertEqual(res.eligibility_status, EligibilityStatus.INELIGIBLE)

    def test_batch_classification_and_consolidation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dir_path = Path(tmpdir)
            files = []

            # Tier 170
            f1 = dir_path / "acc1.json"
            f1.write_text(json.dumps({"user_id": 2001, "fod": "₹170 OFF"}))
            files.append(f1)

            # Tier 190
            f2 = dir_path / "acc2.json"
            f2.write_text(json.dumps({"user_id": 2002, "fod": "₹190 OFF"}))
            files.append(f2)

            # Duplicate of acc1
            f3 = dir_path / "acc1_dup.json"
            f3.write_text(json.dumps({"user_id": 2001, "fod": "₹170 OFF"}))
            files.append(f3)

            out_dir = dir_path / "fod_outputs"
            summary = classify_multiple_fod_files(files, output_dir=out_dir)

            self.assertEqual(summary.total_scanned, 3)
            self.assertEqual(summary.unique_accounts_count, 2)
            self.assertEqual(summary.duplicates_count, 1)
            self.assertEqual(summary.tier_counts.get("tier_170"), 2)
            self.assertEqual(summary.tier_counts.get("tier_190"), 1)

            # Consolidated files
            self.assertTrue((out_dir / "fod_tier_170.json").is_file())
            self.assertTrue((out_dir / "fod_tier_190.json").is_file())
            self.assertTrue((out_dir / "fod_summary.json").is_file())

    def test_classify_advertised_root_keys(self):
        record = {
            "user_id": 3001,
            "mobile": "6399916141",
            "advertised_tier": "tier_170",
            "advertised_amount": 170,
            "offer_bucket": "170"
        }
        res = classify_single_fod_record(record)
        self.assertEqual(res.advertised_tier, "tier_170")
        self.assertEqual(res.advertised_amount, 170)
        self.assertEqual(res.offer_bucket, "170")

    def test_fetch_live_fod_authenticated_only(self):
        from unittest.mock import patch, MagicMock
        from fod_classifier import fetch_live_fod

        # 1. Authenticated account
        acc_auth = {
            "user_id": 605592597,
            "mobile": "6399916141",
            "xo": "valid_xo_token",
            "identity": {"app_session_id": "sess_1", "instance_id": "inst_1", "gaid": "gaid_1"}
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "surgical_first_order_discount_v3": {
                "enabled": True,
                "offer_bucket": 170,
                "offer": {
                    "max_offer_value": 170,
                    "offer_text": "₹170 OFF"
                }
            }
        }

        with patch("proxy_manager.request_with_retry", return_value=mock_resp) as mock_req:
            rec = fetch_live_fod(account_data=acc_auth)
            # Verify it hit the authenticated /api/1.0/fod-personalisation endpoint
            called_url = mock_req.call_args[1].get("url") or mock_req.call_args[0][2]
            called_headers = mock_req.call_args[1].get("headers")
            self.assertIn("/api/1.0/fod-personalisation", called_url)
            self.assertNotIn("/anonymous/", called_url)
            self.assertEqual(called_headers.get("Meesho-User-Context"), "logged_in")
            self.assertEqual(called_headers.get("Xo"), "valid_xo_token")
            self.assertEqual(rec.advertised_tier, "tier_170")
            self.assertEqual(rec.advertised_amount, 170)
            self.assertEqual(rec.offer_bucket, "170")
            self.assertEqual(acc_auth.get("advertised_tier"), "tier_170")

        # 2. Account without mandatory credentials (must be rejected, never calls anonymous)
        acc_no_cred = {
            "identity": {"app_session_id": "sess_2"}
        }

        with patch("proxy_manager.request_with_retry") as mock_req:
            rec = fetch_live_fod(account_data=acc_no_cred)
            # Ensure no API request was dispatched without credentials
            mock_req.assert_not_called()
            self.assertEqual(rec.advertised_tier, "unclassified")
            self.assertEqual(rec.eligibility_status, EligibilityStatus.INELIGIBLE)
            self.assertTrue(any("mandatory" in err.lower() for err in rec.validation_errors))


if __name__ == "__main__":
    unittest.main()

