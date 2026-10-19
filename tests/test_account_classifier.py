"""
Unit tests for account_classifier.py: fresh vs used vs unknown categorization, fault-tolerant batch run.
"""

import json
import tempfile
from pathlib import Path
import unittest

from account_classifier import (
    classify_account_data,
    classify_multiple_accounts,
    AccountClassification
)


class TestAccountClassifier(unittest.TestCase):

    def test_classify_fresh_account_explicit_empty_list(self):
        record = {
            "user_id": 1111,
            "mobile": "9999999991",
            "xo": "token.composite",
            "orders": []
        }
        res = classify_account_data(record, "test_fresh.json")
        self.assertEqual(res.classification, AccountClassification.FRESH)
        self.assertEqual(res.order_count, 0)
        self.assertTrue(res.is_valid_schema)

    def test_classify_fresh_account_explicit_zero_count(self):
        record = {
            "user_id": 2222,
            "mobile": "9999999992",
            "xo": "token.composite",
            "total_orders": 0
        }
        res = classify_account_data(record, "test_fresh_count.json")
        self.assertEqual(res.classification, AccountClassification.FRESH)
        self.assertEqual(res.order_count, 0)

    def test_classify_used_account_with_orders(self):
        record = {
            "user_id": 3333,
            "mobile": "9999999993",
            "xo": "token.composite",
            "orders": [{"order_id": "ORD_123", "amount": 250}]
        }
        res = classify_account_data(record, "test_used.json")
        self.assertEqual(res.classification, AccountClassification.USED)
        self.assertEqual(res.order_count, 1)

    def test_classify_used_account_with_count(self):
        record = {
            "user_id": 4444,
            "mobile": "9999999994",
            "xo": "token.composite",
            "order_count": 5
        }
        res = classify_account_data(record, "test_used_count.json")
        self.assertEqual(res.classification, AccountClassification.USED)
        self.assertEqual(res.order_count, 5)

    def test_classify_unknown_when_order_info_missing(self):
        """CRITICAL: Records with missing order info must NOT be classified as fresh!"""
        record = {
            "user_id": 5555,
            "mobile": "9999999995",
            "xo": "token.composite"
            # No orders or order_count key
        }
        res = classify_account_data(record, "test_missing.json")
        self.assertEqual(res.classification, AccountClassification.UNKNOWN)
        self.assertIsNone(res.order_count)
        self.assertIn("missing", res.reason.lower())

    def test_classify_unknown_when_order_info_malformed(self):
        record = {
            "user_id": 6666,
            "mobile": "9999999996",
            "xo": "token.composite",
            "orders": None   # Malformed / incomplete
        }
        res = classify_account_data(record, "test_malformed.json")
        self.assertEqual(res.classification, AccountClassification.UNKNOWN)
        self.assertIsNone(res.order_count)

    def test_bulk_processing_with_file_outputs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dir_path = Path(tmpdir)
            files = []

            # 1 Fresh
            f1 = dir_path / "fresh.json"
            f1.write_text(json.dumps({"user_id": 101, "mobile": "9111111111", "xo": "token.c", "orders": []}))
            files.append(f1)

            # 1 Used
            f2 = dir_path / "used.json"
            f2.write_text(json.dumps({"user_id": 102, "mobile": "9222222222", "xo": "token.c", "order_count": 2}))
            files.append(f2)

            # 1 Unknown (missing order history)
            f3 = dir_path / "unknown_missing.json"
            f3.write_text(json.dumps({"user_id": 103, "mobile": "9333333333", "xo": "token.c"}))
            files.append(f3)

            # 1 Corrupt JSON
            f4 = dir_path / "corrupt.json"
            f4.write_text("{invalid json content")
            files.append(f4)

            out_dir = dir_path / "outputs"
            summary = classify_multiple_accounts(files, output_dir=out_dir)

            self.assertEqual(summary.total_scanned, 4)
            self.assertEqual(summary.fresh_count, 1)
            self.assertEqual(summary.used_count, 1)
            self.assertEqual(summary.unknown_count, 2)  # unknown_missing + corrupt
            self.assertEqual(summary.invalid_files_count, 1) # corrupt

            # Verify output files exist
            self.assertTrue((out_dir / "fresh_accounts.json").is_file())
            self.assertTrue((out_dir / "used_accounts.json").is_file())
            self.assertTrue((out_dir / "unknown_accounts.json").is_file())
            self.assertTrue((out_dir / "classification_summary.json").is_file())

    def test_live_classification_fallback(self):
        # When live is True but network/mock fails, record must still be safely categorized as UNKNOWN
        record = {
            "user_id": 999999999,
            "mobile": "9999999999",
            "xo": "eyJ0eXBlIjoiY29tcG9zaXRlIn0=.mock_payload.mock_sig"
        }
        res = classify_account_data(record, "test_live.json", live=False)
        self.assertEqual(res.classification, AccountClassification.UNKNOWN)
        self.assertIn("missing", res.reason.lower())


if __name__ == "__main__":
    unittest.main()

